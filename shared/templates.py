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
    """Generate a tour blurb from templates (no external AI required)."""
    dest_lower = destination.lower()
    intro: Optional[str] = None
    for keyword, text in TEMPLATE_INTROS.items():
        if keyword in dest_lower:
            intro = text
            break
    if intro is None:
        intro = f"{destination} — отличное направление для вашего отдыха."

    return (
        "🌴 Ваша подборка туров\n\n"
        f"📍 {destination}: {intro}\n\n"
        f"Поездка на {dates} для {people} человек — хороший выбор, "
        f"чтобы успеть всё и при этом отдохнуть. Бюджет {budget}₽ на человека "
        f"позволяет подобрать комфортный вариант.\n\n"
        f"💡 {TEMPLATE_PACKING}\n\n"
        "ℹ️ Наш менеджер скоро свяжется с вами для уточнения деталей!"
    )
