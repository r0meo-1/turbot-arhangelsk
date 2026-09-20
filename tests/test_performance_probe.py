from deploy.performance_probe import PageResult, PageSpec, _within_budget


def _spec():
    return PageSpec(
        "owned_surface",
        "https://example.invalid/",
        "owned",
        max_wall_ms=8000,
        max_lcp_ms=5000,
        max_transfer_kb=1024,
    )


def test_performance_budget_accepts_owned_surface_inside_limits():
    result = PageResult(
        "owned_surface",
        "owned",
        ok=True,
        within_budget=False,
        wall_ms=4200,
        lcp_ms=3100,
        transfer_kb=420,
    )
    assert _within_budget(_spec(), result) is True


def test_performance_budget_rejects_slow_or_heavy_owned_surface():
    slow = PageResult(
        "owned_surface",
        "owned",
        ok=True,
        within_budget=False,
        wall_ms=9000,
        lcp_ms=3100,
        transfer_kb=420,
    )
    heavy = PageResult(
        "owned_surface",
        "owned",
        ok=True,
        within_budget=False,
        wall_ms=4200,
        lcp_ms=3100,
        transfer_kb=1300,
    )
    lcp = PageResult(
        "owned_surface",
        "owned",
        ok=True,
        within_budget=False,
        wall_ms=4200,
        lcp_ms=6000,
        transfer_kb=420,
    )

    assert _within_budget(_spec(), slow) is False
    assert _within_budget(_spec(), heavy) is False
    assert _within_budget(_spec(), lcp) is False


def test_performance_budget_does_not_claim_failed_probe_is_fast():
    failed = PageResult(
        "owned_surface",
        "owned",
        ok=False,
        within_budget=False,
        error="browser_probe_failed",
    )
    assert _within_budget(_spec(), failed) is False
