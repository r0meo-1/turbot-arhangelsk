from pathlib import Path

bot_path = Path('bot.py')
source = bot_path.read_text(encoding='utf-8')
old = '''def _send_lead_to_mdt_once(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> None:
    """Dispatch a completed request to MDT CRM based on MDT_MODE."""
    mdt_shared.dispatch_lead(
'''
new = '''def _send_lead_to_mdt_once(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> bool:
    """Dispatch a completed request to MDT CRM and return write success."""
    return mdt_shared.dispatch_lead(
'''
assert old in source
source = source.replace(old, new, 1)
bot_path.write_text(source, encoding='utf-8')

test_path = Path('tests/test_bot.py')
tests = test_path.read_text(encoding='utf-8')
name = 'test_send_lead_to_mdt_once_returns_dispatch_result'
if name not in tests:
    tests += r'''


def test_send_lead_to_mdt_once_returns_dispatch_result(monkeypatch):
    calls = []

    def fake_dispatch(*args, **kwargs):
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(bot.mdt_shared, "dispatch_lead", fake_dispatch)

    result = bot._send_lead_to_mdt_once(
        7950,
        {"destination": "Вьетнам", "people": "2", "budget": 250000},
        "Telegram @dispatch",
        "Roman",
    )

    assert result is True
    assert len(calls) == 1
'''

test_path.write_text(tests, encoding='utf-8')
