from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")


def test_miniapp_frontend_matches_production_v2_contract():
    assert "https://bot.r0meo1.ru/miniapp/submit" in APP
    assert "YOUR-DOMAIN" not in APP
    assert "version: 2" in APP
    assert "type: 'trip_request'" in APP
    assert "body: JSON.stringify({ initData: tg.initData, payload })" in APP

    for field in (
        "destination",
        "departure",
        "date",
        "nights",
        "adults",
        "children",
        "childrenAges",
        "budgetMaxRub",
        "directOnly",
        "consent",
    ):
        assert field in APP

    assert "https://telegram.org/js/telegram-web-app.js" in INDEX
    assert 'id="trip-form"' in INDEX
    assert 'id="children-ages"' in INDEX
    assert 'name="departure"' in INDEX
    assert 'name="nights"' in INDEX
    assert 'name="adults"' in INDEX
    assert 'name="children"' in INDEX
    assert 'name="budget"' in INDEX


def test_miniapp_keeps_startapp_destination_prefill():
    assert "tgWebAppStartParam" in APP
    assert "thailand: 'Таиланд'" in APP
    assert "vietnam: 'Вьетнам'" in APP


def test_miniapp_save_flow_is_recoverable_on_slow_network():
    assert "REQUEST_TIMEOUT_MS = 15000" in APP
    assert "AbortController" in APP
    assert "navigator.onLine === false" in APP
    assert "review.setAttribute('aria-busy', 'true')" in APP
    assert "edit.disabled = true" in APP
    assert "tg?.BackButton?.hide?.()" in APP
    assert "tg?.BackButton?.show?.()" in APP
    assert "Повторить сохранение" in APP


def test_miniapp_destination_chips_expose_pressed_state():
    assert "chip.setAttribute('aria-pressed', String(active))" in APP
