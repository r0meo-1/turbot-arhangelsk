from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TG_INDEX = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
TG_PUBLISHED_INDEX = (ROOT / "docs" / "miniapp" / "index.html").read_text(encoding="utf-8")
TG_APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
VK_INDEX = (ROOT / "vk-miniapp" / "index.html").read_text(encoding="utf-8")
VK_APP_JS = (ROOT / "vk-miniapp" / "app.js").read_text(encoding="utf-8")
VK_PRIVACY = (ROOT / "vk-miniapp" / "privacy.html").read_text(encoding="utf-8")

SITE_PATH = "https://r0meo1.ru/apreltour/"
WHITE_LABEL_PATH = "https://travel.r0meo1.ru/"
TRANSFER_PATH = "https://r0meo1.ru/apreltour/transfer/"
YANDEX_TRAVEL_PATH = "https://travel.yandex.ru/hotels/"
YANDEX_AFFILIATE_REDIRECT = "https://tp.media/r"
AIRALO_PATHS = (
    "https://www.airalo.com/thailand-esim",
    "https://www.airalo.com/vietnam-esim",
    "https://www.airalo.com/sri-lanka-esim",
)


def test_telegram_miniapp_has_apreltour_site_shortcut():
    assert 'id="apreltour-site"' in TG_INDEX
    assert SITE_PATH in TG_INDEX
    assert "utm_source=telegram" in TG_INDEX
    assert "utm_campaign=turbot_site" in TG_INDEX


def test_telegram_miniapp_routes_flights_to_branded_white_label():
    assert 'id="flights"' in TG_INDEX
    assert WHITE_LABEL_PATH in TG_INDEX
    assert "utm_source=telegram" in TG_INDEX
    assert "utm_campaign=turbot_flights" in TG_INDEX
    assert "https://www.aviasales.ru/" not in TG_INDEX
    assert "turbot_flights_tg" not in TG_INDEX


def test_telegram_miniapp_has_transfer_shortcut():
    assert 'id="transfer"' in TG_INDEX
    assert TRANSFER_PATH in TG_INDEX
    assert "utm_source=telegram" in TG_INDEX
    assert "utm_campaign=turbot_transfer" in TG_INDEX
    assert "https://bot.r0meo1.ru/miniapp/partner-link" in TG_INDEX
    assert "'transfer'" in TG_INDEX
    assert "tg.initData" in TG_INDEX
    assert "destination" in TG_INDEX
    assert "tg.openLink(url)" in TG_INDEX


def test_telegram_miniapp_routes_yandex_travel_through_backend():
    assert 'id="yandex-travel"' in TG_INDEX
    assert YANDEX_TRAVEL_PATH in TG_INDEX
    assert "https://bot.r0meo1.ru/miniapp/partner-link" in TG_INDEX
    assert "'hotel'" in TG_INDEX


def test_telegram_miniapp_routes_airalo_through_backend():
    assert 'id="esim"' in TG_INDEX
    assert "https://www.airalo.com/" in TG_INDEX
    assert "https://bot.r0meo1.ru/miniapp/partner-link" in TG_INDEX
    assert "'esim'" in TG_INDEX


def test_telegram_miniapp_keeps_affiliate_configuration_server_side():
    forbidden = (
        YANDEX_AFFILIATE_REDIRECT,
        "778488",
        "574782",
        "5916",
        "8310",
        "campaign_id",
        "affiliate.searchParams",
    )
    for value in forbidden:
        assert value not in TG_INDEX


def test_telegram_miniapp_keeps_authenticated_backend_crm_handoff():
    assert "https://bot.r0meo1.ru/miniapp/submit" in TG_APP_JS
    assert "source: 'telegram_mini_app'" in TG_APP_JS
    assert "tg.initData" in TG_APP_JS


def test_published_telegram_copy_matches_service_shortcuts():
    assert TG_PUBLISHED_INDEX == TG_INDEX


def test_vk_miniapp_has_no_external_partner_shortcuts():
    forbidden = (
        SITE_PATH,
        WHITE_LABEL_PATH,
        TRANSFER_PATH,
        YANDEX_TRAVEL_PATH,
        YANDEX_AFFILIATE_REDIRECT,
        "booking-link",
        "Booking.com",
        "Airalo",
        "Kiwitaxi",
        "Travelpayouts",
    ) + AIRALO_PATHS
    for value in forbidden:
        assert value not in VK_INDEX
        assert value not in VK_APP_JS


def test_vk_miniapp_uses_same_origin_final_privacy_policy():
    assert 'href="./privacy.html"' in VK_INDEX
    assert 'target="_blank"' in VK_INDEX
    assert 'https://bot.r0meo1.ru/privacy' not in VK_INDEX
    assert 'ЧЕРНОВИК' not in VK_PRIVACY
    assert 'Telegram' not in VK_PRIVACY
    assert 'ВКонтакте' in VK_PRIVACY


def test_vk_miniapp_keeps_signed_draft_crm_handoff():
    assert "source: 'vk_mini_app'" in VK_APP_JS
    assert "fetch('./draft'" in VK_APP_JS
    assert "launchParams: effectiveLaunchParams" in VK_APP_JS
    assert "REVIEW_PAYLOAD" in VK_APP_JS
    assert 'https://vk.ru/im?sel=-' in VK_APP_JS
