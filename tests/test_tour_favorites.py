"""Shortlisting never crosses the enquiry gate, including retries and restore."""
import json
from dataclasses import asdict

import pytest
import bot as tg
import vk_bot as vk
from shared import tour_favorites as favorites
from shared.tourvisor import TourOffer


@pytest.fixture(params=[tg, vk], ids=['telegram', 'vk'])
def channel(request, monkeypatch):
    module = request.param
    uid = 89100
    info = dict(state=module.STATE_REVIEW, review_token='test', destination='Сочи',
                origin='Москва', dates='2027-01-01 — 2027-01-31', nights=10,
                people=2, kids=1, kids_ages=[9], budget=270123, budget_scope='total',
                direct_only=False, phone='own chat', _tour_search_marker='search')
    offers = [asdict(TourOffer(f'Отель {i}', 4, 'Сочи', f'2027-01-0{i+1}', 10,
                 'HB', 'Стандарт', 'Алеан', 80000+i, provider='alean', tour_id=f'private:{i}'))
              for i in range(4)]
    info.update(_package_offers=offers, _tour_offers=offers)
    module.user_data[uid] = info
    with module._db_cursor(commit=True) as cur:
        favorites.clear(cur, uid)
    sent = []
    monkeypatch.setattr(module, 'send_message', lambda owner, text, **kw: sent.append((owner, text, kw)))
    if module is vk:
        monkeypatch.setattr(module, 'get_user_name', lambda owner: 'Test')
    for name in ('handle_completion', 'save_lead', '_notify_admin', 'send_lead_to_mdt'):
        monkeypatch.setattr(module, name, lambda *a, **kw: pytest.fail('enquiry gate crossed'))
    yield module, uid, info, offers, sent
    module.user_data.pop(uid, None)
    module.delete_session(uid)


def add(module, uid, info, offers, index):
    if module is tg:
        module._process_review(uid, f'rv:fav_{index}:test', {}, info)
    else:
        info['selected_tour'] = offers[index]
        payload = {'command': 'favorite_add', 'search_marker': 'search',
                   'key': vk._tour_offer_key(offers[index])}
        module._favorite_action(uid, payload)


def test_shortlist_retry_limit_compare_restore_owner_and_cleanup(channel):
    module, uid, info, offers, sent = channel
    add(module, uid, info, offers, 0)
    add(module, uid, info, offers, 0)  # lost response / double click
    assert len(favorites.items(module._db_cursor, uid, info)) == 1
    if module is tg:
        module._process_review(uid, 'rv:compare:test', {}, info)
    else:
        module._favorite_action(uid, {'command': 'favorite_compare'})
    assert '2–3 тура' in sent[-1][1]
    for index in (1, 2, 3):
        add(module, uid, info, offers, index)
    assert len(favorites.items(module._db_cursor, uid, info)) == 3
    assert any('уже 3 тура' in text for _, text, _ in sent)
    assert favorites.items(module._db_cursor, uid+1, info) == []
    assert favorites.items(module._db_cursor, uid, dict(info, budget=300000)) == []
    if module is tg:
        module._process_review(uid, 'rv:compare:test', {}, info)
    else:
        module._favorite_action(uid, {'command': 'favorite_compare'})
    text = sent[-1][1]
    assert 'Сравнение' in text and all(f'Отель {i}' in text for i in range(3))
    assert 'Отель 3' not in text and 'private:' not in text
    assert 'HB' in text or 'завтрак' in text.lower()
    assert len(text) < 4096
    keyboard = sent[-1][2].get('reply_markup') or sent[-1][2].get('keyboard')
    assert 'Отправить' not in keyboard and 'send:' not in keyboard
    module.set_session(uid, info)
    module.user_data.pop(uid)
    restored = (module.get_session(uid) if module is tg else
                module._restore_session_from_db(uid))
    assert len(favorites.items(module._db_cursor, uid, restored)) == 3
    module.user_data[uid] = restored
    # Explicit remove can be retried safely; it never re-adds the item.
    key = favorites.offer_key(offers[0])
    for _ in range(2):
        if module is tg:
            module._process_review(uid, f'rv:unfav_{key}:test', {}, restored)
        else:
            module._show_tour_favorites(uid)
            module._favorite_action(uid, {'command': 'favorite_remove', 'key': key,
                'scope': favorites.scope(restored), 'search_marker': restored['_tour_search_marker']})
    assert len(favorites.items(module._db_cursor, uid, restored)) == 2
    assert all(owner == uid for owner, _, _ in sent)
    module.delete_session(uid)
    assert favorites.items(module._db_cursor, uid, info) == []


def test_stale_buttons_and_inflight_search_cannot_add(channel):
    module, uid, info, offers, sent = channel
    if module is tg:
        module._process_review(uid, 'rv:fav_0:old-token', {}, info)
        info['_package_searching'] = True
        module._process_review(uid, 'rv:fav_0:test', {}, info)
    else:
        info['selected_tour'] = offers[1]
        module._favorite_action(uid, {'command': 'favorite_add', 'search_marker': 'old',
                                      'key': vk._tour_offer_key(offers[0])})
        info['_tour_searching'] = True
        add(module, uid, info, offers, 0)
    assert favorites.items(module._db_cursor, uid, info) == []


def test_actualization_metadata_does_not_break_comparison_or_duplicate_offer(channel):
    module, uid, info, offers, sent = channel
    refreshed = dict(offers[0], actualization_status='unknown', actualized_at='now',
                     actualization_confirmed=False)
    assert favorites.change(module._db_cursor, uid, info, refreshed) == 'added'
    assert favorites.change(module._db_cursor, uid, info, offers[0]) == 'exists'
    assert 'Отель 0' in favorites.render(favorites.items(module._db_cursor, uid, info))


def test_same_budget_amount_with_different_constraint_has_separate_shortlist(channel):
    module, uid, info, offers, sent = channel
    info['budget_open_ended'] = True
    favorites.change(module._db_cursor, uid, info, offers[0])
    assert favorites.items(module._db_cursor, uid, info)
    assert favorites.items(module._db_cursor, uid, dict(info, budget_open_ended=False)) == []


def test_vk_card_binds_add_to_exact_offer_and_generation():
    offer = asdict(TourOffer('Отель', 3, 'Сочи', '2027-01-01', 7, 'BB', 'Номер', 'Оператор', 100000))
    keyboard = json.loads(vk._selected_tour_keyboard({'selected_tour': offer, '_tour_search_marker': 'current'}))
    payloads = [json.loads(btn['action']['payload']) for row in keyboard['buttons'] for btn in row
                if btn['action'].get('payload')]
    add_payload = next(p for p in payloads if p.get('command') == 'favorite_add')
    assert add_payload['search_marker'] == 'current' and add_payload['key'] == vk._tour_offer_key(offer)


def test_concurrent_adds_still_obey_three_tour_limit(channel):
    from concurrent.futures import ThreadPoolExecutor
    module, uid, info, offers, sent = channel
    with ThreadPoolExecutor(max_workers=4) as workers:
        statuses = list(workers.map(lambda offer: favorites.change(module._db_cursor, uid, info, offer), offers))
    assert sorted(statuses) == ['added', 'added', 'added', 'full']
    assert len(favorites.items(module._db_cursor, uid, info)) == 3


def test_vk_payload_dispatch_restores_owner_without_supplier_or_lead_calls(channel, monkeypatch):
    module, uid, info, offers, sent = channel
    if module is not vk:
        return
    add(module, uid, info, offers, 0)
    module.set_session(uid, info)
    module.user_data.pop(uid)
    module._process_message({'object': {'message': {'from_id': uid, 'peer_id': uid,
        'text': '⭐ Избранное', 'payload': json.dumps({'command': 'favorites'})}}})
    assert 'Отель 0' in sent[-1][1]
    assert module.user_data[uid]['state'] == module.STATE_REVIEW
    # An old remove cannot erase a newer shortlist with otherwise identical parameters.
    key = favorites.offer_key(offers[0])
    module._favorite_action(uid, {'command': 'favorite_remove', 'key': key,
        'scope': favorites.scope(info), 'search_marker': 'old-generation'})
    assert len(favorites.items(module._db_cursor, uid, info)) == 1
