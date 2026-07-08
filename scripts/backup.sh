#!/usr/bin/env bash
#
# TurBot — daily SQLite backup script.
# Keeps the last 7 backup copies. Run via cron:
#
#   0 3 * * * /opt/turbot/scripts/backup.sh
#
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/turbot}"
BACKUP_DIR="${BACKUP_DIR:-/opt/turbot/backups}"
KEEP_DAYS="${KEEP_DAYS:-7}"

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)

for db in bot_state.sqlite vk_bot_state.sqlite; do
    DB_PATH="$APP_DIR/$db"
    if [ -f "$DB_PATH" ]; then
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

# Remove backups older than KEEP_DAYS days
echo "Cleaning backups older than $KEEP_DAYS days..."
find "$BACKUP_DIR" -name "*_*.sqlite" -mtime +"$KEEP_DAYS" -delete

echo "Done. Backups:"
ls -lh "$BACKUP_DIR"/ 2>/dev/null | tail -10
