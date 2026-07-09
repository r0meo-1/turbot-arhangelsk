from __future__ import annotations

import os
import json
import hmac
import sqlite3
import time
import logging
import threading
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from flask import Flask, request, jsonify
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from groq import Groq

from shared.constants import (
    STATE_BUDGET,
    STATE_CONSENT,
    STATE_DATES,
    STATE_DESTINATION,
    STATE_PEOPLE,
    STATE_PHONE,
    PEOPLE_OPTIONS,
    BACK_BUTTON_TEXT,
    CANCEL_BUTTON_TEXT,
    CONSENT_YES_TEXT,
    CONSENT_NO_TEXT,
    POPULAR_DESTINATIONS_TG,
)
from shared.validation import validate_phone, validate_people, validate_budget
from shared.templates import template_selection as _template_selection
from shared.privacy import consent_text as _shared_consent_text, privacy_text as _shared_privacy_text
from shared.ai import generate_ai_selection as _shared_generate_ai
from shared import mdt as mdt_shared

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("turbot")

BOT_TOKEN         = os.getenv("BOT_TOKEN", "")
ADMIN_ID          = int(os.getenv("ADMIN_ID", "0"))
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL        = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
AI_MODE           = os.getenv("AI_MODE", "groq").lower().strip()
PORT                 = int(os.getenv("PORT", "5000"))
STATE_FILE           = os.getenv("STATE_FILE", "bot_state.json")
DATABASE_PATH        = os.getenv("DATABASE_PATH", "bot_state.sqlite")
TELEGRAM_SECRET_TOKEN = os.getenv("TELEGRAM_SECRET_TOKEN", "")
DIALOG_TIMEOUT_HOURS = int(os.getenv("DIALOG_TIMEOUT_HOURS", "6"))
HTTP_TIMEOUT         = 15    # seconds for outbound HTTP calls

# --- Personal-data compliance (152-ФЗ) ------------------------------------
# URL of the privacy policy / consent text shown to users before their personal
# data (name, phone) is collected. Operators of RF personal data MUST publish
# such a document; set this to your hosted policy URL.
PRIVACY_POLICY_URL = os.getenv("PRIVACY_POLICY_URL", "").strip()
# Name of the data operator shown in the consent text.
DATA_OPERATOR_NAME = os.getenv(
    "DATA_OPERATOR_NAME",
    "ИП Замятина Мария Андреевна (ТА «АПРЕЛЬ тур», ОГРНИП 290211659807)",
)
# Days after which a client's personal data is auto-deleted (data minimisation,
# 152-ФЗ ст. 5). Set to 0 to disable automatic retention cleanup.
DATA_RETENTION_DAYS = int(os.getenv("DATA_RETENTION_DAYS", "180"))
BROADCAST_DELAY      = 0.05  # ~20 msg/s — stays under Telegram's ~30 msg/s limit
# Alert admin on critical errors (sent via Telegram message).
ADMIN_ERROR_ALERTS = os.getenv("ADMIN_ERROR_ALERTS", "true").lower().strip() in ("1", "true", "yes")
# How often to send the same error alert (seconds, to avoid spam).
ERROR_ALERT_COOLDOWN = int(os.getenv("ERROR_ALERT_COOLDOWN", "300"))

# MoiDokumenti-Turism (MDT) CRM integration
MDT_ENABLED    = os.getenv("MDT_ENABLED", "false").lower().strip() in ("1", "true", "yes")
MDT_ACCOUNT    = os.getenv("MDT_ACCOUNT", "")          # your-subdomain
MDT_API_KEY    = os.getenv("MDT_API_KEY", "")
MDT_SOURCE     = os.getenv("MDT_SOURCE", "Telegram Bot")
MDT_BASE_URL   = os.getenv("MDT_BASE_URL", "")         # optional override
MDT_MODE       = os.getenv("MDT_MODE", "lead").lower().strip()  # "lead", "preorder", or "both"
MDT_NOTIFY_MANAGERS = os.getenv("MDT_NOTIFY_MANAGERS", "false").lower().strip() in ("1", "true", "yes")
MDT_MANAGER_IDS = [int(x.strip()) for x in os.getenv("MDT_MANAGER_IDS", "").split(",") if x.strip()]
MDT_REMINDER_ENABLED = os.getenv("MDT_REMINDER_ENABLED", "true").lower().strip() in ("1", "true", "yes")
try:
    MDT_REMINDER_DAYS = int(os.getenv("MDT_REMINDER_DAYS", "1"))
except (ValueError, TypeError):
    MDT_REMINDER_DAYS = 1
MDT_REMINDER_TEXT = os.getenv("MDT_REMINDER_TEXT", "Позвонить по заявке с Telegram-бота")

if MDT_MODE not in ("lead", "preorder", "both"):
    logger.warning("MDT_MODE '%s' is unknown, defaulting to 'lead'", MDT_MODE)
    MDT_MODE = "lead"

if MDT_ENABLED and not (MDT_ACCOUNT or MDT_BASE_URL) and not MDT_API_KEY:
    logger.warning("MDT_ENABLED is set but MDT_ACCOUNT/MDT_BASE_URL or MDT_API_KEY is missing")

if not BOT_TOKEN:
    logger.warning("BOT_TOKEN is not set — bot will not work!")
if not ADMIN_ID:
    logger.warning("ADMIN_ID is not set — admin features disabled.")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POPULAR_DESTINATIONS = POPULAR_DESTINATIONS_TG

SHARE_CONTACT_TEXT = "📱 Отправить номер"

USER_HELP = (
    "🤖 Я бот туристического агентства «АПРЕЛЬ тур».\n\n"
    "Помогу подобрать тур по вашим пожеланиям и передам заявку менеджеру.\n\n"
    "Команды:\n"
    "  /start — начать подбор тура\n"
    "  /cancel — отменить текущую заявку\n"
    "  /privacy — политика обработки персональных данных\n"
    "  /delete — удалить мои данные и отозвать согласие\n"
    "  /help — эта справка\n\n"
    "Во время диалога доступны кнопки:\n"
    "  ◀️ Назад — вернуться к прошлому шагу\n"
    "  ❌ Отменить — прервать заявку\n"
    "  📱 Отправить номер — поделиться контактом\n\n"
    "📋 ИП Замятина Мария Андреевна\n"
    "ТА «АПРЕЛЬ тур» · ОГРНИП 290211659807"
)


def _consent_text() -> str:
    """Build the personal-data consent prompt shown before data collection."""
    return _shared_consent_text(
        DATA_OPERATOR_NAME,
        privacy_policy_url=PRIVACY_POLICY_URL,
        erase_hint="командой /delete",
    )


def _privacy_text() -> str:
    """Short privacy notice for the /privacy command."""
    return _shared_privacy_text(
        DATA_OPERATOR_NAME,
        platform_id_label="Telegram",
        privacy_policy_url=PRIVACY_POLICY_URL,
        retention_days=DATA_RETENTION_DAYS,
        erase_hint="команда /delete",
    )

ADMIN_HELP = (
    "🔧 Команды админа:\n\n"
    "/send {chat_id} {текст} — отправить сообщение пользователю\n"
    "/broadcast {текст} — рассылка всем\n"
    "/broadcast {направление} {текст} — рассылка по направлению\n"
    "/users — список пользователей\n"
    "/stats — статистика\n"
    "/restart — сбросить все активные сессии\n"
    "/analytics — аналитика (заявки, направления)\n"
    "/export — экспорт завершённых заявок с телефонами\n"
    "/followup — отправить напоминания незавершившим\n"
    "/mdt [test|reload] — статус MDT CRM (test — проверить соединение, reload — обновить страны)\n"
    "/help — эта справка\n\n"
    "Поддерживаются HTML-теги: <b>жирный</b>, <i>курсив</i>\n\n"
    "Пример:\n"
    "/send 123456789 🌴 <b>Подборка туров</b>"
)

# ---------------------------------------------------------------------------
# Groq client (created once at startup)
# ---------------------------------------------------------------------------

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# ---------------------------------------------------------------------------
# Shared HTTP session with retries for Telegram API calls
# ---------------------------------------------------------------------------

def _create_telegram_session() -> requests.Session:
    """Create a requests session that retries on network errors and 429/5xx."""
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=["POST", "GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


telegram_session = _create_telegram_session()

# ---------------------------------------------------------------------------
# State management with SQLite persistence
# ---------------------------------------------------------------------------

_db_lock = threading.Lock()

# In-memory caches backed by SQLite. The dialog state machine reads and
# mutates `user_data` directly; `save_state()` persists the full working set
# back to SQLite at the end of each webhook request.
user_data: Dict[int, Dict[str, Any]] = {}
all_users: Dict[int, Dict[str, Any]] = {}
_lock = threading.Lock()

# chat_ids whose in-memory session/user record changed since the last
# save_state(). Guarded by _lock. Lets save_state() flush only what changed
# instead of rewriting the whole database on every webhook request.
_dirty_sessions: set[int] = set()
_dirty_users: set[int] = set()
# OrderedDict preserves insertion order so we can drop oldest IDs when full
# instead of wiping the whole set (which would re-accept recent duplicates).
_seen_update_ids: OrderedDict[int, None] = OrderedDict()
_SEEN_UPDATE_MAX = 1000


def _mark_dirty(chat_id: int, *, session: bool = True, user: bool = True) -> None:
    """Flag a chat's in-memory records to be persisted by the next save_state()."""
    with _lock:
        if session:
            _dirty_sessions.add(chat_id)
        if user:
            _dirty_users.add(chat_id)


@contextmanager
def _db_cursor(commit: bool = False):
    """Open a SQLite connection, yield a cursor, and close on exit."""
    conn = sqlite3.connect(
        DATABASE_PATH,
        check_same_thread=False,
        timeout=5,
    )
    conn.row_factory = sqlite3.Row
    try:
        with _db_lock:
            cur = conn.cursor()
            yield cur
            if commit:
                conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create SQLite tables if they don't exist and enable WAL mode."""
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                last_seen INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                consent_at INTEGER
            )
            """
        )
        # Additive migration for databases created before consent tracking.
        cur.execute("PRAGMA table_info(users)")
        if "consent_at" not in {row[1] for row in cur.fetchall()}:
            cur.execute("ALTER TABLE users ADD COLUMN consent_at INTEGER")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                chat_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                destination TEXT,
                dates TEXT,
                people TEXT,
                budget INTEGER,
                phone TEXT,
                updated_at INTEGER NOT NULL
            )
            """
        )
        # Completed leads survive session cleanup so /export and /analytics work.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                first_name TEXT,
                username TEXT,
                destination TEXT,
                dates TEXT,
                people TEXT,
                budget INTEGER,
                phone TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_chat_id ON leads(chat_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at)"
        )
        cur.execute("PRAGMA journal_mode=WAL")


def migrate_json_state() -> None:
    """One-time migration from the old JSON state file to SQLite."""
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for chat_id_str, info in data.get("user_data", {}).items():
            chat_id = int(chat_id_str)
            set_session(chat_id, info)
        for chat_id_str, meta in data.get("all_users", {}).items():
            chat_id = int(chat_id_str)
            touch_user(
                chat_id,
                meta.get("first_name", ""),
                meta.get("username", ""),
                last_seen=meta.get("last_seen"),
            )
        logger.info("Migrated JSON state to SQLite")
        os.rename(STATE_FILE, STATE_FILE + ".migrated")
    except Exception as exc:
        logger.warning("Could not migrate JSON state: %s", exc)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def set_session(chat_id: int, data: Dict[str, Any]) -> None:
    """Insert or replace a dialog session."""
    now = int(time.time())
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO sessions (chat_id, state, destination, dates, people, budget, phone, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                state=excluded.state,
                destination=excluded.destination,
                dates=excluded.dates,
                people=excluded.people,
                budget=excluded.budget,
                phone=excluded.phone,
                updated_at=excluded.updated_at
            """,
            (
                chat_id,
                data.get("state", ""),
                data.get("destination"),
                data.get("dates"),
                data.get("people"),
                data.get("budget"),
                data.get("phone"),
                data.get("updated_at", now),
            ),
        )


def update_session(chat_id: int, **kwargs) -> None:
    """Update specific fields of an existing session."""
    allowed = {"state", "destination", "dates", "people", "budget", "phone", "updated_at"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    columns = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [chat_id]
    with _db_cursor(commit=True) as cur:
        cur.execute(f"UPDATE sessions SET {columns} WHERE chat_id = ?", values)


def get_session(chat_id: int) -> Optional[Dict[str, Any]]:
    """Return the current session for a user, or None."""
    with _db_cursor() as cur:
        cur.execute("SELECT * FROM sessions WHERE chat_id = ?", (chat_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def session_exists(chat_id: int) -> bool:
    """Check whether a user has an active dialog session."""
    return get_session(chat_id) is not None


def delete_session(chat_id: int) -> None:
    """Remove a user's dialog session."""
    with _db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM sessions WHERE chat_id = ?", (chat_id,))


def clear_sessions() -> None:
    """Delete all active dialog sessions."""
    with _db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM sessions")


def count_sessions() -> int:
    """Return the number of active dialog sessions."""
    with _db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM sessions")
        return cur.fetchone()[0]


def list_stale_sessions(cutoff: int) -> List[int]:
    """Return chat_ids of sessions inactive since before `cutoff`."""
    with _db_cursor() as cur:
        cur.execute("SELECT chat_id FROM sessions WHERE updated_at < ?", (cutoff,))
        return [row[0] for row in cur.fetchall()]


def save_lead(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    first_name: str = "",
    username: str = "",
) -> None:
    """Persist a completed tour request for export/analytics."""
    now = int(time.time())
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO leads (
                chat_id, first_name, username, destination, dates,
                people, budget, phone, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                first_name or None,
                username or None,
                info.get("destination"),
                info.get("dates"),
                info.get("people"),
                info.get("budget"),
                phone,
                now,
            ),
        )


def count_leads() -> int:
    """Return the total number of completed leads."""
    with _db_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM leads")
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# User registry helpers
# ---------------------------------------------------------------------------

def touch_user(
    chat_id: int,
    first_name: str,
    username: str,
    last_seen: Optional[int] = None,
) -> None:
    """Insert or update a user record."""
    now = last_seen if last_seen is not None else int(time.time())
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO users (chat_id, first_name, username, last_seen, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                first_name=excluded.first_name,
                username=excluded.username,
                last_seen=excluded.last_seen,
                updated_at=excluded.updated_at
            """,
            (chat_id, first_name, username, now, now, now),
        )


def get_user(chat_id: int) -> Optional[Dict[str, Any]]:
    """Return a user record by chat_id."""
    with _db_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_users(exclude_admin: Optional[int] = None) -> List[Tuple[int, Dict[str, Any]]]:
    """Return all known users as (chat_id, meta) pairs."""
    with _db_cursor() as cur:
        cur.execute("SELECT * FROM users")
        rows = [dict(row) for row in cur.fetchall()]
    result = [(row["chat_id"], row) for row in rows]
    if exclude_admin is not None:
        result = [(cid, meta) for cid, meta in result if cid != exclude_admin]
    return result


def count_users(exclude_admin: Optional[int] = None) -> int:
    """Return the number of known users."""
    with _db_cursor() as cur:
        if exclude_admin is not None:
            cur.execute("SELECT COUNT(*) FROM users WHERE chat_id != ?", (exclude_admin,))
        else:
            cur.execute("SELECT COUNT(*) FROM users")
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# Personal-data consent & erasure (152-ФЗ)
# ---------------------------------------------------------------------------

def has_consent(chat_id: int) -> bool:
    """Return True if the user has an active personal-data processing consent."""
    with _lock:
        meta = all_users.get(chat_id)
        if meta is not None:
            return bool(meta.get("consent_at"))
    user = get_user(chat_id)
    return bool(user and user.get("consent_at"))


def set_consent(chat_id: int) -> None:
    """Record that the user has granted consent (in memory and SQLite)."""
    now = int(time.time())
    with _lock:
        meta = all_users.setdefault(chat_id, {})
        meta["consent_at"] = now
        first_name = meta.get("first_name", "")
        username = meta.get("username", "")
    # Upsert so consent is persisted even if the user row doesn't exist yet.
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO users (chat_id, first_name, username, last_seen, created_at, updated_at, consent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET consent_at=excluded.consent_at
            """,
            (chat_id, first_name, username, now, now, now, now),
        )


def delete_user_data(chat_id: int) -> None:
    """Erase all personal data for a user: session, registry row, and consent.

    Used both by the /delete command (right to erasure / consent withdrawal)
    and by the retention cleanup job.
    """
    with _lock:
        user_data.pop(chat_id, None)
        all_users.pop(chat_id, None)
        _dirty_sessions.discard(chat_id)
        _dirty_users.discard(chat_id)
    with _db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM sessions WHERE chat_id = ?", (chat_id,))
        cur.execute("DELETE FROM users WHERE chat_id = ?", (chat_id,))
        cur.execute("DELETE FROM leads WHERE chat_id = ?", (chat_id,))


def cleanup_expired_data() -> int:
    """Delete personal data of users inactive longer than DATA_RETENTION_DAYS.

    Returns the number of users erased. The admin (ADMIN_ID) is never purged so
    that admin commands keep working. Returns 0 when retention is disabled.
    """
    if DATA_RETENTION_DAYS <= 0:
        return 0
    cutoff = int(time.time()) - DATA_RETENTION_DAYS * 86400
    with _db_cursor() as cur:
        cur.execute(
            "SELECT chat_id FROM users WHERE last_seen < ? AND chat_id != ?",
            (cutoff, ADMIN_ID),
        )
        expired = [row[0] for row in cur.fetchall()]
    for chat_id in expired:
        delete_user_data(chat_id)
    if expired:
        logger.info("Retention cleanup erased %d expired user(s)", len(expired))
    return len(expired)


# ---------------------------------------------------------------------------
# Follow-up for incomplete dialogs
# ---------------------------------------------------------------------------

FOLLOWUP_DELAY_HOURS = int(os.getenv("FOLLOWUP_DELAY_HOURS", "3"))


def _send_followups() -> int:
    """Send one follow-up reminder to users with incomplete dialogs.

    Targets sessions that have been inactive for > FOLLOWUP_DELAY_HOURS but
    less than DIALOG_TIMEOUT_HOURS, and haven't been followed up yet.
    Returns the number of messages sent.
    """
    if FOLLOWUP_DELAY_HOURS <= 0:
        return 0
    now = int(time.time())
    delay_cutoff = now - FOLLOWUP_DELAY_HOURS * 3600
    timeout_cutoff = now - DIALOG_TIMEOUT_HOURS * 3600 if DIALOG_TIMEOUT_HOURS > 0 else 0
    sent = 0
    with _lock:
        candidates = [
            (cid, dict(info))
            for cid, info in user_data.items()
            if not info.get("_followed_up")
            and info.get("updated_at", now) < delay_cutoff
            and (timeout_cutoff == 0 or info.get("updated_at", now) > timeout_cutoff)
        ]
    for cid, info in candidates:
        dest = info.get("destination", "")
        hint = f" в {dest}" if dest else ""
        send_message(
            cid,
            f"👋 Вы начали подбор тура{hint}, но не завершили заявку.\n\n"
            "Продолжить? Отправьте /start, чтобы начать заново, "
            "или /cancel, чтобы отменить.",
        )
        with _lock:
            if cid in user_data:
                user_data[cid]["_followed_up"] = True
        _mark_dirty(cid, user=False)
        sent += 1
    if sent:
        logger.info("Follow-up sent to %d user(s)", sent)
    return sent


def _start_followup_worker() -> None:
    """Start a daemon that periodically sends follow-up reminders."""
    if FOLLOWUP_DELAY_HOURS <= 0:
        logger.info("Follow-up worker is disabled")
        return

    def _worker() -> None:
        while True:
            time.sleep(600)  # check every 10 minutes
            try:
                _send_followups()
            except Exception as exc:
                logger.error("Error in follow-up worker: %s", exc)

    threading.Thread(target=_worker, daemon=True, name="followup").start()
    logger.info("Follow-up worker started (%s hours delay)", FOLLOWUP_DELAY_HOURS)


# ---------------------------------------------------------------------------
# Stale-dialog cleanup
# ---------------------------------------------------------------------------

def _cancel_stale_session(chat_id: int) -> None:
    """Remove a timed-out session and notify the user."""
    with _lock:
        user_data.pop(chat_id, None)
    delete_session(chat_id)
    send_message(
        chat_id,
        "⏰ Вы долго не отвечали, поэтому заявка отменена.\n\n"
        "Чтобы начать заново — отправьте /start.",
        reply_markup=hide_keyboard(),
    )


def _cleanup_stale_dialogs() -> None:
    """Cancel sessions that have been inactive longer than the timeout."""
    if DIALOG_TIMEOUT_HOURS <= 0:
        return
    cutoff = time.time() - DIALOG_TIMEOUT_HOURS * 3600
    with _lock:
        sessions = list(user_data.items())
    for chat_id, info in sessions:
        updated_at = info.get("updated_at")
        if updated_at is None:
            with _lock:
                user_data[chat_id]["updated_at"] = int(time.time())
            continue
        if updated_at < cutoff:
            _cancel_stale_session(chat_id)


def _start_timeout_worker() -> None:
    """Start a background daemon that periodically cleans stale dialogs."""
    if DIALOG_TIMEOUT_HOURS <= 0:
        logger.info("Dialog timeout worker is disabled")
        return

    def _worker() -> None:
        while True:
            time.sleep(60)
            try:
                _cleanup_stale_dialogs()
            except Exception as exc:
                logger.error("Error in timeout worker: %s", exc)

    threading.Thread(target=_worker, daemon=True, name="dialog-timeout").start()
    logger.info("Dialog timeout worker started (%s hours)", DIALOG_TIMEOUT_HOURS)


def _start_retention_worker() -> None:
    """Start a daemon that periodically erases personal data past retention."""
    if DATA_RETENTION_DAYS <= 0:
        logger.info("Data retention cleanup is disabled")
        return

    def _worker() -> None:
        while True:
            try:
                cleanup_expired_data()
            except Exception as exc:
                logger.error("Error in retention worker: %s", exc)
            time.sleep(6 * 3600)  # re-check four times a day

    threading.Thread(target=_worker, daemon=True, name="data-retention").start()
    logger.info("Data retention worker started (%s days)", DATA_RETENTION_DAYS)

# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

def send_message(
    chat_id: int,
    text: str,
    parse_mode: Optional[str] = None,
    reply_markup: Optional[str] = None,
) -> Optional[requests.Response]:
    """Send a text message via Telegram Bot API."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set — cannot send message")
        return None
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = telegram_session.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.error(
                "Telegram %d for %s: %s", resp.status_code, chat_id, resp.text[:200],
            )
        return resp
    except requests.exceptions.RequestException as exc:
        logger.error("send_message(%s) failed: %s", chat_id, exc)
        return None


def send_typing(chat_id: int) -> None:
    """Send 'typing' chat action so the user sees the bot is working."""
    if not BOT_TOKEN:
        return
    try:
        telegram_session.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=5,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Admin error alerting
# ---------------------------------------------------------------------------

_last_error_alert: Dict[str, float] = {}


def _alert_admin_error(error_msg: str, exc: Optional[Exception] = None) -> None:
    """Send a critical error alert to the admin via Telegram (rate-limited)."""
    if not ADMIN_ERROR_ALERTS or not ADMIN_ID or not BOT_TOKEN:
        return
    # Rate-limit: don't send the same error more than once per cooldown.
    key = error_msg[:100]
    now = time.time()
    if _last_error_alert.get(key, 0) > now - ERROR_ALERT_COOLDOWN:
        return
    _last_error_alert[key] = now
    detail = f": {exc}" if exc else ""
    try:
        # Use a raw requests call to avoid recursion if send_message itself fails.
        telegram_session.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_ID, "text": f"⚠️ Ошибка бота: {error_msg}{detail}"},
            timeout=5,
        )
    except Exception:
        pass  # don't let alerting crash the bot


# ---------------------------------------------------------------------------
# MoiDokumenti-Turism (MDT) CRM integration (thin wrappers over shared.mdt)
# ---------------------------------------------------------------------------

_mdt_country_cache: Dict[str, int] = {}


def _mdt_settings() -> mdt_shared.MDTSettings:
    """Build live MDT settings from module globals (tests may mutate them)."""
    return mdt_shared.MDTSettings(
        enabled=MDT_ENABLED,
        account=MDT_ACCOUNT,
        api_key=MDT_API_KEY,
        source=MDT_SOURCE,
        base_url=MDT_BASE_URL,
        mode=MDT_MODE,
        notify_managers=MDT_NOTIFY_MANAGERS,
        manager_ids=list(MDT_MANAGER_IDS),
        reminder_enabled=MDT_REMINDER_ENABLED,
        reminder_days=MDT_REMINDER_DAYS,
        reminder_text=MDT_REMINDER_TEXT,
        timeout=HTTP_TIMEOUT,
        name_prefix="Telegram",
        tourist_tags="Telegram Bot",
        push_title="Новая заявка с Telegram-бота",
    )


def _mdt_base_url() -> str:
    return mdt_shared.base_url(_mdt_settings())


def _mdt_request(method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """POST to MDT API. Monkeypatched by tests."""
    return mdt_shared.http_request(
        _mdt_settings(), telegram_session, method, params, log=logger
    )


def _mdt_load_countries() -> None:
    result = _mdt_request("get-country-list", {})
    if result is None:
        logger.warning("Could not load MDT country list — country matching will be unavailable")
        return
    _mdt_country_cache.clear()
    _mdt_country_cache.update(mdt_shared.parse_country_list(result))
    logger.info("Loaded %d countries from MDT", len(_mdt_country_cache))


def _match_country_id(destination: str) -> int:
    return mdt_shared.match_country_id(_mdt_country_cache, destination)


def _mdt_add_tourist_temp(name: str, phone: str) -> Optional[int]:
    return mdt_shared.add_tourist_temp(
        _mdt_settings(), name, phone, _mdt_request, log=logger
    )


def send_preorder_to_mdt(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> Tuple[Optional[int], Optional[int]]:
    return mdt_shared.create_preorder(
        _mdt_settings(),
        chat_id,
        info,
        phone,
        client_name,
        _mdt_country_cache,
        _mdt_request,
        log=logger,
    )


def _mdt_notify_managers(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> None:
    mdt_shared.notify_managers(
        _mdt_settings(), chat_id, info, phone, client_name, _mdt_request, log=logger
    )


def _mdt_add_reminder(
    preorder_id: int,
    tourist_id: int,
    manager_id: int,
    reminder_date: str,
    reminder_time: str = "10:00:00",
) -> bool:
    return mdt_shared.add_reminder(
        _mdt_settings(),
        preorder_id,
        tourist_id,
        manager_id,
        reminder_date,
        _mdt_request,
        reminder_time=reminder_time,
        log=logger,
    )


def _mdt_create_reminders_for_preorder(
    chat_id: int,
    preorder_id: Optional[int],
    tourist_id: Optional[int],
) -> None:
    mdt_shared.create_reminders_for_preorder(
        _mdt_settings(), chat_id, preorder_id, tourist_id, _mdt_request, log=logger
    )


def _mdt_create_lead(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> bool:
    return mdt_shared.create_lead(
        _mdt_settings(), chat_id, info, phone, client_name, _mdt_request, log=logger
    )


def send_lead_to_mdt(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> None:
    """Dispatch a completed request to MDT CRM based on MDT_MODE."""
    mdt_shared.dispatch_lead(
        _mdt_settings(),
        chat_id,
        info,
        phone,
        client_name,
        _mdt_country_cache,
        _mdt_request,
        log=logger,
    )


def reply_keyboard(
    options: list,
    one_time: bool = True,
    extra_rows: Optional[list] = None,
) -> str:
    """Build a ReplyKeyboardMarkup JSON string.

    `options` are rendered as one button per row. `extra_rows` (list of rows)
    are appended unchanged and can contain strings or KeyboardButton dicts.
    """
    rows = [[opt] for opt in options]
    if extra_rows:
        rows.extend(extra_rows)
    return json.dumps({
        "keyboard": rows,
        "resize_keyboard": True,
        "one_time_keyboard": one_time,
    })


def contact_keyboard() -> str:
    """Reply keyboard with a contact-sharing button plus navigation."""
    return json.dumps({
        "keyboard": [
            [{"text": SHARE_CONTACT_TEXT, "request_contact": True}],
            [BACK_BUTTON_TEXT],
            [CANCEL_BUTTON_TEXT],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    })


def hide_keyboard() -> str:
    """Build a ReplyKeyboardRemove JSON string."""
    return json.dumps({"remove_keyboard": True})

def generate_ai_selection(destination: str, dates: str, people: str, budget: str) -> str:
    """Generate an AI tour blurb for the client (template or Groq)."""
    return _shared_generate_ai(
        destination,
        dates,
        people,
        budget,
        ai_mode=AI_MODE,
        groq_client=groq_client,
        groq_model=GROQ_MODEL,
        log=logger,
    )

# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

def _admin_help(chat_id: int, arg: str) -> bool:
    send_message(chat_id, ADMIN_HELP, parse_mode="HTML")
    return True


def _admin_users(chat_id: int, arg: str) -> bool:
    with _lock:
        users = [(uid, dict(m)) for uid, m in all_users.items() if uid != ADMIN_ID]
    if not users:
        send_message(chat_id, "Пользователей пока нет.")
        return True
    lines = ["📋 Пользователи бота:\n"]
    for uid, meta in users:
        username = meta.get("username", "")
        name = meta.get("first_name") or (f"@{username}" if username else "") or "Без имени"
        lines.append(f"• {name} — ID: {uid}")
    lines.append(f"\nВсего: {len(users)}")
    send_message(chat_id, "\n".join(lines))
    return True


def _admin_stats(chat_id: int, arg: str) -> bool:
    with _lock:
        total = sum(1 for u in all_users if u != ADMIN_ID)
        active = len(user_data)
    leads = count_leads()
    send_message(
        chat_id,
        f"📊 Статистика:\n\n"
        f"Пользователей: {total}\n"
        f"Активных диалогов: {active}\n"
        f"Завершённых заявок: {leads}",
    )
    return True


def _admin_restart(chat_id: int, arg: str) -> bool:
    with _lock:
        user_data.clear()
        _dirty_sessions.clear()
    clear_sessions()
    send_message(chat_id, "✅ Все активные сессии сброшены.")
    return True


def _admin_send(chat_id: int, arg: str) -> bool:
    """`/send {chat_id} {сообщение}` — forward a message to one user."""
    if not arg.strip():
        send_message(chat_id, "Использование: /send {chat_id} {сообщение}")
        return True
    parts = arg.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        send_message(chat_id, "Использование: /send {chat_id} {сообщение}")
        return True
    target, msg = parts[0], parts[1]
    try:
        send_message(int(target), msg, parse_mode="HTML")
        send_message(chat_id, f"✅ Отправлено пользователю {target}")
    except Exception as exc:
        send_message(chat_id, f"❌ Ошибка: {exc}")
    return True


def _admin_mdt(chat_id: int, arg: str) -> bool:
    """`/mdt` — show MDT CRM integration status; `/mdt test` — test connectivity."""
    if not MDT_ENABLED:
        send_message(chat_id, "MDT CRM интеграция отключена (MDT_ENABLED=false).")
        return True
    lines = [
        "📋 MDT CRM статус:",
        f"  Режим: {MDT_MODE}",
        f"  Account: {MDT_ACCOUNT or '(через MDT_BASE_URL)'}",
        f"  Base URL: {_mdt_base_url() or '(не задан)'}",
        f"  API key: {'✅' if MDT_API_KEY else '❌'}",
        f"  Push менеджерам: {'✅' if MDT_NOTIFY_MANAGERS else '❌'}",
        f"  Manager IDs: {MDT_MANAGER_IDS or '(не заданы)'}",
        f"  Стран в кэше: {len(_mdt_country_cache)}",
    ]
    if arg.strip() == "reload":
        _mdt_country_cache.clear()
        _mdt_load_countries()
        lines.append(f"\n✅ Кэш стран обновлён ({len(_mdt_country_cache)} стран)")
    elif arg.strip() == "test":
        result = _mdt_request("get-country-list", {})
        if result is not None:
            lines.append("\n✅ Соединение с MDT работает!")
        else:
            lines.append("\n❌ Не удалось подключиться к MDT.")
    send_message(chat_id, "\n".join(lines))
    return True


def _admin_analytics(chat_id: int, arg: str) -> bool:
    """Show analytics: completed leads, funnel, popular destinations."""
    with _db_cursor() as cur:
        cur.execute("SELECT state, COUNT(*) FROM sessions GROUP BY state")
        by_state = {row[0]: row[1] for row in cur.fetchall()}
        cur.execute("SELECT COUNT(*) FROM users WHERE consent_at IS NOT NULL")
        consented = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM leads")
        total_leads = cur.fetchone()[0]
        # Popular destinations from completed leads (not incomplete sessions)
        cur.execute(
            "SELECT destination, COUNT(*) as cnt FROM leads "
            "WHERE destination IS NOT NULL AND destination != '' "
            "GROUP BY destination ORDER BY cnt DESC LIMIT 10"
        )
        dest_stats = cur.fetchall()
        # Leads in the last 7 / 30 days
        now = int(time.time())
        cur.execute(
            "SELECT COUNT(*) FROM leads WHERE created_at >= ?",
            (now - 7 * 86400,),
        )
        leads_7d = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM leads WHERE created_at >= ?",
            (now - 30 * 86400,),
        )
        leads_30d = cur.fetchone()[0]

    conversion = (
        f"{100.0 * total_leads / consented:.1f}%"
        if consented
        else "—"
    )
    lines = [
        "📊 Аналитика:\n",
        f"Всего пользователей: {total_users}",
        f"Дали согласие: {consented}",
        f"Завершённых заявок: {total_leads}",
        f"  за 7 дней: {leads_7d}",
        f"  за 30 дней: {leads_30d}",
        f"Конверсия (согласие → заявка): {conversion}",
        f"Активных сессий: {sum(by_state.values())}",
    ]
    if by_state:
        lines.append("  Воронка (активные):")
        for state, cnt in sorted(by_state.items()):
            lines.append(f"    {state}: {cnt}")
    if dest_stats:
        lines.append("\n📍 Популярные направления (заявки):")
        for dest, cnt in dest_stats:
            lines.append(f"  {dest}: {cnt}")
    else:
        lines.append("\n📍 Нет завершённых заявок по направлениям")
    send_message(chat_id, "\n".join(lines))
    return True


def _admin_export(chat_id: int, arg: str) -> bool:
    """Export completed leads as a formatted message (last 50)."""
    with _db_cursor() as cur:
        cur.execute(
            "SELECT chat_id, first_name, destination, dates, people, budget, phone, created_at "
            "FROM leads ORDER BY created_at DESC LIMIT 50"
        )
        rows = cur.fetchall()
    if not rows:
        send_message(chat_id, "Нет завершённых заявок для экспорта.")
        return True
    lines = [f"📋 Экспорт заявок ({len(rows)}):\n"]
    for i, row in enumerate(rows, 1):
        cid, name, dest, dates, people, budget, phone, created = row
        when = datetime.fromtimestamp(created).strftime("%d.%m.%Y") if created else "?"
        who = name or str(cid)
        lines.append(
            f"{i}. [{when}] {who} | {dest or '?'} | {dates or '?'} | "
            f"{people or '?'} чел | {budget or '?'}₽ | {phone}"
        )
    # Split into chunks if too long (Telegram limit ~4096 chars)
    text = "\n".join(lines)
    while text:
        chunk, text = text[:4000], text[4000:]
        send_message(chat_id, chunk)
    return True


def _admin_broadcast(chat_id: int, arg: str) -> bool:
    """`/broadcast {текст}` or `/broadcast {направление} {текст}` — send to all or segment.

    Runs in a background thread so the webhook can answer Telegram immediately.
    Destination filter matches users who completed a lead or have an open session
    with that destination.
    """
    if not arg.strip():
        send_message(
            chat_id,
            "Использование: /broadcast {текст} или /broadcast {направление} {текст}",
        )
        return True

    parts = arg.split(" ", 1)
    filter_dest = None
    msg = arg
    with _db_cursor() as cur:
        cur.execute(
            "SELECT DISTINCT destination FROM leads "
            "WHERE destination IS NOT NULL AND destination != ''"
        )
        known_dests = {row[0].lower() for row in cur.fetchall()}
        cur.execute(
            "SELECT DISTINCT destination FROM sessions "
            "WHERE destination IS NOT NULL AND destination != ''"
        )
        known_dests.update(row[0].lower() for row in cur.fetchall())
    if parts[0].lower() in known_dests and len(parts) > 1:
        filter_dest = parts[0]
        msg = parts[1]

    def _run() -> None:
        count = 0
        filter_lower = filter_dest.lower() if filter_dest else None
        matching_ids: Optional[set[int]] = None
        if filter_lower:
            with _db_cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT chat_id FROM leads "
                    "WHERE lower(destination) LIKE ?",
                    (filter_lower + "%",),
                )
                matching_ids = {row[0] for row in cur.fetchall()}
                cur.execute(
                    "SELECT chat_id FROM sessions "
                    "WHERE destination IS NOT NULL AND lower(destination) LIKE ?",
                    (filter_lower + "%",),
                )
                matching_ids.update(row[0] for row in cur.fetchall())
        with _lock:
            recipients = list(all_users.keys())
        for uid in recipients:
            if uid == ADMIN_ID:
                continue
            if matching_ids is not None and uid not in matching_ids:
                continue
            if send_message(uid, msg, parse_mode="HTML"):
                count += 1
            time.sleep(BROADCAST_DELAY)
        if filter_dest:
            send_message(
                chat_id,
                f"✅ Рассылка отправлена {count} пользователям (фильтр: {filter_dest})",
            )
        else:
            send_message(chat_id, f"✅ Рассылка отправлена {count} пользователям")

    threading.Thread(target=_run, daemon=True, name="broadcast").start()
    send_message(chat_id, "📤 Рассылка запущена…")
    return True


def _admin_followup(chat_id: int, arg: str) -> bool:
    """Manually trigger follow-up for incomplete dialogs."""
    sent = _send_followups()
    send_message(chat_id, f"✅ Follow-up отправлен {sent} пользователям")
    return True


# command -> handler(chat_id, arg). Each handler returns True (recognised).
ADMIN_COMMANDS: Dict[str, Callable[[int, str], bool]] = {
    "/help":      _admin_help,
    "/users":     _admin_users,
    "/stats":      _admin_stats,
    "/analytics":  _admin_analytics,
    "/export":     _admin_export,
    "/restart":   _admin_restart,
    "/send":       _admin_send,
    "/broadcast":  _admin_broadcast,
    "/followup":   _admin_followup,
    "/mdt":        _admin_mdt,
}


def handle_admin(chat_id: int, text: str) -> bool:
    """Process admin-only commands. Returns True if command was recognised."""
    command, _, arg = text.partition(" ")
    handler = ADMIN_COMMANDS.get(command)
    if handler is None:
        return False
    return handler(chat_id, arg)

# ---------------------------------------------------------------------------
# User dialog
# ---------------------------------------------------------------------------

def _strip_emoji_prefix(text: str) -> str:
    """If text matches a keyboard button, return the part after the emoji."""
    text = text.strip()
    for dest in POPULAR_DESTINATIONS:
        if text == dest:
            parts = dest.split(" ", 1)
            return parts[1] if len(parts) > 1 else dest
    return text


def handle_start(chat_id: int, first_name: str = "") -> None:
    """Begin the tour-selection dialog, asking for consent first if needed."""
    if not has_consent(chat_id):
        with _lock:
            user_data[chat_id] = {"state": STATE_CONSENT, "updated_at": int(time.time())}
        _mark_dirty(chat_id)
        send_message(
            chat_id,
            _consent_text(),
            reply_markup=reply_keyboard(
                [CONSENT_YES_TEXT, CONSENT_NO_TEXT],
                one_time=False,
            ),
        )
        return
    _begin_destination(chat_id, first_name)


def _begin_destination(chat_id: int, first_name: str = "") -> None:
    """Enter the first data-collection step (destination)."""
    with _lock:
        user_data[chat_id] = {"state": STATE_DESTINATION, "updated_at": int(time.time())}
    _mark_dirty(chat_id)
    name = f", {first_name}" if first_name else ""
    send_message(
        chat_id,
        f"🌴 Здравствуйте{name}! Я помогу подобрать тур под ваши пожелания.\n\n"
        "📍 Куда бы вы хотели отправиться?\n\n"
        "Выберите из популярных направлений или напишите своё:",
        reply_markup=reply_keyboard(
            POPULAR_DESTINATIONS,
            extra_rows=[[CANCEL_BUTTON_TEXT]],
        ),
    )


def handle_cancel(chat_id: int) -> None:
    """Abort the current dialog flow."""
    with _lock:
        existed = user_data.pop(chat_id, None) is not None
    if existed:
        _mark_dirty(chat_id)
        delete_session(chat_id)
        send_message(
            chat_id,
            "❌ Заявка отменена. Чтобы начать заново — /start",
            reply_markup=hide_keyboard(),
        )
    else:
        send_message(chat_id, "Нет активной заявки. Отправьте /start, чтобы начать.")


# --- dialog steps ---------------------------------------------------------
# Each step receives the live session dict `info` (== user_data[chat_id]) so it
# can read and advance the state in place.

def _step_consent(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    """Handle the user's answer to the personal-data consent prompt."""
    if text == CONSENT_YES_TEXT:
        set_consent(chat_id)
        from_info = message.get("from", {})
        _begin_destination(chat_id, from_info.get("first_name", ""))
        return
    if text == CONSENT_NO_TEXT:
        with _lock:
            user_data.pop(chat_id, None)
        _mark_dirty(chat_id, user=False)
        delete_session(chat_id)
        send_message(
            chat_id,
            "Без согласия на обработку персональных данных мы, к сожалению, не сможем подобрать тур.\n\n"
            "Если передумаете — отправьте /start.",
            reply_markup=hide_keyboard(),
        )
        return
    send_message(
        chat_id,
        "Пожалуйста, нажмите «✅ Согласен» или «❌ Отказаться».",
        reply_markup=reply_keyboard([CONSENT_YES_TEXT, CONSENT_NO_TEXT], one_time=False),
    )


def _step_destination(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    dest = _strip_emoji_prefix(text)
    if dest.lower() == "другое":
        send_message(
            chat_id,
            "✍️ Напишите ваше направление:",
            reply_markup=reply_keyboard(
                [],
                one_time=False,
                extra_rows=[[BACK_BUTTON_TEXT], [CANCEL_BUTTON_TEXT]],
            ),
        )
        return
    info["destination"] = dest
    info["state"] = STATE_DATES
    send_message(
        chat_id,
        "📅 На какие даты планируете поездку? (например: 15-22 июня)",
        reply_markup=reply_keyboard(
            [],
            one_time=False,
            extra_rows=[[BACK_BUTTON_TEXT], [CANCEL_BUTTON_TEXT]],
        ),
    )


def _step_dates(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    info["dates"] = text
    info["state"] = STATE_PEOPLE
    send_message(
        chat_id,
        "👥 Сколько человек будет путешествовать?",
        reply_markup=reply_keyboard(
            PEOPLE_OPTIONS,
            extra_rows=[[BACK_BUTTON_TEXT], [CANCEL_BUTTON_TEXT]],
        ),
    )


def _step_people(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    ok, value = validate_people(text)
    if not ok:
        send_message(
            chat_id,
            "Пожалуйста, укажите число от 1 до 50 (или «5+»).",
            reply_markup=reply_keyboard(
                PEOPLE_OPTIONS,
                extra_rows=[[BACK_BUTTON_TEXT], [CANCEL_BUTTON_TEXT]],
            ),
        )
        return
    info["people"] = value
    info["state"] = STATE_BUDGET
    send_message(
        chat_id,
        "💰 Какой бюджет рассматриваете на человека? (в рублях)",
        reply_markup=reply_keyboard(
            [],
            one_time=False,
            extra_rows=[[BACK_BUTTON_TEXT], [CANCEL_BUTTON_TEXT]],
        ),
    )


def _step_budget(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    ok, value = validate_budget(text)
    if not ok:
        send_message(
            chat_id,
            "Пожалуйста, укажите бюджет числом (например: 60000).",
            reply_markup=reply_keyboard(
                [],
                one_time=False,
                extra_rows=[[BACK_BUTTON_TEXT], [CANCEL_BUTTON_TEXT]],
            ),
        )
        return
    info["budget"] = value
    info["state"] = STATE_PHONE
    send_message(
        chat_id,
        "📱 Укажите ваш номер телефона для связи:",
        reply_markup=contact_keyboard(),
    )


def _step_phone(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    ok, phone = validate_phone(text)
    if not ok:
        send_message(
            chat_id,
            "Похоже, номер некорректен. Попробуйте в формате +7XXXXXXXXXX.",
            reply_markup=contact_keyboard(),
        )
        return
    handle_completion(chat_id, phone, message)


# state -> step handler
STATE_HANDLERS: Dict[str, Callable[[int, str, Dict[str, Any], Dict[str, Any]], None]] = {
    STATE_CONSENT:     _step_consent,
    STATE_DESTINATION: _step_destination,
    STATE_DATES:       _step_dates,
    STATE_PEOPLE:      _step_people,
    STATE_BUDGET:      _step_budget,
    STATE_PHONE:       _step_phone,
}

PREVIOUS_STATE: Dict[str, str] = {
    STATE_DATES:       STATE_DESTINATION,
    STATE_PEOPLE:      STATE_DATES,
    STATE_BUDGET:      STATE_PEOPLE,
    STATE_PHONE:       STATE_BUDGET,
}


def _prompt_for_state(chat_id: int, state: str) -> None:
    """Re-ask the question for the given dialog state (used by /back)."""
    if state == STATE_DESTINATION:
        send_message(
            chat_id,
            "📍 Куда бы вы хотели отправиться?\n\n"
            "Выберите из популярных направлений или напишите своё:",
            reply_markup=reply_keyboard(
                POPULAR_DESTINATIONS,
                extra_rows=[[CANCEL_BUTTON_TEXT]],
            ),
        )
    elif state == STATE_DATES:
        send_message(
            chat_id,
            "📅 На какие даты планируете поездку? (например: 15-22 июня)",
            reply_markup=reply_keyboard(
                [],
                one_time=False,
                extra_rows=[[BACK_BUTTON_TEXT], [CANCEL_BUTTON_TEXT]],
            ),
        )
    elif state == STATE_PEOPLE:
        send_message(
            chat_id,
            "👥 Сколько человек будет путешествовать?",
            reply_markup=reply_keyboard(
                PEOPLE_OPTIONS,
                extra_rows=[[BACK_BUTTON_TEXT], [CANCEL_BUTTON_TEXT]],
            ),
        )
    elif state == STATE_BUDGET:
        send_message(
            chat_id,
            "💰 Какой бюджет рассматриваете на человека? (в рублях)",
            reply_markup=reply_keyboard(
                [],
                one_time=False,
                extra_rows=[[BACK_BUTTON_TEXT], [CANCEL_BUTTON_TEXT]],
            ),
        )
    elif state == STATE_PHONE:
        send_message(
            chat_id,
            "📱 Укажите ваш номер телефона для связи:",
            reply_markup=contact_keyboard(),
        )


def _go_back(chat_id: int) -> None:
    """Move the user one step back in the dialog."""
    info = user_data.get(chat_id, {})
    state = info.get("state")
    previous = PREVIOUS_STATE.get(state)
    if previous is None:
        send_message(chat_id, "Вы на первом шаге. Можно отменить заявку кнопкой «Отменить».")
        return
    info["state"] = previous
    _mark_dirty(chat_id, user=False)
    _prompt_for_state(chat_id, previous)


def handle_dialog(chat_id: int, text: str, message: Dict[str, Any]) -> None:
    """Process a message within the state-machine dialog."""
    info = user_data.get(chat_id, {})
    state = info.get("state")
    if state is None:
        send_message(chat_id, "Для начала работы отправьте /start")
        return
    handler = STATE_HANDLERS.get(state)
    if handler is None:
        # Unknown state — shouldn't happen; behave as the original (no-op).
        return
    handler(chat_id, text, message, info)
    _mark_dirty(chat_id, user=False)


# --- request completion ---------------------------------------------------

import html as _html_module

def _esc(text: Any) -> str:
    """Escape user-provided text for safe inclusion in HTML messages."""
    return _html_module.escape(str(text), quote=False)


def _confirm_to_user(chat_id: int, info: Dict[str, Any], phone: str) -> None:
    """1. Send the request summary back to the client."""
    send_message(
        chat_id,
        "✅ Ваша заявка принята! Наш менеджер свяжется с вами в ближайшее время.\n\n"
        f"📍 Направление: {info.get('destination', '?')}\n"
        f"📅 Даты: {info.get('dates', '?')}\n"
        f"👥 Человек: {info.get('people', '?')}\n"
        f"💰 Бюджет: {info.get('budget', '?')}₽\n"
        f"📱 Телефон: {phone}\n\n"
        "Спасибо за обращение в «АПРЕЛЬ тур»! 🌺\n\n"
        "📋 ИП Замятина Мария Андреевна\n"
        "ОГРНИП 290211659807",
        reply_markup=hide_keyboard(),
    )


def _notify_admin(chat_id: int, info: Dict[str, Any], phone: str, client_name: Optional[str]) -> None:
    """2. Forward the lead to the admin (if configured)."""
    if not ADMIN_ID:
        return
    send_message(
        ADMIN_ID,
        "🔔 Новая заявка!\n\n"
        f"От: {_esc(client_name or 'без имени')} (ID: {chat_id})\n"
        f"📍 {_esc(info.get('destination', '?'))}\n"
        f"📅 {_esc(info.get('dates', '?'))}\n"
        f"👥 {_esc(info.get('people', '?'))} чел\n"
        f"💰 {_esc(info.get('budget', '?'))}₽\n"
        f"📱 {_esc(phone)}\n\n"
        f"Ответить: /send {chat_id} ваше сообщение",
        parse_mode="HTML",
    )


def _send_ai_blurb(chat_id: int, info: Dict[str, Any]) -> None:
    """3. Show typing, then the AI-generated tour suggestion."""
    send_typing(chat_id)
    ai = generate_ai_selection(
        info.get("destination", ""),
        info.get("dates", ""),
        info.get("people", ""),
        info.get("budget", ""),
    )
    send_message(chat_id, ai)


# When true, MDT + AI run inline (tests). In production they run in a
# background thread so the webhook answers Telegram before slow I/O.
SYNC_COMPLETION = os.getenv("SYNC_COMPLETION", "").lower().strip() in ("1", "true", "yes")


def _post_completion_side_effects(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> None:
    """MDT push + AI blurb — intentionally off the webhook critical path."""
    try:
        send_lead_to_mdt(chat_id, info, phone, client_name)
        _send_ai_blurb(chat_id, info)
    except Exception as exc:
        logger.error("Post-completion side effects failed for %s: %s", chat_id, exc)
        _alert_admin_error("Post-completion side effects failed", exc)


def handle_completion(chat_id: int, phone: str, message: Dict[str, Any]) -> None:
    """Finalise the request: confirm, notify admin, persist lead; defer MDT/AI."""
    info = dict(user_data.get(chat_id, {}))
    from_info = message.get("from", {})
    first_name = from_info.get("first_name", "")
    username = from_info.get("username", "")
    client_name = first_name or (f"@{username}" if username else None)

    # Persist lead before side-effects so export/analytics work even if notify fails.
    try:
        save_lead(chat_id, info, phone, first_name=first_name, username=username)
    except Exception as exc:
        logger.error("Failed to save lead for %s: %s", chat_id, exc)
        _alert_admin_error("Failed to save lead", exc)

    _confirm_to_user(chat_id, info, phone)            # 1. Confirm to user
    _notify_admin(chat_id, info, phone, client_name)  # 2. Notify admin (sync — ops must see it)
    with _lock:                                       # 3. Clean up session promptly
        user_data.pop(chat_id, None)
    delete_session(chat_id)

    # 4–5. CRM + AI can be slow (network); don't block Telegram's webhook ACK.
    if SYNC_COMPLETION:
        _post_completion_side_effects(chat_id, info, phone, client_name)
    else:
        threading.Thread(
            target=_post_completion_side_effects,
            args=(chat_id, info, phone, client_name),
            daemon=True,
            name=f"complete-{chat_id}",
        ).start()

# ---------------------------------------------------------------------------
# Flask app & routes
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # 1 MB — Telegram updates are well under this


@app.route("/")
def index() -> str:
    return "TurBot is running!"


@app.route("/health")
def health() -> Any:
    """Simple health/readiness endpoint for monitoring."""
    try:
        leads_total = count_leads()
    except Exception:
        leads_total = -1
    return jsonify({
        "status": "ok",
        "bot_token_configured": bool(BOT_TOKEN),
        "admin_id_configured": bool(ADMIN_ID),
        "groq_configured": bool(GROQ_API_KEY),
        "ai_mode": AI_MODE,
        "mdt_enabled": MDT_ENABLED,
        "mdt_mode": MDT_MODE,
        "total_users": len(all_users),
        "active_sessions": len(user_data),
        "total_leads": leads_total,
        "privacy_policy_configured": bool(PRIVACY_POLICY_URL),
        "data_retention_days": DATA_RETENTION_DAYS,
    })


def _process_update(data: Dict[str, Any]) -> None:
    """Parse one Telegram update and route it to the right handler."""
    # Deduplicate: Telegram may retry the same update_id.
    update_id = data.get("update_id")
    if update_id is not None:
        with _lock:
            if update_id in _seen_update_ids:
                logger.debug("Skipping duplicate update_id=%s", update_id)
                return
            _seen_update_ids[update_id] = None
            # Drop oldest IDs when full — never wipe the whole set.
            while len(_seen_update_ids) > _SEEN_UPDATE_MAX:
                _seen_update_ids.popitem(last=False)
    message = data["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    from_info = message.get("from", {})
    first_name = from_info.get("first_name", "")
    username = from_info.get("username", "")

    # Track every user (preserving an existing consent timestamp).
    with _lock:
        meta = all_users.setdefault(chat_id, {})
        meta["first_name"] = first_name
        meta["username"] = username
        meta["last_seen"] = int(time.time())
        # Update activity timestamp for open dialogs so the timeout worker
        # doesn't cancel them while the user is actively typing.
        if chat_id in user_data:
            user_data[chat_id]["updated_at"] = int(time.time())
            session_open = True
        else:
            session_open = False
    _mark_dirty(chat_id, session=session_open)

    # Shared contact (e.g. phone button)
    contact = message.get("contact")
    if contact and contact.get("phone_number"):
        phone_number = contact["phone_number"]
        info = user_data.get(chat_id, {})
        if info.get("state") == STATE_PHONE:
            _step_phone(chat_id, phone_number, message, info)
        else:
            send_message(chat_id, "Спасибо, но сейчас номер телефона не требуется. 📝")
        return

    # Non-text messages (photos, stickers, etc.)
    if not text:
        send_message(chat_id, "Пожалуйста, отправьте текстовое сообщение. 📝")
        return

    # Normalise /cmd@botname → /cmd
    if text.startswith("/"):
        text = text.split("@")[0]

    # --- Admin commands (checked first) ---
    if chat_id == ADMIN_ID:
        if handle_admin(chat_id, text):
            return

    # --- User commands ---
    if text == "/start":
        handle_start(chat_id, first_name)
        return

    if text == "/help":
        send_message(chat_id, USER_HELP)
        return

    if text == "/privacy":
        send_message(chat_id, _privacy_text())
        return

    if text == "/delete":
        delete_user_data(chat_id)
        send_message(
            chat_id,
            "🗑 Ваши персональные данные удалены, согласие отозвано.\n\n"
            "Чтобы снова воспользоваться подбором тура — отправьте /start.",
            reply_markup=hide_keyboard(),
        )
        return

    if text == "/cancel" or text == CANCEL_BUTTON_TEXT:
        handle_cancel(chat_id)
        return

    # --- Navigation inside the dialog ---
    if text == BACK_BUTTON_TEXT:
        if chat_id in user_data:
            _go_back(chat_id)
        else:
            send_message(chat_id, "Для начала работы отправьте /start")
        return

    # --- Unknown slash-command ---
    if text.startswith("/"):
        if chat_id in user_data:
            send_message(
                chat_id,
                "Неизвестная команда. /cancel — отменить, /help — справка",
            )
        else:
            send_message(
                chat_id,
                "Неизвестная команда. /start — начать, /help — справка",
            )
        return

    # --- Dialog flow ---
    if chat_id in user_data:
        handle_dialog(chat_id, text, message)
    else:
        send_message(chat_id, "Для начала работы отправьте /start")


def _check_webhook_secret() -> bool:
    """Verify Telegram secret token if one is configured."""
    if not TELEGRAM_SECRET_TOKEN:
        return True
    header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return hmac.compare_digest(header, TELEGRAM_SECRET_TOKEN)


@app.route("/webhook", methods=["POST"])
def webhook() -> Tuple[str, int]:
    if not _check_webhook_secret():
        logger.warning("Webhook called with missing/invalid secret token")
        return "Forbidden", 403

    try:
        data = request.get_json(silent=True)
        if data and "message" in data:
            _process_update(data)
    except Exception as exc:
        logger.error("Error in webhook: %s", exc, exc_info=True)
        _alert_admin_error("Webhook processing error", exc)

    finally:
        save_state()

    return "OK", 200


# ---------------------------------------------------------------------------
# State persistence (SQLite-backed)
# ---------------------------------------------------------------------------

def load_state() -> None:
    """Initialize the database and load sessions/users into memory."""
    init_db()
    migrate_json_state()
    with _db_cursor() as cur:
        cur.execute("SELECT * FROM sessions")
        for row in cur.fetchall():
            d = dict(row)
            chat_id = d.pop("chat_id")
            user_data[chat_id] = d
        cur.execute("SELECT * FROM users")
        for row in cur.fetchall():
            d = dict(row)
            chat_id = d.pop("chat_id")
            all_users[chat_id] = d
    logger.info("Loaded %d sessions and %d users from SQLite", len(user_data), len(all_users))


def save_state() -> None:
    """Persist in-memory sessions and users that changed since the last call.

    Only records flagged via _mark_dirty() are written, so a webhook request
    touches a single chat_id instead of rewriting the whole database. A session
    whose chat_id is dirty but no longer in memory (cancelled/completed) is
    deleted, keeping SQLite in sync with memory.
    """
    with _lock:
        session_ids = _dirty_sessions.copy()
        user_ids = _dirty_users.copy()
        _dirty_sessions.clear()
        _dirty_users.clear()

    for chat_id in session_ids:
        info = user_data.get(chat_id)
        if info is not None:
            set_session(chat_id, info)
        else:
            delete_session(chat_id)

    for chat_id in user_ids:
        meta = all_users.get(chat_id)
        if meta is None:
            continue
        touch_user(
            chat_id,
            meta.get("first_name", ""),
            meta.get("username", ""),
            last_seen=meta.get("last_seen"),
        )


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

load_state()
_start_timeout_worker()
_start_followup_worker()
_start_retention_worker()

if MDT_ENABLED:
    _mdt_load_countries()

logger.info(
    "TurBot started (port=%s, admin_set=%s, groq_set=%s, webhook_secret_set=%s)",
    PORT,
    bool(ADMIN_ID),
    bool(GROQ_API_KEY),
    bool(TELEGRAM_SECRET_TOKEN),
)

import signal as _signal_module

def _graceful_shutdown(signum: int, frame: Any) -> None:
    """Save state on SIGTERM/SIGINT so no data is lost during deploy."""
    logger.info("Received signal %s — saving state and exiting", signum)
    try:
        # Flush ALL in-memory state, not just dirty entries.
        with _lock:
            sessions = list(user_data.items())
            users = list(all_users.items())
        for cid, info in sessions:
            set_session(cid, info)
        for cid, meta in users:
            touch_user(cid, meta.get("first_name", ""), meta.get("username", ""),
                       last_seen=meta.get("last_seen"))
        logger.info("State saved on shutdown (%d sessions, %d users)", len(sessions), len(users))
    except Exception as exc:
        logger.error("Error saving state on shutdown: %s", exc)
    import sys; sys.exit(0)


_signal_module.signal(_signal_module.SIGTERM, _graceful_shutdown)
_signal_module.signal(_signal_module.SIGINT, _graceful_shutdown)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
