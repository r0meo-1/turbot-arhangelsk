import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlencode, urlparse

import bot
from shared import travelpayouts_links as links


BOT_TOKEN = "123456789:test_bot_token_for_partner_links"
ORIGIN = "https://r0meo-1.github.io"


def _signed_init_data(user_id=88101):
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrPartners",
        "user": json.dumps(
            {"id": user_id, "first_name": "Roma", "username": "tester"},
            separators=(",", ":"),
        ),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_build_yandex_travel_url_keeps_trip_context():
    url = links.build_yandex_travel_url(
        "Пхукет",
        checkin="2099-02-10",
        nights=10,
        adults=3,
    )
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    assert parsed.netloc == "travel.yandex.ru"
    assert parsed.path == "/hotels/thailand/"
    assert qs["checkinDate"] == ["2099-02-10"]
    assert qs["checkoutDate"] == ["2099-02-20"]
    assert qs["adults"] == ["3"]
    assert qs["utm_source"] == ["turbot"]
    assert qs["utm_content"] == ["telegram"]


def test_build_airalo_url_maps_resort_to_country():
    url = links.build_airalo_url("Фукуок")
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    assert parsed.netloc == "www.airalo.com"
    assert parsed.path == "/vietnam-esim"
    assert qs["utm_campaign"] == ["esim"]
    assert qs["utm_content"] == ["telegram"]


def test_legacy_redirect_keeps_ids_and_subid_on_server(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "778488")
    monkeypatch.setenv("TRAVELPAYOUTS_TRS", "574782")

    url = links.build_legacy_redirect(
        "https://www.airalo.com/thailand-esim",
        program=8310,
        campaign_id=541,
        sub_id="tg_esim",
    )
    qs = parse_qs(urlparse(url).query)

    assert qs["marker"] == ["778488.tg_esim"]
    assert qs["trs"] == ["574782"]
    assert qs["p"] == ["8310"]
    assert qs["campaign_id"] == ["541"]
    assert qs["u"] == ["https://www.airalo.com/thailand-esim"]


def test_partner_links_api_uses_subid(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "result": {
                    "links": [{
                        "code": "success",
                        "partner_url": "https://airalo.tp.st/example",
                    }]
                }
            }

    def fake_post(url, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "secret-token")
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "778488")
    monkeypatch.setenv("TRAVELPAYOUTS_TRS", "574782")
    monkeypatch.setattr(links.requests, "post", fake_post)

    result = links.create_partner_link(
        "https://www.airalo.com/thailand-esim",
        sub_id="tg_esim",
    )

    assert result == "https://airalo.tp.st/example"
    assert captured["url"] == "https://api.travelpayouts.com/links/v1/create"
    assert captured["headers"]["X-Access-Token"] == "secret-token"
    assert captured["json"]["links"][0]["sub_id"] == "tg_esim"


def test_hotel_resolution_falls_back_to_affiliate_redirect(monkeypatch):
    def fail(*_args, **_kwargs):
        raise links.PartnerLinkNotConfigured("program is not enabled")

    monkeypatch.setattr(links, "create_partner_link", fail)
    url, mode = links.resolve_hotel_link(
        "Таиланд",
        checkin="2099-02-10",
        nights=10,
        adults=2,
    )

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert mode == "redirect"
    assert parsed.netloc == "tp.media"
    assert qs["marker"] == ["778488.tg_hotels"]
    assert qs["p"] == ["5916"]
    assert "travel.yandex.ru" in qs["u"][0]


def test_esim_resolution_falls_back_to_affiliate_redirect(monkeypatch):
    def fail(*_args, **_kwargs):
        raise links.PartnerLinkError("provider unavailable")

    monkeypatch.setattr(links, "create_partner_link", fail)
    url, mode = links.resolve_esim_link("Шри-Ланка")

    qs = parse_qs(urlparse(url).query)
    assert mode == "redirect"
    assert qs["marker"] == ["778488.tg_esim"]
    assert qs["p"] == ["8310"]
    assert qs["campaign_id"] == ["541"]
    assert "sri-lanka-esim" in qs["u"][0]


def test_telegram_partner_endpoint_resolves_hotel(monkeypatch):
    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)
    captured = {}

    def fake_resolve(destination, *, checkin, nights, adults):
        captured.update(
            destination=destination,
            checkin=checkin,
            nights=nights,
            adults=adults,
        )
        return "https://yandex.tp.st/from-telegram", "api"

    monkeypatch.setattr(bot._travelpayouts_links, "resolve_hotel_link", fake_resolve)

    response = bot.app.test_client().post(
        "/miniapp/partner-link",
        json={
            "initData": _signed_init_data(),
            "service": "hotel",
            "destination": "Таиланд",
            "date": "2099-02-10",
            "nights": 10,
            "adults": 2,
        },
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "service": "hotel",
        "url": "https://yandex.tp.st/from-telegram",
        "affiliate": True,
        "mode": "api",
    }
    assert captured == {
        "destination": "Таиланд",
        "checkin": "2099-02-10",
        "nights": 10,
        "adults": 2,
    }


def test_telegram_partner_endpoint_resolves_esim(monkeypatch):
    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)
    monkeypatch.setattr(
        bot._travelpayouts_links,
        "resolve_esim_link",
        lambda destination: ("https://airalo.tp.st/from-telegram", "redirect"),
    )

    response = bot.app.test_client().post(
        "/miniapp/partner-link",
        json={
            "initData": _signed_init_data(),
            "service": "esim",
            "destination": "Вьетнам",
        },
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 200
    assert response.get_json()["service"] == "esim"
    assert response.get_json()["affiliate"] is True
    assert response.get_json()["mode"] == "redirect"


def test_telegram_partner_endpoint_rejects_unknown_service(monkeypatch):
    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)

    response = bot.app.test_client().post(
        "/miniapp/partner-link",
        json={
            "initData": _signed_init_data(),
            "service": "casino",
            "destination": "Таиланд",
        },
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_telegram_partner_endpoint_requires_signed_context(monkeypatch):
    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)

    response = bot.app.test_client().post(
        "/miniapp/partner-link",
        json={
            "initData": "bad",
            "service": "hotel",
            "destination": "Таиланд",
        },
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 401
    assert response.get_json()["ok"] is False
