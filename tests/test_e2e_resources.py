import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from e2e_resources import local_server


@pytest.mark.parametrize("fail", [False, True])
def test_server_releases_listening_socket_on_success_and_failure(fail):
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    address = server.server_address
    try:
        with local_server(server):
            assert server.socket.fileno() >= 0
            if fail:
                raise RuntimeError("browser assertion failed")
    except RuntimeError as exc:
        assert fail and str(exc) == "browser assertion failed"
    assert server.socket.fileno() == -1
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(address)


def test_server_closes_socket_when_worker_cannot_start(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    def fail_start(_worker):
        raise RuntimeError("worker unavailable")
    monkeypatch.setattr("e2e_resources.threading.Thread.start", fail_start)
    with pytest.raises(RuntimeError, match="worker unavailable"):
        with local_server(server):
            pytest.fail("must not enter")
    assert server.socket.fileno() == -1
