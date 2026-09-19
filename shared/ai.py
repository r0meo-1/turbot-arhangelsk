"""AI / template tour-blurb generation."""

from __future__ import annotations

import logging
from typing import Any, Optional

from shared.templates import template_selection
from shared.ai_guardrails import TRAVEL_ASSISTANT_SYSTEM_PROMPT, redact_external_ai_text

logger = logging.getLogger("turbot.shared.ai")


def generate_ai_selection(
    destination: str,
    dates: str,
    people: str,
    budget: str,
    *,
    ai_mode: str = "template",
    groq_client: Any = None,
    groq_model: str = "openai/gpt-oss-120b",
    timeout: float = 20.0,
    log: Optional[logging.Logger] = None,
) -> str:
    """Generate a tour blurb via an approved provider or fall back to templates."""
    log = log or logger
    mode = (ai_mode or "template").lower().strip()

    if mode == "template":
        log.info("Template selection generated for '%s'", destination)
        return template_selection(destination, dates, people, budget)

    if mode not in {"groq", "regcloud"}:
        log.warning("Unknown AI mode '%s' — using template fallback", mode)
        return template_selection(destination, dates, people, budget)

    if not groq_client:
        log.warning("%s AI client unavailable — using template fallback", mode)
        return template_selection(destination, dates, people, budget)

    try:
        safe_destination = redact_external_ai_text(destination)
        safe_dates = redact_external_ai_text(dates)
        safe_people = redact_external_ai_text(people)
        safe_budget = redact_external_ai_text(budget)
        prompt = (
            "Клиент хочет:\n"
            f"- Направление: {safe_destination}\n"
            f"- Даты: {safe_dates}\n"
            f"- Количество человек: {safe_people}\n"
            f"- Бюджет: {safe_budget} рублей\n\n"
            "Напиши короткое (3-4 предложения), дружелюбное сообщение с:\n"
            "- Что обычно ожидает турист в этом направлении\n"
            "- Почему направление может подойти под такой запрос\n"
            "- Что обычно полезно взять с собой\n\n"
            "Не называй конкретные отели и не добавляй непроверенные коммерческие факты."
        )
        response = groq_client.chat.completions.create(
            model=groq_model,
            messages=[
                {"role": "system", "content": TRAVEL_ASSISTANT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=300,
            temperature=0.4,
            # Explicit timeout: without it a hung call leaks the background
            # thread that runs post-completion side effects.
            timeout=timeout,
        )
        ai_text = str(response.choices[0].message.content or "").strip()
        if not ai_text:
            raise ValueError("empty AI response")
        log.info("%s AI selection generated for '%s'", mode, destination)
        # Not «подборка туров»: the bot has no hotels, transfers or packages —
        # Tutu returns flights only. Promising a tour and delivering a
        # paragraph about the destination is the kind of overclaim a client
        # notices immediately.
        return (
            f"🌴 О направлении\n\n{ai_text}\n\n"
            "ℹ️ Это предварительная информационная подсказка. "
            "Конкретные условия тура подтвердит менеджер."
        )
    except Exception as exc:
        log.error("Error generating AI selection: %s", exc)
        return template_selection(destination, dates, people, budget)
