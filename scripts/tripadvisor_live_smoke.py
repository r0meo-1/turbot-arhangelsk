from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from hotel_recommendation.m2b_live_smoke import run_live_smoke


def main() -> int:
    parser = argparse.ArgumentParser(description="Run strict M2B Tripadvisor Terra live smoke")
    parser.add_argument("--destination", default="Phuket")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--locale", default="en-US")
    args = parser.parse_args()

    report = run_live_smoke(
        destination=args.destination,
        limit=args.limit,
        locale=args.locale,
    )
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
