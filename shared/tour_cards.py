"""Bounded client-facing tour facts; no search, booking or enquiry effects."""
from shared import tourvisor as tv


def _text(value, limit=100):
    return ' '.join(str(value or '').split())[:limit] or 'не указано'


def facts(offer, info=None):
    info = info or {}
    price = offer.get('price') or 0
    charge = offer.get('fuel_charge') or 0
    currency = offer.get('currency') or 'RUB'
    total = price + charge
    price_text = tv._format_price(total, currency) if price > 0 else 'не указана'
    if charge and price > 0:
        price_text += f' (включая сбор {tv._format_price(charge, currency)})'
    return {
        'Цена на всех': price_text,
        'Курорт': _text(offer.get('region')),
        'Категория': f"{offer['category']}★" if offer.get('category') else 'не указана',
        'Вылет': _text(offer.get('departure') or info.get('origin')),
        'Дата': _text(tv.display_date(offer['date'])) if offer.get('date') else 'не указана',
        'Ночи': str(offer.get('nights') or 'не указаны'),
        'Питание': _text(tv.meal_label(offer['meal'])) if offer.get('meal') else 'не указано',
        'Номер': _text(offer.get('room')),
        'Туроператор': _text(offer.get('operator')),
    }


def party(info):
    from shared.validation import ages_from_db
    ages = info.get('kids_ages') or []
    if not isinstance(ages, (tuple, list)):
        ages = ages_from_db(ages)
    parts = [f"{info.get('people') or '?'} взр."]
    if ages:
        parts.append('дети: ' + ', '.join(map(str, ages)) + ' лет')
    elif info.get('kids'):
        parts.append(f"{info['kids']} дет.")
    if info.get('infants') and not ages:
        parts.append(f"{info['infants']} мл.")
    return ' · '.join(parts)


def card(offer, info=None, *, number=1):
    values = facts(offer, info)
    stars = f" {offer['category']}★" if offer.get('category') else ''
    lines = [f"{number}. {_text(offer.get('hotel'))}{stars}"]
    if offer.get('region'):
        lines.append('📍 ' + _text(offer['region']))
    rating = []
    if offer.get('rating'):
        rating.append(f"⭐ {offer['rating']}/5")
    if offer.get('reviews_pct'):
        rating.append(f"{offer['reviews_pct']}% рекомендуют")
    if offer.get('beach_line'):
        rating.append('🏖 ' + _text(offer['beach_line'], 50))
    if rating:
        lines.append(' · '.join(rating))
    lines.extend([
        f"💰 {values['Цена на всех']} — на всех туристов",
        f"🛫 {values['Вылет']} · {values['Дата']} · {values['Ночи']} ноч.",
        f"🍽 {values['Питание']}", f"🛏 {values['Номер']}",
        f"Туроператор: {values['Туроператор']}",
    ])
    if info:
        lines.append('👥 ' + party(info))
    if offer.get('provider') == 'alean':
        lines.append('Отель и авиабилеты; трансфер не включён.')
    lines.append('Цена предварительная; наличие и итоговую стоимость нужно проверить.')
    return '\n'.join(lines)


def results(offers, info, *, start_index=1):
    return '\n\n'.join(card(offer, info, number=i)
                       for i, offer in enumerate(offers, start_index))


def comparison(offers, info=None):
    """Compare facts, never rank unknown prices or different currencies."""
    rows = [facts(offer, info) for offer in offers]
    lines = ['⚖️ Сравнение туров', 'Цены предварительные; наличие нужно проверить заново.']
    if info:
        lines.append('👥 Состав: ' + party(info))
    for i, offer in enumerate(offers, 1):
        stars = f" {offer['category']}★" if offer.get('category') else ''
        lines.append(f"№{i}: {_text(offer.get('hotel'), 70)}{stars}")
    differing = [key for key in rows[0] if len({row[key] for row in rows}) > 1]
    for heading, keys in [('Различия', differing), ('Совпадает', [key for key in rows[0] if key not in differing])]:
        if not keys:
            continue
        lines.append('\n' + heading + ':')
        for key in keys:
            if key in differing:
                lines.append(key + ': ' + ' | '.join(f'№{i} — {row[key]}' for i, row in enumerate(rows, 1)))
            else:
                lines.append(key + ': ' + rows[0][key])
    if len({offer.get('currency') or 'RUB' for offer in offers}) == 1 and all((offer.get('price') or 0) > 0 for offer in offers):
        totals = [(offer.get('price') or 0) + (offer.get('fuel_charge') or 0) for offer in offers]
        spread = max(totals) - min(totals)
        if spread:
            lines.append('\nРазница между максимальной и минимальной ценой: ' + tv._format_price(spread, offers[0].get('currency') or 'RUB'))
    if any(offer.get('provider') == 'alean' for offer in offers):
        lines.append('Алеан: отель и авиабилеты; трансфер не включён.')
    lines.append('Заявка менеджеру не отправлена.')
    return '\n'.join(lines)
