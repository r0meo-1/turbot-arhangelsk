from pathlib import Path

bot_path = Path("bot.py")
source = bot_path.read_text(encoding="utf-8")

worker_marker = "def _start_mdt_retry_worker() -> None:\n"
assert worker_marker in source
assert "def _mdt_retry_health(" not in source

helper = '''def _mdt_retry_health(now: Optional[float] = None) -> Dict[str, Any]:
    """Return aggregate retry-queue telemetry without exposing lead PII."""
    current = int(time.time() if now is None else now)
    enabled = bool(
        MDT_RETRY_ENABLED
        and MDT_ENABLED
        and MDT_MODE == "lead"
        and not DEMO_MODE
    )
    try:
        with _db_cursor() as cur:
            row = cur.execute(
                """
                SELECT
                    COUNT(*) AS pending,
                    COALESCE(MAX(mdt_attempts), 0) AS max_attempts,
                    MIN(created_at) AS oldest_created_at,
                    MIN(mdt_next_retry_at) AS next_retry_at,
                    SUM(
                        CASE
                            WHEN COALESCE(mdt_next_retry_at, 0) <= ? THEN 1
                            ELSE 0
                        END
                    ) AS due_now
                FROM leads
                WHERE mdt_status='pending'
                """,
                (current,),
            ).fetchone()
    except sqlite3.Error as exc:
        logger.warning("Health could not read Telegram MDT retry queue: %s", exc)
        return {
            "enabled": enabled,
            "available": False,
            "pending": None,
            "due_now": None,
            "max_attempts": None,
            "oldest_pending_seconds": None,
            "next_retry_in_seconds": None,
        }

    pending = int(row["pending"] or 0) if row else 0
    oldest = row["oldest_created_at"] if row else None
    next_retry = row["next_retry_at"] if row else None
    return {
        "enabled": enabled,
        "available": True,
        "pending": pending,
        "due_now": int(row["due_now"] or 0) if row else 0,
        "max_attempts": int(row["max_attempts"] or 0) if row else 0,
        "oldest_pending_seconds": (
            max(0, current - int(oldest)) if pending and oldest is not None else None
        ),
        "next_retry_in_seconds": (
            max(0, int(next_retry) - current)
            if pending and next_retry is not None
            else None
        ),
    }


'''
source = source.replace(worker_marker, helper + worker_marker, 1)

health_marker = '''        "bot_mode": BOT_MODE,
    })'''
assert health_marker in source
source = source.replace(
    health_marker,
    '''        "bot_mode": BOT_MODE,
        "mdt_retry": _mdt_retry_health(now),
    })''',
    1,
)
bot_path.write_text(source, encoding="utf-8")

test_path = Path("tests/test_bot.py")
tests = test_path.read_text(encoding="utf-8")
old_test = '''def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["revision"]
    assert "bot_token_configured" not in data
'''
assert old_test in tests

new_test = '''def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["revision"]
    assert "bot_token_configured" not in data
    assert data["mdt_retry"]["available"] is True
    assert data["mdt_retry"]["pending"] == 0


def test_health_reports_mdt_retry_queue_without_pii(client, monkeypatch):
    monkeypatch.setattr(bot, "MDT_ENABLED", True)
    monkeypatch.setattr(bot, "MDT_MODE", "lead")
    monkeypatch.setattr(bot, "MDT_RETRY_ENABLED", True)
    monkeypatch.setattr(bot, "DEMO_MODE", False)

    now = int(time.time())
    info = {
        "destination": "Шри-Ланка",
        "origin": "Москва",
        "dates": "2026-10-16",
        "people": "2",
        "kids": 0,
        "budget": 270000,
    }
    secret_phone = "+79991234567"
    lead_id = bot.save_lead(
        7801, info, secret_phone, first_name="Roman", username="health"
    )
    with bot._db_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE leads
            SET mdt_status='pending', mdt_attempts=3,
                mdt_next_retry_at=?, created_at=?
            WHERE id=?
            """,
            (now + 45, now - 120, lead_id),
        )

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    retry = data["mdt_retry"]
    assert retry["enabled"] is True
    assert retry["available"] is True
    assert retry["pending"] == 1
    assert retry["due_now"] == 0
    assert retry["max_attempts"] == 3
    assert 119 <= retry["oldest_pending_seconds"] <= 121
    assert 43 <= retry["next_retry_in_seconds"] <= 45
    assert secret_phone not in resp.get_data(as_text=True)
'''
tests = tests.replace(old_test, new_test, 1)
test_path.write_text(tests, encoding="utf-8")
