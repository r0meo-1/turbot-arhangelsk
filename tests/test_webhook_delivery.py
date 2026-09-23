import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from shared.webhook_delivery import DeliveryPending, event_key, run_once


def test_receipts_survive_a_new_process_and_do_not_store_payloads(tmp_path):
    path = tmp_path / "state.sqlite"
    key = event_key("telegram", "bot", 123)
    assert run_once(path, key, lambda: None)
    result = subprocess.run([sys.executable, "-c",
        "import sys; from shared.webhook_delivery import run_once; "
        "assert run_once(sys.argv[1], sys.argv[2], lambda: sys.exit(8)) is False",
        str(path), key], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM webhook_receipts").fetchone()[:2] == (key, "complete")
        assert [r[1] for r in conn.execute("PRAGMA table_info(webhook_receipts)")] == [
            "event_key", "state", "updated_at"]


def test_concurrent_duplicate_never_reenters_handler(tmp_path):
    path = tmp_path / "state.sqlite"
    entered, release = threading.Event(), threading.Event()
    calls = []
    def handler():
        calls.append("sent")
        entered.set()
        assert release.wait(5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_once, path, "same", handler)
        assert entered.wait(5)
        try:
            with pytest.raises(DeliveryPending):
                run_once(path, "same", handler)
        finally:
            release.set()
        assert first.result(timeout=5)
    assert calls == ["sent"]


def test_hard_crash_after_external_effect_is_not_automatically_replayed(tmp_path):
    path = tmp_path / "state.sqlite"
    marker = tmp_path / "external-effect.txt"
    code = """import os, sys
from pathlib import Path
from shared.webhook_delivery import run_once
def effect():
    Path(sys.argv[2]).write_text('sent')
    os._exit(23)
run_once(sys.argv[1], 'crash', effect)
"""
    result = subprocess.run([sys.executable, "-c", code, str(path), str(marker)])
    assert result.returncode == 23
    assert marker.read_text() == "sent"
    with pytest.raises(DeliveryPending):
        run_once(path, "crash", lambda: pytest.fail("would duplicate external effect"))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT state FROM webhook_receipts").fetchone()[0] == "processing"


@pytest.mark.parametrize("fault", [TimeoutError, ConnectionResetError, OSError, sqlite3.OperationalError])
def test_uncertain_side_effect_keeps_receipt_fenced(tmp_path, fault):
    path = tmp_path / "state.sqlite"
    def failed():
        raise fault("simulated timeout after remote acceptance")
    with pytest.raises(fault):
        run_once(path, "event", failed)
    with pytest.raises(DeliveryPending):
        run_once(path, "event", lambda: pytest.fail("unsafe retry"))


def test_database_lock_prevents_processing_then_allows_safe_retry(tmp_path):
    path = tmp_path / "state.sqlite"
    run_once(path, "setup", lambda: None)
    with sqlite3.connect(path) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            run_once(path, "next", lambda: pytest.fail("no receipt committed"))
    assert run_once(path, "next", lambda: None)


def test_readonly_storage_never_calls_handler(tmp_path):
    # A directory is deterministically unwritable as a SQLite file on all OSes.
    with pytest.raises(sqlite3.OperationalError):
        run_once(tmp_path, "event", lambda: pytest.fail("storage unavailable"))


def test_transport_identity_is_namespaced():
    assert len({event_key(c, a, 1) for c in ("telegram", "vk") for a in (1, 2)}) == 4


def test_network_guard_blocks_dns_before_outbound_connection():
    import socket
    with pytest.raises(OSError, match="External network disabled"):
        socket.getaddrinfo("api.telegram.org", 443)


def test_reconciliation_does_not_reopen_completed_deliveries(tmp_path):
    from deploy.webhook_receipts import reconcile
    path = tmp_path / "state.sqlite"
    key = event_key("vk", 99, 33)
    with pytest.raises(TimeoutError):
        run_once(path, key, lambda: (_ for _ in ()).throw(TimeoutError()))
    with sqlite3.connect(path) as conn:
        reconcile(conn, key, "complete")
        with pytest.raises(ValueError, match="already complete"):
            reconcile(conn, key, "retry")
    assert not run_once(path, key, lambda: pytest.fail("duplicate"))


def test_reconciliation_can_allow_a_proven_safe_redelivery(tmp_path):
    from deploy.webhook_receipts import reconcile
    path = tmp_path / "state.sqlite"
    key = event_key("vk", 99, 34)
    with pytest.raises(RuntimeError):
        run_once(path, key, lambda: (_ for _ in ()).throw(RuntimeError()))
    with sqlite3.connect(path) as conn:
        reconcile(conn, key, "retry")
    assert run_once(path, key, lambda: None)
