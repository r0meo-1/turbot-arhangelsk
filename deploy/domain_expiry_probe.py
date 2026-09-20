#!/usr/bin/env python3
"""Monitor .RU domain registration expiry using the official registry WHOIS."""

from __future__ import annotations

import argparse
import json
import re
import socket
from datetime import datetime, timezone
from typing import Callable, Optional

WHOIS_SERVER = "whois.tcinet.ru"
WHOIS_PORT = 43
MAX_WHOIS_BYTES = 128 * 1024
DOMAIN_RE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$")


def _normalize_domain(domain: str) -> str:
    value = str(domain or "").strip().lower()
    if (
        not value
        or len(value) > 253
        or not value.endswith(".ru")
        or not DOMAIN_RE.fullmatch(value)
    ):
        raise ValueError("invalid_domain")
    return value


def _load_whois_record(
    domain: str,
    *,
    timeout: float,
    server: str = WHOIS_SERVER,
    port: int = WHOIS_PORT,
) -> str:
    safe_domain = _normalize_domain(domain)
    chunks: list[bytes] = []
    total = 0
    with socket.create_connection((server, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall((safe_domain + "\r\n").encode("ascii"))
        while True:
            data = sock.recv(4096)
            if not data:
                break
            total += len(data)
            if total > MAX_WHOIS_BYTES:
                raise ValueError("whois_response_too_large")
            chunks.append(data)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _parse_paid_till(record: str) -> datetime:
    for raw_line in record.splitlines():
        key, sep, value = raw_line.partition(":")
        if sep and key.strip().lower() == "paid-till":
            text = value.strip()
            if not text:
                raise ValueError("invalid_paid_till")
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("invalid_paid_till") from exc
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
    raise ValueError("paid_till_missing")


def run(
    domain: str = "r0meo1.ru",
    *,
    warning_days: int = 45,
    timeout: float = 10.0,
    loader: Callable[..., str] = _load_whois_record,
    now: Optional[datetime] = None,
) -> dict:
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    payload = {
        "checked_at": checked_at.isoformat(),
        "ok": False,
        "domain": "",
        "source": WHOIS_SERVER,
        "paid_till": "",
        "days_remaining": None,
        "warning_days": max(1, int(warning_days)),
        "error": "",
    }

    try:
        safe_domain = _normalize_domain(domain)
        payload["domain"] = safe_domain
        record = loader(safe_domain, timeout=timeout)
        paid_till = _parse_paid_till(record)
        days_remaining = (paid_till - checked_at).total_seconds() / 86400
        payload["paid_till"] = paid_till.isoformat()
        payload["days_remaining"] = round(days_remaining, 1)
        if days_remaining < payload["warning_days"]:
            payload["error"] = "domain_expiring"
        else:
            payload["ok"] = True
    except (OSError, socket.timeout):
        payload["error"] = "whois_network_error"
    except ValueError as exc:
        payload["error"] = str(exc)[:80] or "invalid_whois_response"
    except Exception:
        payload["error"] = "domain_probe_error"

    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", default="r0meo1.ru")
    parser.add_argument("--warn-days", type=int, default=45)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    payload = run(
        args.domain,
        warning_days=max(1, min(args.warn_days, 365)),
        timeout=max(1.0, min(args.timeout, 30.0)),
    )
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    print(rendered)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(rendered + "\n")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
