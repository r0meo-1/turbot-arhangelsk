from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "vk_bot.py",
    '''    "рассылка": "broadcast",\n    "напоминания": "followup",\n}\n\n\ndef _remember_client_capabilities''',
    '''    "рассылка": "broadcast",\n    "напоминания": "followup",\n    "crm статус": "crm_status", "mdt статус": "crm_status",\n}\n\n\ndef _mdt_admin_status_text() -> str:\n    """Return a PII-free summary of the latest local MDT delivery state."""\n    with _db_cursor() as cur:\n        row = cur.execute(\n            """\n            SELECT id, mdt_status, mdt_attempts, mdt_preorder_id,\n                   mdt_tourist_id, mdt_synced_at, created_at\n            FROM leads ORDER BY id DESC LIMIT 1\n            """\n        ).fetchone()\n    if row is None:\n        return "🔧 CRM статус\\nЗаявок пока нет."\n\n    now = int(time.time())\n    age_seconds = max(0, now - int(row["created_at"] or now))\n    age_minutes = age_seconds // 60\n    preorder_id = row["mdt_preorder_id"]\n    tourist_id = row["mdt_tourist_id"]\n    synced_at = row["mdt_synced_at"]\n    return (\n        "🔧 CRM статус\\n"\n        f"Локальная заявка: #{int(row['id'])}\\n"\n        f"MDT: {row['mdt_status'] or 'unset'}\\n"\n        f"Попытки: {int(row['mdt_attempts'] or 0)}\\n"\n        f"Preorder ID: {int(preorder_id) if preorder_id is not None else '—'}\\n"\n        f"Tourist ID: {int(tourist_id) if tourist_id is not None else '—'}\\n"\n        f"Синхронизация: {'есть' if synced_at else 'нет'}\\n"\n        f"Возраст записи: {age_minutes} мин."\n    )\n\n\ndef _remember_client_capabilities''',
    "CRM status alias/helper",
)

replace_once(
    "vk_bot.py",
    '''        if command == "analytics":\n            with _db_cursor() as cur:\n''',
    '''        if command == "crm_status":\n            send_message(user_id, _mdt_admin_status_text())\n            return\n        if command == "analytics":\n            with _db_cursor() as cur:\n''',
    "admin CRM status handler",
)

replace_once(
    "tests/test_vk_bot.py",
    '''def test_privacy_command(client):\n    resp = _post(client, 666, "Политика")\n    assert resp.status_code == 200\n\n\ndef test_template_selection():\n''',
    '''def test_privacy_command(client):\n    resp = _post(client, 666, "Политика")\n    assert resp.status_code == 200\n\n\ndef test_admin_crm_status_is_pii_free(client, monkeypatch):\n    monkeypatch.setattr(bot, "MDT_ENABLED", True)\n    monkeypatch.setattr(bot, "MDT_MODE", "preorder")\n    lead_id = bot.save_lead(424242, {"destination": "Секретное направление"}, "SECRET-PHONE", first_name="SECRET-NAME")\n    with bot._db_cursor(commit=True) as cur:\n        cur.execute(\n            """\n            UPDATE leads\n            SET mdt_status='synced', mdt_attempts=1, mdt_synced_at=?,\n                mdt_preorder_id=1816, mdt_tourist_id=4321\n            WHERE id=?\n            """,\n            (int(time.time()), lead_id),\n        )\n    sent = []\n    monkeypatch.setattr(bot, "send_message", lambda uid, text, **kwargs: sent.append((uid, text)))\n\n    response = _post(client, bot.ADMIN_ID, "CRM статус")\n\n    assert response.status_code == 200\n    assert len(sent) == 1\n    text = sent[0][1]\n    assert f"Локальная заявка: #{lead_id}" in text\n    assert "MDT: synced" in text\n    assert "Preorder ID: 1816" in text\n    assert "Tourist ID: 4321" in text\n    assert "SECRET-NAME" not in text\n    assert "SECRET-PHONE" not in text\n    assert "Секретное направление" not in text\n\n\ndef test_template_selection():\n''',
    "admin CRM status test",
)
