#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Safe recurring VK wall autoposter for TurBot / Aprel Tour.

The scheduler is intentionally gated:
- scheduled publishing requires both plan.enabled=true and VK_AUTOPOST_ENABLED=true;
- preview never needs a VK token;
- recurring slots are de-duplicated against both a local SQLite ledger and the VK wall;
- manual publishing requires an explicit --force flag.

Environment:
  VK_TOKEN or VK_ACCESS_TOKEN
  VK_OWNER_ID (negative community wall id) OR VK_GROUP_ID (positive community id)
  VK_AUTOPOST_ENABLED=true
  VK_AUTOPOST_DB=vk_autopost.sqlite        # optional local ledger
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from zoneinfo import ZoneInfo

API_BASE = "https://api.vk.com/method/"
API_VERSION = os.getenv("VK_API_VERSION", "5.199")
DEFAULT_PLAN = Path("marketing/vk_autopost_plan.json")
DEFAULT_DB = Path(os.getenv("VK_AUTOPOST_DB", "vk_autopost.sqlite"))
WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


class VKAutopostError(RuntimeError):
    pass


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_plan(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise VKAutopostError("Unsupported plan version")
    if not data.get("campaign"):
        raise VKAutopostError("Plan campaign is required")
    posts = data.get("posts")
    if not isinstance(posts, list) or not posts:
        raise VKAutopostError("Plan posts must be a non-empty list")
    seen: set[str] = set()
    for post in posts:
        slug = str(post.get("slug") or "").strip()
        source_tag = str(post.get("source_tag") or "").strip()
        if not slug or slug in seen:
            raise VKAutopostError("Every post needs a unique slug")
        seen.add(slug)
        if not source_tag or len(source_tag) > 64:
            raise VKAutopostError(f"Invalid source_tag for {slug}")
        weekday = str(post.get("weekday") or "").lower()
        if weekday not in WEEKDAYS:
            raise VKAutopostError(f"Invalid weekday for {slug}")
        _parse_hhmm(str(post.get("time") or ""))
        if not str(post.get("text") or "").strip():
            raise VKAutopostError(f"Post text is required for {slug}")
    return data


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hh, mm = value.split(":", 1)
        hour, minute = int(hh), int(mm)
    except (ValueError, AttributeError) as exc:
        raise VKAutopostError(f"Invalid time {value!r}; expected HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise VKAutopostError(f"Invalid time {value!r}; expected HH:MM")
    return hour, minute


def resolve_identity() -> tuple[str, int, int]:
    token = (os.getenv("VK_TOKEN") or os.getenv("VK_ACCESS_TOKEN") or "").strip()
    if not token:
        raise VKAutopostError("VK_TOKEN or VK_ACCESS_TOKEN is not set")

    owner_raw = os.getenv("VK_OWNER_ID", "").strip()
    group_raw = os.getenv("VK_GROUP_ID", "").strip()
    if owner_raw:
        try:
            owner_id = int(owner_raw)
        except ValueError as exc:
            raise VKAutopostError("VK_OWNER_ID must be an integer") from exc
        group_id = abs(owner_id)
    elif group_raw:
        try:
            group_id = abs(int(group_raw))
        except ValueError as exc:
            raise VKAutopostError("VK_GROUP_ID must be an integer") from exc
        owner_id = -group_id
    else:
        raise VKAutopostError("VK_OWNER_ID or VK_GROUP_ID is not set")

    if group_id <= 0:
        raise VKAutopostError("VK community id must be positive")
    if owner_id > 0:
        owner_id = -owner_id
    return token, owner_id, group_id


def vk_call(method: str, token: str, **params: Any) -> Any:
    payload = {**params, "access_token": token, "v": API_VERSION}
    try:
        response = requests.post(API_BASE + method, data=payload, timeout=30)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise VKAutopostError(f"VK network/JSON error: {exc}") from exc
    if "error" in body:
        err = body["error"]
        raise VKAutopostError(
            f"VK API error {err.get('error_code')}: {err.get('error_msg')}"
        )
    return body.get("response")


def build_ref_url(group_id: int, source_tag: str) -> str:
    return f"https://vk.me/club{group_id}?ref={quote(source_tag, safe='_-')}"


def render_text(post: dict[str, Any], group_id: int) -> str:
    ref_url = build_ref_url(group_id, str(post["source_tag"]))
    text = str(post["text"]).strip()
    return text.replace("{vk_ref_url}", ref_url)


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(DEFAULT_DB)
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS vk_autopost_log (
                campaign TEXT NOT NULL,
                slug TEXT NOT NULL,
                period_key TEXT NOT NULL,
                post_id INTEGER,
                published_at TEXT NOT NULL,
                PRIMARY KEY (campaign, slug, period_key)
            )
            """
        )
    except BaseException:
        db.close()
        raise
    return db


def period_key(now: datetime) -> str:
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def already_published(campaign: str, slug: str, key: str) -> bool:
    with closing(_db()) as db, db:
        row = db.execute(
            "SELECT 1 FROM vk_autopost_log WHERE campaign=? AND slug=? AND period_key=?",
            (campaign, slug, key),
        ).fetchone()
    return row is not None


def mark_published(campaign: str, slug: str, key: str, post_id: int | None, now: datetime) -> None:
    with closing(_db()) as db, db:
        db.execute(
            """
            INSERT OR IGNORE INTO vk_autopost_log(
                campaign, slug, period_key, post_id, published_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (campaign, slug, key, post_id, now.isoformat()),
        )


def _iso_week_start(now: datetime) -> datetime:
    start = now - timedelta(
        days=now.weekday(),
        hours=now.hour,
        minutes=now.minute,
        seconds=now.second,
        microseconds=now.microsecond,
    )
    return start


def wall_has_source_tag(
    token: str,
    owner_id: int,
    source_tag: str,
    now: datetime,
    *,
    count: int = 100,
) -> bool:
    """Use VK itself as durable idempotency state across ephemeral CI runners."""
    response = vk_call(
        "wall.get",
        token,
        owner_id=owner_id,
        count=count,
        offset=0,
        filter="owner",
    )
    items = response.get("items", []) if isinstance(response, dict) else []
    week_start_ts = int(_iso_week_start(now).timestamp())
    needle = f"ref={source_tag}"
    for item in items:
        if int(item.get("date") or 0) < week_start_ts:
            continue
        if needle in str(item.get("text") or ""):
            return True
    return False


def _campaign_active(plan: dict[str, Any], local_date: date) -> bool:
    start_raw = str(plan.get("start_date") or "").strip()
    end_raw = str(plan.get("end_date") or "").strip()
    if start_raw and local_date < date.fromisoformat(start_raw):
        return False
    if end_raw and local_date > date.fromisoformat(end_raw):
        return False
    return True


def due_posts(plan: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    grace_minutes = int(plan.get("grace_minutes", 180))
    result: list[dict[str, Any]] = []
    for post in plan["posts"]:
        if WEEKDAYS[str(post["weekday"]).lower()] != now.weekday():
            continue
        hour, minute = _parse_hhmm(str(post["time"]))
        scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delta = now - scheduled
        if timedelta(0) <= delta <= timedelta(minutes=grace_minutes):
            result.append(post)
    return result


def publish_one(
    plan: dict[str, Any],
    post: dict[str, Any],
    *,
    force: bool = False,
    now: datetime | None = None,
) -> int:
    tz = ZoneInfo(str(plan.get("timezone") or "Europe/Moscow"))
    now = now or datetime.now(tz)
    campaign = str(plan["campaign"])
    slug = str(post["slug"])
    source_tag = str(post["source_tag"])
    key = period_key(now)

    token, owner_id, group_id = resolve_identity()

    if not force:
        if already_published(campaign, slug, key):
            raise VKAutopostError(f"{slug} already published for {key}")
        if wall_has_source_tag(token, owner_id, source_tag, now):
            mark_published(campaign, slug, key, None, now)
            raise VKAutopostError(f"{slug} already exists on VK wall for {key}")

    message = render_text(post, group_id)
    params: dict[str, Any] = {
        "owner_id": owner_id,
        "from_group": 1,
        "message": message,
    }
    attachments = str(post.get("attachments") or "").strip()
    if attachments:
        params["attachments"] = attachments

    response = vk_call("wall.post", token, **params)
    post_id = int(response.get("post_id")) if isinstance(response, dict) and response.get("post_id") else 0
    mark_published(campaign, slug, key, post_id or None, now)
    return post_id


def cmd_preview(args: argparse.Namespace) -> None:
    plan = load_plan(Path(args.plan))
    group_id = abs(int(os.getenv("VK_GROUP_ID", "240310110") or "240310110"))
    found = False
    for post in plan["posts"]:
        if args.slug and post["slug"] != args.slug:
            continue
        found = True
        print(f"--- {post['slug']} | {post['weekday']} {post['time']} | {post['source_tag']} ---")
        print(render_text(post, group_id))
        print()
    if args.slug and not found:
        raise VKAutopostError(f"Unknown slug: {args.slug}")


def cmd_run(args: argparse.Namespace) -> None:
    plan = load_plan(Path(args.plan))
    tz = ZoneInfo(str(plan.get("timezone") or "Europe/Moscow"))
    now = datetime.now(tz)

    if not bool(plan.get("enabled")):
        print("VK autopost plan is disabled; nothing published.")
        return
    if not _bool_env("VK_AUTOPOST_ENABLED"):
        print("VK_AUTOPOST_ENABLED is not true; nothing published.")
        return
    if not _campaign_active(plan, now.date()):
        print("Campaign is outside its active date range; nothing published.")
        return

    due = due_posts(plan, now)
    if not due:
        print("No VK posts are due in the current schedule window.")
        return

    for post in due:
        try:
            post_id = publish_one(plan, post, now=now)
        except VKAutopostError as exc:
            if "already" in str(exc):
                print(f"{post['slug']}: {exc}; skipping.")
                continue
            raise
        print(f"{post['slug']}: published VK post_id={post_id}")
        return
    print("All due VK posts were already published.")


def cmd_post(args: argparse.Namespace) -> None:
    if not args.force:
        raise VKAutopostError("Manual publishing requires --force")
    plan = load_plan(Path(args.plan))
    post = next((item for item in plan["posts"] if item["slug"] == args.slug), None)
    if post is None:
        raise VKAutopostError(f"Unknown slug: {args.slug}")
    tz = ZoneInfo(str(plan.get("timezone") or "Europe/Moscow"))
    post_id = publish_one(plan, post, force=True, now=datetime.now(tz))
    print(f"{args.slug}: manually published VK post_id={post_id}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TurBot VK marketing autoposter")
    parser.add_argument("--plan", default=str(DEFAULT_PLAN))
    sub = parser.add_subparsers(dest="command", required=True)

    preview = sub.add_parser("preview", help="Render copy and attribution links; no VK writes")
    preview.add_argument("--slug")
    preview.set_defaults(func=cmd_preview)

    run = sub.add_parser("run", help="Publish the due scheduled post when all safety gates are enabled")
    run.set_defaults(func=cmd_run)

    post = sub.add_parser("post", help="Manually publish one configured post")
    post.add_argument("--slug", required=True)
    post.add_argument("--force", action="store_true")
    post.set_defaults(func=cmd_post)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (VKAutopostError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"VK autopost error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
