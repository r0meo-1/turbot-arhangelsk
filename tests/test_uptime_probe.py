import json
from datetime import datetime, timedelta, timezone

from deploy.uptime_probe import (
    EndpointSpec,
    TLSSpec,
    probe_endpoint,
    probe_tls_certificate,
    run,
)


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self, _limit=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def opener_for(body: bytes, status: int = 200):
    def _open(_request, timeout=0):
        assert timeout > 0
        return FakeResponse(body, status)
    return _open


def test_json_health_probe_requires_ok_status_and_revision():
    spec = EndpointSpec(
        "telegram_health",
        "https://example.invalid/health",
        "json_status",
        "ok",
    )
    good = json.dumps({"status": "ok", "revision": "abc123"}).encode()
    result = probe_endpoint(
        spec,
        attempts=1,
        delay=0,
        timeout=1,
        opener=opener_for(good),
    )
    assert result.ok is True
    assert result.status == 200


def test_probe_retries_and_reports_only_coarse_error():
    spec = EndpointSpec(
        "vk_miniapp",
        "https://example.invalid/vk/miniapp/",
        "contains",
        "trip-form",
    )
    sleeps = []
    result = probe_endpoint(
        spec,
        attempts=3,
        delay=2,
        timeout=1,
        opener=opener_for(b"<html>not-ready</html>"),
        sleeper=lambda seconds: sleeps.append(seconds),
    )
    assert result.ok is False
    assert result.attempts == 3
    assert result.error == "expected_marker_missing"
    assert sleeps == [2, 2]
    assert "not-ready" not in repr(result)


def test_run_summary_never_echoes_response_body():
    secret_marker = "Private Person +79990001122 token=super-secret"
    endpoints = (
        EndpointSpec("landing", "https://example.invalid/", "nonempty"),
    )
    payload = run(
        endpoints,
        tls_hosts=(),
        attempts=1,
        delay=0,
        timeout=1,
        opener=opener_for(secret_marker.encode()),
    )
    assert payload["ok"] is True
    assert secret_marker not in json.dumps(payload, ensure_ascii=False)


def test_vk_health_probe_rejects_wrong_platform():
    spec = EndpointSpec(
        "vk_health",
        "https://example.invalid/vk/health",
        "json_status",
        "ok",
    )
    body = json.dumps({"status": "ok", "platform": "telegram"}).encode()
    result = probe_endpoint(
        spec,
        attempts=1,
        delay=0,
        timeout=1,
        opener=opener_for(body),
    )
    assert result.ok is False
    assert result.error == "unexpected_platform"



def test_tls_probe_reports_safe_expiry_metadata():
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    expires = now + timedelta(days=45)
    certificate = {
        "notAfter": expires.strftime("%b %d %H:%M:%S %Y GMT"),
        "subject": ((("commonName", "private-certificate-label"),),),
    }
    spec = TLSSpec("bot_tls", "bot.example.invalid", min_days_remaining=21)

    result = probe_tls_certificate(
        spec,
        attempts=1,
        delay=0,
        timeout=1,
        cert_loader=lambda _spec, timeout: certificate,
        now=now,
    )

    assert result.ok is True
    assert result.days_remaining == 45.0
    assert result.expires_at.startswith("2030-02-15T")
    assert "private-certificate-label" not in repr(result)


def test_tls_probe_fails_before_certificate_expiry_window():
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    expires = now + timedelta(days=10)
    certificate = {
        "notAfter": expires.strftime("%b %d %H:%M:%S %Y GMT"),
    }
    spec = TLSSpec("site_tls", "example.invalid", min_days_remaining=21)

    result = probe_tls_certificate(
        spec,
        attempts=2,
        delay=0,
        timeout=1,
        cert_loader=lambda _spec, timeout: certificate,
        sleeper=lambda _seconds: None,
        now=now,
    )

    assert result.ok is False
    assert result.attempts == 2
    assert result.error == "certificate_expiring"
    assert result.days_remaining is None


def test_run_includes_tls_summary_without_certificate_body():
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    expires = now + timedelta(days=30)
    secret_marker = "CN=Private Internal Label"
    certificate = {
        "notAfter": expires.strftime("%b %d %H:%M:%S %Y GMT"),
        "subject": ((("commonName", secret_marker),),),
    }

    payload = run(
        endpoints=(),
        tls_hosts=(TLSSpec("travel_tls", "travel.example.invalid"),),
        attempts=1,
        delay=0,
        timeout=1,
        cert_loader=lambda _spec, timeout: certificate,
        now=now,
    )

    assert payload["ok"] is True
    assert payload["tls_results"][0]["days_remaining"] == 30.0
    assert secret_marker not in json.dumps(payload, ensure_ascii=False)
