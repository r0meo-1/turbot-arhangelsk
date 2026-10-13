import json

from scripts import alean_live_smoke as smoke
from shared import tourvisor as tv


def test_missing_credentials_do_not_call_supplier(monkeypatch, capsys):
    monkeypatch.delenv("ALEAN_USERNAME", raising=False)
    monkeypatch.delenv("ALEAN_PASSWORD", raising=False)
    monkeypatch.setattr(smoke.alean, "_catalog", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    assert smoke.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["syntheticSearch"] == "not_run"


def test_readonly_search_checks_access_without_enabling_application(monkeypatch, capsys):
    monkeypatch.setenv("ALEAN_USERNAME", "fixture-user")
    monkeypatch.setenv("ALEAN_PASSWORD", "fixture-secret")
    monkeypatch.setenv("ALEAN_ENABLED", "false")
    def catalog(settings, session, action):
        assert action == "GetCountries"
        session.operations.append({"action": action, "status": 200})
        return [{"id": "1", "cid": "RU", "name": "Россия"}]
    def search(settings, session, query):
        assert settings.enabled and settings.username == "fixture-user"
        assert query["people"] == 2 and query["kids_ages"] == [7]
        assert query["budget_scope"] == "total" and query["nights"] == 7
        assert not {"phone", "name", "chat_id"}.intersection(query)
        assert smoke.alean.AleanSettings.from_env().enabled is False
        session.operations.append({"action": "GetTours", "status": 200})
        return tv.SearchResult()
    monkeypatch.setattr(smoke.alean, "_catalog", catalog)
    monkeypatch.setattr(smoke.alean, "search_tours", search)
    assert smoke.main() == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["syntheticSearch"] == "passed" and report["offerCount"] == 0
    assert report["messagesSent"] == report["bookingCalls"] == 0
    assert "fixture-secret" not in output and "fixture-user" not in output


def test_auth_failure_is_sanitized_and_search_is_not_run(monkeypatch, capsys):
    monkeypatch.setenv("ALEAN_USERNAME", "fixture-user")
    monkeypatch.setenv("ALEAN_PASSWORD", "fixture-secret")
    def fail(*args, **kwargs):
        raise RuntimeError("URL containing fixture-secret and fixture-user")
    monkeypatch.setattr(smoke.alean, "_catalog", fail)
    monkeypatch.setattr(smoke.alean, "search_tours", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    assert smoke.main() == 1
    output = capsys.readouterr().out
    report = json.loads(output)
    assert not report["authenticatedCountries"] and report["syntheticSearch"] == "not_run"
    assert report["blocker"] == "RuntimeError"
    assert "fixture-secret" not in output and "fixture-user" not in output
