from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "docs" / "turbot"


def test_campaign_landing_routes_ctas_through_bounded_attribution_script():
    html = (LANDING / "index.html").read_text(encoding="utf-8")
    script = (LANDING / "attribution.js").read_text(encoding="utf-8")

    assert 'data-turbot-link' in html
    assert 'attribution.js' in html
    assert 'https://t.me/apreltour_bot?start=landing' in html
    assert "utm_content" in script
    assert "utm_campaign" in script
    assert "source_tag" in script
    assert "^[A-Za-z0-9_-]{1,64}$" in script
    assert "https://t.me/apreltour_bot?start=" in script
