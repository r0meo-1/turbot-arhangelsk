"""Tests for the safe VK marketing autoposter."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import vk_autopost as ap


PLAN = {
    "version": 1,
    "campaign": "winter",
    "timezone": "Europe/Moscow",
    "enabled": True,
    "grace_minutes": 180,
    "posts": [
        {
            "slug": "pain",
            "source_tag": "vk_post_pain",
            "weekday": "tue",
            "time": "19:30",
            "attachments": "",
            "text": "Start: {vk_ref_url}",
        }
    ],
}


def test_ref_url_and_render():
    assert ap.build_ref_url(240310110, "vk_post_pain") == (
        "https://vk.me/club240310110?ref=vk_post_pain"
    )
    assert ap.render_text(PLAN["posts"][0], 240310110).endswith(
        "https://vk.me/club240310110?ref=vk_post_pain"
    )


def test_due_posts_only_inside_schedule_window():
    tz = ZoneInfo("Europe/Moscow")
    assert ap.due_posts(PLAN, datetime(2026, 9, 22, 20, 0, tzinfo=tz))
    assert not ap.due_posts(PLAN, datetime(2026, 9, 22, 23, 0, tzinfo=tz))
    assert not ap.due_posts(PLAN, datetime(2026, 9, 23, 20, 0, tzinfo=tz))


def test_wall_dedupe_finds_current_week_source_tag(monkeypatch):
    tz = ZoneInfo("Europe/Moscow")
    now = datetime(2026, 9, 22, 19, 30, tzinfo=tz)
    current_ts = int(datetime(2026, 9, 21, 10, 0, tzinfo=tz).timestamp())
    old_ts = int(datetime(2026, 9, 13, 10, 0, tzinfo=tz).timestamp())

    def fake_vk_call(method, token, **params):
        assert method == "wall.get"
        return {
            "items": [
                {"date": old_ts, "text": "https://vk.me/club240310110?ref=vk_post_pain"},
                {"date": current_ts, "text": "https://vk.me/club240310110?ref=vk_post_pain"},
            ]
        }

    monkeypatch.setattr(ap, "vk_call", fake_vk_call)
    assert ap.wall_has_source_tag("t", -240310110, "vk_post_pain", now)


def test_publish_is_idempotent_per_iso_week(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "DEFAULT_DB", tmp_path / "autopost.sqlite")
    monkeypatch.setenv("VK_TOKEN", "test-token")
    monkeypatch.setenv("VK_GROUP_ID", "240310110")
    calls = []

    def fake_vk_call(method, token, **params):
        calls.append((method, token, params))
        if method == "wall.get":
            return {"items": []}
        if method == "wall.post":
            return {"post_id": 321}
        raise AssertionError(method)

    monkeypatch.setattr(ap, "vk_call", fake_vk_call)
    now = datetime(2026, 9, 22, 19, 30, tzinfo=ZoneInfo("Europe/Moscow"))

    assert ap.publish_one(PLAN, PLAN["posts"][0], now=now) == 321
    assert [c[0] for c in calls] == ["wall.get", "wall.post"]
    assert calls[1][2]["owner_id"] == -240310110
    assert calls[1][2]["from_group"] == 1
    assert "ref=vk_post_pain" in calls[1][2]["message"]

    try:
        ap.publish_one(PLAN, PLAN["posts"][0], now=now)
    except ap.VKAutopostError as exc:
        assert "already published" in str(exc)
    else:
        raise AssertionError("second publish in same ISO week must be rejected")


def test_publish_rejects_existing_vk_wall_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "DEFAULT_DB", tmp_path / "autopost.sqlite")
    monkeypatch.setenv("VK_TOKEN", "test-token")
    monkeypatch.setenv("VK_GROUP_ID", "240310110")
    now = datetime(2026, 9, 22, 19, 30, tzinfo=ZoneInfo("Europe/Moscow"))
    current_ts = int(datetime(2026, 9, 22, 18, 0, tzinfo=ZoneInfo("Europe/Moscow")).timestamp())

    def fake_vk_call(method, token, **params):
        if method == "wall.get":
            return {
                "items": [
                    {
                        "date": current_ts,
                        "text": "Start: https://vk.me/club240310110?ref=vk_post_pain",
                    }
                ]
            }
        raise AssertionError("wall.post must not run")

    monkeypatch.setattr(ap, "vk_call", fake_vk_call)
    try:
        ap.publish_one(PLAN, PLAN["posts"][0], now=now)
    except ap.VKAutopostError as exc:
        assert "already exists on VK wall" in str(exc)
    else:
        raise AssertionError("existing VK source marker must block duplicate publish")


def test_campaign_date_gate():
    assert ap._campaign_active(
        {"start_date": "2026-11-01", "end_date": "2027-02-28"},
        datetime(2026, 11, 1).date(),
    )
    assert not ap._campaign_active(
        {"start_date": "2026-11-01", "end_date": "2027-02-28"},
        datetime(2026, 10, 31).date(),
    )
