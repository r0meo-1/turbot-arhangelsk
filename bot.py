from __future__ import annotations

import os
import re
import json
import hmac
import sqlite3
import time
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from flask import Flask, request, jsonify
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from groq import Groq

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

POPULAR_DESTINATIONS = [
    "🏖 Египет",
    "🏝 Турция",
    "🌴 Таиланд",
    "🌊 Мальдивы",
    "🏛 ОАЭ",
    "✏️ Другое",
]

PEOPLE_OPTIONS = ["1", "2", "3", "4", "5+"]

BACK_BUTTON_TEXT   = "◀️ Назад"
CANCEL_BUTTON_TEXT = "❌ Отменить"
SHARE_CONTACT_TEXT = "📱 Отправить номер"
CONSENT_YES_TEXT   = "✅ Согласен"
CONSENT_NO_TEXT    = "❌ Отказаться"

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
    policy_line = (
        f"\n📄 Полный текст: {PRIVACY_POLICY_URL}\n"
        if PRIVACY_POLICY_URL else "\n"
    )
    return (
        "🔒 Перед подбором тура нужно ваше согласие на обработку персональных данных.\n\n"
        f"Оператор: {DATA_OPERATOR_NAME}.\n\n"
        "Нажимая «Согласен», вы даёте согласие на обработку ваших имени и номера телефона с целью подбора тура и связи с вами (ст. 6, 9 ФЗ-152)."
        + policy_line +
        "Вы вправе отозвать согласие и удалить данные командой /delete."
    )


def _privacy_text() -> str:
    """Short privacy notice for the /privacy command."""
    lines = [
        "🔒 Обработка персональных данных\n",
        f"Оператор: {DATA_OPERATOR_NAME}.",
        "Цель: подбор тура и связь с клиентом.",
        "Обрабатываемые данные: имя, номер телефона, идентификатор Telegram.",
    ]
    if DATA_RETENTION_DAYS > 0:
        lines.append(f"Срок хранения: до {DATA_RETENTION_DAYS} дней после обращения, затем автоматическое удаление.")
    lines.append("Права: отозвать согласие и удалить данные — команда /delete.")
    if PRIVACY_POLICY_URL:
        lines.append(f"\n📄 Полный текст: {PRIVACY_POLICY_URL}")
    return "\n".join(lines)

ADMIN_HELP = (
    "🔧 Команды админа:\n\n"
    "/send {chat_id} {текст} — отправить сообщение пользователю\n"
    "/broadcast {текст} — разослать всем пользователям\n"
    "/users — список пользователей\n"
    "/stats — статистика\n"
    "/restart — сбросить все активные сессии\n"
    "/mdt [test|reload] — статус MDT CRM (test — проверить соединение, reload — обновить страны)\n"
    "/help — эта справка\n\n"
    "Поддерживаются HTML-теги: <b>жирный</b>, <i>курсив</i>\n\n"
    "Пример:\n"
    "/send 123456789 🌴 <b>Подборка туров</b>"
)

# Static text shown when the AI blurb cannot be generated (no key or API error).
AI_FALLBACK_MESSAGE = (
    "🌴 Спасибо за заявку! Наш менеджер подберёт для вас\n"
    "лучшие варианты туров и свяжется с вами в ближайшее время."
)

# Template-based tour blurbs. No external AI is required for this mode,
# so it works regardless of network blocks or API availability.
TEMPLATE_INTROS: Dict[str, str] = {
    "египет": (
        "Вас ждут древние пирамиды, кристально чистое Красное море и «всё включённое» "
        "на любом вкус."
    ),
    "турция": (
        "Отличное сочетание пляжного отдыха, богатой истории и гостеприимной кухни "
        "с all inclusive на побережье."
    ),
    "таиланд": (
        "Экзотическая природа, белоснежные пляжи, буддийские храмы и доступный "
        "комфортный отдых для всей семьи."
    ),
    "мальдивы": (
        "Райские острова с бирюзовой лагуной, bungalow над водой и идеальной "
        "атмосферой для романтического getaway."
    ),
    "оаэ": (
        "Современный комфорт, роскошные отели, шопинг и пляжи с тёплым морем — "
        "и всё это без визового оформления."
    ),
}

TEMPLATE_PACKING = (
    "Возьмите с собой удобную обувь, купальные принадлежности, солнцезащитный крем "
    "и хорошее настроение."
)

# Russian month prefixes for parsing free-text dates into MDT flight dates.
# Longer prefixes are listed before shorter ones to avoid false matches
# (e.g. "март" before "ма").
_MONTHS_RU: Dict[str, int] = {
    "январ":   1, "феврал":  2, "март":   3, "апрел":  4,
    "ма":      5, "июн":    6, "июл":    7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

# Dialog state machine — plain string aliases (kept as strings so the persisted
# JSON state stays compatible without custom (de)serialisation).
STATE_CONSENT     = "consent"
STATE_DESTINATION = "destination"
STATE_DATES       = "dates"
STATE_PEOPLE      = "people"
STATE_BUDGET      = "budget"
STATE_PHONE       = "phone"

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
# MoiDokumenti-Turism (MDT) CRM integration
# ---------------------------------------------------------------------------

def _mdt_base_url() -> str:
    """Build the MDT API base URL."""
    if MDT_BASE_URL:
        return MDT_BASE_URL.rstrip("/")
    if MDT_ACCOUNT:
        return f"https://{MDT_ACCOUNT}.moidokumenti.ru"
    return ""


def _mdt_request(method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Make a POST request to the MDT API."""
    base = _mdt_base_url()
    if not base or not MDT_API_KEY:
        logger.warning("MDT is not configured: missing base URL or API key")
        return None
    url = f"{base}/api/{method}"
    payload = {
        "params": json.dumps(params, ensure_ascii=False),
        "key": MDT_API_KEY,
    }
    try:
        resp = telegram_session.post(url, data=payload, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("MDT request %s failed: %s", method, exc)
        return None


# Country cache: lowercase country name → MDT country ID.
# Populated at startup via _mdt_load_countries() when MDT is enabled.
_mdt_country_cache: Dict[str, int] = {}


def _mdt_load_countries() -> None:
    """Fetch and cache the MDT country list at startup."""
    result = _mdt_request("get-country-list", {})
    if result is None:
        logger.warning("Could not load MDT country list — country matching will be unavailable")
        return
    # The API may return data in different formats; handle them defensively.
    data = result.get("data", result) if isinstance(result, dict) else result
    if isinstance(data, dict):
        # Format: {"id": name, ...} or {"id": {"id": int, "name": str}, ...}
        for key, value in data.items():
            try:
                cid = int(key)
            except (ValueError, TypeError):
                continue
            name = value if isinstance(value, str) else (value.get("name", "") if isinstance(value, dict) else "")
            if name:
                _mdt_country_cache[name.strip().lower()] = cid
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                cid = item.get("id")
                name = item.get("name", "")
                if cid is not None and name:
                    _mdt_country_cache[name.strip().lower()] = int(cid)
    logger.info("Loaded %d countries from MDT", len(_mdt_country_cache))


def _match_country_id(destination: str) -> int:
    """Try to match a destination name to an MDT country ID from the cache.

    Returns 0 if no match is found.
    """
    if not _mdt_country_cache:
        return 0
    dest_lower = destination.strip().lower()
    if not dest_lower:
        return 0
    # Exact match first.
    if dest_lower in _mdt_country_cache:
        return _mdt_country_cache[dest_lower]
    # Partial match: destination contains a country name or vice versa.
    for cached_name, cid in _mdt_country_cache.items():
        if cached_name in dest_lower or dest_lower in cached_name:
            return cid
    return 0


def _parse_russian_dates(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse free-text Russian date ranges into (from, to) in YYYY-MM-DD format.

    Handles patterns like "15-22 июня", "15-22 июня 2026", "15 июня - 22 июля",
    "с 1 по 15 августа".
    Returns (None, None) if parsing fails.
    """
    if not text:
        return None, None

    now = time.localtime()
    current_year = now.tm_year
    current_month = now.tm_mon
    current_day = now.tm_mday

    def _month_from_text(s: str) -> Optional[int]:
        for prefix, month_num in _MONTHS_RU.items():
            if prefix in s.lower():
                return month_num
        return None

    def _to_ymd(day: int, month: int, year: int) -> str:
        return f"{year:04d}-{month:02d}-{day:02d}"

    # Try to extract an explicit year from the full text.
    year_match = re.search(r"\b(20\d{2})\b", text)
    year = int(year_match.group(1)) if year_match else current_year

    # Split on range separators: hyphen, en/em-dash, or the word "по".
    parts = re.split(r"\s*(?:-|–|—|\bпо\b)\s*", text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) < 2:
        return None, None

    def _parse_part(s: str) -> Optional[Tuple[int, int]]:
        day_match = re.search(r"\b(\d{1,2})\b", s)
        if not day_match:
            return None
        day = int(day_match.group(1))
        month = _month_from_text(s)
        if month is None:
            return None
        return (day, month)

    from_parsed = _parse_part(parts[0])
    to_parsed = _parse_part(parts[1])

    # Handle "15-22 июня" where the month is only in the second part.
    if from_parsed is None and to_parsed is not None:
        day_match = re.search(r"\b(\d{1,2})\b", parts[0])
        if day_match:
            from_parsed = (int(day_match.group(1)), to_parsed[1])

    if from_parsed is None or to_parsed is None:
        return None, None

    from_day, from_month = from_parsed
    to_day, to_month = to_parsed

    # If year wasn't explicitly given, adjust: if both month/day are in the past, use next year.
    if not year_match:
        if from_month < current_month or (from_month == current_month and from_day < current_day):
            year = current_year + 1

    from_date = _to_ymd(from_day, from_month, year)
    to_date = _to_ymd(to_day, to_month, year)
    # Handle wrap-around: "15 декабря - 5 января" → to_date is next year.
    if to_month < from_month:
        to_date = _to_ymd(to_day, to_month, year + 1)

    return from_date, to_date


def _mdt_add_tourist_temp(name: str, phone: str) -> Optional[int]:
    """Create a temporary tourist in MDT and return its ID."""
    params = {
        "name": name,
        "tel": phone,
        "tags": "Telegram Bot",
    }
    result = _mdt_request("add-tourist-temp", params)
    if result is None:
        return None
    # Try to extract the ID from the response.
    data = result.get("data", result) if isinstance(result, dict) else result
    if isinstance(data, dict):
        tid = data.get("id") or data.get("tourist_id")
        if tid is not None:
            return int(tid)
    if isinstance(data, (int, str)):
        try:
            return int(data)
        except (ValueError, TypeError):
            pass
    logger.warning("Could not extract tourist ID from add-tourist-temp response: %s", result)
    return None


def send_preorder_to_mdt(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> Tuple[Optional[int], Optional[int]]:
    """Create a preorder (обращение) in MDT CRM from a completed bot request.

    Flow: add-tourist-temp → create-preorder.
    Returns (preorder_id, tourist_id) on success, (None, None) on failure.
    """
    name = client_name or f"Telegram {chat_id}"

    # Step 1: create a temp tourist.
    tourist_id = _mdt_add_tourist_temp(name, phone)
    if tourist_id is None:
        logger.warning("Failed to create temp tourist in MDT for chat %s", chat_id)
        return None, None

    # Step 2: create the preorder.
    country_id = _match_country_id(info.get("destination", ""))
    date_from, date_to = _parse_russian_dates(info.get("dates", ""))

    # Parse people count.
    people_str = str(info.get("people", ""))
    persons = 0
    cleaned = re.sub(r"[^\d]", "", people_str)
    if cleaned:
        persons = int(cleaned)

    # Parse budget.
    budget = info.get("budget", 0)
    if not isinstance(budget, (int, float)):
        try:
            budget = int(re.sub(r"[^\d]", "", str(budget)))
        except (ValueError, TypeError):
            budget = 0

    # Build comment with all the raw info.
    comment_parts = []
    if info.get("destination"):
        comment_parts.append(f"Направление: {info['destination']}")
    if info.get("dates"):
        comment_parts.append(f"Даты: {info['dates']}")
    if info.get("people"):
        comment_parts.append(f"Человек: {info['people']}")
    if budget:
        comment_parts.append(f"Бюджет: {budget}₽")
    comment = " | ".join(comment_parts)

    params: Dict[str, Any] = {
        "tourist_type": "tourist_temp",
        "tourist_id": tourist_id,
        "country_id1": country_id,
        "country_id2": 0,
        "country_id3": 0,
        "persons": persons,
        "children": 0,
        "children_ages": [],
        "price_from": 0,
        "price_to": budget,
        "comment": comment,
        "wait_for_hot": 0,
    }
    if date_from:
        params["flightdate_from"] = date_from
    if date_to:
        params["flightdate_to"] = date_to

    result = _mdt_request("create-preorder", params)
    if result is not None:
        data = result.get("data", result) if isinstance(result, dict) else result
        preorder_id = None
        if isinstance(data, dict):
            preorder_id = data.get("id") or data.get("preorder_id")
        elif isinstance(data, (int, str)):
            try:
                preorder_id = int(data)
            except (ValueError, TypeError):
                pass
        logger.info("Preorder created in MDT for chat %s (ID: %s, tourist: %s)", chat_id, preorder_id, tourist_id)
        return preorder_id, tourist_id
    else:
        logger.warning("Failed to create preorder in MDT for chat %s", chat_id)
        return None, None


def _mdt_notify_managers(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> None:
    """Send a push notification to MDT managers about a new lead."""
    if not MDT_MANAGER_IDS:
        logger.warning("MDT_NOTIFY_MANAGERS is on but MDT_MANAGER_IDS is empty")
        return

    title = "Новая заявка с Telegram-бота"
    text_parts = []
    if client_name:
        text_parts.append(f"Клиент: {client_name}")
    if info.get("destination"):
        text_parts.append(f"Направление: {info['destination']}")
    if info.get("dates"):
        text_parts.append(f"Даты: {info['dates']}")
    if info.get("people"):
        text_parts.append(f"Человек: {info['people']}")
    if info.get("budget"):
        text_parts.append(f"Бюджет: {info['budget']}₽")
    text_parts.append(f"Телефон: {phone}")
    text = "\n".join(text_parts)

    params = {
        "manager_ids": MDT_MANAGER_IDS,
        "title": title,
        "text": text,
    }
    result = _mdt_request("send-push", params)
    if result is not None:
        logger.info("Push notification sent to MDT managers for chat %s", chat_id)
    else:
        logger.warning("Failed to send push notification to MDT managers for chat %s", chat_id)


def _mdt_add_reminder(
    preorder_id: int,
    tourist_id: int,
    manager_id: int,
    reminder_date: str,
    reminder_time: str = "10:00:00",
) -> bool:
    """Create a manager reminder/task in MDT CRM via /api/add-reminder."""
    params: Dict[str, Any] = {
        "date": reminder_date,
        "time": reminder_time,
        "text": MDT_REMINDER_TEXT,
        "tourist_type": "tourist_temp",
        "tourist_id": tourist_id,
        "manager_id": manager_id,
        "preorder_id": preorder_id,
        "only_one_manager": False,
    }
    result = _mdt_request("add-reminder", params)
    if result is not None:
        logger.info("MDT reminder created for manager %s (preorder %s)", manager_id, preorder_id)
        return True
    logger.warning("Failed to create MDT reminder for manager %s (preorder %s)", manager_id, preorder_id)
    return False


def _mdt_create_reminders_for_preorder(
    chat_id: int,
    preorder_id: Optional[int],
    tourist_id: Optional[int],
) -> None:
    """Create follow-up reminders in MDT for each configured manager."""
    if not MDT_REMINDER_ENABLED:
        return
    if not (preorder_id and tourist_id):
        logger.debug("Skipping MDT reminder: missing preorder_id or tourist_id")
        return
    if not MDT_MANAGER_IDS:
        logger.warning("MDT_REMINDER_ENABLED is on but MDT_MANAGER_IDS is empty")
        return

    reminder_date = (datetime.now() + timedelta(days=MDT_REMINDER_DAYS)).strftime("%Y-%m-%d")
    for manager_id in MDT_MANAGER_IDS:
        _mdt_add_reminder(preorder_id, tourist_id, manager_id, reminder_date)


def _mdt_create_lead(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> bool:
    """Create a lead in MDT CRM via /api/add-lead. Returns True on success."""
    fields = []
    if info.get("destination"):
        fields.append({"name": "Направление", "values": [info["destination"]]})
    if info.get("dates"):
        fields.append({"name": "Даты", "values": [info["dates"]]})
    if info.get("people"):
        fields.append({"name": "Количество человек", "values": [str(info["people"])]})
    if info.get("budget"):
        fields.append({"name": "Бюджет", "values": [str(info["budget"])]})

    params = {
        "name": client_name or f"Telegram {chat_id}",
        "phone": phone,
        "email": "",
        "source": MDT_SOURCE,
        "fields": fields,
    }
    result = _mdt_request("add-lead", params)
    if result is not None:
        logger.info("Lead sent to MDT for chat %s", chat_id)
        return True
    else:
        logger.warning("Failed to send lead to MDT for chat %s", chat_id)
        return False


def send_lead_to_mdt(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> None:
    """Dispatch a completed request to MDT CRM based on MDT_MODE.

    Modes:
      - "lead":     create a lead only (via /api/add-lead)
      - "preorder": create a temp tourist + preorder only (via /api/create-preorder)
      - "both":     create both a lead and a preorder

    After successful creation, if MDT_NOTIFY_MANAGERS is enabled, sends a push
    notification to the configured manager IDs (via /api/send-push).
    """
    if not MDT_ENABLED:
        return

    success = False

    if MDT_MODE in ("lead", "both"):
        success = _mdt_create_lead(chat_id, info, phone, client_name) or success

    preorder_id: Optional[int] = None
    tourist_id: Optional[int] = None

    if MDT_MODE in ("preorder", "both"):
        preorder_id, tourist_id = send_preorder_to_mdt(chat_id, info, phone, client_name)
        success = (preorder_id is not None) or success
        _mdt_create_reminders_for_preorder(chat_id, preorder_id, tourist_id)

    if success and MDT_NOTIFY_MANAGERS:
        _mdt_notify_managers(chat_id, info, phone, client_name)


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

# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def validate_phone(text: str) -> Tuple[bool, Optional[str]]:
    """Validate a Russian phone number. Returns (ok, normalised)."""
    digits = re.sub(r"[^\d+]", "", text).lstrip("+")
    if digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return True, "+" + digits
    if len(digits) == 10:
        return True, "+7" + digits
    return False, None


def validate_people(text: str) -> Tuple[bool, Optional[str]]:
    """Validate number of travellers. Returns (ok, value_str)."""
    cleaned = text.strip()
    if cleaned == "5+":
        return True, "5+"
    try:
        value = int(re.sub(r"[^\d]", "", cleaned))
        if 1 <= value <= 50:
            return True, str(value)
    except (ValueError, TypeError):
        pass
    return False, None


def validate_budget(text: str) -> Tuple[bool, Optional[int]]:
    """Parse budget as a positive integer. Returns (ok, value)."""
    try:
        value = int(re.sub(r"[^\d]", "", text))
        if value > 0:
            return True, value
    except (ValueError, TypeError):
        pass
    return False, None



def _template_selection(destination: str, dates: str, people: str, budget: str) -> str:
    """Generate a tour blurb from templates (no external AI required)."""
    dest_lower = destination.lower()
    intro: Optional[str] = None
    for keyword, text in TEMPLATE_INTROS.items():
        if keyword in dest_lower:
            intro = text
            break
    if intro is None:
        intro = f"{destination} — отличное направление для вашего отдыха."

    return (
        "🌴 Ваша подборка туров\n\n"
        f"📍 {destination}: {intro}\n\n"
        f"Поездка на {dates} для {people} человек — хороший выбор, "
        f"чтобы успеть всё и при этом отдохнуть. Бюджет {budget}₽ на человека "
        f"позволяет подобрать комфортный вариант.\n\n"
        f"💡 {TEMPLATE_PACKING}\n\n"
        "ℹ️ Наш менеджер скоро свяжется с вами для уточнения деталей!"
    )


def generate_ai_selection(destination: str, dates: str, people: str, budget: str) -> str:
    """Generate an AI tour blurb for the client."""
    if AI_MODE == "template":
        logger.info("Template selection generated for '%s'", destination)
        return _template_selection(destination, dates, people, budget)

    if not groq_client:
        logger.warning("Groq client unavailable — using template fallback")
        return _template_selection(destination, dates, people, budget)

    try:
        prompt = (
            "Ты — эксперт по туризму туристического агентства «АПРЕЛЬ тур».\n\n"
            "Клиент хочет:\n"
            f"- Направление: {destination}\n"
            f"- Даты: {dates}\n"
            f"- Количество человек: {people}\n"
            f"- Бюджет: {budget} рублей\n\n"
            "Напиши короткое (3-4 предложения), дружелюбное сообщение с:\n"
            "- Что ожидает в этом направлении\n"
            "- Почему это отличный выбор\n"
            "- Что взять с собой\n\n"
            "Используй эмодзи. Не упоминай цены и конкретные отели."
        )
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.7,
        )
        ai_text = response.choices[0].message.content
        logger.info("AI selection generated for '%s'", destination)
        return (
            "🌴 Ваша подборка туров\n\n"
            f"{ai_text}\n\n"
            "ℹ️ Наш менеджер скоро свяжется с вами для уточнения деталей!"
        )
    except Exception as exc:
        logger.error("Error generating AI selection: %s", exc)
        return _template_selection(destination, dates, people, budget)

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
    send_message(
        chat_id,
        f"📊 Статистика:\n\nПользователей: {total}\nАктивных заявок: {active}",
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


def _admin_broadcast(chat_id: int, arg: str) -> bool:
    """`/broadcast {текст}` — send a message to every known user."""
    if not arg.strip():
        send_message(chat_id, "Использование: /broadcast {текст}")
        return True
    count = 0
    with _lock:
        recipients = list(all_users.keys())
    for uid in recipients:
        if uid == ADMIN_ID:
            continue
        if send_message(uid, arg, parse_mode="HTML"):
            count += 1
        time.sleep(BROADCAST_DELAY)
    send_message(chat_id, f"✅ Рассылка отправлена {count} пользователям")
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


# command -> handler(chat_id, arg). Each handler returns True (recognised).
ADMIN_COMMANDS: Dict[str, Callable[[int, str], bool]] = {
    "/help":      _admin_help,
    "/users":     _admin_users,
    "/stats":     _admin_stats,
    "/restart":   _admin_restart,
    "/send":      _admin_send,
    "/broadcast": _admin_broadcast,
    "/mdt":       _admin_mdt,
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
        f"От: {client_name or 'без имени'} (ID: {chat_id})\n"
        f"📍 {info.get('destination', '?')}\n"
        f"📅 {info.get('dates', '?')}\n"
        f"👥 {info.get('people', '?')} чел\n"
        f"💰 {info.get('budget', '?')}₽\n"
        f"📱 {phone}\n\n"
        f"Ответить: /send {chat_id} ваше сообщение",
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


def handle_completion(chat_id: int, phone: str, message: Dict[str, Any]) -> None:
    """Finalise the request: confirm, notify admin, AI blurb."""
    info = dict(user_data.get(chat_id, {}))
    from_info = message.get("from", {})
    first_name = from_info.get("first_name", "")
    username = from_info.get("username", "")
    client_name = first_name or (f"@{username}" if username else None)

    _confirm_to_user(chat_id, info, phone)            # 1. Confirm to user
    _notify_admin(chat_id, info, phone, client_name)  # 2. Notify admin
    send_lead_to_mdt(chat_id, info, phone, client_name)  # 3. Send to MDT CRM (lead/preorder/both)
    _send_ai_blurb(chat_id, info)                     # 4. AI selection (with typing)
    with _lock:                                       # 5. Clean up
        user_data.pop(chat_id, None)
    delete_session(chat_id)

# ---------------------------------------------------------------------------
# Flask app & routes
# ---------------------------------------------------------------------------

app = Flask(__name__)


@app.route("/")
def index() -> str:
    return "TurBot is running!"


@app.route("/health")
def health() -> Any:
    """Simple health/readiness endpoint for monitoring."""
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
        "privacy_policy_configured": bool(PRIVACY_POLICY_URL),
        "data_retention_days": DATA_RETENTION_DAYS,
    })


def _process_update(data: Dict[str, Any]) -> None:
    """Parse one Telegram update and route it to the right handler."""
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
