import vk_bot as bot


def test_miniapp_review_return_aliases_replay_current_review(monkeypatch):
    assert bot._COMMAND_ALIASES["проверить заявку"] == "menu"
    assert bot._COMMAND_ALIASES["проверить"] == "menu"

    user_id = 424242
    bot.user_data[user_id] = {"state": bot.STATE_REVIEW}
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
