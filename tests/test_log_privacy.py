import logging
from pathlib import Path

from shared.log_privacy import correlation_id


ROOT = Path(__file__).resolve().parents[1]


def test_correlation_id_is_keyed_stable_and_namespace_scoped(monkeypatch):
    monkeypatch.setenv("LOG_CORRELATION_KEY", "unit-test-secret")
    first = correlation_id(123456789, namespace="tg-user")
    assert first == correlation_id("123456789", namespace="tg-user")
    assert first.startswith("tg-user:")
    assert "123456789" not in first
    assert first != correlation_id(123456789, namespace="vk-user")


def test_correlation_id_fails_closed_without_key(monkeypatch):
    monkeypatch.delenv("LOG_CORRELATION_KEY", raising=False)
    assert correlation_id(123456789, namespace="tg-user") == "tg-user:redacted"
    assert correlation_id("", namespace="tg-user") == "tg-user:none"


def test_routine_log_sources_do_not_use_known_raw_platform_id_messages():
    sources = {
        "shared/mdt.py": (ROOT / "shared" / "mdt.py").read_text(encoding="utf-8"),
        "bot.py": (ROOT / "bot.py").read_text(encoding="utf-8"),
        "vk_bot.py": (ROOT / "vk_bot.py").read_text(encoding="utf-8"),
    }
    forbidden = (
        "Failed to create temp tourist in MDT for chat %s",
        "Lead sent to MDT for chat %s",
        "Rejected Telegram web_app_data for chat %s",
        "Concurrent completion ignored for chat_id=%s",
        "Invalid Mini App snapshot for chat_id=%s",
        "VK lead from %s delivered to Telegram chat %s",
    )
    combined = "\n".join(sources.values())
    for marker in forbidden:
        assert marker not in combined
