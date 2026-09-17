"""Durable, restart-safe follow-up delivery for the VK bot.

The legacy VK worker kept its one-shot marker only in ``user_data``. A process
restart therefore forgot that a reminder had already been sent and could send
the same message again ten minutes after every deploy. Production deploys are
frequent enough that this became visible to real users.

This module keeps the delivery claim in SQLite and installs itself as a small
runtime patch. The claim is written *before* the VK API call, so two workers or
two overlapping processes cannot both send the same reminder. A normal API
failure releases the claim for a later retry; a process crash keeps the claim,
preferring at-most-once delivery over duplicate customer messages.
"""
from __future__ import annotations

import time
from typing import Any


_TABLE = "vk_followup_claims"


def _cutoffs(bot: Any, now: int) -> tuple[int, int]:
    delay_cutoff = now - int(bot.FOLLOWUP_DELAY_HOURS) * 3600
    timeout_hours = int(bot.DIALOG_TIMEOUT_HOURS)
    timeout_cutoff = now - timeout_hours * 3600 if timeout_hours > 0 else 0
    return delay_cutoff, timeout_cutoff


def ensure_schema(bot: Any, *, seed_existing: bool = False) -> None:
    """Create the durable claim table.

    On the first production rollout, optionally mark already-eligible sessions
    as handled. Their old in-memory marker was lost during the deploy, so we
    cannot know whether they already received a reminder. Skipping one reminder
    is safer than sending another duplicate to a customer.
    """
    now = int(time.time())
    delay_cutoff, timeout_cutoff = _cutoffs(bot, now)
    with bot._db_cursor(commit=True) as cur:
        existed = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (_TABLE,),
        ).fetchone() is not None
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                chat_id INTEGER PRIMARY KEY,
                session_updated_at INTEGER NOT NULL,
                claimed_at INTEGER NOT NULL,
                sent_at INTEGER
            )
            """
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_sent_at ON {_TABLE}(sent_at)"
        )
        # Old sessions may already have been reminded by the pre-fix worker.
        # Seed only on first creation; future starts explicitly clear the claim.
        if seed_existing and not existed and int(bot.FOLLOWUP_DELAY_HOURS) > 0:
            cur.execute(
                f"""
                INSERT OR IGNORE INTO {_TABLE}
                    (chat_id, session_updated_at, claimed_at, sent_at)
                SELECT chat_id, updated_at, ?, ?
                FROM sessions
                WHERE updated_at < ?
                  AND (? = 0 OR updated_at > ?)
                """,
                (now, now, delay_cutoff, timeout_cutoff, timeout_cutoff),
            )
        # Claims for deleted sessions are not useful and chat_id is personal
        # data, so keep the auxiliary table tidy as well.
        cur.execute(
            f"DELETE FROM {_TABLE} WHERE chat_id NOT IN (SELECT chat_id FROM sessions)"
        )


def reset_claim(bot: Any, chat_id: int) -> None:
    """Allow one reminder for a newly started dialog."""
    with bot._db_cursor(commit=True) as cur:
        cur.execute(f"DELETE FROM {_TABLE} WHERE chat_id = ?", (chat_id,))
    with bot._lock:
        info = bot.user_data.get(chat_id)
        if info is not None:
            info.pop("_followed_up", None)


def _claim_candidate(
    bot: Any,
    chat_id: int,
    updated_at: int,
    now: int,
    delay_cutoff: int,
    timeout_cutoff: int,
) -> bool:
    """Atomically reserve one still-eligible session across processes."""
    with bot._db_cursor(commit=True) as cur:
        cur.execute(
            f"""
            INSERT OR IGNORE INTO {_TABLE}
                (chat_id, session_updated_at, claimed_at, sent_at)
            SELECT chat_id, updated_at, ?, NULL
            FROM sessions
            WHERE chat_id = ?
              AND updated_at = ?
              AND updated_at < ?
              AND (? = 0 OR updated_at > ?)
            """,
            (
                now,
                chat_id,
                updated_at,
                delay_cutoff,
                timeout_cutoff,
                timeout_cutoff,
            ),
        )
        return cur.rowcount == 1


def _release_failed_claim(bot: Any, chat_id: int, updated_at: int) -> None:
    with bot._db_cursor(commit=True) as cur:
        cur.execute(
            f"DELETE FROM {_TABLE} "
            "WHERE chat_id = ? AND session_updated_at = ? AND sent_at IS NULL",
            (chat_id, updated_at),
        )


def send_followups(bot: Any) -> int:
    """Send at most one follow-up per dialog, durably across restarts."""
    if int(bot.FOLLOWUP_DELAY_HOURS) <= 0:
        return 0

    ensure_schema(bot)
    now = int(time.time())
    delay_cutoff, timeout_cutoff = _cutoffs(bot, now)
    with bot._db_cursor() as cur:
        rows = cur.execute(
            f"""
            SELECT s.chat_id, s.destination, s.updated_at
            FROM sessions AS s
            LEFT JOIN {_TABLE} AS f ON f.chat_id = s.chat_id
            WHERE f.chat_id IS NULL
              AND s.updated_at < ?
              AND (? = 0 OR s.updated_at > ?)
            ORDER BY s.updated_at ASC
            LIMIT 200
            """,
            (delay_cutoff, timeout_cutoff, timeout_cutoff),
        ).fetchall()

    sent = 0
    for row in rows:
        chat_id = int(row["chat_id"])
        updated_at = int(row["updated_at"])
        if not _claim_candidate(
            bot,
            chat_id,
            updated_at,
            now,
            delay_cutoff,
            timeout_cutoff,
        ):
            continue

        destination = str(row["destination"] or "")
        hint = f" в {destination}" if destination else ""
        response = bot.send_message(
            chat_id,
            f"👋 Вы начали подбор тура{hint}, но не завершили заявку.\n\n"
            "Продолжить? Напишите «Начать», чтобы начать заново, «Отмена», чтобы отменить.",
        )
        if response is None:
            # A known API failure is safe to retry later because VK did not
            # acknowledge the send. A process crash, by contrast, leaves the
            # claim in place and therefore cannot create a duplicate.
            _release_failed_claim(bot, chat_id, updated_at)
            continue

        with bot._db_cursor(commit=True) as cur:
            cur.execute(
                f"UPDATE {_TABLE} SET sent_at = ? "
                "WHERE chat_id = ? AND session_updated_at = ?",
                (int(time.time()), chat_id, updated_at),
            )
        with bot._lock:
            if chat_id in bot.user_data:
                bot.user_data[chat_id]["_followed_up"] = True
        sent += 1

    if sent:
        bot.logger.info("VK durable follow-up sent to %d user(s)", sent)
    return sent


def install(bot: Any) -> None:
    """Install durable follow-ups into an imported ``vk_bot`` module."""
    if getattr(bot, "_durable_followup_installed", False):
        return

    ensure_schema(bot, seed_existing=True)

    original_start = bot.handle_start
    original_set_session = bot.set_session
    original_delete_session = bot.delete_session
    original_delete_user_data = bot.delete_user_data

    def handle_start(user_id: int, first_name: str = "") -> Any:
        reset_claim(bot, user_id)
        return original_start(user_id, first_name)

    def set_session(chat_id: int, data: dict[str, Any]) -> Any:
        # If a session is newly created after a crash/deletion, an orphan claim
        # from an older dialog must not suppress its one allowed reminder.
        if bot.get_session(chat_id) is None:
            reset_claim(bot, chat_id)
        return original_set_session(chat_id, data)

    def delete_session(chat_id: int) -> Any:
        result = original_delete_session(chat_id)
        reset_claim(bot, chat_id)
        return result

    def delete_user_data(chat_id: int) -> Any:
        result = original_delete_user_data(chat_id)
        reset_claim(bot, chat_id)
        return result

    bot.handle_start = handle_start
    bot.set_session = set_session
    bot.delete_session = delete_session
    bot.delete_user_data = delete_user_data
    bot._send_followups = lambda: send_followups(bot)
    bot._durable_followup_installed = True
    bot.logger.info("Durable VK follow-up guard installed")
