from pathlib import Path

vk_path = Path("vk_bot.py")
source = vk_path.read_text(encoding="utf-8")

# Shared admin-alert settings: VK uses the Telegram bot only as an ops channel.
config_marker = 'ADMIN_ID          = _env_int("ADMIN_ID", 0)\n'
assert config_marker in source
assert 'ADMIN_ERROR_ALERTS =' not in source
source = source.replace(
    config_marker,
    config_marker
    + 'ADMIN_ERROR_ALERTS = os.getenv("ADMIN_ERROR_ALERTS", "true").lower().strip() in ("1", "true", "yes")\n'
    + 'ERROR_ALERT_COOLDOWN = max(0, _env_int("ERROR_ALERT_COOLDOWN", 300))\n',
    1,
)

record_marker = 'def _record_mdt_preorder_result(\n'
assert record_marker in source
assert 'def _mdt_delivery_health(' not in source

helpers = r'''_ops_alert_lock = threading.Lock()
_last_ops_alert: Dict[str, float] = {}


def _mdt_delivery_health(now: Optional[float] = None) -> Dict[str, Any]:
    """Return aggregate VK MDT delivery telemetry without customer data."""
    current = int(time.time() if now is None else now)
    try:
        with _db_cursor() as cur:
            row = cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN mdt_status='synced' THEN 1 ELSE 0 END) AS synced,
                    SUM(CASE WHEN mdt_status='failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN mdt_status='pending' THEN 1 ELSE 0 END) AS pending,
                    SUM(
                        CASE
                            WHEN mdt_status IS NULL OR mdt_status='' OR mdt_status='unset'
                            THEN 1 ELSE 0
                        END
                    ) AS unset_count,
                    MAX(
                        CASE WHEN mdt_status='failed' THEN created_at ELSE NULL END
                    ) AS latest_failed_created_at
                FROM leads
                """
            ).fetchone()
    except sqlite3.Error as exc:
        logger.warning("Health could not read VK MDT delivery state: %s", exc)
        return {
            "enabled": bool(MDT_ENABLED and not DEMO_MODE),
            "mode": MDT_MODE,
            "available": False,
            "total": None,
            "synced": None,
            "failed": None,
            "pending": None,
            "unset": None,
            "latest_failed_seconds": None,
        }

    latest_failed = row["latest_failed_created_at"] if row else None
    return {
        "enabled": bool(MDT_ENABLED and not DEMO_MODE),
        "mode": MDT_MODE,
        "available": True,
        "total": int(row["total"] or 0) if row else 0,
        "synced": int(row["synced"] or 0) if row else 0,
        "failed": int(row["failed"] or 0) if row else 0,
        "pending": int(row["pending"] or 0) if row else 0,
        "unset": int(row["unset_count"] or 0) if row else 0,
        "latest_failed_seconds": (
            max(0, current - int(latest_failed)) if latest_failed is not None else None
        ),
    }


def _notify_ops_alert(message: str, *, alert_key: str) -> bool:
    """Send a rate-limited, PII-free VK/MDT operations alert to Telegram."""
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not ADMIN_ERROR_ALERTS or not bot_token or not LEAD_NOTIFY_IDS:
        return False

    now = time.time()
    key = str(alert_key or "vk_ops")[:100]
    with _ops_alert_lock:
        if _last_ops_alert.get(key, 0) > now - ERROR_ALERT_COOLDOWN:
            return False
        _last_ops_alert[key] = now

    delivered = False
    text = f"⚠️ VK/MDT: {message}"
    for recipient in LEAD_NOTIFY_IDS:
        try:
            resp = http_session.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": recipient, "text": text},
                timeout=5,
            )
            delivered = delivered or resp.status_code == 200
            if resp.status_code != 200:
                logger.warning(
                    "VK ops alert failed for Telegram chat %s: HTTP %s",
                    recipient,
                    resp.status_code,
                )
        except Exception as exc:
            logger.warning("VK ops alert failed for Telegram chat %s: %s", recipient, exc)
    return delivered


def _alert_mdt_preorder_failure() -> bool:
    """Report a one-shot preorder failure without exposing the failed lead."""
    stats = _mdt_delivery_health()
    if stats.get("available"):
        message = (
            "MDT preorder write failed; "
            f"failed={int(stats.get('failed') or 0)}, "
            f"synced={int(stats.get('synced') or 0)}, "
            f"mode={stats.get('mode') or MDT_MODE}"
        )
    else:
        message = f"MDT preorder write failed; mode={MDT_MODE}"
    return _notify_ops_alert(message, alert_key="vk_mdt_preorder_failed")


'''
source = source.replace(record_marker, helpers + record_marker, 1)

old_record_tail = '''        cur.execute(
            """
            UPDATE leads
            SET mdt_status=?, mdt_attempts=1, mdt_next_retry_at=NULL,
                mdt_synced_at=?, mdt_preorder_id=?, mdt_tourist_id=?
            WHERE id=?
            """,
            (
                "synced" if synced else "failed",
                now if synced else None,
                preorder_id,
                tourist_id,
                lead_id,
            ),
        )
'''
assert old_record_tail in source
source = source.replace(
    old_record_tail,
    old_record_tail + '    if not synced:\n        _alert_mdt_preorder_failure()\n',
    1,
)

health_marker = '''        "revision": _version.REVISION,
        "uptime_seconds": _version.uptime_seconds(),
    })'''
assert health_marker in source
source = source.replace(
    health_marker,
    '''        "revision": _version.REVISION,
        "uptime_seconds": _version.uptime_seconds(),
        "mdt_delivery": _mdt_delivery_health(),
    })''',
    1,
)
vk_path.write_text(source, encoding="utf-8")

# Clarify that the existing admin-alert setting covers VK CRM operations too.
env_path = Path('.env.example')
env = env_path.read_text(encoding='utf-8')
old_comment = '# Send critical error alerts to the admin via Telegram (true/false).\n'
assert old_comment in env
env = env.replace(
    old_comment,
    '# Send critical error alerts to the admin via Telegram, including VK CRM failures.\n',
    1,
)
env_path.write_text(env, encoding='utf-8')

# VK tests intentionally disable network alerts by default; focused tests enable them.
test_path = Path('tests/test_vk_bot.py')
tests = test_path.read_text(encoding='utf-8')
setup_marker = 'os.environ.setdefault("VK_MDT_RETRY_ENABLED", "false")\n'
assert setup_marker in tests
if 'os.environ.setdefault("ADMIN_ERROR_ALERTS", "false")' not in tests:
    tests = tests.replace(
        setup_marker,
        setup_marker + 'os.environ.setdefault("ADMIN_ERROR_ALERTS", "false")\n',
        1,
    )

health_old = '''    assert data["revision"]
    assert "total_users" not in data
    assert "vk_group_id" not in data
'''
assert health_old in tests
health_new = '''    assert data["revision"]
    assert data["mdt_delivery"]["available"] is True
    assert data["mdt_delivery"]["total"] == 0
    assert "total_users" not in data
    assert "vk_group_id" not in data
'''
tests = tests.replace(health_old, health_new, 1)

if 'def test_vk_mdt_delivery_health_is_aggregate_and_pii_free' not in tests:
    tests += r'''


def test_vk_mdt_delivery_health_is_aggregate_and_pii_free(client):
    now = int(time.time())
    secret_phone = "+79990002233"
    secret_name = "Private Person"

    lead_ids = []
    for chat_id in (8801, 8802, 8803, 8804):
        lead_ids.append(
            bot.save_lead(
                chat_id,
                {"destination": "Таиланд", "people": "2", "budget": 250000},
                secret_phone,
                first_name=secret_name,
            )
        )
    with bot._db_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE leads SET mdt_status='synced', mdt_synced_at=? WHERE id=?",
            (now - 30, lead_ids[0]),
        )
        cur.execute(
            "UPDATE leads SET mdt_status='failed', created_at=? WHERE id=?",
            (now - 120, lead_ids[1]),
        )
        cur.execute(
            "UPDATE leads SET mdt_status='pending' WHERE id=?",
            (lead_ids[2],),
        )
        cur.execute(
            "UPDATE leads SET mdt_status='unset' WHERE id=?",
            (lead_ids[3],),
        )

    resp = client.get("/vk/health")
    assert resp.status_code == 200
    raw = resp.get_data(as_text=True)
    data = resp.get_json()["mdt_delivery"]
    assert data["available"] is True
    assert data["total"] == 4
    assert data["synced"] == 1
    assert data["failed"] == 1
    assert data["pending"] == 1
    assert data["unset"] == 1
    assert 119 <= data["latest_failed_seconds"] <= 121
    assert secret_phone not in raw
    assert secret_name not in raw


def test_vk_failed_preorder_alert_is_aggregate_and_pii_free(monkeypatch):
    secret_phone = "+79990004455"
    secret_name = "Hidden Customer"
    lead_id = bot.save_lead(
        8810,
        {"destination": "Вьетнам", "people": "2", "budget": 270000},
        secret_phone,
        first_name=secret_name,
    )

    alerts = []
    monkeypatch.setattr(
        bot,
        "_notify_ops_alert",
        lambda message, *, alert_key: alerts.append((message, alert_key)) or True,
    )

    bot._record_mdt_preorder_result(lead_id, None, None)

    assert len(alerts) == 1
    message, alert_key = alerts[0]
    assert alert_key == "vk_mdt_preorder_failed"
    assert "failed=1" in message
    assert "synced=0" in message
    assert secret_phone not in message
    assert secret_name not in message


def test_vk_successful_preorder_does_not_alert(monkeypatch):
    lead_id = bot.save_lead(
        8811,
        {"destination": "Египет", "people": "2", "budget": 220000},
        "vk:8811",
    )
    alerts = []
    monkeypatch.setattr(bot, "_alert_mdt_preorder_failure", lambda: alerts.append(True))

    bot._record_mdt_preorder_result(lead_id, 12345, 67890)

    assert alerts == []
    with bot._db_cursor() as cur:
        row = cur.execute(
            "SELECT mdt_status, mdt_preorder_id, mdt_tourist_id FROM leads WHERE id=?",
            (lead_id,),
        ).fetchone()
    assert tuple(row) == ("synced", 12345, 67890)


def test_vk_ops_alert_uses_stable_cooldown_key(monkeypatch):
    monkeypatch.setattr(bot, "ADMIN_ERROR_ALERTS", True)
    monkeypatch.setattr(bot, "ERROR_ALERT_COOLDOWN", 300)
    monkeypatch.setattr(bot, "LEAD_NOTIFY_IDS", [999])
    monkeypatch.setenv("BOT_TOKEN", "dummy-token")
    bot._last_ops_alert.clear()

    posts = []

    class Resp:
        status_code = 200

    monkeypatch.setattr(
        bot.http_session,
        "post",
        lambda *args, **kwargs: posts.append((args, kwargs)) or Resp(),
    )

    assert bot._notify_ops_alert("failed=1", alert_key="vk_mdt_preorder_failed") is True
    assert bot._notify_ops_alert("failed=2", alert_key="vk_mdt_preorder_failed") is False
    assert len(posts) == 1
    payload = posts[0][1]["json"]
    assert payload["chat_id"] == 999
    assert "failed=1" in payload["text"]
'''

test_path.write_text(tests, encoding='utf-8')
