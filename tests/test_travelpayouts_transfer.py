import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import bot
from shared import travelpayouts_transfer as transfer


BOT_TOKEN = "123456789:test_bot_token_for_transfer"
ORIGIN = "https://r0meo-1.github.io"


def _signed_init_data(user_id=88011):
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrTransfer",
        "user": json.dumps(
            {"id": user_id, "first_name": "Roma", "username": "tester"},
            separators=(",", ":"),
        ),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_kiwitaxi_country_and_resort_mapping():
    assert transfer.build_kiwitaxi_url("Таиланд") == "https://kiwitaxi.com/en/thailand"
    assert transfer.build_kiwitaxi_url("Пхукет") == "https://kiwitaxi.com/en/thailand"
    assert transfer.build_kiwitaxi_url("Шри-Ланка") == "https://kiwitaxi.com/en/sri-lanka"
    assert transfer.build_kiwitaxi_url("Занзибар") == "https://kiwitaxi.com/en/tanzania"


def test_kiwitaxi_unknown_destination_uses_safe_root():
    assert transfer.build_kiwitaxi_url("Мальдивы") == "https://kiwitaxi.com/en/"


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
                        "partner_url": "https://kiwitaxi.tp.st/example",
                    }]
                }
            }

    def fake_post(url, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "secret-token")
    monkeypatch.setenv("TRAVELPAYOUTS_MARKER", "778488")
    monkeypatch.setenv("TRAVELPAYOUTS_TRS", "574782")
    monkeypatch.setattr(transfer.requests, "post", fake_post)

    result = transfer.create_transfer_partner_link("Пхукет")

    assert result == "https://kiwitaxi.tp.st/example"
    assert captured["url"] == "https://api.travelpayouts.com/links/v1/create"
    assert captured["headers"]["X-Access-Token"] == "secret-token"
    assert captured["json"]["marker"] == 778488
    assert captured["json"]["trs"] == 574782
    assert captured["json"]["links"][0]["sub_id"] == "tg_kiwitaxi_transfer"
    assert captured["json"]["links"][0]["url"] == "https://kiwitaxi.com/en/thailand"


def test_telegram_transfer_endpoint_returns_partner_link(monkeypatch):
    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)
    monkeypatch.setattr(
        bot._travelpayouts_transfer,
        "create_transfer_partner_link",
        lambda destination: "https://kiwitaxi.tp.st/from-telegram",
    )

    response = bot.app.test_client().post(
        "/miniapp/transfer-link",
        json={"initData": _signed_init_data(), "destination": "Пхукет"},
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "url": "https://kiwitaxi.tp.st/from-telegram",
        "affiliate": True,
    }
    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN


def test_telegram_transfer_endpoint_falls_back_when_program_is_unavailable(monkeypatch):
    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)

    def fail(_destination):
        raise transfer.TransferLinkNotConfigured(
            "Kiwitaxi program is not enabled for this project"
        )

    monkeypatch.setattr(bot._travelpayouts_transfer, "create_transfer_partner_link", fail)

    response = bot.app.test_client().post(
        "/miniapp/transfer-link",
        json={"initData": _signed_init_data(), "destination": "Шри-Ланка"},
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "url": "https://kiwitaxi.com/en/sri-lanka",
        "affiliate": False,
    }


def test_telegram_transfer_endpoint_rejects_bad_init_data(monkeypatch):
    monkeypatch.setattr(bot, "BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(bot, "MINI_APP_ORIGIN", ORIGIN)

    response = bot.app.test_client().post(
        "/miniapp/transfer-link",
        json={"initData": "bad", "destination": "Таиланд"},
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 401
    assert response.get_json()["ok"] is False
