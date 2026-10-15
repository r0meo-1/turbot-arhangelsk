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
