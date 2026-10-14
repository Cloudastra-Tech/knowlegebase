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
Accounts are public users unless created with `--role hr`:

```bash
docker compose exec knowledgebase python auth.py add admin --role hr
```

Add more people the same way. Also available:
`python auth.py list`, `python auth.py role <username> <public|hr>`,
`python auth.py remove <username>`. Accounts are stored in `./config/users.json`
on the host (mounted into the container, gitignored). Accounts created before
roles existed are public until given a role, e.g. `python auth.py role admin hr`.

## 4c. Single sign-on (Google / Microsoft)

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
4. Set `SESSION_SECRET` to a long random string.
5. Say who may sign in and who is HR / Admin in `./config/sso.json`:

   ```json
   {
     "workspaces": {
       "default": {
         "domains": ["cloudastra.in", "cloudastra.co"],
         "hr": ["someone@cloudastra.in"]
       }
     }
   }
   ```

   Anyone with an address on a listed domain gets in as a public user; addresses
   under `"hr"` get HR / Admin. Other addresses are turned away. List every domain
   of your Google Workspace, primary one included. The file is checked on every
   request, so edits apply straight away (no restart) — remove someone, or their
   HR role, and it takes effect on their next click. The same goes for
   `auth.py role` / `auth.py remove` on password accounts.
6. `docker compose up -d` to pick up the new `.env`. The login page now shows
   *Continue with Google* / *Continue with Microsoft* for each provider set up.
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
  "Isn't set up for this app": add their email's domain to `config/sso.json`.
  "Didn't finish": `docker compose logs` shows the provider's error (e.g. a
  redirect URI that doesn't match `PUBLIC_URL`, or admin consent needed).
- **An admin lost the Documents screen after upgrading** — password accounts
  made before roles existed are public: `python auth.py role <name> hr`.
- **`OPENAI_API_KEY` errors in logs** — the `.env` key is missing/wrong. Fix it,
  then `docker compose up -d --build`.
- **Port 8501 unreachable** — firewall (step 5) or the cloud provider's
  security-group is still blocking it.
- **Out of memory during build** — this stack (onnxruntime + guardrails) is
  heavy; use a VPS with **≥ 2 GB RAM**.
