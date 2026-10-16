"""Private, bounded result snapshots tied to the exact active trip."""
import json
import math
import time
from datetime import datetime, timezone

from shared.tour_favorites import scope, _snapshot

MAX_OFFERS = 100
MAX_BYTES = 512_000


def init_schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS tour_result_snapshots (
        user_id INTEGER PRIMARY KEY, scope TEXT NOT NULL, payload TEXT NOT NULL)''')


def clear(cur, user_id):
    cur.execute('DELETE FROM tour_result_snapshots WHERE user_id=?', (user_id,))


def _offers(rows):
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_OFFERS:
        raise ValueError('Invalid result pool')
    result = []
    for row in rows:
        offer = _snapshot(row)
        if not isinstance(offer['hotel'], str) or not offer['hotel']:
            raise ValueError('Invalid hotel')
        for key in ('price', 'fuel_charge'):
            if type(offer[key]) not in (int, float) or not math.isfinite(offer[key]) or offer[key] < 0:
                raise ValueError('Invalid price')
        if any(isinstance(value, str) and len(value) > 2000 for value in offer.values()):
            raise ValueError('Oversized offer')
        result.append(offer)
    return result


def _fields(channel):
    return ('_package_offers', '_package_page', '_package_warning') if channel == 'telegram' else (
        '_tour_offers', '_tour_page', '_tour_partial_notice')


def save(cur, user_id, info, channel):
    pool, page, warning = _fields(channel)
    if info.get('state') != 'review' or not info.get(pool) or info.get(
            '_package_searching' if channel == 'telegram' else '_tour_searching'):
        clear(cur, user_id)
        return
    try:
        info.setdefault('_results_searched_at', int(time.time()))
        payload = dict(channel=channel, offers=_offers(info[pool]),
            page=int(info.get(page) or 0), warning=str(info.get(warning) or '')[:2000],
            searched_at=int(info['_results_searched_at']))
        if channel == 'vk':
            payload.update(base=_offers(info.get('_tour_offers_base') or info[pool]),
                marker=info.get('_tour_search_marker'), filter=info.get('_tour_filter_mode'))
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(raw.encode()) > MAX_BYTES:
            raise ValueError('Oversized snapshot')
    except (TypeError, ValueError, KeyError, AttributeError):
        clear(cur, user_id)
        return
    cur.execute('INSERT OR REPLACE INTO tour_result_snapshots VALUES (?,?,?)', (user_id, scope(info), raw))


def restore(cur, user_id, info, channel):
    if info.get('state') != 'review':
        return
    row = cur.execute('SELECT scope,payload FROM tour_result_snapshots WHERE user_id=?', (user_id,)).fetchone()
    if not row or row[0] != scope(info):
        return
    try:
        if len(row[1].encode()) > MAX_BYTES:
            return
        payload = json.loads(row[1])
        if payload['channel'] != channel:
            return
        offers = _offers(payload['offers'])
        page = int(payload['page']) % max(1, (len(offers) + 2) // 3)
        searched_at = int(payload['searched_at'])
        if not 0 < searched_at <= time.time() + 60:
            return
        pool_key, page_key, warning_key = _fields(channel)
        values = {pool_key: offers, page_key: page, warning_key: str(payload.get('warning') or '')[:2000],
                  '_results_searched_at': searched_at, '_results_restored': True}
        if channel == 'vk':
            marker = payload.get('marker')
            if not isinstance(marker, str) or not marker:
                return
            values.update(_tour_offers_base=_offers(payload['base']), _tour_search_marker=marker,
                          _tour_filter_mode=payload.get('filter'))
        info.update(values)
    except (TypeError, ValueError, KeyError, AttributeError):
        return


def notice(info):
    stamp = info.get('_results_searched_at')
    if not stamp:
        return ''
    when = datetime.fromtimestamp(stamp, timezone.utc).strftime('%d.%m.%Y %H:%M UTC')
    return f'🕒 Поиск: {when}. Сохранённая выдача: цены и наличие требуют обновления. Обновить — отдельной кнопкой поиска.\n\n'
