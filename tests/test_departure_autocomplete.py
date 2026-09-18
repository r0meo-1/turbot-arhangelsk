from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TG_APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
TG_PUBLISHED_APP_JS = (ROOT / "docs" / "miniapp" / "app.js").read_text(encoding="utf-8")
VK_APP_JS = (ROOT / "vk-miniapp" / "app.js").read_text(encoding="utf-8")


def test_departure_autocomplete_is_available_in_both_miniapps():
    for source in (TG_APP_JS, VK_APP_JS):
        assert "DEPARTURE_CITIES" in source
        assert "departure-cities" in source
        assert "departure.setAttribute('list', suggestions.id)" in source
        assert "departure.setAttribute('autocomplete', 'off')" in source
        assert "Архангельск" in source
        assert "Москва" in source
        assert "Санкт-Петербург" in source
        assert "Мурманск" in source


def test_destination_autocomplete_is_available_in_both_miniapps():
    for source in (TG_APP_JS, VK_APP_JS):
        assert "DESTINATIONS" in source
        assert "destination-options" in source
        assert "destination.setAttribute('list', suggestions.id)" in source
        assert "destination.setAttribute('autocomplete', 'off')" in source
        assert "Таиланд" in source
        assert "Вьетнам" in source
        assert "Шри-Ланка" in source
        assert "Египет" in source
        assert "ОАЭ" in source
        assert "Турция" in source
        assert "Танзания" in source
        assert "Пхукет, Таиланд" in source
        assert "Нячанг, Вьетнам" in source
        assert "Хургада, Египет" in source
        assert "Анталья, Турция" in source
        assert "Занзибар, Танзания" in source


def test_published_telegram_autocomplete_matches_source():
    assert TG_PUBLISHED_APP_JS == TG_APP_JS
