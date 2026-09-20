from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "vk-miniapp" / "app.js").read_text(encoding="utf-8")


def test_vk_miniapp_save_flow_has_recoverable_network_feedback():
    assert "REQUEST_TIMEOUT_MS = 15000" in APP
    assert "navigator.onLine === false" in APP
    assert "AbortController" in APP
    assert "response.json().catch(() => ({}))" in APP
    assert "$('review').setAttribute('aria-busy', 'true')" in APP
    assert "$('review').setAttribute('aria-busy', 'false')" in APP
    assert "Повторите попытку" in APP
