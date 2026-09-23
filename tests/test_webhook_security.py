import importlib
import itertools
import os
import sqlite3

import pytest

os.environ.setdefault("BOT_TOKEN", "dummy-token")
os.environ.setdefault("ADMIN_ID", "999")
os.environ.setdefault("VK_ACCESS_TOKEN", "dummy-token")
os.environ.setdefault("DIALOG_TIMEOUT_HOURS", "0")
os.environ.setdefault("SYNC_COMPLETION", "true")


@pytest.fixture(params=["telegram", "vk"])
def endpoint(request, monkeypatch, tmp_path):
    channel = request.param
    module = importlib.import_module("bot" if channel == "telegram" else "vk_bot")
    monkeypatch.setattr(module, "DATABASE_PATH", str(tmp_path / "state.sqlite"))
    monkeypatch.setattr(module, "save_state", lambda: None)
    monkeypatch.setattr(module, "TELEGRAM_SECRET_TOKEN" if channel == "telegram" else "VK_SECRET_KEY", "test-secret")
    if channel == "vk":
        monkeypatch.setattr(module, "VK_GROUP_ID", 999)
    calls = []
    if channel == "telegram":
        monkeypatch.setattr(module, "dispatch_update", lambda data, **kw: calls.append(data))
        data = {"update_id": 123, "message": {"chat": {"id": 123}, "from": {"id": 123}, "text": "hello"}}
        headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
        url = "/webhook"
    else:
        monkeypatch.setattr(module, "_process_message", lambda data: calls.append(data))
        data = {"type": "message_new", "group_id": 999, "secret": "test-secret", "event_id": "123",
                "object": {"message": {"from_id": 123, "peer_id": 123, "text": "hello"}}}
        headers, url = {}, "/vk/webhook"
    return channel, module, data, headers, url, calls


def post(endpoint, data=None, headers=None):
    _, module, payload, defaults, url, _ = endpoint
    return module.app.test_client().post(url, json=payload if data is None else data,
                                       headers=defaults if headers is None else headers)


def test_missing_configuration_fails_closed(endpoint, monkeypatch):
    channel, module, _, _, _, calls = endpoint
    monkeypatch.setattr(module, "TELEGRAM_SECRET_TOKEN" if channel == "telegram" else "VK_SECRET_KEY", "")
    assert post(endpoint).status_code == 503
    assert calls == []


@pytest.mark.parametrize("secret", ["", "wrong", "wrong-юникод"])
def test_bad_auth_has_no_writes_or_side_effects(endpoint, secret):
    channel, module, data, headers, _, calls = endpoint
    if channel == "telegram":
        headers["X-Telegram-Bot-Api-Secret-Token"] = secret
    else:
        data["secret"] = secret
    assert post(endpoint).status_code == 403
    assert calls == []
    assert not os.path.exists(module.DATABASE_PATH)


@pytest.mark.parametrize("bad", [[], ["type"], "text", 9, True])
def test_nonobject_json_is_rejected(endpoint, bad):
    assert post(endpoint, bad).status_code == 400
    assert endpoint[-1] == []


def test_oversize_body_and_invalid_json(endpoint):
    _, module, _, headers, url, calls = endpoint
    client = module.app.test_client()
    assert client.post(url, data="{" + "x" * (1024 * 1024), headers=headers,
                       content_type="application/json").status_code == 413
    assert client.post(url, data="{broken", headers=headers,
                       content_type="application/json").status_code == 400
    assert calls == []


@pytest.mark.parametrize("bad", [[], 42, {"text": []}, {"chat": []}])
def test_malformed_message_is_rejected(endpoint, bad):
    channel, _, data, _, _, calls = endpoint
    if channel == "telegram":
        data["message"] = bad
    else:
        data["object"]["message"] = bad
    assert post(endpoint).status_code == 400
    assert calls == []


def test_retry_after_memory_reset_does_not_repeat_effects(endpoint):
    channel, module, _, _, _, calls = endpoint
    assert post(endpoint).status_code == 200
    if channel == "telegram":
        module._seen_update_ids.clear()
    assert post(endpoint).status_code == 200
    assert len(calls) == 1


def test_failure_is_not_acknowledged_or_automatically_replayed(endpoint, monkeypatch):
    channel, module, _, _, _, calls = endpoint
    def failed(*args, **kwargs):
        calls.append("remote accepted, response lost")
        raise TimeoutError("synthetic")
    monkeypatch.setattr(module, "dispatch_update" if channel == "telegram" else "_process_message", failed)
    assert post(endpoint).status_code == 503
    assert post(endpoint).status_code == 503
    assert len(calls) == 1


def test_persistence_failure_never_acknowledges(endpoint, monkeypatch):
    channel, module, _, _, _, _ = endpoint
    if channel == "telegram":
        monkeypatch.setattr(module, "dispatch_update", lambda *a, **kw: (_ for _ in ()).throw(sqlite3.OperationalError()))
    else:
        monkeypatch.setattr(module, "save_state", lambda: (_ for _ in ()).throw(sqlite3.OperationalError()))
    assert post(endpoint).status_code == 503


@pytest.mark.parametrize("bad", [None, [], {}, 4, True])
def test_vk_secret_types_are_rejected(monkeypatch, bad):
    vk = importlib.import_module("vk_bot")
    monkeypatch.setattr(vk, "VK_GROUP_ID", 999)
    monkeypatch.setattr(vk, "VK_SECRET_KEY", "secret")
    response = vk.app.test_client().post("/vk/webhook", json={"type": "message_new", "group_id": 999, "secret": bad})
    assert response.status_code == 403


@pytest.mark.parametrize("kind", ["confirmation", "message_new", "app_payload"])
def test_vk_wrong_community_is_rejected(monkeypatch, kind):
    vk = importlib.import_module("vk_bot")
    monkeypatch.setattr(vk, "VK_GROUP_ID", 999)
    monkeypatch.setattr(vk, "VK_SECRET_KEY", "test-secret")
    response = vk.app.test_client().post("/vk/webhook", json={"type": kind, "group_id": 1000, "secret": "test-secret"})
    assert response.status_code == 403


def test_vk_lead_storage_failure_preserves_review_and_has_no_success_effects(monkeypatch):
    vk = importlib.import_module("vk_bot")
    monkeypatch.setattr(vk, "user_data", {123: {"state": vk.STATE_CONTACT}})
    def unavailable(*a, **kw):
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(vk, "save_lead", unavailable)
    monkeypatch.setattr(vk, "_record_ops_metric", lambda *a: None)
    monkeypatch.setattr(vk, "_confirm_to_user", lambda *a: pytest.fail("false success"))
    monkeypatch.setattr(vk, "_notify_admin", lambda *a: pytest.fail("lead not durable"))
    with pytest.raises(sqlite3.OperationalError):
        vk.handle_completion(123, "synthetic", {})
    assert vk.user_data[123] == {"state": vk.STATE_CONTACT}


# Full factorial coverage is stronger than pairwise for these five factors:
# channel x auth x receipt storage x downstream outcome x new/replayed event.
@pytest.mark.parametrize("auth,storage,outcome,replay", list(itertools.product(
    ("valid", "invalid", "missing"), ("available", "locked"),
    ("success", "timeout", "http500"), (False, True))))
def test_webhook_factor_matrix(endpoint, monkeypatch, auth, storage, outcome, replay):
    channel, module, data, headers, _, calls = endpoint
    if auth != "valid":
        if channel == "telegram":
            headers["X-Telegram-Bot-Api-Secret-Token"] = "bad" if auth == "invalid" else ""
        else:
            data["secret"] = "bad" if auth == "invalid" else ""
    def deliver(*a, **kw):
        calls.append("attempt")
        if outcome != "success":
            raise TimeoutError("simulated timeout") if outcome == "timeout" else RuntimeError("simulated HTTP 500")
    monkeypatch.setattr(module, "dispatch_update" if channel == "telegram" else "_process_message", deliver)
    conn = sqlite3.connect(module.DATABASE_PATH)
    try:
        if storage == "locked":
            conn.execute("CREATE TABLE lock_probe (value INTEGER)")
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
        expected = 403 if auth != "valid" else 503 if storage == "locked" or outcome != "success" else 200
        assert post(endpoint).status_code == expected
        if replay:
            assert post(endpoint).status_code == expected
        assert len(calls) == (1 if auth == "valid" and storage == "available" else 0)
    finally:
        conn.rollback()
        conn.close()
