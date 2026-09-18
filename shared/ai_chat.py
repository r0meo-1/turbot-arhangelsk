"""Feature-flagged free-form AI travel chat.

This module intentionally has no public HTTP or bot route. Callers must opt in
explicitly and provide the model client. High-risk topics are handled
deterministically before any external model call.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Optional

from shared.ai_guardrails import (
    TRAVEL_ASSISTANT_SYSTEM_PROMPT,
    classify_unverified_ai_output,
    guard_external_ai_message,
    restricted_topic_handoff,
)

logger = logging.getLogger("turbot.shared.ai_chat")


@dataclass(frozen=True)
class AIChatReply:
    text: str
    used_external_model: bool
    handoff_required: bool
    reason: str = ""
    topic: Optional[str] = None


_DISABLED_TEXT = (
    "ИИ-чат пока работает только в тестовом режиме. "
    "По заявке поможет менеджер «АПРЕЛЬ тур»."
)

_SENSITIVE_TEXT = (
    "Не отправляйте сюда данные банковской карты, паспорт, пароли или токены. "
    "Для продолжения напишите только параметры поездки без этих данных."
)

_FALLBACK_TEXT = (
    "Сейчас ИИ-помощник недоступен. "
    "Параметры поездки можно продолжить оформлять без него, менеджер подключится при необходимости."
)

_UNVERIFIED_FACT_TEXT = (
    "Цена, наличие и другие условия должны приходить из проверенного источника. "
    "ИИ не будет выдавать непроверенный факт. Менеджер уточнит актуальные данные."
)


def generate_ai_chat_reply(
    message: object,
    *,
    enabled: bool = False,
    groq_client: Any = None,
    groq_model: str = "openai/gpt-oss-120b",
    timeout: float = 20.0,
    log: Optional[logging.Logger] = None,
) -> AIChatReply:
    """Return a guarded travel-chat answer.

    Public callers should keep enabled=False until provider/data-flow review
    and the launch gates are complete. Restricted topics never reach the model.
    """
    log = log or logger

    if not enabled:
        return AIChatReply(
            text=_DISABLED_TEXT,
            used_external_model=False,
            handoff_required=False,
            reason="feature_disabled",
        )

    decision = guard_external_ai_message(message)

    if decision.reason == "empty_message":
        return AIChatReply(
            text="Напишите вопрос о поездке.",
            used_external_model=False,
            handoff_required=False,
            reason=decision.reason,
        )

    if decision.reason == "sensitive_data":
        return AIChatReply(
            text=_SENSITIVE_TEXT,
            used_external_model=False,
            handoff_required=True,
            reason=decision.reason,
        )

    if not decision.allow_external_model:
        return AIChatReply(
            text=restricted_topic_handoff(decision.topic),
            used_external_model=False,
            handoff_required=True,
            reason=decision.reason,
            topic=decision.topic,
        )

    if not groq_client:
        return AIChatReply(
            text=_FALLBACK_TEXT,
            used_external_model=False,
            handoff_required=False,
            reason="provider_unavailable",
        )

    try:
        response = groq_client.chat.completions.create(
            model=groq_model,
            messages=[
                {"role": "system", "content": TRAVEL_ASSISTANT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Ответь как туристический ИИ-помощник. "
                        "Не добавляй цены, наличие, визовые, юридические, страховые "
                        "или медицинские факты, если они не были переданы как проверенные данные.\n\n"
                        f"Вопрос пользователя: {decision.safe_text}"
                    ),
                },
            ],
            max_tokens=450,
            temperature=0.3,
            timeout=timeout,
        )
        content = str(response.choices[0].message.content or "").strip()
        if not content:
            raise ValueError("empty AI chat response")

        output_reason = classify_unverified_ai_output(content)
        if output_reason:
            topic = None
            text = _UNVERIFIED_FACT_TEXT
            if output_reason == "unverified_visa_or_entry_claim":
                topic = "visa_or_entry"
                text = restricted_topic_handoff(topic)
            elif output_reason == "unverified_legal_or_refund_claim":
                topic = "legal_or_contract"
                text = restricted_topic_handoff(topic)
            return AIChatReply(
                text=text,
                used_external_model=True,
                handoff_required=True,
                reason=output_reason,
                topic=topic,
            )

        return AIChatReply(
            text=content,
            used_external_model=True,
            handoff_required=False,
        )
    except Exception as exc:
        log.error("AI chat provider failed: %s", exc)
        return AIChatReply(
            text=_FALLBACK_TEXT,
            used_external_model=False,
            handoff_required=False,
            reason="provider_error",
        )
