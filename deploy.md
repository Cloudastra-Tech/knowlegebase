# Deploy — Chat With Your Notes (Docker on a VPS)

Self-contained runbook. A person **or a Claude agent** can follow this top-to-bottom
to deploy the app on a fresh Linux server. Commands are for **Ubuntu 22.04+**.

---

## 0. What you need before starting

- A fresh **Ubuntu 22.04+** VPS with root/sudo SSH access.
- The **private** repo URL: `https://github.com/Cloudastra-Tech/knowlegebase.git`
  (private matters — `docs/restricted/staff_salaries.txt` is committed).
- An **OpenAI API key** (`sk-...`). Optionally Outline + Cloudflare Access values.

> ⚠️ **Security note — the app has NO real login.** The "Public / HR-Admin" choice
> in the UI is just a dropdown (`app.py`), so *anyone who can reach the URL can
> select "HR / Admin" and read staff salaries.* This runbook exposes the app
> directly on port 8501. Only do this on an internal / IP-restricted network. To
> add a real password, put Caddy or Cloudflare Access in front (not covered here).

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
# Wait for the Streamlit URL line, then Ctrl-C to stop tailing.
```

## 5. Open the firewall

```bash
sudo ufw allow 8501/tcp     # skip if your cloud uses security-groups instead
```

## 6. Confirm it's up

```bash
docker compose ps           # STATUS should show "Up"
curl -fsS http://localhost:8501/_stcore/health && echo OK
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
| Shell into the container | `docker compose exec knowledgebase sh` |

## Troubleshooting

- **Page won't load right after `up`** — first boot is still building Chroma.
  `docker compose logs -f` until you see the Streamlit URL.
- **`OPENAI_API_KEY` errors in logs** — the `.env` key is missing/wrong. Fix it,
  then `docker compose up -d --build`.
- **Port 8501 unreachable** — firewall (step 5) or the cloud provider's
  security-group is still blocking it.
- **Out of memory during build** — this stack (onnxruntime + guardrails) is
  heavy; use a VPS with **≥ 2 GB RAM**.
