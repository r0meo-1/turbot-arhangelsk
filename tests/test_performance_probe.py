from deploy.performance_probe import PageResult, PageSpec, _within_budget
from deploy.performance_probe import probe_page
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("status", [404, 429, 500, 503, None])
def test_http_failure_cannot_pass_performance_budget(status):
    browser = MagicMock()
    context = browser.new_context.return_value
    page = context.new_page.return_value
    page.goto.return_value = (
        None if status is None else MagicMock(status=status, ok=False)
    )

    result = probe_page(_spec(), browser)

    assert result.ok is False
    assert result.within_budget is False
    assert result.error == "http_response_failed"
    page.evaluate.assert_not_called()
    context.close.assert_called_once()


def test_successful_http_response_collects_performance_metrics():
    browser = MagicMock()
    context = browser.new_context.return_value
    page = context.new_page.return_value
    page.goto.return_value = MagicMock(status=200, ok=True)
    page.evaluate.return_value = {
        "dom": 100, "load": 200, "fcp": 100, "lcp": 150,
        "bytes": 1024, "resources": 2,
    }

    result = probe_page(_spec(), browser)

    assert result.ok is True
    assert result.within_budget is True
    assert result.transfer_kb == 1
    context.close.assert_called_once()


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
