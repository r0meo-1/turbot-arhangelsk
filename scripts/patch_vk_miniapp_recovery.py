from pathlib import Path

path = Path("vk_bot.py")
text = path.read_text(encoding="utf-8")

migration_anchor = '        # Additive migration for databases created before the origin step.\n'
migration_block = '''        cur.execute("""
            CREATE TABLE IF NOT EXISTS miniapp_drafts (
                chat_id INTEGER PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        # Additive migration for databases created before the origin step.
'''
if "CREATE TABLE IF NOT EXISTS miniapp_drafts" not in text:
    if migration_anchor not in text:
        raise SystemExit("miniapp migration anchor not found")
    text = text.replace(migration_anchor, migration_block, 1)

helper_anchor = '''def delete_session(chat_id: int) -> None:
    with _db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM sessions WHERE chat_id = ?", (chat_id,))


'''
helper_block = '''def delete_session(chat_id: int) -> None:
    with _db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM sessions WHERE chat_id = ?", (chat_id,))


_MINIAPP_SNAPSHOT_FIELDS = (
    "destination", "origin", "dates", "nights", "dates_are_trip",
    "hotel_query", "people", "kids", "kids_ages", "infants",
    "budget", "budget_scope", "source", "vk_ref", "vk_platform",
    "needs_consultation",
)


def _save_miniapp_snapshot(chat_id: int, info: Dict[str, Any]) -> None:
    payload = {key: info.get(key) for key in _MINIAPP_SNAPSHOT_FIELDS}
    now = int(time.time())
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO miniapp_drafts (chat_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                payload=excluded.payload, updated_at=excluded.updated_at
            """,
            (chat_id, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), now),
        )


def _load_miniapp_snapshot(chat_id: int) -> Optional[Dict[str, Any]]:
    with _db_cursor() as cur:
        cur.execute("SELECT payload FROM miniapp_drafts WHERE chat_id = ?", (chat_id,))
        row = cur.fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row[0])
    except (TypeError, ValueError):
        logger.warning("Invalid Mini App snapshot for chat_id=%s", chat_id)
        return None
    if not isinstance(payload, dict):
        return None
    payload["state"] = STATE_REVIEW
    payload["updated_at"] = int(time.time())
    return payload


def _delete_miniapp_snapshot(chat_id: int) -> None:
    with _db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM miniapp_drafts WHERE chat_id = ?", (chat_id,))


'''
if "def _save_miniapp_snapshot" not in text:
    if helper_anchor not in text:
        raise SystemExit("session helper anchor not found")
    text = text.replace(helper_anchor, helper_block, 1)

delete_anchor = '''        cur.execute("DELETE FROM sessions WHERE chat_id = ?", (chat_id,))
        cur.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
        cur.execute("DELETE FROM leads WHERE chat_id = ?", (chat_id,))
'''
delete_block = '''        cur.execute("DELETE FROM sessions WHERE chat_id = ?", (chat_id,))
        cur.execute("DELETE FROM miniapp_drafts WHERE chat_id = ?", (chat_id,))
        cur.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
        cur.execute("DELETE FROM leads WHERE chat_id = ?", (chat_id,))
'''
if 'DELETE FROM miniapp_drafts WHERE chat_id = ?", (chat_id,))\n        cur.execute("DELETE FROM users' not in text:
    if delete_anchor not in text:
        raise SystemExit("delete_user_data anchor not found")
    text = text.replace(delete_anchor, delete_block, 1)

cancel_anchor = '''def handle_cancel(user_id: int) -> None:
    with _lock:
        existed = user_data.pop(user_id, None) is not None
    if existed:
        _mark_dirty(user_id)
        delete_session(user_id)
        send_message(
'''
cancel_block = '''def handle_cancel(user_id: int) -> None:
    snapshot_existed = _load_miniapp_snapshot(user_id) is not None
    with _lock:
        existed = user_data.pop(user_id, None) is not None
    _delete_miniapp_snapshot(user_id)
    if existed or snapshot_existed:
        _mark_dirty(user_id)
        delete_session(user_id)
        send_message(
'''
if "snapshot_existed = _load_miniapp_snapshot" not in text:
    if cancel_anchor not in text:
        raise SystemExit("handle_cancel anchor not found")
    text = text.replace(cancel_anchor, cancel_block, 1)

aliases_old = '''    "кнопки": "menu", "меню": "menu", "продолжить": "menu", "где кнопки": "menu",
    "проверить заявку": "menu", "проверить": "menu",
    "проверить заявку": "menu", "проверить": "menu",
'''
aliases_new = '''    "кнопки": "menu", "меню": "menu", "продолжить": "menu", "где кнопки": "menu",
    "проверить заявку": "review", "проверить": "review",
'''
if '"проверить заявку": "review"' not in text:
    if aliases_old not in text:
        raise SystemExit("command alias anchor not found")
    text = text.replace(aliases_old, aliases_new, 1)

menu_anchor = '''    if command == "menu":
        # Повторить текущий вопрос вместе с его кнопками. Mini App persists its
'''
review_block = '''    if command == "review":
        # `sessions` is mutable FSM state. Keep Mini App's saved review separate
        # so chat navigation cannot destroy it.
        snapshot = _load_miniapp_snapshot(user_id)
        if snapshot is not None:
            with _lock:
                user_data[user_id] = snapshot
            set_session(user_id, snapshot)
            _ask_review(user_id)
            return

        # Backward compatibility for drafts saved before miniapp_drafts existed.
        info = user_data.get(user_id) or _restore_session_from_db(user_id)
        if info and info.get("source") == "vk_mini_app":
            destination = str(info.get("destination") or "")
            legacy_complete = bool(
                destination
                and destination not in (DEST_HOT_TOURS_LABEL, DEST_DIRECT_FLIGHTS_LABEL)
                and info.get("origin") and info.get("dates") and info.get("people")
                and (info.get("budget") is not None or info.get("budget_open_ended"))
            )
            if legacy_complete:
                info["state"] = STATE_REVIEW
                info.pop("selected_tour", None)
                info.pop("_tour_offers", None)
                info.pop("_tour_offers_base", None)
                info["updated_at"] = int(time.time())
                set_session(user_id, info)
                _ask_review(user_id)
                return
            send_message(
                user_id,
                "Черновик Mini App был изменён старой навигацией. "
                "Откройте приложение, проверьте параметры и сохраните их ещё раз — "
                "после этого команда «Проверить заявку» восстановит их независимо от кнопок чата.",
                keyboard=_soft_start_keyboard(),
            )
            return

        state = (info or {}).get("state")
        if state:
            _prompt_for_state(user_id, state)
        else:
            send_message(user_id, HINT_START, keyboard=_soft_start_keyboard())
        return

    if command == "menu":
        # Повторить текущий вопрос вместе с его кнопками. Mini App persists its
'''
if 'if command == "review":' not in text:
    if menu_anchor not in text:
        raise SystemExit("menu command anchor not found")
    text = text.replace(menu_anchor, review_block, 1)

completion_anchor = '''    delete_session(user_id)

    if SYNC_COMPLETION:
'''
completion_block = '''    delete_session(user_id)
    _delete_miniapp_snapshot(user_id)

    if SYNC_COMPLETION:
'''
if "delete_session(user_id)\n    _delete_miniapp_snapshot(user_id)" not in text:
    if completion_anchor not in text:
        raise SystemExit("completion anchor not found")
    text = text.replace(completion_anchor, completion_block, 1)

miniapp_anchor = '''        info["updated_at"] = int(time.time())
        set_session(user_id, info)
        user_data[user_id] = info
'''
miniapp_block = '''        info["updated_at"] = int(time.time())
        _save_miniapp_snapshot(user_id, info)
        set_session(user_id, info)
        user_data[user_id] = info
'''
if "_save_miniapp_snapshot(user_id, info)" not in text:
    if miniapp_anchor not in text:
        raise SystemExit("Mini App save anchor not found")
    text = text.replace(miniapp_anchor, miniapp_block, 1)

path.write_text(text, encoding="utf-8")

test_path = Path("tests/test_vk_bot.py")
tests = test_path.read_text(encoding="utf-8")
fixture_anchor = '''        cur.execute("DELETE FROM sessions")
        cur.execute("DELETE FROM users")
        cur.execute("DELETE FROM leads")
'''
fixture_block = '''        cur.execute("DELETE FROM sessions")
        cur.execute("DELETE FROM miniapp_drafts")
        cur.execute("DELETE FROM users")
        cur.execute("DELETE FROM leads")
'''
if 'cur.execute("DELETE FROM miniapp_drafts")' not in tests:
    if fixture_anchor not in tests:
        raise SystemExit("test fixture anchor not found")
    tests = tests.replace(fixture_anchor, fixture_block, 1)

marker = "test_miniapp_review_command_restores_snapshot_after_chat_navigation"
if marker not in tests:
    tests += '''

def test_miniapp_review_command_restores_snapshot_after_chat_navigation(client, monkeypatch):
    from datetime import date, timedelta
    from shared.vk_miniapp import validate_vk_trip

    user_id = 9962
    raw = dict(
        type="trip_request", version=2, destination="Шри-Ланка",
        departure="Архангельск",
        date=(date.today() + timedelta(days=30)).isoformat(),
        nights=10, adults=2, children=0, childrenAges=[],
        budgetMaxRub=270000, consent=True,
    )
    bot._save_miniapp_draft(user_id, validate_vk_trip(raw))

    corrupted = dict(bot.user_data[user_id])
    corrupted["state"] = bot.STATE_DESTINATION
    corrupted["destination"] = bot.DEST_HOT_TOURS_LABEL
    bot.user_data[user_id] = corrupted
    bot.set_session(user_id, corrupted)

    reviews = []
    monkeypatch.setattr(bot, "_ask_review", lambda uid: reviews.append(dict(bot.user_data[uid])))
    response = _post(client, user_id, "Проверить заявку")

    assert response.status_code == 200
    restored = bot.user_data[user_id]
    assert restored["state"] == bot.STATE_REVIEW
    assert restored["destination"] == "Шри-Ланка"
    assert restored["origin"] == "Архангельск"
    assert str(restored["nights"]) == "10"
    assert restored["budget"] == 270000
    assert reviews and reviews[0]["destination"] == "Шри-Ланка"
    assert bot.get_session(user_id)["destination"] == "Шри-Ланка"


def test_miniapp_snapshot_is_removed_on_cancel():
    from datetime import date, timedelta
    from shared.vk_miniapp import validate_vk_trip

    user_id = 9963
    raw = dict(
        type="trip_request", version=2, destination="Шри-Ланка",
        departure="Архангельск",
        date=(date.today() + timedelta(days=30)).isoformat(),
        nights=10, adults=2, children=0, childrenAges=[],
        budgetMaxRub=270000, consent=True,
    )
    bot._save_miniapp_draft(user_id, validate_vk_trip(raw))
    assert bot._load_miniapp_snapshot(user_id) is not None

    bot.handle_cancel(user_id)

    assert bot._load_miniapp_snapshot(user_id) is None
    assert bot.get_session(user_id) is None
'''
test_path.write_text(tests, encoding="utf-8")
