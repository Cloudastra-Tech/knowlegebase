#!/usr/bin/env bash
# pip_audit.sh — check requirements.txt for known-vulnerable Python packages.
#
#   pip install pip-audit==2.10.1 && scripts/pip_audit.sh
#
# CI's `scan` job runs this and fails on any advisory not listed in IGNORE below.
# Each entry says why it does not apply to this app and what would let it go.
# Re-check the list whenever chromadb, litellm or guardrails-ai is upgraded:
# `pip-audit -r requirements.txt` (no ignores) shows everything, ignored or not.
# Extra arguments are passed to pip-audit (e.g. --format json).

set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

IGNORE=(
  # chromadb 1.5.9: no fixed release exists (1.5.9 is the newest; every advisory
  # lists "<= 1.5.9", no patched version). All four are in Chroma's own HTTP
  # server: its /api/v2 collection endpoints (trust_remote_code model loading)
  # and its multi-tenant authorization. This app only runs Chroma embedded
  # (chromadb.PersistentClient / langchain_chroma on a local folder, rag.py,
  # ingest.py, health.py, api.py); no Chroma server is started or exposed and
  # collections are created by our code with OpenAI embeddings. Drop these when
  # chromadb ships a fix, and before ever running `chroma run` / a Chroma server.
  GHSA-f4j7-r4q5-qw2c   # CVE-2026-45829  pre-auth code injection, create-collection endpoint
  GHSA-36p7-vc44-83pf   # CVE-2026-45833  code injection, update-collection endpoint
  GHSA-xph7-9rjv-w5fr   # CVE-2026-45831  SimpleRBACAuthorizationProvider ignores tenant scope
  GHSA-2wm9-hf6c-p5cr   # CVE-2026-45830  cross-tenant access for authenticated server users

  # filelock 3.32.7: fixed only in 4.2.1, but litellm (required by guardrails-ai
  # 0.11.0, both their newest releases) requires filelock<4.0, so 3.32.7 is the
  # newest installable. One advisory is in ReadWriteLock / AsyncReadWriteLock,
  # which nothing here uses (Chroma's ReadWriteLock is its own threading lock;
  # huggingface_hub uses FileLock / SoftFileLock); the other two are Windows-only
  # and the app runs on Linux. Drop these when litellm allows filelock 4.
  GHSA-v4x8-3wr5-3wq8   # CVE-2026-112940 ReadWriteLock reopens its SQLite file by path (Linux)
  GHSA-9vhv-jxww-xfm5   # CVE-2026-112913 ReadWriteLock SQLite side files (Windows)
  GHSA-567q-5qvw-pf92   # CVE-2026-112907 soft-lock marker read by path (Windows)
)

args=()
for id in "${IGNORE[@]}"; do
  args+=(--ignore-vuln "$id")
done
exec pip-audit -r requirements.txt "${args[@]}" "$@"
