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
  - back up `./config/` as a whole: `app.db` holds the providers' secrets
    **encrypted** with `config/integrations.key` (or `INTEGRATIONS_KEY`), and one is
    useless without the other.
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
curl -fsS http://localhost:8501/api/health && echo OK
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
| View logs | `docker compose logs -f` |
| Stop | `docker compose down` |
| Restart | `docker compose restart` |
| Deploy new code | `git pull && docker compose up -d --build` |
| Rebuild vector DB from scratch | `docker compose down -v && docker compose up -d --build` |
| Re-index after editing `docs/` by hand | `docker compose exec knowledgebase python ingest.py && docker compose restart` |
| Shell into the container | `docker compose exec knowledgebase sh` |

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
- **"Too many attempts. Wait a minute"** — the sign-in endpoints are rate-limited
  per address (and overall). Set `FORWARDED_ALLOW_IPS` in `.env` to your reverse
  proxy's address (e.g. `172.17.0.1` for nginx on the Docker host): uvicorn then
  takes the client's address from the proxy only. The default, `*`, trusts the
  `X-Forwarded-For` header from anyone, so a client can pose as many addresses.
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
