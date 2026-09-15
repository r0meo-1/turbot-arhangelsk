import base64
import hashlib
import hmac
import time
from datetime import date, timedelta
from urllib.parse import urlencode

from flask import Flask

from shared.vk_miniapp import create_blueprint


FIXTURE_KEY = "fixture-key-only"


def _signed_launch(*, vk_ref="community_messages", vk_platform="desktop_web"):
    params = {
        "vk_app_id": "123",
        "vk_group_id": "999",
        "vk_platform": vk_platform,
        "vk_ref": vk_ref,
        "vk_ts": str(int(time.time())),
        "vk_user_id": "42",
    }
    query = urlencode(sorted(params.items()))
    signature = base64.urlsafe_b64encode(
        hmac.new(FIXTURE_KEY.encode(), query.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    return query + "&sign=" + signature


def _payload():
    return {
        "type": "trip_request",
        "version": 2,
        "destination": "Таиланд",
        "departure": "Архангельск",
        "date": (date.today() + timedelta(days=30)).isoformat(),
        "nights": 10,
        "adults": 2,
        "children": 0,
        "childrenAges": [],
        "budgetMaxRub": 270000,
        "consent": True,
    }


def _client(saved):
    app = Flask(__name__)
    app.register_blueprint(
        create_blueprint(
            lambda uid, info: saved.append((uid, info)),
            lambda: (FIXTURE_KEY, "123", 999),
        )
    )
    return app.test_client()


def test_draft_keeps_signed_vk_launch_attribution():
    saved = []
    response = _client(saved).post(
        "/vk/miniapp/draft",
        json={"launchParams": _signed_launch(), "payload": _payload()},
    )

    assert response.status_code == 200
    assert saved[0][0] == 42
    assert saved[0][1]["source"] == "vk_mini_app"
    assert saved[0][1]["vk_ref"] == "community_messages"
    assert saved[0][1]["vk_platform"] == "desktop_web"


def test_tampered_vk_ref_is_rejected_before_attribution_is_saved():
    saved = []
    launch = _signed_launch().replace("vk_ref=community_messages", "vk_ref=other")
    response = _client(saved).post(
        "/vk/miniapp/draft",
        json={"launchParams": launch, "payload": _payload()},
    )

    assert response.status_code == 401
    assert saved == []
