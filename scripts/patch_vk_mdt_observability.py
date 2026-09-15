from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing patch anchor: {label}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "vk_bot.py",
    '''                mdt_next_retry_at INTEGER,\n                mdt_synced_at INTEGER,\n                created_at INTEGER NOT NULL\n''',
    '''                mdt_next_retry_at INTEGER,\n                mdt_synced_at INTEGER,\n                mdt_preorder_id INTEGER,\n                mdt_tourist_id INTEGER,\n                created_at INTEGER NOT NULL\n''',
    "lead schema observability columns",
)

replace_once(
    "vk_bot.py",
    '''                if "mdt_synced_at" not in _cols:\n                    cur.execute("ALTER TABLE leads ADD COLUMN mdt_synced_at INTEGER")\n''',
    '''                if "mdt_synced_at" not in _cols:\n                    cur.execute("ALTER TABLE leads ADD COLUMN mdt_synced_at INTEGER")\n                if "mdt_preorder_id" not in _cols:\n                    cur.execute("ALTER TABLE leads ADD COLUMN mdt_preorder_id INTEGER")\n                if "mdt_tourist_id" not in _cols:\n                    cur.execute("ALTER TABLE leads ADD COLUMN mdt_tourist_id INTEGER")\n''',
    "lead observability migration",
)

replace_once(
    "vk_bot.py",
    '''def _lead_row_to_info(row: Dict[str, Any]) -> Dict[str, Any]:\n    info = dict(row)\n    info["kids_ages"] = _ages_from_db(info.get("kids_ages"))\n    info["needs_consultation"] = bool(info.get("needs_consultation"))\n    if info.get("dates_are_trip") is not None:\n        info["dates_are_trip"] = bool(info["dates_are_trip"])\n    info["selected_tour"] = _tour_from_db(info.get("selected_tour"))\n    info["_mdt_delivery_key"] = f"vk-lead-{info['id']}"\n    return info\n\n\n# --- user helpers ---\n''',
    '''def _lead_row_to_info(row: Dict[str, Any]) -> Dict[str, Any]:\n    info = dict(row)\n    info["kids_ages"] = _ages_from_db(info.get("kids_ages"))\n    info["needs_consultation"] = bool(info.get("needs_consultation"))\n    if info.get("dates_are_trip") is not None:\n        info["dates_are_trip"] = bool(info["dates_are_trip"])\n    info["selected_tour"] = _tour_from_db(info.get("selected_tour"))\n    info["_mdt_delivery_key"] = f"vk-lead-{info['id']}"\n    return info\n\n\ndef _record_mdt_preorder_result(\n    lead_id: int,\n    preorder_id: Optional[int],\n    tourist_id: Optional[int],\n) -> None:\n    """Persist the one-shot VK preorder outcome without storing more PII.\n\n    Preorder writes are intentionally not retried because MDT creates a temp\n    tourist and then a preorder. Retrying that transaction after a partial\n    success can duplicate CRM entities. Persisting the returned IDs gives us a\n    durable, non-sensitive production proof instead.\n    """\n    synced = preorder_id is not None\n    now = int(time.time())\n    with _db_cursor(commit=True) as cur:\n        cur.execute(\n            """\n            UPDATE leads\n            SET mdt_status=?, mdt_attempts=1, mdt_next_retry_at=NULL,\n                mdt_synced_at=?, mdt_preorder_id=?, mdt_tourist_id=?\n            WHERE id=?\n            """,\n            (\n                "synced" if synced else "failed",\n                now if synced else None,\n                preorder_id,\n                tourist_id,\n                lead_id,\n            ),\n        )\n\n\n# --- user helpers ---\n''',
    "preorder result recorder",
)

replace_once(
    "vk_bot.py",
    '''        if lead_id is not None and MDT_MODE == "lead":\n            _deliver_mdt_lead(lead_id)\n        elif MDT_ENABLED and not DEMO_MODE:\n            # preorder/both remain one-shot because their multi-call transaction\n            # cannot be retried safely without server-side idempotency.\n            send_lead_to_mdt(user_id, delivery_info, phone, client_name)\n''',
    '''        if lead_id is not None and MDT_MODE == "lead":\n            _deliver_mdt_lead(lead_id)\n        elif MDT_ENABLED and not DEMO_MODE:\n            # preorder/both remain one-shot because their multi-call transaction\n            # cannot be retried safely without server-side idempotency. Capture\n            # the production VK preorder IDs so diagnostics can prove the write\n            # without exposing customer data.\n            if lead_id is not None and MDT_MODE == "preorder":\n                preorder_id, tourist_id = send_preorder_to_mdt(\n                    user_id, delivery_info, phone, client_name\n                )\n                _record_mdt_preorder_result(lead_id, preorder_id, tourist_id)\n            else:\n                send_lead_to_mdt(user_id, delivery_info, phone, client_name)\n''',
    "capture preorder result",
)

replace_once(
    "deploy/verify-mdt.sh",
    '''        latest = conn.execute(\n            "SELECT id, mdt_status, mdt_attempts, mdt_next_retry_at, created_at "\n            "FROM leads ORDER BY id DESC LIMIT 1"\n        ).fetchone()\n''',
    '''        columns = {str(row['name']) for row in conn.execute("PRAGMA table_info(leads)").fetchall()}\n        preorder_expr = "mdt_preorder_id" if "mdt_preorder_id" in columns else "NULL AS mdt_preorder_id"\n        tourist_expr = "mdt_tourist_id" if "mdt_tourist_id" in columns else "NULL AS mdt_tourist_id"\n        latest = conn.execute(\n            "SELECT id, mdt_status, mdt_attempts, mdt_next_retry_at, created_at, "\n            f"{preorder_expr}, {tourist_expr} FROM leads ORDER BY id DESC LIMIT 1"\n        ).fetchone()\n''',
    "safe verifier select",
)

replace_once(
    "deploy/verify-mdt.sh",
    '''        print(\n            'MDT outbox: '\n            f'counts={counts_text} '\n            f'latest_id={int(latest["id"])} '\n            f'latest_status={latest["mdt_status"] or "unset"} '\n            f'latest_attempts={int(latest["mdt_attempts"] or 0)} '\n            f'retry_due={retry_due} '\n            f'age_seconds={age}'\n        )\n''',
    '''        preorder_id = latest['mdt_preorder_id']\n        tourist_id = latest['mdt_tourist_id']\n        print(\n            'MDT outbox: '\n            f'counts={counts_text} '\n            f'latest_id={int(latest["id"])} '\n            f'latest_status={latest["mdt_status"] or "unset"} '\n            f'latest_attempts={int(latest["mdt_attempts"] or 0)} '\n            f'preorder_id={int(preorder_id) if preorder_id is not None else "n/a"} '\n            f'tourist_id={int(tourist_id) if tourist_id is not None else "n/a"} '\n            f'retry_due={retry_due} '\n            f'age_seconds={age}'\n        )\n''',
    "safe verifier IDs",
)

replace_once(
    "tests/test_vk_bot.py",
    '''def test_mdt_retry_is_not_armed_for_multistep_modes(monkeypatch):\n    monkeypatch.setattr(bot, "MDT_ENABLED", True)\n    monkeypatch.setattr(bot, "MDT_MODE", "both")\n    lead_id = bot.save_lead(778, {"destination": "Турция"}, "vk:778", first_name="Test")\n    with bot._db_cursor() as cur:\n        row = cur.execute("SELECT mdt_status, mdt_next_retry_at FROM leads WHERE id=?", (lead_id,)).fetchone()\n    assert tuple(row) == ("disabled", None)\n\n\n''',
    '''def test_mdt_retry_is_not_armed_for_multistep_modes(monkeypatch):\n    monkeypatch.setattr(bot, "MDT_ENABLED", True)\n    monkeypatch.setattr(bot, "MDT_MODE", "both")\n    lead_id = bot.save_lead(778, {"destination": "Турция"}, "vk:778", first_name="Test")\n    with bot._db_cursor() as cur:\n        row = cur.execute("SELECT mdt_status, mdt_next_retry_at FROM leads WHERE id=?", (lead_id,)).fetchone()\n    assert tuple(row) == ("disabled", None)\n\n\ndef test_vk_preorder_observability_records_ids(monkeypatch):\n    monkeypatch.setattr(bot, "MDT_ENABLED", True)\n    monkeypatch.setattr(bot, "MDT_MODE", "preorder")\n    monkeypatch.setattr(bot, "DEMO_MODE", False)\n    info = {\n        "destination": "Таиланд",\n        "origin": "Архангельск",\n        "dates": "2026-10-15",\n        "nights": "10",\n        "people": "2",\n        "budget": 270000,\n        "selected_tour": {"hotel": "Test Hotel", "tour_id": "th-test"},\n    }\n    lead_id = bot.save_lead(779, info, "VK test", first_name="Test")\n    monkeypatch.setattr(bot, "send_preorder_to_mdt", lambda *a, **k: (1816, 4321))\n\n    bot._post_completion_side_effects(779, info, "VK test", "Test", lead_id)\n\n    with bot._db_cursor() as cur:\n        row = cur.execute(\n            "SELECT mdt_status, mdt_attempts, mdt_next_retry_at, mdt_synced_at, "\n            "mdt_preorder_id, mdt_tourist_id FROM leads WHERE id=?",\n            (lead_id,),\n        ).fetchone()\n    assert row["mdt_status"] == "synced"\n    assert row["mdt_attempts"] == 1\n    assert row["mdt_next_retry_at"] is None\n    assert row["mdt_synced_at"] > 0\n    assert row["mdt_preorder_id"] == 1816\n    assert row["mdt_tourist_id"] == 4321\n\n\ndef test_vk_preorder_observability_records_failure_without_retry(monkeypatch):\n    monkeypatch.setattr(bot, "MDT_ENABLED", True)\n    monkeypatch.setattr(bot, "MDT_MODE", "preorder")\n    monkeypatch.setattr(bot, "DEMO_MODE", False)\n    info = {"destination": "Турция", "selected_tour": {"hotel": "Test Hotel"}}\n    lead_id = bot.save_lead(780, info, "VK test", first_name="Test")\n    monkeypatch.setattr(bot, "send_preorder_to_mdt", lambda *a, **k: (None, 9876))\n\n    bot._post_completion_side_effects(780, info, "VK test", "Test", lead_id)\n\n    with bot._db_cursor() as cur:\n        row = cur.execute(\n            "SELECT mdt_status, mdt_attempts, mdt_next_retry_at, mdt_synced_at, "\n            "mdt_preorder_id, mdt_tourist_id FROM leads WHERE id=?",\n            (lead_id,),\n        ).fetchone()\n    assert row["mdt_status"] == "failed"\n    assert row["mdt_attempts"] == 1\n    assert row["mdt_next_retry_at"] is None\n    assert row["mdt_synced_at"] is None\n    assert row["mdt_preorder_id"] is None\n    assert row["mdt_tourist_id"] == 9876\n\n\n''',
    "preorder observability tests",
)
