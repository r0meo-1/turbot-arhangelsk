import pytest
from shared import departure_catalog as catalog, alean, tour_providers


@pytest.fixture(autouse=True)
def clean_catalog_cache():
    catalog._cache.clear()
    yield
    catalog._cache.clear()


def settings(password='secret'):
    return tour_providers.ProviderSettings(order=('alean',), alean=alean.AleanSettings(
        enabled=True, username='fixture', password=password))


def test_public_catalog_is_read_only_cached_and_account_scoped(monkeypatch):
    calls = []
    def read(config, session, method):
        assert method == 'GetDepartCities'
        calls.append(method)
        return [{'name': 'Москва', 'cid': 'MOW'}, {'name': 'Без перелета'}]
    monkeypatch.setattr(alean, '_catalog', read)
    first = catalog.public_departures(settings(), None)
    assert first == dict(complete=True, departures=['Москва'])
    first['departures'].append('not a real departure')
    assert catalog.public_departures(settings(), None)['departures'] == ['Москва']
    assert len(calls) == 1
    catalog.public_departures(settings('another'), None)
    assert len(calls) == 2


@pytest.mark.parametrize('rows', [[], [{'name': 'Москва'}], [{'cid': 'missing-name'}]])
def test_bad_catalog_never_confirms_absence(monkeypatch, rows):
    monkeypatch.setattr(alean, '_catalog', lambda *a, **k: rows)
    assert not catalog.public_departures(settings(), None)['complete']


def test_unknown_provider_prevents_absence_claim(monkeypatch):
    config = settings()
    monkeypatch.setattr(config, 'enabled_names', lambda: ['alean', 'sletat'])
    monkeypatch.setattr(alean, '_catalog', lambda *a, **k: [{'name': 'Москва', 'cid': 'MOW'}])
    assert catalog.public_departures(config, None) == dict(complete=False, departures=['Москва'])


def test_public_routes_expose_only_catalog_and_never_save(monkeypatch):
    import bot
    from flask import Flask
    from shared.vk_miniapp import create_blueprint
    result = dict(complete=True, departures=['Москва'])
    monkeypatch.setattr(catalog, 'public_departures', lambda *a: result)
    response = bot.app.test_client().get('/miniapp/departures', headers={'Origin': bot.MINI_APP_ORIGIN})
    assert response.json == result and response.headers['Cache-Control'] == 'no-store'
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda *a: pytest.fail('catalog saved draft'),
        lambda: ('', '', 0), departures=lambda: result))
    response = app.test_client().get('/vk/miniapp/departures')
    assert response.json == result and response.headers['Cache-Control'] == 'no-store'
