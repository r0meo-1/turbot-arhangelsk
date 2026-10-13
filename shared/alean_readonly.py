"""Read-only domestic search contract. Demo fixtures never call a supplier."""
from datetime import date
import os
import re

import requests

from shared import alean

DESTINATIONS = ("Сочи", "Адлер", "Анапа", "Геленджик", "Кисловодск", "Россия")
NOTICE = "Бронирование, оплата и отправка заявки отключены. Цена и наличие не подтверждены."
DEMO_NOTICE = "ДЕМО: вымышленные отели, цены и наличие; это не предложения Алеан."


class ValidationError(ValueError):
    pass


def _integer(value, low, high, label):
    if isinstance(value, bool) or len(str(value)) > 8 or not re.fullmatch(r"\d+", str(value)):
        raise ValidationError(f"{label}: укажите целое число от {low} до {high}.")
    number = int(value)
    if not low <= number <= high:
        raise ValidationError(f"{label}: укажите целое число от {low} до {high}.")
    return number


def validate_query(raw):
    if not isinstance(raw, dict):
        raise ValidationError("Параметры поиска должны быть объектом.")
    kind = raw.get("kind", "tour")
    if kind not in ("hotel", "tour"):
        raise ValidationError("Выберите отель или тур.")
    destination = next((name for name in DESTINATIONS
                        if name.casefold() == str(raw.get("destination", "")).strip().casefold()), None)
    if not destination:
        raise ValidationError("Выберите направление в России: " + ", ".join(DESTINATIONS) + ".")
    try:
        arrival = date.fromisoformat(str(raw.get("arrival", "")))
        departure = date.fromisoformat(str(raw.get("departure", "")))
    except ValueError:
        raise ValidationError("Укажите даты заезда и выезда в формате ГГГГ-ММ-ДД.") from None
    nights = (departure - arrival).days
    if arrival < date.today() or not 1 <= nights <= 28:
        raise ValidationError("Заезд не в прошлом; выезд через 1–28 ночей после заезда.")
    adults = _integer(raw.get("adults"), 1, 8, "Взрослые")
    ages = raw.get("children_ages", [])
    if not isinstance(ages, list) or len(ages) > 6:
        raise ValidationError("Укажите возраст каждого ребёнка, не более шести детей.")
    ages = [_integer(age, 0, 17, "Возраст ребёнка") for age in ages]
    budget = _integer(raw.get("budget"), 0, 10000000, "Бюджет на всех, ₽")
    origin = str(raw.get("origin", "Москва")).strip()
    if not 2 <= len(origin) <= 100 or any(ord(c) < 32 for c in origin):
        raise ValidationError("Укажите город вылета (2–100 символов).")
    return dict(kind=kind, destination=destination, arrival=arrival.isoformat(),
                departure=departure.isoformat(), adults=adults, children_ages=ages,
                budget=budget, origin=origin, nights=nights)


def search(raw, *, mode="demo", scenario="success", session=None):
    """No customer identity, persistence, leads, booking, or manager notifications."""
    query = validate_query(raw)
    if mode not in ("demo", "live"):
        raise ValidationError("Неизвестный режим поиска.")
    result = dict(ok=True, state="results", mode=mode, query=query, offers=[],
                  notice=(DEMO_NOTICE + " " if mode == "demo" else "") + NOTICE,
                  booking_enabled=False, submission_enabled=False)
    if mode == "demo":
        if scenario not in ("success", "empty", "timeout", "error"):
            raise ValidationError("Неизвестный демо-сценарий.")
        if scenario in ("timeout", "error"):
            return {**result, "ok": False, "state": scenario,
                    "error": "Демо: время ожидания истекло." if scenario == "timeout"
                             else "Демо: провайдер временно недоступен."}
        if scenario != "empty":
            base = query["nights"] * (query["adults"] * 3800 + len(query["children_ages"]) * 1800)
            if query["kind"] == "tour":
                base += query["adults"] * 20000
            for index, name in enumerate(("Морской бриз", "Солнечный двор", "Тихая гавань")):
                price = base + index * 5000
                if query["budget"] and price > query["budget"]:
                    continue
                result["offers"].append(dict(id=f"demo-{index}", name=f"Демо • {name} (вымышленный)",
                    location=query["destination"], arrival=query["arrival"], departure=query["departure"],
                    price=price, currency="RUB", availability="Демо: условное наличие",
                    confirmed=False, booking_url=None, mocked=True))
    else:
        settings = alean.AleanSettings.from_env()
        if (os.getenv("ALEAN_READONLY_LIVE_ENABLED", "false").lower() not in ("true", "1", "yes")
                or not settings.enabled or not settings.username or not settings.password):
            return {**result, "ok": False, "state": "unavailable",
                    "error": "Живой поиск не включён или не настроен. Используйте демо."}
        if query["kind"] == "hotel":
            return {**result, "ok": False, "state": "unavailable",
                    "error": "Живой поиск только отелей ещё не проверен. Доступны демо и пакетные туры."}
        supplier_query = dict(destination=query["destination"], origin=query["origin"],
            dates=query["arrival"], dates_are_trip=False, nights=query["nights"],
            people=query["adults"], kids_ages=query["children_ages"],
            budget=query["budget"], budget_scope="total")
        try:
            if session is None:
                with requests.Session() as transport:
                    found = alean.search_tours(settings, transport, supplier_query)
            else:
                found = alean.search_tours(settings, session, supplier_query)
        except Exception:
            return {**result, "ok": False, "state": "error", "error": "Алеан временно недоступен."}
        if found.error:
            state = "timeout" if found.error == "Alean: timeout" else "error"
            return {**result, "ok": False, "state": state,
                    "error": "Алеан: время ожидания истекло." if state == "timeout"
                             else "Алеан временно недоступен. Попробуйте позже."}
        for offer in found.offers[:15]:
            result["offers"].append(dict(id=offer.tour_id, name=offer.hotel, location=offer.region,
                arrival=query["arrival"], departure=query["departure"], price=offer.price,
                currency=offer.currency, availability="Наличие требует проверки менеджером",
                confirmed=False, booking_url=None, mocked=False))
        result["notice"] += " Трансфер не включён. SOAP Specify и ссылка бронирования не проверены."
    if not result["offers"]:
        result["state"] = "empty"
    return result
