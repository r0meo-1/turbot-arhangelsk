"""Privacy-minimized acquisition funnel metrics shared by TurBot channels."""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, Optional

_SAFE = re.compile(r"[^a-z0-9._:-]+")
_ALLOWED_STAGES = {"start", "lead", "manager", "ai_handoff"}
_ALLOWED_OUTCOMES = {
    "opened", "accepted", "duplicate", "delivered", "failed", "escalated"
}


def _safe_token(value: Any, fallback: str, max_len: int) -> str:
    token = _SAFE.sub("_", str(value or "").strip().lower()).strip("_.:-")
    return (token or fallback)[:max_len]


def _safe_source(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if (
        "@" in raw
        or "http://" in raw
        or "https://" in raw
        or re.search(r"\d{7,}", raw)
    ):
        return "redacted"
    return _safe_token(raw, "direct", 64)


def init_schema(cur: Any) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS acquisition_funnel_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel TEXT NOT NULL,
            source TEXT NOT NULL,
            stage TEXT NOT NULL,
            outcome TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_acquisition_funnel_created "
        "ON acquisition_funnel_events(created_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_acquisition_funnel_source "
        "ON acquisition_funnel_events(channel, source, created_at)"
    )


def record(
    db_cursor_factory: Callable[..., Any],
    channel: str,
    source: str,
    stage: str,
    outcome: str,
    *,
    now: Optional[int] = None,
) -> None:
    """Store one bounded event. No user identifier or free-form text is accepted."""
    channel_key = _safe_token(channel, "unknown", 24)
    source_key = _safe_source(source)
    stage_key = _safe_token(stage, "unknown", 24)
    outcome_key = _safe_token(outcome, "unknown", 24)
    if stage_key not in _ALLOWED_STAGES or outcome_key not in _ALLOWED_OUTCOMES:
        return
    with db_cursor_factory(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO acquisition_funnel_events(
                channel, source, stage, outcome, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                channel_key,
                source_key,
                stage_key,
                outcome_key,
                int(time.time()) if now is None else int(now),
            ),
        )


def snapshot(
    db_cursor_factory: Callable[..., Any],
    *,
    now: Optional[float] = None,
    window_seconds: int = 30 * 86400,
) -> Dict[str, Any]:
    current = int(time.time() if now is None else now)
    cutoff = current - int(window_seconds)
    with db_cursor_factory() as cur:
        rows = cur.execute(
            """
            SELECT channel, source, stage, outcome, COUNT(*) AS n
            FROM acquisition_funnel_events
            WHERE created_at >= ?
            GROUP BY channel, source, stage, outcome
            ORDER BY channel, source, stage, outcome
            """,
            (cutoff,),
        ).fetchall()

    channels: Dict[str, Any] = {}
    total_events = 0
    for row in rows:
        channel = str(row[0])
        source = str(row[1])
        stage = str(row[2])
        outcome = str(row[3])
        count = int(row[4] or 0)
        total_events += count
        source_node = channels.setdefault(channel, {}).setdefault(source, {})
        source_node.setdefault(stage, {})[outcome] = count

    for sources in channels.values():
        for node in sources.values():
            leads = int(node.get("lead", {}).get("accepted", 0))
            delivered = int(node.get("manager", {}).get("delivered", 0))
            node["summary"] = {
                "leads": leads,
                "manager_delivered": delivered,
                "lead_to_manager_pct": (
                    round(delivered * 100.0 / leads, 1) if leads else None
                ),
            }

    return {
        "available": True,
        "window_seconds": int(window_seconds),
        "events": total_events,
        "channels": channels,
    }


def cleanup(
    db_cursor_factory: Callable[..., Any],
    retention_days: int,
    *,
    now: Optional[int] = None,
) -> int:
    """Delete anonymous funnel events older than the configured lifetime."""
    days = int(retention_days or 0)
    if days <= 0:
        return 0
    current = int(time.time()) if now is None else int(now)
    cutoff = current - days * 86400
    with db_cursor_factory(commit=True) as cur:
        cur.execute(
            "DELETE FROM acquisition_funnel_events WHERE created_at < ?",
            (cutoff,),
        )
        return int(cur.rowcount or 0)



def format_report(
    data: Dict[str, Any],
    *,
    channels: Optional[list[str]] = None,
    max_sources: int = 10,
) -> str:
    """Render a compact human admin report without exposing customer data."""
    window_days = max(1, int(data.get("window_seconds") or 0) // 86400)
    all_channels = data.get("channels") if isinstance(data.get("channels"), dict) else {}
    selected = channels or sorted(all_channels)
    lines = [f"📈 Воронка · {window_days} дней"]
    shown_any = False

    labels = {
        "telegram": "Telegram",
        "website": "Сайт",
        "vk": "VK",
    }
    for channel in selected:
        sources = all_channels.get(channel)
        if not isinstance(sources, dict) or not sources:
            continue
        shown_any = True
        lines.append(f"\n{labels.get(channel, channel)}:")
        ordered = sorted(
            sources.items(),
            key=lambda item: (
                -int((item[1].get("summary") or {}).get("leads") or 0),
                -int((item[1].get("start") or {}).get("opened") or 0),
                item[0],
            ),
        )
        for source, node in ordered[:max(1, int(max_sources))]:
            starts = int((node.get("start") or {}).get("opened") or 0)
            accepted = int((node.get("lead") or {}).get("accepted") or 0)
            duplicates = int((node.get("lead") or {}).get("duplicate") or 0)
            delivered = int((node.get("manager") or {}).get("delivered") or 0)
            failed = int((node.get("manager") or {}).get("failed") or 0)
            escalated = int((node.get("ai_handoff") or {}).get("escalated") or 0)
            pct = (node.get("summary") or {}).get("lead_to_manager_pct")
            pct_text = f"{pct:g}%" if isinstance(pct, (int, float)) else "—"

            if channel == "website":
                core = (
                    f"• {source}: лиды {accepted} → менеджер {delivered} "
                    f"({pct_text})"
                )
            else:
                core = (
                    f"• {source}: старт {starts} → лиды {accepted} → "
                    f"менеджер {delivered} ({pct_text})"
                )
            extras = []
            if duplicates:
                extras.append(f"дубли {duplicates}")
            if failed:
                extras.append(f"ошибки доставки {failed}")
            if escalated:
                extras.append(f"AI→человек {escalated}")
            if extras:
                core += " · " + ", ".join(extras)
            lines.append(core)

        if len(ordered) > max(1, int(max_sources)):
            lines.append(f"… ещё источников: {len(ordered) - max(1, int(max_sources))}")

    if not shown_any:
        lines.append("\nДанных пока нет.")
    if "website" in selected:
        lines.append("\nСтарты формы сайта считаются отдельно в Яндекс Метрике.")
    return "\n".join(lines)
