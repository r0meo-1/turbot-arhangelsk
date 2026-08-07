"""Input validation for tour-request dialog fields."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# Чанки вроде «8 месяцев» содержат цифру, которая возрастом не является —
# проверяются раньше, иначе младенец превратится в восьмилетнего.
_UNDER_ONE = re.compile(r"(до\s*год|меньше\s*год|полгод|месяц|мес\b|грудн|младен)", re.I)
_SPLIT_AGES = re.compile(r"[,;/]|\s+и\s+|\n")
_NO_KIDS = re.compile(
    r"\s*(0+|нет(\s+детей)?|нету|неа|без\s+детей|детей\s+нет|не\s+едут|[-—])\s*", re.I
)
_MAX_KIDS = 10
ADULT_FARE_FROM = 12   # с этого возраста тариф взрослый
INFANT_UNDER = 2       # младше — летит без места


def parse_kids_ages(text: str) -> Tuple[bool, List[int], str]:
    """Прочитать «5, 9» / «5 и 9» / «8 месяцев и 4» в список возрастов.

    Возвращает (ok, ages, problem). Возрасты — источник истины по количеству
    детей: клиент, выбравший «2» и написавший три возраста, себя поправил, и
    переспрашивать значит спорить с ним о том, сколько у него детей.
    """
    raw = (text or "").strip()
    if not raw:
        return False, [], "Напишите возрасты детей через запятую — например: 5, 9"

    # Отдельного вопроса «дети едут?» больше нет, поэтому «нет детей» — один из
    # допустимых ответов здесь. Одинокий 0 значит именно это: возраст младенца
    # выражается словами («до года», «8 месяцев»), а в списке вида «0, 5» ноль
    # снова читается как младенец.
    if _NO_KIDS.fullmatch(raw):
        return True, [], ""

    ages: List[int] = []
    for chunk in _SPLIT_AGES.split(raw):
        chunk = chunk.strip()
        if not chunk:
            continue
        if _UNDER_ONE.search(chunk):
            ages.append(0)
            continue
        found = re.findall(r"\d{1,2}", chunk)
        if not found:
            continue
        ages.extend(int(value) for value in found)

    if not ages:
        return False, [], "Не понял возрасты. Напишите числами через запятую — например: 5, 9"
    if len(ages) > _MAX_KIDS:
        return False, [], f"Слишком много значений. Максимум {_MAX_KIDS} детей в одной заявке."
    too_old = [a for a in ages if a > 17]
    if too_old:
        return False, [], (
            "Возраст ребёнка — до 17 лет. "
            f"Не похоже на возраст: {', '.join(str(a) for a in too_old)}. "
            "Если летят только взрослые, вернитесь назад и выберите «Без детей»."
        )
    return True, ages, ""


def ages_to_db(ages) -> Optional[str]:
    """[5, 9] → "5,9". Читается в sqlite3-шелле, где и разбирают вопрос
    менеджера «почему ребёнка посчитали взрослым»; JSON тут только мешал бы."""
    if not ages:
        return None
    return ",".join(str(int(a)) for a in ages)


def ages_from_db(raw) -> List[int]:
    """"5,9" → [5, 9]. Терпимо к NULL и мусору: упавшая загрузка сессии
    выкидывает клиента из его же диалога, что хуже потерянных возрастов."""
    if not raw:
        return []
    return [int(part) for part in str(raw).split(",") if part.strip().isdigit()]


def party_bands(info: Dict) -> Tuple[int, int, int]:
    """(взрослые, дети 2–11, младенцы) — так тарифицируют авиакомпании.

    Считается из точных возрастов, а не из отдельных счётчиков: два вопроса
    («сколько детей» и «сколько младенцев») могли противоречить друг другу,
    один список возрастов — не может. Подросток от 12 лет попадает во
    взрослые, потому что тариф у него взрослый, сколько бы раз клиент ни
    назвал его ребёнком.
    """
    try:
        adults = int(str(info.get("people", 1)).rstrip("+") or 1)
    except (TypeError, ValueError):
        adults = 1
    ages = info.get("kids_ages") or []
    if not ages:
        # Заявки, собранные до появления шага с возрастами.
        return adults, int(info.get("kids") or 0), int(info.get("infants") or 0)
    return (
        adults + sum(1 for a in ages if a >= ADULT_FARE_FROM),
        sum(1 for a in ages if INFANT_UNDER <= a < ADULT_FARE_FROM),
        sum(1 for a in ages if a < INFANT_UNDER),
    )


def _years(n: int) -> str:
    """год / года / лет. «4 лет» в сообщении живому клиенту читается как брак."""
    if n % 100 in (11, 12, 13, 14):
        return "лет"
    if n % 10 == 1:
        return "год"
    if n % 10 in (2, 3, 4):
        return "года"
    return "лет"


def _join_ru(items: List[str]) -> str:
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " и " + items[-1]


def format_ages(ages: List[int]) -> str:
    """[5, 9] → «5 и 9 лет»; [0, 4] → «до года и 4 года»."""
    ages = sorted(ages)
    if ages and all(a >= 1 for a in ages):
        # Единица измерения одна на весь список — «5 лет, 9 лет» звучит как
        # опись имущества, а не как ответ на вопрос о детях.
        return _join_ru([str(a) for a in ages]) + " " + _years(ages[-1])
    return _join_ru(["до года" if a == 0 else f"{a} {_years(a)}" for a in ages])


def party_text(info: Dict) -> str:
    """«2 взр. + дети: 5 и 9 лет» — менеджер считает по точному возрасту."""
    ages = info.get("kids_ages") or []
    try:
        declared_adults = int(str(info.get("people", 1)).rstrip("+") or 1)
    except (TypeError, ValueError):
        declared_adults = 1
    parts = [f"{declared_adults} взр."]
    if ages:
        parts.append(f"дети: {format_ages(ages)}")
    else:
        _, children, infants = party_bands(info)
        if children:
            parts.append(f"{children} реб. (2–11)")
        if infants:
            parts.append(f"{infants} млад. (до 2)")
    return " + ".join(parts)


def validate_phone(text: str) -> Tuple[bool, Optional[str]]:
    """Validate a Russian phone number. Returns (ok, normalised E.164-like)."""
    digits = re.sub(r"[^\d+]", "", text).lstrip("+")
    if digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return True, "+" + digits
    if len(digits) == 10:
        return True, "+7" + digits
    return False, None


def validate_people(text: str) -> Tuple[bool, Optional[str]]:
    """Validate number of travellers. Returns (ok, value_str)."""
    cleaned = text.strip()
    if cleaned == "5+":
        return True, "5+"
    try:
        value = int(re.sub(r"[^\d]", "", cleaned))
        if 1 <= value <= 50:
            return True, str(value)
    except (ValueError, TypeError):
        pass
    return False, None


def validate_budget(text: str) -> Tuple[bool, Optional[int]]:
    """Parse budget as a positive integer. Returns (ok, value)."""
    try:
        value = int(re.sub(r"[^\d]", "", text))
        if value > 0:
            return True, value
    except (ValueError, TypeError):
        pass
    return False, None
