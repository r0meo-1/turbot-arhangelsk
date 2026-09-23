import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
from contextlib import closing


ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "scripts" / "backup.sh"
RESTORE = ROOT / "scripts" / "restore-drill.sh"
OFFSITE = ROOT / "scripts" / "offsite-backup.sh"


def _seed(path: Path, table: str, secret: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, value TEXT)')
        connection.execute(f'INSERT INTO "{table}" (value) VALUES (?)', (secret,))
        connection.commit()
    finally:
        connection.close()


def test_backup_requires_sqlite_online_backup_and_integrity_check():
    text = BACKUP.read_text(encoding="utf-8")
    assert '".backup ' in text
    assert "PRAGMA integrity_check" in text
    assert "command -v sqlite3" in text
    assert 'cp "$DB_PATH"' not in text
    assert "safe online backup" in text


def test_backup_and_restore_drill_are_isolated_and_do_not_print_row_values(tmp_path):
    assert shutil.which("sqlite3"), "sqlite3 CLI must exist in CI and production"

    app_dir = tmp_path / "app"
    backup_dir = tmp_path / "backups"
    app_dir.mkdir()

    secret = "PII_TEST_PHONE_+79990001122"
    _seed(app_dir / "bot_state.sqlite", "leads", secret)
    _seed(app_dir / "vk_bot_state.sqlite", "vk_sessions", "VK_SECRET_TEST_VALUE")

    env = os.environ.copy()
    env.update(
        APP_DIR=str(app_dir),
        BACKUP_DIR=str(backup_dir),
        KEEP_DAYS="7",
        MAX_BACKUP_AGE_SECONDS="86400",
    )

    backup = subprocess.run(
        ["bash", str(BACKUP)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "integrity=ok" in backup.stdout
    copies = sorted(backup_dir.glob("*.sqlite"))
    assert len(copies) == 2
    for copy in copies:
        assert stat.S_IMODE(copy.stat().st_mode) == 0o600

    # A long backup history must not trip `set -o pipefail`. The former
    # `sort | head -n 1` selector closed stdout early and made sort exit with
    # SIGPIPE (141) once its output exceeded the pipe buffer.
    main_copy = next(path for path in copies if path.name.startswith("bot_state_"))
    for index in range(900):
        shutil.copy2(main_copy, backup_dir / f"bot_state_history_{index:04d}.sqlite")

    restore = subprocess.run(
        ["bash", str(RESTORE)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    output = restore.stdout + restore.stderr
    assert "Restore drill complete: isolated=true" in output
    assert "db=bot_state.sqlite" in output
    assert "db=vk_bot_state.sqlite" in output
    assert "rows=1" in output
    assert secret not in output
    assert "VK_SECRET_TEST_VALUE" not in output

    # The drill must never mutate or replace the live files.
    with closing(sqlite3.connect(app_dir / "bot_state.sqlite")) as connection, connection:
        assert connection.execute("SELECT value FROM leads").fetchone()[0] == secret


def test_production_deploy_exposes_only_restricted_backup_drill_marker():
    deployer = (ROOT / "deploy" / "turbot-deploy.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "deploy-bundle.yml").read_text(encoding="utf-8")
    marker = "TURBOT_BACKUP_DRILL_V1"
    assert marker in deployer
    assert marker in workflow
    assert "Unexpected backup drill marker payload" in deployer
    assert "ensure_backup_and_restore_drill" in deployer
    assert "Verify production backup and isolated restore drill" in workflow


def test_offsite_backup_is_disabled_by_default_and_keeps_private_key_off_server(tmp_path):
    env = os.environ.copy()
    env["OFFSITE_BACKUP_CONFIG_FILE"] = str(tmp_path / "missing.env")
    result = subprocess.run(
        ["bash", str(OFFSITE)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "state=disabled reason=config_missing" in result.stdout

    text = OFFSITE.read_text(encoding="utf-8")
    assert "OFFSITE_AGE_RECIPIENT" in text
    assert "age -r" in text
    assert "age --decrypt" not in text
    assert "OFFSITE_S3_ENDPOINT_URL" in text
    assert "https://*" in text
    assert "AWS_SECRET_ACCESS_KEY" in text
    assert "OFFSITE_AGE_IDENTITY" not in text


def test_offsite_backup_encrypts_before_s3_upload_and_verifies_remote_key(tmp_path):
    app_dir = tmp_path / "app"
    backup_dir = tmp_path / "backups"
    fake_bin = tmp_path / "bin"
    config = tmp_path / "offsite.env"
    app_dir.mkdir()
    backup_dir.mkdir()
    fake_bin.mkdir()

    main = backup_dir / "bot_state_20300101_030000.sqlite"
    vk = backup_dir / "vk_bot_state_20300101_030000.sqlite"
    main.write_bytes(b"main-backup-test-data")
    vk.write_bytes(b"vk-backup-test-data")

    config.write_text(
        "\n".join(
            [
                "OFFSITE_BACKUP_ENABLED=true",
                "OFFSITE_S3_ENDPOINT_URL=https://s3.example.invalid",
                "OFFSITE_S3_REGION=ru-test",
                "OFFSITE_S3_BUCKET=turbot-backups",
                "OFFSITE_S3_PREFIX=turbot",
                "OFFSITE_AGE_RECIPIENT=age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
                "AWS_ACCESS_KEY_ID=test-access",
                "AWS_SECRET_ACCESS_KEY=test-secret",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config.chmod(0o600)

    (fake_bin / "stat").write_text(
        """#!/usr/bin/env bash
if [[ "$1" == "-c" && "$2" == "%u" && "$3" == "$OFFSITE_BACKUP_CONFIG_FILE" ]]; then
  echo 0
elif [[ "$1" == "-c" && "$2" == "%a" && "$3" == "$OFFSITE_BACKUP_CONFIG_FILE" ]]; then
  echo 600
else
  exec /usr/bin/stat "$@"
fi
""",
        encoding="utf-8",
    )
    (fake_bin / "age").write_text(
        """#!/usr/bin/env bash
out=""
input=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -r) shift 2 ;;
    -o) out="$2"; shift 2 ;;
    *) input="$1"; shift ;;
  esac
done
printf 'AGE-TEST\\n' > "$out"
cat "$input" >> "$out"
""",
        encoding="utf-8",
    )
    (fake_bin / "aws").write_text(
        """#!/usr/bin/env bash
args="$*"
if [[ "$args" == *" s3 cp "* ]]; then
  printf '%s\\n' "$args" >> "$AWS_LOG"
  exit 0
fi
if [[ "$args" == *" s3api list-objects-v2 "* ]]; then
  prev=""
  for arg in "$@"; do
    if [[ "$prev" == "--prefix" ]]; then
      printf '%s\\n' "$arg"
      exit 0
    fi
    prev="$arg"
  done
fi
exit 2
""",
        encoding="utf-8",
    )
    for path in (fake_bin / "stat", fake_bin / "age", fake_bin / "aws"):
        path.chmod(0o755)

    aws_log = tmp_path / "aws.log"
    env = os.environ.copy()
    env.update(
        APP_DIR=str(app_dir),
        BACKUP_DIR=str(backup_dir),
        OFFSITE_BACKUP_CONFIG_FILE=str(config),
        MAX_BACKUP_AGE_SECONDS=str(10**10),
        PATH=f"{fake_bin}:{env['PATH']}",
        AWS_LOG=str(aws_log),
    )

    result = subprocess.run(
        ["bash", str(OFFSITE)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Off-site backup complete: encrypted=true files=2" in result.stdout
    upload = aws_log.read_text(encoding="utf-8")
    assert ".tar.gz.age" in upload
    assert ".sqlite" not in upload
    assert "s3://turbot-backups/turbot/" in upload


def test_local_backup_chains_offsite_helper_only_after_verified_backup():
    text = BACKUP.read_text(encoding="utf-8")
    assert 'if [[ -x "$APP_DIR/scripts/offsite-backup.sh" ]]' in text
    assert text.index('echo "Backup complete: copies=$BACKED_UP') < text.index(
        '"$APP_DIR/scripts/offsite-backup.sh"'
    )

    deployer = (ROOT / "deploy" / "turbot-deploy.sh").read_text(encoding="utf-8")
    assert '"$repo/scripts/offsite-backup.sh"' in deployer
