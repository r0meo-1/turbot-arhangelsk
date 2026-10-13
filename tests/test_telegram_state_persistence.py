"""Telegram dialog changes remain pending after a transient database failure."""

import sqlite3

import pytest

@pytest.mark.parametrize("failure_stage", ["session", "delete", "user"])
def test_failed_state_flush_is_retried_without_losing_new_changes(monkeypatch, failure_stage):
    import bot

    monkeypatch.setattr(bot, "_dirty_sessions", {101})
    monkeypatch.setattr(bot, "_dirty_users", {101})
    sessions = {} if failure_stage == "delete" else {101: {"state": "destination"}}
    monkeypatch.setattr(bot, "user_data", sessions)
    monkeypatch.setattr(bot, "all_users", {101: {"first_name": "Test", "last_seen": 123}})
    writes = []
    fail = True

    def persist(stage, chat_id, value):
        nonlocal fail
        if fail and stage == failure_stage:
            fail = False
            # A new dialog change can arrive while the old batch is flushing.
            bot._mark_dirty(202)
            raise sqlite3.OperationalError("database is locked")
        writes.append((stage, chat_id))

    monkeypatch.setattr(bot, "set_session", lambda uid, info: persist("session", uid, info))
    monkeypatch.setattr(bot, "delete_session", lambda uid: persist("delete", uid, None))
    monkeypatch.setattr(bot, "touch_user", lambda uid, *args, **kwargs: persist("user", uid, None))

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        bot.save_state()

    assert bot._dirty_sessions == {101, 202}
    assert bot._dirty_users == {101, 202}
    bot.save_state()
    assert ("delete" if failure_stage == "delete" else "session", 101) in writes
    assert ("user", 101) in writes
    assert ("delete", 202) in writes
    assert not bot._dirty_sessions
    assert not bot._dirty_users
