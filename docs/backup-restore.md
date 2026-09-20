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

### Prepared architecture

`scripts/offsite-backup.sh` is disabled by default and is called only after the local SQLite online backup and integrity checks succeed.

When explicitly enabled, it:

1. reads a separate root-owned `/etc/turbot/offsite-backup.env` file;
2. refuses a config file that is not owned by root or is group/world accessible;
3. requires the newest verified local backup to remain inside the configured RPO window;
4. packages the main backup and optional VK backup into one temporary archive;
5. encrypts the archive locally with an **age X25519 public recipient**;
6. deletes the plaintext temporary archive;
7. uploads only the `.age` ciphertext to the approved S3-compatible bucket over HTTPS;
8. verifies the exact remote object through a bounded bucket listing;
9. prints only ciphertext metadata (file count, SHA-256, object key), never customer rows.

The production VPS should contain only the public age recipient. Keep the corresponding private age identity outside the VPS/failure domain. This means a compromise of the production host plus S3 upload credentials is not, by itself, sufficient to decrypt historical backups.

### Provider/compliance gate

As of 2026-09-20, provider documentation reviewed for this design includes:

- Selectel S3: documentation states S3 is suitable for processing personal data under 152-FZ, with access-control responsibility shared with the customer and a separate personal-data processing order available through support.
- Yandex Object Storage: the Russia region is geographically separated from the Kazakhstan region, data remains in the selected region, and Object Storage supports KMS/server-side encryption plus documented client-side encryption patterns.

This repository does not make the legal/provider decision automatically. Before enabling upload, record the selected provider/account/region, execute the required processing agreement/order if applicable, and confirm the agency's personal-data documentation covers the backup processor and retention.

### Production setup after approval

Install the two runtime tools on the VPS:

```bash
sudo apt-get update
sudo apt-get install -y age awscli
```

Generate the decryption identity **off the VPS** on a trusted admin/recovery machine:

```bash
age-keygen -o turbot-backup.agekey
age-keygen -y turbot-backup.agekey
```

Store `turbot-backup.agekey` outside the production VPS. Put only the printed `age1...` public recipient into the server config.

Create the protected server config from `deploy/offsite-backup.env.example`:

```bash
sudo install -d -m 700 /etc/turbot
sudo install -m 600 -o root -g root \
  /opt/turbot/deploy/offsite-backup.env.example \
  /etc/turbot/offsite-backup.env
sudoedit /etc/turbot/offsite-backup.env
```

Use a dedicated bucket principal scoped to the backup bucket/prefix. The VPS needs upload plus exact-prefix listing for verification; it does not need the age private identity. Prefer provider-side lifecycle retention and, where appropriate, versioning/object-lock controls instead of giving the production host broad delete permissions.

After configuration, run one explicit smoke:

```bash
sudo APP_DIR=/opt/turbot \
  BACKUP_DIR=/opt/turbot/backups \
  /opt/turbot/scripts/offsite-backup.sh
```

Expected output ends with `Off-site backup complete: encrypted=true ...`. Confirm the object exists from a separate administrator account/console before promoting #136.

### Off-host disaster restore

On a recovery machine with the age private identity and approved S3 read access:

1. download the selected `.tar.gz.age` object;
2. decrypt it locally:
   ```bash
   age --decrypt -i turbot-backup.agekey \
     -o turbot-backup.tar.gz turbot-backup-YYYYMMDDTHHMMSSZ.tar.gz.age
   ```
3. extract into a temporary directory;
4. run `PRAGMA integrity_check` on each SQLite file;
5. transfer the chosen verified SQLite backup to the replacement VPS through the approved administrative channel;
6. follow the **Real incident restore** procedure above;
7. record actual RPO/RTO, object timestamp and integrity result without customer PII.

Until an approved provider/account/region is configured and a real encrypted upload + independent recovery-read smoke succeeds, issue #136 remains open for the off-host requirement.
