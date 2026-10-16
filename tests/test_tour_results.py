"""Result restoration is local, owner-scoped and never submits an enquiry."""
import sqlite3
from dataclasses import asdict

import pytest
import bot as tg
import vk_bot as vk
from shared import tour_results as results
from shared.tourvisor import TourOffer


@pytest.fixture(params=[tg, vk], ids=['telegram', 'vk'])
def channel(request, monkeypatch):
    module = request.param
    uid = 89109
    info = dict(state='review', review_token='persist', destination='Сочи',
                origin='Москва', dates='2027-01-01 — 2027-01-02', nights='7-10',
                people=2, kids=1, kids_ages=[9], budget=270123, budget_scope='total',
                direct_only=False, phone='own chat', _tour_search_marker='generation')
    offers = [asdict(TourOffer(f'Отель {i}', 4, 'Сочи', '2027-01-01', 7,
               'HB', 'Стандарт', 'Алеан', 80000+i, provider='alean', tour_id=f'private:{i}'))
              for i in range(7)]
    info.update(_package_offers=offers, _tour_offers=offers,
                _package_page=2, _tour_page=2, _tour_offers_base=offers,
                _results_searched_at=1700000000, _package_warning='Неполная выдача',
                _tour_partial_notice='Неполная выдача')
    module.user_data[uid] = info
    sent = []
    monkeypatch.setattr(module, 'send_message', lambda owner, text, **kw: sent.append((owner, text, kw)))
    for name in ('handle_completion', 'save_lead', '_notify_admin', 'send_lead_to_mdt'):
        monkeypatch.setattr(module, name, lambda *a, **kw: pytest.fail('enquiry gate crossed'))
    yield module, uid, info, offers, sent
    module.user_data.pop(uid, None)
    module.delete_session(uid)


def test_restart_restores_page_prices_party_and_timestamp_without_search(channel):
    module, uid, info, offers, sent = channel
    module.set_session(uid, info)
    module.user_data.pop(uid)
    module.load_state()  # startup path, not a copy of the live cache
    restored = module.user_data[uid]
    pool, page, warning = results._fields('telegram' if module is tg else 'vk')
    assert restored[pool] == offers
    assert restored[page] == 2
    assert restored[warning] == 'Неполная выдача'
    assert restored['_results_searched_at'] == 1700000000
    assert int(restored['budget']) == 270123
    assert restored['kids_ages'] in ([9], '[9]', '9')
    assert restored['nights'] == '7-10'
    assert not sent  # startup neither sends messages nor searches
    if module is tg:
        module._show_package_page(uid, restored, restored[page])
    else:
        module._send_tour_results_page(uid, restored[page])
    assert '3 из 3' in sent[-1][1] and 'Отель 6' in sent[-1][1]
    assert 'цены и наличие требуют обновления' in sent[-1][1]
    assert 'private:' not in sent[-1][1]
    again = module.get_session(uid)
    assert again[page] == 2 and again['_results_searched_at'] == 1700000000
    assert module.get_session(uid+1) is None


@pytest.mark.parametrize('change', ['cancel', 'replacement', 'searching', 'edit'])
def test_old_results_cannot_return_after_invalidation(channel, change):
    module, uid, info, offers, sent = channel
    module.set_session(uid, info)
    if change == 'cancel':
        module.delete_session(uid)
    elif change == 'replacement':
        module.set_session(uid, {key: value for key, value in info.items() if not key.startswith('_')})
    elif change == 'searching':
        info['_package_searching' if module is tg else '_tour_searching'] = True
        module.set_session(uid, info)
    else:
        module.set_session(uid, dict(info, state='dates'))
    with module._db_cursor() as cur:
        assert cur.execute('SELECT count(*) FROM tour_result_snapshots WHERE user_id=?', (uid,)).fetchone()[0] == 0
    module.user_data.pop(uid, None)
    restored = module.get_session(uid)
    assert not restored or not restored.get('_package_offers' if module is tg else '_tour_offers')
    assert not sent


def test_scope_and_malformed_storage_are_safe(channel):
    module, uid, info, offers, sent = channel
    module.set_session(uid, info)
    channel_name = 'telegram' if module is tg else 'vk'
    for changed in (dict(info, budget=1), dict(info, kids_ages=[10]), dict(info, dates='2027-01-03')):
        clean = {k: v for k, v in changed.items() if not k.startswith('_')}
        with module._db_cursor() as cur:
            results.restore(cur, uid, clean, channel_name)
        assert not clean.get(results._fields(channel_name)[0])
    with module._db_cursor(commit=True) as cur:
        cur.execute('UPDATE tour_result_snapshots SET payload=? WHERE user_id=?', ('{"offers":[null]}', uid))
    restored = module.get_session(uid)
    assert not restored.get(results._fields(channel_name)[0])
    assert not sent


def test_snapshot_bounds_and_whitelist():
    conn = sqlite3.connect(':memory:')
    cur = conn.cursor()
    results.init_schema(cur)
    offer = asdict(TourOffer('Hotel', 4, 'Sochi', '2027-01-01', 7, 'HB', 'Room', 'Provider', 10))
    info = dict(state='review', _package_offers=[dict(offer, secret='never store')])
    results.save(cur, 1, info, 'telegram')
    raw = cur.execute('SELECT payload FROM tour_result_snapshots').fetchone()[0]
    assert 'secret' not in raw
    for pool in ([None], [dict(offer, price=float('nan'))], [offer]*101):
        results.save(cur, 1, dict(info, _package_offers=pool), 'telegram')
        assert cur.execute('SELECT count(*) FROM tour_result_snapshots').fetchone()[0] == 0
    conn.close()


def test_vk_filtered_view_and_open_budget_survive_restart(channel):
    module, uid, info, offers, sent = channel
    info['budget_open_ended'] = True
    if module is vk:
        info.update(_tour_offers=offers[1:4], _tour_page=0, _tour_filter_mode='cheaper')
    module.set_session(uid, info)
    module.user_data.pop(uid)
    module.load_state()
    restored = module.user_data[uid]
    assert restored['budget_open_ended']
    if module is vk:
        assert restored['_tour_offers'] == offers[1:4]
        assert restored['_tour_offers_base'] == offers
        assert restored['_tour_search_marker'] == 'generation'
        assert restored['_tour_filter_mode'] == 'cheaper'
    else:
        assert restored['review_token'] == 'persist'
        assert restored['_package_offers'] == offers


def test_partial_session_update_invalidates_results(channel):
    module, uid, info, offers, sent = channel
    if module is not tg:
        return
    module.set_session(uid, info)
    module.update_session(uid, budget=300000)
    module.update_session(uid, budget=270123)  # returning to the same query cannot resurrect it
    assert not module.get_session(uid).get('_package_offers')


def test_fresh_search_shows_time_without_requesting_immediate_refresh():
    info = dict(_results_searched_at=1700000000)
    assert 'Поиск:' in results.notice(info)
    assert 'требуют обновления' not in results.notice(info)
    info['_results_restored'] = True
    assert 'требуют обновления' in results.notice(info)
