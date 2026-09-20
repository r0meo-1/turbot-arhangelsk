#!/usr/bin/env python3
"""Mobile/slow-network production performance diagnostic for TurBot surfaces."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional


@dataclass(frozen=True)
class PageSpec:
    name: str
    url: str
    owner: str
    max_wall_ms: int
    max_lcp_ms: int
    max_transfer_kb: int


@dataclass
class PageResult:
    name: str
    owner: str
    ok: bool
    within_budget: bool
    wall_ms: Optional[int] = None
    dom_content_loaded_ms: Optional[int] = None
    load_ms: Optional[int] = None
    fcp_ms: Optional[int] = None
    lcp_ms: Optional[int] = None
    transfer_kb: Optional[int] = None
    resource_count: Optional[int] = None
    error: str = ""


PAGES = (
    PageSpec(
        "telegram_miniapp",
        "https://r0meo-1.github.io/turbot-arhangelsk/miniapp/",
        "owned",
        8000,
        5000,
        1024,
    ),
    PageSpec(
        "vk_miniapp",
        "https://bot.r0meo1.ru/vk/miniapp/",
        "owned",
        8000,
        5000,
        1024,
    ),
    PageSpec(
        "travel_whitelabel",
        "https://travel.r0meo1.ru/",
        "provider",
        12000,
        7000,
        2048,
    ),
)

NETWORK_PROFILE = {
    "name": "slow_4g_class",
    "latency_ms": 150,
    "download_kbps": 1600,
    "upload_kbps": 750,
}
VIEWPORT = {"width": 390, "height": 844}


def _within_budget(spec: PageSpec, result: PageResult) -> bool:
    if not result.ok:
        return False
    checks = [
        result.wall_ms is not None and result.wall_ms <= spec.max_wall_ms,
        result.transfer_kb is not None and result.transfer_kb <= spec.max_transfer_kb,
    ]
    if result.lcp_ms is not None and result.lcp_ms > 0:
        checks.append(result.lcp_ms <= spec.max_lcp_ms)
    return all(checks)


def probe_page(spec: PageSpec, browser) -> PageResult:
    context = browser.new_context(viewport=VIEWPORT)
    page = context.new_page()
    session = context.new_cdp_session(page)

    try:
        session.send("Network.enable")
        session.send("Network.setCacheDisabled", {"cacheDisabled": True})
        session.send(
            "Network.emulateNetworkConditions",
            {
                "offline": False,
                "latency": NETWORK_PROFILE["latency_ms"],
                "downloadThroughput": NETWORK_PROFILE["download_kbps"] * 1000 / 8,
                "uploadThroughput": NETWORK_PROFILE["upload_kbps"] * 1000 / 8,
                "connectionType": "cellular4g",
            },
        )
        page.add_init_script(
            """
            window.__turbotLcp = 0;
            try {
              new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                  window.__turbotLcp = Math.max(window.__turbotLcp, entry.startTime || 0);
                }
              }).observe({ type: 'largest-contentful-paint', buffered: true });
            } catch (_) {}
            """
        )

        started = time.perf_counter()
        page.goto(spec.url, wait_until="load", timeout=30_000)
        page.wait_for_timeout(750)
        wall_ms = round((time.perf_counter() - started) * 1000)

        metrics = page.evaluate(
            """
            () => {
              const nav = performance.getEntriesByType('navigation')[0];
              const resources = performance.getEntriesByType('resource');
              const paints = performance.getEntriesByType('paint');
              const fcp = paints.find((entry) => entry.name === 'first-contentful-paint');
              const bytes = [nav, ...resources].reduce((sum, entry) => {
                const value = entry.transferSize || entry.encodedBodySize || 0;
                return sum + value;
              }, 0);
              return {
                dom: nav ? nav.domContentLoadedEventEnd : 0,
                load: nav ? nav.loadEventEnd : 0,
                fcp: fcp ? fcp.startTime : 0,
                lcp: window.__turbotLcp || 0,
                bytes,
                resources: resources.length,
              };
            }
            """
        )

        result = PageResult(
            name=spec.name,
            owner=spec.owner,
            ok=True,
            within_budget=False,
            wall_ms=wall_ms,
            dom_content_loaded_ms=round(metrics["dom"]) if metrics["dom"] else None,
            load_ms=round(metrics["load"]) if metrics["load"] else None,
            fcp_ms=round(metrics["fcp"]) if metrics["fcp"] else None,
            lcp_ms=round(metrics["lcp"]) if metrics["lcp"] else None,
            transfer_kb=round(metrics["bytes"] / 1024),
            resource_count=int(metrics["resources"]),
        )
        result.within_budget = _within_budget(spec, result)
        return result
    except Exception:
        return PageResult(
            name=spec.name,
            owner=spec.owner,
            ok=False,
            within_budget=False,
            error="browser_probe_failed",
        )
    finally:
        context.close()


def run(pages: Iterable[PageSpec] = PAGES) -> dict:
    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        try:
            for spec in pages:
                results.append(probe_page(spec, browser))
        finally:
            browser.close()

    owned = [result for result in results if result.owner == "owned"]
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "profile": NETWORK_PROFILE,
        "viewport": VIEWPORT,
        "owned_ok": all(item.ok for item in owned),
        "owned_within_budget": all(item.within_budget for item in owned),
        "results": [asdict(item) for item in results],
        "budgets": {
            spec.name: {
                "max_wall_ms": spec.max_wall_ms,
                "max_lcp_ms": spec.max_lcp_ms,
                "max_transfer_kb": spec.max_transfer_kb,
                "enforcement": "diagnostic",
            }
            for spec in PAGES
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    payload = run()
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    print(rendered)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            fh.write(rendered + "\n")

    # Initial baseline is deliberately report-only. Once production evidence
    # establishes stable numbers, owned-surface budgets can become blocking.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
