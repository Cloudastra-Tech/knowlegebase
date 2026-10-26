# ---- Knowledge Assistant: production image (multi-stage, slim) ----
# React site (frontend/) + FastAPI (api.py) in one container on port 8501.

# --- Stage 0: build the React site into static files (frontend/dist).
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 1: builder. Has the C compiler ONLY to build wheels (e.g. hnswlib).
#     None of these ~250MB of build tools end up in the final image.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# Install into a self-contained prefix we can copy wholesale into the final image.
RUN pip install --prefix=/install -r requirements.txt

# --- Stage 2: the actual runtime image. Slim + no compiler.
FROM python:3.12-slim

# Faster, cleaner Python in containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is only needed for the HEALTHCHECK below; xmlsec1 checks SAML signatures (saml_sp.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl xmlsec1 \
    && rm -rf /var/lib/apt/lists/*

# Bring in the pre-built Python packages from the builder stage.
COPY --from=builder /install /usr/local

# Copy the app (docs come along via the build context; chroma_db is gitignored
# and gets rebuilt on first boot — see CMD).
COPY . .
# The built React site, served by api.py.
COPY --from=web /web/dist ./frontend/dist

# Same port the Streamlit version used, so nginx needs no change.
EXPOSE 8501

# Simple health check so orchestrators know when the app is up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8501/api/health || exit 1

# On first run, build the vector DB if it's missing (needs OPENAI_API_KEY),
# then launch the API + site bound to all interfaces. ONE worker on purpose:
# sign-in codes, OAuth states, the re-index queue and login lockouts live in
# memory, so several workers would not share them (sessions are in SQLite).
# FORWARDED_ALLOW_IPS: the reverse proxy's address. Only it may say (in
# X-Forwarded-For) who the client is; "*" trusts anyone, so clients can pose as
# other addresses and get around the per-address sign-in limits.
CMD ["sh", "-c", "if [ ! -d chroma_db ] || [ -z \"$(ls -A chroma_db 2>/dev/null)\" ]; then echo 'Building chroma_db via ingest.py...'; python ingest.py; fi; exec uvicorn api:app --host 0.0.0.0 --port ${PORT:-8501} --proxy-headers --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-*}"]
