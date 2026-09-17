from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TG_INDEX = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
TG_PUBLISHED_INDEX = (ROOT / "docs" / "miniapp" / "index.html").read_text(encoding="utf-8")
TG_APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
VK_INDEX = (ROOT / "vk-miniapp" / "index.html").read_text(encoding="utf-8")
VK_APP_JS = (ROOT / "vk-miniapp" / "app.js").read_text(encoding="utf-8")

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
    assert "tg.openLink(url)" in TG_INDEX


def test_telegram_miniapp_has_yandex_travel_affiliate_shortcut():
    assert 'id="yandex-travel"' in TG_INDEX
    assert YANDEX_TRAVEL_PATH in TG_INDEX
    assert YANDEX_AFFILIATE_REDIRECT in TG_INDEX
    assert "affiliate.searchParams.set('marker', '778488')" in TG_INDEX
    assert "affiliate.searchParams.set('trs', '574782')" in TG_INDEX
    assert "affiliate.searchParams.set('p', '5916')" in TG_INDEX
    assert "target.searchParams.set('checkinDate', checkin)" in TG_INDEX
    assert "target.searchParams.set('checkoutDate'" in TG_INDEX
    assert "target.searchParams.set('utm_content', 'telegram')" in TG_INDEX


def test_telegram_miniapp_has_airalo_affiliate_shortcut():
    assert 'id="esim"' in TG_INDEX
    assert all(path in TG_INDEX for path in AIRALO_PATHS)
    assert "778488.turbot_esim_tg" in TG_INDEX
    assert "affiliate.searchParams.set('p', '8310')" in TG_INDEX
    assert "affiliate.searchParams.set('campaign_id', '541')" in TG_INDEX


def test_telegram_miniapp_keeps_authenticated_backend_crm_handoff():
    assert "https://bot.r0meo1.ru/miniapp/submit" in TG_APP_JS
    assert "source: 'telegram_mini_app'" in TG_APP_JS
    assert "tg.initData" in TG_APP_JS


def test_published_telegram_copy_matches_service_shortcuts():
    assert TG_PUBLISHED_INDEX == TG_INDEX


def test_vk_miniapp_has_apreltour_site_shortcut():
    assert 'id="apreltour-site"' in VK_INDEX
    assert SITE_PATH in VK_INDEX
    assert "utm_source=vk" in VK_INDEX
    assert "utm_campaign=turbot_site" in VK_INDEX


def test_vk_miniapp_routes_flights_to_branded_white_label():
    assert 'id="flights"' in VK_INDEX
    assert WHITE_LABEL_PATH in VK_INDEX
    assert "utm_source=vk" in VK_INDEX
    assert "utm_campaign=turbot_flights" in VK_INDEX
    assert "https://www.aviasales.ru/" not in VK_INDEX
    assert "turbot_flights_vk" not in VK_INDEX


def test_vk_miniapp_has_transfer_shortcut():
    assert 'id="transfer"' in VK_INDEX
    assert TRANSFER_PATH in VK_INDEX
    assert "utm_source=vk" in VK_INDEX
    assert "utm_campaign=turbot_transfer" in VK_INDEX
    assert "window.open(url, '_blank'" in VK_INDEX


def test_vk_miniapp_has_yandex_travel_affiliate_shortcut():
    assert 'id="yandex-travel"' in VK_INDEX
    assert YANDEX_TRAVEL_PATH in VK_INDEX
    assert YANDEX_AFFILIATE_REDIRECT in VK_INDEX
    assert "affiliate.searchParams.set('marker', '778488')" in VK_INDEX
    assert "affiliate.searchParams.set('trs', '574782')" in VK_INDEX
    assert "affiliate.searchParams.set('p', '5916')" in VK_INDEX
    assert "target.searchParams.set('checkinDate', checkin)" in VK_INDEX
    assert "target.searchParams.set('checkoutDate'" in VK_INDEX
    assert "target.searchParams.set('utm_content', 'vk')" in VK_INDEX


def test_vk_miniapp_has_airalo_affiliate_shortcut():
    assert 'id="esim"' in VK_INDEX
    assert all(path in VK_INDEX for path in AIRALO_PATHS)
    assert "778488.turbot_esim_vk" in VK_INDEX
    assert "affiliate.searchParams.set('p', '8310')" in VK_INDEX
    assert "affiliate.searchParams.set('campaign_id', '541')" in VK_INDEX


def test_vk_miniapp_keeps_signed_draft_crm_handoff():
    assert "source: 'vk_mini_app'" in VK_APP_JS
    assert "fetch('./draft'" in VK_APP_JS
    assert "launchParams: effectiveLaunchParams" in VK_APP_JS
    assert "REVIEW_PAYLOAD" in VK_APP_JS
