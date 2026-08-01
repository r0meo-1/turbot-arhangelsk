#!/usr/bin/env bash
#
# TurBot — daily SQLite backup script.
# Keeps the last 7 backup copies. Run via cron:
#
#   0 3 * * * /opt/turbot/scripts/backup.sh
#
# Defaults target the VPS/systemd layout. Docker Compose keeps the database on
# the bot-data volume, so point APP_DIR at it there:
#
#   docker compose exec telegram-bot sh -c \
#     'APP_DIR=/app/data BACKUP_DIR=/app/data/backups scripts/backup.sh'
#
# Exits non-zero when nothing was backed up: a silent no-op is worse than a
# failure, because it looks exactly like a backup that works.
#
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/turbot}"
BACKUP_DIR="${BACKUP_DIR:-/opt/turbot/backups}"
KEEP_DAYS="${KEEP_DAYS:-7}"

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)

BACKED_UP=0

for db in bot_state.sqlite vk_bot_state.sqlite; do
    DB_PATH="$APP_DIR/$db"
    if [ -f "$DB_PATH" ]; then
        BACKED_UP=$((BACKED_UP + 1))
        echo "Backing up $db..."
        # Use SQLite .backup (safe online backup) if sqlite3 is available
        if command -v sqlite3 &>/dev/null; then
            sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/${db%.sqlite}_$TIMESTAMP.sqlite'"
        else
            cp "$DB_PATH" "$BACKUP_DIR/${db%.sqlite}_$TIMESTAMP.sqlite"
        fi
        echo "  → $BACKUP_DIR/${db%.sqlite}_$TIMESTAMP.sqlite"
    fi
done

# Only the VK database is genuinely optional. Zero backups means APP_DIR is
# wrong (the usual cause: running the VPS defaults against a Docker deploy).
if [ "$BACKED_UP" -eq 0 ]; then
    echo "ERROR: no database found under $APP_DIR — nothing was backed up." >&2
    echo "       Docker/Compose stores it at /app/data; pass APP_DIR=/app/data." >&2
    exit 1
fi

# Remove backups older than KEEP_DAYS days
echo "Cleaning backups older than $KEEP_DAYS days..."
find "$BACKUP_DIR" -name "*_*.sqlite" -mtime +"$KEEP_DAYS" -delete

echo "Done. Backups:"
ls -lh "$BACKUP_DIR"/ 2>/dev/null | tail -10
