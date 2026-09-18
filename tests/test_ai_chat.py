from types import SimpleNamespace

from shared.ai_chat import generate_ai_chat_reply


class _FakeCompletions:
    def __init__(self, captured, content="Безопасный ответ о поездке."):
        self.captured = captured
        self.content = content

    def create(self, **kwargs):
        self.captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class _FakeGroq:
    def __init__(self, captured, content="Безопасный ответ о поездке."):
        self.chat = SimpleNamespace(
            completions=_FakeCompletions(captured, content=content)
        )


class _FailingCompletions:
    def create(self, **kwargs):
        raise RuntimeError("provider down")


class _FailingGroq:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FailingCompletions())


def test_ai_chat_is_disabled_by_default_and_never_calls_provider():
    captured = {}
    reply = generate_ai_chat_reply(
        "Что взять в Таиланд?",
        groq_client=_FakeGroq(captured),
    )
    assert reply.used_external_model is False
    assert reply.reason == "feature_disabled"
    assert captured == {}


def test_restricted_topic_hands_off_without_model_call():
    captured = {}
    reply = generate_ai_chat_reply(
        "Вернут ли мне деньги по договору?",
        enabled=True,
        groq_client=_FakeGroq(captured),
    )
    assert reply.used_external_model is False
    assert reply.handoff_required is True
    assert reply.topic == "legal_or_contract"
    assert captured == {}


def test_sensitive_data_is_blocked_before_model_call():
    captured = {}
    reply = generate_ai_chat_reply(
        "Мой паспорт 12 34 567890, что дальше?",
        enabled=True,
        groq_client=_FakeGroq(captured),
    )
    assert reply.used_external_model is False
    assert reply.handoff_required is True
    assert reply.reason == "sensitive_data"
    assert captured == {}


def test_safe_question_is_redacted_before_external_model():
    captured = {}
    reply = generate_ai_chat_reply(
        "Что взять на Пхукет? Ответ на test@example.com",
        enabled=True,
        groq_client=_FakeGroq(captured),
    )
    assert reply.used_external_model is True
    assert reply.handoff_required is False
    assert reply.text == "Безопасный ответ о поездке."
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "test@example.com" not in messages[1]["content"]
    assert "[email-redacted]" in messages[1]["content"]
    assert captured["temperature"] == 0.3


def test_missing_provider_degrades_without_losing_the_flow():
    reply = generate_ai_chat_reply(
        "Что взять в поездку?",
        enabled=True,
        groq_client=None,
    )
    assert reply.used_external_model is False
    assert reply.handoff_required is False
    assert reply.reason == "provider_unavailable"
    assert reply.text


def test_provider_failure_degrades_without_raw_error_to_user():
    reply = generate_ai_chat_reply(
        "Что посмотреть на Пхукете?",
        enabled=True,
        groq_client=_FailingGroq(),
    )
    assert reply.used_external_model is False
    assert reply.reason == "provider_error"
    assert "provider down" not in reply.text


def test_empty_model_output_uses_safe_fallback():
    reply = generate_ai_chat_reply(
        "Что посмотреть на Пхукете?",
        enabled=True,
        groq_client=_FakeGroq({}, content="   "),
    )
    assert reply.used_external_model is False
    assert reply.reason == "provider_error"
