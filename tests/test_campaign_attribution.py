from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "docs" / "turbot"
CANONICAL = ROOT / "docs" / "apreltour"
PAGES_ROOT = ROOT / "docs"


def test_campaign_landing_routes_ctas_through_bounded_attribution_script():
    html = (LANDING / "index.html").read_text(encoding="utf-8")
    script = (LANDING / "attribution.js").read_text(encoding="utf-8")

    assert 'data-turbot-link' in html
    assert 'data-vk-turbot-link' in html
    assert 'attribution.js' in html
    assert 'https://t.me/apreltour_bot?start=landing' in html
    assert 'https://vk.me/club240310110?ref=landing' in html
    assert "utm_content" in script
    assert "utm_campaign" in script
    assert "source_tag" in script
    assert "^[A-Za-z0-9_-]{1,64}$" in script
    assert "https://t.me/apreltour_bot?start=" in script
    assert "https://vk.me/club240310110?ref=" in script


def test_pages_root_canonical_and_landing_expose_current_turbot_experience():
    root_html = (PAGES_ROOT / "index.html").read_text(encoding="utf-8")
    canonical_html = (CANONICAL / "index.html").read_text(encoding="utf-8")
    landing_html = (LANDING / "index.html").read_text(encoding="utf-8")

    for html in (root_html, canonical_html, landing_html):
        assert "TurBot × АПРЕЛЬ тур" in html
        assert "Наталья Ильина" in html
        assert "Вьетнам" in html
        assert "Таиланд" in html
        assert "Шри-Ланка" in html
        assert "Танзания" in html
        assert "data-turbot-link" in html
        assert "data-vk-turbot-link" in html

    assert 'href="turbot/styles.css"' in root_html
    assert 'src="turbot/attribution.js"' in root_html
    assert 'href="../turbot/styles.css"' in canonical_html
    assert 'src="../turbot/attribution.js"' in canonical_html
    assert 'href="styles.css"' in landing_html
    assert 'src="attribution.js"' in landing_html


def test_landing_has_required_legal_pages():
    for name, heading in {
        "privacy.html": "Политика обработки персональных данных",
        "consent.html": "Согласие на обработку персональных данных",
        "terms.html": "Условия использования TurBot",
        "moderation.html": "Правила модерации и безопасного использования",
    }.items():
        text = (LANDING / name).read_text(encoding="utf-8")
        assert heading in text
        assert "Наталья Ильина" in text
        assert "+7 902 193-29-23" in text
