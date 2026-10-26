#!/usr/bin/env bash
# backup.sh — back up the Knowledge assistant on its single Docker host.
#
# One timestamped archive, kb-backup-<UTC time>.tar.gz[.age|.gpg], in $BACKUP_DIR:
#   db/app.db        consistent online copy of config/app.db (SQLite backup API,
#                    never cp: the app may be writing)
#   config/          the rest of config/ (legacy users.json / sso.json, ...)
#   docs/, data/     uploaded and connector-synced documents of every workspace
#   chroma.tar       the Chroma named volume (the search index; rebuildable, but a
#                    rebuild re-embeds every document at OpenAI's prices)
#   MANIFEST         row count per table and file counts, checked by restore.sh
#
# The encryption key for the secrets in app.db (config/integrations.key) is NEVER
# put in that archive: one leaked archive would otherwise decrypt every
# workspace's OAuth tokens and SSO secrets. It is copied to $KEY_BACKUP_DIR
# instead (only when it changed), and where it went is printed.
#
# Settings (environment, or a file named by BACKUP_ENV_FILE holding KEY=value lines):
#   APP_DIR                   the app's folder (default: this repo)
#   BACKUP_DIR                where archives go         (default /var/backups/knowlegebase)
#   KEY_BACKUP_DIR            where the key copy goes   (default $HOME/kb-integrations-key);
#                             must be outside BACKUP_DIR
#   RETENTION_DAYS            local archives older than this are deleted (default 14)
#   CHROMA_VOLUME             Docker volume holding the index (default <project>_chroma_data)
#   CHROMA_DIR                or a plain folder instead of a volume (no Docker needed)
#   KB_IMAGE                  image used to read the volume / run Python when the host
#                             has no python3 (default cloudastra-knowledgebase)
#   BACKUP_AGE_RECIPIENT      encrypt to this age public key (age1...), or
#   BACKUP_GPG_RECIPIENT      to this gpg key; the private key stays OFF this server
#   BACKUP_RCLONE_REMOTE      off-site copy, e.g. b2:kb-backups/prod (rclone config'd)
#   REMOTE_RETENTION_DAYS     delete remote archives older than this (default: keep all)
#   BACKUP_RESTIC_REPO        or a restic repository (RESTIC_PASSWORD[_FILE] set);
#                             kept 7 daily / 4 weekly / 6 monthly
#   BACKUP_PING_URL           healthchecks.io-style URL: pinged on success, /fail on error
#
# Usage: scripts/backup.sh            (as root, from cron; see deploy.md)

set -Eeuo pipefail
umask 077

if [[ -n "${BACKUP_ENV_FILE:-}" ]]; then
    # shellcheck disable=SC1090
    set -a && . "$BACKUP_ENV_FILE" && set +a
fi

say() { printf '[backup] %s\n' "$*"; }
die() { printf '[backup] ERROR: %s\n' "$*" >&2; exit 1; }

ping_url() {
    [[ -n "${BACKUP_PING_URL:-}" ]] || return 0
    curl -fsS -m 10 --retry 3 -o /dev/null "$BACKUP_PING_URL$1" || say "couldn't ping the monitor"
}

stage=""
archive=""
cleanup() {
    local status=$?
    if [[ -n "$stage" && -d "$stage" ]]; then rm -rf "$stage"; fi
    if [[ -n "$archive" ]]; then rm -f "$archive.part"; fi
    if (( status != 0 )); then ping_url /fail; fi
    return "$status"
}
trap cleanup EXIT

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(realpath -m "${APP_DIR:-$SCRIPTS/..}")"
BACKUP_DIR="$(realpath -m "${BACKUP_DIR:-/var/backups/knowlegebase}")"
KEY_BACKUP_DIR="$(realpath -m "${KEY_BACKUP_DIR:-$HOME/kb-integrations-key}")"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
KB_IMAGE="${KB_IMAGE:-cloudastra-knowledgebase}"
project="${COMPOSE_PROJECT_NAME:-$(basename "$APP_DIR" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')}"
CHROMA_VOLUME="${CHROMA_VOLUME:-${project}_chroma_data}"
[[ -d "$APP_DIR" ]] || die "no app folder at $APP_DIR"

# Python for the SQLite steps: the host's, else the app image's (same paths mounted).
py() {
    if command -v python3 >/dev/null 2>&1; then
        python3 "$SCRIPTS/sqlite_tool.py" "$@"
    else
        docker run --rm -i --entrypoint python -v "$SCRIPTS:$SCRIPTS:ro" -v "$APP_DIR:$APP_DIR" \
            -v "$BACKUP_DIR:$BACKUP_DIR" "$KB_IMAGE" "$SCRIPTS/sqlite_tool.py" "$@"
    fi
}

case "$KEY_BACKUP_DIR/" in
    "$BACKUP_DIR"/*) die "KEY_BACKUP_DIR must be outside BACKUP_DIR (the key never travels with the archives)" ;;
esac

db="$APP_DIR/config/app.db"
[[ -f "$db" ]] || die "no database at $db"
mkdir -p "$BACKUP_DIR"
exec 9>"$BACKUP_DIR/.backup.lock"
flock -n 9 || die "another backup is running"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="kb-backup-$stamp"
stage="$(mktemp -d "$BACKUP_DIR/.$name.XXXXXX")"
mkdir -p "$stage/db"

# 1. SQLite: an online copy that is consistent even while the app writes.
say "copying the database"
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$db" ".backup '$stage/db/app.db'"
else
    py backup "$db" "$stage/db/app.db"
fi
py report "$stage/db/app.db" > "$stage/db.report"
[[ "$(head -n1 "$stage/db.report")" == "integrity ok" ]] || die "the copied database fails its integrity check"

# 2. The search index: a named Docker volume, or a folder.
chroma="none"
if [[ -n "${CHROMA_DIR:-}" ]]; then
    [[ -d "$CHROMA_DIR" ]] || die "CHROMA_DIR $CHROMA_DIR isn't a folder"
    tar -C "$CHROMA_DIR" -cf "$stage/chroma.tar" .
    chroma="folder $CHROMA_DIR"
elif command -v docker >/dev/null 2>&1 && docker volume inspect "$CHROMA_VOLUME" >/dev/null 2>&1; then
    say "copying the search index (volume $CHROMA_VOLUME)"
    docker run --rm --entrypoint tar -v "$CHROMA_VOLUME:/v:ro" -v "$stage:/out" \
        "$KB_IMAGE" -C /v -cf /out/chroma.tar .
    chroma="volume $CHROMA_VOLUME"
else
    say "WARNING: no search index found (volume $CHROMA_VOLUME); the archive won't have one"
fi

# 3. What's in it, for restore.sh to check.
count_files() { if [[ -d "$APP_DIR/$1" ]]; then find "$APP_DIR/$1" -type f | wc -l; else echo 0; fi; }
{
    echo "created $stamp"
    echo "app_dir $APP_DIR"
    echo "version $(cd "$APP_DIR" && git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "chroma $chroma"
    echo "files docs $(count_files docs)"
    echo "files data $(count_files data)"
    sed -e 1d -e 's/^/rows /' "$stage/db.report"
} > "$stage/MANIFEST"
rm "$stage/db.report"

# 4. One archive. config/ minus the database files (copied above) and the key.
say "writing the archive"
archive="$BACKUP_DIR/$name.tar.gz"
staged=(MANIFEST db)
if [[ -f "$stage/chroma.tar" ]]; then staged+=(chroma.tar); fi
members=()
for d in config docs data; do
    if [[ -d "$APP_DIR/$d" ]]; then members+=("$d"); fi
done
tar -czf "$archive.part" \
    --exclude='config/integrations.key*' --exclude='config/app.db*' \
    -C "$stage" "${staged[@]}" -C "$APP_DIR" "${members[@]}"
if tar -tzf "$archive.part" | grep -q 'integrations\.key'; then
    rm -f "$archive.part"
    die "the encryption key ended up in the archive; refusing to keep it"
fi
mv "$archive.part" "$archive"

# 5. Encrypt (to a public key: this server can't decrypt its own backups).
if [[ -n "${BACKUP_AGE_RECIPIENT:-}" ]]; then
    age -r "$BACKUP_AGE_RECIPIENT" -o "$archive.age" "$archive"
    rm -f "$archive"
    archive="$archive.age"
elif [[ -n "${BACKUP_GPG_RECIPIENT:-}" ]]; then
    gpg --batch --yes --trust-model always -r "$BACKUP_GPG_RECIPIENT" -o "$archive.gpg" -e "$archive"
    rm -f "$archive"
    archive="$archive.gpg"
else
    say "WARNING: not encrypted (set BACKUP_AGE_RECIPIENT or BACKUP_GPG_RECIPIENT)"
fi
say "archive: $archive ($(du -h "$archive" | cut -f1))"

# 6. The key, on its own, only when it changed.
keyfile="$APP_DIR/config/integrations.key"
if [[ -f "$keyfile" ]]; then
    mkdir -p "$KEY_BACKUP_DIR"
    chmod 700 "$KEY_BACKUP_DIR"
    fingerprint="$(sha256sum "$keyfile" | cut -c1-12)"
    if [[ -n "${BACKUP_AGE_RECIPIENT:-}" ]]; then
        keycopy="$KEY_BACKUP_DIR/integrations-$fingerprint.key.age"
        [[ -f "$keycopy" ]] || age -r "$BACKUP_AGE_RECIPIENT" -o "$keycopy" "$keyfile"
    else
        keycopy="$KEY_BACKUP_DIR/integrations-$fingerprint.key"
        [[ -f "$keycopy" ]] || install -m 600 "$keyfile" "$keycopy"
    fi
    say "encryption key: $keycopy (NOT in the archive; keep a copy in the team password manager too)"
elif grep -qs '^INTEGRATIONS_KEY=.' "$APP_DIR/.env"; then
    say "encryption key: INTEGRATIONS_KEY in $APP_DIR/.env (not backed up here; keep it in the team password manager)"
else
    say "encryption key: none yet (made on first use)"
fi

# 7. Off-site.
if [[ -n "${BACKUP_RCLONE_REMOTE:-}" ]]; then
    say "copying to $BACKUP_RCLONE_REMOTE"
    rclone copy "$archive" "$BACKUP_RCLONE_REMOTE"
    if [[ -n "${REMOTE_RETENTION_DAYS:-}" ]]; then
        rclone delete --min-age "${REMOTE_RETENTION_DAYS}d" --include 'kb-backup-*' "$BACKUP_RCLONE_REMOTE"
    fi
fi
if [[ -n "${BACKUP_RESTIC_REPO:-}" ]]; then
    say "copying to restic $BACKUP_RESTIC_REPO"
    restic -r "$BACKUP_RESTIC_REPO" backup --tag knowlegebase "$archive"
    restic -r "$BACKUP_RESTIC_REPO" forget --tag knowlegebase --prune \
        --keep-daily 7 --keep-weekly 4 --keep-monthly 6
fi
if [[ -z "${BACKUP_RCLONE_REMOTE:-}${BACKUP_RESTIC_REPO:-}" ]]; then
    say "WARNING: no off-site copy (set BACKUP_RCLONE_REMOTE or BACKUP_RESTIC_REPO)"
fi

# 8. Local retention.
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'kb-backup-*' -mtime "+$RETENTION_DAYS" -print -delete |
    sed 's/^/[backup] pruned /'

ping_url ""
say "done"
