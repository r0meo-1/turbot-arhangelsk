from pathlib import Path

bot_path = Path("bot.py")
source = bot_path.read_text(encoding="utf-8")

cfg_marker = 'MDT_RETRY_BATCH_SIZE = max(1, _env_int("MDT_RETRY_BATCH_SIZE", 10))\n'
assert cfg_marker in source
source = source.replace(
    cfg_marker,
    cfg_marker + 'MDT_RETRY_ALERT_AFTER_SECONDS = max(0, _env_int("MDT_RETRY_ALERT_AFTER_SECONDS", 7200))\n',
    1,
)

old_alert_sig = 'def _alert_admin_error(error_msg: str, exc: Optional[Exception] = None) -> None:\n'
assert old_alert_sig in source
source = source.replace(
    old_alert_sig,
    'def _alert_admin_error(\n'
    '    error_msg: str,\n'
    '    exc: Optional[Exception] = None,\n'
    '    *,\n'
    '    alert_key: Optional[str] = None,\n'
    ') -> None:\n',
    1,
)
old_key = '    key = error_msg[:100]\n'
assert old_key in source
source = source.replace(old_key, '    key = (alert_key or error_msg)[:100]\n', 1)

worker_marker = 'def _start_mdt_retry_worker() -> None:\n'
assert worker_marker in source
assert 'def _alert_stale_mdt_retry_queue(' not in source
helper = '''def _alert_stale_mdt_retry_queue(now: Optional[float] = None) -> bool:
    """Alert the admin when the Telegram MDT retry queue is persistently stale."""
    if MDT_RETRY_ALERT_AFTER_SECONDS <= 0:
        return False
    stats = _mdt_retry_health(now)
    age = stats.get("oldest_pending_seconds")
    if (
        not stats.get("enabled")
        or not stats.get("available")
        or not stats.get("pending")
        or age is None
        or int(age) < MDT_RETRY_ALERT_AFTER_SECONDS
    ):
        return False

    message = (
        "MDT retry queue stale: "
        f"pending={int(stats.get('pending') or 0)}, "
        f"due_now={int(stats.get('due_now') or 0)}, "
        f"max_attempts={int(stats.get('max_attempts') or 0)}, "
        f"oldest={int(age)}s"
    )
    _alert_admin_error(message, alert_key="mdt_retry_queue_stale")
    return True


'''
source = source.replace(worker_marker, helper + worker_marker, 1)

old_worker = '''            try:
                _retry_pending_mdt_once()
            except Exception:
                logger.exception("Telegram MDT retry worker failed")
            time.sleep(MDT_RETRY_POLL_SECONDS)
'''
assert old_worker in source
new_worker = '''            try:
                _retry_pending_mdt_once()
                _alert_stale_mdt_retry_queue()
            except Exception:
                logger.exception("Telegram MDT retry worker failed")
            time.sleep(MDT_RETRY_POLL_SECONDS)
'''
source = source.replace(old_worker, new_worker, 1)
bot_path.write_text(source, encoding="utf-8")

env_path = Path('.env.example')
env = env_path.read_text(encoding='utf-8')
env_marker = '# MDT_RETRY_BATCH_SIZE=10\n'
assert env_marker in env
env = env.replace(
    env_marker,
    env_marker
    + '# Alert admin only when the oldest pending Telegram MDT lead is this old; 0 disables.\n'
    + '# MDT_RETRY_ALERT_AFTER_SECONDS=7200\n',
    1,
)
env_path.write_text(env, encoding='utf-8')

test_path = Path('tests/test_bot.py')
tests = test_path.read_text(encoding='utf-8')
name = 'test_mdt_retry_stale_alert_is_pii_free_and_uses_stable_key'
if name not in tests:
    tests += r'''


def test_mdt_retry_stale_alert_is_pii_free_and_uses_stable_key(monkeypatch):
    monkeypatch.setattr(bot, "MDT_ENABLED", True)
    monkeypatch.setattr(bot, "MDT_MODE", "lead")
    monkeypatch.setattr(bot, "MDT_RETRY_ENABLED", True)
    monkeypatch.setattr(bot, "DEMO_MODE", False)
    monkeypatch.setattr(bot, "MDT_RETRY_ALERT_AFTER_SECONDS", 60)

    now = int(time.time())
    secret_phone = "+79990001122"
    lead_id = bot.save_lead(
        7901,
        {"destination": "Вьетнам", "people": "2", "budget": 250000},
        secret_phone,
        first_name="Secret",
        username="secret_user",
    )
    with bot._db_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE leads
            SET mdt_status='pending', mdt_attempts=4,
                mdt_next_retry_at=?, created_at=?
            WHERE id=?
            """,
            (now - 10, now - 120, lead_id),
        )

    alerts = []
    monkeypatch.setattr(
        bot,
        "_alert_admin_error",
        lambda message, exc=None, *, alert_key=None: alerts.append(
            (message, exc, alert_key)
        ),
    )

    assert bot._alert_stale_mdt_retry_queue(now=now) is True
    assert len(alerts) == 1
    message, exc, alert_key = alerts[0]
    assert alert_key == "mdt_retry_queue_stale"
    assert exc is None
    assert "pending=1" in message
    assert "due_now=1" in message
    assert "max_attempts=4" in message
    assert "oldest=120s" in message
    assert secret_phone not in message
    assert "Secret" not in message
    assert "secret_user" not in message


def test_mdt_retry_stale_alert_ignores_fresh_queue(monkeypatch):
    monkeypatch.setattr(bot, "MDT_ENABLED", True)
    monkeypatch.setattr(bot, "MDT_MODE", "lead")
    monkeypatch.setattr(bot, "MDT_RETRY_ENABLED", True)
    monkeypatch.setattr(bot, "DEMO_MODE", False)
    monkeypatch.setattr(bot, "MDT_RETRY_ALERT_AFTER_SECONDS", 300)

    now = int(time.time())
    lead_id = bot.save_lead(
        7902,
        {"destination": "Таиланд", "people": "2", "budget": 250000},
        "Telegram @fresh",
    )
    with bot._db_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE leads
            SET mdt_status='pending', mdt_attempts=1,
                mdt_next_retry_at=?, created_at=?
            WHERE id=?
            """,
            (now + 30, now - 60, lead_id),
        )

    alerts = []
    monkeypatch.setattr(
        bot,
        "_alert_admin_error",
        lambda *args, **kwargs: alerts.append((args, kwargs)),
    )

    assert bot._alert_stale_mdt_retry_queue(now=now) is False
    assert alerts == []


def test_mdt_retry_stale_alert_can_be_disabled(monkeypatch):
    monkeypatch.setattr(bot, "MDT_RETRY_ALERT_AFTER_SECONDS", 0)
    monkeypatch.setattr(
        bot,
        "_mdt_retry_health",
        lambda now=None: (_ for _ in ()).throw(AssertionError("health should not be read")),
    )
    assert bot._alert_stale_mdt_retry_queue(now=123) is False
'''

test_path.write_text(tests, encoding='utf-8')
