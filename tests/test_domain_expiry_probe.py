from datetime import datetime, timedelta, timezone

from deploy.domain_expiry_probe import run


def _record(paid_till: datetime, extra: str = "") -> str:
    return (
        "domain: R0MEO1.RU\n"
        f"paid-till: {paid_till.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"{extra}\n"
        "source: TCI\n"
    )


def test_domain_expiry_probe_reports_safe_healthy_metadata():
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    paid_till = now + timedelta(days=90)
    secret = "org: Private Holder Name"

    payload = run(
        "r0meo1.ru",
        warning_days=45,
        timeout=1,
        loader=lambda _domain, timeout: _record(paid_till, secret),
        now=now,
    )

    assert payload["ok"] is True
    assert payload["days_remaining"] == 90.0
    assert payload["paid_till"].startswith("2030-04-01T")
    assert secret not in repr(payload)


def test_domain_expiry_probe_warns_inside_threshold():
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    paid_till = now + timedelta(days=10)

    payload = run(
        "r0meo1.ru",
        warning_days=45,
        timeout=1,
        loader=lambda _domain, timeout: _record(paid_till),
        now=now,
    )

    assert payload["ok"] is False
    assert payload["error"] == "domain_expiring"
    assert payload["days_remaining"] == 10.0


def test_domain_expiry_probe_rejects_missing_paid_till():
    payload = run(
        "r0meo1.ru",
        timeout=1,
        loader=lambda _domain, timeout: "domain: R0MEO1.RU\nsource: TCI\n",
    )

    assert payload["ok"] is False
    assert payload["error"] == "paid_till_missing"


def test_domain_expiry_probe_rejects_query_injection_before_network():
    called = False

    def loader(_domain, timeout):
        nonlocal called
        called = True
        return ""

    payload = run("r0meo1.ru\r\nexample.ru", timeout=1, loader=loader)

    assert payload["ok"] is False
    assert payload["error"] == "invalid_domain"
    assert called is False
