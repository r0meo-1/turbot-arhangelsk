"""Tests for privacy-minimized cross-channel acquisition funnel metrics."""

from contextlib import contextmanager
import sqlite3

from shared import funnel_metrics


def _factory(path):
    @contextmanager
    def db_cursor(commit=False):
        conn = sqlite3.connect(path)
        try:
            cur = conn.cursor()
            yield cur
            if commit:
                conn.commit()
        finally:
            conn.close()
    return db_cursor


def test_funnel_snapshot_groups_source_and_conversion(tmp_path):
    db = _factory(tmp_path / "funnel.sqlite")
    with db(commit=True) as cur:
        funnel_metrics.init_schema(cur)

    funnel_metrics.record(db, "telegram", "vk_winter", "start", "opened", now=1000)
    funnel_metrics.record(db, "telegram", "vk_winter", "lead", "accepted", now=1001)
    funnel_metrics.record(db, "telegram", "vk_winter", "manager", "delivered", now=1002)
    funnel_metrics.record(db, "website", "yandex:sea", "lead", "accepted", now=1003)

    data = funnel_metrics.snapshot(db, now=1100, window_seconds=1000)

    tg = data["channels"]["telegram"]["vk_winter"]
    assert tg["start"]["opened"] == 1
    assert tg["lead"]["accepted"] == 1
    assert tg["manager"]["delivered"] == 1
    assert tg["summary"] == {
        "leads": 1,
        "manager_delivered": 1,
        "lead_to_manager_pct": 100.0,
    }
    assert data["channels"]["website"]["yandex:sea"]["summary"]["leads"] == 1


def test_funnel_source_redacts_accidental_pii(tmp_path):
    db = _factory(tmp_path / "privacy.sqlite")
    with db(commit=True) as cur:
        funnel_metrics.init_schema(cur)

    funnel_metrics.record(db, "website", "test@example.com", "lead", "accepted")
    funnel_metrics.record(db, "telegram", "+79991234567", "lead", "accepted")
    funnel_metrics.record(db, "vk", "https://example.com/user", "start", "opened")

    with db() as cur:
        sources = [row[0] for row in cur.execute(
            "SELECT source FROM acquisition_funnel_events ORDER BY id"
        ).fetchall()]

    assert sources == ["redacted", "redacted", "redacted"]
    stored = "\n".join(sources)
    assert "example.com" not in stored
    assert "79991234567" not in stored


def test_funnel_rejects_unbounded_stage_or_outcome(tmp_path):
    db = _factory(tmp_path / "invalid.sqlite")
    with db(commit=True) as cur:
        funnel_metrics.init_schema(cur)

    funnel_metrics.record(db, "telegram", "site", "free_form_stage", "opened")
    funnel_metrics.record(db, "telegram", "site", "lead", "customer_text_here")

    with db() as cur:
        assert cur.execute(
            "SELECT COUNT(*) FROM acquisition_funnel_events"
        ).fetchone()[0] == 0
