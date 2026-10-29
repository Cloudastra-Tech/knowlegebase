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

```bash
docker compose up -d --build
```

No build secret or token is needed. **Toxicity check:** every question is checked
for abuse, harassment, hate, threats, sexual content involving minors and
self-harm encouragement, for everyone (HR and admins too), and refused with
"Sorry, I can't help with that request." It needs no setting, download or token
(`safety.py`; it replaces the Guardrails Hub `toxic_language` validator, gone
with the Hub in 2026-10):

- A workspace whose text goes to OpenAI already (its chat, answer or embedding
  provider is OpenAI) is checked with OpenAI's moderation endpoint
  (`omni-moderation-latest`, free of charge), with its own OpenAI key, else the
  server's `OPENAI_API_KEY`. What it flags gets a second opinion from the
  workspace's fast model, so a name, a swear word quoted from a document or an HR
  complaint about harassment isn't refused.
- Any other workspace (Bedrock, Anthropic with local embeddings, or no OpenAI key)
  is checked by its own fast model alone: one short call per question. Its text is
  never sent to OpenAI for this.
- **It fails open.** If the moderation endpoint can't be reached, the fast model
  decides; if that fails too, the question goes through and the log says
  `The toxicity check failed`. The injection filter and the access rules still
  apply. (A question moderation flagged is refused when the fast model can't give
  its second opinion.) Nothing is called when the server starts, so an OpenAI
  outage never stops it starting.

| Setting | Effect |
|---|---|
| `TOXICITY_GUARD=off` | No toxicity check, for every workspace. `/api/health` says `"guards": {"toxicity": "off", …}` and the app logs `TOXICITY_GUARD=off` at WARNING when it starts. Default: on (`"toxicity": "active"`). |
| `REQUIRE_TOXICITY_GUARD=1` | Refuse to start with `TOXICITY_GUARD=off` (and `restart: unless-stopped` keeps retrying, visible in `docker compose ps` and the logs), so the check can't be turned off by mistake. `/api/health/ready` checks it too. |

The other checks are unchanged: the question length limit, the staff-pay rules,
the prompt-injection filter and PII redaction.

**Upgrading a server that used the Guardrails Hub token** (its `.env` has
`COMPOSE_FILE=docker-compose.yml:docker-compose.guardrails.yml`, and the folder has
`guardrails_token.secret`): nothing has to change for the deploy to work.
`docker-compose.guardrails.yml` is now an empty override, kept so that line still
works. To tidy up, once going back to a version from before the change is unlikely
(those versions' builds still read the token file):

```bash
sed -i '/^COMPOSE_FILE=docker-compose.yml:docker-compose.guardrails.yml$/d' .env
rm -f guardrails_token.secret
```

`docker-compose.guardrails.yml` can be deleted from the repo once no server's
`.env` names it. `REQUIRE_TOXICITY_GUARD=1` can stay in `.env` from the version
with the moderation check on; only the versions between the Guardrails removal
and it (no toxicity check at all) refuse to start with it.

A server that still has the even older hand-applied patch (local changes to
`Dockerfile`, `docker-compose.yml` and `.gitignore` for `gr_token`) drops it first,
or `scripts/deploy.sh` stops on it:

```bash
git diff > ~/guardrails-patch-backup.diff                  # a copy, just in case
git checkout -- Dockerfile docker-compose.yml .gitignore
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
curl -fsS http://localhost:8501/api/health && echo OK   # "toxicity": "active" unless TOXICITY_GUARD=off (step 4)
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
   version keeps running. Then `docker compose build`; the image is tagged
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
patch first (step 4, "A server that still has the even older hand-applied patch").
The running image is tagged after the current commit so the first deploy can go
back to it. The server's checkout is on a detached `HEAD` from then on: deploy with
`scripts/deploy.sh <branch or tag>`, not `git pull`.

**Images from the registry.** A release tag also builds an image on GHCR,
`ghcr.io/cloudastra-tech/knowlegebase:<version>` (`.github/workflows/release.yml`).
It is the same image a server builds: since the Guardrails Hub validator was
removed (step 4) no build needs a token, so nothing secret is in it.
`scripts/deploy.sh --image 0.2.0` runs a registry image (pulled, tagged and rolled
back the same way, the code checked out at its tag) instead of building one on the
server (`scripts/deploy.sh v0.2.0`). If the package is private (the org's Packages
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
one workspace, and workspaces take turns. Slack and Teams answers (someone is
waiting) go before any other job, and `JOB_INTERACTIVE_WORKERS` more threads
(default 1) run only them, so a long re-index never holds an answer up.

Chunks are embedded once: a vector is kept per workspace by the hash of the
chunk's text and the embedding model (`embedding_cache`), so a rebuild, or an
edit that leaves most of a document alone, embeds only text it hasn't seen.
Vectors no index used for `EMBEDDING_CACHE_DAYS` (30) are deleted daily;
`EMBEDDING_CACHE=off` turns it off. Each chunk is also scanned for text written
to an AI ("ignore previous instructions", fake system messages, chat-template
tokens): such documents are flagged on the Knowledge screen's API for HR and
admins (`injection`), never dropped.

Other upkeep jobs: `share_prune` (daily) deletes share links revoked or expired
over `SHARE_KEEP_DAYS` (30) ago. A webhook whose last `WEBHOOK_DISABLE_AFTER`
(50) delivery attempts all failed is turned off and the workspace's admins are
emailed; its secret can be rotated (the old one keeps signing for up to a week)
and any delivery sent again. SCIM answers an address that sent
`RATE_LIMIT_SCIM_FAILURES` (10) wrong tokens in a minute with 429.

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
SharePoint & OneDrive, Zendesk, GitHub, Linear, Dropbox, Box, Amazon S3,
Intercom, Freshdesk, HubSpot, Salesforce Knowledge, Asana, ClickUp, Coda, Teams
channel messages and websites are **connectors** (and each member's own Gmail and
Google Calendar, below)
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
- **Linear.** Create an OAuth application (Linear → Settings → API →
  Applications): callback URL `<PUBLIC_URL>/api/integrations/linear/callback`;
  put its client id and secret in `LINEAR_CLIENT_ID` / `LINEAR_CLIENT_SECRET`.
  The sync asks for the `read` scope only, as the connecting user, so it sees
  what they see (a private team they aren't in never appears). Teams are picked
  on the card; their issues (with comments), projects, project updates and
  documents sync as Markdown. Access tokens last 24 hours; the refresh token rotates on every
  refresh and is saved before it's used. Each item's acl is its team's members.
- **Dropbox.** Create a *Scoped access* app with *Full Dropbox* access in the
  App Console (dropbox.com/developers/apps): scopes `account_info.read`,
  `files.metadata.read`, `files.content.read`, `sharing.read`; redirect URI
  `<PUBLIC_URL>/api/integrations/dropbox/callback`; put the app key and secret
  in `DROPBOX_APP_KEY` / `DROPBOX_APP_SECRET`. Connecting asks for offline access
  (a refresh token). A team member whose team has a team space syncs from it.
  **Production approval:** a new app is in *development* status and can link up
  to 500 users, but **once it links 50 users it has two weeks to be approved for
  production, or linking freezes** (unlinking users doesn't unfreeze it). Apply
  in the App Console (*Apply for production*) before the 50th workspace
  connects. Folders are picked on the card; each file's acl is its shared
  folder's members (or the owner only).
- **Box.** Create a *Custom App* with *User Authentication (OAuth 2.0)* in the
  Box Developer Console: redirect URI `<PUBLIC_URL>/api/integrations/box/callback`,
  application scope *Read and write all files and folders stored in Box* (Box
  documents it as required to download files; the sync only reads), and put the
  client id and secret in `BOX_CLIENT_ID` / `BOX_CLIENT_SECRET`. In an enterprise
  that disables unpublished apps, its admin enables the app first (Admin Console
  → Integrations → Platform Apps Manager). Refresh tokens are single-use and last
  60 days: a connection idle that long needs a reconnect. Server authentication
  (JWT / Client Credentials Grant) isn't offered yet. Folders are picked on the
  card; each sync reads the user's event stream, with a full walk once a day.
- **Amazon S3** (and S3-compatible stores). Nothing is needed on the server for
  **access keys**: a workspace admin enters the bucket, its region, an optional
  prefix and an IAM user's access key (`s3:ListBucket` on the bucket,
  `s3:GetObject` on its objects); an S3-compatible endpoint (MinIO, R2) must be a
  public https address. The preferred way is an **IAM role** in the customer's
  account that this server assumes with a per-workspace **external ID**: give the
  server an IAM user allowed `sts:AssumeRole` (`S3_ROLE_ACCESS_KEY_ID` /
  `S3_ROLE_SECRET_ACCESS_KEY`), its ARN in `S3_ROLE_PRINCIPAL_ARN` (shown in the
  trust policy the form suggests), and a long random `S3_EXTERNAL_ID_SECRET`
  (each workspace's external ID is derived from it and kept with the connection;
  changing it changes the IDs shown for new connections only). Keys and the
  role's hour-long credentials are encrypted with the workspace's key. Every sync
  lists the bucket and fetches only objects whose ETag changed. S3 has no
  per-person sharing, so its connections offer only *HR and admins* or
  *Everyone*, never *As shared at the source*.
- **Intercom.** Help Center articles; closed conversations if turned on. The
  bootstrap needs nothing on the server: a workspace admin creates a **private
  app** in their own Intercom workspace (Developer Hub → New app; permissions
  *Read articles*, plus *Read conversations* for conversations) and pastes its
  access token; private apps need no review. To let admins *sign in* instead, set
  `INTERCOM_CLIENT_ID` / `INTERCOM_CLIENT_SECRET` from a **public app** with the
  same permissions and redirect URL `<PUBLIC_URL>/api/integrations/intercom/callback`;
  **Intercom reviews every public app before other workspaces can install it**
  (listed or unlisted, up to about 7 business days). The form asks for the
  workspace's region (US, EU, Australia): signing in goes to that region's host,
  and every call goes to the region `GET /me` reports. Intercom tokens don't
  expire; a revoked one shows *Access expired*. Intercom's API doesn't expose an
  article's **audience targeting**, so connections offer only *HR and admins* or
  *Everyone*. **Closed conversations** are off by default and lock the connection
  to HR and admins while on (`INTERCOM_CONVERSATIONS_BACKFILL_DAYS`, default 365);
  internal notes and customers' names are left out.
- **Freshdesk.** Knowledge base (Solutions) articles; resolved and closed tickets
  if turned on. Nothing on the server: a workspace admin enters the Freshdesk
  subdomain and an agent's **API key** (Freshdesk documents no OAuth for this).
  Freshdesk's Solutions API has no "changed since" filter, so the knowledge base
  is listed at most every `FRESHDESK_ARTICLES_EVERY` seconds (default 10800: 3
  hours), which also finds deletions; Freshdesk's rate limit is per **account**
  (3,000-5,000 calls an hour on current plans) and shared with the customer's
  other integrations, so the sync spaces its calls to one every two seconds.
  Each article's folder visibility is captured (all users / logged-in users:
  everyone; agents, companies, segments, bots: HR and admins). **Tickets** lock
  the connection to HR and admins (`FRESHDESK_TICKETS_BACKFILL_DAYS`, default
  365); private notes are left out.
- **HubSpot.** Knowledge base articles (needs **Service Hub Professional or
  Enterprise**; HubSpot's knowledge base API is in **beta**, version
  `2027-03-beta`, under HubSpot's developer beta terms). The bootstrap needs
  nothing on the server: a workspace admin creates a **private app** in HubSpot
  (Settings → Integrations → Private apps) with the scope
  `cms.knowledge_base.articles.read` and pastes its token. To let admins sign in
  instead, set `HUBSPOT_CLIENT_ID` / `HUBSPOT_CLIENT_SECRET` from a public app
  with that scope and redirect URL `<PUBLIC_URL>/api/integrations/hubspot/callback`
  (access tokens last 30 minutes and are refreshed). HubSpot documents no token
  revocation: disconnecting deletes the tokens here; uninstall the app in HubSpot
  too. Each article's `accessType` is captured (public: everyone; SSO or access
  groups: HR and admins). Most HubSpot knowledge bases are public: the website
  connector covers those on any plan.
- **Salesforce Knowledge.** Published Lightning Knowledge articles
  (`Knowledge__kav`), every language. **Since Spring '26 Salesforce restricts
  creating new Connected Apps by default** (re-enabling takes a request to
  Salesforce Support), so this uses an **External Client App (ECA)**. A *local*
  ECA only works in the org that created it, so by default each customer creates
  one in their own org and enters its consumer key and secret on the card (stored
  encrypted with the workspace's key):
  1. Setup → External Client App Manager → *New External Client App*; distribution
     state *Local*.
  2. *Enable OAuth*: callback URL `<PUBLIC_URL>/api/integrations/salesforce/callback`;
     scopes *Manage user data via APIs (api)* and *Perform requests at any time
     (refresh_token, offline_access)*; keep *Require Proof Key for Code Exchange
     (PKCE)* on; turn on *refresh token rotation*.
  3. Policies: let the integration user (a user with *Read* on Knowledge and the
     data categories to sync) use the app; refresh token valid until revoked.
  4. Copy the consumer key and secret into Integrations → Salesforce Knowledge,
     with the org's **My Domain** address (`https://acme.my.salesforce.com`; a
     sandbox is `acme--dev.sandbox.my.salesforce.com`) → *Sign in with
     Salesforce*.
  A server operator who ships a *packaged* ECA (a second-generation managed
  package installed in each customer's org) sets `SALESFORCE_CLIENT_ID` /
  `SALESFORCE_CLIENT_SECRET`; the form then only asks for the address. Article
  record types are picked on the card (none: every article). Each sync uses the
  org's daily API allowance (`REQUEST_LIMIT_EXCEEDED` shows as *rate limited*).
  Only articles in the **public knowledge base** with no data categories count
  as everyone's under *As shared in Salesforce*; every other article is HR and
  admins (Salesforce's profiles and data category visibility don't map onto
  members here). Disconnecting revokes the refresh token.
- **Asana.** Create an app in the Asana developer console (My apps → *Create new
  app*): redirect URL `<PUBLIC_URL>/api/integrations/asana/callback`, **Full
  permissions** (Asana's granular scopes don't cover project briefs, memberships
  or the events API yet; nothing is ever written). Put its client id and secret
  in `ASANA_CLIENT_ID` / `ASANA_CLIENT_SECRET`. Workspaces or single projects
  are picked on the card; tasks (with their comments) and project briefs sync.
  Access tokens last an hour and are refreshed; changes come from
  `modified_since`, deletions from the events API (its sync tokens expire within
  a day: then that project is listed again), everything once a day. Project
  members (read by email) are captured; a whole team added to a project isn't
  expanded. 150 requests a minute per token on free Asana domains.
- **ClickUp.** Create an OAuth app (ClickUp Settings → *Apps* → *Create new
  app*): redirect URL `<PUBLIC_URL>/api/integrations/clickup/callback`; put its
  client id and secret in `CLICKUP_CLIENT_ID` / `CLICKUP_CLIENT_SECRET`. The
  person connecting picks which ClickUp Workspaces to authorize; Spaces,
  Folders or Lists are then picked on the card. Tasks (open and closed, with
  comments) and Docs (as Markdown) sync; tasks by `date_updated`, Docs are
  listed every run (ClickUp has no Doc change feed). Each List's explicit
  members are captured; Docs on a Folder or a public Space have no readable
  audience and are for no one under *As shared in ClickUp*. 100 requests a
  minute on most plans. Tokens don't expire; disconnecting deletes ours (remove
  the app in ClickUp to revoke it there).
- **Coda** (now "Superhuman Docs") needs nothing on the server: a workspace admin
  pastes an API token (Account settings → API settings; read-only is enough) and
  picks docs (only docs that account has opened are listed). Pages are exported
  as Markdown; a doc whose `updatedAt` didn't move isn't read again. A doc's
  sharing is captured where Coda shows it to the token (people, domains, anyone
  with the link); *As shared in Coda* is only offered once every picked doc's
  sharing could be read.
- **Teams messages** (channel conversations, a *source*: separate from the Teams
  bot above). A multitenant Entra app registration, Web redirect URI
  `<PUBLIC_URL>/api/integrations/teams_messages/callback`, Microsoft Graph
  **application** permissions `Team.ReadBasic.All`, `Channel.ReadBasic.All`,
  `ChannelMessage.Read.All`, `TeamMember.Read.All`, `ChannelMember.Read.All`
  (and delegated `openid profile email`), in `TEAMS_MESSAGES_CLIENT_ID` /
  `TEAMS_MESSAGES_CLIENT_SECRET`. **Microsoft treats channel messages as a
  protected API**: the app must also be approved by Microsoft (the protected APIs
  request form linked from Graph's 403) before any tenant can sync; until then a
  sync stops with *Microsoft hasn't approved this app for Teams' protected APIs*
  and waits. A tenant admin connects (signs in, then approves the app for the
  organisation), picks teams or channels; a connection is **locked to HR and
  admins** until an admin turns on *Let members ask* in the picker, after which
  *As shared in Teams* follows channel membership (a standard channel: the team's
  members and its Microsoft 365 group; a private or shared channel: its own
  members). Threads (root + replies) of the last eight months sync through the
  channel delta query; chats are never read.
- **Gmail and Google Calendar** are **each member's own** (*Settings › Connections*, from the
  account menu), never a workspace connection: what a member syncs is searchable
  by them alone (`user:<them>` on every chunk), never listed, counted, digested or
  sent to webhooks for anyone else, admins included; removing or erasing the
  member, or deleting the workspace, revokes the token and deletes the copies.
  They use the Google client above; add the redirect URIs
  `<PUBLIC_URL>/api/integrations/gmail/callback` and
  `<PUBLIC_URL>/api/integrations/google_calendar/callback`, enable the Gmail and
  Calendar APIs, and turn them on with `GMAIL_SYNC=on` / `CALENDAR_SYNC=on`.
  **Google's review comes first:** `gmail.readonly` is a *restricted* scope — an
  app that keeps Gmail data on a server needs restricted-scope verification and
  an **annual CASA security assessment** by a Google-approved assessor before
  users outside the Google Cloud project's organisation can grant it (until then
  it's limited to 100 test users behind the "unverified app" screen);
  `calendar.readonly` is *sensitive* (verification, no assessment). Members pick
  Gmail labels (threads of the last 180 days) and calendars (events ending within
  the last year or later; recurring series once).
- These capture source permissions (Confluence space permissions and page
  restrictions; Jira project browse grants and security levels; SharePoint item
  permissions; Zendesk user segments; GitHub visibility and collaborators; Linear
  team members; Dropbox shared-folder members; Box collaborations; Freshdesk
  folder visibility; HubSpot article access; Salesforce channels and data
  categories; Intercom's article state, without its audience) into
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

### Checking Linear, Dropbox, Box and S3 by hand (no accounts here: not run live)

These four were built against Linear's, Dropbox's, Box's and AWS's documentation
and tested with recorded answers (`tests/fixtures/connectors/`); no real account
was available. Before telling a customer they work, run each list once with a
test account, the server's OAuth app set up as above and `PUBLIC_URL` reachable
by the provider (a tunnel on a laptop). Watch the API log for `Sync:` lines.

**Linear**
1. Integrations → Linear → *Connect Linear*: Linear's consent screen asks for
   **read** access, as you; pick the Linear workspace, *Authorize*. Back on
   Integrations, the team picker opens. Activity shows `integration.connect`.
2. Tick one public team and one private team you belong to → *Save*. The card
   shows *Syncing*, then a count of items. A private team you're **not** in is
   not in the picker.
3. Ask about an issue's description and one of its comments: the answer cites
   `ENG-123: …` linking to the issue. Ask about a project's content, its latest
   project update (cited as `<project>: update of <date>`, with its health) and
   a document of one of its projects.
4. Edit an issue's description in Linear, *Sync now*: the history shows an
   incremental run with 1 upserted; the answer has the new text. Archive the
   issue and sync: 1 deleted.
5. Set the policy to *As shared in Linear*: a member who is in the team sees its
   issues in answers; one who isn't, doesn't (Knowledge page shows who can see
   each file).
6. Wait a day (or set the token's `renew_at` back): the next sync refreshes the
   token without a reconnect. In Linear, Settings → Applications → revoke the
   app: the next sync shows *Access expired*, *Reconnect* works.
7. *Disconnect*: the app disappears from Linear's authorised applications.

**Dropbox** (with the app in development status: link only test accounts)
1. *Connect Dropbox*: Dropbox's screen lists the four read scopes; *Allow*. With a
   Dropbox Business account that has a team space, the picker shows the team
   folders.
2. Pick a folder with a Word file, a PDF, a Markdown file, a PNG and a 25 MB
   PDF. After the sync: three files, one skipped (*too large*); the PNG isn't
   listed.
3. Share the folder with a colleague and a group; sync with *Sync permissions
   now* under *As shared in Dropbox*: the colleague (if a member here) can ask
   about it; someone else can't.
4. Edit the Markdown file, move the PDF to a sub-folder, delete the Word file;
   *Sync now*: 1 upserted, 1 deleted, the PDF still there (a move isn't a delete).
5. Delete everything in the folder in Dropbox; sync: the card asks to confirm
   removing them (the mass-delete guard).
6. *Disconnect*: the app leaves Dropbox's Settings → Connected apps.
7. Production approval: in the App Console, check *Development users* and apply
   for production before 50 users are linked.

**Box**
1. *Connect Box*: Box's consent screen; *Grant access to Box*. (In an
   enterprise with unpublished apps disabled, an admin enables the app in Admin
   Console → Integrations → Platform Apps Manager first; until then Box refuses.)
2. Pick a folder with a sub-folder and supported files. After the sync, check a
   file deep in the sub-folder is there and downloads fine (the download needs
   the read-and-write scope the app was configured with).
3. Add a collaborator to the sub-folder and to one file; *Sync permissions now*
   under *As shared in Box*: both are in the file's acl (Knowledge page). Make a
   *People with the link* shared link: the file is for everyone. Note whether a
   sub-folder's collaborations list includes the parent's collaborators (Box's
   docs don't say; the connector reads every ancestor either way).
4. Upload a new version of a file, trash another; *Sync now*: an incremental run
   (no folder walk in the history counts) with 1 upserted, 1 deleted.
5. Rename the sub-folder; *Sync now*: the run is a full one (a folder event).
6. *Disconnect*: Box → Account Settings → Apps no longer lists it.

**Amazon S3**
1. Access keys: make a bucket with a prefix `handbook/` holding a PDF, a
   Markdown file and an image; an IAM user with the form's read policy;
   Integrations → Amazon S3 → *Connect*, *Access keys*, bucket, region, prefix,
   key → *Connect*. The card syncs the two readable files; the image is skipped
   silently. A wrong region or bucket says so in the form.
2. Role: set `S3_ROLE_*` and `S3_EXTERNAL_ID_SECRET`, reopen the form: the *IAM
   role* tab shows the external ID and the trust policy. Create the role with
   them and the read policy, paste its ARN → *Connect*. Change the trust
   policy's external ID: the next sync shows *Access expired*.
3. Edit the Markdown object, delete the PDF; *Sync now*: 1 upserted (only it was
   fetched again), 1 deleted.
4. Pick a sub-folder on the card: only it is synced; objects outside it are
   removed (that's the admin's choice, so no confirmation).
5. The card's *Who can ask* offers HR and admins / Everyone only, with the note
   that S3 has no per-person sharing.
6. An S3-compatible store (MinIO on a public https address): *Access keys* with
   the endpoint; a private address is refused.
7. *Disconnect*: the credentials are gone from the database (and the role
   remains in the customer's account until they delete it).

### Checking Intercom, Freshdesk, HubSpot and Salesforce by hand (no accounts here: not run live)

Built against each vendor's documentation and tested with recorded answers
(`tests/fixtures/connectors/`); no real account was available. Run each list once
with a test account before telling a customer they work; for the sign-in paths,
`PUBLIC_URL` must be reachable by the vendor (a tunnel on a laptop).

**Intercom**
1. In a test workspace, Developer Hub → *New app* (private), permissions *Read
   articles* and *Read conversations*; copy the access token.
2. Integrations → Intercom → *Connect*: pick the workspace's region, paste the
   token → *Connect*. The card shows the app's name and syncs. An EU or AU
   workspace: the API log shows calls to `api.eu.intercom.io` / `api.au.intercom.io`.
3. Ask about a published article: the answer cites it with its Help Center link.
   A draft article is not there. Pick one collection on the card: only its
   articles (and its sub-collections') remain.
4. Edit an article, *Sync now*: an incremental run with 1 upserted. Unpublish it
   and sync: 1 deleted. Delete one: removed at the next daily full listing (or
   *Full re-sync*).
5. The *Who can ask* control offers *HR and admins* / *Everyone* only, with the
   note about audience targeting.
6. Turn on *Also sync closed conversations*: the card says it's locked to HR and
   admins; close a test conversation with a note and a reply, sync: the reply is
   there, the note and the customer's name aren't. Turn it off: the
   conversations go.
7. With `INTERCOM_CLIENT_ID` / `_SECRET` (a public app, before review only your own
   workspace can install it): *Sign in with Intercom* on the EU region lands on
   `app.eu.intercom.com` and comes back connected.
8. Revoke the token (or uninstall the app): the next sync shows *Access expired*.

**Freshdesk**
1. In a trial account, Profile settings → *View API key*. Integrations →
   Freshdesk → *Connect*: subdomain and key. A wrong key says so.
2. Make a folder visible to *All users*, one to *Agents*, one with a sub-folder
   (Flexible Hierarchy), and a draft article. After the sync every published
   article is there, the draft isn't; customer-visible articles link to
   `/support/solutions/articles/…`, agent-only ones to `/a/solutions/articles/…`
   (check both links open).
3. *As shared in Freshdesk*: a member sees the all-users article; only HR and
   admins see the agents-only one.
4. Edit an article: it updates at the next listing (`FRESHDESK_ARTICLES_EVERY`,
   or *Full re-sync* at once). Delete one: removed then.
5. Turn on *Also sync resolved tickets*; resolve a ticket with a public reply and
   a private note; sync: the reply is there, the note isn't; the card is locked
   to HR and admins. Reopen it, sync: it goes. Delete a resolved ticket: it goes
   within a day (the deleted-tickets check).
6. Watch the account's API usage (Admin → API) during a full listing: it should
   stay well under the plan's hourly limit.

**HubSpot** (a Service Hub Professional or Enterprise test account)
1. Settings → Integrations → Private apps → *Create*; scope
   `cms.knowledge_base.articles.read`; copy the token. Integrations → HubSpot →
   *Connect* → paste → *Connect*. The account shows the knowledge base's domain.
   Without the scope, the form says which scope to add. **Note whether private
   apps can call the beta knowledge base API at all**; if HubSpot refuses, only
   the OAuth path below works.
2. After the sync: published articles are there; drafts and articles archived in
   the dashboard aren't. Pick a category: only it and its subcategories remain.
3. Edit an article, *Sync now*: an incremental run asks with `updatedAfter`;
   1 upserted. Unpublish one: 1 deleted.
4. Set an article to *SSO login* or an access group, sync; *As shared in HubSpot*:
   only HR and admins can ask about it; public articles stay everyone's.
5. With `HUBSPOT_CLIENT_ID` / `_SECRET`: *Sign in with HubSpot*, pick the
   account; wait 30 minutes (or set `renew_at` back) and sync: the token is
   refreshed without a reconnect.
6. *Disconnect*, then uninstall the app (or delete the private app) in HubSpot.

**Salesforce Knowledge** (a Developer Edition org with Lightning Knowledge on)
1. Confirm Setup → *Connected Apps* creation is restricted (Spring '26) and create
   the External Client App as above (local, PKCE, refresh token rotation).
2. Integrations → Salesforce Knowledge → *Connect*: My Domain address, consumer key
   and secret → *Sign in with Salesforce*; approve. The card shows the My Domain
   host and syncs.
3. Publish an article with a rich text body in two languages: two items, each
   citing its Lightning record. A draft isn't there; a plain-text custom field
   isn't in the text.
4. Publish a new version of the article: *Sync now* is incremental, the text
   updates, nothing is deleted. Archive the other article: 1 deleted.
5. Tick the article as *Visible in Public Knowledge Base* with no data categories:
   under *As shared in Salesforce* everyone can ask about it; add a data category
   and sync: HR and admins only.
6. Pick one record type on the card: only its articles remain.
7. Revoke the app's token for the user (Setup → Connected Apps OAuth Usage): the
   next sync shows *Access expired*; *Reconnect* works. With rotation on, check
   the stored refresh token changes after a refresh (an expired session).
8. *Disconnect*: the token is gone from the org's OAuth usage.

### Checking Asana, ClickUp, Coda, Teams messages, Gmail and Calendar by hand (no accounts here: not run live)

Built against each vendor's documentation and tested with recorded answers
(`tests/fixtures/connectors/`, written by `make_work_tools.py`); no real account
was available. `PUBLIC_URL` must be reachable by the vendor for the sign-ins.

**Asana**
1. Connect; the card shows the account's email. Pick one workspace: every
   unarchived project's tasks and its brief sync; comments appear oldest first,
   activity ("assigned to") doesn't.
2. Edit a task, *Sync now*: incremental (asks with `modified_since`), 1 upserted.
   Delete a task: 1 deleted (events). Wait a day (or clear the cursor): the
   events token is renewed after a 412 and a full listing runs.
3. *As shared in Asana*: a project member who is a member here can ask about its
   tasks; someone outside the project can't.
4. Wait an hour, sync: the token is refreshed. Revoke the app (My settings → Apps):
   *Access expired*. *Disconnect* revokes the refresh token.

**ClickUp**
1. Connect, authorize one Workspace. Open it in the picker: Spaces → Folders →
   Lists. Pick a Folder: its Lists' tasks and the Docs on its Lists sync.
2. Edit a task and add a comment, sync: incremental, the comment is there. Delete
   a Doc: it's removed at the next run.
3. *As shared in ClickUp*: members of the List can ask; a Doc on the Folder itself
   is for no one (ClickUp doesn't say who can read it).
4. Watch for 429s on a large Workspace (100 a minute): the sync waits and goes on.

**Coda**
1. Paste an API token; a bad token says so. Pick a doc with several pages and an
   embed page: the pages sync as Markdown (headings, lists, tables), the embed is
   listed as skipped.
2. Edit a page, sync: only that doc's pages are listed again. Don't edit, sync:
   nothing but the doc's metadata is read.
3. With a token of the doc's owner, *As shared in Coda* is offered and a person
   the doc is shared with can ask. With a token of someone who can't share it,
   the sharing can't be read: the card offers only HR / everyone and says why.

**Teams messages** (after Microsoft has approved the app's protected-API request)
1. Before approval: connect, pick a channel, sync: the card says Microsoft hasn't
   approved the app for protected APIs and nothing is retried.
2. After approval: a tenant admin connects (sign-in, then the consent screen) and
   picks a team: its channels' threads sync with replies; system messages
   ("X joined") aren't there. The card says *Members can ask: no*.
3. Ask as a member: nothing from Teams. Turn on *Let members ask*, choose *As
   shared in Teams*: a team member can ask about a standard channel; only a
   private channel's members about it.
4. Edit a message and delete another, sync: incremental (the delta link), 1
   upserted, 1 deleted. Remove the app's consent in Entra: *Access expired*.

**Gmail and Google Calendar** (a Google Cloud project in Testing with the test user
added, or after verification and CASA)
1. With `GMAIL_SYNC=on`: account menu → *Settings* → *Connections* → *Connect your Gmail*;
   untick the Gmail permission on Google's screen: it says the permission is
   missing. Connect again with it: choose labels; threads with them sync.
2. Ask about an email as that member: it's cited. Sign in as an admin viewing as
   HR: the same question finds nothing; Knowledge, Integrations, Insights and the
   audit log show no mailbox, no label names, no subjects.
3. Remove the label from a thread, sync: it's deleted. Leave the connection for
   over a week: the history id is too old and a full listing runs.
4. Calendar: connect, pick the primary calendar: upcoming events and recurring
   series sync with attendees; cancel one: it goes at the next sync.
5. Remove the member (Access → Members): both connections are revoked at Google
   (myaccount.google.com → Security → third-party access no longer lists the app)
   and their copies are gone.

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

## Microsoft Teams bot

People can ask in Teams: a 1:1 chat with the app (answered with their own role),
or `@Knowledge` in a channel or group chat (answered in the thread from **public**
documents only, with a note saying so). Answers are Adaptive Cards with numbered
source links and 👍 / 👎 that land in the same feedback as the web chat's. When a
search as an HR / Admin asker finds restricted passages for a channel question,
the full answer comes to them in their 1:1 chat with the app (a proactive message;
Teams allows it only when the app is installed for them personally, otherwise
nothing more is sent). Nothing in the channel says restricted sources exist.

**Who gets answered.** The activity's Microsoft Entra tenant must be the one the
workspace connected. The sender's email or sign-in name (UPN), from the Bot
Connector's member lookup, is the member they are, with their role as it is now.
People with no account are refused (default) or answered from public documents
only (the card's setting); turned-off members and guests from other tenants are
never answered.

**Requests** carry the Bot Connector's JWT, checked before anything else
([connector authentication](https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-authentication)):
signed by a key from `https://login.botframework.com/v1/.well-known/keys` endorsed
for `msteams` (else 403), issuer `https://api.botframework.com`, audience the bot's
App ID, unexpired with 5 minutes' skew, and its `serviceUrl` claim equal to the
activity's. Keys are cached a day. Each answer is a job (`teams`); questions are
in the question log (surface `teams`) and the audit log (`teams.ask`, its length,
never its text).

**Which bot.** Azure stopped creating multi-tenant bots after 31 July 2025
([bot registration](https://learn.microsoft.com/en-us/azure/bot-service/bot-service-quickstart-registration)),
so each customer normally registers their **own single-tenant Azure Bot** and pastes
it on the card. A host that already has a multi-tenant bot can offer it to every
workspace with `TEAMS_APP_ID` / `TEAMS_APP_PASSWORD` (endpoint
`PUBLIC_URL/api/teams/messages`); the admin then only names their tenant ID.

### Checking Teams by hand (no Microsoft tenant here: not run live)

Needs a Microsoft 365 tenant where you may register apps and upload custom Teams
apps, an Azure subscription, and this server reachable over https.

1. **Register the bot.** Azure portal → *Create a resource* → **Azure Bot**. Type of
   App: **Single Tenant**; creation type: *Create new Microsoft App ID*. Once made,
   *Configuration* shows the **Microsoft App ID** and **App Tenant ID**; *Manage
   Password* → *New client secret* → copy its **Value**.
2. **Connect it here.** As an admin, Integrations → Microsoft Teams → *Connect your
   bot* → paste the App ID, App Tenant ID and secret value → *Save*. The card says
   **Waiting for Teams**; Activity shows `teams.app` with the App ID and tenant, no
   secret. Open *Messaging endpoint for Azure* and copy it
   (`https://<host>/api/teams/apps/<key>/messages`).
3. **Point the bot at us.** Azure Bot → *Configuration* → *Messaging endpoint*: paste
   it → *Apply*. *Channels* → add **Microsoft Teams** (accept the terms) → *Apply*.
4. **Upload the Teams app.** On the card, *Download Teams app* (a zip:
   `manifest.json` with the bot's App ID and this host, and the two icons). Teams
   admin center → *Teams apps* → *Manage apps* → *Upload new app* → the zip (or, if
   custom-app upload is allowed for you, Teams → *Apps* → *Manage your apps* →
   *Upload an app*). Allow it in the org's app permission policy if needed.
5. **1:1 chat.** In Teams, open the app → *Chat* → ask something from your documents.
   "Looking that up…" appears, then becomes the card: the answer with `[1]` links, a
   Sources list, 👍 / 👎 and *Open in the app*. The card here now says **Connected**.
   As an HR / Admin member, ask about a restricted document: answered from it.
6. **A channel.** Add the app to a team, then in a channel `@Knowledge <question>` as
   a **public** member: answered in the thread from public documents, with the
   "Answered from documents everyone here may see" note; no 1:1 message.
7. **The private answer.** As an **HR / Admin** member who has the app installed
   personally (step 5), `@Knowledge` a restricted question in the channel: the
   thread gets the public-only card; your 1:1 chat with the app gets "You asked in a
   channel: …" and the full answer. Remove the app from your personal apps and ask
   again: only the channel answer (the server log says Teams refused the 1:1 message).
8. **Feedback.** Click 👎 on a channel answer as another member: Teams shows "Thanks
   for telling us…". Insights → Feedback lists it; Activity shows `feedback.set`
   with surface `teams`.
9. **Who's refused.** A user with no account here gets a polite refusal. Switch the
   card to *Answer from public documents only*: answered, public-only. Turn a member
   off in Members: refused.
10. **A forged request.** `curl -X POST https://<host>/api/teams/apps/<key>/messages
    -H 'Content-Type: application/json' -d '{}'` → 401. Insights → *Where questions
    come from* lists Microsoft Teams.
11. **Disconnect.** *Disconnect* on the card: the secret and tenant are forgotten and
    Teams messages get 401. Remove the app in the Teams admin center too.

## Email-in

Members email `ask@<workspace>.<EMAIL_IN_DOMAIN>` and get a cited answer as a reply
in the same thread; a reply to it continues the same chat. Off per workspace until
an admin turns it on (Integrations → Email). Postmark's inbound webhook is the
provider (`/api/email-in/postmark`); the code has a provider interface for SES/SNS
later. Only an **enabled member of that workspace** whose email passed Postmark's
SPF check (`Received-SPF: pass`) **and** carries a DKIM signature aligned with the
From domain (`DKIM_VALID_AU` in Postmark's `X-Spam-Tests`) is answered; Postmark
documents no DMARC verdict
([parse an email](https://postmarkapp.com/developer/user-guide/inbound/parse-an-email)).
Everything else is dropped **without a reply** (no backscatter to spoofed
senders): automatic mail (`Auto-Submitted` other than `no`, RFC 3834),
`Precedence: bulk/list/junk`, any `List-*` header, `X-Auto-Response-Suppress`, a
null Return-Path, our own addresses, mailer-daemon-like senders, a Message-ID
already taken in, and senders over `RATE_LIMIT_EMAIL_USER` a minute. Replies carry
`Auto-Submitted: auto-replied`, In-Reply-To and References. Questions are logged
with surface `email`.

### Checking email-in by hand (no mail provider here: not run live)

1. **DNS.** Pick a mail domain, e.g. `kb.example.com`. Add MX `*.kb.example.com` →
   `inbound.postmarkapp.com`, priority 10 (and MX `kb.example.com` → the same, for
   the `ask+<workspace>@kb.example.com` form)
   ([inbound domain forwarding](https://postmarkapp.com/developer/user-guide/inbound/inbound-domain-forwarding)).
2. **Postmark.** In the server's *Default Inbound Stream* → *Settings*: *Inbound
   domain* `*.kb.example.com`; *Webhook URL*
   `https://<USER>:<PASSWORD>@<host>/api/email-in/postmark` (Postmark signs nothing,
   so the Basic credentials are the check:
   [webhooks](https://postmarkapp.com/developer/webhooks/webhooks-overview)).
   Optionally allow only Postmark's webhook IPs at the firewall
   ([IPs](https://postmarkapp.com/support/article/800-ips-for-firewalls#webhooks)).
3. **This server.** Set `EMAIL_IN_DOMAIN`, `EMAIL_IN_WEBHOOK_USER` and
   `EMAIL_IN_WEBHOOK_PASSWORD` (long random values, the same as in the URL), a real
   `MAIL_BACKEND` (e.g. `postmark` with `POSTMARK_TOKEN` and a verified `MAIL_FROM`),
   optionally `EMAIL_IN_FROM`; restart.
4. **Turn it on.** As an admin, Integrations → Email → *Answer emails*; copy the
   address.
5. **Test.**
   - From a member's real mailbox (a domain with SPF and DKIM), email the address:
     "Re: <subject>" with numbered sources arrives within a minute.
   - Reply with a follow-up: it continues the same chat (check the web app). If
     Postmark replaces the Message-ID we set, follow-ups are still answered, in a
     new chat: note it.
   - From a non-member, or with email-in off: no reply; `email_in_messages` and the
     log line "Email-in: dropped …" say why.
   - Postmark's *Retry* on a message: no second answer.
   - Break the webhook password: Postmark's activity shows 403 and stops retrying.

## Browser extension

`extension/` (its README has the details): a Chrome / Edge side panel. Build it
with `cd frontend && npm run build:extension` (→ `extension/dist`) and load it
unpacked, or publish that folder to the Chrome Web Store / Edge Add-ons. Then set
`EXTENSION_ORIGINS=chrome-extension://<its id>` (comma-separate several: a store
build and a dev build have different ids) and restart. Until it's set the
extension can't be connected and `/api/v1` sends no CORS headers. People connect
it from the extension (Connect → confirm in the app); each connection is a
personal API key with the `ask` scope for 90 days, listed under API keys and
Integrations → Browser extension, and revoked by Disconnect. Questions from it are
logged with surface `extension`.

## Installing as an app (PWA)

The site is installable (Chrome / Edge: the install icon in the address bar;
Android: *Add to Home screen*; iOS Safari: *Share → Add to Home Screen*). It needs
https (or localhost). `manifest.webmanifest` and `sw.js` are served from the site
root with `Cache-Control: no-cache`, so a new service worker is picked up on the
next visit. The service worker caches the app shell only and never touches
`/api/`, `/mcp` or `/scim/`.

## Digests and outbound webhooks

**Digests** go by the mailer (`MAIL_BACKEND`, above) or as a Slack DM from the
workspace's Slack app (the member is found by email: `users.lookupByEmail`, scope
`users:read.email`, already in the manifest). Links in them use `PUBLIC_URL`. The
summaries use `FAST_MODEL`, once per version of a document.

**Webhooks** are added by a workspace's admins (Access → Webhooks). Addresses must
be `https://` and resolve only to public IPs. To try one against a receiver on
this machine, list it like a connector's test site:
`CONNECTOR_ALLOW_PRIVATE_HOSTS=127.0.0.1:9000` (plain http is then allowed for it).
A receiver checks each request:

```python
import hashlib, hmac, time
def valid(secret, timestamp, body: bytes, signature):          # X-KB-Timestamp, X-KB-Signature
    if abs(time.time() - int(timestamp)) > 300:
        return False
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return hmac.compare_digest("v1=" + mac.hexdigest(), signature)
```

It should answer 2xx quickly; anything else is retried for 24 hours, and
`X-KB-Delivery` repeats on a retry, so receivers can drop duplicates.

## Health checks and error tracking

- `GET /api/health` — the process is up. Always 200 while it runs; the Docker
  `HEALTHCHECK` uses it. Don't point anything that restarts the container at a
  deeper check: a restart fixes none of what it finds. Its `guards` field says
  whether the toxicity check is running: `"toxicity": "active"`, or `"off"` with
  `TOXICITY_GUARD=off` (step 4).
- `GET /api/health/ready` — the server can actually serve: `config/app.db` opens and
  answers a query, the Chroma index opens, and the encryption key loads **and
  decrypts a stored secret** (a changed `INTEGRATIONS_KEY` otherwise breaks every SSO
  provider and integration silently). With `REQUIRE_TOXICITY_GUARD=1` it also
  checks the toxicity check is on (`toxicity_guard`); without it, one turned off is
  only reported under `guards`. 503 with the failing check otherwise:

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

**The encryption key is never in the archive.** `config/integrations.key` (the KEK,
see "The encryption key" below) opens every workspace's data keys, and through them
its OAuth tokens and SSO secrets in `app.db`; an archive holding both
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

## Moving to Postgres

SQLite (`config/app.db`) is the default and stays right for one server. Postgres
is for several app processes or servers, managed backups with point-in-time
recovery, and row-level security (ARCHITECTURE.md, "Postgres, row-level security
and pgvector"). The app switches when `DATABASE_URL` is set.

**The database.** Postgres 14 or newer, built with ICU (every packaged and managed
one is). For `VECTOR_STORE=pgvector`, the `vector` extension 0.8 or newer
(`pgvector/pgvector:pg16` in Docker; on a managed service, enable it). Make one
database and one login role that owns it; the app makes its tables on first start:

```sql
CREATE ROLE kb LOGIN PASSWORD '...' CREATEROLE;   -- CREATEROLE: for row-level security
CREATE DATABASE kb OWNER kb;
```

```bash
# .env
DATABASE_URL=postgresql://kb:...@db.internal:5432/kb?sslmode=require
VECTOR_STORE=pgvector        # optional: the search index in Postgres too
# DB_POOL_MAX=20             # connections per app process
# DB_RLS=off                 # only to rule row-level security out while debugging
```

`kb` owns the tables, so it is the **platform role** row-level security doesn't
limit (sign-in, jobs of no workspace, the operator, migrations). Each workspace's
requests and jobs run as `kb_tenant` (`DB_TENANT_ROLE`), which the first start
makes (it needs `CREATEROLE`; without it the app logs "Row-level security not set
up" and runs everything as `kb`, as on SQLite). If the tables end up owned by
another role (restored by a DBA, say), give `kb` `BYPASSRLS` instead. Backups: the
provider's PITR, or `pg_dump -Fc` nightly; `scripts/backup.sh` covers SQLite only
(it still archives `config/` with the key, `docs/` and `data/`).

**Runbook** (minutes for today's data; plan a short maintenance window):

1. **Freeze writes.** Announce it, then stop the app so nothing writes to
   `config/app.db` while it's copied: `docker compose stop knowledgebase`.
2. **Back up** as usual: `BACKUP_ENV_FILE=/root/kb-backup.env scripts/backup.sh`.
3. **Migrate.** With the new image (so `app.db` is at the newest migration: start
   it once on SQLite first if it isn't; the tool refuses otherwise):

   ```bash
   docker compose run --rm -e DATABASE_URL='postgresql://kb:...@db.internal:5432/kb' \
       knowledgebase python -m tools.migrate_sqlite_to_pg \
       --sqlite config/app.db --chroma-dir chroma_db      # --chroma-dir only for pgvector
   ```

   It makes the schema in Postgres, copies every table (FTS rows, message order,
   binary keys and all) in one transaction, moves every id sequence past the
   highest id SQLite gave out, and copies the Chroma index's vectors into pgvector
   without re-embedding. Run it again at any time: a table already copied is
   checked and left alone; one with different rows is refused (nothing changes)
   unless `--overwrite`.
4. **Verify.** The tool prints, per table, the row counts on both sides and
   whether a SHA-256 over every row matches, and exits 1 if any doesn't.
   `--verify-only` repeats the check without changing anything.
5. **Switch.** Put `DATABASE_URL` (and `VECTOR_STORE=pgvector`) in `.env`, then
   `docker compose up -d` and `curl -fsS http://localhost:8501/api/health/ready`;
   sign in and ask a question. Keep `config/app.db` untouched for a while.

**Going back** (before anything new is written to Postgres): stop the app, remove
`DATABASE_URL` / `VECTOR_STORE` from `.env`, start it: it uses `config/app.db` and
`chroma_db/` again, as they were at step 1. After writes, going back means copying
them out of Postgres by hand; there's no reverse tool.

## The encryption key (KEK) and each workspace's keys

Secrets in `app.db` (integration tokens, SSO client secrets and SAML keys, the
Slack app's secrets and bot token) are sealed with **envelope encryption**
(`secret_box.py`):

- **The server key (KEK)**: `INTEGRATIONS_KEY` (a Fernet key), or on a self-hosted
  server `config/integrations.key`, made on first use. It only wraps the keys below.
- **A data key per workspace** (`workspace_keys`), random, stored wrapped by the KEK.
  Each secret is sealed under its own workspace's newest data key. Deleting a
  workspace deletes its data keys: its secrets can never be opened again, from any
  backup either (crypto-shredding).

After the upgrade that brings this, the `secrets_reseal` job (queued at start)
moves every existing secret under its workspace's data key; each one keeps working
throughout, and the job can be stopped and run again. Data keys are rotated
yearly by a job (`KEY_ROTATION_DAYS`, default 365; 0 turns it off); the old
version is retired but still opens anything sealed under it.

**Hosted mode** (`APP_ENV=production` and `HOSTED=1`, the multi-tenant service):
the KEK must come from `INTEGRATIONS_KEY` (or a KMS, `KEK_PROVIDER=kms` with a
client plugged into `secret_box.use_kek()`): no key file is made or read next to
the database, and the server refuses to start without it.

**Back the KEK up separately from the data.** Losing it loses every workspace's
secrets (each provider and integration has to be set up again); keeping it with
the database lets anyone with a copy of the backup read them. Keep it in the team
password manager and in the secrets store the server reads it from, never in the
backup archive (`scripts/backup.sh` keeps `config/integrations.key` apart, see
above). To replace it:

```bash
# 1. both keys, the new one first: new secrets use it, both open
INTEGRATIONS_KEY=<new>,<old>      # restart the app
# 2. wrap every data key under the new one (and re-seal any old-format secret)
venv/bin/python -c "from kb.services import keys; keys.schedule_rewrap()"
# 3. once the secrets_rewrap job is done (the jobs table): drop the old key, restart
INTEGRATIONS_KEY=<new>
```

## Workspace lifecycle, data requests and retention

Admins have these under **Access > Data**:

- **Export**: a ZIP of members (CSV), the audit log (JSON lines, exactly as hashed),
  uploaded documents, chats still inside the chat retention, verified answers, and
  the settings, never secrets (tokens, keys, client secrets, hashes). It's made by a
  job under `EXPORT_DIR` (default `data/exports/<workspace>/`) and kept 24 hours.
- **Delete the workspace**: typing its name schedules the purge 7 days later
  (`WORKSPACE_DELETE_GRACE_DAYS`). Members are signed out at once and can't sign
  in; admins can, only to export or cancel; the admins are emailed both ways. The
  purge job then revokes each connector's tokens at the provider, uninstalls the
  Slack app, revokes API keys and SCIM tokens, drops the search index collections,
  deletes the documents and exports, destroys the workspace's data keys and deletes
  every row of it. The operators' audit chain (`_platform`) keeps that it happened
  (when, who asked, row counts), nothing else.
- **Retention**: chats (30 / 90 days, a year, or until deleted), the question log
  (its own settings, also on Insights) and the audit log: kept for good by default,
  or 1, 2, 3 or 7 years. Old audit events are deleted from the start of the chain,
  whose last deleted hash is kept (`audit_anchors`), so it still verifies.
- **Cloudastra support**: the admin can let support in for 24 hours, 3 or 7 days.

**Data requests (DSAR)**, under **Access > Members**, a member's menu:

- **Download their data**: their member row, the questions they asked, their chats,
  feedback, API keys, invites, and the audit events where they are the actor.
- **Erase their data**: deletes their questions, chats, feedback, API keys, invites,
  sessions, sign-in events and the member. Where their address names who made
  something (a provider, an invite, a verified answer) it's replaced by a
  pseudonym. **The audit log is not edited**: it's a hash chain, and keeps what
  happened (a legal-obligation and legitimate-interest record). Their rows stay as
  hashed, and every read, export and filter shows the pseudonym (and no IP or
  browser) instead of them; the chain still verifies. `audit_pseudonyms` holds only
  a hash of their address.

### Subprocessors

What a customer's data goes through, for a DPA or a security questionnaire:

| Subprocessor | What it gets | Where | Notes |
|---|---|---|---|
| OpenAI (or Azure OpenAI) | Questions, the passages retrieved for them, documents when they are embedded or summarised | US (OpenAI); the chosen region (Azure) | API data isn't used for training; zero-data-retention or Azure in-region on request. **Not used** for a workspace on AWS Bedrock, nor at all with `MODEL_PROVIDER=bedrock` |
| Amazon Web Services (Amazon Bedrock), when `MODEL_PROVIDER=bedrock` | The same as OpenAI above, instead of OpenAI | The AWS region set (`AWS_REGION`) | Bedrock doesn't store prompts or use them for training, and model providers don't see them ("AWS Bedrock" above). A workspace that brings its own AWS account is its own processor there: its data goes to ITS account, under its contract with AWS, and is not one of ours |
| The hosting provider (the VPS) | Everything at rest: the database, documents, index, backups on the server | The server's region | Disk encryption; secrets additionally sealed per workspace |
| The email provider (`MAIL_BACKEND`: SMTP relay, Resend or Postmark) | Recipients' addresses and the email's text: invites, removal and deletion notices | The provider's region | Only when email is turned on |
| The off-site backup target (rclone / restic remote) | Encrypted backup archives (age); never the key | The bucket's region | Only when set up |
| Sentry (`SENTRY_DSN`) | Error reports, scrubbed of request bodies | Sentry's region | Only when set up |
| Slack, Google, Microsoft, Notion, Atlassian, Outline | What the workspace connects: its own data, fetched with its own tokens | The provider's | Chosen by the customer; revoked when disconnected or deleted |

## The operator console

Whoever runs the server for every workspace uses `/operator` (not a workspace's
Access screen). Operators are kept apart from any workspace's members:

```bash
OPERATOR_EMAILS=ops@cloudastra.in,devarshi@cloudastra.in   # who may sign in there
OPERATOR_SSO_PROVIDERS=google        # optional: which server-wide providers (default: all)
OPERATOR_REQUIRE_MFA=on              # optional: the ID token's amr must show a second factor
OPERATOR_SESSION_HOURS=4             # default 4
```

They sign in only through a server-wide single sign-on provider (`GOOGLE_CLIENT_ID`,
...), never a password; enforce 2-step verification for them at the provider
(Google Workspace / Entra ID) whether or not `OPERATOR_REQUIRE_MFA` is on. Removing
an address from `OPERATOR_EMAILS` ends that operator's access at once.

The console lists each workspace with its seats (members active in the last 30
days), questions and model cost, last activity, connectors in error and index
status, and can suspend or unsuspend it (everyone signed out; API keys, SCIM and
Slack stop), re-run its index, reset its rate limits (this process's counters)
and read its audit log. **Open as support** works only while the workspace's
admin allows support: the operator then uses the workspace as an admin, for at
most an hour at a time and never past the grant, can't delete it, export it,
grant access or erase anyone, and every request they make is in its audit log
with `actor_type=operator`. Every operator action is in the workspace's audit log
and in the operators' own chain.

### Abuse, cost alerts and spend caps

An hourly job (`abuse_monitor`) checks every workspace and raises an alert in the
console's **Alerts** panel, emailed once to the operators: questions today above
k times the 7-day median, one member asking more than N questions an hour, a spike
of failed sign-ins (`sso_events`), and one address creating several workspaces in a
day (the audit log's `workspace.create`). A daily job (`usage_daily`) keeps
questions, people, cached answers, tokens and cost per workspace and day.

```bash
ABUSE_ALERT_EMAILS=ops@cloudastra.in   # default: OPERATOR_EMAILS
ABUSE_NOTIFY_TENANT_ADMINS=off         # on: the workspace's admins are told too
ABUSE_SPIKE_FACTOR=3                   # k, for questions and failed sign-ins
ABUSE_SPIKE_MIN=50                     # questions a day before a spike counts
ABUSE_USER_PER_HOUR=120                # N, one member's questions an hour
ABUSE_FAILED_SIGN_INS=20               # failed sign-ins an hour before a spike counts
ABUSE_SIGNUPS_PER_IP=3                 # workspaces a day from one address
SPEND_SOFT_USD_PER_DAY=                # empty: no cap (each workspace can get its own
SPEND_HARD_USD_PER_DAY=                #   in the console: row menu, Spend caps)
```

Past the **soft** cap the workspace's admins and the operators are emailed once a
day; past the **hard** cap every question, on every surface, is answered with a 429
and a plain sentence until midnight UTC. Spend is the question log's tokens at list
prices, as Insights shows it. Alerts the tenant is told about are also an
`abuse.alert` webhook event.

## Sign-up, trials and billing

### Sign-up methods

`WORKSPACE_SIGNUP=on` lets a new company make its own workspace from the sign-in
page. The founder proves the company owns its email domain first, in one of three
ways, each offered once it's set up (`WORKSPACE_SIGNUP_METHODS` narrows them):

- **Google Workspace**: the Google client (`GOOGLE_CLIENT_ID`); the account's `hd`
  must be the email's domain.
- **Microsoft 365**: see below.
- **Email and DNS**: anyone else. The founder gets a link, opens it, and is shown a
  TXT record (`_knowledge-assistant.<domain>`, `kb-verify=...`) to add at their DNS
  host. No workspace exists until the record checks out; then it's made and the
  founder sets a password. Personal mail domains (gmail.com, outlook.com, ...) are
  refused, and one address can start at most 3 an hour. The link needs real email
  (`MAIL_BACKEND`, above), not `console`, in production.

`WORKSPACE_SIGNUP_CODES` still gates any of them (each code makes one workspace).
Every new workspace starts with an onboarding checklist on its Access screen,
worked out from its data (domain verified, documents connected, first index, first
cited answer, 3 colleagues signed in), and an "ask your first question" screen with
an optional sample HR handbook that its admin removes in one click.

### Microsoft 365 sign-up

1. Entra admin center → App registrations → New registration. Supported account
   types: **Accounts in any organizational directory (multitenant)**. Redirect URI
   (Web): `<PUBLIC_URL>/api/auth/m365/callback`.
2. API permissions: Microsoft Graph, delegated **User.Read** (the default). No admin
   consent is needed in most tenants.
3. Certificates & secrets → New client secret. Set `M365_CLIENT_ID` (the
   application ID) and `M365_CLIENT_SECRET`, and restart.

The founder's user principal name must be on a domain their tenant has verified
(Graph's `organization` resource); guests are refused. Members of a workspace made
this way sign in with "Microsoft 365" on the sign-in page.

### Billing (Stripe)

Off unless `BILLING=on`, and test mode only: a live key (`sk_live_`, `rk_live_`) is
refused unless `STRIPE_ALLOW_LIVE=on`. With billing off nothing is limited and the
Plan & billing screen isn't shown.

With it on:

- A workspace that signs up gets a `TRIAL_DAYS` (default 14) trial of the Trial
  plan, with a countdown banner and emails 7, 3 and 1 days before it ends. After
  it the workspace is **read-only** (people sign in, read and export; nothing new
  is asked, uploaded or changed). 30 days after the trial ended it's deleted
  through the workspace lifecycle (its admins are told and can still cancel).
  Operators extend a trial in the console (row menu, Extend trial; audited).
- Workspaces made before billing, or by the operator, have no plan and are never
  limited until an operator gives them one (Extend trial, then the owner picks a
  plan). Give them a billing owner first (row menu, Billing owner).
- Plans are rows in the `plans` table (seeded: Trial, Starter, Business,
  Enterprise), each with limits (questions a month, documents, storage, connectors)
  and features (own sign-in provider, SCIM, audit export, API, Slack, MCP, Teams).
  A feature the plan lacks answers 402 with what the plan includes; a limit is
  explained where it blocks (the chat says "Your plan's limit"). They compose with
  the spend caps above: whichever is reached first refuses.
- Seats are members not turned off who signed in or asked something in the last 30
  days. Checkout charges for the seats in use when it starts.
- Only a **billing owner** (the founder, and whoever an owner adds) starts Checkout
  or opens the Customer Portal; other admins see the screen, support never changes
  it.

Stripe setup (in test mode):

1. Products: one per plan sold, each with a monthly recurring per-unit price. Put
   each price ID in `STRIPE_PRICE_STARTER`, `STRIPE_PRICE_BUSINESS`,
   `STRIPE_PRICE_ENTERPRISE` (a plan without one shows "Not on sale here yet").
2. Developers → API keys: `STRIPE_SECRET_KEY=sk_test_...` (or a restricted key with
   Checkout Sessions, Customer Portal and Customers write).
3. Developers → Webhooks → Add endpoint `<PUBLIC_URL>/api/billing/webhook` with
   `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted` and
   `invoice.payment_failed`. Put its signing secret in `STRIPE_WEBHOOK_SECRET`.
   Every event's signature is checked, and each is applied once.
4. Settings → Billing → Customer portal: allow changing the plan (the same
   prices), the quantity, the payment method and cancelling.
5. Optional: `STRIPE_TAX=on` once Stripe Tax is set up.

### Checking Stripe test mode by hand

Not run live here (no Stripe account); the tests replay recorded fixtures
(`tests/fixtures/stripe/`).

1. `BILLING=on`, `WORKSPACE_SIGNUP=on` and the Stripe settings above; sign up a
   workspace. The banner shows 14 days left; Plan & billing shows the Trial plan,
   usage against its limits and 1 seat.
2. As its founder, Choose Business: Stripe Checkout opens for 1 seat. Pay with
   `4242 4242 4242 4242`, any future date and CVC. Back on Plan & billing, the plan
   is Business, active, and the banner is gone (after the webhook arrives:
   `stripe listen --forward-to <PUBLIC_URL>/api/billing/webhook` on a laptop).
3. "Payment details and invoices" opens the Customer Portal; cancel the
   subscription there. The workspace turns read-only (a 402 on asking, with the
   reason).
4. Pick a plan again, then pay with `4000 0000 0000 0341` (attaches, then fails)
   at the next renewal (Stripe test clock): the status is past due, with a warning,
   and questions still work.
5. A second admin who isn't an owner sees the screen but can't choose a plan
   (403). An owner adds them under Billing owners; now they can.
6. `curl -X POST <PUBLIC_URL>/api/billing/webhook -d '{}'` answers 400 (no
   signature); `stripe trigger checkout.session.completed` is accepted.
7. In the operator console, extend an expired trial: the workspace is writable
   again and its scheduled deletion is cancelled; both are in its audit log.

## Answer cache, languages and personal data

- **Answer cache** (Assistant screen, "Reuse recent answers"): the same question from
  someone with exactly the same principals and role is answered from the answer
  given in the last `ANSWER_CACHE_TTL` seconds (600), while the index, settings and
  verified answers are unchanged; never for follow-ups. "Similar questions" also
  matches by embedding at `ANSWER_CACHE_SIMILARITY` (0.95; one embedding call per
  question). `ANSWER_CACHE=off` turns it off for every workspace.
- **Languages**: with the answer language on "Same as the question", the question's
  language is detected without a model call and the answer is written in it.
  "Search across languages" also searches a question translated into the
  documents' language (one FAST model call for those questions).
- **Personal data** (Assistant screen): which types are masked in answers and in the
  question log, and flagged on the Knowledge screen after indexing, with the rule
  packs US SSN, UK NI, India PAN and Aadhaar, IBAN and payment cards (Luhn). Emails at
  the workspace's verified domains stay in answers unless the admin turns it off.
- **Presidio (optional, not installed by default)**: to add Microsoft Presidio's
  detection for the same types, install it in the image and turn it on:

  ```bash
  pip install presidio-analyzer        # pin the version you tested
  python -m spacy download en_core_web_lg
  PII_ENGINE=presidio
  ```

  It adds a spaCy model (about 600 MB) to the image, which is why it isn't in
  `requirements.txt`. If it isn't installed or fails, the rules alone apply (one
  warning in the log).

## Model providers

The chat models (answer and FAST) and the embeddings are chosen apart, since not
every provider has embeddings. `models.py` keeps a registry of what each provider
offers:

| Provider | Chat | Embeddings | Images | Prompt caching | Key |
|---|---|---|---|---|---|
| `openai` (default) | yes | yes | yes (`ATTACHMENT_VISION_MODEL`) | automatic | `OPENAI_API_KEY` |
| `bedrock` | yes | yes | yes (the chat model) | `cachePoint` marker | the AWS credentials below |
| `anthropic` | yes | **no** | yes (the chat model) | `cache_control` marker | `ANTHROPIC_API_KEY` |
| `local` | no | yes (on this server's CPU) | - | - | none |
| `gemini` | yes | yes | yes (the chat model) | automatic (Google's) | `GEMINI_API_KEY` |
| `azure` | yes (by deployment) | yes (by deployment) | yes (the chat deployment) | automatic | `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` |
| `openai_compatible` | yes | yes, when a model is named | no | the server's own | `OPENAI_COMPAT_BASE_URL` (+ `OPENAI_COMPAT_API_KEY`, optional) |

The server's providers, in `.env`:

- `MODEL_PROVIDER` sets both, as it always has: unset, everything is OpenAI's.
- `CHAT_PROVIDER` and `EMBED_PROVIDER` override one side each, e.g.
  `CHAT_PROVIDER=bedrock` with `EMBED_PROVIDER=openai`.
- Without `EMBED_PROVIDER`, embeddings come from `MODEL_PROVIDER`, else from the chat
  provider when it has them. A chat provider without embeddings (Anthropic) and nothing
  else set gets **local** embeddings (said once in the log at startup): the server never
  sends documents to OpenAI because nobody said otherwise.
- A name that isn't known, or an embeddings provider without embeddings, is refused
  (every model call fails with the reason), never read as `openai`.

A workspace's admin can choose its own on **Assistant → Models**
(`PUT /api/admin/assistant/models`: `chat_provider`, `embed_provider`, each `default`
for the server's, `api_keys: {provider: key}`, and for Azure and an OpenAI-compatible
server `endpoints: {provider: {base_url, api_version}}`). A key is sealed under the
workspace's key, never returned (only `api_keys_saved`) and never in the audit log; a
provider chosen there must have the workspace's own key, never the server's. Keys of
providers the workspace stops using are deleted. Only an embeddings change (provider,
model or size) rebuilds the index; moving only the chat provider doesn't.

**Addresses an admin types** (Azure's endpoint, an OpenAI-compatible server's base
URL) are the server calling wherever it's told, so they go through `url_guard.py`
like a connector's: https only, and the host must resolve only to public IP addresses,
checked when saved and again on every request (a host whose DNS later moves inside is
refused then too, with `UnsafeURL` and the reason). A cloud metadata service, localhost
and the office network are refused. To let workspaces use a server on a private
network (a vLLM or Ollama next to this one), list its `host:port` in `.env`; a listed
pair may also use plain http:

```bash
MODEL_ALLOW_PRIVATE_HOSTS=localhost:11434,vllm.internal:8000
```

It is its own list: `SSO_ALLOW_PRIVATE_HOSTS` and `CONNECTOR_ALLOW_PRIVATE_HOSTS` don't
open it. The server's own addresses in `.env` (`AZURE_OPENAI_ENDPOINT`,
`OPENAI_COMPAT_BASE_URL`) are the operator's and aren't checked. The addresses aren't
secret: they're shown back on the screen and kept in the audit log (no query string is
accepted, so nothing secret rides in one); they're stored in the same sealed JSON as the
workspace's keys, so no migration was needed.

## Anthropic

Claude can write the answers and do the small jobs through Anthropic's own API
(`langchain-anthropic` `ChatAnthropic`; streaming, tool calls, images, prompt caching).
Claude has **no embeddings**, so the index needs another provider:

```bash
CHAT_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
# EMBED_PROVIDER: unset or local = on this server's CPU; openai (with OPENAI_API_KEY,
# for the embeddings only) or bedrock (with AWS_REGION and the BEDROCK_EMBED_* below)
```

With only an Anthropic key, leave `EMBED_PROVIDER` unset (or set it to `local`): the
index is embedded on the server's own CPU ("Local embeddings" below), never by OpenAI
unless told to. A workspace can instead choose Claude on **Assistant → Models** with its own Anthropic
key (`chat_provider: anthropic`, `api_keys: {"anthropic": ...}`), keeping the
server's embeddings, so its index isn't rebuilt.

| Job | Setting | Default | Notes |
|---|---|---|---|
| Chat | `ANTHROPIC_CHAT_MODEL` | `claude-sonnet-5-5` | Claude Sonnet 5.5: $2 / $10 per million tokens in / out |
| Fast | `ANTHROPIC_FAST_MODEL` | `claude-haiku-5-5` | Claude Haiku 5.5: $0.10 / $0.50. The guards' classifiers run on it: run the eval gate before relying on it (below) |
| Fast effort | `ANTHROPIC_FAST_EFFORT` | the model's (`medium` on Haiku 5.5) | `low` makes the small jobs quicker; leave it empty for models without effort (Haiku 4.5) |
| Prompt cache | `ANTHROPIC_PROMPT_CACHE` | `on` | A `cache_control` breakpoint after the agent's rules: the tool list renders before them, so one breakpoint caches both. Prefixes under the model's minimum (512 tokens on the 5.5 models, 4,096 on Haiku 4.5) aren't cached; cache reads are logged as `tokens_cached` |

How it behaves:

- **No temperature.** Current Claude models refuse sampling settings other than their own,
  so the `temperature=0` the pipeline asks for isn't sent.
- **Thinking stays on** (the models' adaptive default) and counts toward `max_tokens`, so
  a call asking for fewer than 1,024 tokens (a label, a one-word classifier) is given
  1,024. Thinking blocks are handed back to Claude inside the agent's tool loop, as the
  API requires; the rest of the app sees plain text.
- **Fails closed.** No key (neither the workspace's nor `ANTHROPIC_API_KEY`) fails every
  model call with the reason. An API error reaches the chat as its class
  (`AnthropicAuthenticationError`, `AnthropicRateLimitError`, `AnthropicOverloadedError`,
  ...), a 502 on `POST /api/chat`; Test connection shows Anthropic's type and message,
  with any key blanked out. Nothing falls back to another provider.
- **Cost.** Insights prices Claude models from `ANTHROPIC_PRICES` (`kb/services/insights.py`,
  list prices, October 2026); a Claude model that isn't listed has an unknown cost.

**Before recommending it for production**, run the gate with a real key, since the
guards' classifiers run on the FAST model:

```bash
CHAT_PROVIDER=anthropic EMBED_PROVIDER=openai python -m eval.harness --gate
CHAT_PROVIDER=anthropic EMBED_PROVIDER=openai python evaluate.py --redteam
```

(`eval.harness` scores retrieval with offline embeddings and calls no chat model; the
red team runs the agent and its guards on Claude.)

## Google Gemini

Gemini writes the answers, does the small jobs and can embed the index, through
Google's **OpenAI-compatible endpoint**
(`https://generativelanguage.googleapis.com/v1beta/openai/`) with the same
langchain-openai classes as OpenAI (`ChatOpenAI`, `OpenAIEmbeddings`): no extra package.
Streaming, tool calls and images work there ([OpenAI
compatibility](https://ai.google.dev/gemini-api/docs/openai)); Google calls it beta.

```bash
CHAT_PROVIDER=gemini
GEMINI_API_KEY=AIza...                # Google AI Studio's key
# EMBED_PROVIDER=gemini               # or openai / bedrock / local; changing it rebuilds the index
```

| Job | Setting | Default | Notes |
|---|---|---|---|
| Chat | `GEMINI_CHAT_MODEL` | `gemini-3.8-flash` | Stable. $0.75 / $3.75 per million tokens in / out until 31 December 2026, then $1.50 / $7.50 |
| Fast | `GEMINI_FAST_MODEL` | `gemini-3.5-flash-lite` | Stable. $0.30 / $2.50. The guards' classifiers run on it: run the eval gate before relying on it |
| Fast thinking | `GEMINI_FAST_EFFORT` | `low` | `reasoning_effort` for the fast model: `minimal`, `low`, `medium`, `high`; empty for the model's own |
| Embeddings | `GEMINI_EMBED_MODEL` | `gemini-embedding-001` | 3,072 numbers, 2,048 tokens in (the 200-token chunks here fit). $0.15 per million tokens |

The curated list on the Models screen is the stable text models of [Gemini
models](https://ai.google.dev/gemini-api/docs/models) as of October 2026: Gemini 4 Argon
(best), 3.8 Flash and 3.6 Flash (balanced), 3.5 Flash-Lite and 3.1 Flash-Lite (fast).
Gemini 2.5 is limited to past users and isn't listed; any id can still be added.

How it behaves:

- **No temperature.** Google recommends leaving Gemini 3 at its default; the
  `temperature=0` the pipeline asks for isn't sent.
- **Thinking stays on** (it can't be turned off on Gemini 3) and counts toward the
  output, so a call asking for fewer than 1,024 tokens (a label, a classifier) is given
  1,024, as on Anthropic.
- **Fails closed.** No key (neither the workspace's nor `GEMINI_API_KEY`) fails every
  model call with the reason. An API error reaches the chat as the OpenAI SDK's class
  (`AuthenticationError`, `RateLimitError`, ...), a 502 on `POST /api/chat`; Test
  connection lists the models (free) and shows the error with the key blanked out.
- **Cost.** Insights prices Gemini from `GEMINI_PRICES` (`kb/services/insights.py`, paid
  tier, prompts up to 200k tokens, October 2026). Gemini 4 Argon and 3.8 / 3.6 Flash are
  at their introductory prices, which step up on 1 December 2026 and 1 January 2027:
  update the table then. A Gemini model that isn't listed has an unknown cost.

A workspace chooses it on **Assistant → Models** (*Add a provider → Google Gemini*, with
its own key; for search, *Google Gemini* under Search). Moving only the answers to
Gemini never rebuilds the index.

## Azure OpenAI

OpenAI's models in a company's own **Azure OpenAI** resource, called by **deployment
name** (`AzureChatOpenAI`, `AzureOpenAIEmbeddings` from langchain-openai: no extra
package). Chat and embeddings, with an API key (Microsoft Entra ID isn't supported here).

```bash
CHAT_PROVIDER=azure                    # and/or EMBED_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_API_VERSION=2024-10-21    # the default: the latest GA data-plane version
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o-prod
AZURE_OPENAI_FAST_DEPLOYMENT=          # empty: the chat deployment does the small jobs too
AZURE_OPENAI_EMBED_DEPLOYMENT=embed-small-prod
```

Requests go to `<endpoint>/openai/deployments/<deployment>/...?api-version=<version>`.
There are no default deployments: they're your own names. On **Assistant → Models** an
admin adds *Azure OpenAI*, pastes the endpoint and key (and an API version, or leaves it
for `2024-10-21`), and types each deployment people may pick; for search, an embedding
deployment. The endpoint goes through `url_guard` (Model providers, above): a private
endpoint (a Private Link address) needs `MODEL_ALLOW_PRIVATE_HOSTS`.

- **No temperature.** Deployment names are your own, so nothing can tell a GPT-5 /
  o-series deployment (which refuses `temperature=0` with a 400) from another: the
  pipeline's `temperature=0` is never sent to Azure, whatever a deployment is named.
  Each deployment runs at its model's own default, as Gemini and Claude do.
- **The fast model** (the guards, query rewrite, HyDE, titles) is the fast deployment
  when one is set, else the workspace's **default** deployment: a chat that picks another
  deployment, or another provider's model, moves only its answer.
- **Test connection** makes one short reply (16 tokens at most) from the default
  deployment and, when Azure embeds, one short embedding: the data plane lists base
  models, not the resource's deployments, so a list proves nothing. A wrong name gives
  `NotFoundError` ("The API deployment for this resource does not exist"), a wrong key
  `AuthenticationError`, both with the key blanked out. Other enabled deployments show
  their own access on their first question.
- **Fails closed**: no endpoint or key fails every model call with the reason; nothing
  falls back to OpenAI.
- **Cost.** Insights prices the model Azure's reply names (`gpt-4o-2024-11-20`) at
  OpenAI's list price when it's in the table (an estimate: Azure prices each
  deployment type and region itself), else as unknown.

## OpenAI-compatible servers

Any server speaking OpenAI's chat completions API: Groq, Together, OpenRouter, a
self-hosted vLLM or Ollama, LiteLLM. Its base URL, an API key (optional: a self-hosted
server may take none) and its model ids, typed in, since there's no fixed list.
Embeddings only when an embedding model is named.

```bash
CHAT_PROVIDER=openai_compatible
OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1   # Together: https://api.together.xyz/v1
OPENAI_COMPAT_API_KEY=gsk_...                           # empty for a server without one
OPENAI_COMPAT_CHAT_MODEL=llama-3.3-70b-versatile
OPENAI_COMPAT_FAST_MODEL=llama-3.1-8b-instant           # empty: the chat model
# EMBED_PROVIDER=openai_compatible with OPENAI_COMPAT_EMBED_MODEL=nomic-embed-text
```

- **The model must call tools**: the agent's search is a tool. Models that can't fail
  the question with the server's error; check its model card first. Images aren't sent
  to these models (the server's abilities aren't known).
- **What's sent**: chat completions with `max_tokens` (which every such server takes),
  `stream_options.include_usage` for the token counts; embeddings as plain text (no
  token ids), floats back, 100 texts a request. The key is sent as the bearer token; a
  server without one gets the placeholder `no-key`. The server's `OPENAI_API_KEY` never
  goes to it. *Remove key* on Assistant → Models clears a saved key alone: the base URL
  and models stay (*Remove* takes the whole server).
- **The base URL is checked** (Model providers, above): a workspace's must be a public
  https address unless `MODEL_ALLOW_PRIVATE_HOSTS` lists it, e.g.
  `MODEL_ALLOW_PRIVATE_HOSTS=localhost:11434` for an Ollama on this machine, then
  `http://localhost:11434/v1`.
- **Test connection** lists the server's models (`GET /models`, free) and fails an
  enabled id it doesn't list. A server without a model list fails the test with its
  `NotFoundError`; the models may still answer.
- **Cost** is unknown in Insights: the tokens are logged, not priced.
- **Switching servers** with the same embedding model id doesn't rebuild the index (the
  index records the provider and model, not the address): rebuild it on Documents (*Rebuild
  index from scratch*) if the new server's model differs.

## Local embeddings

`EMBED_PROVIDER=local` (or a workspace's *Local* embeddings) embeds documents and
questions on the server itself with [fastembed](https://github.com/qdrant/fastembed)
(ONNX Runtime, no torch, no GPU). Nothing leaves the server for the index. It's the
default when the chat provider has no embeddings (Anthropic) and `EMBED_PROVIDER`
isn't set.

| Setting | Default | Notes |
|---|---|---|
| `LOCAL_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | 384 numbers, English, 512 tokens in. About 67 MB to download. Any model in fastembed's list works (`BAAI/bge-base-en-v1.5`, 768; `intfloat/multilingual-e5-large`, 1,024, for other languages) |
| `LOCAL_EMBED_THREADS` | `2` | ONNX Runtime threads, so indexing doesn't take every core of a small server |
| `LOCAL_EMBED_BATCH` | `32` | Texts embedded per batch |
| `LOCAL_EMBED_CACHE` | `config/models/fastembed` (next to `app.db`) | Where the model is downloaded on first use |

**The model is downloaded on first use** (from Hugging Face) into `LOCAL_EMBED_CACHE`.
Keep that folder on a persisted volume: the default sits in `config/`, which
`docker-compose.yml` already mounts, so a new container doesn't download it again
(`scripts/backup.sh` leaves `config/models` out of backups). To
download it ahead of time (a server without internet access later, or a quicker first
index):

```bash
docker compose exec knowledgebase python -c "import models; models.current().embeddings().embed_query('warm up')"
# or, outside Docker, into a folder you then point LOCAL_EMBED_CACHE at:
python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/srv/kb/models/fastembed')"
```

A model that can't be downloaded or loaded fails the index run and every meaning
search with the reason (keywords still work); nothing falls back to another provider.
On a laptop CPU, with 2 threads, `bge-small-en-v1.5` embedded 100 short chunks in
about 1.5 s after a 10 s first load (download included). Switching to or from local
embeddings rebuilds the index, like any embeddings change (384 numbers against
OpenAI's 1,536). The offline eval gate (`python -m eval.harness --gate`) never loads
it: it always indexes with its stand-in embeddings.

## AWS Bedrock

Every model the assistant uses can run on **Amazon Bedrock** instead of OpenAI: the
chat model that writes answers, the FAST model (the guards' classifiers, follow-up
rewriting, HyDE, titles, follow-ups, summaries, digests, insights labels, verified
answer rewording, image attachments), the embeddings of the search index, and
optionally the reranker. Documents and questions then stay in an AWS account and
region of the company's choosing and never reach OpenAI (`models.py`). Two ways:

- **The whole server** (a self-hosted deployment): `MODEL_PROVIDER=bedrock` in `.env`
  with `AWS_REGION` and the server's own AWS credentials (an EC2 instance role, an
  ECS task role, `AWS_PROFILE`, or `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`:
  boto3's default chain).
- **One workspace, in its own AWS account** ("Bring your own AWS"): an admin opens
  **Assistant → Models**, picks *AWS Bedrock in your own AWS account*, and gives the
  region and either a **role** the server assumes (preferred: no AWS secret is stored
  here) or an IAM user's **access keys** (sealed under the workspace's key, never
  shown again). Every change and every Test connection is in the audit log.

**Several answer models, picked per chat.** A workspace can hold credentials for
several chat providers at once (an OpenAI key, an Anthropic key, its AWS). On
Assistant → Models the admin ticks the answer models people may pick (each provider's
curated list in `models.CATALOGUE`, or a custom model id) and marks one as the
default. The chat's composer then shows a model menu (hidden with one model); each
chat keeps its pick (`conversations.model_provider` / `model_id`, migration 0026),
and every question is checked against the enabled list: a model the request names that
isn't enabled is refused with a 400, never swapped silently; a chat whose model was
turned off since starts on the default. Only the answer follows the pick: the FAST model
(the guards' classifiers, titles, HyDE) stays the default provider's, and search stays
the workspace's one embeddings provider. The answer cache keys on the answer model;
the question log's provider is the answer's, its model the one that used the most
tokens. **Test connection costs as little as proves it**: OpenAI, Anthropic, Gemini
and an OpenAI-compatible server list their models (`models.list`, free), and an
enabled model they don't list fails the test by name; Bedrock answers one token on its
default chat model and, when it embeds, makes one short embedding (a model list
wouldn't show the model access granted); Azure OpenAI the same on its default
deployment (its data plane lists base models, not your deployments).

How it calls Bedrock: chat through the **Converse API** (`ConverseStream` while an
answer streams; langchain-aws `ChatBedrockConverse`, with tool calls), embeddings
through **InvokeModel**. A workspace on Bedrock **fails closed**: if its models can't
be reached (no region, a role that can't be assumed, model access missing) the
question fails with the reason; nothing falls back to OpenAI. Hosted rerankers
(Cohere's and Voyage's APIs) are skipped for a Bedrock workspace; `RERANKER=local`
or `RERANKER=bedrock` (Bedrock's Rerank API) still apply.

### Models

| Job | Default | Notes |
|---|---|---|
| Chat (`BEDROCK_CHAT_MODEL`) | `amazon.nova-pro-v1:0` | Must support tool use: Amazon Nova does in both Converse and ConverseStream ([Nova tool use](https://docs.aws.amazon.com/nova/latest/userguide/tool-use.html); [tool use, client side](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use-client-side.html) lists the few models that reject tools when streaming: Llama, Mistral Large, Pixtral, Palmyra; no Nova or Claude model). On-demand by this id in us-east-1; elsewhere use an inference profile id such as `eu.amazon.nova-pro-v1:0`. |
| Fast (`BEDROCK_FAST_MODEL`) | `amazon.nova-lite-v1:0` | On-demand in us-east-1, us-east-2 and us-west-2; `eu.` / `apac.` / `ca.` profiles elsewhere. `amazon.nova-micro-v1:0` is cheaper (text only). Run the leak and red-team suites before changing it: the guards' classifiers run on it. |
| Embeddings (`BEDROCK_EMBED_MODEL`, `BEDROCK_EMBED_DIMENSIONS`) | `amazon.titan-embed-text-v2:0`, 1024 | Titan Text Embeddings V2: up to 8,192 tokens or 50,000 characters in; 1,024 (default), 512 or 256 numbers out, normalised by default ([Titan embeddings](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html), [request body](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-titan-embed-text.html)). `cohere.embed-english-v3` / `cohere.embed-multilingual-v3` make 1,024 and take 512 tokens a text ([Cohere Embed v3](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v3.html)): fine with the 200-token chunks here. |
| Rerank (`RERANKER=bedrock`, `BEDROCK_RERANK_MODEL`) | `cohere.rerank-v3-5:0` | Bedrock's Rerank API (`bedrock-agent-runtime`). `amazon.rerank-v1:0` isn't offered in us-east-1 ([supported regions](https://docs.aws.amazon.com/bedrock/latest/userguide/rerank-supported.html)). |

Claude works too, e.g. `us.anthropic.claude-haiku-4-5-20251001-v1:0`: Claude Haiku 4.5
is called through a geo or global **inference profile** only ("The bare model ID isn't
supported for on-demand throughput"), and Anthropic models need the one-time *First
Time Use* form ([model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)).
Claude Sonnet 4 reached end of life on 14 October 2026; check a model's card before
picking it ([models at a glance](https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html)).

**Switching the embeddings re-embeds everything.** The embedding provider, model and
size are part of the workspace's index fingerprint, so changing them (or moving
between OpenAI and Bedrock) starts a shadow rebuild: the old index keeps answering
until the new one is ready, then it switches (progress in the top bar). The screen
asks for confirmation first. While an OpenAI-built index is being replaced by a
Bedrock one (or the reverse), questions are searched by **keywords only**: they are
never sent to the provider the workspace left.

**Prompt caching.** `BEDROCK_PROMPT_CACHE=auto` (the default) marks the end of the
system prompt (the agent's rules and tools, the same for every question) with a
`cachePoint` for Amazon Nova and Anthropic Claude models; `on` for any model, `off` for
none. A checkpoint needs at least 1,024 tokens on Nova and Claude Sonnet 4.5 (4,096 on
Claude Haiku 4.5); cached tokens last 5 minutes and are reported as
`cacheReadInputTokens`, which the question log keeps as `tokens_cached`
([prompt caching](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)).

**Cost.** Insights prices Bedrock models from `kb/services/insights.py`
`BEDROCK_PRICES` (on-demand, us-east-1, from [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/),
October 2026; a `global.` profile at its own price). A model that isn't in the table
has an **unknown** cost: its tokens are logged but not priced, and Insights names it
instead of guessing. The question log records the provider and the model of every
question.

### Model access

In commercial regions most models are enabled on first use: "Access to many Amazon
Bedrock foundation models is enabled by default with the correct AWS Marketplace
permissions", and a third-party model's subscription starts on its first call (up to
15 minutes). The calling identity needs `aws-marketplace:Subscribe`,
`aws-marketplace:Unsubscribe` and `aws-marketplace:ViewSubscriptions` for that first
call of a third-party (e.g. Cohere, Anthropic) model, and Anthropic models need the
First Time Use form, once per account. The console's **Model access** page applies to
AWS GovCloud (US) only ([model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)).
Amazon Nova and Titan need none of this.

### IAM policy

"To call `Converse`, you require permission for the `bedrock:InvokeModel` operation. To
call `ConverseStream`, you require permission for the
`bedrock:InvokeModelWithResponseStream` operation"
([Converse API](https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html));
there is no separate Converse action. Embeddings are `bedrock:InvokeModel`. Attach
this to the role (or user), with your region and models; the Assistant screen shows it
filled in for the workspace's models:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "KnowledgeAssistantChat",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": [
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0",
        "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0"
      ]
    },
    {
      "Sid": "KnowledgeAssistantEmbeddings",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel"],
      "Resource": ["arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"]
    }
  ]
}
```

With an **inference profile** (`eu.amazon.nova-pro-v1:0`, `global.anthropic...`),
allow the profile AND its foundation model in every region the profile routes to:
"When you specify an inference profile in the `Resource` field in the first statement,
you must also specify the foundation model in each Region associated with it"
([prerequisites](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-prereq.html)):

```json
"Resource": [
  "arn:aws:bedrock:eu-central-1:111122223333:inference-profile/eu.amazon.nova-pro-v1:0",
  "arn:aws:bedrock:*::foundation-model/amazon.nova-pro-v1:0"
]
```

(AWS's own example narrows the foundation-model statement further with
`"Condition": {"StringEquals": {"bedrock:InferenceProfileArn": "<the profile ARN>"}}`;
a `global.` profile also needs `arn:aws:bedrock:::foundation-model/<model>`
[global inference](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html).)
`RERANKER=bedrock` adds `bedrock:Rerank` (on `*`) and `bedrock:InvokeModel` on the
rerank model ([rerank permissions](https://docs.aws.amazon.com/bedrock/latest/userguide/rerank-prereq.html)).

### Cross-account role (Bring your own AWS)

The server assumes the customer's role with **STS AssumeRole**, passing the
workspace's **ExternalId**. The ExternalId is generated by this app for each
workspace and can't be chosen by the admin: that is what stops another customer from
naming the same role and having the server assume it for them (the "confused deputy":
"The `ExternalId` value must be unique among Example Corp's customers and controlled
by Example Corp, not its customers", [IAM](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html)).
It isn't a secret. Sessions last an hour (`DurationSeconds` 3600, STS's default; the
minimum is 900) and are named `knowledge-<workspace>` (what the customer's CloudTrail
shows); they are refreshed shortly before they expire, not assumed per question
([AssumeRole](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html)).

1. Set `BEDROCK_SERVER_PRINCIPAL` to the server's own AWS account id (or the ARN of
   the role it runs as), so the screen shows the right trust policy. The server needs
   AWS credentials of its own (an instance or task role) allowed to call
   `sts:AssumeRole` on customers' roles.
2. The customer creates a role in their account with this trust policy (the screen
   shows it with the workspace's ExternalId filled in) and the IAM policy above:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": {"AWS": "arn:aws:iam::SERVER_ACCOUNT_ID:root"},
       "Action": "sts:AssumeRole",
       "Condition": {"StringEquals": {"sts:ExternalId": "kb-<the workspace's id>"}}
     }]
   }
   ```

3. The admin pastes the role ARN, presses **Test connection** (with what's typed,
   saved or not: one Converse of a single token on the default chat model and one
   short embedding, a fraction of a cent), then Save. It needs only the
   `bedrock:InvokeModel` the policy above already grants.

Access keys instead: an IAM user with the IAM policy above, its keys pasted on the
screen. They're sealed under the workspace's data key (`secret_box`), like other
integration secrets, and deleted with the workspace.

### Privacy

AWS: "Amazon Bedrock doesn't store any text, images, or documents that you provide as
content. The data is only used to generate the response"
([Converse API reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html));
"AWS and the third-party model providers will not use any inputs to or outputs from
Amazon Bedrock to train Amazon Nova, Amazon Titan, or any third-party models"
([Bedrock FAQ](https://aws.amazon.com/bedrock/faqs/)); model providers "don't have
access to Amazon Bedrock logs or to customer prompts and completions"
([data protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html)).
With a workspace on its own AWS account, Bedrock is the customer's own processor, not
ours (see Subprocessors).

### Checking Bedrock by hand (no AWS account here: not run live)

The tests stub every AWS call (`tests/test_bedrock.py`: botocore Stubber for STS,
Converse, InvokeModel and Rerank; a fake runtime for the end-to-end "no OpenAI call"
test). With a real account, once:

1. **Model access.** In the account and region you'll use (e.g. us-east-1), open the
   Bedrock console's playground and send one message to Amazon Nova Pro and Nova Lite,
   and one embedding to Titan Text Embeddings V2 (or call them with the AWS CLI). For
   Claude, submit the First Time Use form first.
2. **The IAM role.** Create the policy above for your models and region, and a role
   with the trust policy the Assistant screen shows (the server's principal, the
   workspace's ExternalId). In IAM's policy simulator, `bedrock:InvokeModel` and
   `bedrock:InvokeModelWithResponseStream` on the model ARNs are *allowed*.
3. **Test connection.** On Assistant → Models choose AWS Bedrock, the region, *A role
   it assumes*, paste the role ARN, press Test connection: "AWS Bedrock: connected",
   naming the models it reached. A wrong ExternalId in the trust policy gives
   `AccessDenied` on AssumeRole; a model without access granted, or a policy missing
   its ARN, gives `AccessDeniedException`; a base id where a profile is needed gives a
   `ValidationException` naming inference profiles. Only the default chat model is
   invoked: another enabled Bedrock model shows its own access on its first question.
   CloudTrail shows `AssumeRole` with the session name `knowledge-<workspace>`, the
   `Converse` call and the embedding's `InvokeModel`.
4. **Save** (confirm the index rebuild): the top bar shows the rebuild; when it's
   done, the audit log has `model_provider.update` with `reindex: true`.
5. **Ask a question** in the chat: it streams; the steps show the search; the answer
   cites the documents. Insights' question log row has provider `bedrock` and the
   chat model's id; cost is priced (Nova) or listed as unknown. With the server's
   `OPENAI_API_KEY` set to an invalid key, everything above still works: nothing
   calls OpenAI.
6. Switch the workspace back to the server default: confirm, the index is rebuilt
   with OpenAI's embeddings, questions are answered by OpenAI again.

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
- **`/api/health` shows `"toxicity": "off"`** — `.env` sets `TOXICITY_GUARD=off`
  (step 4); remove it to turn the check back on. With `REQUIRE_TOXICITY_GUARD=1`
  as well, the container keeps restarting and its log ends with
  `REQUIRE_TOXICITY_GUARD is set but TOXICITY_GUARD=off turns the toxicity check
  off`: remove one of the two. (`"toxicity": "missing"` is a version from before
  the moderation check, which had none.)
- **`The moderation endpoint failed` in the logs** — OpenAI's moderation endpoint
  couldn't be reached or refused the key; the workspace's fast model checked the
  question instead (step 4). **`The toxicity check failed`** — that failed too, so
  the question went through unchecked for abuse (it fails open). The same provider
  error usually shows up in chat; fix the key or the provider, no restart needed.
- **Someone is locked out after "Require single sign-on" was turned on** — only
  admins keep passwords. If the provider itself is broken, sign in as an admin
  with a password and turn it off under **Access → Sign-in**, or run
  `python auth.py role <name> admin`.
- **`OPENAI_API_KEY` errors in logs** — the `.env` key is missing/wrong. Fix it,
  then `docker compose up -d --build`.
- **Port 8501 unreachable** — firewall (step 5) or the cloud provider's
  security-group is still blocking it.
- **Out of memory during build** — this stack (onnxruntime + chromadb) is
  heavy; use a VPS with **≥ 2 GB RAM**.
