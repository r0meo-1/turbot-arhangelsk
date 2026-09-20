#!/usr/bin/env python3
"""External production uptime probe for TurBot."""

from __future__ import annotations

import argparse
import json
import re
import socket
import ssl
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class EndpointSpec:
    name: str
    url: str
    kind: str
    expected: str = ""


@dataclass(frozen=True)
class TLSSpec:
    name: str
    host: str
    port: int = 443
    min_days_remaining: int = 21


@dataclass
class TLSProbeResult:
    name: str
    host: str
    ok: bool
    attempts: int
    days_remaining: Optional[float] = None
    expires_at: str = ""
    error: str = ""


@dataclass
class ProbeResult:
    name: str
    url: str
    ok: bool
    attempts: int
    status: Optional[int] = None
    error: str = ""


ENDPOINTS = (
    EndpointSpec("telegram_health", "https://bot.r0meo1.ru/health", "json_status", "ok"),
    EndpointSpec("vk_health", "https://bot.r0meo1.ru/vk/health", "json_status", "ok"),
    EndpointSpec("landing", "https://r0meo1.ru/apreltour/", "nonempty"),
    EndpointSpec("vk_miniapp", "https://bot.r0meo1.ru/vk/miniapp/", "contains", "trip-form"),
    EndpointSpec("travel_whitelabel_https", "https://travel.r0meo1.ru/", "nonempty"),
)

DIAGNOSTIC_ENDPOINTS = (
    EndpointSpec(
        "travel_whitelabel_http_redirect",
        "http://travel.r0meo1.ru/",
        "https_redirect",
        "travel.r0meo1.ru",
    ),
    EndpointSpec(
        "travel_whitelabel_hygiene",
        "https://travel.r0meo1.ru/",
        "whitelabel_hygiene",
        "travel.r0meo1.ru",
    ),
)

TLS_HOSTS = (
    TLSSpec("bot_tls", "bot.r0meo1.ru"),
    TLSSpec("site_tls", "r0meo1.ru"),
    TLSSpec("travel_tls", "travel.r0meo1.ru"),
)


def _validate_body(spec: EndpointSpec, body: bytes, *, final_url: str = "") -> None:
    if spec.kind == "json_status":
        data = json.loads(body.decode("utf-8"))
        if str(data.get("status") or "").lower() != spec.expected:
            raise ValueError("unexpected_status")
        if spec.name == "telegram_health" and not str(data.get("revision") or "").strip():
            raise ValueError("missing_revision")
        if spec.name == "vk_health" and str(data.get("platform") or "") != "vk":
            raise ValueError("unexpected_platform")
        return
    if spec.kind == "contains":
        if spec.expected.encode("utf-8") not in body:
            raise ValueError("expected_marker_missing")
        return
    if spec.kind == "nonempty":
        if not body.strip():
            raise ValueError("empty_body")
        return
    if spec.kind == "https_redirect":
        parsed = urlsplit(final_url)
        if parsed.scheme.lower() != "https":
            raise ValueError("redirect_not_https")
        if spec.expected and (parsed.hostname or "").lower() != spec.expected.lower():
            raise ValueError("redirect_host_mismatch")
        if not body.strip():
            raise ValueError("empty_body")
        return
    if spec.kind == "whitelabel_hygiene":
        parsed = urlsplit(final_url)
        if parsed.scheme.lower() != "https":
            raise ValueError("final_not_https")
        if spec.expected and (parsed.hostname or "").lower() != spec.expected.lower():
            raise ValueError("final_host_mismatch")
        if not body.strip():
            raise ValueError("empty_body")
        html = body.decode("utf-8", errors="ignore")
        mixed_patterns = (
            r'''(?:src|action|poster)\s*=\s*["']\s*http://''',
            r'''srcset\s*=\s*["'][^"']*\bhttp://''',
            r'''url\(\s*["']?http://''',
        )
        if any(re.search(pattern, html, flags=re.IGNORECASE) for pattern in mixed_patterns):
            raise ValueError("mixed_content")
        return
    raise ValueError("unsupported_probe_kind")


def _probe_once(spec: EndpointSpec, *, timeout: float, opener: Callable = urlopen) -> tuple[int, bytes]:
    request = Request(
        spec.url,
        headers={
            "User-Agent": "TurBot-Uptime-Monitor/1.0",
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        },
    )
    with opener(request, timeout=timeout) as response:
        status = int(getattr(response, "status", 200))
        geturl = getattr(response, "geturl", None)
        final_url = str(geturl() if callable(geturl) else spec.url)
        body = response.read(1024 * 1024 + 1)
    if not 200 <= status < 300:
        raise HTTPError(spec.url, status, "non_2xx", {}, None)
    if len(body) > 1024 * 1024:
        raise ValueError("response_too_large")
    _validate_body(spec, body, final_url=final_url)
    return status, body


def probe_endpoint(
    spec: EndpointSpec,
    *,
    attempts: int,
    delay: float,
    timeout: float,
    opener: Callable = urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> ProbeResult:
    last_error = ""
    last_status: Optional[int] = None
    used = 0
    total_attempts = max(1, attempts)
    for attempt in range(1, total_attempts + 1):
        used = attempt
        try:
            status, _ = _probe_once(spec, timeout=timeout, opener=opener)
            return ProbeResult(spec.name, spec.url, True, attempt, status=status)
        except HTTPError as exc:
            last_status = int(exc.code)
            last_error = f"http_{exc.code}"
        except (URLError, TimeoutError):
            last_error = "network_error"
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)[:80] or "invalid_response"
        except Exception:
            last_error = "probe_error"

        if attempt < total_attempts:
            sleeper(max(0.0, delay))

    return ProbeResult(
        spec.name,
        spec.url,
        False,
        used,
        status=last_status,
        error=last_error or "unknown_error",
    )


def _load_peer_certificate(spec: TLSSpec, *, timeout: float) -> dict:
    context = ssl.create_default_context()
    with socket.create_connection((spec.host, spec.port), timeout=timeout) as raw_socket:
        with context.wrap_socket(raw_socket, server_hostname=spec.host) as tls_socket:
            return dict(tls_socket.getpeercert() or {})


def _certificate_expiry(
    spec: TLSSpec,
    certificate: dict,
    *,
    now: Optional[datetime] = None,
) -> tuple[datetime, float]:
    not_after = str(certificate.get("notAfter") or "").strip()
    if not not_after:
        raise ValueError("missing_not_after")
    expires_at = datetime.fromtimestamp(
        ssl.cert_time_to_seconds(not_after),
        tz=timezone.utc,
    )
    current = now or datetime.now(timezone.utc)
    days_remaining = (expires_at - current).total_seconds() / 86400
    if days_remaining < spec.min_days_remaining:
        raise ValueError("certificate_expiring")
    return expires_at, days_remaining


def probe_tls_certificate(
    spec: TLSSpec,
    *,
    attempts: int,
    delay: float,
    timeout: float,
    cert_loader: Callable = _load_peer_certificate,
    sleeper: Callable[[float], None] = time.sleep,
    now: Optional[datetime] = None,
) -> TLSProbeResult:
    last_error = ""
    used = 0
    total_attempts = max(1, attempts)
    for attempt in range(1, total_attempts + 1):
        used = attempt
        try:
            certificate = cert_loader(spec, timeout=timeout)
            expires_at, days_remaining = _certificate_expiry(
                spec,
                certificate,
                now=now,
            )
            return TLSProbeResult(
                spec.name,
                spec.host,
                True,
                attempt,
                days_remaining=round(days_remaining, 1),
                expires_at=expires_at.isoformat(),
            )
        except (OSError, ssl.SSLError, TimeoutError):
            last_error = "tls_network_error"
        except ValueError as exc:
            last_error = str(exc)[:80] or "invalid_certificate"
        except Exception:
            last_error = "tls_probe_error"

        if attempt < total_attempts:
            sleeper(max(0.0, delay))

    return TLSProbeResult(
        spec.name,
        spec.host,
        False,
        used,
        error=last_error or "unknown_error",
    )


def run(
    endpoints: Iterable[EndpointSpec] = ENDPOINTS,
    *,
    diagnostic_endpoints: Iterable[EndpointSpec] = (),
    tls_hosts: Iterable[TLSSpec] = TLS_HOSTS,
    attempts: int = 3,
    delay: float = 5.0,
    timeout: float = 15.0,
    opener: Callable = urlopen,
    cert_loader: Callable = _load_peer_certificate,
    sleeper: Callable[[float], None] = time.sleep,
    now: Optional[datetime] = None,
) -> dict:
    results = [
        probe_endpoint(
            spec,
            attempts=attempts,
            delay=delay,
            timeout=timeout,
            opener=opener,
            sleeper=sleeper,
        )
        for spec in endpoints
    ]
    diagnostic_results = [
        probe_endpoint(
            spec,
            attempts=attempts,
            delay=delay,
            timeout=timeout,
            opener=opener,
            sleeper=sleeper,
        )
        for spec in diagnostic_endpoints
    ]
    tls_results = [
        probe_tls_certificate(
            spec,
            attempts=min(max(1, attempts), 2),
            delay=min(max(0.0, delay), 2.0),
            timeout=min(max(1.0, timeout), 5.0),
            cert_loader=cert_loader,
            sleeper=sleeper,
            now=now,
        )
        for spec in tls_hosts
    ]
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "ok": all(item.ok for item in results) and all(item.ok for item in tls_results),
        "results": [asdict(item) for item in results],
        "diagnostic_results": [asdict(item) for item in diagnostic_results],
        "tls_results": [asdict(item) for item in tls_results],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    payload = run(
        diagnostic_endpoints=DIAGNOSTIC_ENDPOINTS,
        attempts=max(1, min(args.attempts, 5)),
        delay=max(0.0, min(args.delay, 60.0)),
        timeout=max(1.0, min(args.timeout, 60.0)),
    )
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    print(rendered)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(rendered + "\n")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
