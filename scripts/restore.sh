#!/usr/bin/env bash
# restore.sh — unpack a backup.sh archive into a NEW folder and check it.
#
#   scripts/restore.sh [--key KEYFILE] [--identity AGE_KEY] ARCHIVE TARGET_DIR
#
# ARCHIVE        kb-backup-*.tar.gz, or .tar.gz.age (needs --identity, the age
#                private key) or .tar.gz.gpg (gpg's own keyring)
# TARGET_DIR     must not exist yet, or be empty: this never writes over a live app
# --key KEYFILE  the integrations key backed up apart from the archive
#                (integrations-<fingerprint>.key[.age]); installed as
#                config/integrations.key and checked against the stored secrets
#
# TARGET_DIR ends up laid out like the app folder:
#   config/app.db, config/..., docs/, data/, chroma_db/, BACKUP_MANIFEST
# and every table's row count and the document file counts are checked against
# the MANIFEST written at backup time. Exit status is non-zero on any mismatch.
#
# Settings: KB_IMAGE (default cloudastra-knowledgebase) runs Python when the host
# has no python3.

set -Eeuo pipefail
umask 077

SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KB_IMAGE="${KB_IMAGE:-cloudastra-knowledgebase}"
say() { printf '[restore] %s\n' "$*"; }
die() { printf '[restore] ERROR: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

key="" identity=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --key) key="${2:?--key needs a file}"; shift 2 ;;
        --identity) identity="${2:?--identity needs a file}"; shift 2 ;;
        -h|--help) usage ;;
        --*) die "unknown option $1" ;;
        *) break ;;
    esac
done
[[ $# -eq 2 ]] || usage
archive="$(realpath "$1")"
target="$(realpath -m "$2")"
[[ -f "$archive" ]] || die "no archive at $archive"
if [[ -e "$target" && -n "$(ls -A "$target" 2>/dev/null)" ]]; then
    die "$target isn't empty; restore into a new folder"
fi
mkdir -p "$target"

py() {
    if command -v python3 >/dev/null 2>&1; then
        python3 "$SCRIPTS/sqlite_tool.py" "$@"
    else
        docker run --rm -i --entrypoint python -v "$SCRIPTS:$SCRIPTS:ro" -v "$target:$target" \
            "$KB_IMAGE" "$SCRIPTS/sqlite_tool.py" "$@"
    fi
}

# Decrypting writes plaintext only inside the target folder.
age_decrypt() {
    [[ -n "$identity" ]] || die "$1 is age-encrypted: pass --identity <age private key file>"
    age -d -i "$identity" -o "$2" "$1"
}

plain="$archive"
case "$archive" in
    *.age) plain="$target/.archive.tar.gz"; age_decrypt "$archive" "$plain" ;;
    *.gpg) plain="$target/.archive.tar.gz"; gpg --batch --yes -o "$plain" -d "$archive" ;;
esac

say "unpacking into $target"
tar -xzf "$plain" -C "$target"
[[ "$plain" == "$archive" ]] || rm -f "$plain"
[[ -f "$target/MANIFEST" && -f "$target/db/app.db" ]] || die "not a backup.sh archive (no MANIFEST / db/app.db)"
mkdir -p "$target/config"
mv "$target/db/app.db" "$target/config/app.db"
rmdir "$target/db"
mv "$target/MANIFEST" "$target/BACKUP_MANIFEST"
manifest="$target/BACKUP_MANIFEST"
if [[ -f "$target/chroma.tar" ]]; then
    mkdir -p "$target/chroma_db"
    tar -xf "$target/chroma.tar" -C "$target/chroma_db"
    rm "$target/chroma.tar"
fi

# ---- Check it against the MANIFEST ----
failed=0
check() {   # what, expected, actual
    if [[ "$2" == "$3" ]]; then
        printf '[restore]   ok        %-28s %s\n' "$1" "$3"
    else
        printf '[restore]   MISMATCH  %-28s expected %s, got %s\n' "$1" "$2" "$3"
        failed=1
    fi
}

say "checking against the manifest (backup of $(awk '$1=="created"{print $2}' "$manifest"))"
report="$(py report "$target/config/app.db")" || true
check "database integrity" "integrity ok" "$(head -n1 <<<"$report")"
while read -r _ table rows; do
    check "rows $table" "$rows" "$(awk -v t="$table" '$1==t{print $2}' <<<"$report")"
done < <(grep '^rows ' "$manifest")
# Tables in the restored database that the manifest doesn't know about.
while read -r table _; do
    grep -q "^rows $table " "$manifest" || check "rows $table" "(not in manifest)" "present"
done < <(tail -n +2 <<<"$report")

count_files() { if [[ -d "$target/$1" ]]; then find "$target/$1" -type f | wc -l; else echo 0; fi; }
for d in docs data; do
    check "files $d" "$(awk -v d="$d" '$1=="files" && $2==d{print $3}' "$manifest")" "$(count_files "$d")"
done

if grep -q '^chroma none' "$manifest"; then
    say "  (the backup has no search index: run ingest.py / re-index after restoring)"
else
    if [[ -f "$target/chroma_db/chroma.sqlite3" ]]; then
        check "search index integrity" "integrity ok" \
            "$(py report "$target/chroma_db/chroma.sqlite3" | head -n1)"
    else
        check "search index" "chroma.sqlite3" "missing"
    fi
fi

# ---- The integrations key, kept apart from the archive ----
if [[ -n "$key" ]]; then
    case "$key" in
        *.age) age_decrypt "$key" "$target/config/integrations.key" ;;
        *) install -m 600 "$key" "$target/config/integrations.key" ;;
    esac
    chmod 600 "$target/config/integrations.key"
    if out="$(py check-key "$target/config/app.db" "$target/config/integrations.key")"; then
        say "  ok        integrations key             $out"
    elif [[ $? -eq 3 ]]; then
        say "  skipped   integrations key             $out"
    else
        printf '[restore]   MISMATCH  %-28s %s\n' "integrations key" "$out"
        failed=1
    fi
else
    say "  no --key given: SSO secrets and integration tokens in app.db stay unreadable"
    say "  until config/integrations.key (or INTEGRATIONS_KEY) is put back"
fi

if (( failed )); then
    die "the restored copy does NOT match its manifest (see MISMATCH above)"
fi
say "restored and checked: $target"
say "try it: cd $target && <app>/venv/bin/uvicorn api:app --app-dir <app> --port 8011"
say "  then GET /api/health/ready (see deploy.md, Backups and restore)"
