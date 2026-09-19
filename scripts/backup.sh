#!/usr/bin/env bash
#
# TurBot — SQLite online backup.
# Production cron:
#   0 3 * * * /opt/turbot/scripts/backup.sh
#
# A live SQLite database must never fall back to a plain file copy. This
# script requires the sqlite3 CLI and uses its online .backup command.
#
set -Eeuo pipefail
umask 077

APP_DIR="${APP_DIR:-/opt/turbot}"
BACKUP_DIR="${BACKUP_DIR:-/opt/turbot/backups}"
KEEP_DAYS="${KEEP_DAYS:-7}"

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "ERROR: sqlite3 is required for a safe online backup." >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
if [[ "$(id -u)" -eq 0 ]]; then
    chown root:root "$BACKUP_DIR"
fi

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKED_UP=0

backup_one() {
    local db="$1"
    local db_path="$APP_DIR/$db"
    local dest="$BACKUP_DIR/${db%.sqlite}_$TIMESTAMP.sqlite"
    local integrity

    [[ -f "$db_path" ]] || return 0

    echo "Backing up $db with SQLite online backup..."
    sqlite3 "$db_path" ".backup '$dest'"
    chmod 600 "$dest"
    if [[ "$(id -u)" -eq 0 ]]; then
        chown root:root "$dest"
    fi

    integrity="$(sqlite3 "$dest" 'PRAGMA integrity_check;')"
    if [[ "$integrity" != "ok" ]]; then
        echo "ERROR: integrity_check failed for $db backup." >&2
        rm -f "$dest"
        return 1
    fi

    BACKED_UP=$((BACKED_UP + 1))
    echo "Backup verified: db=$db integrity=ok"
}

backup_one bot_state.sqlite
backup_one vk_bot_state.sqlite

# The VK database is optional. The main Telegram/state database is not.
if [[ ! -f "$APP_DIR/bot_state.sqlite" ]]; then
    echo "ERROR: required database $APP_DIR/bot_state.sqlite does not exist." >&2
    exit 1
fi
if [[ "$BACKED_UP" -eq 0 ]]; then
    echo "ERROR: no database was backed up from $APP_DIR." >&2
    exit 1
fi

# Normalize old copies too, because previous deploys may have changed ownership.
find "$BACKUP_DIR" -maxdepth 1 -type f -name '*_*.sqlite' -exec chmod 600 {} +
if [[ "$(id -u)" -eq 0 ]]; then
    find "$BACKUP_DIR" -maxdepth 1 -type f -name '*_*.sqlite' -exec chown root:root {} +
fi

echo "Cleaning backups older than $KEEP_DAYS days..."
find "$BACKUP_DIR" -maxdepth 1 -type f -name '*_*.sqlite' -mtime +"$KEEP_DAYS" -delete

echo "Backup complete: copies=$BACKED_UP retention_days=$KEEP_DAYS"
