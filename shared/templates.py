"""Template-based tour blurbs (no external AI required)."""

from __future__ import annotations

from typing import Dict, Optional

AI_FALLBACK_MESSAGE = (
    "🌴 Спасибо за заявку! Наш менеджер подберёт для вас\n"
    "лучшие варианты туров и свяжется с вами в ближайшее время."
)

TEMPLATE_INTROS: Dict[str, str] = {
    "египет": (
        "Вас ждут древние пирамиды, кристально чистое Красное море и «всё включённое» "
        "на любом вкус."
    ),
    "турция": (
        "Отличное сочетание пляжного отдыха, богатой истории и гостеприимной кухни "
        "с all inclusive на побережье."
    ),
    "таиланд": (
        "Экзотическая природа, белоснежные пляжи, буддийские храмы и доступный "
        "комфортный отдых для всей семьи."
    ),
    "мальдивы": (
        "Райские острова с бирюзовой лагуной, bungalow над водой и идеальной "
        "атмосферой для романтического getaway."
    ),
    "оаэ": (
        "Современный комфорт, роскошные отели, шопинг и пляжи с тёплым морем — "
        "и всё это без визового оформления."
    ),
}

TEMPLATE_PACKING = (
    "Возьмите с собой удобную обувь, купальные принадлежности, солнцезащитный крем "
    "и хорошее настроение."
)


def template_selection(destination: str, dates: str, people: str, budget: str) -> str:
    """Fallback text when no real offers are available.

    Deliberately not called «подборка туров» any more. It contained no offers,
    no prices and no dates the client did not already type in — a paragraph of
    filler under a heading that promised results. Worse than saying nothing,
    because it reads as a bot padding for the sake of a reply.

    What a client actually wants here is to know their request landed and when
    someone will answer. A destination note is kept only where there is
    something real to say.
    """
    dest_lower = (destination or "").lower()
    intro: Optional[str] = None
    for keyword, text in TEMPLATE_INTROS.items():
        if keyword in dest_lower:
            intro = text
            break

    parts = ["✅ Заявка у менеджера."]
    if intro:
        parts.append(f"📍 {destination}. {intro}")
    parts.append(
        "Менеджер подберёт варианты под ваши даты и бюджет и напишет сюда. "
        "В рабочее время это обычно занимает до часа."
    )
    return "\n\n".join(parts)
