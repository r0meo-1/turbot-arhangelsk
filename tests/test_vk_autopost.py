"""Tests for the safe VK marketing autoposter."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
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


def test_publish_is_idempotent_per_iso_week(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "DEFAULT_DB", tmp_path / "autopost.sqlite")
    monkeypatch.setenv("VK_TOKEN", "test-token")
    monkeypatch.setenv("VK_GROUP_ID", "240310110")
    calls = []

    def fake_vk_call(method, token, **params):
        calls.append((method, token, params))
        return {"post_id": 321}

    monkeypatch.setattr(ap, "vk_call", fake_vk_call)
    now = datetime(2026, 9, 22, 19, 30, tzinfo=ZoneInfo("Europe/Moscow"))

    assert ap.publish_one(PLAN, PLAN["posts"][0], now=now) == 321
    assert len(calls) == 1
    assert calls[0][2]["owner_id"] == -240310110
    assert calls[0][2]["from_group"] == 1
    assert "ref=vk_post_pain" in calls[0][2]["message"]

    try:
        ap.publish_one(PLAN, PLAN["posts"][0], now=now)
    except ap.VKAutopostError as exc:
        assert "already published" in str(exc)
    else:
        raise AssertionError("second publish in same ISO week must be rejected")


def test_campaign_date_gate():
    assert ap._campaign_active(
        {"start_date": "2026-11-01", "end_date": "2027-02-28"},
        datetime(2026, 11, 1).date(),
    )
    assert not ap._campaign_active(
        {"start_date": "2026-11-01", "end_date": "2027-02-28"},
        datetime(2026, 10, 31).date(),
    )
