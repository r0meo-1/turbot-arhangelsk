"""Keep the regression suite offline and webhook receipts isolated per case."""
import os
import socket
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Test imports must not start housekeeping against mutable module DB paths.
# Individual retention/follow-up tests set their own explicit configuration.
for setting in ("DIALOG_TIMEOUT_HOURS", "FOLLOWUP_DELAY_HOURS", "DATA_RETENTION_DAYS",
                "PARTNER_ANALYTICS_RETENTION_DAYS", "FUNNEL_ANALYTICS_RETENTION_DAYS",
                "OPS_METRICS_RETENTION_DAYS"):
    os.environ[setting] = "0"
for setting in ("ADMIN_ERROR_ALERTS", "MDT_ENABLED", "VK_MDT_RETRY_ENABLED", "TUTU_ENABLED"):
    os.environ[setting] = "false"
os.environ["DATABASE_PATH"] = str(Path(tempfile.gettempdir()) / f"turbot_suite_{os.getpid()}.sqlite")
for setting in ("BOT_DISPLAY_NAME", "BOT_SHORT_DESCRIPTION", "BOT_DESCRIPTION"):
    os.environ[setting] = ""


_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_getaddrinfo = socket.getaddrinfo


def _local(address):
    host = address[0] if isinstance(address, tuple) else address
    if host not in ("localhost", "127.0.0.1", "::1", b"localhost"):
        raise OSError("External network disabled in regression tests; inject a transport")


def _offline_connect(sock, address):
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        _local(address)
    return _connect(sock, address)


def _offline_connect_ex(sock, address):
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        _local(address)
    return _connect_ex(sock, address)


def _offline_getaddrinfo(host, *args, **kwargs):
    if host is not None:
        _local(host)
    return _getaddrinfo(host, *args, **kwargs)


# Install before collection: importing the bots can start background workers.
socket.socket.connect = _offline_connect
socket.socket.connect_ex = _offline_connect_ex
socket.getaddrinfo = _offline_getaddrinfo


@pytest.fixture(autouse=True)
def isolate_webhook_receipts():
    for name in ("bot", "vk_bot"):
        module = sys.modules.get(name)
        path = getattr(module, "DATABASE_PATH", None)
        if not path or path == ":memory:" or not Path(path).is_file():
            continue
        conn = sqlite3.connect(path)
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='webhook_receipts'").fetchone():
                with conn:
                    conn.execute("DELETE FROM webhook_receipts")
        finally:
            conn.close()
