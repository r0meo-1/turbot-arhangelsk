from pathlib import Path

mdt_path = Path("shared/mdt.py")
text = mdt_path.read_text(encoding="utf-8")
old = '''    params = {\n        "name": client_name or f"{settings.name_prefix} {chat_id}",\n        "phone": raw_contact if is_phone else "",\n        "email": "",\n        "source": settings.source,\n        "fields": fields,\n    }\n'''
new = '''    is_vk = settings.name_prefix.strip().casefold() == "vk"\n    params = {\n        "name": client_name or f"{settings.name_prefix} {chat_id}",\n        # MDT's live add-lead endpoint still expects a non-empty contact in the\n        # phone slot. The VK chat descriptor is intentionally preserved here;\n        # `content` and `url` below keep the same contact machine-readable.\n        "phone": raw_contact if (is_phone or is_vk) else "",\n        "email": "",\n        "source": settings.source,\n        "fields": fields,\n    }\n'''
if '"phone": raw_contact if (is_phone or is_vk) else ""' not in text:
    if old not in text:
        raise SystemExit("MDT phone mapping anchor not found")
    text = text.replace(old, new, 1)
text = text.replace(
    '    if settings.name_prefix.strip().casefold() == "vk":\n        params["url"] = f"https://vk.com/id{chat_id}"\n',
    '    if is_vk:\n        params["url"] = f"https://vk.com/id{chat_id}"\n',
    1,
)
mdt_path.write_text(text, encoding="utf-8")

shared_test = Path("tests/test_shared.py")
tests = shared_test.read_text(encoding="utf-8")
tests = tests.replace(
    "def test_create_lead_maps_vk_contact_outside_phone_field():",
    "def test_create_lead_keeps_vk_contact_for_mdt_and_metadata():",
    1,
)
tests = tests.replace(
    '    assert params["phone"] == ""\n    fields = {field["name"]: field["values"][0] for field in params["fields"]}\n',
    '    assert params["phone"] == "VK (чат id 424242) · Тест"\n    fields = {field["name"]: field["values"][0] for field in params["fields"]}\n',
    1,
)
shared_test.write_text(tests, encoding="utf-8")

vk_path = Path("vk_bot.py")
vk = vk_path.read_text(encoding="utf-8")
anchor = '''            cur.execute(\n                "INSERT INTO maintenance_migrations (name, applied_at) VALUES (?, ?)",\n                (repair_name, repair_now),\n            )\n\n        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_chat_id ON leads(chat_id)")\n'''
replacement = '''            cur.execute(\n                "INSERT INTO maintenance_migrations (name, applied_at) VALUES (?, ?)",\n                (repair_name, repair_now),\n            )\n\n        # The CRM UI later confirmed that this exact lead already exists as\n        # inquiry #1815 with external key vk-lead-35. Stop retrying that row: a\n        # retry cannot improve an already-created CRM record and could create a\n        # duplicate on endpoints that do not enforce external-key uniqueness.\n        confirm_name = "20260915_confirm_existing_mdt_lead_35"\n        cur.execute("SELECT 1 FROM maintenance_migrations WHERE name = ?", (confirm_name,))\n        if cur.fetchone() is None:\n            confirm_now = int(time.time())\n            cur.execute(\n                """\n                UPDATE leads\n                SET mdt_status='synced', mdt_next_retry_at=NULL, mdt_synced_at=?\n                WHERE id=35 AND mdt_status='pending' AND mdt_attempts > 0\n                """,\n                (confirm_now,),\n            )\n            if cur.rowcount:\n                logger.warning("Stopped retries for MDT lead 35 after CRM confirmation")\n            cur.execute(\n                "INSERT INTO maintenance_migrations (name, applied_at) VALUES (?, ?)",\n                (confirm_name, confirm_now),\n            )\n\n        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_chat_id ON leads(chat_id)")\n'''
if "20260915_confirm_existing_mdt_lead_35" not in vk:
    if anchor not in vk:
        raise SystemExit("VK maintenance anchor not found")
    vk = vk.replace(anchor, replacement, 1)
vk_path.write_text(vk, encoding="utf-8")

vk_test_path = Path("tests/test_vk_bot.py")
vk_tests = vk_test_path.read_text(encoding="utf-8")
marker = "def test_confirmed_mdt_lead_35_stops_retry_once"
if marker not in vk_tests:
    vk_tests += '''\n\n\ndef test_confirmed_mdt_lead_35_stops_retry_once(tmp_path, monkeypatch):\n    db_path = tmp_path / "confirm-existing.sqlite"\n    monkeypatch.setattr(bot, "DATABASE_PATH", str(db_path))\n    bot.init_db()\n\n    confirm_name = "20260915_confirm_existing_mdt_lead_35"\n    with bot._db_cursor(commit=True) as cur:\n        cur.execute("DELETE FROM maintenance_migrations WHERE name = ?", (confirm_name,))\n        cur.execute(\n            """\n            INSERT INTO leads (\n                id, chat_id, phone, mdt_status, mdt_attempts,\n                mdt_next_retry_at, mdt_synced_at, created_at\n            ) VALUES (35, 424242, 'VK test', 'pending', 4, 123, NULL, 123)\n            """\n        )\n\n    bot.init_db()\n    with bot._db_cursor() as cur:\n        row = cur.execute(\n            "SELECT mdt_status, mdt_attempts, mdt_next_retry_at, mdt_synced_at "\n            "FROM leads WHERE id=35"\n        ).fetchone()\n        marker_row = cur.execute(\n            "SELECT applied_at FROM maintenance_migrations WHERE name = ?", (confirm_name,)\n        ).fetchone()\n\n    assert row["mdt_status"] == "synced"\n    assert row["mdt_attempts"] == 4\n    assert row["mdt_next_retry_at"] is None\n    assert row["mdt_synced_at"] is not None\n    assert marker_row is not None\n\n    # The marker makes this a one-time production repair.\n    with bot._db_cursor(commit=True) as cur:\n        cur.execute(\n            "UPDATE leads SET mdt_status='pending', mdt_next_retry_at=123 WHERE id=35"\n        )\n    bot.init_db()\n    with bot._db_cursor() as cur:\n        row = cur.execute("SELECT mdt_status FROM leads WHERE id=35").fetchone()\n    assert row["mdt_status"] == "pending"\n'''
    vk_test_path.write_text(vk_tests, encoding="utf-8")
