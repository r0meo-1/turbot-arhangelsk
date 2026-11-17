import json
import sys

import httpx

from shared.ai_provider import build_selection_provider
from tools import yandex_ai_smoke as smoke


def test_config_probe_prints_presence_only(monkeypatch, capsys):
    monkeypatch.setenv("YANDEX_AI_API_KEY", "private-synthetic-marker")
    monkeypatch.setenv("YANDEX_AI_MODEL", "gpt://synthetic-folder/model")
    monkeypatch.setattr(sys, "argv", ["smoke"])
    assert smoke.main() == 0
    output = capsys.readouterr().out
    assert "private-synthetic-marker" not in output
    assert json.loads(output)["key_present"]


def test_live_without_config_makes_no_calls(monkeypatch, capsys):
    monkeypatch.delenv("YANDEX_AI_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_AI_MODEL", raising=False)
    monkeypatch.setattr(sys, "argv", ["smoke", "--live"])
    assert smoke.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["cases"] == []


def test_smoke_stops_on_first_api_error_and_hides_body(monkeypatch, capsys):
    provider = build_selection_provider(
        "yandex", yandex_api_key="synthetic", yandex_model="gpt://synthetic-folder/model")
    provider.client._client.close()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={"error": {"message": "private-synthetic-marker"}})

    provider.client._client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(smoke, "build_selection_provider", lambda *a, **kw: provider)
    monkeypatch.setattr(sys, "argv", ["smoke", "--live"])
    # main disables logs for a standalone process; restore global state here.
    import logging
    previous = logging.root.manager.disable
    try:
        assert smoke.main() == 1
    finally:
        logging.disable(previous)
    output = capsys.readouterr().out
    assert "private-synthetic-marker" not in output
    report = json.loads(output)
    assert len(calls) == len(report["cases"]) == 1
    assert report["cases"][0]["error"] == "InternalServerError"
    assert not report["transport_success"]
