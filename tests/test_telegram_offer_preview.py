"""Pre-enquiry supplier browsing must never create or transmit a lead."""
import json
import os
from dataclasses import asdict

os.environ.setdefault('BOT_TOKEN', 'dummy-token')
import pytest
import bot
from shared import tourvisor as tv


@pytest.fixture
def preview(monkeypatch):
    bot.user_data.clear()
    with bot._db_cursor(commit=True) as db:
        db.execute('DELETE FROM sessions')
        db.execute('DELETE FROM leads')
    info = dict(state=bot.STATE_REVIEW, review_token='old', destination='Сочи', origin='Москва',
                dates='2027-01-01', nights=7, people=2, kids_ages=[0,14], budget=270123,
                budget_scope='total', phone='Telegram')
    bot.user_data[42] = info
    sent = []
    monkeypatch.setattr(bot, 'send_message', lambda uid, text, **kw: sent.append((uid,text,kw)))
    monkeypatch.setattr(bot, '_package_preview_enabled', lambda: True)
    monkeypatch.setattr(bot, 'SYNC_COMPLETION', True)
    for name in ('save_lead', '_notify_admin', 'send_lead_to_mdt', 'handle_completion'):
        monkeypatch.setattr(bot, name, lambda *a, **k: pytest.fail('browsing crossed the enquiry gate'))
    yield info, sent
    bot.user_data.clear()


def offers():
    return [tv.TourOffer(f'Отель {i}',4,'Сочи','2027-01-01',7,'HB','Стандарт','Алеан',80000+i,
                         provider='alean',tour_id=f'alean:{i}') for i in range(5)]


def test_iso_departure_and_night_ranges_remain_independent_after_restore(monkeypatch, preview):
    info, sent = preview
    monkeypatch.setenv('TOUR_PROVIDER_ORDER', 'alean')
    settings = bot._alean.AleanSettings(enabled=True, username='fixture', password='fixture')
    monkeypatch.setattr(bot._alean.AleanSettings, 'from_env', lambda: settings)
    captured = []
    monkeypatch.setattr(bot._alean, 'search_tours', lambda config, session, query, **kw: captured.append(query) or tv.SearchResult())
    info.update(dates='2099-10-15 — 2099-10-22', nights='7-10')
    bot.set_session(42, info)
    restored = bot.get_session(42)
    bot._alean_search(restored)
    assert captured[0]['dates_are_trip'] is False and captured[0]['nights'] == '7-10'
    assert captured[0]['dates'] == info['dates']
    assert 'phone' not in captured[0]


def test_unsupported_departure_explains_limits_and_requires_explicit_city_change(monkeypatch, preview):
    info, sent = preview
    info['origin'] = 'Архангельск'
    result = tv.SearchResult(error='not in catalog', error_code='departure_unsupported')
    bot._publish_package_preview(42, info, 'old', result)
    text = sent[-1][1]
    assert 'Архангельск' in text and 'не поддерживается' in text and 'наличие не проверено' in text
    assert 'Изменить город вылета' in str(json.loads(sent[-1][2]['reply_markup']))
    assert info['origin'] == 'Архангельск' and info['budget'] == 270123
    bot._process_review(42, f'rv:{bot.STATE_ORIGIN}:old', {}, info)
    assert info['state'] == bot.STATE_ORIGIN_REVIEW and info['origin'] == 'Архангельск'
    bot.set_session(42, info)
    bot.user_data.clear()
    bot.load_state()
    info = bot.user_data[42]
    assert info['state'] == bot.STATE_ORIGIN_REVIEW
    before = {key: info.get(key) for key in ('dates', 'nights', 'people', 'kids_ages', 'budget', 'budget_scope', 'phone')}
    bot._step_origin(42, 'Москва', {}, info)
    assert info['state'] == bot.STATE_REVIEW and info['origin'] == 'Москва'
    assert {key: info.get(key) for key in before} == before
    assert info['review_token'] != 'old' and not info.get('_package_searching')
    token = info['review_token']
    bot._process_review(42, f'rv:{bot.STATE_ORIGIN}:{token}', {}, info)
    bot._go_back(42)
    assert info['state'] == bot.STATE_REVIEW and info['origin'] == 'Москва'


def test_progress_is_read_only_coalesced_and_cannot_replace_final_or_cancelled_chat(monkeypatch, preview):
    info, sent = preview
    info.update(_package_searching=True, _package_wait_message_id=10,
                _package_progress_result=tv.SearchResult(offers=offers(), completed_windows=1,total_windows=6))
    edits = []
    monkeypatch.setattr(bot, '_edit_package_wait', lambda uid, mid, text, markup: edits.append((text,markup)))
    assert bot._publish_package_progress(42,info,'old')
    assert '1 из 6' in edits[0][0] and 'Отель 0' in edits[0][0]
    assert 'после завершения' in edits[0][0]
    assert 'details_' not in edits[0][1] and 'cancel:old' in edits[0][1]
    bot._publish_package_progress(42,info,'old')
    assert len(edits)==1
    result=tv.SearchResult(offers=offers(),completed_windows=6,total_windows=6)
    bot._publish_package_preview(42,info,'old',result)
    assert 'Поиск завершён' in edits[-1][0] and 'Страница' in sent[-1][1]
    assert not bot._publish_package_progress(42,info,'old')
    info['_package_searching']=True
    bot.user_data.pop(42)
    assert not bot._publish_package_progress(42,info,'old')
    assert len(edits)==2


def test_worker_captures_progress_before_final_without_messaging_supplier_callback(monkeypatch, preview):
    info,sent=preview
    info['_package_searching']=True
    def search(snapshot, **kwargs):
        partial=tv.SearchResult(offers=offers()[:1],completed_windows=1,total_windows=6)
        kwargs['on_progress'](partial)
        assert info['_package_progress_result'] is partial
        assert sent==[]
        return tv.SearchResult(offers=offers())
    monkeypatch.setattr(bot,'_alean_search',search)
    bot._package_preview_worker(42,info,'old',dict(info))
    assert '_package_progress_result' not in info and 'Страница' in sent[-1][1]


def test_search_details_pagination_and_selection_require_separate_manager_confirmation(monkeypatch, preview):
    info, sent = preview
    monkeypatch.setattr(bot, '_alean_search', lambda *a, **kw: tv.SearchResult(offers=offers()))
    bot._process_review(42, 'rv:search:old', {}, info)
    token = info['review_token']
    assert token != 'old' and len(info['_package_offers']) == 5
    assert all(uid == 42 for uid,_,_ in sent)
    assert '270123' not in str(info['_package_offers'])  # supplier prices, not budget substituted as price
    assert 'Страница 1 из 2' in sent[-1][1] and 'Заявка менеджеру не отправлена' in sent[-1][1]
    bot._process_review(42, f'rv:page_1:{token}', {}, info)
    assert 'Страница 2 из 2' in sent[-1][1]
    bot._process_review(42, f'rv:details_4:{token}', {}, info)
    assert 'Отель 4' in sent[-1][1] and not info.get('selected_tour')
    bot._process_review(42, f'rv:choose_4:{token}', {}, info)
    assert info['selected_tour']['hotel'] == 'Отель 4'
    assert 'Выбран для обсуждения' in sent[-1][1]
    assert 'Отправить менеджеру' in str(json.loads(sent[-1][2]['reply_markup']))
    bot.set_session(42, info)
    assert bot.get_session(42)['selected_tour'] == info['selected_tour']
    restored = bot.get_session(42)
    restored['kids_ages'] = bot._ages_from_db(restored.get('kids_ages'))
    notice = bot._format_lead_notify_text(42, restored, 'Telegram', 'Клиент')
    assert 'Отель 4' in notice and 'требуют проверки менеджером' in notice
    with bot._db_cursor() as db:
        assert db.execute('SELECT count(*) FROM leads').fetchone()[0] == 0


@pytest.mark.parametrize('result,expected', [
    (tv.SearchResult(), 'вариантов не нашлось'),
    (tv.SearchResult(error='timeout'), 'Наличие туров не проверено'),
    (tv.SearchResult(error='partial', partial=True, warning='Проверено 1 из 6 окон.'), 'Проверено 1 из 6'),
])
def test_preview_distinguishes_empty_failed_and_partial_searches(monkeypatch, preview, result, expected):
    info, sent = preview
    monkeypatch.setattr(bot, '_alean_search', lambda *a, **kw: result)
    bot._start_package_preview(42, info)
    assert expected in sent[-1][1] and not info.get('_package_searching')
    assert 'Повторить поиск' in str(json.loads(sent[-1][2]['reply_markup']))


def test_cancelled_or_replaced_review_discards_late_results(monkeypatch, preview):
    info, sent = preview
    def search(*a, **kw):
        bot.user_data.pop(42)
        assert kw['cancelled']()
        return tv.SearchResult(offers=offers())
    monkeypatch.setattr(bot, '_alean_search', search)
    bot._start_package_preview(42, info)
    assert len(sent) == 1 and 'Ищу предложения' in sent[0][1]


def test_old_offer_buttons_cannot_select_from_new_search(monkeypatch, preview):
    info, sent = preview
    monkeypatch.setattr(bot, '_alean_search', lambda *a, **kw: tv.SearchResult(offers=offers()))
    bot._start_package_preview(42, info)
    previous = info['review_token']
    bot._start_package_preview(42, info)
    bot._process_review(42, f'rv:choose_0:{previous}', {}, info)
    assert 'устарела' in sent[-1][1] and not info.get('selected_tour')


def test_cancel_between_supplier_completion_and_publication_suppresses_result(monkeypatch, preview):
    info, sent = preview
    publish = bot._publish_package_preview
    def cancel_first(*args):
        bot.user_data.pop(42)
        publish(*args)
    monkeypatch.setattr(bot, '_publish_package_preview', cancel_first)
    monkeypatch.setattr(bot, '_alean_search', lambda *a, **k: tv.SearchResult(offers=offers()))
    bot._start_package_preview(42, info)
    assert len(sent) == 1


def test_unavailable_preview_keeps_review_actions(monkeypatch, preview):
    info, sent = preview
    monkeypatch.setattr(bot, '_package_preview_enabled', lambda: False)
    bot._start_package_preview(42, info)
    labels = str(json.loads(sent[-1][2]['reply_markup']))
    assert 'Отправить менеджеру' in labels and 'Изменить' in labels and 'Отмена' in labels


def test_selection_and_new_search_cannot_interleave(monkeypatch, preview):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    info, sent = preview
    entered, release = Event(), Event()
    info['_package_offers'] = [asdict(row) for row in offers()]
    def review(*a):
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(bot, '_ask_review', review)
    monkeypatch.setattr(bot, '_alean_search', lambda *a, **k: tv.SearchResult(offers=offers()))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(bot._process_review, 42, 'rv:choose_0:old', {}, info)
        assert entered.wait(5)
        second = pool.submit(bot._start_package_preview, 42, info)
        try:
            assert info['review_token'] == 'old'
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert info['review_token'] != 'old' and not info.get('selected_tour')


@pytest.mark.parametrize("preset", [value for _, value in bot.DATE_PRESETS])
def test_chat_presets_resolve_before_supplier_search(monkeypatch, preview, preset):
    info, _ = preview
    monkeypatch.setenv('TOUR_PROVIDER_ORDER', 'alean')
    settings = bot._alean.AleanSettings(enabled=True, username='fixture', password='fixture')
    monkeypatch.setattr(bot._alean.AleanSettings, 'from_env', lambda: settings)
    captured = []
    monkeypatch.setattr(bot._alean, 'search_tours', lambda config, session, query, **kw: captured.append(query) or tv.SearchResult())
    info.update(dates=preset, budget_open_ended=True, budget=200000, people='2')
    bot._alean_search(info)
    assert captured[0]['dates'] == bot._tutu.resolve_dates(preset)[0]
    assert captured[0]['dates_are_trip'] is False
    assert captured[0]['budget_open_ended'] is True
    assert captured[0]['kids_ages'] == [0,14]
    assert info['dates'] == preset
