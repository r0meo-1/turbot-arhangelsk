from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from shared import vk_followup


ROOT = Path(__file__).resolve().parents[1]


class FakeBot:
    FOLLOWUP_DELAY_HOURS = 3
    DIALOG_TIMEOUT_HOURS = 6

    def __init__(self, db_path: Path, sent: list[int] | None = None) -> None:
        self.DATABASE_PATH = str(db_path)
        self._db_lock = threading.Lock()
        self._lock = threading.Lock()
        self.user_data: dict[int, dict] = {}
        self.sent = sent if sent is not None else []
        self.fail_send = False
        self.logger = SimpleNamespace(info=lambda *args, **kwargs: None)

    @contextmanager
    def _db_cursor(self, commit: bool = False):
        conn = sqlite3.connect(self.DATABASE_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with self._db_lock:
                cur = conn.cursor()
                yield cur
                if commit:
                    conn.commit()
        finally:
            conn.close()

    def send_message(self, chat_id: int, text: str):
        if self.fail_send:
            return None
        self.sent.append(chat_id)
        return {"message_id": len(self.sent)}


def _create_sessions(bot: FakeBot) -> None:
    with bot._db_cursor(commit=True) as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                chat_id INTEGER PRIMARY KEY,
                destination TEXT,
                updated_at INTEGER NOT NULL
            )
            """
        )


def _insert_eligible(bot: FakeBot, chat_id: int = 101) -> None:
    now = int(time.time())
    with bot._db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT OR REPLACE INTO sessions(chat_id, destination, updated_at) VALUES (?, ?, ?)",
            (chat_id, "Таиланд", now - 4 * 3600),
        )


def test_followup_is_durable_across_calls_and_process_restart(tmp_path):
    db_path = tmp_path / "vk.sqlite"
    sent: list[int] = []
    first = FakeBot(db_path, sent)
    _create_sessions(first)
    vk_followup.ensure_schema(first)
    _insert_eligible(first)

    assert vk_followup.send_followups(first) == 1
    assert vk_followup.send_followups(first) == 0

    # A fresh bot object simulates a new process after a deploy/restart. The
    # SQLite claim, unlike the old user_data flag, survives it.
    restarted = FakeBot(db_path, sent)
    assert vk_followup.send_followups(restarted) == 0
    assert sent == [101]


def test_known_vk_send_failure_releases_claim_for_retry(tmp_path):
    bot = FakeBot(tmp_path / "vk.sqlite")
    _create_sessions(bot)
    vk_followup.ensure_schema(bot)
    _insert_eligible(bot)

    bot.fail_send = True
    assert vk_followup.send_followups(bot) == 0
    with bot._db_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM vk_followup_claims").fetchone()[0] == 0

    bot.fail_send = False
    assert vk_followup.send_followups(bot) == 1
    assert bot.sent == [101]


def test_atomic_claim_allows_only_one_sender(tmp_path):
    db_path = tmp_path / "vk.sqlite"
    sent: list[int] = []
    one = FakeBot(db_path, sent)
    two = FakeBot(db_path, sent)
    _create_sessions(one)
    vk_followup.ensure_schema(one)
    _insert_eligible(one)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda bot: vk_followup.send_followups(bot), (one, two)))

    assert sum(results) == 1
    assert sent == [101]


def test_first_rollout_seeds_old_eligible_sessions_instead_of_resending(tmp_path):
    bot = FakeBot(tmp_path / "vk.sqlite")
    _create_sessions(bot)
    _insert_eligible(bot)

    vk_followup.ensure_schema(bot, seed_existing=True)

    assert vk_followup.send_followups(bot) == 0
    assert bot.sent == []
    with bot._db_cursor() as cur:
        row = cur.execute(
            "SELECT sent_at FROM vk_followup_claims WHERE chat_id = 101"
        ).fetchone()
    assert row is not None and row[0] is not None


def test_reset_claim_allows_exactly_one_reminder_for_new_dialog(tmp_path):
    bot = FakeBot(tmp_path / "vk.sqlite")
    _create_sessions(bot)
    vk_followup.ensure_schema(bot)
    _insert_eligible(bot)

    assert vk_followup.send_followups(bot) == 1
    vk_followup.reset_claim(bot, 101)
    assert vk_followup.send_followups(bot) == 1
    assert vk_followup.send_followups(bot) == 0
    assert bot.sent == [101, 101]


def test_production_entrypoints_use_guarded_vk_runtime():
    unit = (ROOT / "deploy" / "vk-turbot.service").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    deploy = (ROOT / "deploy" / "turbot-deploy.sh").read_text(encoding="utf-8")

    assert "shared.vk_runtime:app" in unit
    assert "gunicorn shared.vk_runtime:app" in compose
    assert "install_systemd_units() {" in deploy
    assert '"$repo/deploy/turbot.service"' in deploy
    assert '"$repo/deploy/vk-turbot.service"' in deploy
    assert "/etc/systemd/system/turbot.service" in deploy
    assert "/etc/systemd/system/vk-turbot.service" in deploy
