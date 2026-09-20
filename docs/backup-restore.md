# TurBot backup and restore runbook

## Recovery targets

Initial launch targets:

- **RPO <= 24 hours**: nightly SQLite online backup must be no older than one day.
- **RTO <= 60 minutes**: restore the latest verified backup, validate it, restore file permissions, start services and pass health checks within one hour.

These are targets, not measured achievements, until a production drill is recorded.

## Normal production backup

Production uses `scripts/backup.sh`. It requires the `sqlite3` CLI and uses SQLite's online `.backup` command. A plain `cp` of a live database is intentionally not allowed.

Each new copy receives:

- `PRAGMA integrity_check`;
- mode `0600`;
- a timestamped filename under `/opt/turbot/backups`;
- seven-day local retention by default.

The production deployer also ensures `/etc/cron.d/turbot-backup` runs at 03:00 every day and performs one backup + isolated restore drill after a successful code bundle is staged.

## Safe restore drill

Run:

```bash
sudo APP_DIR=/opt/turbot \
  BACKUP_DIR=/opt/turbot/backups \
  MAX_BACKUP_AGE_SECONDS=86400 \
  /opt/turbot/scripts/restore-drill.sh
```

The drill copies the newest backup into a temporary directory, runs `PRAGMA integrity_check`, and prints only application table names and row counts. It never opens the live database for writing and never prints row values.

## Real incident restore

Do not overwrite a running database.

1. Record the incident start time and suspected failure in the Release & Incident Log.
2. Stop writers:
   ```bash
   sudo systemctl stop turbot
   sudo systemctl stop vk-turbot
   ```
3. Select the newest backup that is within the RPO and verify it before use:
   ```bash
   sudo /opt/turbot/scripts/restore-drill.sh
   ls -lt /opt/turbot/backups/*.sqlite
   ```
4. Preserve the failed live files for investigation without using them as the recovery source:
   ```bash
   sudo install -d -m 700 /opt/turbot/incident
   sudo cp -a /opt/turbot/bot_state.sqlite /opt/turbot/incident/bot_state.failed.sqlite
   sudo test ! -f /opt/turbot/vk_bot_state.sqlite || \
     sudo cp -a /opt/turbot/vk_bot_state.sqlite /opt/turbot/incident/vk_bot_state.failed.sqlite
   ```
5. Restore the selected **main** backup through a temporary file, verify it again, then replace the failed live file:
   ```bash
   BACKUP=/opt/turbot/backups/bot_state_YYYYMMDD_HHMMSS.sqlite
   sudo install -m 600 -o turbot -g turbot "$BACKUP" /opt/turbot/bot_state.sqlite.restore
   test "$(sudo sqlite3 /opt/turbot/bot_state.sqlite.restore 'PRAGMA integrity_check;')" = "ok"
   sudo mv -f /opt/turbot/bot_state.sqlite.restore /opt/turbot/bot_state.sqlite
   ```
   If the VK database exists, repeat the same procedure with the selected
   `vk_bot_state_*.sqlite` backup and
   `/opt/turbot/vk_bot_state.sqlite.restore`.
6. Restore ownership and permissions:
   ```bash
   sudo chown turbot:turbot /opt/turbot/bot_state.sqlite
   sudo chmod 600 /opt/turbot/bot_state.sqlite
   sudo test ! -f /opt/turbot/vk_bot_state.sqlite || \
     sudo chown turbot:turbot /opt/turbot/vk_bot_state.sqlite
   sudo test ! -f /opt/turbot/vk_bot_state.sqlite || \
     sudo chmod 600 /opt/turbot/vk_bot_state.sqlite
   ```
7. Start services and verify both health routes:
   ```bash
   sudo systemctl start turbot
   sudo systemctl start vk-turbot
   curl -fsS https://bot.r0meo1.ru/health
   curl -fsS https://bot.r0meo1.ru/vk/health
   ```
8. Record the actual RPO/RTO, restored backup timestamp, integrity result and service-health result. Do not put customer PII into the incident log.

## Off-host copy

Local backups protect against logical/database failures but not total VPS loss.

Do **not** upload production databases to an arbitrary cloud, GitHub artifact, personal drive or foreign region merely to tick the off-host checkbox. The off-host target must first have confirmed storage geography, access controls, encryption at rest/in transit, retention/deletion controls and a documented lawful processing basis for the personal data involved.

Until that provider is approved and configured, issue #136 remains open for the off-host requirement.
