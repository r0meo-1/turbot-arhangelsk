#!/usr/bin/env python3
"""Fail CI when tracked repository text contains high-confidence credential formats.

The scanner intentionally reports only the file, line and credential class. It
never prints the matched value, so a CI finding does not amplify a leaked secret.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("telegram_bot_token", re.compile(r"(?<!\d)\d{7,12}:[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_-])")),
    ("github_classic_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("github_fine_grained_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("openai_style_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    (
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
)


def _tracked_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return [
        Path(item.decode("utf-8", errors="surrogateescape"))
        for item in proc.stdout.split(b"\0")
        if item
    ]


def scan_text(text: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for name, pattern in PATTERNS:
            if pattern.search(line):
                findings.append((line_number, name))
    return findings


def main() -> int:
    findings: list[tuple[str, int, str]] = []
    for path in _tracked_files():
        try:
            raw = path.read_bytes()
        except (OSError, PermissionError):
            continue
        if len(raw) > MAX_TEXT_FILE_BYTES or b"\0" in raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        for line_number, name in scan_text(text):
            findings.append((path.as_posix(), line_number, name))

    if findings:
        print("High-confidence credential pattern(s) detected:")
        for path, line_number, name in findings:
            print(f"- {path}:{line_number}: {name}")
        print("Matched credential values are intentionally not printed.")
        return 1

    print(f"Secret scan: ok ({len(_tracked_files())} tracked files checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
