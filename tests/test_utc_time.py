from datetime import datetime, timezone
from pathlib import Path

from shared.utc_time import utc_now_naive


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_MODULES = (
    ROOT / "bot.py",
    ROOT / "vk_bot.py",
    ROOT / "website_app.py",
    ROOT / "shared" / "travel_crm.py",
    ROOT / "shared" / "travel_crm_store.py",
)


def test_utc_now_naive_preserves_existing_naive_utc_contract():
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    value = utc_now_naive()
    after = datetime.now(timezone.utc).replace(tzinfo=None)

    assert value.tzinfo is None
    assert before <= value <= after


def test_production_modules_do_not_use_deprecated_datetime_utcnow():
    for path in PRODUCTION_MODULES:
        source = path.read_text(encoding="utf-8")
        assert "datetime.utcnow" not in source, path
