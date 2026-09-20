#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Verify redacted campaign-attribution evidence without printing customer data.

Input files are plain-text copies of:
1) the manager notification for a completed lead;
2) the admin /export output containing the same completed lead.

The command prints only PASS/FAIL metadata and SHA-256 fingerprints of the
input files. It never echoes their contents.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

TAG_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
TOKEN_CHARS = r"A-Za-z0-9_-"


class EvidenceError(RuntimeError):
    pass


def canonical_tag(raw: str) -> str:
    tag = (raw or "").strip().lower()
    if not TAG_RE.fullmatch(tag):
        raise EvidenceError(
            "source tag must be 1-64 chars using lowercase letters, digits, _ or -"
        )
    return tag


def read_text(path: Path) -> tuple[str, str]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise EvidenceError(f"cannot read {path}: {exc}") from exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError(f"{path} is not valid UTF-8 text") from exc
    return text, hashlib.sha256(data).hexdigest()


def manager_has_tag(text: str, tag: str) -> bool:
    # Handles Telegram HTML (<code>tag</code>) and plain VK manager cards.
    pattern = re.compile(
        rf"Источник:\\s*(?:<code>\\s*)?"
        rf"{re.escape(tag)}"
        rf"(?:\\s*</code>)?(?![{TOKEN_CHARS}])",
        re.IGNORECASE,
    )
    return bool(pattern.search(text))


def export_has_tag(text: str, tag: str) -> bool:
    pattern = re.compile(
        rf"src={re.escape(tag)}(?![{TOKEN_CHARS}])",
        re.IGNORECASE,
    )
    return bool(pattern.search(text))


def verify(tag: str, manager_text: str, export_text: str) -> tuple[bool, bool]:
    return manager_has_tag(manager_text, tag), export_has_tag(export_text, tag)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify TurBot campaign attribution evidence without exposing PII"
    )
    parser.add_argument("--source-tag", required=True)
    parser.add_argument("--manager-file", required=True, type=Path)
    parser.add_argument("--export-file", required=True, type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        tag = canonical_tag(args.source_tag)
        manager_text, manager_sha = read_text(args.manager_file)
        export_text, export_sha = read_text(args.export_file)
    except EvidenceError as exc:
        print(f"campaign_evidence_status=FAIL")
        print(f"error={exc}", file=sys.stderr)
        return 2

    manager_ok, export_ok = verify(tag, manager_text, export_text)
    passed = manager_ok and export_ok

    print(f"campaign_evidence_status={'PASS' if passed else 'FAIL'}")
    print(f"source_tag={tag}")
    print(f"manager_marker={'present' if manager_ok else 'missing'}")
    print(f"export_marker={'present' if export_ok else 'missing'}")
    print(f"manager_sha256={manager_sha}")
    print(f"export_sha256={export_sha}")

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
