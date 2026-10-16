"""Read-only bounded public departure advice; never starts a tour search."""
import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, replace

from shared import alean, tourvisor

_cache = OrderedDict()
_lock = threading.Lock()


def public_departures(settings, session):
    names = settings.enabled_names()
    identity = hashlib.sha256(repr((names, asdict(settings))).encode()).digest()
    with _lock:
        cached = _cache.get(identity)
        if cached and cached[0] > time.monotonic():
            return dict(cached[1], departures=list(cached[1]['departures']))
        cities = set()
        complete = bool(names)
        for provider in names:
            try:
                if provider == 'alean':
                    rows = alean._catalog(replace(settings.alean, timeout=3), session, 'GetDepartCities')
                    if not rows or any(not row.get('name') for row in rows):
                        raise ValueError('Invalid catalogue')
                    if any(not row.get('cid') and row['name'].casefold().replace('ё', 'е') != 'без перелета' for row in rows):
                        complete = False
                    cities.update(row['name'] for row in rows if row.get('cid'))
                elif provider == 'tourvisor':
                    rows = tourvisor._http_request(replace(settings.tourvisor, timeout=3), session,
                                                  'GET', 'departures', {'departureCountryId': 1})
                    if not isinstance(rows, list) or not rows or not all(
                        isinstance(row, dict) and str(row.get('id', '')).isdigit() and int(row['id']) > 0 and isinstance(row.get('name'), str)
                        and row['name'].strip() for row in rows):
                        raise ValueError('Invalid catalogue')
                    cities.update(row['name'] for row in rows)
                else:
                    complete = False
            except Exception:
                complete = False
        result = dict(complete=complete and len(cities) <= 1000, departures=sorted(cities)[:1000])
        _cache[identity] = (time.monotonic() + (300 if complete else 30), result)
        while len(_cache) > 8:
            _cache.popitem(last=False)
        return dict(result, departures=list(result['departures']))
