import json
from dataclasses import asdict
import pytest
import bot


@pytest.fixture
def owner_messages(monkeypatch):
    sent = []
    monkeypatch.setattr(bot, 'LEAD_NOTIFY_IDS', [])
    monkeypatch.setattr(bot, 'send_lead_owner_vk', lambda text: sent.append(text) or True)
    return sent


def request_info():
    return dict(destination='Сочи', origin='Москва', dates='2099-01-15', nights=7,
                people='2', budget=100000, budget_scope='per_person')


@pytest.mark.parametrize('encoded', [False, True])
def test_selected_package_reaches_vk_owner(owner_messages, encoded):
    info = request_info()
    tour = asdict(bot._tourvisor.TourOffer(hotel='ТЕСТ Отель', category=3,
        region='Адлер', price=123456, meal='завтрак', room='Стандарт',
        date='2099-01-15', nights=7, departure='Москва', operator='Алеан', provider='alean'))
    info['selected_tour'] = json.dumps(tour) if encoded else tour
    assert bot._notify_admin(12345, info, 'Telegram @test_user', 'ТЕСТ', username='test_user')
    text = owner_messages[0]
    for value in ['ТЕСТ Отель', 'Адлер', '123456', 'завтрак', 'Стандарт', '2099-01-15']:
        rendered = text.replace(' ', '') if value == '123456' else text
        assert value in rendered
    assert '<b>' not in text


@pytest.mark.parametrize('contact', ['Telegram @test_user', '@test_user', 'Telegram https://t.me/test_user'])
def test_telegram_contact_is_https_not_vk_mention(owner_messages, contact):
    bot._notify_admin(12345, request_info(), contact, 'ТЕСТ', username='test_user')
    text = owner_messages[0]
    assert 'https://t.me/test_user' in text
    assert '@test_user' not in text


def test_phone_contact_is_preserved_without_username(owner_messages):
    bot._notify_admin(12345, request_info(), '+70000000000', 'ТЕСТ')
    assert 'Связь клиента: +70000000000' in owner_messages[0]
    assert 'https://t.me/' not in owner_messages[0]
    assert 'Выбран для обсуждения' not in owner_messages[0]


def test_missing_telegram_username_does_not_invent_link(owner_messages):
    bot._notify_admin(12345, request_info(), 'Telegram ID: 12345', 'ТЕСТ')
    assert 'Telegram ID: 12345' in owner_messages[0]
    assert 'https://t.me/' not in owner_messages[0]


def test_vk_contact_is_not_rewritten_as_telegram(owner_messages):
    bot._notify_admin(12345, request_info(), 'VK @test_user', 'ТЕСТ')
    assert 'Связь клиента: VK @test_user' in owner_messages[0]
    assert 'https://t.me/' not in owner_messages[0]


def test_invalid_saved_selection_does_not_break_notification(owner_messages):
    info = {**request_info(), 'selected_tour': '{bad json'}
    assert bot._notify_admin(12345, info, 'Telegram @test_user', 'ТЕСТ')
    assert 'https://t.me/test_user' in owner_messages[0]
