from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TG_INDEX = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
TG_PUBLISHED_INDEX = (ROOT / "docs" / "miniapp" / "index.html").read_text(encoding="utf-8")
VK_INDEX = (ROOT / "vk-miniapp" / "index.html").read_text(encoding="utf-8")

TRANSFER_PATH = "https://r0meo1.ru/apreltour/transfer/"
YANDEX_TRAVEL_PATH = "https://travel.yandex.ru/hotels/"
YANDEX_AFFILIATE_REDIRECT = "https://tp.media/r"


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


def test_published_telegram_copy_matches_transfer_and_yandex_shortcuts():
    assert TG_PUBLISHED_INDEX == TG_INDEX


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
