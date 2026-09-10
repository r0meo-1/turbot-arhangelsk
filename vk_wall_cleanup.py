#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Safe VK wall scanner/backup helper.

This repository version intentionally supports only read-only scan and local JSON backup.
It does not implement apply, wall.delete, photos.delete, or any destructive VK action.

Required environment variables:
  VK_TOKEN
  VK_OWNER_ID
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

API_VERSION = "5.199"
API_BASE = "https://api.vk.com/method/"
DEFAULT_KEYWORDS = ["Апрель Тур", "Апрель тур", "Aprel Tour"]


class VKError(RuntimeError):
    pass


def vk_call(method: str, token: str, **params):
    payload = {**params, "access_token": token, "v": API_VERSION}
    req = Request(API_BASE + method, data=urlencode(payload).encode("utf-8"), method="POST")
    try:
        with urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raise VKError(f"HTTP error {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise VKError(f"Network error: {exc.reason}") from exc
    if "error" in body:
        err = body["error"]
        raise VKError(f"VK API error {err.get('error_code')}: {err.get('error_msg')}")
    return body["response"]


def require_env():
    token = os.getenv("VK_TOKEN", "").strip()
    owner_raw = os.getenv("VK_OWNER_ID", "").strip()
    if not token:
        raise SystemExit("VK_TOKEN is not set")
    if not owner_raw:
        raise SystemExit("VK_OWNER_ID is not set")
    try:
        owner_id = int(owner_raw)
    except ValueError:
        raise SystemExit("VK_OWNER_ID must be an integer")
    return token, owner_id


def get_all_posts(token: str, owner_id: int):
    posts = []
    offset = 0
    while True:
        response = vk_call("wall.get", token, owner_id=owner_id, count=100, offset=offset, filter="owner")
        batch = response.get("items", [])
        posts.extend(batch)
        if not batch or len(posts) >= response.get("count", 0):
            break
        offset += len(batch)
        time.sleep(0.35)
    return posts


def post_haystack(post: dict) -> str:
    parts = [post.get("text", "") or ""]
    for att in post.get("attachments", []) or []:
        if att.get("type") == "photo":
            photo = att.get("photo", {}) or {}
            parts.append(photo.get("text", "") or "")
    return "\n".join(parts).casefold()


def matches(post: dict, keywords: list[str]) -> bool:
    haystack = post_haystack(post)
    return any(k.casefold() in haystack for k in keywords)


def attached_owned_photos(post: dict, owner_id: int):
    seen = set()
    result = []
    for att in post.get("attachments", []) or []:
        if att.get("type") != "photo":
            continue
        photo = att.get("photo", {}) or {}
        p_owner = photo.get("owner_id")
        p_id = photo.get("id")
        if p_owner == owner_id and isinstance(p_id, int):
            key = (p_owner, p_id)
            if key not in seen:
                seen.add(key)
                result.append({"owner_id": p_owner, "id": p_id})
    return result


def summarize(items: list[dict], owner_id: int):
    photos = sum(len(attached_owned_photos(p, owner_id)) for p in items)
    print(f"Matched posts: {len(items)}")
    print(f"Attached photos owned by this wall: {photos}")
    for post in items:
        text = (post.get("text") or "").replace("\n", " ").strip()
        if len(text) > 100:
            text = text[:97] + "..."
        print(f"post_id={post.get('id')} {text}")


def cmd_scan(args):
    token, owner_id = require_env()
    posts = get_all_posts(token, owner_id)
    found = [p for p in posts if matches(p, args.keyword)]
    print(f"Wall posts fetched: {len(posts)}")
    print("Keywords:", ", ".join(args.keyword))
    summarize(found, owner_id)


def cmd_backup(args):
    token, owner_id = require_env()
    posts = get_all_posts(token, owner_id)
    found = [p for p in posts if matches(p, args.keyword)]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(args.output or Path("backups") / stamp)
    backup_dir.mkdir(parents=True, exist_ok=False)
    payload = {
        "created_at": datetime.now().astimezone().isoformat(),
        "api_version": API_VERSION,
        "owner_id": owner_id,
        "keywords": args.keyword,
        "matched_count": len(found),
        "attached_photo_count": sum(len(attached_owned_photos(p, owner_id)) for p in found),
        "post_ids": [p.get("id") for p in found],
        "posts": found,
    }
    backup_file = backup_dir / "backup.json"
    backup_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Backup created: {backup_file.resolve()}")
    summarize(found, owner_id)
    print("Nothing was deleted.")


def build_parser():
    parser = argparse.ArgumentParser(description="Safe VK wall scan and backup tool")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="Show matching posts; no changes")
    scan.add_argument("--keyword", action="append", default=None)
    scan.set_defaults(func=cmd_scan)
    backup = sub.add_parser("backup", help="Save matching posts to JSON; no VK changes")
    backup.add_argument("--keyword", action="append", default=None)
    backup.add_argument("--output")
    backup.set_defaults(func=cmd_backup)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "keyword") and not args.keyword:
        args.keyword = DEFAULT_KEYWORDS.copy()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("Cancelled")
        sys.exit(130)
    except VKError as exc:
        print(f"VK error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
