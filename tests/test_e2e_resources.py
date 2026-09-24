"""Resource ownership tests independent of Playwright installation."""
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from e2e_support import isolated_browser, running_server


@pytest.mark.parametrize("fail", [False, True])
def test_server_closes_socket_on_normal_and_exceptional_exit(fail):
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    with pytest.raises(AssertionError, match="synthetic failure") if fail else nullcontext():
        with running_server(server):
            assert server.socket.fileno() >= 0
            if fail:
                raise AssertionError("synthetic failure")
    assert server.socket.fileno() == -1


@pytest.mark.parametrize("online", [False, True])
def test_context_then_browser_close_even_when_assertion_fails(online):
    events, scripts, routes = [], [], []
    context = SimpleNamespace(
        add_init_script=scripts.append,
        route=lambda pattern, handler: routes.append((pattern, handler)),
        close=lambda: events.append("context_closed"),
    )
    browser = SimpleNamespace(
        new_context=lambda **options: context,
        close=lambda: events.append("browser_closed"),
    )
    pw = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **options: browser))
    with pytest.raises(AssertionError, match="synthetic failure"):
        with isolated_browser(pw, simulated_online=online):
            raise AssertionError("synthetic failure")
    assert events == ["context_closed", "browser_closed"]
    assert "get: () => " + str(online).lower() in scripts[0]
    assert len(routes) == 1
    handler = routes[0][1]
    for url, expected in [("http://127.0.0.1:1234/draft", "local"), ("https://example.invalid/draft", "denied")]:
        seen = []
        route = SimpleNamespace(
            request=SimpleNamespace(url=url),
            continue_=lambda: seen.append("local"),
            abort=lambda reason: seen.append("denied"),
        )
        handler(route)
        assert seen == [expected]


def test_context_creation_failure_closes_browser():
    events = []

    def fail(**options):
        raise RuntimeError("context unavailable")

    browser = SimpleNamespace(new_context=fail, close=lambda: events.append("closed"))
    pw = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **options: browser))
    with pytest.raises(RuntimeError, match="context unavailable"):
        with isolated_browser(pw, simulated_online=True):
            pytest.fail("unreachable")
    assert events == ["closed"]
