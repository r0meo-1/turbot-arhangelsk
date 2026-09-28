"""Own local test-server sockets, including when a browser assertion fails."""
from contextlib import contextmanager
import threading


@contextmanager
def local_server(server):
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    started = False
    try:
        worker.start()
        started = True
        yield server
    finally:
        try:
            if started:
                server.shutdown()
                worker.join(timeout=5)
                assert not worker.is_alive(), "local test server did not stop"
        finally:
            server.server_close()
