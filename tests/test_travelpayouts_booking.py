import base64
import hashlib
import hmac
import time
from datetime import date, timedelta
from urllib.parse import parse_qs, urlencode, urlparse

from flask import Flask

from shared import travelpayouts_booking as booking
from shared.vk_miniapp import create_blueprint


SECRET = "test-app-secret"


def trip_info():
    return {
        "destination": "Пхукет",
        "dates": (date.today() + timedelta(days=30)).isoformat(),
        "nights": 10,
        "people": "2",
        "kids_ages": [7],
    }


def vk_payload():
    return {
        "type": "trip_request",
        "version": 2,
        "destination": "Пхукет",
        "departure": "Москва",
        "date": (date.today() + timedelta(days=30)).isoformat(),
        "nights": 10,
        "adults": 2,
        "children": 1,
        "childrenAges": [7],
        "budgetMaxRub": 270000,
        "budgetScope": "total",
        "consent": True,
        "termsAccepted": True,
        "directOnly": False,
    }


def signed():
    params = {
        "vk_app_id": "123",
        "vk_group_id": "999",
        "vk_user_id": "42",
        "vk_ts": str(int(time.time())),
    }
    query = urlencode(sorted(params.items()))
    signature = base64.urlsafe_b64encode(
        hmac.new(SECRET.encode(), query.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    return query + "&sign=" + signature


def test_build_booking_search_url_keeps_trip_parameters():
    url = booking.build_booking_search_url(trip_info())
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    assert parsed.netloc == "www.booking.com"
    assert qs["ss"] == ["Пхукет"]
    assert qs["group_adults"] == ["2"]
    assert qs["group_children"] == ["1"]
    assert qs["age"] == ["7"]
    assert qs["checkin"] == [trip_info()["dates"]]
    expected_checkout = (date.fromisoformat(trip_info()["dates"]) + timedelta(days=10)).isoformat()
    assert qs["checkout"] == [expected_checkout]


def test_partner_link_api_request(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "result": {
                    "links": [{
                        "code": "success",
                        "partner_url": "https://booking.tp.st/example",
                    }]
                }
            }

    def fake_post(url, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "secret-token")
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "778488")
    monkeypatch.setenv("TRAVELPAYOUTS_TRS", "574782")
    monkeypatch.setattr(booking.requests, "post", fake_post)

    result = booking.create_booking_partner_link(trip_info())

    assert result == "https://booking.tp.st/example"
    assert captured["url"] == "https://api.travelpayouts.com/links/v1/create"
    assert captured["headers"]["X-Access-Token"] == "secret-token"
    assert captured["json"]["marker"] == 778488
    assert captured["json"]["trs"] == 574782
    assert captured["json"]["links"][0]["sub_id"] == "vk_booking_search"
    assert captured["json"]["links"][0]["url"].startswith("https://www.booking.com/searchresults.html?")


def test_vk_booking_link_endpoint(monkeypatch):
    monkeypatch.setattr(
        booking,
        "create_booking_partner_link",
        lambda info: "https://booking.tp.st/from-vk",
    )
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda *_: None, lambda: (SECRET, "123", 999)))
    response = app.test_client().post(
        "/vk/miniapp/booking-link",
        json={"launchParams": signed(), "payload": vk_payload()},
    )
    assert response.status_code == 200
    assert response.json == {"ok": True, "url": "https://booking.tp.st/from-vk"}


def test_vk_booking_link_requires_signed_vk_context(monkeypatch):
    monkeypatch.setattr(
        booking,
        "create_booking_partner_link",
        lambda info: "https://booking.tp.st/from-vk",
    )
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda *_: None, lambda: (SECRET, "123", 999)))
    response = app.test_client().post(
        "/vk/miniapp/booking-link",
        json={"launchParams": "bad", "payload": vk_payload()},
    )
    assert response.status_code == 401
    assert response.json["ok"] is False


def test_vk_booking_link_reports_program_not_enabled_without_secret_details(monkeypatch):
    def fail(_info):
        raise booking.BookingLinkNotConfigured("Booking.com program is not enabled for this project")

    monkeypatch.setattr(booking, "create_booking_partner_link", fail)
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda *_: None, lambda: (SECRET, "123", 999)))
    response = app.test_client().post(
        "/vk/miniapp/booking-link",
        json={"launchParams": signed(), "payload": vk_payload()},
    )
    assert response.status_code == 503
    assert response.json == {
        "ok": False,
        "error": "Поиск Booking.com ещё не подключён к проекту.",
        "errorCode": "booking_program_not_enabled",
    }


def test_vk_booking_link_reports_invalid_token_as_safe_code(monkeypatch):
    def fail(_info):
        raise booking.BookingLinkNotConfigured("Travelpayouts API token is invalid")

    monkeypatch.setattr(booking, "create_booking_partner_link", fail)
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda *_: None, lambda: (SECRET, "123", 999)))
    response = app.test_client().post(
        "/vk/miniapp/booking-link",
        json={"launchParams": signed(), "payload": vk_payload()},
    )
    assert response.status_code == 503
    assert response.json["errorCode"] == "travelpayouts_token_invalid"