#!/usr/bin/env bash
# Prove that the latest TurBot backups are readable and restorable without
# touching the live databases. Output contains schema/table counts only, never
# row values or customer identifiers.
set -Eeuo pipefail
umask 077

APP_DIR="${APP_DIR:-/opt/turbot}"
BACKUP_DIR="${BACKUP_DIR:-/opt/turbot/backups}"
MAX_BACKUP_AGE_SECONDS="${MAX_BACKUP_AGE_SECONDS:-86400}"

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "ERROR: sqlite3 is required for restore verification." >&2
    exit 1
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

latest_backup() {
    local stem="$1"
    find "$BACKUP_DIR" -maxdepth 1 -type f -name "${stem}_*.sqlite" -printf '%T@ %p\n' \
        | sort -nr | sed -n '1s/^[^ ]* //p'
}

quote_identifier() {
    printf '%s' "$1" | sed 's/"/""/g'
}

drill_one() {
    local db="$1"
    local stem="${db%.sqlite}"
    local latest restored mtime now age integrity schema_count tables table quoted count

    latest="$(latest_backup "$stem")"
    if [[ -z "$latest" || ! -f "$latest" ]]; then
        echo "ERROR: no backup found for $db." >&2
        return 1
    fi

    now="$(date +%s)"
    mtime="$(stat -c %Y "$latest")"
    age=$((now - mtime))
    if (( age < 0 || age > MAX_BACKUP_AGE_SECONDS )); then
        echo "ERROR: latest $db backup is too old: age_seconds=$age." >&2
        return 1
    fi

    restored="$tmp_dir/$db"
    cp -- "$latest" "$restored"
    chmod 600 "$restored"

    integrity="$(sqlite3 "$restored" 'PRAGMA integrity_check;')"
    if [[ "$integrity" != "ok" ]]; then
        echo "ERROR: restored $db failed integrity_check." >&2
        return 1
    fi

    schema_count="$(sqlite3 "$restored" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")"
    if [[ ! "$schema_count" =~ ^[0-9]+$ || "$schema_count" -lt 1 ]]; then
        echo "ERROR: restored $db has no application tables." >&2
        return 1
    fi

    echo "Restore drill: db=$db age_seconds=$age integrity=ok tables=$schema_count"
    tables="$(sqlite3 "$restored" "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")"
    while IFS= read -r table; do
        [[ -n "$table" ]] || continue
        quoted="$(quote_identifier "$table")"
        count="$(sqlite3 "$restored" "SELECT COUNT(*) FROM \"$quoted\";")"
        echo "Restore drill count: db=$db table=$table rows=$count"
    done <<<"$tables"
}

if [[ ! -f "$APP_DIR/bot_state.sqlite" ]]; then
    echo "ERROR: live main database is missing at $APP_DIR/bot_state.sqlite." >&2
    exit 1
fi

drill_one bot_state.sqlite

if [[ -f "$APP_DIR/vk_bot_state.sqlite" ]]; then
    drill_one vk_bot_state.sqlite
else
    echo "Restore drill: db=vk_bot_state.sqlite state=not_present_optional"
fi

echo "Restore drill complete: isolated=true max_backup_age_seconds=$MAX_BACKUP_AGE_SECONDS"
