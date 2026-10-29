#!/usr/bin/env bash
# deploy.sh — deploy a version of the Knowledge assistant on its Docker host, wait
# until it can serve, and go back to the version before automatically if it can't.
#
#   scripts/deploy.sh [options] REF           build REF (branch, tag or commit) here
#   scripts/deploy.sh [options] --image TAG   run $REGISTRY_IMAGE:TAG (built by the
#                                             release workflow: deploy.md, "Deploying")
#   scripts/deploy.sh [options] --rollback    back to the version before the current one
#
# Options:
#   --dry-run       print every step and change nothing (no fetch, backup, build, restart)
#   --timeout SECS  how long the new version has to pass /api/health/ready (default 300;
#                   a first start with an empty index builds it first)
#   --restore-db    with --rollback: put back config/app.db as it was before the current
#                   version was deployed; needed when that version migrated it, since
#                   the older one can't start on a newer database. Changes made since
#                   that deploy are lost (scripts/backup.sh's archives still have them).
#   --skip-backup   don't run scripts/backup.sh first
#
# What a deploy does, in order:
#   1. git fetch, and which commit REF is.
#   2. scripts/backup.sh (with BACKUP_ENV_FILE, as cron runs it), and a quick copy of
#      config/app.db into .deploy/db/ for an automatic rollback to put back.
#   3. check out the commit (detached; stops if local changes would be overwritten,
#      e.g. a hand-applied patch: the old version keeps running), then
#      docker compose build, the image tagged cloudastra-knowledgebase:<commit>;
#      or, with --image, docker pull and tag it the same way.
#   4. docker compose up -d with that tag.
#   5. wait for HEALTH_URL (/api/health/ready) to answer 200.
#   6. ok: record it in .deploy/ (current, previous, history), tag it :latest, and
#      remove older image tags beyond KEEP_IMAGES.
#      Not ok: stop it; if its migrations changed the database, put back the copy
#      from step 2; check out the previous commit, start the previous image (still
#      there: no rebuild), wait for it, and exit 1. Exit 3 if even that fails.
#
# Settings (environment):
#   APP_DIR          the app's folder (default: this repo)
#   HEALTH_URL       default http://127.0.0.1:8501/api/health/ready
#   REGISTRY_IMAGE   for --image (default ghcr.io/cloudastra-tech/knowlegebase)
#   GIT_REMOTE       default origin
#   KEEP_IMAGES      older image tags kept, besides the current and previous (default 3)
#   BACKUP_ENV_FILE  handed to scripts/backup.sh
#
# Exit status: 0 deployed; 1 not deployed (the previous version runs, or nothing
# changed); 2 usage; 3 the new version failed AND going back failed: look now.

set -Eeuo pipefail
umask 077

IMAGE_NAME=cloudastra-knowledgebase       # docker-compose.yml's image, before the tag
SERVICE=knowledgebase

say() { printf '[deploy] %s\n' "$*"; }
warn() { printf '[deploy] WARNING: %s\n' "$*" >&2; }
die() { printf '[deploy] ERROR: %s\n' "$1" >&2; exit "${2:-1}"; }
usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

# Every step that changes something goes through run: printed, and skipped in a dry run.
run() {
    printf '[deploy] + %s\n' "$*"
    if (( ! dry_run )); then "$@"; fi
}

py() {
    if command -v python3 >/dev/null 2>&1; then
        python3 "$SCRIPTS/sqlite_tool.py" "$@"
    else
        docker run --rm -i --entrypoint python -v "$SCRIPTS:$SCRIPTS:ro" -v "$APP_DIR:$APP_DIR" \
            "$IMAGE_NAME:latest" "$SCRIPTS/sqlite_tool.py" "$@"
    fi
}

gitc() { git -C "$APP_DIR" "$@"; }
image_exists() { docker image inspect "$IMAGE_NAME:$1" >/dev/null 2>&1; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
short() { printf '%s' "${1:0:12}"; }

db_revision() {   # the migration a database file is at ("none" without one)
    if [[ -f "$1" ]]; then py revision "$1"; else echo none; fi
}

# .deploy/current and .deploy/previous: "<image tag> <commit or -> <deployed at>"
read_state() {
    local file="$STATE/$1"
    state_tag="" state_commit=""
    if [[ -s "$file" ]]; then
        read -r state_tag state_commit _ < "$file"
    fi
    [[ "$state_commit" == "-" ]] && state_commit=""
    return 0
}

write_state() {   # file tag commit
    printf '%s %s %s\n' "$2" "${3:--}" "$(now)" > "$STATE/$1"
}

wait_ready() {    # seconds
    local deadline=$(( SECONDS + $1 ))
    say "waiting up to $1s for $HEALTH_URL"
    if (( dry_run )); then return 0; fi
    while (( SECONDS < deadline )); do
        if curl -fsS -m 5 -o /dev/null "$HEALTH_URL" 2>/dev/null; then
            say "ready"
            return 0
        fi
        sleep "${POLL_SECONDS:-3}"
    done
    warn "not ready after $1s; what it says:"
    curl -sS -m 5 "$HEALTH_URL" >&2 || true
    echo >&2
    return 1
}

start() {         # tag: run that image (compose's IMAGE_TAG)
    run env IMAGE_TAG="$1" docker compose up -d --no-build
}

ensure_image() {  # tag commit: the image is here, or is built again from its commit
    image_exists "$1" && return 0
    [[ -n "$2" ]] || { warn "no image $IMAGE_NAME:$1 and no commit to build it from"; return 1; }
    warn "image $IMAGE_NAME:$1 is gone; building it again from $2 (slow)"
    build "$1" "$2"
}

build() {         # tag commit
    local version
    version="$(gitc describe --tags --always "$2" 2>/dev/null || short "$2")"
    run env IMAGE_TAG="$1" APP_VERSION="$version" GIT_SHA="$2" \
        docker compose build
}

resolve() {       # ref -> its commit, or nothing
    gitc rev-parse --verify -q "$GIT_REMOTE/$1^{commit}" 2>/dev/null \
        || gitc rev-parse --verify -q "$1^{commit}" 2>/dev/null || true
}

checkout() {      # commit
    [[ -n "$1" ]] || return 0
    run git -C "$APP_DIR" checkout -q --detach "$1"
}

# Go back to tag/commit. restore=1: put back the database copy taken before the
# version being left was started (its migrations ran on the live one).
go_back() {       # tag commit snapshot restore
    local tag="$1" commit="$2" snapshot="$3" restore="$4"
    say "going back to $IMAGE_NAME:$tag${commit:+ (commit $(short "$commit"))}"
    run docker compose stop "$SERVICE" || return 1
    if (( restore )); then
        [[ -f "$snapshot" ]] || { warn "no database copy at $snapshot"; return 1; }
        warn "putting back the database from before the deploy ($snapshot):" \
             "anything written since is lost; scripts/backup.sh's archive has it"
        run cp "$snapshot" "$DB.restoring" || return 1
        run rm -f "$DB-wal" "$DB-shm" || return 1
        run mv "$DB.restoring" "$DB" || return 1
    fi
    checkout "$commit" || return 1
    ensure_image "$tag" "$commit" || return 1
    start "$tag" || return 1
    wait_ready "$TIMEOUT"
}

backup() {
    if (( skip_backup )); then
        warn "--skip-backup: no backup taken"
        return 0
    fi
    run env APP_DIR="$APP_DIR" "$SCRIPTS/backup.sh"
}

# The tags of this image, newest first, minus the ones to keep.
prune_images() {
    local keep="${KEEP_IMAGES:-3}" n=0 tag
    while read -r tag; do
        [[ -z "$tag" || "$tag" == "<none>" || "$tag" == latest ]] && continue
        [[ "$tag" == "$new_tag" || "$tag" == "$prev_tag" ]] && continue
        n=$(( n + 1 ))
        if (( n > keep )); then
            run docker rmi "$IMAGE_NAME:$tag" >/dev/null || warn "couldn't remove $IMAGE_NAME:$tag"
            run rm -f "$STATE/db/$tag.db"
        fi
    done < <(docker image ls "$IMAGE_NAME" --format '{{.Tag}}' 2>/dev/null || true)
}

main() {
    dry_run=0 skip_backup=0 restore_db=0 rollback=0 image="" ref="" TIMEOUT=300
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run) dry_run=1; shift ;;
            --skip-backup) skip_backup=1; shift ;;
            --restore-db) restore_db=1; shift ;;
            --rollback) rollback=1; shift ;;
            --image) image="${2:?--image needs a tag}"; shift 2 ;;
            --timeout) TIMEOUT="${2:?--timeout needs seconds}"; shift 2 ;;
            -h|--help) usage ;;
            --*) die "unknown option $1" 2 ;;
            *) [[ -z "$ref" ]] || die "one REF only" 2; ref="$1"; shift ;;
        esac
    done
    [[ "$TIMEOUT" =~ ^[0-9]+$ ]] || die "--timeout needs a number of seconds" 2
    if (( rollback )); then
        [[ -z "$ref$image" ]] || die "--rollback takes no REF or --image" 2
    else
        [[ -n "$ref" || -n "$image" ]] || usage
        [[ -z "$ref" || -z "$image" ]] || die "give a REF or --image, not both" 2
        (( ! restore_db )) || die "--restore-db goes with --rollback" 2
    fi

    SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    APP_DIR="$(realpath -m "${APP_DIR:-$SCRIPTS/..}")"
    HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8501/api/health/ready}"
    REGISTRY_IMAGE="${REGISTRY_IMAGE:-ghcr.io/cloudastra-tech/knowlegebase}"
    GIT_REMOTE="${GIT_REMOTE:-origin}"
    STATE="$APP_DIR/.deploy"
    DB="$APP_DIR/config/app.db"
    [[ -f "$APP_DIR/docker-compose.yml" ]] || die "no docker-compose.yml in $APP_DIR"
    cd "$APP_DIR"           # docker compose reads docker-compose.yml and .env (COMPOSE_FILE) here
    (( dry_run )) && say "DRY RUN: printing the steps, changing nothing"

    mkdir -p "$STATE/db"
    exec 8>"$STATE/.lock"
    flock -n 8 || die "another deploy is running"

    if (( rollback )); then
        do_rollback
    else
        do_deploy
    fi
}

do_deploy() {
    # ---- 0. What runs now: the version to come back to if this one fails ----
    read_state current
    prev_tag="$state_tag" prev_commit="$state_commit"
    if [[ -z "$prev_tag" ]]; then
        # The first deploy with this script: name the running image after the commit.
        prev_commit="$(gitc rev-parse HEAD)"
        prev_tag="$(short "$prev_commit")"
        if ! image_exists "$prev_tag"; then
            local cid running=""
            cid="$(docker compose ps -q "$SERVICE" 2>/dev/null | head -n1 || true)"
            [[ -n "$cid" ]] && running="$(docker inspect -f '{{.Image}}' "$cid" 2>/dev/null || true)"
            if [[ -n "$running" ]]; then
                run docker tag "$running" "$IMAGE_NAME:$prev_tag"
            elif image_exists latest; then
                run docker tag "$IMAGE_NAME:latest" "$IMAGE_NAME:$prev_tag"
            else
                warn "nothing is running and there is no previous image: no automatic rollback"
                prev_tag=""
            fi
        fi
    fi
    say "running now: ${prev_tag:-nothing}${prev_commit:+ (commit $(short "$prev_commit"))}"

    # ---- 1. Which code (with --image: the release's commit, if it is a tag here,
    #      so docker-compose.yml and these scripts match the image) ----
    local commit="" new_tag
    if (( dry_run )); then
        say "+ git -C $APP_DIR fetch --tags --force $GIT_REMOTE   (skipped in a dry run)"
    else
        gitc fetch -q --tags --force "$GIT_REMOTE"
    fi
    if [[ -n "$ref" ]]; then
        commit="$(resolve "$ref")"
        if [[ -z "$commit" ]]; then
            (( dry_run )) || die "no branch, tag or commit $ref at $GIT_REMOTE"
            warn "$ref isn't known here yet (a real run fetches first)"
            commit="$ref"
        fi
        new_tag="$(short "$commit")"
    else
        new_tag="$image"
        commit="$(resolve "$image")"
        [[ -n "$commit" ]] || commit="$(resolve "v$image")"
        [[ -n "$commit" ]] || warn "no tag $image here: the code stays at $(short "$(gitc rev-parse HEAD)")"
    fi
    if [[ "$new_tag" == "$prev_tag" ]]; then
        say "$IMAGE_NAME:$new_tag is already the running version; starting it again"
    fi
    say "deploying: $IMAGE_NAME:$new_tag${commit:+ (commit $(short "$commit"))}"

    # ---- 2. Backups (before anything changes) ----
    backup || die "the backup failed; nothing changed"
    local snapshot="$STATE/db/${prev_tag:-none}.db" rev_before=none
    if [[ -f "$DB" ]]; then
        say "+ copy $DB to $snapshot (put back if the new version's migrations ran and it fails)"
        (( dry_run )) || py backup "$DB" "$snapshot"
        rev_before="$(db_revision "$DB")"
    fi

    # ---- 3. The code, and the image ----
    if [[ -n "$commit" ]]; then
        local changed
        changed="$(gitc status --porcelain --untracked-files=no -- . ':!docs' || true)"
        [[ -z "$changed" ]] || warn "local changes to tracked files (kept unless they conflict):"$'\n'"$changed"
        checkout "$commit" || die "couldn't check out $(short "$commit") (local changes in the way?); nothing was changed"
    fi
    if [[ -n "$image" ]]; then
        { run docker pull "$REGISTRY_IMAGE:$image" \
            && run docker tag "$REGISTRY_IMAGE:$image" "$IMAGE_NAME:$new_tag"; } \
            || not_built "couldn't pull $REGISTRY_IMAGE:$image"
    else
        build "$new_tag" "$commit" || not_built "the build failed"
    fi

    # ---- 4, 5. Start it, and wait until it can serve ----
    if ! start "$new_tag"; then
        fail_and_go_back "it didn't start" "$(migrated "$rev_before")"
    elif ! wait_ready "$TIMEOUT"; then
        fail_and_go_back "it isn't ready" "$(migrated "$rev_before")"
    fi

    # ---- 6. Done ----
    if (( ! dry_run )); then
        if [[ "$new_tag" != "$prev_tag" ]]; then
            [[ -n "$prev_tag" ]] && write_state previous "$prev_tag" "$prev_commit"
        fi
        write_state current "$new_tag" "$commit"
        printf '%s deployed %s %s (before: %s)\n' "$(now)" "$new_tag" "${commit:--}" \
            "${prev_tag:-none}" >> "$STATE/history"
    fi
    run docker tag "$IMAGE_NAME:$new_tag" "$IMAGE_NAME:latest"
    prune_images
    say "deployed $IMAGE_NAME:$new_tag"
}

not_built() {     # reason: the old version still runs; only the code moved
    if [[ -n "$commit" && -n "$prev_commit" ]]; then
        checkout "$prev_commit" || warn "couldn't check out $(short "$prev_commit") again"
    fi
    printf '%s FAILED %s (%s); %s kept running\n' "$(now)" "$new_tag" "$1" "${prev_tag:-nothing}" \
        >> "$STATE/history"
    die "not deployed: $1. ${prev_tag:+$IMAGE_NAME:$prev_tag is still running}"
}

migrated() {      # revision before -> 1 if the live database is at another one now
    if (( dry_run )); then echo 0; return; fi
    [[ "$(db_revision "$DB")" != "$1" ]] && echo 1 || echo 0
}

fail_and_go_back() {   # reason restore
    warn "the new version failed: $1"
    if [[ -z "$prev_tag" ]]; then
        printf '%s FAILED %s (%s); nothing to go back to\n' "$(now)" "$new_tag" "$1" >> "$STATE/history"
        die "nothing to go back to: fix it and deploy again" 3
    fi
    if (( $2 )); then warn "its migrations changed the database"; fi
    if go_back "$prev_tag" "$prev_commit" "$STATE/db/$prev_tag.db" "$2"; then
        printf '%s FAILED %s (%s); went back to %s\n' "$(now)" "$new_tag" "$1" "$prev_tag" \
            >> "$STATE/history"
        die "not deployed: $1. Back on $IMAGE_NAME:$prev_tag" 1
    fi
    printf '%s FAILED %s (%s); going back to %s FAILED too\n' "$(now)" "$new_tag" "$1" \
        "$prev_tag" >> "$STATE/history"
    die "the new version failed ($1) and going back to $prev_tag failed too: look now" 3
}

do_rollback() {
    read_state previous
    local tag="$state_tag" commit="$state_commit"
    [[ -n "$tag" ]] || die "no previous version recorded in $STATE/previous"
    read_state current
    local cur_tag="$state_tag" cur_commit="$state_commit"
    # The copy taken just before the current version first started.
    local snapshot="$STATE/db/$tag.db" restore=0
    if [[ -f "$snapshot" && -f "$DB" ]]; then
        local then_rev now_rev
        then_rev="$(db_revision "$snapshot")" now_rev="$(db_revision "$DB")"
        if [[ "$then_rev" != "$now_rev" ]]; then
            (( restore_db )) || die "$(printf '%s ' "$cur_tag migrated the database" \
                "($then_rev -> $now_rev), so $tag can't start on it. Run again with" \
                "--restore-db to put back the copy from before it ($snapshot;" \
                "changes since are lost), or restore a backup")"
            restore=1
        fi
    elif (( restore_db )); then
        die "no database copy at $snapshot to put back"
    fi
    say "rolling back: $cur_tag -> $tag"
    backup || die "the backup failed; nothing changed"
    if ! go_back "$tag" "$commit" "$snapshot" "$restore"; then
        die "rolling back to $tag failed: look now" 3
    fi
    if (( ! dry_run )); then
        write_state current "$tag" "$commit"
        [[ -n "$cur_tag" ]] && write_state previous "$cur_tag" "$cur_commit"
        printf '%s rolled back %s -> %s\n' "$(now)" "${cur_tag:-?}" "$tag" >> "$STATE/history"
    fi
    run docker tag "$IMAGE_NAME:$tag" "$IMAGE_NAME:latest"
    say "rolled back to $IMAGE_NAME:$tag"
}

# One line, read before it runs: checking out another commit rewrites this file.
main "$@"; exit $?
