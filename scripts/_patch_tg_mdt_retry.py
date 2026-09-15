from pathlib import Path

bot_path = Path("bot.py")
source = bot_path.read_text(encoding="utf-8")

config_anchor = 'MDT_REMINDER_TEXT = os.getenv("MDT_REMINDER_TEXT", "Позвонить по заявке с Telegram-бота")\n'
assert config_anchor in source
source = source.replace(
    config_anchor,
    config_anchor
    + 'MDT_RETRY_ENABLED = os.getenv("MDT_RETRY_ENABLED", "true").lower().strip() in ("1", "true", "yes")\n'
    + 'MDT_RETRY_POLL_SECONDS = max(5, _env_int("MDT_RETRY_POLL_SECONDS", 60))\n'
    + 'MDT_RETRY_BASE_SECONDS = max(5, _env_int("MDT_RETRY_BASE_SECONDS", 60))\n'
    + 'MDT_RETRY_MAX_SECONDS = max(MDT_RETRY_BASE_SECONDS, _env_int("MDT_RETRY_MAX_SECONDS", 3600))\n'
    + 'MDT_RETRY_BATCH_SIZE = max(1, _env_int("MDT_RETRY_BATCH_SIZE", 10))\n',
    1,
)

index_anchor = '''        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_chat_id ON leads(chat_id)"
        )
'''
assert index_anchor in source
migration = '''        cur.execute("PRAGMA table_info(leads)")
        _lead_cols = {row[1] for row in cur.fetchall()}
        if "mdt_status" not in _lead_cols:
            cur.execute("ALTER TABLE leads ADD COLUMN mdt_status TEXT")
        if "mdt_attempts" not in _lead_cols:
            cur.execute("ALTER TABLE leads ADD COLUMN mdt_attempts INTEGER NOT NULL DEFAULT 0")
        if "mdt_next_retry_at" not in _lead_cols:
            cur.execute("ALTER TABLE leads ADD COLUMN mdt_next_retry_at INTEGER")
        if "mdt_synced_at" not in _lead_cols:
            cur.execute("ALTER TABLE leads ADD COLUMN mdt_synced_at INTEGER")
        if "mdt_payload" not in _lead_cols:
            cur.execute("ALTER TABLE leads ADD COLUMN mdt_payload TEXT")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_mdt_retry ON leads(mdt_status, mdt_next_retry_at)"
        )
'''
source = source.replace(index_anchor, migration + index_anchor, 1)

send_anchor = "def send_lead_to_mdt(\n"
assert send_anchor in source
source = source.replace(send_anchor, "def _send_lead_to_mdt_once(\n", 1)
start = source.index("def _send_lead_to_mdt_once(")
end = source.index("\ndef ", start + 1)
helpers = '''

_mdt_delivery_lock = threading.Lock()


def _mdt_retry_delay(attempts: int) -> int:
    exponent = max(0, min(int(attempts) - 1, 16))
    return min(MDT_RETRY_MAX_SECONDS, MDT_RETRY_BASE_SECONDS * (2 ** exponent))


def _queue_mdt_lead(
    lead_id: int,
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> None:
    if not (MDT_RETRY_ENABLED and MDT_ENABLED and MDT_MODE == "lead" and not DEMO_MODE):
        return
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "info": dict(info),
            "phone": phone,
            "client_name": client_name,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    now = int(time.time())
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE leads
            SET mdt_status='pending', mdt_attempts=0,
                mdt_next_retry_at=?, mdt_synced_at=NULL, mdt_payload=?
            WHERE id=?
            """,
            (now, payload, int(lead_id)),
        )


def _record_mdt_attempt(lead_id: int, ok: bool) -> None:
    now = int(time.time())
    with _db_cursor(commit=True) as cur:
        row = cur.execute(
            "SELECT mdt_attempts FROM leads WHERE id=?", (int(lead_id),)
        ).fetchone()
        attempts = int(row["mdt_attempts"] or 0) + 1 if row else 1
        if ok:
            cur.execute(
                """
                UPDATE leads
                SET mdt_status='synced', mdt_attempts=?,
                    mdt_next_retry_at=NULL, mdt_synced_at=?
                WHERE id=?
                """,
                (attempts, now, int(lead_id)),
            )
        else:
            cur.execute(
                """
                UPDATE leads
                SET mdt_status='pending', mdt_attempts=?,
                    mdt_next_retry_at=?, mdt_synced_at=NULL
                WHERE id=?
                """,
                (attempts, now + _mdt_retry_delay(attempts), int(lead_id)),
            )


def _attempt_mdt_delivery(
    lead_id: int,
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> bool:
    with _mdt_delivery_lock:
        try:
            ok = bool(_send_lead_to_mdt_once(chat_id, info, phone, client_name))
        except Exception as exc:
            logger.warning("MDT delivery failed for Telegram lead %s: %s", lead_id, exc)
            ok = False
        _record_mdt_attempt(lead_id, ok)
        return ok


def send_lead_to_mdt(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> bool:
    lead_id = info.get("_local_lead_id")
    if (
        lead_id
        and MDT_RETRY_ENABLED
        and MDT_ENABLED
        and MDT_MODE == "lead"
        and not DEMO_MODE
    ):
        return _attempt_mdt_delivery(int(lead_id), chat_id, info, phone, client_name)
    return _send_lead_to_mdt_once(chat_id, info, phone, client_name)


def _deliver_mdt_lead(lead_id: int) -> bool:
    with _db_cursor() as cur:
        row = cur.execute(
            "SELECT mdt_status, mdt_payload FROM leads WHERE id=?", (int(lead_id),)
        ).fetchone()
    if not row or row["mdt_status"] == "synced" or not row["mdt_payload"]:
        return True
    try:
        payload = json.loads(row["mdt_payload"])
        info = dict(payload["info"])
        info["_local_lead_id"] = int(lead_id)
        info["_mdt_delivery_key"] = f"tg-lead-{int(lead_id)}"
        return _attempt_mdt_delivery(
            int(lead_id),
            int(payload["chat_id"]),
            info,
            str(payload["phone"]),
            payload.get("client_name"),
        )
    except Exception as exc:
        logger.warning("Invalid MDT retry payload for Telegram lead %s: %s", lead_id, exc)
        _record_mdt_attempt(int(lead_id), False)
        return False


def _retry_pending_mdt_once(now: Optional[int] = None) -> int:
    if not (MDT_RETRY_ENABLED and MDT_ENABLED and MDT_MODE == "lead" and not DEMO_MODE):
        return 0
    current = int(time.time()) if now is None else int(now)
    with _db_cursor() as cur:
        rows = cur.execute(
            """
            SELECT id FROM leads
            WHERE mdt_status='pending'
              AND COALESCE(mdt_next_retry_at, 0) <= ?
            ORDER BY id
            LIMIT ?
            """,
            (current, MDT_RETRY_BATCH_SIZE),
        ).fetchall()
    synced = 0
    for row in rows:
        if _deliver_mdt_lead(int(row["id"])):
            synced += 1
    return synced


def _start_mdt_retry_worker() -> None:
    if not (MDT_RETRY_ENABLED and MDT_ENABLED and MDT_MODE == "lead" and not DEMO_MODE):
        return

    def _worker() -> None:
        while True:
            try:
                _retry_pending_mdt_once()
            except Exception:
                logger.exception("Telegram MDT retry worker failed")
            time.sleep(MDT_RETRY_POLL_SECONDS)

    threading.Thread(target=_worker, daemon=True, name="mdt-retry").start()
'''
source = source[:end] + helpers + source[end:]

delivery_anchor = '''    delivery_info = dict(info)
    delivery_info["_mdt_delivery_key"] = f"tg-lead-{lead_id}"
'''
assert delivery_anchor in source
source = source.replace(
    delivery_anchor,
    delivery_anchor
    + '    delivery_info["_local_lead_id"] = lead_id\n'
    + '    _queue_mdt_lead(lead_id, chat_id, delivery_info, phone, client_name)\n',
    1,
)

worker_anchor = "_start_timeout_worker()\n_start_followup_worker()\n_start_retention_worker()"
assert worker_anchor in source
source = source.replace(worker_anchor, worker_anchor + "\n_start_mdt_retry_worker()", 1)

bot_path.write_text(source, encoding="utf-8")

env_path = Path(".env.example")
env = env_path.read_text(encoding="utf-8")
env_anchor = 'MDT_REMINDER_TEXT="Позвонить по заявке с Telegram-бота"\n'
assert env_anchor in env
env = env.replace(
    env_anchor,
    env_anchor
    + "\n# Telegram durable MDT delivery for MDT_MODE=lead.\n"
    + "# Failed CRM writes stay in SQLite and retry with exponential backoff.\n"
    + "# MDT_RETRY_ENABLED=true\n"
    + "# MDT_RETRY_POLL_SECONDS=60\n"
    + "# MDT_RETRY_BASE_SECONDS=60\n"
    + "# MDT_RETRY_MAX_SECONDS=3600\n"
    + "# MDT_RETRY_BATCH_SIZE=10\n",
    1,
)
env_path.write_text(env, encoding="utf-8")

test_path = Path("tests/test_bot.py")
tests = test_path.read_text(encoding="utf-8")
if "test_telegram_mdt_retry_persists_and_recovers" not in tests:
    tests += '''


def test_telegram_mdt_retry_persists_and_recovers(monkeypatch):
    monkeypatch.setattr(bot, "MDT_ENABLED", True)
    monkeypatch.setattr(bot, "MDT_MODE", "lead")
    monkeypatch.setattr(bot, "MDT_RETRY_ENABLED", True)
    monkeypatch.setattr(bot, "DEMO_MODE", False)
    monkeypatch.setattr(bot, "MDT_RETRY_BASE_SECONDS", 5)
    monkeypatch.setattr(bot, "MDT_RETRY_MAX_SECONDS", 60)

    info = {
        "destination": "Шри-Ланка",
        "origin": "Москва",
        "dates": "2026-10-16",
        "nights": "10",
        "people": "2",
        "kids": 0,
        "budget": 270000,
        "budget_scope": "per_person",
        "direct_only": True,
        "_mdt_delivery_key": "tg-lead-placeholder",
    }
    lead_id = bot.save_lead(
        7401, info, "Telegram @retry", first_name="Roman", username="retry"
    )
    info["_local_lead_id"] = lead_id
    info["_mdt_delivery_key"] = f"tg-lead-{lead_id}"
    bot._queue_mdt_lead(lead_id, 7401, info, "Telegram @retry", "Roman")

    calls = []
    monkeypatch.setattr(
        bot,
        "_send_lead_to_mdt_once",
        lambda chat_id, payload, phone, client_name: calls.append(
            (chat_id, dict(payload), phone, client_name)
        ) or False,
    )
    assert bot._deliver_mdt_lead(lead_id) is False
    with bot._db_cursor() as cur:
        row = cur.execute(
            "SELECT mdt_status, mdt_attempts, mdt_next_retry_at FROM leads WHERE id=?",
            (lead_id,),
        ).fetchone()
    assert row["mdt_status"] == "pending"
    assert row["mdt_attempts"] == 1
    assert row["mdt_next_retry_at"] is not None
    assert calls[-1][1]["_mdt_delivery_key"] == f"tg-lead-{lead_id}"

    monkeypatch.setattr(
        bot,
        "_send_lead_to_mdt_once",
        lambda chat_id, payload, phone, client_name: calls.append(
            (chat_id, dict(payload), phone, client_name)
        ) or True,
    )
    assert bot._deliver_mdt_lead(lead_id) is True
    with bot._db_cursor() as cur:
        row = cur.execute(
            "SELECT mdt_status, mdt_attempts, mdt_next_retry_at, mdt_synced_at FROM leads WHERE id=?",
            (lead_id,),
        ).fetchone()
    assert row["mdt_status"] == "synced"
    assert row["mdt_attempts"] == 2
    assert row["mdt_next_retry_at"] is None
    assert row["mdt_synced_at"] is not None
    assert calls[-1][1]["_mdt_delivery_key"] == f"tg-lead-{lead_id}"


def test_telegram_mdt_retry_does_not_run_for_preorder(monkeypatch):
    monkeypatch.setattr(bot, "MDT_ENABLED", True)
    monkeypatch.setattr(bot, "MDT_MODE", "preorder")
    monkeypatch.setattr(bot, "MDT_RETRY_ENABLED", True)
    monkeypatch.setattr(bot, "DEMO_MODE", False)
    assert bot._retry_pending_mdt_once(now=123456) == 0
'''
    test_path.write_text(tests, encoding="utf-8")
