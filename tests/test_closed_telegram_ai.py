from types import SimpleNamespace

import pytest

from tools.closed_telegram_ai import ClosedTest, Settings, Telegram


def settings():
    return Settings.load({"TEST_TELEGRAM_BOT_TOKEN": "200:synthetic-only",
                          "TEST_BOT_USERNAME": "synthetic_test_bot", "TEST_USER_ID": "42",
                          "FORBIDDEN_PRODUCTION_BOT_ID": "100"})


class FakeTelegram:
    def __init__(self, fail_notice=False, fail_client=False):
        self.sent = []
        self.fail_notice, self.fail_client = fail_notice, fail_client

    def send(self, text):
        if self.fail_notice and "уведомление менеджеру" in text:
            raise TimeoutError("synthetic token-bearing raw error")
        if self.fail_client and text.startswith("ТЕСТ ·"):
            raise TimeoutError("synthetic client failure")
        self.sent.append(text)
        return len(self.sent)


def update(text, number=1, user=42, chat=42, kind="private"):
    return {"update_id": number, "message": {"from": {"id": user},
            "chat": {"id": chat, "type": kind}, "text": text}}


@pytest.mark.parametrize("user,chat,kind", [(43, 43, "private"), (42, -1, "group"), (43, 42, "private")])
def test_unknown_users_or_groups_never_get_ai_or_any_reply(tmp_path, user, chat, kind):
    tg = FakeTelegram()
    runner = ClosedTest(settings(), tg, tmp_path / "state.json")
    runner.handle(update("/manager", user=user, chat=chat, kind=kind))
    assert tg.sent == []
    assert runner.state["handoffs"] == {}
    assert runner.state["ai_calls"] == 0


def test_real_helper_handoff_receipt_ack_and_restart_dedup(tmp_path):
    tg = FakeTelegram()
    path = tmp_path / "state.json"
    runner = ClosedTest(settings(), tg, path)
    runner.handle(update("/manager"))
    assert len(tg.sent) == 2
    test_id, receipt = next(iter(runner.state["handoffs"].items()))
    assert receipt["api_accepted"] and not receipt["human_acknowledged"]
    assert receipt["message_id"] == 2
    assert "реальной заявки нет" in tg.sent[-1]
    reopened = ClosedTest(settings(), tg, path)
    reopened.handle(update("/manager"))
    assert len(tg.sent) == 2
    reopened.handle(update("/ack " + test_id, number=2))
    assert reopened.summary()["human_acknowledged"] == 1
    assert "Хочу менеджера" not in path.read_text(), "raw questions must not persist"


def test_failed_notice_cannot_be_acknowledged_or_claimed_delivered(tmp_path):
    tg = FakeTelegram(fail_notice=True)
    runner = ClosedTest(settings(), tg, tmp_path / "state.json")
    runner.handle(update("/manager"))
    test_id, event = next(iter(runner.state["handoffs"].items()))
    assert not event["api_accepted"]
    assert event["error_class"] == "TimeoutError"
    runner.handle(update("/ack " + test_id, number=2))
    assert not event["human_acknowledged"]
    assert runner.summary()["api_accepted"] == 0


def test_client_send_failure_does_not_prevent_manager_notice(tmp_path):
    tg = FakeTelegram(fail_client=True)
    runner = ClosedTest(settings(), tg, tmp_path / "state.json")
    runner.handle(update("/manager"))
    assert runner.summary()["api_accepted"] == 1


def test_sensitive_question_is_redacted_in_real_notice_path(tmp_path):
    tg = FakeTelegram()
    runner = ClosedTest(settings(), tg, tmp_path / "state.json")
    runner.handle(update("Мой CVV: 123"))
    assert runner.summary()["api_accepted"] == 1
    assert "CVV: 123" not in " ".join(tg.sent)


def test_inference_quota_persists_across_restart(tmp_path):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(total_tokens=9), choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content="Возьмите удобную обувь."))])
    provider = SimpleNamespace(ready=True, model="synthetic-model", client=SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    tg, path = FakeTelegram(), tmp_path / "state.json"
    runner = ClosedTest(settings(), tg, path, provider, max_ai_calls=1)
    runner.handle(update("/ask Что взять с собой?"))
    reopened = ClosedTest(settings(), tg, path, provider, max_ai_calls=1)
    reopened.handle(update("/ask Что взять с собой?", number=2))
    assert len(calls) == 1
    assert reopened.summary()["tokens"] == 9


def test_production_token_and_state_rebinding_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        Settings.load({"TEST_TELEGRAM_BOT_TOKEN": "100:synthetic", "TEST_BOT_USERNAME": "test",
                       "TEST_USER_ID": "42", "FORBIDDEN_PRODUCTION_BOT_ID": "100"})
    runner = ClosedTest(settings(), FakeTelegram(), tmp_path / "state.json")
    runner.save()
    with pytest.raises(ValueError):
        ClosedTest(Settings("201:synthetic", "another_bot", 42, 100), FakeTelegram(), runner.path)


def test_telegram_transport_blocks_other_recipients_and_mutating_webhook_methods():
    tg = Telegram(settings())
    try:
        with pytest.raises(ValueError):
            tg.call("sendMessage", chat_id=43, text="must not leave process")
        with pytest.raises(ValueError):
            tg.call("deleteWebhook")
    finally:
        tg.session.close()


@pytest.mark.parametrize("webhook_url", ["https://example.invalid/existing"])
def test_preflight_refuses_existing_webhook(monkeypatch, webhook_url):
    tg = Telegram(settings())
    monkeypatch.setattr(tg, "call", lambda method: {"id": 200, "username": "synthetic_test_bot", "is_bot": True}
                        if method == "getMe" else {"url": webhook_url})
    try:
        with pytest.raises(ValueError):
            tg.preflight()
    finally:
        tg.session.close()


@pytest.mark.parametrize("identity", [
    {"id": 100, "username": "synthetic_test_bot", "is_bot": True},
    {"id": 200, "username": "wrong_bot", "is_bot": True},
])
def test_preflight_rejects_production_or_unexpected_identity(monkeypatch, identity):
    tg = Telegram(settings())
    monkeypatch.setattr(tg, "call", lambda method: identity)
    try:
        with pytest.raises(ValueError):
            tg.preflight()
    finally:
        tg.session.close()


@pytest.mark.parametrize("result", [{"chat": {"id": 43}, "message_id": 5}, {"chat": {"id": 42}}])
def test_send_needs_matching_chat_and_message_receipt(monkeypatch, result):
    tg = Telegram(settings())
    monkeypatch.setattr(tg, "call", lambda *a, **kw: result)
    try:
        with pytest.raises(RuntimeError):
            tg.send("synthetic test")
    finally:
        tg.session.close()
