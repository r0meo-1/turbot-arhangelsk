"""Focused coverage for VK -> MDT CRM write behavior."""

import json

import vk_bot as bot


class _JsonResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": {"id": 321}}


def _enable_mdt(monkeypatch):
    monkeypatch.setattr(bot, "MDT_ENABLED", True)
    monkeypatch.setattr(bot, "MDT_ACCOUNT", "apreltour")
    monkeypatch.setattr(bot, "MDT_BASE_URL", "https://apreltour.moidokumenti.ru")
    monkeypatch.setattr(bot, "MDT_API_KEY", "unit-test-key")
    monkeypatch.setattr(bot, "MDT_MODE", "lead")
    monkeypatch.setattr(bot, "MDT_SOURCE", "Telegram Bot")
    monkeypatch.setenv("VK_MDT_SOURCE", "VK Bot")


def test_vk_mdt_source_is_platform_specific(monkeypatch):
    _enable_mdt(monkeypatch)
    monkeypatch.delenv("VK_MDT_SOURCE", raising=False)

    # The shared Telegram source must not leak into VK attribution.
    assert bot._mdt_settings().source == "VK Bot"

    monkeypatch.setenv("VK_MDT_SOURCE", "VK Community")
    assert bot._mdt_settings().source == "VK Community"


def test_vk_send_lead_posts_expected_add_lead_payload(monkeypatch):
    _enable_mdt(monkeypatch)
    captured = []

    def fake_post(url, data=None, timeout=None, **kwargs):
        captured.append((url, data, timeout, kwargs))
        return _JsonResponse()

    monkeypatch.setattr(bot.http_session, "post", fake_post)

    bot.send_lead_to_mdt(
        424242,
        {
            "destination": "Таиланд",
            "dates": "15-25 января",
            "nights": "10",
            "people": "2",
            "budget": 270000,
            "budget_scope": "total",
            "selected_tour": {
                "hotel": "Mandarava Resort",
                "date": "2027-01-15",
                "nights": 10,
                "meal": "BB",
                "price": 245000,
                "tour_id": "tv-42",
            },
        },
        "+79000000000",
        "Тестовый клиент",
    )

    assert len(captured) == 1
    url, form, timeout, kwargs = captured[0]
    assert url == "https://apreltour.moidokumenti.ru/api/add-lead"
    assert form["key"] == "unit-test-key"
    assert timeout == bot.HTTP_TIMEOUT
    assert kwargs == {}

    params = json.loads(form["params"])
    assert params["name"] == "Тестовый клиент"
    assert params["phone"] == "+79000000000"
    assert params["source"] == "VK Bot"
    fields = {item["name"]: item["values"] for item in params["fields"]}
    assert fields["Направление"] == ["Таиланд"]
    assert fields["Бюджет"] == ["270000 ₽ на всю поездку"]
    assert "Mandarava Resort" in fields["Выбранный тур"][0]
    assert "ID tv-42" in fields["Выбранный тур"][0]


def test_vk_completion_survives_mdt_failure(monkeypatch):
    user_id = 90909091
    bot.delete_user_data(user_id)
    events = []
    bot.user_data[user_id] = {
        "state": bot.STATE_CONTACT,
        "destination": "Таиланд",
        "origin": "Архангельск",
        "dates": "15-25 января",
        "nights": "10",
        "people": "2",
        "kids_ages": [],
        "budget": 270000,
        "budget_scope": "total",
    }

    monkeypatch.setattr(bot, "SYNC_COMPLETION", True)
    monkeypatch.setattr(bot, "_confirm_to_user", lambda *args, **kwargs: events.append("client-confirmed"))
    monkeypatch.setattr(bot, "_notify_admin", lambda *args, **kwargs: events.append("manager-notified"))

    def fail_mdt(*args, **kwargs):
        raise RuntimeError("simulated CRM outage")

    monkeypatch.setattr(bot, "send_lead_to_mdt", fail_mdt)

    bot.handle_completion(user_id, "+79000000000", {"_user_name": "Тестовый клиент"})

    assert events == ["client-confirmed", "manager-notified"]
    assert user_id not in bot.user_data
    with bot._db_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM leads WHERE chat_id = ?", (user_id,)).fetchone()[0] == 1

    bot.delete_user_data(user_id)
