"""Focused coverage for VK -> MDT CRM write behavior."""

import json
import os
import tempfile

os.environ.setdefault("VK_ACCESS_TOKEN", "dummy-token")
os.environ.setdefault("VK_GROUP_ID", "999")
os.environ.setdefault("VK_CONFIRMATION", "confirm123")
os.environ.setdefault("VK_SECRET_KEY", "vk-test-secret")
os.environ.setdefault("TUTU_ENABLED", "false")
os.environ.setdefault("VK_DEMO_MODE", "false")
os.environ.setdefault("ADMIN_ID", "999")
os.environ.setdefault("DIALOG_TIMEOUT_HOURS", "0")
os.environ.setdefault("SYNC_COMPLETION", "true")
os.environ.setdefault("VK_MDT_RETRY_ENABLED", "false")
os.environ.setdefault("AI_MODE", "template")
os.environ.setdefault("CONSENT_MODE", "strict")
os.environ["DATABASE_PATH"] = os.path.join(
    tempfile.gettempdir(), f"vk_turbot_test_{os.getpid()}.sqlite"
)

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
    monkeypatch.setattr(bot, "MDT_MODE", "preorder")
    monkeypatch.setattr(bot, "MDT_SOURCE", "Telegram Bot")
    monkeypatch.setenv("VK_MDT_SOURCE", "VK Bot")


def test_vk_mdt_source_is_platform_specific(monkeypatch):
    _enable_mdt(monkeypatch)
    monkeypatch.delenv("VK_MDT_SOURCE", raising=False)

    # The shared Telegram source must not leak into VK attribution.
    assert bot._mdt_settings().source == "VK Bot"

    monkeypatch.setenv("VK_MDT_SOURCE", "VK Community")
    assert bot._mdt_settings().source == "VK Community"


def test_vk_send_preorder_assigns_sole_manager(monkeypatch):
    _enable_mdt(monkeypatch)
    captured = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_post(url, data=None, timeout=None, **kwargs):
        params = json.loads(data["params"])
        captured.append((url, params, timeout, kwargs))
        if url.endswith("/api/get-manager-list"):
            return Response({"data": [{"id": 17, "dismissed": 0, "office_id": 1}]})
        if url.endswith("/api/add-tourist-temp"):
            return Response({"id": 10})
        if url.endswith("/api/create-preorder"):
            return Response({"id": 20})
        raise AssertionError(url)

    monkeypatch.setattr(bot.http_session, "post", fake_post)

    ok = bot.send_lead_to_mdt(
        424242,
        {
            "destination": "Таиланд",
            "origin": "Архангельск",
            "dates": "15-25 января 2027",
            "nights": "10",
            "people": "2",
            "budget": 270000,
            "budget_scope": "total",
            "_mdt_delivery_key": "vk-lead-36",
            "selected_tour": {
                "hotel": "Mandarava Resort",
                "date": "2027-01-15",
                "nights": 10,
                "meal": "BB",
                "price": 245000,
                "tour_id": "tv-42",
            },
        },
        "VK (чат id 424242) · Тестовый клиент",
        "Тестовый клиент",
    )

    assert ok is True
    assert [item[0].rsplit("/", 1)[-1] for item in captured] == [
        "get-manager-list", "add-tourist-temp", "create-preorder"
    ]
    for _, _, timeout, kwargs in captured:
        assert timeout == bot.HTTP_TIMEOUT
        assert kwargs == {}

    tourist = captured[1][1]
    assert tourist["name"] == "Тестовый клиент"
    assert tourist["manager_id"] == 17

    preorder = captured[2][1]
    assert preorder["preorder_manager_id"] == 17
    assert preorder["country_id1"] == 0  # country cache is lazy and may be empty in this focused test
    assert preorder["link"] == "https://vk.com/id424242"
    assert preorder["nights_from"] == 10
    assert preorder["nights_to"] == 10
    assert "Вылет: Архангельск" in preorder["comment"]
    assert "Источник: VK Bot" in preorder["comment"]
    assert "ID заявки бота: vk-lead-36" in preorder["comment"]
    assert "Mandarava Resort" in preorder["comment"]
    assert "ID tv-42" in preorder["comment"]


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
    # SYNC_COMPLETION exercises post-completion work inline. Keep this unit
    # test hermetic: the fallback recommendation must not reach the live VK
    # API when developer credentials happen to be present in the environment.
    monkeypatch.setattr(bot, "send_typing", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "send_message", lambda *args, **kwargs: None)

    def fail_mdt(*args, **kwargs):
        raise RuntimeError("simulated CRM outage")

    monkeypatch.setattr(bot, "send_lead_to_mdt", fail_mdt)

    bot.handle_completion(user_id, "+79000000000", {"_user_name": "Тестовый клиент"})

    assert events == ["client-confirmed", "manager-notified"]
    assert user_id not in bot.user_data
    with bot._db_cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM leads WHERE chat_id = ?", (user_id,)).fetchone()[0] == 1

    bot.delete_user_data(user_id)
