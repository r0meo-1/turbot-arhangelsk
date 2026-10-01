from datetime import date
import json

import pytest
import requests

from shared.biblio_globus import ExportClient, ExportError, ExportSettings


class Response:
    def __init__(self, payload=None, status=200, raw=None):
        self.status_code = status
        self.data = json.dumps(payload).encode() if raw is None else raw
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.data

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.cookies = requests.cookies.RequestsCookieJar()
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        self.cookies.set("Z1", str(len(self.calls)), domain=".bgoperator.ru")
        return item

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        for name in ("A1", "Z1", "L"):
            self.cookies.set(name, "synthetic", domain=".bgoperator.ru")
        return Response(status=302)

    def close(self):
        self.closed = True


def enabled(**kwargs):
    return ExportSettings(enabled=True, login="synthetic-login", password="synthetic-password", **kwargs)


def test_disabled_makes_no_requests_and_repr_hides_credentials():
    session = Session([])
    with ExportClient(ExportSettings(login="secret-user", password="secret-pwd"), session=session) as client:
        with pytest.raises(ExportError, match="disabled"):
            client.countries()
    assert session.calls == []
    assert "secret" not in repr(client.settings)
    assert not session.closed  # borrowed session


def test_one_auth_retry_posts_secrets_only_to_fixed_login_host():
    denied, success = Response(status=401), Response([{"id": "1", "title_ru": "Synthetic"}])
    session = Session([denied, success])
    client = ExportClient(enabled(), session=session)
    assert client.countries()[0]["id"] == "1"
    assert [call[0] for call in session.calls] == ["GET", "POST", "GET"]
    method, url, kwargs = session.calls[1]
    assert url == "https://login.bgoperator.ru/auth"
    assert kwargs["data"] == {"login": "synthetic-login", "pwd": "synthetic-password"}
    assert not kwargs["allow_redirects"]
    for method, url, kwargs in session.calls:
        assert "synthetic" not in url
        assert "pwd" not in kwargs.get("params", {})
        assert not kwargs["allow_redirects"]
    assert denied.closed and success.closed


def test_repeated_401_does_not_loop():
    session = Session([Response(status=401), Response(status=401)])
    with pytest.raises(ExportError, match="HTTP 401"):
        ExportClient(enabled(), session=session).countries()
    assert len(session.calls) == 3


@pytest.mark.parametrize("response,message", [
    (Response(status=302), "HTTP 302"),
    (Response(status=429), "HTTP 429"),
    (Response(raw=b"bad JSON secret response"), "invalid export JSON"),
    (Response("secret response"), "invalid export response shape"),
    (Response(raw=b"[" + b"0," * 50 + b"0]"), "size limit"),
])
def test_bad_responses_closed_and_diagnostics_do_not_echo_payload(response, message):
    client = ExportClient(enabled(max_response_bytes=100), session=Session([response]))
    with pytest.raises(ExportError, match=message) as error:
        client.countries()
    assert "secret" not in str(error.value)
    assert response.closed


@pytest.mark.parametrize("error,message", [
    (requests.Timeout("secret cookie"), "timeout"),
    (requests.ConnectionError("https://secret?pwd=secret"), "transport failure"),
])
def test_transport_errors_are_redacted(error, message):
    with pytest.raises(ExportError, match=message) as raised:
        ExportClient(enabled(), session=Session([error])).countries()
    assert "secret" not in str(raised.value)


def test_read_endpoints_never_follow_booking_urls_or_mutate_prices():
    session = Session([Response([]), Response([]), Response([]), Response({"entries": []}),
                       Response({"href0": "https://evil.example/booking", "entries": []})])
    client = ExportClient(enabled(), session=session)
    client.countries()
    client.resorts()
    client.hotels("123")
    client.price_lists("1", "2")
    assert client.prices("1", "2", "3", date(2026, 10, 31), 10)["entries"] == []
    for method, url, kwargs in session.calls:
        assert method == "GET"
        assert url.startswith("https://export.bgoperator.ru/")
        assert kwargs["headers"]["Accept-Encoding"] == "gzip"
    params = session.calls[-1][2]["params"]
    assert params["action"] == "price" and params["novirt"] == 0
    assert params["data"] == "31.10.2026" and params["f7"] == 10


@pytest.mark.parametrize("identifier", ["https://evil.example", "1&pwd=x", "-1", "", "1" * 21])
def test_invalid_ids_rejected_before_network(identifier):
    session = Session([])
    with pytest.raises(ExportError, match="identifier"):
        ExportClient(enabled(), session=session).hotels(identifier)
    assert session.calls == []


def test_owned_session_is_closed(monkeypatch):
    session = Session([])
    monkeypatch.setattr(requests, "Session", lambda: session)
    with ExportClient(ExportSettings()):
        pass
    assert session.closed


@pytest.mark.parametrize("nights", [0, 29, True, "7"])
def test_invalid_nights_make_no_request(nights):
    session = Session([])
    with pytest.raises(ExportError, match="dates or nights"):
        ExportClient(enabled(), session=session).prices("1", "2", "3", date(2026, 10, 31), nights)
    assert session.calls == []


def test_auth_without_cookies_stops_before_replay():
    session = Session([Response(status=401)])
    session.post = lambda *args, **kwargs: Response(status=302)
    with pytest.raises(ExportError, match="cookies missing"):
        ExportClient(enabled(), session=session).countries()
    assert len(session.calls) == 1
