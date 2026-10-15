"""Re-entry preserves drafts; only an explicit, current choice resets them."""
import json
from dataclasses import asdict

import pytest

import bot as tg
import vk_bot as vk
from shared import tour_favorites as favorites
from shared.tourvisor import TourOffer


@pytest.fixture(params=[tg, vk], ids=['telegram', 'vk'])
def channel(request, monkeypatch):
    module, uid = request.param, 89123
    info = dict(state=module.STATE_REVIEW, review_token='review', destination='Сочи',
                origin='Москва', dates='2027-01-01', nights=10, people=2, kids=1,
                kids_ages=[9], infants=0, budget=270123, budget_scope='total',
                budget_open_ended=False, direct_only=True, phone='own chat',
                source_tag='resume-test')
    module.user_data[uid] = info
    module.set_session(uid, info)
    favorites.change(module._db_cursor, uid, info, offer=asdict(TourOffer(
        'Тестовый отель', 4, 'Сочи', '2027-01-01', 10, 'HB', 'Стандарт', 'Тест', 80000)))
    sent, prompts = [], []
    monkeypatch.setattr(module, 'has_consent', lambda owner: True)
    monkeypatch.setattr(module, 'send_message', lambda owner, text, **kw: sent.append((owner, text, kw)))
    monkeypatch.setattr(module, '_prompt_for_state', lambda owner, state: prompts.append((owner, state)))
    for name in ('handle_completion', 'save_lead', '_notify_admin', 'send_lead_to_mdt'):
        monkeypatch.setattr(module, name, lambda *a, **kw: pytest.fail('enquiry gate crossed'))
    yield module, uid, info, sent, prompts
    module.user_data.pop(uid, None)
    module.delete_session(uid)
    if module is vk:
        module._delete_miniapp_snapshot(uid)


def token(info):
    return info['_resume_choice'][0]


def test_start_continue_preserves_exact_values_and_favorites_after_reload(channel):
    module, uid, info, sent, prompts = channel
    module.user_data.pop(uid)
    module.handle_start(uid)
    restored = module.user_data[uid]
    assert restored['budget'] == 270123
    assert restored['dates'] == info['dates']
    assert restored['kids_ages'] == [9]
    assert restored['direct_only']
    assert len(favorites.items(module._db_cursor, uid, restored)) == 1
    keyboard = json.loads(sent[-1][2].get('reply_markup') or sent[-1][2]['keyboard'])
    assert 'Продолжить подбор' in json.dumps(keyboard, ensure_ascii=False)
    assert 'Начать заново' in json.dumps(keyboard, ensure_ascii=False)
    assert not prompts
    module._resume_draft(uid, 'continue', token(restored))
    assert prompts == [(uid, module.STATE_REVIEW)]
    assert module.user_data[uid] is restored
    assert len(favorites.items(module._db_cursor, uid, restored)) == 1


def test_explicit_restart_clears_draft_and_favorites_only_once(channel):
    module, uid, info, sent, prompts = channel
    module.handle_start(uid)
    old_token = token(info)
    module._resume_draft(uid, 'restart', old_token)
    new = module.user_data[uid]
    assert new['state'] == module.STATE_DESTINATION
    assert 'budget' not in new
    assert new['source_tag'] == 'resume-test'
    assert favorites.items(module._db_cursor, uid, info) == []
    new['destination'] = 'Египет'
    module._resume_draft(uid, 'restart', old_token)
    assert module.user_data[uid]['destination'] == 'Египет'
    assert 'устарела' in sent[-1][1]


@pytest.mark.parametrize('change', ['parameters', 'new_prompt', 'owner', 'cancel', 'consumed', 'consent'])
def test_stale_restart_never_resets_current_trip(channel, monkeypatch, change):
    module, uid, info, sent, prompts = channel
    module.handle_start(uid)
    old_token = token(info)
    if change == 'parameters':
        info['budget'] += 1
    elif change == 'new_prompt':
        module.handle_start(uid)
    elif change == 'owner':
        uid += 1
    elif change == 'cancel':
        module.handle_cancel(uid)
    elif change == 'consumed':
        module._resume_draft(uid, 'continue', old_token)
    else:
        monkeypatch.setattr(module, 'has_consent', lambda owner: False)
    before = module.user_data.get(uid)
    module._resume_draft(uid, 'restart', old_token)
    assert module.user_data.get(uid) is before
    assert 'устарела' in sent[-1][1]


def test_partial_resume_and_inflight_operations_do_not_start_search(channel):
    module, uid, info, sent, prompts = channel
    info['state'] = module.STATE_DATES
    module.handle_start(uid)
    module._resume_draft(uid, 'continue', token(info))
    assert prompts == [(uid, module.STATE_DATES)]
    info['state'] = module.STATE_REVIEW
    info['_package_searching' if module is tg else '_tour_searching'] = True
    module.handle_start(uid)
    module._resume_draft(uid, 'continue', token(info))
    assert len(prompts) == 1
    assert 'Поиск ещё выполняется' in sent[-1][1]
    info['_completing'] = True
    module.handle_start(uid)
    assert 'Отправка уже выполняется' in sent[-1][1]
    assert module.user_data[uid] is info


def test_actual_callback_routes_to_resume(channel, monkeypatch):
    module, uid, info, sent, prompts = channel
    module.handle_start(uid)
    choice = token(info)
    if module is tg:
        monkeypatch.setattr(module, 'answer_callback', lambda *a, **kw: None)
        monkeypatch.setattr(module, 'clear_inline_keyboard', lambda *a, **kw: None)
        module._process_callback({'callback_query': {'id': 'resume-test',
            'from': {'id': uid, 'first_name': 'Test'},
            'message': {'chat': {'id': uid}, 'message_id': 17},
            'data': f'draft:continue:{choice}'}})
    else:
        monkeypatch.setattr(module, 'get_user_name', lambda owner: 'Test')
        module._process_message({'message': {'from_id': uid, 'peer_id': uid,
            'text': 'Продолжить подбор',
            'payload': json.dumps({'command': 'draft_continue', 'token': choice})}})
    assert prompts == [(uid, module.STATE_REVIEW)]
