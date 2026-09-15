import vk_bot as bot


def test_miniapp_review_return_aliases_replay_current_review(monkeypatch):
    assert bot._COMMAND_ALIASES["проверить заявку"] == "review"
    assert bot._COMMAND_ALIASES["проверить"] == "review"

    user_id = 424242
    # Compatibility path: an ordinary durable review without a dedicated
    # Mini App snapshot still replays its current state.
    bot.delete_session(user_id)
    bot._delete_miniapp_snapshot(user_id)
    bot.set_session(user_id, {
        "state": bot.STATE_REVIEW,
        "destination": "Шри-Ланка",
        "origin": "Архангельск",
        "updated_at": 1,
    })
    bot.user_data.pop(user_id, None)
    prompted = []

    monkeypatch.setattr(bot, "get_user_name", lambda _: "Тест")
    monkeypatch.setattr(
        bot,
        "_prompt_for_state",
        lambda uid, state: prompted.append((uid, state)),
    )

    event = {
        "object": {
            "message": {
                "from_id": user_id,
                "peer_id": user_id,
                "text": "Проверить заявку",
                "payload": None,
            }
        }
    }

    try:
        bot._process_message(event)
        assert prompted == [(user_id, bot.STATE_REVIEW)]
    finally:
        bot.user_data.pop(user_id, None)
        bot._delete_miniapp_snapshot(user_id)
        bot.delete_session(user_id)
