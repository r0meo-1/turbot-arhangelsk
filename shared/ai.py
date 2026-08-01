"""AI / template tour-blurb generation."""

from __future__ import annotations

import logging
from typing import Any, Optional

from shared.templates import template_selection

logger = logging.getLogger("turbot.shared.ai")


def generate_ai_selection(
    destination: str,
    dates: str,
    people: str,
    budget: str,
    *,
    ai_mode: str = "template",
    groq_client: Any = None,
    groq_model: str = "llama-3.3-70b-versatile",
    timeout: float = 20.0,
    log: Optional[logging.Logger] = None,
) -> str:
    """Generate a tour blurb via Groq or fall back to templates."""
    log = log or logger
    mode = (ai_mode or "template").lower().strip()

    if mode == "template":
        log.info("Template selection generated for '%s'", destination)
        return template_selection(destination, dates, people, budget)

    if not groq_client:
        log.warning("Groq client unavailable — using template fallback")
        return template_selection(destination, dates, people, budget)

    try:
        prompt = (
            "Ты — эксперт по туризму туристического агентства «АПРЕЛЬ тур».\n\n"
            "Клиент хочет:\n"
            f"- Направление: {destination}\n"
            f"- Даты: {dates}\n"
            f"- Количество человек: {people}\n"
            f"- Бюджет: {budget} рублей\n\n"
            "Напиши короткое (3-4 предложения), дружелюбное сообщение с:\n"
            "- Что ожидает в этом направлении\n"
            "- Почему это отличный выбор\n"
            "- Что взять с собой\n\n"
            "Используй эмодзи. Не упоминай цены и конкретные отели."
        )
        response = groq_client.chat.completions.create(
            model=groq_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.7,
            # Explicit timeout: without it a hung call leaks the background
            # thread that runs post-completion side effects.
            timeout=timeout,
        )
        ai_text = response.choices[0].message.content
        log.info("AI selection generated for '%s'", destination)
        return (
            "🌴 Ваша подборка туров\n\n"
            f"{ai_text}\n\n"
            "ℹ️ Наш менеджер скоро свяжется с вами для уточнения деталей!"
        )
    except Exception as exc:
        log.error("Error generating AI selection: %s", exc)
        return template_selection(destination, dates, people, budget)
