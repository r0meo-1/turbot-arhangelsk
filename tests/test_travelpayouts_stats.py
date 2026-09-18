import pytest

from shared import travelpayouts_stats as stats


def _response(payload, status_code=200):
    class Response:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            return payload

    return Response()


@pytest.fixture(autouse=True)
def clear_stats_cache(monkeypatch):
    stats.clear_cache()
    monkeypatch.setattr(stats, "_last_success_at", 0.0)
    monkeypatch.setattr(stats, "_last_error_at", 0.0)
    monkeypatch.setattr(stats, "_last_error_code", "")
    yield
    stats.clear_cache()


def test_fetch_partner_performance_summarizes_tracked_subids(monkeypatch):
    captured = {}

    rows = [
        {
            "action_id": "hotel-1",
            "sub_id": "tg_hotels",
            "price_eur": "500.00",
            "paid_profit_eur": "25.50",
            "state": "paid",
            "updated_at": "2026-09-18 10:00:00",
        },
        {
            # Older update for the same action must not double-count.
            "action_id": "hotel-1",
            "sub_id": "tg_hotels",
            "price_eur": "500.00",
            "paid_profit_eur": "0",
            "state": "processing",
            "updated_at": "2026-09-17 10:00:00",
        },
        {
            "action_id": "esim-1",
            "sub_id": ".tg_esim",
            "price_eur": "20",
            "paid_profit_eur": "0",
            "state": "processing",
            "updated_at": "2026-09-18 11:00:00",
        },
        {
            "action_id": "esim-old",
            "sub_id": "turbot_esim_tg",
            "price_eur": "10",
            "paid_profit_eur": "0",
            "state": "canceled",
            "updated_at": "2026-09-18 11:00:00",
        },
        {
            "action_id": "transfer-1",
            "sub_id": "tg_kiwitaxi_transfer",
            "price_eur": "60",
            "paid_profit_eur": "8",
            "state": "paid",
            "updated_at": "2026-09-18 12:00:00",
        },
        {
            "action_id": "other-1",
            "sub_id": "someone_else",
            "price_eur": "999",
            "paid_profit_eur": "999",
            "state": "paid",
            "updated_at": "2026-09-18 12:00:00",
        },
    ]

    def fake_post(url, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return _response({"results": rows, "total_rows": len(rows)})

    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "stats-token")
    monkeypatch.setenv("TRAVELPAYOUTS_STATS_CACHE_TTL", "0")
    monkeypatch.setattr(stats.requests, "post", fake_post)

    result = stats.fetch_partner_performance(30, force=True)

    assert captured["url"] == "https://api.travelpayouts.com/statistics/v1/execute_query"
    assert captured["headers"]["X-Access-Token"] == "stats-token"
    assert {"field": "type", "op": "eq", "value": "action"} in captured["json"]["filters"]
    assert "sub_id" in captured["json"]["fields"]

    totals = result["totals"]
    assert totals == {
        "bookings": 4,
        "paid": 2,
        "processing": 1,
        "canceled": 1,
        "other": 0,
        "booking_value_eur": 580.0,
        "paid_profit_eur": 33.5,
    }
    assert result["by_service"]["hotel"]["paid"] == 1
    assert result["by_service"]["esim"]["bookings"] == 2
    assert result["by_service"]["transfer"]["paid_profit_eur"] == 8.0


def test_fetch_partner_performance_paginates(monkeypatch):
    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append(json["offset"])
        if json["offset"] == 0:
            return _response({
                "results": [
                    {"action_id": "a", "sub_id": "tg_hotels", "state": "paid"},
                    {"action_id": "b", "sub_id": "tg_esim", "state": "processing"},
                ],
                "total_rows": 3,
            })
        return _response({
            "results": [
                {"action_id": "c", "sub_id": "tg_transfer", "state": "paid"},
            ],
            "total_rows": 3,
        })

    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "stats-token")
    monkeypatch.setenv("TRAVELPAYOUTS_STATS_CACHE_TTL", "0")
    monkeypatch.setattr(stats, "PAGE_LIMIT", 2)
    monkeypatch.setattr(stats.requests, "post", fake_post)

    result = stats.fetch_partner_performance(7, force=True)

    assert calls == [0, 2]
    assert result["totals"]["bookings"] == 3
    assert result["by_service"]["transfer"]["paid"] == 1


def test_fetch_partner_performance_uses_cache(monkeypatch):
    calls = 0

    def fake_post(url, json, headers, timeout):
        nonlocal calls
        calls += 1
        return _response({"results": [], "total_rows": 0})

    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "stats-token")
    monkeypatch.setenv("TRAVELPAYOUTS_STATS_CACHE_TTL", "300")
    monkeypatch.setattr(stats.requests, "post", fake_post)

    first = stats.fetch_partner_performance(30)
    second = stats.fetch_partner_performance(30)

    assert calls == 1
    assert first == second
    assert first is not second


def test_fetch_partner_performance_requires_token(monkeypatch):
    monkeypatch.delenv("TRAVELPAYOUTS_API_TOKEN", raising=False)

    with pytest.raises(stats.TravelpayoutsStatsNotConfigured):
        stats.fetch_partner_performance(30, force=True)


def test_fetch_partner_performance_rejects_bad_token(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "bad")
    monkeypatch.setattr(
        stats.requests,
        "post",
        lambda *args, **kwargs: _response({}, status_code=401),
    )

    with pytest.raises(stats.TravelpayoutsStatsNotConfigured):
        stats.fetch_partner_performance(30, force=True)


def test_fetch_partner_performance_rejects_invalid_payload(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "stats-token")
    monkeypatch.setattr(
        stats.requests,
        "post",
        lambda *args, **kwargs: _response({"no_results": []}),
    )

    with pytest.raises(stats.TravelpayoutsStatsError):
        stats.fetch_partner_performance(30, force=True)


def test_force_refresh_bypasses_cache(monkeypatch):
    calls = 0

    def fake_post(url, json, headers, timeout):
        nonlocal calls
        calls += 1
        return _response({"results": [], "total_rows": 0})

    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "stats-token")
    monkeypatch.setenv("TRAVELPAYOUTS_STATS_CACHE_TTL", "300")
    monkeypatch.setattr(stats.requests, "post", fake_post)

    stats.fetch_partner_performance(30)
    stats.fetch_partner_performance(30, force=True)

    assert calls == 2


def test_health_snapshot_is_secret_free_and_tracks_success(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "super-secret-token")
    monkeypatch.setenv("TRAVELPAYOUTS_STATS_CACHE_TTL", "300")
    monkeypatch.setattr(
        stats.requests,
        "post",
        lambda *args, **kwargs: _response({"results": [], "total_rows": 0}),
    )
    monkeypatch.setattr(stats.time, "time", lambda: 1000.0)

    stats.fetch_partner_performance(30, force=True)
    snapshot = stats.health_snapshot(now=1012.5)

    assert snapshot["configured"] is True
    assert snapshot["status"] == "ok"
    assert snapshot["last_success_age_seconds"] == 12.5
    assert snapshot["last_error_code"] is None
    assert snapshot["cached_windows"] == [30]
    assert "super-secret-token" not in repr(snapshot)


def test_health_snapshot_tracks_sanitized_error(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_API_TOKEN", "bad-secret")
    monkeypatch.setattr(
        stats.requests,
        "post",
        lambda *args, **kwargs: _response({}, status_code=401),
    )
    monkeypatch.setattr(stats.time, "time", lambda: 2000.0)

    with pytest.raises(stats.TravelpayoutsStatsNotConfigured):
        stats.fetch_partner_performance(7, force=True)

    snapshot = stats.health_snapshot(now=2003.0)
    assert snapshot["configured"] is True
    assert snapshot["status"] == "error"
    assert snapshot["last_error_age_seconds"] == 3.0
    assert snapshot["last_error_code"] == "not_configured"
    assert "bad-secret" not in repr(snapshot)
