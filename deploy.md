# Deploy — Chat With Your Notes (Docker on a VPS)

Self-contained runbook. A person **or a Claude agent** can follow this top-to-bottom
to deploy the app on a fresh Linux server. Commands are for **Ubuntu 22.04+**.

---

## 0. What you need before starting

- A fresh **Ubuntu 22.04+** VPS with root/sudo SSH access.
- The **private** repo URL: `https://github.com/Cloudastra-Tech/knowlegebase.git`
  (private matters — `docs/restricted/staff_salaries.txt` is committed).
- An **OpenAI API key** (`sk-...`). Optionally Outline + Cloudflare Access values.

> ⚠️ **Security note — serve it over HTTPS.** People sign in with Google /
> Microsoft (step 4c) or a username + password (step 4b). Each account has a role:
> only HR / Admin accounts see restricted documents and the Documents and
> Integrations screens. This runbook serves port 8501 without HTTPS, so put an
> HTTPS reverse proxy (nginx, Caddy) or Cloudflare Access in front before exposing
> it to the internet. Google and Microsoft refuse to send sign-ins back to a plain
> `http://` address (except `localhost`).

---

## 1. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
```

## 2. Get the code

```bash
git clone https://github.com/Cloudastra-Tech/knowlegebase.git genai
cd genai
```

## 3. Create the secrets file

Create `.env` in the project root (it is gitignored — never commit it):

```bash
cat > .env <<'EOF'
OPENAI_API_KEY=sk-REPLACE_ME
# Optional — only if using the Outline wiki tools:
OUTLINE_URL=https://app.cloudastra.co
OUTLINE_API_TOKEN=
OUTLINE_MIN_ROLE=public       # who gets the wiki: public (everyone) or hr (HR / Admin only)
OUTLINE_COLLECTIONS=          # optional comma-separated collection ids; blank = all the token sees
CF_ACCESS_CLIENT_ID=
CF_ACCESS_CLIENT_SECRET=
EOF
nano .env    # paste the real OPENAI_API_KEY (and Outline values if used)
```

## 4. Build and start

**Guardrails Hub token (production).** The toxicity check on chat questions needs
the Guardrails Hub `toxic_language` validator, which the image installs only when
the build is given a Hub token (free, from https://hub.guardrailsai.com/keys). The
token is a BuildKit build secret: it is never a build argument or part of git.
Without it the image still builds (CI and fresh clones do), the build log says
`no gr_token build secret - skipping the toxicity validator`, and the app starts
without the toxicity check (see `REQUIRE_TOXICITY_GUARD` below).

```bash
printf '%s\n' 'PASTE_THE_HUB_TOKEN' > guardrails_token.secret   # gitignored and not copied into the image
chmod 600 guardrails_token.secret
echo 'COMPOSE_FILE=docker-compose.yml:docker-compose.guardrails.yml' >> .env
```

`COMPOSE_FILE` makes every `docker compose` command in this folder add
`docker-compose.guardrails.yml`, which hands the file to the build as the
`gr_token` secret. It is a separate file because Compose refuses to build when a
secret's file is missing. With it on, a missing `guardrails_token.secret` fails the
build (`failed to stat …guardrails_token.secret`) instead of silently dropping the
check. The build writes the token into the image (`/root/.guardrailsrc`, needed at
run time to call the Hub's hosted model), so never push an image built this way to
a public registry. BuildKit does not notice a changed secret: after adding or
rotating the token, rebuild with `docker compose build --no-cache`.

```bash
docker compose up -d --build
```

Check the guard is on: `curl -fsS http://localhost:8501/api/health` shows
`"guards": {"toxicity": "active", …}`; `"missing"` means the image was built
without the token (the app also logs `toxicity check is OFF` at ERROR level when it
starts). To make a missing validator stop the app instead, add
`REQUIRE_TOXICITY_GUARD=1` to `.env`: the server then refuses to start (and
`restart: unless-stopped` keeps retrying, visible in `docker compose ps` and the
logs) until the image is rebuilt with the token. It is off by default so a build
without the token still runs.

**Upgrading a server that has the old hand-applied patch** (local changes to
`Dockerfile`, `docker-compose.yml` and `.gitignore` for `gr_token`): the repo now
carries this setup, so drop the patch once and keep the token file:

```bash
git diff > ~/guardrails-patch-backup.diff                  # a copy, just in case
git checkout -- Dockerfile docker-compose.yml .gitignore
git pull
echo 'COMPOSE_FILE=docker-compose.yml:docker-compose.guardrails.yml' >> .env
docker compose build --no-cache && docker compose up -d
```

First boot runs `ingest.py` to build the Chroma vector DB from `docs/` (~1 min,
uses the OpenAI key). Watch it finish:

```bash
docker compose logs -f
# Wait for "Uvicorn running on http://0.0.0.0:8501", then Ctrl-C to stop tailing.
```

## 4b. Create the first login

Skip this if everyone signs in with Google / Microsoft (step 4c). Otherwise
create one account per person (it asks for the password, minimum 8 characters).
Accounts are public users unless created with `--role hr` or `--role admin`
(admin is HR, plus who may sign in):

```bash
docker compose exec knowledgebase python auth.py add admin --role admin
```

Add more people the same way. Also available:
`python auth.py list`, `python auth.py role <username> <public|hr|admin>`,
`python auth.py remove <username>`. Accounts are stored in the database,
`./config/app.db` on the host (mounted into the container, gitignored).

**Upgrading from `config/users.json` / `config/sso.json`:** the first start of
this version copies both into the database, once. After that the files are no
longer read (keep them as a backup); make changes with `python auth.py`.

## 4c. Single sign-on

There are two kinds of sign-in provider:

- **Each workspace's own** (its Okta, Entra ID, Google Workspace SAML app, …):
  set up by the workspace's **admin** in the app, under **Access**, with no server
  change. [sign-in-guide.md](sign-in-guide.md) is the guide to hand them. As the
  server operator you only need to:
  - set `PUBLIC_URL` (step 1 below) **and keep it**: the addresses each workspace
    registered at its provider (redirect URIs, SAML entity IDs and ACS URLs)
    contain it, so changing it breaks every workspace's sign-in until they update
    their provider;
  - give the workspace an admin: `python auth.py add <name> --workspace <slug> --role admin`
    (or `python auth.py role <their email> admin --workspace <slug>` for someone who
    will sign in with single sign-on);
  - back up `config/app.db` **and** the key it is encrypted with
    (`config/integrations.key`, or `INTEGRATIONS_KEY`): one is useless without the
    other, so keep them in different places (see [Backups and restore](#backups-and-restore)).
  SAML needs the `xmlsec1` program, which the Docker image includes.
- **Server-wide** providers, set in `.env` and offered to everyone: Google,
  Microsoft and any OpenID Connect provider listed in `OIDC_PROVIDERS`. The steps
  below set those up; skip them if every workspace brings its own.

1. Set `PUBLIC_URL` in `.env` to the site's `https://` address.
2. **Google:** in Google Cloud -> APIs & Services -> Credentials, use (or create)
   an OAuth client of type *Web application* and add the redirect URI
   `<PUBLIC_URL>/api/auth/sso/google/callback`. Put its id and secret in
   `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (shared with the Drive integration).
   Only Google **Workspace** accounts can sign in, never personal Gmail ones.
3. **Microsoft:** in Entra ID -> App registrations, register a *single tenant*
   app with the Web redirect URI `<PUBLIC_URL>/api/auth/sso/microsoft/callback`,
   create a client secret, and set `MICROSOFT_CLIENT_ID`,
   `MICROSOFT_CLIENT_SECRET` and `MICROSOFT_TENANT_ID` (the Directory / tenant id).
4. **Okta, Keycloak, Auth0 or any other OpenID Connect provider:** create a
   *Web* app (authorization code flow, with a client secret) whose redirect URI is
   `<PUBLIC_URL>/api/auth/sso/<id>/callback`, where `<id>` is a short name you
   choose (e.g. `okta`). Then in `.env`: `OIDC_PROVIDERS=okta`, plus
   `OIDC_OKTA_ISSUER` (the issuer URL, e.g. `https://dev-123456.okta.com/oauth2/default`
   or `https://keycloak.example.com/realms/<realm>`), `OIDC_OKTA_CLIENT_ID`,
   `OIDC_OKTA_CLIENT_SECRET` and, optionally, `OIDC_OKTA_LABEL` for the button.
   List several ids separated by commas. These providers must mark the email as
   **verified**; only add one your organisation controls, because anyone who can
   get a verified address on your domains there can sign in.
5. Set `SESSION_SECRET` to a long random string.
6. Say who may sign in, and who is HR / Admin:

   ```bash
   docker compose exec knowledgebase python auth.py domain add cloudastra.in
   docker compose exec knowledgebase python auth.py domain add cloudastra.co
   docker compose exec knowledgebase python auth.py role someone@cloudastra.in hr
   ```

   Anyone with an address on an added domain gets in as a public user, and is
   added to `python auth.py list` the first time. `role` works before or after
   someone's first sign-in. Other addresses are turned away. Add every domain of
   your Google Workspace, primary one included. Access is checked on every
   request, so changes apply straight away (no restart): `auth.py domain remove`,
   `auth.py remove`, `auth.py disable` or a lower role takes effect on the
   person's next click. A workspace's admin can do all of this in the app
   (**Access**) instead; domains they add there must pass a DNS check, while
   `auth.py domain add` trusts you.
7. `docker compose up -d` to pick up the new `.env`. The login page now shows
   *Continue with Google* / *Microsoft* / *Okta* … for each provider set up.
   Set `PASSWORD_LOGIN=off` to remove the username + password form.

## 5. Open the firewall

```bash
sudo ufw allow 8501/tcp     # skip if your cloud uses security-groups instead
```

## 6. Confirm it's up

```bash
docker compose ps           # STATUS should show "Up"
curl -fsS http://localhost:8501/api/health && echo OK   # "toxicity": "active" with the Hub token (step 4)
```

Open **`http://<SERVER_IP>:8501`** in a browser.

---

## 7. Auto-start on reboot

The container has `restart: unless-stopped`, so it relaunches when the Docker
daemon starts. Ensure the daemon itself starts on boot:

```bash
sudo systemctl enable docker containerd
systemctl is-enabled docker containerd    # both should print: enabled
```

Test:

```bash
sudo reboot
# reconnect after ~30s:
cd genai && docker compose ps              # STATUS: Up
```

---

## Day-2 operations

| Task | Command (run inside the `genai` folder) |
|---|---|
| View logs | `docker compose logs -f` (Docker keeps the last 5 × 10 MB per container: `logging` in `docker-compose.yml`) |
| Stop | `docker compose down` |
| Restart | `docker compose restart` |
| Deploy new code | `git pull && docker compose up -d --build` (no local patch to re-apply; the Guardrails token comes from step 4's `COMPOSE_FILE`) |
| Rebuild vector DB from scratch | `docker compose down -v && docker compose up -d --build` |
| Re-index after editing `docs/` by hand | `docker compose exec knowledgebase python ingest.py && docker compose restart` |
| Shell into the container | `docker compose exec knowledgebase sh` |

## Health checks and error tracking

- `GET /api/health` — the process is up. Always 200 while it runs; the Docker
  `HEALTHCHECK` uses it. Don't point anything that restarts the container at a
  deeper check: a restart fixes none of what it finds. Its `guards` field says
  whether the toxicity check is running (`"toxicity": "active"` or `"missing"`,
  step 4).
- `GET /api/health/ready` — the server can actually serve: `config/app.db` opens and
  answers a query, the Chroma index opens, and the encryption key loads **and
  decrypts a stored secret** (a changed `INTEGRATIONS_KEY` otherwise breaks every SSO
  provider and integration silently). With `REQUIRE_TOXICITY_GUARD=1` it also
  checks the toxicity guard is loaded (`toxicity_guard`); without it, a missing
  guard is only reported under `guards`. 503 with the failing check otherwise:

  ```json
  {"ok": false, "checks": {"database": "ok", "search_index": "ok",
   "integrations_key": "the encryption key doesn't decrypt the stored secrets"},
   "guards": {"toxicity": "active", "toxicity_required": false}}
  ```

  Point an external uptime monitor (Better Stack, UptimeRobot, Uptime Kuma on
  another host) at `https://<your domain>/api/health/ready`, and check it after
  every deploy: `curl -fsS http://localhost:8501/api/health/ready`.
- Errors: failed chat answers, re-indexes, connector syncs and agent tool calls
  are logged (`docker compose logs`) with their workspace, never with the
  question. Set `SENTRY_DSN` (Sentry, or a self-hosted GlitchTip) and `APP_ENV`
  in `.env` to also send them to an error tracker; request bodies, query strings,
  cookies and `Authorization` headers are scrubbed first (`observability.py`).

## Backups and restore

`scripts/backup.sh` makes one timestamped archive of everything the app keeps:
a consistent online copy of `config/app.db` (SQLite's backup API, safe while the
app runs), the rest of `config/`, `docs/`, `data/` and the Chroma volume
(`<folder>_chroma_data`, e.g. `knowlegebase_chroma_data`), plus a `MANIFEST` of
row counts. It needs only bash, tar and Docker on the host; the SQLite steps use
the host's `python3`, or the app image's Python if there is none.

**The encryption key is never in the archive.** `config/integrations.key` decrypts
every workspace's OAuth tokens and SSO secrets in `app.db`; an archive holding both
would hand all of them to whoever gets a copy. The script copies the key to
`KEY_BACKUP_DIR` (default `~/kb-integrations-key`, outside `BACKUP_DIR`, never
synced off-site by it) and prints where it went. Also store the key once in the
team password manager. If the key comes from `INTEGRATIONS_KEY` in `.env`, keep
that value there instead; `.env` is not in the archive either.

Settings go in a file kept outside the repo, e.g. `/root/kb-backup.env`
(`chmod 600`); every option is listed at the top of `scripts/backup.sh`:

```bash
BACKUP_DIR=/var/backups/knowlegebase
KEY_BACKUP_DIR=/root/kb-integrations-key
RETENTION_DAYS=14                        # local archives kept this many days
# Encrypt to a PUBLIC key; its private half stays off this server (password manager),
# so a stolen server can't read its own backups. age-keygen makes the pair.
BACKUP_AGE_RECIPIENT=age1...
# Off-site: an rclone remote (rclone config) ...
BACKUP_RCLONE_REMOTE=b2:kb-backups/prod
REMOTE_RETENTION_DAYS=90
# ... or a restic repository (kept 7 daily / 4 weekly / 6 monthly)
# BACKUP_RESTIC_REPO=s3:s3.eu-central-1.amazonaws.com/kb-backups
# RESTIC_PASSWORD_FILE=/root/.restic-password
# Dead-man alert: pinged after each good run, /fail after a bad one
BACKUP_PING_URL=https://hc-ping.com/<uuid>
```

Run it once by hand, then nightly from root's crontab (`crontab -e`), and before
each deploy:

```bash
BACKUP_ENV_FILE=/root/kb-backup.env /root/knowlegebase/scripts/backup.sh
# crontab: every night at 02:30, logging to a file
30 2 * * * BACKUP_ENV_FILE=/root/kb-backup.env /root/knowlegebase/scripts/backup.sh >> /var/log/kb-backup.log 2>&1
```

(Install `age` and `rclone` or `restic` first, from the distribution's packages.)

**Restore** always goes into a new, empty folder, and checks what it unpacked:
every table's row count and the document counts against the `MANIFEST`, the
database's and the index's integrity, and that the key decrypts the stored secrets.

```bash
rclone copy b2:kb-backups/prod/kb-backup-20260101T023000Z.tar.gz.age /root/restore-in/
scripts/restore.sh --identity /path/to/age-private-key.txt \
    --key /root/kb-integrations-key/integrations-<fingerprint>.key.age \
    /root/restore-in/kb-backup-20260101T023000Z.tar.gz.age /root/kb-restored
```

It exits non-zero on any mismatch. `/root/kb-restored` is then laid out like the
app folder (`config/`, `docs/`, `data/`, `chroma_db/`). To put it in service:

```bash
cd /root/knowlegebase && docker compose down
mv config config.old && mv docs docs.old && mv data data.old
cp -a /root/kb-restored/config /root/kb-restored/docs /root/kb-restored/data .
docker run --rm -v knowlegebase_chroma_data:/v -v /root/kb-restored/chroma_db:/src:ro \
    --entrypoint sh cloudastra-knowledgebase -c 'rm -rf /v/* && cp -a /src/. /v/'
docker compose up -d && curl -fsS http://localhost:8501/api/health/ready
```

**Drill**: once a month, restore the latest off-site archive on another machine
(or a throwaway folder), run the app against it and check `/api/health/ready`
and a sign-in. A backup that was never restored is a hope, not a backup.

## Managing documents from the UI

Sign in with an HR / Admin account and open **Documents** in the top bar to view,
edit, add, upload (TXT/PDF) or delete documents; every change is re-indexed
automatically (progress shows in the top bar). `docs/` is mounted
from the host (`./docs:/app/docs`), so UI changes survive rebuilds — but they are
**not committed to git**. A `git pull` that touches the same files may conflict;
commit them from the server or copy them back if you want them in the repo.

## Troubleshooting

- **Page won't load right after `up`** — first boot is still building Chroma.
  `docker compose logs -f` until you see the `Uvicorn running` line.
- **Someone can't sign in with Google / Microsoft** — the login page says why.
  "Isn't set up for this app": `python auth.py domain add <their domain>`.
  "Didn't finish": `docker compose logs` shows the provider's error (e.g. a
  redirect URI that doesn't match `PUBLIC_URL`, or admin consent needed).
- **An admin lost the Documents screen after upgrading** — password accounts
  made before roles existed are public: `python auth.py role <name> hr`.
- **A workspace admin's SAML setup says xmlsec1 isn't installed** — outside
  Docker, install it (`apt-get install xmlsec1`) or set `XMLSEC_BINARY`.
- **Testing a provider that runs on this machine or your network** (e.g.
  Keycloak in Docker) says it "points to a private network address" — the server
  only calls public addresses. For local testing, list it in `.env`:
  `SSO_ALLOW_PRIVATE_HOSTS=localhost:8180`.
- **"You've asked 20 questions in the last minute"** (or "Your workspace has…") —
  chat, uploads, **Re-index now** and **Sync now** are limited per person and per
  workspace, per minute. Raise them in `.env`: `RATE_LIMIT_CHAT_USER` (default 20),
  `RATE_LIMIT_CHAT_WORKSPACE` (300), and the same `_USER` / `_WORKSPACE` pair for
  `UPLOAD` (30 / 120), `REINDEX` (5 / 20) and `SYNC` (5 / 20).
- **An upload is refused as too large** — files can be up to `UPLOAD_MAX_FILE_MB`
  (25) each and `UPLOAD_MAX_REQUEST_MB` (100) per upload; the second also caps any
  other request body. Behind nginx, `client_max_body_size` must be at least as big
  (nginx's default is 1 MB).
- **Security headers** — every response carries a Content-Security-Policy,
  `X-Frame-Options: DENY`, `X-Content-Type-Options` and `Referrer-Policy`, and over
  https `Strict-Transport-Security` (`HSTS_MAX_AGE`, default 180 days; `0` turns it
  off). Behind a proxy that ends TLS, uvicorn learns it was https from
  `X-Forwarded-Proto`, so `FORWARDED_ALLOW_IPS` (below) must include the proxy.
- **`/docs` doesn't show the API pages** — FastAPI's own pages (`/docs`, `/redoc`,
  `/openapi.json`) list every endpoint, so they are off unless `APP_ENV` is `dev`
  or `local` (a developer's machine) or `API_DOCS=on` is set. Leave both unset in
  production.
- **"Too many attempts. Wait N seconds"** — the sign-in endpoints are rate-limited
  per address (and overall), and the address comes from the reverse proxy's
  `X-Forwarded-For` header. uvicorn only believes that header from the addresses
  in `FORWARDED_ALLOW_IPS`. The image's default is
  `127.0.0.1,::1,172.16.0.0/12,192.168.0.0/16`: loopback plus the private ranges
  Docker gives its bridge networks. nginx on the host, proxying to port 8501,
  reaches the container from the bridge gateway (e.g. `172.18.0.1`), so it is
  trusted, while a client connecting to 8501 directly keeps its public address and
  cannot pose as anyone else. If everyone shows up as one address (all hitting one
  limit), the proxy isn't in that list — e.g. a proxy on another host, or Docker
  set up with custom `default-address-pools` such as `10.x`. Set
  `FORWARDED_ALLOW_IPS` in `.env` to the proxy's address as the container sees it
  (comma-separated, CIDR allowed); for nginx on the host that is the network's
  gateway: `docker network inspect <folder>_default -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}'`.
  Never `*`: it trusts the header from anyone. With nginx on the same host, also
  consider publishing the port on loopback only (`"127.0.0.1:8501:8501"`) so
  nothing can bypass nginx.
- **`/api/health` shows `"toxicity": "missing"`** — the image was built without
  the Guardrails Hub token; see step 4. With `REQUIRE_TOXICITY_GUARD=1` the
  container instead keeps restarting and its log ends with
  `REQUIRE_TOXICITY_GUARD is set but the Guardrails Hub toxicity validator is not installed`.
- **Someone is locked out after "Require single sign-on" was turned on** — only
  admins keep passwords. If the provider itself is broken, sign in as an admin
  with a password and turn it off under **Access → Sign-in**, or run
  `python auth.py role <name> admin`.
- **`OPENAI_API_KEY` errors in logs** — the `.env` key is missing/wrong. Fix it,
  then `docker compose up -d --build`.
- **Port 8501 unreachable** — firewall (step 5) or the cloud provider's
  security-group is still blocking it.
- **Out of memory during build** — this stack (onnxruntime + guardrails) is
  heavy; use a VPS with **≥ 2 GB RAM**.
