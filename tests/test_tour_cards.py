from dataclasses import asdict

from shared import tour_cards as cards
from shared.tourvisor import TourOffer


def offer(**changes):
    result = asdict(TourOffer('Отель', 4, 'Сочи', '2027-01-01', 7, 'HB',
                             'Стандарт', 'Алеан', 180000, fuel_charge=5000, provider='alean'))
    result.update(changes)
    return result


def test_card_total_includes_fee_and_keeps_party_and_actual_departure():
    info = dict(origin='Архангельск', people=2, kids=1, kids_ages=[9])
    text = cards.card(offer(departure='Москва'), info)
    assert '185 000 ₽' in text and 'включая сбор 5 000 ₽' in text
    assert 'на всех туристов' in text and 'Москва' in text and 'Архангельск' not in text
    assert '2 взр.' in text and '9 лет' in text and '7 ноч.' in text
    assert 'трансфер не включён' in text and 'предварительная' in text


def test_comparison_groups_differences_and_common_values_without_ranking():
    text = cards.comparison([offer(), offer(hotel='Другой', price=200000, meal='BB')], dict(people=2))
    assert 'Различия:' in text and 'Совпадает:' in text
    assert '№1 — 185 000 ₽' in text and '№2 — 205 000 ₽' in text
    assert '20 000 ₽' in text and 'Питание:' in text
    assert 'Заявка менеджеру не отправлена' in text


def test_unknown_or_different_currency_prices_never_produce_price_spread():
    for second in (offer(price=0), offer(currency='EUR')):
        text = cards.comparison([offer(), second])
        assert 'Разница между' not in text
    assert cards.facts(offer(price=0))['Цена на всех'] == 'не указана'


def test_long_supplier_fields_fit_messenger_limits_without_internal_ids():
    long = 'Очень длинный текст ' * 1000
    offers = [offer(hotel=long, region=long, meal=long, room=long, operator=long,
                    date=long, departure=long, tour_id='private:offer') for _ in range(3)]
    text = cards.comparison(offers)
    assert len(text) < 4096 and 'private:offer' not in text
    assert len(cards.results(offers, dict(people=2))) < 4096
