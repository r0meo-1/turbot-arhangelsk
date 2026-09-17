from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TG_INDEX = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
TG_PUBLISHED_INDEX = (ROOT / "docs" / "miniapp" / "index.html").read_text(encoding="utf-8")
VK_INDEX = (ROOT / "vk-miniapp" / "index.html").read_text(encoding="utf-8")

TRANSFER_PATH = "https://r0meo1.ru/apreltour/transfer/"


def test_telegram_miniapp_has_transfer_shortcut():
    assert 'id="transfer"' in TG_INDEX
    assert TRANSFER_PATH in TG_INDEX
    assert "utm_source=telegram" in TG_INDEX
    assert "utm_campaign=turbot_transfer" in TG_INDEX
    assert "tg.openLink(url)" in TG_INDEX


def test_published_telegram_copy_matches_transfer_shortcut():
    assert TG_PUBLISHED_INDEX == TG_INDEX


def test_vk_miniapp_has_transfer_shortcut():
    assert 'id="transfer"' in VK_INDEX
    assert TRANSFER_PATH in VK_INDEX
    assert "utm_source=vk" in VK_INDEX
    assert "utm_campaign=turbot_transfer" in VK_INDEX
    assert "window.open(url, '_blank'" in VK_INDEX
