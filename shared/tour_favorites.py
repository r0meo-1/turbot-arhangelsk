"""Private, draft-scoped shortlist. No supplier or enquiry side effects."""
import hashlib
import json

from shared import tourvisor
from shared.validation import ages_from_db


def scope(info):
    fields = ('destination', 'origin', 'dates', 'nights', 'people', 'kids',
              'kids_ages', 'infants', 'budget', 'budget_scope', 'direct_only', 'hotel_query')
    values = {k: str(info.get(k) or '') for k in fields}
    ages = info.get('kids_ages')
    values['kids_ages'] = list(ages) if isinstance(ages, (list, tuple)) else ages_from_db(ages)
    values['direct_only'] = bool(info.get('direct_only'))
    values['dates_are_trip'] = bool(info.get('dates_are_trip'))
    return hashlib.sha256(json.dumps(values,
        sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def offer_key(offer):
    return hashlib.sha256(json.dumps(offer, sort_keys=True,
        ensure_ascii=False, default=str).encode()).hexdigest()[:24]


def _init(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS tour_favorites (
        user_id INTEGER NOT NULL, scope TEXT NOT NULL, offer_key TEXT NOT NULL,
        offer TEXT NOT NULL, PRIMARY KEY(user_id, scope, offer_key))''')


def clear(cur, user_id):
    _init(cur)
    cur.execute('DELETE FROM tour_favorites WHERE user_id=?', (user_id,))


def items(cursor, user_id, info):
    with cursor(commit=True) as cur:
        _init(cur)
        return [json.loads(row[0]) for row in cur.execute(
            'SELECT offer FROM tour_favorites WHERE user_id=? AND scope=? ORDER BY rowid',
            (user_id, scope(info))).fetchall()]


def change(cursor, user_id, info, offer=None, *, remove=None):
    """Explicit add/remove makes duplicate deliveries idempotent, unlike toggle."""
    draft = scope(info)
    with cursor(commit=True) as cur:
        _init(cur)
        cur.execute('DELETE FROM tour_favorites WHERE user_id=? AND scope<>?', (user_id, draft))
        if remove:
            cur.execute('DELETE FROM tour_favorites WHERE user_id=? AND scope=? AND offer_key=?',
                        (user_id, draft, remove))
            return 'removed'
        if not offer:
            return 'missing'
        key = offer_key(offer)
        if cur.execute('SELECT 1 FROM tour_favorites WHERE user_id=? AND scope=? AND offer_key=?',
                       (user_id, draft, key)).fetchone():
            return 'exists'
        count = cur.execute('SELECT count(*) FROM tour_favorites WHERE user_id=? AND scope=?',
                            (user_id, draft)).fetchone()[0]
        if count >= 3:
            return 'full'
        cur.execute('INSERT INTO tour_favorites VALUES (?,?,?,?)',
                    (user_id, draft, key, json.dumps(offer, ensure_ascii=False)))
        return 'added'


def render(offers, *, compare=False):
    if compare and len(offers) < 2:
        return 'Для сравнения добавьте в избранное 2–3 тура.'
    if not offers:
        return '⭐ Избранное пока пусто. Откройте предложение и добавьте его в избранное.'
    title = '⚖️ Сравнение туров' if compare else '⭐ Избранное'
    lines = [title + f' ({len(offers)}/3)',
             'Сохранённые цены предварительные; наличие и итоговую стоимость нужно проверить заново.']
    for number, offer in enumerate(offers, 1):
        lines.append(tourvisor.format_client_message(
            tourvisor.SearchResult(offers=[tourvisor.TourOffer(**offer)]),
            max_len=950, start_index=number))
    lines.append('Цены — на всех туристов текущего подбора. Заявка менеджеру не отправлена.')
    return '\n\n'.join(lines)
