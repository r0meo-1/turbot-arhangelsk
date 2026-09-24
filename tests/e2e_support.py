"""Explicit browser fixtures and resource ownership for loopback E2E tests.

A simulated navigator.onLine value is a test input, not evidence of external
connectivity. Application code and endpoint responses are not patched here.
"""
from contextlib import closing, contextmanager
import threading
from urllib.parse import urlsplit

from werkzeug.serving import WSGIRequestHandler


class QuietRequestHandler(WSGIRequestHandler):
    """Do not persist signed launch queries in test-server access logs."""

    def log_request(self, code="-", size="-"):
        pass


@contextmanager
def running_server(server):
    """Stop the serving thread and close its socket even when assertions fail."""
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        name="turbot-e2e-server",
        daemon=True,
    )
    try:
        thread.start()
    except BaseException:
        server.server_close()
        raise
    try:
        yield server
    finally:
        try:
            server.shutdown()
        finally:
            server.server_close()
            thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("E2E server thread did not terminate")
        if server.socket.fileno() != -1:
            raise RuntimeError("E2E server socket did not close")


@contextmanager
def isolated_browser(pw, *, simulated_online: bool, **options):
    """Own browser/context lifetime and declare the connectivity test input.

    Unknown external requests are blocked independently. Page-level routes may
    fulfill synthetic API responses, but must never forward to production.
    CI additionally enforces a loopback-only OS network namespace.
    """
    if type(simulated_online) is not bool:
        raise TypeError("simulated_online must be explicitly True or False")
    with closing(pw.chromium.launch(channel="msedge", headless=True)) as browser:
        with closing(browser.new_context(service_workers="block", **options)) as context:
            flag = "true" if simulated_online else "false"
            context.add_init_script(
                "Object.defineProperty(navigator, 'onLine', "
                "{configurable: true, get: () => " + flag + "});"
            )

            def local_only(route):
                target = urlsplit(route.request.url)
                if target.scheme in ("http", "https") and target.hostname in {
                    "127.0.0.1", "localhost", "::1"
                }:
                    route.continue_()
                else:
                    route.abort("blockedbyclient")

            context.route("**/*", local_only)
            yield context
