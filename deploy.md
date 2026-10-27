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
| Deploy new code | `scripts/deploy.sh main` or a release tag, `scripts/deploy.sh v0.2.0`: backup, build, health gate, automatic rollback ([Deploying](#deploying)) |
| Go back to the version before | `scripts/deploy.sh --rollback` ([Deploying](#deploying)) |
| Which version runs | `curl -fsS http://localhost:8501/api/health` (`version`, `commit`); `cat .deploy/current .deploy/history` |
| Rebuild vector DB from scratch | `docker compose down -v && docker compose up -d --build` |
| Re-index after editing `docs/` by hand | **Re-index** on the Documents screen (only changed files are embedded again), or a full rebuild: `docker compose exec knowledgebase python ingest.py` |
| See the background jobs | `docker compose exec knowledgebase python -c "import db; [print(tuple(r)) for r in db.connect().execute('SELECT id, kind, workspace, status, attempts, error FROM jobs ORDER BY id DESC LIMIT 20')]"` |
| Shell into the container | `docker compose exec knowledgebase sh` |
| Which database migration `config/app.db` is at | `docker compose exec knowledgebase alembic current` |
| Who changed what (the admin audit log) | **Access > Activity** (filter, export as CSV or JSON lines), or `GET /api/admin/audit/verify` to check its hash chain. Kept for good; `python auth.py` changes show as the operator (`cli`) |

**Email** (invites from Access > Members) goes through `MAIL_BACKEND` in `.env`:
`smtp` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`; STARTTLS),
`resend` (`RESEND_API_KEY`) or `postmark` (`POSTMARK_TOKEN`), from `MAIL_FROM`.
The default, `console`, sends nothing and writes each email to the log, invite
link included: set a real backend before admins start inviting people.

**Database upgrades** happen by themselves: a new version upgrades
`config/app.db` in place on its first request, in one transaction, keeping every
row (an older file, from before migrations, is detected and brought up to date
too). The log says `Database upgraded from 0001 to 0002` (and, once, `Bringing a
database from before migrations to the baseline`) when it does. Take a backup (`scripts/backup.sh`) before deploying
a release that adds a migration: there is no automatic way back down. How
migrations work and how to add one: [ARCHITECTURE.md](ARCHITECTURE.md), "Database migrations".

## Deploying

`scripts/deploy.sh` deploys one version and checks it can serve before calling it
done. Run it as root in the app folder:

```bash
scripts/deploy.sh --dry-run main     # every step it would take; changes nothing
scripts/deploy.sh main               # the newest main (or a tag: v0.2.0, or a commit)
```

What it does, in order (the header of `scripts/deploy.sh` has every option):

1. `git fetch`, and which commit the branch, tag or commit you named is.
2. `scripts/backup.sh` (with `BACKUP_ENV_FILE`, as cron runs it: export it first),
   plus a quick copy of `config/app.db` into `.deploy/db/`.
3. Checks the commit out (detached `HEAD`). If local changes to tracked files would
   be overwritten (the old Guardrails hand patch, step 4) it stops here and the old
   version keeps running. Then `docker compose build`, with the Guardrails secret
   from step 4's `COMPOSE_FILE`; the image is tagged
   `cloudastra-knowledgebase:<first 12 characters of the commit>`.
4. `docker compose up -d` with that tag, and waits (`--timeout`, default 300 s) for
   `/api/health/ready` to answer 200.
5. Ready: records it in `.deploy/current`, `.deploy/previous` and `.deploy/history`,
   tags it `:latest` (so a plain `docker compose up -d` keeps it), and removes all
   but the last few older image tags (`KEEP_IMAGES`, default 3).
   **Not ready** (or it didn't start): stops it, puts back the database copy from
   step 2 if its migrations changed `config/app.db`, checks the previous commit out
   and starts the previous image, which is still there, so no rebuild. It exits 1
   with the previous version running, or 3 if even that failed (look at
   `docker compose logs` now).

Downtime is the restart, a few seconds (one container: sign-in codes and running
answers in memory are lost, as before). A failed build changes nothing that runs.
If the database is put back, anything written between the new version starting and
the rollback is lost; the backup from step 2 still has the state before the deploy.

**Going back later**: `scripts/deploy.sh --rollback` starts the version before the
current one (`.deploy/previous`). If the current version migrated the database,
the older one can't start on it (Alembic refuses a revision it doesn't know), so the
script stops and asks for `--rollback --restore-db`, which puts back the copy taken
just before the current version was deployed: everything written since is lost.
Prefer fixing forward when that is more than a few minutes of use.

**First deploy with the script on an existing server**: drop the Guardrails hand
patch first (step 4, "Upgrading a server that has the old hand-applied patch").
The running image is tagged after the current commit so the first deploy can go
back to it. The server's checkout is on a detached `HEAD` from then on: deploy with
`scripts/deploy.sh <branch or tag>`, not `git pull`.

**Images from the registry.** A release tag also builds an image on GHCR,
`ghcr.io/cloudastra-tech/knowlegebase:<version>` (`.github/workflows/release.yml`).
It is built **without the Guardrails Hub token**: installing the toxicity validator
writes the token into the image (`/root/.guardrailsrc`), and an image in a registry
must never carry it, so the workflow checks that it doesn't. Such an image runs
without the toxicity check (`/api/health` says `"toxicity": "missing"`); with
`REQUIRE_TOXICITY_GUARD=1` it refuses to start, and the deploy goes back. So
production keeps **building on the server** (`scripts/deploy.sh v0.2.0`), with the
token, until the validator gets its token at run time instead.
`scripts/deploy.sh --image 0.2.0` runs a registry image (pulled, tagged and rolled
back the same way, the code checked out at its tag) where the toxicity check isn't
needed, e.g. a staging box. If the package is private (the org's Packages
settings), `docker login ghcr.io` with a token that can read packages first.

**Deploying from GitHub Actions** is not wired up: deploys stay manual until the
owner adds the secrets. The sketch at the bottom of `.github/workflows/release.yml`
is the whole job: a `production` environment with required reviewers, a deploy key
that may only run `scripts/deploy.sh` (`command=` in `authorized_keys`), and the
host, user, key and `known_hosts` as environment secrets. The job then runs
`scripts/deploy.sh <tag>` over SSH after the image is built, once a reviewer approves.

## Releases

`main` is production and only moves by promotion:

1. Features merge into `dev` (squash; CI runs `backend`, `frontend`, `e2e`,
   `docker`, `scan`).
2. **Promote**: when the owner says so, a PR from `dev` into `main`, merged with a
   merge commit (not squash, so the two histories don't drift).
3. **Release PR**: on every push to `main`, release-please
   (`.github/workflows/release-please.yml`) opens or updates
   "chore(main): release X.Y.Z", whose `CHANGELOG.md` entry lists the `feat:` and
   `fix:` commits since the last release (version rules: Conventional Commits;
   before 1.0 a `feat` bumps the minor version, a `fix` the patch).
4. Merging it tags `vX.Y.Z`, publishes a GitHub release and builds the registry
   image. Deploy that tag: `scripts/deploy.sh vX.Y.Z` (above).
5. **Back-merge**: a PR from `main` into `dev`, so the release commit
   (`CHANGELOG.md`, `version.txt`) is on `dev` too.

Hotfixes branch from `main`, go by PR into `main` (release PR, tag, deploy as
above), and are merged back into `dev` the same way.

GitHub runs no workflows for PRs or tags made with the default `GITHUB_TOKEN`, so
the release PR gets no CI checks unless the repo secret `RELEASE_PLEASE_TOKEN` (a
fine-grained token or GitHub App token with contents and pull-request write) is
set; without it, close and reopen the release PR to run them. The image is built
either way. Config: `release-please-config.json`, `.release-please-manifest.json`
(0.1.0 stands for what production ran before releases were tagged).

## Background jobs

Re-indexing, connector syncs (every 30 minutes, and on **Sync now**) and the SAML
metadata refresh are **jobs** in the `jobs` table of `config/app.db`
(`kb/jobs.py`). They survive a restart: a job that was running when the server
stopped is picked up again about a minute later (when its lease runs out). A
failed job is tried again after 30 s, 1 min, ... (3 tries for an index or a sync),
then shows as failed: an index error in the top bar, a sync error on the
Integrations page. Two jobs run at once (`JOB_WORKERS`), never two index runs of
one workspace, and workspaces take turns.

Re-indexing is **incremental**: an upload, edit or delete embeds only that
document's changed chunks, and changing who may ask about a connection's files
only re-tags them. Questions are answered throughout. **Rebuild everything**
(`POST /api/reindex?full=true`, or `python ingest.py`) builds a new index beside
the current one, which keeps answering until the new one is ready; a failed
rebuild leaves the old one in place. The first re-index of a workspace after
upgrading to this version is such a rebuild (it gives every chunk a stable id),
and so is the first one after the chunking or embedding settings change. If you
roll back to a version from before it, press **Re-index** once: the old version
reads the old collection, which this one drops 10 minutes after its rebuild.

## Connectors

Google Drive, Notion, Outline, Confluence and Jira (Cloud and Data Center),
SharePoint & OneDrive, Zendesk, GitHub and websites are **connectors**
(`connectors/registry.py`), all synced the same way (`kb/services/sync.py`): each
run asks the source only for what changed since its last cursor (Drive's change
feed, Notion's and Outline's newest edits, a Confluence CQL search on
`lastmodified`, a Jira JQL search on `updated`, each SharePoint library's delta
link, Zendesk's incremental exports, a GitHub compare from the last synced commit;
a website is crawled again with conditional requests), writes changed documents to
`<docs>/connectors/<provider>/`, records them in `source_items` (which replaced
each folder's `manifest.json`: an existing manifest is imported the first time
it's met and left in place) and re-indexes exactly those documents. A full
listing still runs on the first sync, when the picks change, and every 6 hours
for Notion and Outline (every 24 hours for Confluence, Jira, SharePoint, Zendesk
articles and GitHub, which also refreshes captured permissions), to find deletions.

- **Errors** show on the Integrations page in plain words, with the sync history
  (the last 50 runs) and skipped items. "Access expired" and "settings need
  attention" wait for an admin; the rest are retried.
- **Mass deletes.** A sync that would remove more than
  `CONNECTOR_MASS_DELETE_PERCENT` (default 30) % of a connection's items, and at
  least `CONNECTOR_MASS_DELETE_MIN` (default 10), removes nothing and asks an
  admin to confirm on the card. Changing what a connection syncs is that
  confirmation.
- **Limit.** One connection syncs at most `CONNECTOR_MAX_ITEMS` (default 10000;
  0 = no limit) items; a sync that stops there says so on the card and deletes
  nothing.
- **Outline.** Each workspace can connect its own wiki (address + API token,
  stored encrypted, collections picked on the card). The `.env` wiki
  (`OUTLINE_URL` / `OUTLINE_API_TOKEN`, default workspace only) is still searched
  live as before, unless the workspace has an Outline connection: then only the
  synced copy is searched, under that connection's access level.
- **Confluence** (Cloud). Create an OAuth 2.0 integration at
  developer.atlassian.com/console/myapp: *Authorization* → OAuth 2.0 (3LO), callback
  URL `<PUBLIC_URL>/api/integrations/confluence/callback`; *Permissions* →
  Confluence API → granular scopes `read:space:confluence`, `read:page:confluence`,
  `read:blogpost:confluence`, `read:attachment:confluence`,
  `read:content-details:confluence`, `read:content.restriction:confluence`;
  *Distribution* → Sharing (so other organisations' sites can install it). Put its
  client id and secret in `ATLASSIAN_CLIENT_ID` / `ATLASSIAN_CLIENT_SECRET`
  (`ATLASSIAN_SCOPES` overrides the scope list if Atlassian renames one). A
  connection reaches the first Confluence site the admin approves. Atlassian
  meters 3LO apps in points per hour shared by every customer of the app: syncs
  search only what changed and list everything once a day; apply for the
  per-tenant tier before many workspaces connect.
- **Confluence Data Center** (7.9+) needs nothing on the server: a workspace admin
  pastes the site's address and a personal access token. The address goes through
  the same public-address check as Outline's (`CONNECTOR_ALLOW_PRIVATE_HOSTS` for a
  test server). Space permissions aren't readable through Data Center's REST API,
  so only page restrictions are captured there.
- **Jira** (Cloud) uses the **same Atlassian app** as Confluence: in its
  *Permissions* add the Jira API with the classic scopes `read:jira-work` and
  `read:jira-user` (the sync asks for these plus `offline_access`; `JIRA_SCOPES`
  overrides them). An Atlassian app has **one** callback URL, so Jira comes back
  on Confluence's, `<PUBLIC_URL>/api/integrations/confluence/callback`: keep that
  one even if only Jira is used. Projects are picked on the card; each issue is
  one document (summary, key fields, description, comments; comments restricted
  to a group or role are left out). New issues sync if updated within
  `JIRA_BACKFILL_DAYS` (default 365; 0 = all). The project's browse permissions
  (read from its permission scheme, which needs a Jira or project admin to
  connect) and each issue's security level are captured. Jira's and
  Confluence's calls share the app's hourly points quota.
- **Jira Data Center** (8.14+) is like Confluence Data Center: the site's address
  and a personal access token, nothing on the server.
- **Zendesk.** A workspace admin enters the Zendesk subdomain and either an
  agent's or admin's email + API token (nothing on the server), or, when the
  server has `ZENDESK_CLIENT_ID` / `ZENDESK_CLIENT_SECRET`, signs in with Zendesk
  OAuth (redirect URL `<PUBLIC_URL>/api/integrations/zendesk/callback`, scopes
  `hc:read tickets:read users:read`, `ZENDESK_SCOPES` overrides). An OAuth client
  made in one Zendesk account only works for that account: serving other
  companies needs a **global OAuth client, which only Zendesk grants** (build a
  local client whose identifier starts with `zdg-` in a `d3v-` sponsored test
  account, then request global status in the Marketplace portal; allow about a
  week). Help Center articles sync (all, or the categories / sections picked);
  their user segments are captured. **Solved tickets** are off by default; turning
  them on (the picker's switch) locks the connection to HR and admins while they
  sync (`ZENDESK_TICKETS_BACKFILL_DAYS`, default 365). Incremental exports are
  limited to 10 calls a minute per Zendesk account.
- **GitHub.** Create a **GitHub App** (organisation or personal Settings →
  Developer settings → GitHub Apps → New): *Callback URL*
  `<PUBLIC_URL>/api/integrations/github/callback`, tick **Request user
  authorization (OAuth) during installation** (it replaces the Setup URL), keep
  *Expire user authorization tokens*, untick *Webhook → Active* (not used yet);
  *Repository permissions*: Contents **read**, Metadata **read** (Administration
  read only if repo teams should be captured); *Where can this app be installed*:
  Any account. Generate a private key and a client secret, then set
  `GITHUB_APP_ID`, `GITHUB_APP_SLUG` (the app's URL name), `GITHUB_APP_CLIENT_ID`,
  `GITHUB_APP_CLIENT_SECRET` and `GITHUB_APP_PRIVATE_KEY` (the PEM, newlines as
  `\n`) or `GITHUB_APP_PRIVATE_KEY_FILE`. Connecting installs the app on the
  admin's organisation; repositories are picked on the card. READMEs and docs
  (`.md`, `.mdx`, `.rst`, `.txt`) sync; code only for repositories where it's
  turned on, with the file types listed in the picker (size caps
  `GITHUB_MAX_DOC_BYTES` 1 MB, `GITHUB_MAX_CODE_BYTES` 256 KB). Wikis aren't
  synced (the REST API doesn't serve them). Webhooks (push, repository and
  membership events) are a follow-up; until then each sync compares the default
  branch with the last synced commit.
- **SharePoint & OneDrive.** Register a *multi-tenant* app in Entra ID (App
  registrations → New: "Accounts in any organizational directory"), Web redirect
  URI `<PUBLIC_URL>/api/integrations/sharepoint/callback`, a client secret, and
  API permissions → Microsoft Graph → *Application* `Sites.Read.All` (plus the
  default delegated `User.Read`; sign-in uses `openid profile email`). Put them in
  `SHAREPOINT_CLIENT_ID` / `SHAREPOINT_CLIENT_SECRET`. Connecting needs an admin
  of the customer's tenant: they sign in, then approve the app for their
  organisation. Customers whose policy blocks unverified apps need publisher
  verification on the registration. Change notifications (webhooks) are not
  used yet: libraries are polled with their delta link.
- These capture source permissions (Confluence space permissions and page
  restrictions; Jira project browse grants and security levels; SharePoint item
  permissions; Zendesk user segments; GitHub visibility and collaborators) into
  each item's `acl` for the per-user filtering to come. They are **not enforced** yet: who may ask is the
  connection's access level, as for every connector.
- **Websites** must be https on a public address (checked again before every
  request, redirects included). To crawl a test site on this machine, list it
  in `.env` as `CONNECTOR_ALLOW_PRIVATE_HOSTS=localhost:8000` (it may then use
  http); this list is separate from `SSO_ALLOW_PRIVATE_HOSTS`. Never set it in
  production.

By default the jobs run inside the web process. To move the connector syncs and
the SAML refresh to a process of their own (the same image, `.env` and volumes),
give the web service `JOB_KINDS=index,retag,drop_collection,slack` and start a worker
beside it that runs the rest:

```yaml
  worker:            # in docker-compose.yml, next to the knowledgebase service
    build: .
    env_file: .env
    environment:
      - JOB_KINDS=sync,sync_all,saml_refresh,jobs_prune
    volumes: [the same as the knowledgebase service]
    command: python -m kb.worker
```

The index jobs stay in the web process for now: the search index is an
embedded Chroma folder, which the process that answers questions should also be
the one writing. Slack's answers (`slack`) stay with them: they answer from it. Once Chroma runs as a server (engineering plan P2-1), set
`JOBS_IN_PROCESS=off` on the web service (it then only queues jobs) and drop
`JOB_KINDS` from the worker so it runs them all. `JOB_WORKERS` is the number of
jobs a process runs at once (default 2); several workers may run, since a job is
claimed in the database first.

Two more periodic jobs feed the admin Insights screen: `insights_rollup` (daily:
rolls up the question log and clears question text past each workspace's
retention) and `insights_gaps` (weekly: embeds unanswered questions and names
each group with one small model call). Put them in a worker's `JOB_KINDS` with
the other periodic jobs if you split the work. Optional knobs:
`INSIGHTS_NEVER_CITED_DAYS` (30), `INSIGHTS_STALE_DAYS` (180),
`INSIGHTS_STALE_MIN_CITED` (3), `INSIGHTS_GAP_WINDOW_DAYS` (30),
`INSIGHTS_GAP_MIN_SIZE` (3) and `INSIGHTS_GAP_SIMILARITY` (0.75).

## Slack app

People can ask in Slack: a DM to the app, `@Knowledge` in a channel (answered in
the thread), `/ask` (only the asker sees the answer) and the message shortcut
"Ask about this thread". Answers cite their sources as numbered links and carry
👍 / 👎 buttons that land in the same feedback as the web chat's. An admin
installs it per workspace from **Integrations → Slack**.

**Who gets answered.** The Slack user's email (`users.info`) is the member they
are in the workspace that installed the app, with their role as it is now.
People with no account are refused (default) or answered from public documents
only (the card's setting); turned-off members, bots and people from another
organisation in a Slack Connect channel are never answered.

**The audience rule.** Anything posted where others can read it (a channel, a
thread, a group DM, `/ask` outside the asker's own DM with the app) is answered
from **public** documents only, whatever the asker's role. When a search as the
asker finds restricted passages, they get an ephemeral "I found restricted
sources; I've sent you a DM" and the full answer in their DM with the app. A
channel shared with another organisation gets no answer at all: the asker is
answered in a DM.

**Two ways to run the app.**

1. *This server's app* (hosted, for many companies): create one app from
   `slack/manifest.yml` with `PUBLIC_URL` in place of `https://YOUR-KNOWLEDGE-HOST`,
   turn on **Manage Distribution**, and put its Client ID, Client Secret and
   Signing Secret in `.env` as `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`,
   `SLACK_SIGNING_SECRET`. Every workspace's admin then clicks **Add to Slack**.
   Installing in workspaces other than your own is *distribution*: Slack's API
   terms (2025-10-10) require such an app to be listed in the Slack Marketplace,
   and Marketplace review expects at least 10 active workspaces and 10 weekly
   active users, a privacy policy saying how long data is kept and how the
   model uses it, no training on Slack data, and the fewest scopes possible
   ([terms](https://slack.com/terms-of-service/api),
   [change](https://docs.slack.dev/changelog/2025/10/13/api-terms-update),
   [guidelines](https://docs.slack.dev/slack-marketplace/slack-marketplace-app-guidelines-and-requirements)).
   Since 2025-05-29, commercially distributed apps that aren't in the Marketplace
   get `conversations.history` / `conversations.replies` at 1 request a minute and
   15 messages a call for new installs; internal apps keep 50+ a minute
   ([changelog](https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps/)).
   The app calls neither for answering; only the optional thread-reading of the
   shortcut and auto-answer would feel it.
2. *The customer's own app* (self-hosted, or a company that wants it internal):
   the admin chooses **Your own app**, copies the manifest the card shows (this
   server's URLs, and request URLs private to that app), creates the app in their
   Slack, pastes its three credentials, then clicks **Add to Slack**. The app is
   internal to their Slack, so none of the distribution rules above apply. The
   secrets are sealed (`secret_box`) and never shown again.

**Scopes** (bot, all in the manifest, each used): `app_mentions:read` (mentions),
`chat:write` (post, update, ephemeral notes), `commands` (`/ask` and the
shortcut), `im:history` (DMs to the app), `im:write` (`conversations.open`: the
DM a restricted answer goes to), `users:read` and `users:read.email`
(`users.info`: who is asking). `im:read` is **not** requested: nothing calls a
method that needs it. Auto-answer in help channels (off by default; posts only
confident, cited answers) needs `channels:history` and the `message.channels`
event added to the app; with `channels:history` (and `groups:history` for private
channels) the shortcut reads the whole thread instead of the one message.

**Requests** from Slack are checked against the app's Signing Secret
(`X-Slack-Signature`, at most 5 minutes old) before anything in them is read;
events are taken in once however often Slack retries them; each answer is a job
(`slack`), so Slack gets its reply within its 3 seconds. Every question is in
the question log (surface `slack`) and the audit log (`slack.ask`, its length,
never its text), as are installs and uninstalls (`slack.install`,
`slack.uninstall`, including one done from Slack's side).

### Checking it by hand

Needs a Slack workspace where you may install apps, and this server reachable
over https (Slack refuses http request URLs): in production `PUBLIC_URL`; on a
laptop a tunnel, e.g. `cloudflared tunnel --url http://localhost:5180` with
`PUBLIC_URL` set to the address it prints, then restart the API.

1. **Create the app.** As an admin, Integrations → Slack → *Your own app* →
   copy the manifest. At <https://api.slack.com/apps> → *Create New App* → *From a
   manifest* → pick the workspace → paste → *Create*. Slack checks the event
   request URL: *Event Subscriptions* shows **Verified**. (Or, for this server's
   app, set the three `SLACK_*` variables and skip to step 3.)
2. **Paste the credentials.** From *Basic Information → App Credentials*, paste
   Client ID, Client Secret and Signing Secret into the dialog → *Save app*. The
   card says **Not installed**; Activity (audit) shows `slack.app` with the client
   ID and no secret.
3. **Install.** *Add to Slack* → Slack's consent screen lists the seven scopes
   above → *Allow*. Back on Integrations: a toast "Installed in Slack", the card
   **Installed** with the Slack workspace's name; Activity shows `slack.install`.
4. **DM it.** In Slack, open the app (Apps → Knowledge → *Messages*) and ask
   something in your documents. "Looking that up…" appears within a second or
   two, then becomes the answer: numbered `[1]` links, a sources line, 👍 / 👎 and
   *Open in the app* (opens the saved chat). As an HR / Admin member, ask about a
   restricted document: the DM answers from it.
5. **Mention it in a channel.** `/invite @Knowledge` in a test channel, then
   `@Knowledge <question>` as a **public** member: the answer comes in the
   thread, from public documents only, and nobody gets a DM. Reply in the
   thread with another `@Knowledge` follow-up: it's answered with the thread's
   history (one chat in the app).
6. **The restricted-source DM.** As an **HR / Admin** member, `@Knowledge` the same
   restricted question in the channel: the thread gets the public-only answer,
   you (only) see "I found restricted sources; I've sent you a DM", and your DM
   with the app has "You asked in #channel: …" and the full answer. A colleague
   in the channel sees neither the note nor the restricted answer.
7. **/ask.** `/ask <question>` in the channel: an ephemeral "Looking that up…"
   replaced by the answer, visible only to you, still public-only (HR also gets
   the DM note). `/ask` in your DM with the app answers with your role. `/ask`
   alone shows how to use it.
8. **The shortcut.** On a message with a question, ⋮ → *Ask about this thread*:
   an ephemeral answer to it.
9. **Feedback.** Click 👎 on a channel answer as another member: "Thanks for
   telling us…" (ephemeral). In the app, Insights → Feedback lists it with the
   question and answer; Activity shows `feedback.set` with surface `slack`.
10. **Who's refused.** A Slack user with no account here: a polite refusal
    (ephemeral). Switch the card to *Answer from public documents only* and ask
    again: answered, public-only. Turn a member off in Members: they're refused.
11. **Uninstall.** *Uninstall* on the card → the app disappears from Slack;
    Activity shows `slack.uninstall` (by admin). Install again, then remove the
    app from Slack's side (*Manage apps → Remove app*): the card shows **Removed
    in Slack**, and Activity has `slack.uninstall` by `slack` (system).

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
- Logs: `docker compose logs` has one JSON object per line (`LOG_FORMAT=json`,
  set by `docker-compose.yml`; `text` is the default outside Docker), ready for a
  log shipper. Every request gets an id, the proxy's `X-Request-ID` if it sends
  one (nginx: `proxy_set_header X-Request-ID $request_id;`), sent back in the
  `X-Request-ID` response header, and one line when it ends:

  ```json
  {"ts": "2026-10-27T10:00:00.123Z", "level": "INFO", "logger": "kb.request",
   "msg": "POST /api/chat/stream 200 5321ms", "request_id": "9f1c…",
   "workspace": "acme", "user": "u_3f2a9c0d1e2b4a57", "method": "POST",
   "route": "/api/chat/stream", "status": 200, "duration_ms": 5321}
  ```

  Every other line logged while serving that request carries the same
  `request_id`, `workspace` and `user`; a background job's lines carry `job_id` and
  `job_kind` instead. `user` is a keyed hash of the account, never the email (set
  `LOG_USER_KEY`, or `SESSION_SECRET` is used); find someone's with
  `docker compose exec knowledgebase python -m kb.logs user-id someone@example.com`.
  Lines never hold a question, an answer, a query string or a token from a path
  (routes are logged as their template, `/s/{token}`). `LOG_LEVEL` (default `INFO`).
- Errors: failed chat answers, re-indexes, connector syncs and agent tool calls
  are logged (`docker compose logs`) with their workspace, never with the
  question. Set `SENTRY_DSN` (Sentry, or a self-hosted GlitchTip) and `APP_ENV`
  in `.env` to also send them to an error tracker; request bodies, query strings,
  cookies and `Authorization` headers are scrubbed first (`observability.py`).
  Each event is tagged with the `request_id` (or `job_id`) of its log lines, and
  its release is the image's version.

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
automatically, a document at a time (progress shows in the top bar). `docs/` is mounted
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
- **"This API key has made 60 calls in the last minute"** — calls with API keys
  (`/api/v1` and `/mcp`) are limited per key and per workspace:
  `RATE_LIMIT_API_KEY` (default 60) and `RATE_LIMIT_API_WORKSPACE` (600).
- **An MCP client can't connect** — behind nginx, `/mcp` must reach the app like
  `/api` does (the app serves it itself, at exactly `/mcp`, no trailing slash),
  with `proxy_buffering off` so answers aren't held back. The key must be a
  personal one, and Access → API & MCP must have the MCP server on.
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
