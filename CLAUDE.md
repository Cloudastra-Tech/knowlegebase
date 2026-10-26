# knowlegebase

RAG chat app: FastAPI API (`api.py`, port 8010) + React/shadcn site (`frontend/`, Vite proxies
`/api` to 8010). Sign-in code is `auth.py`, `sso.py`, `saml_sp.py`, `members_store.py`.
Architecture: `ARCHITECTURE.md`; ops: `deploy.md`.

Branches: `main` is production. Feature work branches from `dev` and PRs go into `dev` (CI runs
on both); `dev` is promoted to `main` only when the owner says so.

Linux setup (Python 3.12, venv dir is `venv/`):

```bash
python3.12 -m venv venv && venv/bin/pip install -r requirements-dev.txt  # includes requirements.txt
venv/bin/python ingest.py                                   # build the Chroma index once
venv/bin/uvicorn api:app --reload --port 8010
cd frontend && npm install && npx vite --port 5180 --strictPort
venv/bin/python -m pytest
```

- Use Vite on **5180**: 5173 is taken by another app on this machine, and the local `.env`
  `PUBLIC_URL` (OAuth / SAML redirect URIs) is `http://localhost:5180`.
- SAML tests skip without `xmlsec1`. Install it (`apt-get install xmlsec1`, as CI does) or export
  `XMLSEC_BINARY` (shell, not `.env`) pointing at an unpacked copy (`apt download xmlsec1 && dpkg -x xmlsec1_*.deb <dir>`).
- Tests never read `.env`: `tests/conftest.py` stubs `load_dotenv` and points the stores at temp
  files. A branch without that conftest (e.g. `main` before the SSO epic merges) picks up the
  local `.env` and `config/app.db`; run its tests from a fresh worktree, which has neither.
- Local Keycloak (`kb-keycloak` container, http://localhost:8180): set
  `SSO_ALLOW_PRIVATE_HOSTS=localhost:8180` in `.env`. Provider setup: `sign-in-guide.md`
  ("Keycloak"), `deploy.md` §4c and Troubleshooting.
