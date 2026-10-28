"""
connectors/github.py  —  sync a workspace's GitHub repositories (docs first)
into its index, through a GitHub App.

Connecting: the server has ONE GitHub App (GITHUB_APP_ID, GITHUB_APP_SLUG,
GITHUB_APP_PRIVATE_KEY or GITHUB_APP_PRIVATE_KEY_FILE, GITHUB_APP_CLIENT_ID,
GITHUB_APP_CLIENT_SECRET; deploy.md, "GitHub"). Connect sends the admin to the
app's install page, where they choose the organisation or account and its
repositories. The app has "Request user authorization (OAuth) during
installation" on, so GitHub comes back to the callback URL with an
`installation_id` AND a `code`. The installation_id alone can be spoofed
(GitHub: "Bad actors can hit this URL with a spoofed installation_id"), so the
code is swapped for a user token and the installation must be among
GET /user/installations for that user before it is saved. The user token is
used for that check only and not kept. Syncs then use INSTALLATION TOKENS:
a JWT signed with the app's private key (RS256; iat 60 s in the past, exp at
most 10 minutes ahead, iss the app id) is exchanged at
POST /app/installations/{id}/access_tokens for a token that lasts an hour
(cached, encrypted, with the connection, and minted again when it's near its end).

What is synced (nothing until an admin picks REPOSITORIES on the card): the
default branch's docs (README files, *.md, *.mdx, *.markdown, *.rst, *.txt)
and, only for repos where the admin turns "code" on, source files whose
extension is in the connection's allowlist (options.code_extensions,
DEFAULT_CODE_EXTENSIONS), each as one fenced block with its path as the title.
Size caps: GITHUB_MAX_DOC_BYTES (1 MB), GITHUB_MAX_CODE_BYTES (256 KB); vendored
and build folders are skipped. Wikis are separate git repositories that the REST
API doesn't serve: not synced (a follow-up, cloning `<repo>.wiki.git`).

How a sync finds what changed (per repo; the cursor keeps each repo's last SHA):
  FULL (first run, picks changed, every RECONCILE seconds, asked for)
      the default branch's tree (Git Trees, ?recursive=1; if GitHub says it's
      `truncated`, sub-tree by sub-tree), the authoritative list: anything known
      but not in it was deleted and goes behind the mass-delete guard. The
      repo's visibility and collaborators are read again.
  INCREMENTAL
      the head SHA of the default branch; if it moved, the compare API from the
      last synced SHA (`base...head`: files added, modified, renamed, removed).
      When compare can't say (the base is gone after a force push, the branch
      diverged, or 300 files changed: the most it lists) the repo's tree is
      listed again.

Permissions are CAPTURED into each item's `acl`, not enforced: the repo's
visibility (public repos are readable by anyone), its collaborators (needs the
Metadata permission; GitHub logins: emails are often private, so mapping them
to members is the enforcement step's job), and its teams (needs the optional
Administration: read permission; None when the app doesn't have it).

Webhooks (push, installation_repositories, member, team) would make the
incremental runs immediate; they are a follow-up (deploy.md, "GitHub").

API references (checked 2026-10-27):
  JWT               https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app
  installation token https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app
                    (POST /app/installations/{installation_id}/access_tokens: expires after one hour)
  install URL/state https://docs.github.com/en/apps/sharing-github-apps/sharing-your-github-app
  setup URL warning https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-setup-url
  user token        https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app
                    (POST https://github.com/login/oauth/access_token: client_id, client_secret, code)
  registering       https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app
  REST (spec)       https://github.com/github/rest-api-description (api.github.com.json):
                    GET /user/installations, GET /installation/repositories,
                    GET /repos/{owner}/{repo}/branches/{branch},
                    GET /repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1 (100,000
                    entries / 7 MB, `truncated`), GET .../git/blobs/{file_sha} (base64,
                    up to 100 MB), GET .../compare/{basehead} (files: up to 300; status
                    added | removed | modified | renamed | copied | changed | unchanged),
                    GET .../collaborators, GET .../teams
  permissions       https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps
                    (trees, blobs, compare, branches: Contents read; collaborators:
                    Metadata read; repo teams: Administration read)
  rate limits       https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
                    (403 or 429; x-ratelimit-remaining 0 + x-ratelimit-reset epoch seconds,
                    or retry-after; installations 5,000 an hour and up)
"""

import base64
import datetime
import json
import logging
import os
import posixpath
import re
import time
import urllib.parse

import integrations_store as store
from connectors import http
from connectors.base import (AuthExpired, ConfigError, ConnectRefused, Connector, Cursor,
                             Delete, Fetched, Forbidden, FormError, Item, LimitReached,
                             NextStep, NotFound, Skipped, Upsert)
from kb.settings import env_int

log = logging.getLogger("uvicorn.error")

PROVIDER = "github"
API = "https://api.github.com"
OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
API_VERSION = "2022-11-28"

RATE = 1.3                            # calls a second per installation (5,000 an hour)
RECONCILE = 24 * 60 * 60
COMPARE_MAX_FILES = 300               # compare lists at most this many changed files
MAX_COLLABORATORS = 1000
DOC_EXTENSIONS = (".md", ".mdx", ".markdown", ".rst", ".txt")
DEFAULT_CODE_EXTENSIONS = (".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".kt", ".rb",
                           ".rs", ".cs", ".php", ".swift", ".c", ".h", ".cpp", ".sql", ".sh",
                           ".yaml", ".yml", ".toml", ".proto", ".tf")
SKIP_DIRS = {"node_modules", "vendor", "dist", "build", ".git", "__pycache__", ".venv",
             "venv", "target", "third_party", ".next", "coverage"}
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
              "cargo.lock", "go.sum", "composer.lock", "gemfile.lock"}
MAX_DOC_BYTES = 1024 * 1024
MAX_CODE_BYTES = 256 * 1024
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_EXT = re.compile(r"^\.[a-z0-9_+-]{1,15}$")


# ---- app credentials ----------------------------------------------------------------
def _private_key():
    pem = os.getenv("GITHUB_APP_PRIVATE_KEY") or ""
    path = os.getenv("GITHUB_APP_PRIVATE_KEY_FILE") or ""
    if not pem and path:
        try:
            with open(path, encoding="utf-8") as f:
                pem = f.read()
        except OSError:
            pem = ""
    return pem.replace("\\n", "\n").strip()


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def app_jwt(now=None):
    """The app's JWT (RS256): iat 60 s in the past, exp 9 minutes ahead (at most
    10 allowed), iss the app id."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    now = int(now if now is not None else time.time())
    try:
        key = serialization.load_pem_private_key(_private_key().encode(), password=None)
    except (ValueError, TypeError):
        raise ConfigError("GITHUB_APP_PRIVATE_KEY isn't a PEM private key") from None
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = _b64(json.dumps({"iat": now - 60, "exp": now + 540,
                              "iss": os.getenv("GITHUB_APP_ID", "")},
                             separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64(signature)}"


def install_url(state):
    return f"https://github.com/apps/{os.environ.get('GITHUB_APP_SLUG', '')}/installations/new?" \
           + urllib.parse.urlencode({"state": state})


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION}


def _ext(path):
    return posixpath.splitext(path.lower())[1]


class GitHub(Connector):
    PROVIDER = PROVIDER
    LABEL = "GitHub"
    DESCRIPTION = "READMEs and docs from the repositories you pick; code if you turn it on"
    AUTH = "oauth"
    PICKER = "list"
    PICKER_REQUIRED = True
    SETTINGS_KEY = "repos"
    UNIT = "files"
    ACL = True
    SETUP_ENV = ("GITHUB_APP_ID / GITHUB_APP_SLUG / GITHUB_APP_PRIVATE_KEY / "
                 "GITHUB_APP_CLIENT_ID / GITHUB_APP_CLIENT_SECRET")

    def configured(self):
        return all(os.getenv(k) for k in ("GITHUB_APP_ID", "GITHUB_APP_SLUG",
                                          "GITHUB_APP_CLIENT_ID", "GITHUB_APP_CLIENT_SECRET")) \
            and bool(_private_key())

    # ---- connecting ---------------------------------------------------------------
    def auth_url(self, redirect_uri, state):
        return install_url(state)

    def callback_code(self, params, step=None):
        """GitHub comes back with code, installation_id and setup_action
        ("install", "update", or "request" when an org owner must approve)."""
        if not params.get("code") and params.get("setup_action") != "request":
            return ""
        return json.dumps({k: params.get(k) for k in ("code", "installation_id",
                                                      "setup_action")})

    def finish_connect(self, workspace, code, redirect_uri, user, step=None):
        got = json.loads(code)
        if got.get("setup_action") == "request":
            raise ConnectRefused("install_requested", "an organisation owner must approve")
        if not got.get("code"):
            raise ConnectRefused("connect_failed", "no code from GitHub")
        r = http.request("POST", OAUTH_TOKEN_URL, retries=1, headers={"Accept": "application/json"},
                         data={"client_id": os.environ.get("GITHUB_APP_CLIENT_ID", ""),
                               "client_secret": os.environ.get("GITHUB_APP_CLIENT_SECRET", ""),
                               "code": got["code"], "redirect_uri": redirect_uri})
        t = r.json() if r.content else {}
        if not t.get("access_token"):
            raise ConfigError(f"GitHub refused the code ({t.get('error') or 'no token'})")
        # The user's installations of THIS app: the only ones they may connect.
        mine, page = [], 1
        while True:
            data = http.request("GET", f"{API}/user/installations", retries=1,
                                params={"per_page": 100, "page": page},
                                headers=_headers(t["access_token"])).json()
            got_page = data.get("installations") or []
            mine += [i for i in got_page if str(i.get("app_id")) == os.getenv("GITHUB_APP_ID")]
            if len(got_page) < 100:
                break
            page += 1
        wanted = str(got.get("installation_id") or "")
        if wanted:
            chosen = next((i for i in mine if str(i.get("id")) == wanted), None)
            if not chosen:
                raise ConnectRefused("wrong_account", "that installation isn't this user's")
        elif len(mine) == 1:
            chosen = mine[0]
        elif step is None:
            # Not installed yet, or on several accounts: GitHub's install page asks which.
            raise NextStep(install_url, {"asked": True})
        else:
            raise ConnectRefused("no_installation", "no installation was chosen")
        acct = chosen.get("account") or {}
        account = f"{acct.get('login') or 'GitHub'} ({acct.get('type') or 'account'})"
        row = store.get(workspace, PROVIDER)
        store.connect(workspace, PROVIDER, account, {"installation_id": str(chosen["id"])}, user)
        settings = dict((row or {}).get("settings") or {}, installation_id=str(chosen["id"]))
        if row and (row.get("settings") or {}).get("installation_id") != str(chosen["id"]):
            settings["repos"] = []                # another account: other repositories
        store.update(workspace, PROVIDER, settings=settings)

    # ---- calling ------------------------------------------------------------------
    def _token(self, ws, force=False):
        """An installation token, minted with the app's JWT when there is none or
        it has under five minutes left."""
        with http.refresh_lock(f"{PROVIDER}:{ws}"):
            sec = store.secret(ws, PROVIDER)
            if not sec or not sec.get("installation_id"):
                raise AuthExpired("GitHub is not connected")
            if not force and sec.get("token") and sec.get("expires_at", 0) - 300 > time.time():
                return sec["token"]
            r = http.request("POST", f"{API}/app/installations/{sec['installation_id']}/"
                             "access_tokens", retries=2, ok=(401, 403, 404),
                             headers=_headers(app_jwt()))
            if r.status_code == 401:
                raise ConfigError("GitHub rejected the app's JWT (app id or private key)")
            if r.status_code in (403, 404):       # uninstalled or suspended
                raise AuthExpired(f"installation {sec['installation_id']}: HTTP {r.status_code}")
            t = r.json()
            sec.update(token=t["token"], expires_at=_epoch(t.get("expires_at")) or
                       time.time() + 3600)
            store.update(ws, PROVIDER, secret=sec)
            return sec["token"]

    def _get(self, ws, path, params=None, ok=()):
        url = path if path.startswith("https://") else f"{API}{path}"
        bucket = http.bucket_key(PROVIDER, ws)
        for attempt in (0, 1):
            r = http.request("GET", url, params=params, ok=(401, *ok), bucket=bucket, rate=RATE,
                             burst=10, headers=_headers(self._token(ws, force=attempt == 1)))
            if r.status_code != 401:
                return r
        raise AuthExpired(f"GET {http.safe_url(url)}: HTTP 401")

    def _json(self, ws, path, params=None, ok=()):
        r = self._get(ws, path, params, ok)
        if r.status_code in ok:
            return None
        try:
            return r.json()
        except ValueError:
            raise ConfigError("the answer isn't JSON") from None

    # ---- picking ------------------------------------------------------------------
    def list_containers(self, ctx, parent=None):
        out, page = [], 1
        while True:
            data = self._json(ctx.workspace, "/installation/repositories",
                              {"per_page": 100, "page": page})
            repos = data.get("repositories") or []
            out += [{"id": r["full_name"], "name": r["full_name"], "kind": "repo",
                     "private": bool(r.get("private"))} for r in repos if r.get("full_name")]
            if len(repos) < 100:
                break
            page += 1
        return sorted(out, key=lambda c: c["name"].lower())

    def check_containers(self, picked):
        out = []
        for p in super().check_containers(picked):
            if not _REPO.match(p["id"]):
                raise FormError("Pick repositories by their owner/name.")
            out.append({"id": p["id"], "name": p["name"] or p["id"], "kind": "repo",
                        "code": bool(next((x.get("code") for x in picked
                                           if isinstance(x, dict) and x.get("id") == p["id"]),
                                          False))})
        return out

    def check_options(self, options, settings):
        raw = (options or {}).get("code_extensions")
        if raw is None:
            return {}
        items = raw.split(",") if isinstance(raw, str) else raw
        if not isinstance(items, list):
            raise FormError("List code file types like .py, .ts.")
        exts = []
        for e in items[:60]:
            e = str(e).strip().lower()
            if not e:
                continue
            e = e if e.startswith(".") else "." + e
            if not _EXT.match(e):
                raise FormError(f"{e[:20]} isn't a file extension.")
            exts.append(e)
        return {"code_extensions": sorted(set(exts)) or None}

    def options_of(self, settings):
        return {"code_extensions": list((settings or {}).get("code_extensions")
                                        or DEFAULT_CODE_EXTENSIONS)}

    # ---- syncing ------------------------------------------------------------------
    def _kind(self, path, code_on, exts):
        """"doc", "code" or None for a file in a repo."""
        parts = path.split("/")
        if any(p in SKIP_DIRS for p in parts[:-1]):
            return None
        name = parts[-1].lower()
        if name in SKIP_NAMES or name.endswith((".min.js", ".min.css")):
            return None
        ext = _ext(name)
        if ext in DOC_EXTENSIONS or name.startswith("readme"):
            return "doc"
        if code_on and ext in exts:
            return "code"
        return None

    def _cap(self, kind):
        return (env_int("GITHUB_MAX_CODE_BYTES", MAX_CODE_BYTES) if kind == "code"
                else env_int("GITHUB_MAX_DOC_BYTES", MAX_DOC_BYTES))

    def _tree(self, ws, repo, sha):
        """Every blob in the commit's tree: [(path, blob sha, size)]."""
        data = self._json(ws, f"/repos/{repo}/git/trees/{sha}", {"recursive": "1"})
        if not data.get("truncated"):
            return [(e["path"], e["sha"], e.get("size")) for e in data.get("tree") or []
                    if e.get("type") == "blob"]
        out, todo = [], [("", sha)]               # too big for one answer: tree by tree
        while todo:
            prefix, tree_sha = todo.pop()
            for e in (self._json(ws, f"/repos/{repo}/git/trees/{tree_sha}") or {}).get("tree") or []:
                path = f"{prefix}{e['path']}"
                if e.get("type") == "tree" and e["path"] not in SKIP_DIRS:
                    todo.append((path + "/", e["sha"]))
                elif e.get("type") == "blob":
                    out.append((path, e["sha"], e.get("size")))
        return out

    def _repo_acl(self, ws, repo, info):
        acl = {"repo": repo, "visibility": info.get("visibility")
               or ("private" if info.get("private") else "public"),
               "public": not info.get("private")}
        if acl["public"]:
            return acl
        users, page = [], 1
        try:
            while len(users) < MAX_COLLABORATORS:
                got = self._json(ws, f"/repos/{repo}/collaborators",
                                 {"per_page": 100, "page": page, "affiliation": "all"}) or []
                users += [f"github:{u['login']}" for u in got if u.get("login")]
                if len(got) < 100:
                    break
                page += 1
            acl["users"] = sorted(set(users))
            if len(users) >= MAX_COLLABORATORS:
                acl["truncated"] = True
        except Forbidden:
            acl["users"] = None
        try:
            teams = self._json(ws, f"/repos/{repo}/teams", {"per_page": 100}) or []
            acl["teams"] = sorted(f"github:team:{repo.split('/')[0]}/{t['slug']}"
                                  for t in teams if t.get("slug"))
        except (Forbidden, NotFound):
            acl["teams"] = None                   # needs Administration: read
        if acl["users"] is None or acl["teams"] is None:
            acl["partial"] = True
        return acl

    def _item(self, repo, branch, path, sha, size, kind, acl):
        return Item(source_id=f"{repo}:{path}", title=f"{repo}/{path}",
                    url=f"https://github.com/{repo}/blob/{branch}/{path}", version=sha,
                    kind="code" if kind == "code" else "file",
                    mime="text/markdown" if kind == "code" or _ext(path) in (".md", ".mdx",
                                                                          ".markdown")
                    else "text/plain",
                    parent_id=f"repo-{repo}", size=size, acl=acl,
                    attrs={"repo": repo, "path": path, "sha": sha, "branch": branch,
                           "type": kind})

    def changes(self, ctx, cursor):
        ws = ctx.workspace
        settings = ctx.settings or {}
        picked = settings.get("repos") or []
        state = json.loads(cursor) if cursor else {}
        if not picked:
            yield from ctx.missing(())
            yield Cursor(None, full=True)
            return
        sig = sorted([p["id"], bool(p.get("code"))] for p in picked)
        full = (ctx.full or not state or state.get("picks") != sig
                or time.time() - state.get("full_at", 0) > RECONCILE)
        exts = set(settings.get("code_extensions") or DEFAULT_CODE_EXTENSIONS)
        repos, seen, count = {}, set(), [0]

        def emit(repo, branch, path, sha, size, code_on, acl):
            """Upsert / Skipped for one file, or None if it isn't synced."""
            kind = self._kind(path, code_on, exts)
            if not kind:
                return None
            sid = f"{repo}:{path}"
            seen.add(sid)
            count[0] += 1
            if size and size > self._cap(kind):
                return Skipped(sid, "too_large", f"{repo}/{path}",
                               f"https://github.com/{repo}/blob/{branch}/{path}")
            return Upsert(self._item(repo, branch, path, sha, size, kind, acl))

        for p in sorted(picked, key=lambda p: p["id"]):
            repo, code_on = p["id"], bool(p.get("code"))
            info = self._json(ws, f"/repos/{repo}", ok=(404,))
            if info is None:
                raise ConfigError(f"picked repository is gone or not shared: {repo}")
            branch = info.get("default_branch") or "main"
            head = (self._json(ws, f"/repos/{repo}/branches/{branch}", ok=(404,)) or {}) \
                .get("commit", {}).get("sha")
            prev = (state.get("repos") or {}).get(repo) or {}
            acl = self._repo_acl(ws, repo, info) if full or not prev else prev.get("acl")
            repos[repo] = {"sha": head, "branch": branch, "acl": acl}
            files = None                          # None: compare said what changed
            if not head:
                files = []                        # an empty repository
            elif full or not prev.get("sha") or prev.get("branch") != branch:
                files = self._tree(ws, repo, head)
            elif prev["sha"] != head:
                cmp = self._json(ws, f"/repos/{repo}/compare/{prev['sha']}...{head}",
                                 ok=(404,))
                changed = (cmp or {}).get("files")
                if (not cmp or cmp.get("status") not in ("ahead", "identical")
                        or changed is None or len(changed) >= COMPARE_MAX_FILES):
                    files = self._tree(ws, repo, head)
                else:
                    for f in changed:
                        path, status = f.get("filename") or "", f.get("status")
                        if status == "renamed" and f.get("previous_filename"):
                            old = f"{repo}:{f['previous_filename']}"
                            if old in ctx.known:
                                yield Delete(old)
                        sid = f"{repo}:{path}"
                        event = None if status == "removed" else emit(
                            repo, branch, path, f.get("sha"), None, code_on, acl)
                        if event is None:
                            if sid in ctx.known:
                                yield Delete(sid)     # removed, or no longer a synced type
                            continue
                        if ctx.max_items and count[0] > ctx.max_items:
                            yield LimitReached(ctx.max_items)
                            yield Cursor(cursor)
                            return
                        yield event
            if files is not None:
                mine = set()
                for path, sha, size in files:
                    event = emit(repo, branch, path, sha, size, code_on, acl)
                    if event is None:
                        continue
                    if ctx.max_items and count[0] > ctx.max_items:
                        yield LimitReached(ctx.max_items)
                        yield Cursor(cursor)          # deletes nothing; the next run tries again
                        return
                    mine.add(f"{repo}:{path}")
                    yield event
                if not full:                          # this repo was listed again
                    for sid in sorted(ctx.known):
                        if sid.startswith(f"{repo}:") and sid not in mine:
                            yield Delete(sid)
        if full:
            yield from ctx.missing(seen)
        yield Cursor(json.dumps({"picks": sig, "repos": repos,
                                 "full_at": time.time() if full else state.get("full_at", 0)}),
                     full=full)

    def fetch(self, ctx, item):
        a = item.attrs or {}
        cap = self._cap(a.get("type"))
        if item.size and item.size > cap:
            return None
        blob = self._json(ctx.workspace, f"/repos/{a['repo']}/git/blobs/{a['sha']}")
        size = int(blob.get("size") or 0)
        if size > cap:
            item.size = size                      # the framework says "too large"
            return None
        try:
            data = base64.b64decode(blob.get("content") or "")
        except ValueError:
            return None
        if b"\0" in data[:8000]:
            return None                           # binary: not text after all
        text = data.decode("utf-8", errors="replace")
        if a.get("type") == "code":
            lang = _ext(a["path"]).lstrip(".")
            text = f"```{lang}\n{text.rstrip()}\n```"
        return Fetched(text, item.mime)

    def disconnect(self, workspace):
        """The installation stays on GitHub (an owner uninstalls the app under the
        organisation's Settings → GitHub Apps); the cached token is dropped with
        the connection."""


def _epoch(iso):
    try:
        return datetime.datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


CONNECTOR = GitHub()
