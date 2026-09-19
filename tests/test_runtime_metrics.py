from shared.runtime_metrics import event_counter_snapshot, lead_delivery_snapshot


def test_lead_delivery_snapshot_reports_counts_and_percentiles():
    snapshot = lead_delivery_snapshot(
        [(100, 101), (100, 102), (100, 110), (100, None)],
        window_seconds=3600,
    )
    assert snapshot == {
        "window_seconds": 3600,
        "accepted": 4,
        "manager_notified": 3,
        "pending_manager_delivery": 1,
        "latency_seconds": {"p50": 2, "p95": 10, "p99": 10},
    }


def test_lead_delivery_snapshot_does_not_echo_timestamp_values():
    marker = 79161234567
    snapshot = lead_delivery_snapshot([(marker, marker + 3)], window_seconds=86400)
    assert str(marker) not in repr(snapshot)
    assert snapshot["latency_seconds"]["p50"] == 3


def test_event_counter_snapshot_contains_only_grouped_dimensions():
    snapshot = event_counter_snapshot(
        [("tourvisor", "success", 4), ("tourvisor", "timeout", 1)],
        window_seconds=3600,
    )
    assert snapshot == {
        "window_seconds": 3600,
        "subjects": {"tourvisor": {"success": 4, "timeout": 1}},
    }
