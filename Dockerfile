# ---- Chat With Your Notes: production image (multi-stage, slim) ----

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

# curl is only needed for the HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Bring in the pre-built Python packages from the builder stage.
COPY --from=builder /install /usr/local

# Copy the app (docs come along via the build context; chroma_db is gitignored
# and gets rebuilt on first boot — see CMD).
COPY . .

# Streamlit's default port.
EXPOSE 8501

# Simple health check so orchestrators know when the app is up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

# On first run, build the vector DB if it's missing (needs OPENAI_API_KEY),
# then launch the app bound to all interfaces so it's reachable from outside.
CMD ["sh", "-c", "if [ ! -d chroma_db ] || [ -z \"$(ls -A chroma_db 2>/dev/null)\" ]; then echo 'Building chroma_db via ingest.py...'; python ingest.py; fi; exec streamlit run app.py --server.port=${PORT:-8501} --server.address=0.0.0.0 --server.headless=true"]
