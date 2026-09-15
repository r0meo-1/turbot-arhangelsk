from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_telegram_miniapp_pages_copy_matches_source():
    for name in ("index.html", "app.js", "styles.css"):
        source = (ROOT / "miniapp" / name).read_bytes()
        pages = (ROOT / "docs" / "miniapp" / name).read_bytes()
        assert pages == source, f"docs/miniapp/{name} drifted from miniapp/{name}"
