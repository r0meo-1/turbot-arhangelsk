import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "scripts" / "backup.sh"
RESTORE = ROOT / "scripts" / "restore-drill.sh"


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
    with sqlite3.connect(app_dir / "bot_state.sqlite") as connection:
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
