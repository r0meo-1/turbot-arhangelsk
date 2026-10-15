import gzip

import bot
import pytest
from flask import Flask
from shared.vk_miniapp import create_blueprint


@pytest.fixture(params=['tg', 'vk'])
def surface(request):
    if request.param == 'tg':
        return bot.app.test_client(), '/tg/miniapp/', 'vendor/telegram-web-app.js'
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda *a: None, lambda: ('secret', '123', 999)))
    return app.test_client(), '/vk/miniapp/', 'vk-bridge.js'


def test_compressed_assets_roundtrip_and_revalidate(surface):
    client, base, sdk = surface
    for name in (sdk, 'app.js', 'styles.css'):
        url = base + name + '?v=release'
        plain = client.get(url)
        packed = client.get(url, headers={'Accept-Encoding': 'gzip'})
        assert packed.status_code == 200
        assert packed.headers['Content-Encoding'] == 'gzip'
        assert gzip.decompress(packed.data) == plain.data
        assert len(packed.data) < len(plain.data) * .6
        assert int(packed.headers['Content-Length']) == len(packed.data)
        assert packed.headers['Cache-Control'] == 'public, max-age=86400'
        assert 'Accept-Encoding' in packed.headers['Vary']
        cached = client.get(url, headers={'Accept-Encoding': 'gzip', 'If-None-Match': packed.headers['ETag']})
        assert cached.status_code == 304
        denied = client.get(url, headers={'Accept-Encoding': 'gzip;q=0, identity'})
        assert 'Content-Encoding' not in denied.headers
        assert denied.data == plain.data


def test_signed_entry_and_unversioned_assets_stay_uncached(surface):
    client, base, sdk = surface
    for path in ('?v=release', '?vk_user_id=42&sign=private', sdk, sdk + '?v=release&sign=private'):
        response = client.get(base + path, headers={'Accept-Encoding': 'gzip'})
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'no-store'
    assert 'Content-Encoding' not in client.get(base, headers={'Accept-Encoding': 'gzip'}).headers
