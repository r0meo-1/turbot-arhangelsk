"""Verify that public health belongs to the intended bundle revision."""

import argparse
import json
import re
import time
from urllib.request import Request, urlopen


def matches_revision(payload, expected):
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and payload.get("revision") in (expected, expected[:7])
    )


def verify(url, expected, attempts=12):
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise ValueError("Expected a full commit SHA")
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"Cache-Control": "no-cache"})
            with urlopen(request, timeout=15) as response:
                payload = json.load(response)
            if matches_revision(payload, expected):
                return True
        except (OSError, ValueError):
            pass
        if attempt + 1 < attempts:
            time.sleep(5)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", required=True)
    parser.add_argument("--url", default="https://bot.r0meo1.ru/health")
    args = parser.parse_args()
    if not verify(args.url, args.expected):
        raise SystemExit("Public health did not confirm the deployed revision")
    print(f"Public health confirmed revision {args.expected[:7]}")


if __name__ == "__main__":
    main()
