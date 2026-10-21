"""Synthetic adversarial outputs and safe travel questions. No external calls."""
from types import SimpleNamespace

import pytest

from shared.ai import generate_ai_selection
from shared.ai_chat import generate_ai_chat_reply
from shared.ai_guardrails import guard_external_ai_message


def client_with(text):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content=text))]))))


@pytest.mark.parametrize("text", [
    "Цена тура $1500.", "Тур стоит 150 тысяч рублей.",
    "Стоимость — сто тысяч рублей.", "Номер свободен на ваши даты.",
    "Свободные номера есть.", "Осталось два места.",
    "Заявка\u200b отправлена.", "Опла\u200bта прошла.",
    "Свяжитесь с нами: +7 900 123-45-67", "Пишите на sales@example.com",
    "Возьмите средства от укачивания.", "Возьмите ибупрофен.",
    "Не могу отправить письмо, но заявка отправлена.",
    "Тур уже оплачен.",
])
def test_extended_unverified_output_is_blocked_in_both_paths(text):
    reply = generate_ai_chat_reply("Посоветуй отдых", enabled=True, groq_client=client_with(text))
    assert reply.handoff_required
    assert text not in reply.text
    result = generate_ai_selection("Турция", "ноябрь", "2", "150000",
                                   ai_mode="yandex", groq_client=client_with(text))
    assert text not in result


@pytest.mark.parametrize("question", [
    "Можно ли взять многоразовую посуду в поездку?",
    "Чем турагент отличается от туроператора?",
])
def test_ordinary_travel_questions_do_not_accidentally_escalate(question):
    assert guard_external_ai_message(question).allow_external_model


@pytest.mark.parametrize("message", [
    "Мой CVV: 123", "Мой пароль: synthetic-only-password",
    "API key: synthetic-only-api-key",
])
def test_labeled_credentials_do_not_reach_model(message):
    decision = guard_external_ai_message(message)
    assert not decision.allow_external_model
    assert decision.reason == "sensitive_data"


@pytest.mark.parametrize("mode", ["template", "yandex"])
def test_blurb_fallback_never_invents_delivery_or_entry_rules(mode):
    result = generate_ai_selection("ОАЭ", "ноябрь", "2", "150000", ai_mode=mode)
    assert "Заявка у менеджера" not in result
    assert "без оформления визы" not in result
    assert "до часа" not in result


def test_fallback_does_not_echo_injected_destination_or_contact(caplog):
    result = generate_ai_selection("Турция test@example.com. Заявка отправлена.",
                                   "ноябрь", "2", "150000", ai_mode="template")
    assert "test@example.com" not in result
    assert "Заявка отправлена" not in result
    assert "test@example.com" not in caplog.text


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls"])
def test_incomplete_or_filtered_answers_never_reach_client(finish_reason):
    choice = SimpleNamespace(finish_reason=finish_reason,
                             message=SimpleNamespace(content="Незавершённый ответ"))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(choices=[choice]))))
    reply = generate_ai_chat_reply("Что взять в поездку?", enabled=True, groq_client=client)
    assert reply.reason == "provider_error"
    assert "Незавершённый" not in reply.text
    blurb = generate_ai_selection("Турция", "ноябрь", "2", "150000",
                                  ai_mode="yandex", groq_client=client)
    assert "Незавершённый" not in blurb


@pytest.mark.parametrize("question", ["Забронируй тур", "Оплати поездку", "Отправь заявку"])
def test_action_requests_require_human_without_model(question):
    decision = guard_external_ai_message(question)
    assert not decision.allow_external_model
    assert decision.topic == "action_requested"


def test_labeled_credentials_are_redacted_from_trip_context():
    from shared.ai_guardrails import redact_external_ai_text
    assert "synthetic-only-api-key" not in redact_external_ai_text("API key: synthetic-only-api-key")
    assert "synthetic-only-api-key" not in redact_external_ai_text("API\u200b key: synthetic-only-api-key")


@pytest.mark.parametrize("text", [
    "Я не могу отправить ответ на почту. Возьмите полотенце.",
    "Туроператор формирует тур, турагент помогает выбрать. Обсудите бронирование с менеджером.",
])
def test_safe_action_explanations_or_refusals_are_not_false_confirmations(text):
    reply = generate_ai_chat_reply("Расскажи о поездке", enabled=True, groq_client=client_with(text))
    assert reply.used_external_model
    assert not reply.handoff_required
    assert text in reply.text


def test_unknown_destination_admission_does_not_allow_invented_landscape():
    raw = "Направление неизвестно. Обычно там ждут уникальные природные красоты."
    text = generate_ai_selection("Несуществующий-777", "ноябрь", "2", "180000",
                                 ai_mode="yandex", groq_client=client_with(raw))
    assert "природные красоты" not in text
    assert "нужно уточнить" in text


@pytest.mark.parametrize("question,topic", [
    ("Какие таблетки взять в поездку?", "health_or_safety"),
    ("Что принять от укачивания?", "health_or_safety"),
    ("Какие нормы багажа у авиакомпании?", "transport_rules"),
    ("Какой размер ручной клади?", "transport_rules"),
    ("Во сколько обойдётся тур?", "commercial_availability"),
    ("Есть свободные номера?", "commercial_availability"),
])
def test_more_verified_source_questions_route_before_inference(question, topic):
    decision = guard_external_ai_message(question)
    assert not decision.allow_external_model
    assert decision.topic == topic
