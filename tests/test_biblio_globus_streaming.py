"""Exercise the export reader against real HTTP streams on loopback only."""
from contextlib import contextmanager
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from types import SimpleNamespace

import pytest
import requests

from shared import biblio_globus
from shared.biblio_globus import ExportClient, ExportError, ExportSettings


@contextmanager
def local_response(body, *, encoding=None, drip=False):
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            if encoding:
                self.send_header("Content-Encoding", encoding)
            self.end_headers()
            try:
                if drip:
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        if stop.wait(0.05):
                            break
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02})
    thread.start()
    session = requests.Session()
    session.trust_env = False

    class LocalSession:
        response = None

        def get(self, url, **kwargs):
            assert url == "https://export.bgoperator.ru/yandex"
            assert kwargs["params"] == {"action": "countries"}
            self.response = session.get(f"http://127.0.0.1:{server.server_port}/", **kwargs)
            return self.response

    transport = LocalSession()
    try:
        yield transport
    finally:
        stop.set()
        session.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.parametrize("encoding", [None, "gzip"])
def test_trickled_body_times_out_before_the_entire_buffer_arrives(monkeypatch, encoding):
    # Accelerate only the client's clock; sockets, server and elapsed assertion
    # use real time. The pre-fix 64 KiB reader waits for the whole slow body.
    began = time.monotonic()
    monkeypatch.setattr(biblio_globus, "time", SimpleNamespace(monotonic=lambda: (time.monotonic() - began) * 50))
    body = b'["synthetic slow response"]'
    if encoding:
        body = gzip.compress(body)
    with local_response(body, encoding=encoding, drip=True) as transport:
        started = time.monotonic()
        with pytest.raises(ExportError, match="timeout"):
            ExportClient(ExportSettings(enabled=True, login="test", password="test", timeout=1),
                         session=transport).countries()
        elapsed = time.monotonic() - started
        assert elapsed < 0.9, f"reader waited {elapsed:.2f}s for a whole response buffer"
        assert transport.response.raw.closed


@pytest.mark.parametrize("encoding", [None, "gzip", "x-gzip"])
def test_valid_responses_remain_readable(encoding):
    body = b'[{"id":"7","title_ru":"Synthetic"}]'
    if encoding:
        # Concatenated members are valid gzip too; use a boundary inside JSON.
        body = gzip.compress(body[:10]) + gzip.compress(body[10:])
    with local_response(body, encoding=encoding) as transport:
        client = ExportClient(ExportSettings(enabled=True, login="test", password="test"),
                              session=transport)
        assert client.countries() == [{"id": "7", "title_ru": "Synthetic"}]
        assert transport.response.raw.closed


def test_gzip_limit_applies_to_decompressed_bytes():
    body = gzip.compress(b'["' + b"a" * 100000 + b'"]')
    with local_response(body, encoding="gzip") as transport:
        client = ExportClient(ExportSettings(enabled=True, login="test", password="test",
                                              max_response_bytes=1024), session=transport)
        with pytest.raises(ExportError, match="size limit"):
            client.countries()
        assert transport.response.raw.closed


@pytest.mark.parametrize("body,encoding", [
    (b"private-provider-response", "gzip"),
    (gzip.compress(b"[]")[:-4], "gzip"),
    (gzip.compress(b"[]")[:10] + b"\xff" * 8, "gzip"),
    (b"private-provider-response", "br"),
])
def test_bad_compressed_responses_are_closed_without_echoing_payload(body, encoding):
    with local_response(body, encoding=encoding) as transport:
        client = ExportClient(ExportSettings(enabled=True, login="test", password="test"),
                              session=transport)
        with pytest.raises(ExportError) as error:
            client.countries()
        assert "private-provider" not in str(error.value)
        assert transport.response.raw.closed


def test_legacy_raw_reader_without_read1_still_decodes_gzip():
    import io
    from urllib3.response import HTTPResponse

    raw = HTTPResponse(body=io.BytesIO(gzip.compress(b'[{"id":"7"}]')), preload_content=False)
    legacy = SimpleNamespace(read=raw.read)
    response = SimpleNamespace(raw=legacy, headers={"Content-Encoding": "gzip"},
                               status_code=200, close=raw.close)
    transport = SimpleNamespace(get=lambda *args, **kwargs: response)
    client = ExportClient(ExportSettings(enabled=True, login="test", password="test"),
                          session=transport)
    assert client.countries() == [{"id": "7"}]
    assert raw.closed


def test_raw_socket_timeout_is_redacted_and_response_closed():
    from urllib3.exceptions import ReadTimeoutError

    class Raw:
        def read1(self, *args, **kwargs):
            raise ReadTimeoutError(None, "/?cookie=private-value", "private-value")

    response = SimpleNamespace(raw=Raw(), headers={}, status_code=200, closed=False)

    def close():
        response.closed = True

    response.close = close
    transport = SimpleNamespace(get=lambda *args, **kwargs: response)
    with ExportClient(ExportSettings(enabled=True, login="test", password="test"),
                      session=transport) as client:
        with pytest.raises(ExportError, match="timeout") as error:
            client.countries()
    assert "private-value" not in str(error.value)
    assert response.closed
