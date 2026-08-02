"""TurBot VK — бот турагентства «АПРЕЛЬ тур» для VK.com.

Самодостаточный Flask-webhook для группы ВКонтакте. Паритет с bot.py:
soft/strict согласие, кнопки на всех шагах (даты, бюджет, люди),
связь VK / телефон / Telegram, лиды в Telegram админу, MDT CRM.

Деплой: отдельный процесс (см. deploy/vk-turbot.service).
"""
from __future__ import annotations

import os
import json
import time
import random
import sqlite3
import logging
import threading
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from flask import Flask, request, jsonify
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from groq import Groq
except ImportError:  # groq may not be installed in all environments
    Groq = None  # type: ignore

from shared.constants import (
    STATE_BUDGET,
    STATE_CONSENT,
    STATE_CONTACT,
    STATE_DATES,
    STATE_DESTINATION,
    STATE_ORIGIN,
    STATE_PEOPLE,
    STATE_PHONE,
    PEOPLE_OPTIONS,
    BACK_BUTTON_TEXT,
    CANCEL_BUTTON_TEXT,
    CONSENT_YES_TEXT,
    CONSENT_NO_TEXT,
    START_BUTTON_TEXT,
    CONTACT_TG_TEXT,
    CONTACT_PHONE_TEXT,
    CONTACT_VK_TEXT,
    POPULAR_DESTINATIONS_PLAIN,
    ORIGIN_OPTIONS_PLAIN,
)
from shared import tutu as _tutu
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
logger = logging.getLogger("turbot-vk")

VK_ACCESS_TOKEN      = os.getenv("VK_ACCESS_TOKEN", "")
VK_GROUP_ID          = int(os.getenv("VK_GROUP_ID", "0"))
VK_CONFIRMATION      = os.getenv("VK_CONFIRMATION", "")
VK_API_VERSION       = os.getenv("VK_API_VERSION", "5.199")
VK_SECRET_KEY        = os.getenv("VK_SECRET_KEY", "")  # optional callback secret
VK_API_BASE          = "https://api.vk.com/method/"

GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL        = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
AI_MODE           = os.getenv("AI_MODE", "template").lower().strip()
PORT              = int(os.getenv("VK_PORT", os.getenv("PORT", "5100")))
DATABASE_PATH     = os.getenv("VK_DATABASE_PATH", os.getenv("DATABASE_PATH", "vk_bot_state.sqlite"))
ADMIN_ID          = int(os.getenv("ADMIN_ID", "0"))
DIALOG_TIMEOUT_HOURS = int(os.getenv("DIALOG_TIMEOUT_HOURS", "6"))
HTTP_TIMEOUT      = 15


def _parse_chat_ids(raw: str) -> List[int]:
    """Parse comma-separated chat IDs; skip empty/invalid parts."""
    ids: List[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            logger.warning("Invalid chat id in LEAD_NOTIFY_IDS: %r", part)
    return ids


# Who receives new leads in Telegram (admin bot). LEAD_NOTIFY_IDS or ADMIN_ID.
_lead_notify_raw = os.getenv("LEAD_NOTIFY_IDS", "").strip()
if _lead_notify_raw:
    LEAD_NOTIFY_IDS: List[int] = list(dict.fromkeys(_parse_chat_ids(_lead_notify_raw)))
elif ADMIN_ID:
    LEAD_NOTIFY_IDS = [ADMIN_ID]
else:
    LEAD_NOTIFY_IDS = []

# MDT CRM (same env vars as Telegram bot)
MDT_ENABLED    = os.getenv("MDT_ENABLED", "false").lower().strip() in ("1", "true", "yes")
MDT_ACCOUNT    = os.getenv("MDT_ACCOUNT", "")
MDT_API_KEY    = os.getenv("MDT_API_KEY", "")
MDT_SOURCE     = os.getenv("MDT_SOURCE", "VK Bot")
MDT_BASE_URL   = os.getenv("MDT_BASE_URL", "")
MDT_MODE       = os.getenv("MDT_MODE", "lead").lower().strip()
MDT_NOTIFY_MANAGERS = os.getenv("MDT_NOTIFY_MANAGERS", "false").lower().strip() in ("1", "true", "yes")
MDT_MANAGER_IDS = [int(x.strip()) for x in os.getenv("MDT_MANAGER_IDS", "").split(",") if x.strip()]
MDT_REMINDER_ENABLED = os.getenv("MDT_REMINDER_ENABLED", "true").lower().strip() in ("1", "true", "yes")
try:
    MDT_REMINDER_DAYS = int(os.getenv("MDT_REMINDER_DAYS", "1"))
except (ValueError, TypeError):
    MDT_REMINDER_DAYS = 1
MDT_REMINDER_TEXT = os.getenv("MDT_REMINDER_TEXT", "Позвонить по заявке с VK-бота")

if MDT_MODE not in ("lead", "preorder", "both"):
    logger.warning("MDT_MODE '%s' is unknown, defaulting to 'lead'", MDT_MODE)
    MDT_MODE = "lead"

# 152-ФЗ compliance
PRIVACY_POLICY_URL = os.getenv("PRIVACY_POLICY_URL", "").strip()
DATA_OPERATOR_NAME = os.getenv(
    "DATA_OPERATOR_NAME",
    "ИП Замятина Мария Андреевна (ТА «АПРЕЛЬ тур», ОГРНИП 290211659807)",
)
DATA_RETENTION_DAYS = int(os.getenv("DATA_RETENTION_DAYS", "180"))
# soft (default): short notice + «Начать», flexible contact (VK/phone/TG).
# strict: classic «Согласен / Отказаться».
CONSENT_MODE = os.getenv("CONSENT_MODE", "soft").lower().strip()

# --- Demo mode --------------------------------------------------------------
# VK_DEMO_MODE overrides DEMO_MODE for this bot alone. The two bots share one
# .env, and they are not always used the same way: the Telegram instance can be
# a portfolio showcase while VK takes real enquiries for the agency.
_demo_default = os.getenv("DEMO_MODE", "false")
DEMO_MODE = os.getenv("VK_DEMO_MODE", _demo_default).lower().strip() in ("1", "true", "yes")

DEMO_NOTICE = (
    "⚠️ Это демонстрационная версия.\n"
    "Заявка не попадёт в турагентство, телефон не сохраняется — "
    "вводите любой номер вида +79001234567.\n"
    "Цены на перелёт при этом настоящие: они приходят из Tutu.ru."
)

# --- Tutu.ru MCP ------------------------------------------------------------
TUTU_ENABLED = os.getenv("TUTU_ENABLED", "true").lower().strip() in ("1", "true", "yes")
TUTU_ENDPOINT = os.getenv("TUTU_ENDPOINT", "https://mcp.tutu.ru/mcp").strip()
TUTU_TIMEOUT = int(os.getenv("TUTU_TIMEOUT", "12"))
TUTU_DEFAULT_ORIGIN = os.getenv("TUTU_DEFAULT_ORIGIN", "Архангельск").strip()
TUTU_MAX_OFFERS = int(os.getenv("TUTU_MAX_OFFERS", "3"))
TUTU_CACHE_TTL = int(os.getenv("TUTU_CACHE_TTL", "900"))
TUTU_SHOW_CLIENT = os.getenv("TUTU_SHOW_CLIENT", "true").lower().strip() in ("1", "true", "yes")
TUTU_SHOW_ADMIN = os.getenv("TUTU_SHOW_ADMIN", "true").lower().strip() in ("1", "true", "yes")


def _tutu_settings() -> "_tutu.TutuSettings":
    return _tutu.TutuSettings(
        enabled=TUTU_ENABLED, endpoint=TUTU_ENDPOINT, timeout=TUTU_TIMEOUT,
        default_origin=TUTU_DEFAULT_ORIGIN, max_offers=TUTU_MAX_OFFERS,
        cache_ttl=TUTU_CACHE_TTL, show_client=TUTU_SHOW_CLIENT,
        show_admin=TUTU_SHOW_ADMIN,
    )
if CONSENT_MODE not in ("soft", "strict"):
    CONSENT_MODE = "soft"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POPULAR_DESTINATIONS = POPULAR_DESTINATIONS_PLAIN

# Quick picks (label on keyboard → value stored in lead). VK label ≤ 40 chars.
DATE_PRESETS: List[Tuple[str, str]] = [
    ("🏖 Выходные", "ближайшие выходные"),
    ("📅 1–2 недели", "через 1-2 недели"),
    ("🗓 Через месяц", "через месяц"),
    ("☀️ Лето", "лето"),
    ("❄️ Зима", "зима"),
    ("🤷 Даты гибкие", "даты гибкие"),
]
BUDGET_PRESETS: List[Tuple[str, int]] = [
    ("до 40 000 ₽", 40000),
    ("60 000 ₽", 60000),
    ("80 000 ₽", 80000),
    ("100 000 ₽", 100000),
    ("150 000 ₽", 150000),
    ("200 000+ ₽", 200000),
]
DATE_CUSTOM_LABEL = "✏️ Свои даты"
BUDGET_CUSTOM_LABEL = "✏️ Свой бюджет"
CONTACT_VK_CHAT_LABEL = "💙 VK (этот чат)"

USER_HELP = (
    "🌴 «АПРЕЛЬ тур» — подбор отдыха\n\n"
    "Соберу короткую заявку и передам менеджеру. Можно почти всё кнопками.\n\n"
    "Команды:\n"
    "  Начать — подбор тура\n"
    "  Отмена — отменить заявку\n"
    "  Политика — персональные данные\n"
    "  Удалить — стереть мои данные\n"
    "  Помощь — эта справка\n\n"
    "Связь: VK / телефон / Telegram — на выбор.\n\n"
    "📋 ИП Замятина Мария Андреевна\n"
    "ТА «АПРЕЛЬ тур» · ОГРНИП 290211659807"
)

WELCOME_BODY = (
    "Подберём тур под даты и бюджет — заявка уйдёт менеджеру.\n\n"
    "Как это работает:\n"
    "1) несколько вопросов (можно кнопками)\n"
    "2) удобный способ связи: VK, телефон или Telegram\n"
    "3) менеджер напишет или позвонит\n\n"
    "Около минуты. Данные — только чтобы связаться (Политика).\n"
    "Жмите кнопку ниже 👇"
)

HINT_START = "Чтобы подобрать тур, напишите «Начать» или нажмите кнопку.\nСправка — «Помощь»."

# ---------------------------------------------------------------------------
# Groq client
# ---------------------------------------------------------------------------

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY and Groq else None

# ---------------------------------------------------------------------------
# Shared HTTP session
# ---------------------------------------------------------------------------

def _create_http_session() -> requests.Session:
    retry = Retry(
        total=3, backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=["POST", "GET"], raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

http_session = _create_http_session()

# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

_db_lock = threading.Lock()
_lock = threading.Lock()

user_data: Dict[int, Dict[str, Any]] = {}
all_users: Dict[int, Dict[str, Any]] = {}
_dirty_sessions: set[int] = set()
_dirty_users: set[int] = set()


def _mark_dirty(chat_id: int, *, session: bool = True, user: bool = True) -> None:
    with _lock:
        if session:
            _dirty_sessions.add(chat_id)
        if user:
            _dirty_users.add(chat_id)


@contextmanager
def _db_cursor(commit: bool = False):
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False, timeout=5)
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
    with _db_cursor(commit=True) as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                last_seen INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                consent_at INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                chat_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                destination TEXT,
                origin TEXT,
                dates TEXT,
                people TEXT,
                budget INTEGER,
                phone TEXT,
                updated_at INTEGER NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                first_name TEXT,
                username TEXT,
                destination TEXT,
                origin TEXT,
                dates TEXT,
                people TEXT,
                budget INTEGER,
                phone TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        # Additive migration for databases created before the origin step.
        for _t in ("sessions", "leads"):
            cur.execute(f"PRAGMA table_info({_t})")
            if "origin" not in {r[1] for r in cur.fetchall()}:
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN origin TEXT")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_chat_id ON leads(chat_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at)")
        cur.execute("PRAGMA journal_mode=WAL")


# --- session helpers ---

def set_session(chat_id: int, data: Dict[str, Any]) -> None:
    now = int(time.time())
    with _db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO sessions (chat_id, state, destination, origin, dates, people, budget, phone, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                state=excluded.state, destination=excluded.destination,
                origin=excluded.origin,
                dates=excluded.dates, people=excluded.people,
                budget=excluded.budget, phone=excluded.phone,
                updated_at=excluded.updated_at
        """, (chat_id, data.get("state", ""), data.get("destination"),
              data.get("origin"),
              data.get("dates"), data.get("people"), data.get("budget"),
              data.get("phone"), data.get("updated_at", now)))


def get_session(chat_id: int) -> Optional[Dict[str, Any]]:
    with _db_cursor() as cur:
        cur.execute("SELECT * FROM sessions WHERE chat_id = ?", (chat_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def delete_session(chat_id: int) -> None:
    with _db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM sessions WHERE chat_id = ?", (chat_id,))


def list_stale_sessions(cutoff: int) -> List[int]:
    with _db_cursor() as cur:
        cur.execute("SELECT chat_id FROM sessions WHERE updated_at < ?", (cutoff,))
        return [row[0] for row in cur.fetchall()]


def _tutu_mask_phone(phone: str) -> str:
    """Keep the shape of a number without keeping the number."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) < 8:
        return "+7***"
    return f"+{digits[:4]}***{digits[-4:]}"


def save_lead(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    first_name: str = "",
    username: str = "",
) -> None:
    """Persist a completed tour request for retention-aware export/history."""
    now = int(time.time())
    if DEMO_MODE:
        phone = _tutu_mask_phone(phone)
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO leads (
                chat_id, first_name, username, destination, origin, dates,
                people, budget, phone, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                first_name or None,
                username or None,
                info.get("destination"),
                info.get("origin"),
                info.get("dates"),
                info.get("people"),
                info.get("budget"),
                phone,
                now,
            ),
        )


# --- user helpers ---

def touch_user(chat_id: int, first_name: str, username: str = "",
               last_seen: Optional[int] = None) -> None:
    now = last_seen if last_seen is not None else int(time.time())
    with _db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO users (chat_id, first_name, username, last_seen, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                first_name=excluded.first_name, username=excluded.username,
                last_seen=excluded.last_seen, updated_at=excluded.updated_at
        """, (chat_id, first_name, username, now, now, now))


def get_user(chat_id: int) -> Optional[Dict[str, Any]]:
    with _db_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
        row = cur.fetchone()
        return dict(row) if row else None


# --- consent & erasure (152-ФЗ) ---

def has_consent(chat_id: int) -> bool:
    with _lock:
        meta = all_users.get(chat_id)
        if meta is not None:
            return bool(meta.get("consent_at"))
    user = get_user(chat_id)
    return bool(user and user.get("consent_at"))


def set_consent(chat_id: int) -> None:
    now = int(time.time())
    with _lock:
        meta = all_users.setdefault(chat_id, {})
        meta["consent_at"] = now
        first_name = meta.get("first_name", "")
    with _db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO users (chat_id, first_name, username, last_seen, created_at, updated_at, consent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET consent_at=excluded.consent_at
        """, (chat_id, first_name, "", now, now, now, now))


def delete_user_data(chat_id: int) -> None:
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
    if DATA_RETENTION_DAYS <= 0:
        return 0
    cutoff = int(time.time()) - DATA_RETENTION_DAYS * 86400
    with _db_cursor() as cur:
        cur.execute("SELECT chat_id FROM users WHERE last_seen < ? AND chat_id != ?",
                     (cutoff, ADMIN_ID))
        expired = [row[0] for row in cur.fetchall()]
    for cid in expired:
        delete_user_data(cid)
    if expired:
        logger.info("Retention cleanup erased %d expired user(s)", len(expired))
    return len(expired)


# --- stale-dialog cleanup ---

def _cancel_stale_session(chat_id: int) -> None:
    with _lock:
        user_data.pop(chat_id, None)
    delete_session(chat_id)
    send_message(chat_id,
        "⏰ Вы долго не отвечали, поэтому заявка отменена.\n\n"
        "Чтобы начать заново — напишите «Начать».",
        keyboard=_hide_keyboard())


def _cleanup_stale_dialogs() -> None:
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
    if DIALOG_TIMEOUT_HOURS <= 0:
        return
    def _worker():
        while True:
            time.sleep(60)
            try:
                _cleanup_stale_dialogs()
            except Exception as exc:
                logger.error("Error in timeout worker: %s", exc)
    threading.Thread(target=_worker, daemon=True, name="vk-dialog-timeout").start()
    logger.info("Dialog timeout worker started (%s hours)", DIALOG_TIMEOUT_HOURS)


def _start_retention_worker() -> None:
    if DATA_RETENTION_DAYS <= 0:
        return
    def _worker():
        while True:
            try:
                cleanup_expired_data()
            except Exception as exc:
                logger.error("Error in retention worker: %s", exc)
            time.sleep(6 * 3600)
    threading.Thread(target=_worker, daemon=True, name="vk-data-retention").start()
    logger.info("Data retention worker started (%s days)", DATA_RETENTION_DAYS)


# ---------------------------------------------------------------------------
# VK API helpers
# ---------------------------------------------------------------------------

def _vk_api(method: str, **params: Any) -> Optional[Dict[str, Any]]:
    """Call a VK API method. Returns response['response'] or None on error."""
    if not VK_ACCESS_TOKEN:
        logger.error("VK_ACCESS_TOKEN not set — cannot call API")
        return None
    params["access_token"] = VK_ACCESS_TOKEN
    params["v"] = VK_API_VERSION
    try:
        resp = http_session.post(VK_API_BASE + method, data=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            err = data["error"]
            logger.error("VK API %s error %s: %s", method, err.get("error_code"), err.get("error_msg"))
            return None
        return data.get("response")
    except Exception as exc:
        logger.error("VK API %s failed: %s", method, exc)
        return None


def send_message(
    user_id: int,
    text: str,
    keyboard: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Send a text message via VK messages.send."""
    if not VK_ACCESS_TOKEN:
        logger.error("VK_ACCESS_TOKEN not set — cannot send message")
        return None
    params: Dict[str, Any] = {
        "user_id": user_id,
        "message": text,
        "random_id": random.randint(0, 2**31),
    }
    if keyboard:
        params["keyboard"] = keyboard
    return _vk_api("messages.send", **params)


def send_typing(user_id: int) -> None:
    """Send 'typing' indicator via VK messages.setActivity."""
    if not VK_ACCESS_TOKEN:
        return
    try:
        _vk_api("messages.setActivity", user_id=user_id, type="typing")
    except Exception:
        pass


def get_user_name(user_id: int) -> str:
    """Fetch first_name + last_name from VK users.get. Falls back to ID."""
    result = _vk_api("users.get", user_ids=str(user_id), fields="first_name,last_name")
    if result and isinstance(result, list) and result:
        first = result[0].get("first_name", "")
        last = result[0].get("last_name", "")
        name = f"{first} {last}".strip()
        return name or f"VK user {user_id}"
    return f"VK user {user_id}"


# ---------------------------------------------------------------------------
# VK keyboards
# ---------------------------------------------------------------------------

def _keyboard(rows: List[List[Dict[str, Any]]], one_time: bool = False) -> str:
    """Build a VK Keyboard JSON string."""
    return json.dumps({
        "one_time": one_time,
        "inline": False,
        "buttons": rows,
    })


def _btn(label: str, color: str = "secondary", payload: Optional[Dict] = None) -> Dict[str, Any]:
    """Create a single VK keyboard button."""
    action: Dict[str, Any] = {"type": "text", "label": label}
    if payload:
        action["payload"] = json.dumps(payload, ensure_ascii=False)
    return {"action": action, "color": color}


def _chunk_buttons(labels: List[str], color: str = "primary", per_row: int = 2) -> List[List[Dict[str, Any]]]:
    rows: List[List[Dict[str, Any]]] = []
    row: List[Dict[str, Any]] = []
    for label in labels:
        row.append(_btn(label, color))
        if len(row) >= per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def _dest_keyboard() -> str:
    rows = _chunk_buttons(list(POPULAR_DESTINATIONS), "primary", 2)
    rows.append([_btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _nav_keyboard(extra_top: Optional[List[Dict]] = None) -> str:
    rows: List[List[Dict[str, Any]]] = []
    if extra_top:
        rows.append(extra_top)
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary")])
    rows.append([_btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _dates_keyboard() -> str:
    labels = [label for label, _ in DATE_PRESETS] + [DATE_CUSTOM_LABEL]
    rows = _chunk_buttons(labels, "primary", 2)
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary")])
    rows.append([_btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _people_keyboard() -> str:
    rows = [[_btn(p, "primary") for p in PEOPLE_OPTIONS]]
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary")])
    rows.append([_btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _budget_keyboard() -> str:
    labels = [label for label, _ in BUDGET_PRESETS] + [BUDGET_CUSTOM_LABEL]
    rows = _chunk_buttons(labels, "primary", 2)
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary")])
    rows.append([_btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _contact_keyboard() -> str:
    return _keyboard([
        [_btn(CONTACT_VK_CHAT_LABEL, "positive")],
        [_btn(CONTACT_PHONE_TEXT, "primary")],
        [_btn(CONTACT_TG_TEXT, "primary")],
        [_btn(BACK_BUTTON_TEXT, "secondary")],
        [_btn(CANCEL_BUTTON_TEXT, "negative")],
    ])


def _consent_keyboard() -> str:
    return _keyboard([
        [_btn(CONSENT_YES_TEXT, "positive")],
        [_btn(CONSENT_NO_TEXT, "negative")],
    ])


def _soft_start_keyboard() -> str:
    return _keyboard([[_btn(START_BUTTON_TEXT, "positive")]])


def _hide_keyboard() -> str:
    return _keyboard([], one_time=True)


def generate_ai_selection(destination: str, dates: str, people: str, budget: str) -> str:
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
# MDT CRM integration (thin wrappers over shared.mdt)
# ---------------------------------------------------------------------------

_mdt_country_cache: Dict[str, int] = {}


def _mdt_settings() -> mdt_shared.MDTSettings:
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
        name_prefix="VK",
        tourist_tags="VK Bot",
        push_title="Новая заявка с VK-бота",
    )


def _mdt_base_url() -> str:
    return mdt_shared.base_url(_mdt_settings())


def _mdt_request(method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return mdt_shared.http_request(
        _mdt_settings(), http_session, method, params, log=logger
    )


def _mdt_load_countries() -> None:
    result = _mdt_request("get-country-list", {})
    if result is None:
        return
    _mdt_country_cache.clear()
    _mdt_country_cache.update(mdt_shared.parse_country_list(result))
    logger.info("Loaded %d countries from MDT", len(_mdt_country_cache))


def _match_country_id(destination: str) -> int:
    return mdt_shared.match_country_id(_mdt_country_cache, destination)


def send_preorder_to_mdt(chat_id, info, phone, client_name) -> Tuple[Optional[int], Optional[int]]:
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


def send_lead_to_mdt(chat_id, info, phone, client_name) -> None:
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


def _consent_text() -> str:
    return _shared_consent_text(
        DATA_OPERATOR_NAME,
        privacy_policy_url=PRIVACY_POLICY_URL,
        erase_hint="— напишите «Удалить»",
    )


def _privacy_text() -> str:
    return _shared_privacy_text(
        DATA_OPERATOR_NAME,
        platform_id_label="VK",
        privacy_policy_url=PRIVACY_POLICY_URL,
        retention_days=DATA_RETENTION_DAYS,
        erase_hint="напишите «Удалить»",
    )


# ---------------------------------------------------------------------------
# Dialog handlers
# ---------------------------------------------------------------------------

def _welcome_text(first_name: str = "") -> str:
    if first_name:
        head = f"🌴 Добро пожаловать, {first_name}!"
    else:
        head = "🌴 Добро пожаловать в «АПРЕЛЬ тур»!"
    if DEMO_MODE:
        return f"{head}\n\n{DEMO_NOTICE}\n\n{WELCOME_BODY}"
    return f"{head}\n\n{WELCOME_BODY}"


def handle_start(user_id: int, first_name: str = "") -> None:
    if CONSENT_MODE == "strict" and not has_consent(user_id):
        with _lock:
            user_data[user_id] = {"state": STATE_CONSENT, "updated_at": int(time.time())}
        _mark_dirty(user_id)
        send_message(user_id, _welcome_text(first_name))
        send_message(user_id, _consent_text(), keyboard=_consent_keyboard())
        return

    if CONSENT_MODE == "soft" and not has_consent(user_id):
        with _lock:
            user_data[user_id] = {"state": STATE_CONSENT, "updated_at": int(time.time())}
        _mark_dirty(user_id)
        send_message(
            user_id,
            _welcome_text(first_name),
            keyboard=_soft_start_keyboard(),
        )
        return

    _begin_destination(user_id, first_name)


def _begin_destination(user_id: int, first_name: str = "") -> None:
    with _lock:
        user_data[user_id] = {"state": STATE_DESTINATION, "updated_at": int(time.time())}
    _mark_dirty(user_id)
    name = f", {first_name}" if first_name else ""
    send_message(
        user_id,
        f"🌴 Отлично{name}! Давайте подберём тур.\n\n"
        "📍 Куда хотите поехать?\n\n"
        "Жмите кнопку — или напишите своё направление:",
        keyboard=_dest_keyboard(),
    )


def handle_cancel(user_id: int) -> None:
    with _lock:
        existed = user_data.pop(user_id, None) is not None
    if existed:
        _mark_dirty(user_id)
        delete_session(user_id)
        send_message(
            user_id,
            "❌ Заявка отменена.\n\nКогда будете готовы — «Начать».",
            keyboard=_hide_keyboard(),
        )
    else:
        send_message(user_id, f"Сейчас нет активной заявки.\n\n{HINT_START}")


def _origin_keyboard() -> str:
    rows = _chunk_buttons(list(ORIGIN_OPTIONS_PLAIN), "primary", 2)
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary"),
                 _btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _ask_origin(user_id: int) -> None:
    send_message(
        user_id,
        "🛫 Откуда вылетаете?\n\n"
        "Нужно, чтобы посчитать перелёт — цена сильно зависит от города.",
        keyboard=_origin_keyboard(),
    )


def _ask_dates(user_id: int) -> None:
    send_message(
        user_id,
        "📅 Когда планируете поездку?\n\n"
        "Кнопка или свои даты (например: 15-22 июня):",
        keyboard=_dates_keyboard(),
    )


def _ask_people(user_id: int) -> None:
    send_message(
        user_id,
        "👥 Сколько человек поедет?\nКнопка или число 1–50:",
        keyboard=_people_keyboard(),
    )


def _ask_budget(user_id: int) -> None:
    send_message(
        user_id,
        "💰 Бюджет на человека (примерно, ₽)\nКнопка или своя сумма:",
        keyboard=_budget_keyboard(),
    )


def _ask_contact(user_id: int) -> None:
    send_message(
        user_id,
        "📞 Как удобнее связаться?\n\n"
        "Можно просто VK (этот чат) — телефон не обязателен.\n"
        "Или телефон / Telegram.",
        keyboard=_contact_keyboard(),
    )


def _step_consent(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    first_name = message.get("_user_name", "")
    if text in (START_BUTTON_TEXT, CONSENT_YES_TEXT, "Начать подбор"):
        set_consent(user_id)
        _begin_destination(user_id, first_name)
        return
    if CONSENT_MODE == "strict" and text == CONSENT_NO_TEXT:
        with _lock:
            user_data.pop(user_id, None)
        _mark_dirty(user_id, user=False)
        delete_session(user_id)
        send_message(
            user_id,
            "Поняли. Без согласия заявку оформить нельзя.\n\nЕсли передумаете — «Начать».",
            keyboard=_hide_keyboard(),
        )
        return
    if CONSENT_MODE == "soft":
        send_message(
            user_id,
            "Нажмите «🚀 Начать подбор», чтобы продолжить.",
            keyboard=_soft_start_keyboard(),
        )
    else:
        send_message(
            user_id,
            "Нажмите «✅ Согласен» или «❌ Отказаться».",
            keyboard=_consent_keyboard(),
        )


def _step_destination(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    dest = text.strip()
    if dest.lower() == "другое":
        send_message(user_id, "✍️ Напишите ваше направление:", keyboard=_nav_keyboard())
        return
    info["destination"] = dest
    info["state"] = STATE_ORIGIN
    _ask_origin(user_id)


def _step_origin(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    city = (text or "").strip()
    if city.lower() in ("другой город", "другое"):
        send_message(user_id, "✍️ Напишите город вылета:", keyboard=_nav_keyboard())
        return
    if not city:
        _ask_origin(user_id)
        return
    info["origin"] = city
    info["state"] = STATE_DATES
    _ask_dates(user_id)


def _step_dates(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    raw = (text or "").strip()
    if raw in (DATE_CUSTOM_LABEL, "свои даты"):
        send_message(
            user_id,
            "✍️ Напишите даты текстом (например: 15-22 июня):",
            keyboard=_nav_keyboard(),
        )
        return
    preset_map = {label: val for label, val in DATE_PRESETS}
    if raw in preset_map:
        raw = preset_map[raw]
    if not raw:
        _ask_dates(user_id)
        return
    info["dates"] = raw
    info["state"] = STATE_PEOPLE
    _ask_people(user_id)


def _step_people(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    ok, value = validate_people(text)
    if not ok:
        send_message(
            user_id,
            "Укажите число от 1 до 50 (или «5+») — удобнее кнопкой.",
            keyboard=_people_keyboard(),
        )
        return
    info["people"] = value
    info["state"] = STATE_BUDGET
    _ask_budget(user_id)


def _step_budget(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    raw = (text or "").strip()
    if raw in (BUDGET_CUSTOM_LABEL, "свой бюджет"):
        send_message(
            user_id,
            "✍️ Напишите бюджет числом (например: 75000):",
            keyboard=_nav_keyboard(),
        )
        return
    budget_map = {label: val for label, val in BUDGET_PRESETS}
    if raw in budget_map:
        info["budget"] = budget_map[raw]
        info["state"] = STATE_CONTACT
        _ask_contact(user_id)
        return
    ok, value = validate_budget(raw)
    if not ok:
        send_message(
            user_id,
            "Нужна сумма числом или кнопка с бюджетом.",
            keyboard=_budget_keyboard(),
        )
        return
    info["budget"] = value
    info["state"] = STATE_CONTACT
    _ask_contact(user_id)


def _step_contact(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    t = (text or "").strip()
    client_name = message.get("_user_name") or f"VK {user_id}"

    if t in (CONTACT_VK_CHAT_LABEL, CONTACT_VK_TEXT, "vk", "вк", "VK"):
        info["contact_method"] = "vk"
        handle_completion(user_id, f"VK (чат id {user_id}) · {client_name}", message)
        return

    if t in (CONTACT_PHONE_TEXT, "телефон", "phone"):
        info["contact_method"] = "phone"
        info["state"] = STATE_PHONE
        send_message(
            user_id,
            "📱 Укажите номер телефона (+7…):",
            keyboard=_nav_keyboard(),
        )
        return

    if t in (CONTACT_TG_TEXT, "telegram", "tg", "телеграм"):
        info["contact_method"] = "telegram"
        info["state"] = "telegram_handle"
        send_message(
            user_id,
            "✈️ Напишите Telegram: @username или номер, привязанный к TG:",
            keyboard=_nav_keyboard(),
        )
        return

    ok, phone = validate_phone(t)
    if ok and phone:
        info["contact_method"] = "phone"
        handle_completion(user_id, phone, message)
        return

    send_message(
        user_id,
        "Выберите способ связи кнопкой — или введите номер телефона.",
        keyboard=_contact_keyboard(),
    )


def _step_phone(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    ok, phone = validate_phone(text)
    if not ok:
        send_message(
            user_id,
            "Номер некорректен. Формат +7XXXXXXXXXX.\nНазад — другой способ связи.",
            keyboard=_nav_keyboard(),
        )
        return
    info["contact_method"] = "phone"
    handle_completion(user_id, phone, message)


def _step_telegram_handle(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    raw = (text or "").strip()
    if not raw or len(raw) < 2:
        send_message(
            user_id,
            "Нужен @username или контакт Telegram.",
            keyboard=_nav_keyboard(),
        )
        return
    if not raw.startswith("@") and not raw.startswith("+") and not raw.isdigit():
        raw = f"@{raw}" if " " not in raw else raw
    info["contact_method"] = "telegram"
    handle_completion(user_id, f"Telegram {raw}", message)


STATE_HANDLERS: Dict[str, Callable] = {
    STATE_CONSENT:     _step_consent,
    STATE_DESTINATION: _step_destination,
    STATE_ORIGIN:      _step_origin,
    STATE_DATES:       _step_dates,
    STATE_PEOPLE:      _step_people,
    STATE_BUDGET:      _step_budget,
    STATE_CONTACT:     _step_contact,
    STATE_PHONE:       _step_phone,
    "telegram_handle": _step_telegram_handle,
}

PREVIOUS_STATE: Dict[str, str] = {
    STATE_ORIGIN:      STATE_DESTINATION,
    STATE_DATES:       STATE_ORIGIN,
    STATE_PEOPLE:      STATE_DATES,
    STATE_BUDGET:      STATE_PEOPLE,
    STATE_CONTACT:     STATE_BUDGET,
    STATE_PHONE:       STATE_CONTACT,
    "telegram_handle": STATE_CONTACT,
}


def _prompt_for_state(user_id: int, state: str) -> None:
    if state == STATE_DESTINATION:
        send_message(
            user_id,
            "📍 Куда хотите поехать?\nКнопка или своё направление:",
            keyboard=_dest_keyboard(),
        )
    elif state == STATE_ORIGIN:
        _ask_origin(user_id)
    elif state == STATE_DATES:
        _ask_dates(user_id)
    elif state == STATE_PEOPLE:
        _ask_people(user_id)
    elif state == STATE_BUDGET:
        _ask_budget(user_id)
    elif state == STATE_CONTACT:
        _ask_contact(user_id)
    elif state == STATE_PHONE:
        send_message(user_id, "📱 Укажите номер телефона (+7…):", keyboard=_nav_keyboard())
    elif state == "telegram_handle":
        send_message(
            user_id,
            "✈️ Напишите Telegram (@username):",
            keyboard=_nav_keyboard(),
        )
    else:
        send_message(user_id, "Продолжите ввод:", keyboard=_nav_keyboard())


def _go_back(user_id: int) -> None:
    info = user_data.get(user_id, {})
    state = info.get("state")
    previous = PREVIOUS_STATE.get(state)
    if previous is None:
        send_message(user_id, "Вы на первом шаге. Можно отменить заявку кнопкой «Отменить».")
        return
    info["state"] = previous
    _mark_dirty(user_id, user=False)
    _prompt_for_state(user_id, previous)


def handle_dialog(user_id: int, text: str, message: Dict[str, Any]) -> None:
    info = user_data.get(user_id, {})
    state = info.get("state")
    if state is None:
        send_message(user_id, HINT_START)
        return
    handler = STATE_HANDLERS.get(state)
    if handler is None:
        return
    handler(user_id, text, message, info)
    _mark_dirty(user_id, user=False)


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

def _confirm_to_user(user_id: int, info: Dict[str, Any], phone: str) -> None:
    send_message(
        user_id,
        "✅ Заявка принята! Менеджер «АПРЕЛЬ тур» свяжется с вами.\n\n"
        f"📍 Направление: {info.get('destination', '?')}\n"
        f"📅 Даты: {info.get('dates', '?')}\n"
        f"👥 Человек: {info.get('people', '?')}\n"
        f"💰 Бюджет: {info.get('budget', '?')}₽\n"
        f"📞 Связь: {phone}\n\n"
        "Спасибо, что выбрали нас 🌺\n\n"
        "📋 ИП Замятина Мария Андреевна\nОГРНИП 290211659807",
        keyboard=_hide_keyboard(),
    )


def _notify_admin_telegram(
    user_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> None:
    """Deliver VK lead to the bot creator in Telegram (ADMIN_ID / LEAD_NOTIFY_IDS)."""
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token or not LEAD_NOTIFY_IDS:
        return
    text = (
        "🔔 Новая заявка (VK)!\n\n"
        f"От: {client_name or 'без имени'}\n"
        f"VK ID: {user_id}\n"
        f"📍 {info.get('destination', '?')}\n"
        f"📅 {info.get('dates', '?')}\n"
        f"👥 {info.get('people', '?')} чел\n"
        f"💰 {info.get('budget', '?')}₽\n"
        f"📞 Связь: {phone}"
    )
    for recipient in LEAD_NOTIFY_IDS:
        try:
            resp = http_session.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": recipient, "text": text},
                timeout=HTTP_TIMEOUT,
            )
            if resp.status_code == 200:
                logger.info("VK lead from %s delivered to Telegram chat %s", user_id, recipient)
            else:
                logger.error(
                    "Telegram notify failed for %s→%s: %s",
                    user_id, recipient, resp.text[:200],
                )
        except Exception as exc:
            logger.error("Telegram notify error for VK lead %s: %s", user_id, exc)


def _notify_admin(user_id: int, info: Dict[str, Any], phone: str, client_name: Optional[str]) -> None:
    """Notify admin: Telegram (creator) + optional VK peer if ADMIN_ID is a VK user."""
    _notify_admin_telegram(user_id, info, phone, client_name)
    if ADMIN_ID:
        # Legacy: also ping ADMIN_ID inside VK (if it is a VK user id).
        send_message(
            ADMIN_ID,
            "🔔 Новая заявка (VK)!\n\n"
            f"От: {client_name or 'без имени'} (ID: {user_id})\n"
            f"📍 {info.get('destination', '?')}\n"
            f"📅 {info.get('dates', '?')}\n"
            f"👥 {info.get('people', '?')} чел\n"
            f"💰 {info.get('budget', '?')}₽\n"
            f"📞 Связь: {phone}",
        )
    elif not LEAD_NOTIFY_IDS:
        logger.warning(
            "VK lead from %s not delivered: set ADMIN_ID or LEAD_NOTIFY_IDS (+ BOT_TOKEN for TG)",
            user_id,
        )


# When true, MDT + AI run inline (tests). Production defers them off the webhook.
SYNC_COMPLETION = os.getenv("SYNC_COMPLETION", "").lower().strip() in ("1", "true", "yes")


def _tutu_search(info: Dict[str, Any]) -> Optional[Any]:
    """Live transport search for a completed lead. Never raises."""
    if not TUTU_ENABLED:
        return None
    try:
        return _tutu.search_offers(
            _tutu_settings(), http_session,
            destination=info.get("destination", ""),
            dates_raw=info.get("dates", ""),
            origin=info.get("origin", ""),
            people=info.get("people", 1),
            budget=info.get("budget"),
            log=logger,
        )
    except Exception as exc:
        logger.error("VK Tutu search failed: %s", exc)
        return None


def _send_tutu_to_admin(user_id: int, result: Any, client_name: Optional[str]) -> None:
    """Price anchor + checkout links to the manager, in Telegram.

    Sent separately: the lead notification itself goes out on the critical
    path and must not wait for a search.
    """
    block = _tutu.format_admin_block(result)
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not block or not bot_token or not LEAD_NOTIFY_IDS:
        return
    text = f"💼 <b>По заявке из VK от {client_name or user_id}</b>{block}"
    for recipient in LEAD_NOTIFY_IDS:
        try:
            http_session.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": recipient, "text": text, "parse_mode": "HTML"},
                timeout=HTTP_TIMEOUT,
            )
        except Exception as exc:
            logger.error("VK Tutu admin notify failed: %s", exc)


def _post_completion_side_effects(
    user_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> None:
    """MDT push + live offers + AI blurb — off the VK Callback hot path."""
    try:
        send_lead_to_mdt(user_id, info, phone, client_name)

        result = _tutu_search(info)
        client_text = ""
        if result and TUTU_SHOW_CLIENT:
            # VK renders no markup at all, so the HTML variant would show tags.
            client_text = _tutu.format_client_message(result, markup="plain")

        send_typing(user_id)
        if client_text:
            send_message(user_id, client_text)
        else:
            # Tutu off or unavailable — the client still gets a suggestion.
            send_message(user_id, generate_ai_selection(
                info.get("destination", ""), info.get("dates", ""),
                info.get("people", ""), info.get("budget", ""),
            ))

        if result and TUTU_SHOW_ADMIN:
            _send_tutu_to_admin(user_id, result, client_name)
    except Exception as exc:
        logger.error("VK post-completion side effects failed for %s: %s", user_id, exc)


def handle_completion(user_id: int, phone: str, message: Dict[str, Any]) -> None:
    info = dict(user_data.get(user_id, {}))
    client_name = message.get("_user_name") or f"VK {user_id}"

    try:
        save_lead(user_id, info, phone, first_name=client_name)
    except Exception as exc:
        logger.error("Failed to save VK lead for %s: %s", user_id, exc)

    _confirm_to_user(user_id, info, phone)
    _notify_admin(user_id, info, phone, client_name)
    with _lock:
        user_data.pop(user_id, None)
    delete_session(user_id)

    if SYNC_COMPLETION:
        _post_completion_side_effects(user_id, info, phone, client_name)
    else:
        threading.Thread(
            target=_post_completion_side_effects,
            args=(user_id, info, phone, client_name),
            daemon=True,
            name=f"vk-complete-{user_id}",
        ).start()


# ---------------------------------------------------------------------------
# Update processing
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Follow-up for incomplete dialogs
# ---------------------------------------------------------------------------

FOLLOWUP_DELAY_HOURS = int(os.getenv("FOLLOWUP_DELAY_HOURS", "3"))


def _send_followups() -> int:
    """Send one follow-up reminder to users with incomplete dialogs."""
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
            "Продолжить? Напишите «Начать», чтобы начать заново, «Отмена», чтобы отменить.",
        )
        with _lock:
            if cid in user_data:
                user_data[cid]["_followed_up"] = True
        _mark_dirty(cid, user=False)
        sent += 1
    if sent:
        logger.info("VK follow-up sent to %d user(s)", sent)
    return sent


def _start_followup_worker() -> None:
    if FOLLOWUP_DELAY_HOURS <= 0:
        return
    def _worker():
        while True:
            time.sleep(600)
            try:
                _send_followups()
            except Exception as exc:
                logger.error("Error in VK follow-up worker: %s", exc)
    threading.Thread(target=_worker, daemon=True, name="vk-followup").start()
    logger.info("Follow-up worker started (%s hours delay)", FOLLOWUP_DELAY_HOURS)


# ---------------------------------------------------------------------------
# Admin helpers (VK)
# ---------------------------------------------------------------------------

# VK command aliases (users type natural language, not /commands)
_COMMAND_ALIASES = {
    "начать": "start", "старт": "start", "привет": "start",
    "отмена": "cancel", "назад": "back",
    "помощь": "help", "справка": "help",
    "политика": "privacy",
    "удалить": "delete",
    "аналитика": "analytics", "статистика": "analytics",
    "экспорт": "export", "заявки": "export",
    "рассылка": "broadcast",
    "напоминания": "followup",
}


def _process_message(message: Dict[str, Any]) -> None:
    """Process one VK message_new event."""
    msg = message.get("object", {}).get("message", message.get("message", {}))
    user_id = msg.get("from_id") or msg.get("peer_id")
    if not user_id:
        return
    text = (msg.get("text") or "").strip()

    # Fetch user name (cached in all_users)
    with _lock:
        meta = all_users.get(user_id)
    if meta is None or not meta.get("first_name"):
        name = get_user_name(user_id)
    else:
        name = meta["first_name"]

    with _lock:
        all_users[user_id] = {
            "first_name": name,
            "username": "",
            "last_seen": int(time.time()),
        }
        if "consent_at" in (meta or {}):
            all_users[user_id]["consent_at"] = meta["consent_at"]
        session_open = user_id in user_data
        if session_open:
            user_data[user_id]["updated_at"] = int(time.time())
    _mark_dirty(user_id, session=session_open)

    # Build augmented message with user name
    msg["_user_name"] = name

    # Command recognition (case-insensitive, natural language)
    text_lower = text.lower()
    command = _COMMAND_ALIASES.get(text_lower)
    # Soft-start button must not re-trigger handle_start while already on consent step.
    if text == START_BUTTON_TEXT or text_lower in ("🚀 начать подбор", "начать подбор"):
        cur_state = (user_data.get(user_id) or {}).get("state")
        if cur_state == STATE_CONSENT:
            command = None
        else:
            command = "start"
    arg_or_text = text  # full text for broadcast, etc.

    # Admin commands
    if user_id == ADMIN_ID:
        if command == "help":
            send_message(user_id, USER_HELP)
            return
        if command == "analytics":
            with _db_cursor() as cur:
                cur.execute("SELECT state, COUNT(*) FROM sessions GROUP BY state")
                by_state = {row[0]: row[1] for row in cur.fetchall()}
                cur.execute("SELECT destination, COUNT(*) as cnt FROM sessions WHERE destination IS NOT NULL AND destination != '' GROUP BY destination ORDER BY cnt DESC LIMIT 10")
                dest_stats = cur.fetchall()
                cur.execute("SELECT COUNT(*) FROM users WHERE consent_at IS NOT NULL")
                consented = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM users")
                total_users = cur.fetchone()[0]
            lines = ["📊 Аналитика:\n", f"Всего: {total_users}", f"С согласием: {consented}", f"Активных сессий: {sum(by_state.values())}"]
            if dest_stats:
                lines.append("\n📍 Направления:")
                for dest, cnt in dest_stats:
                    lines.append(f"  {dest}: {cnt}")
            send_message(user_id, "\n".join(lines))
            return
        if command == "export":
            with _db_cursor() as cur:
                cur.execute("SELECT chat_id, destination, dates, people, budget, phone FROM sessions WHERE phone IS NOT NULL AND phone != '' ORDER BY updated_at DESC LIMIT 50")
                rows = cur.fetchall()
            if not rows:
                send_message(user_id, "Нет завершённых заявок для экспорта.")
                return
            lines = [f"📋 Экспорт ({len(rows)}):\n"]
            for i, (cid, dest, dates, people, budget, phone) in enumerate(rows, 1):
                lines.append(f"{i}. {dest or '?'} | {dates or '?'} | {people or '?'} чел | {budget or '?'}₽ | {phone}")
            text = "\n".join(lines)
            while text:
                send_message(user_id, text[:4000])
                text = text[4000:]
            return
        if command == "broadcast":
            if not arg_or_text.strip():
                send_message(user_id, "Напишите: рассылка {текст}")
                return
            count = 0
            with _lock:
                recipients = list(all_users.keys())
            for uid in recipients:
                if uid == ADMIN_ID:
                    continue
                if send_message(uid, arg_or_text):
                    count += 1
                time.sleep(0.05)
            send_message(user_id, f"✅ Рассылка отправлена {count} пользователям")
            return
        if command == "followup":
            sent = _send_followups()
            send_message(user_id, f"✅ Напоминания отправлены {sent} пользователям")
            return

    if command == "start":
        handle_start(user_id, name)
        return
    if command == "help":
        send_message(user_id, USER_HELP)
        return
    if command == "privacy":
        send_message(user_id, _privacy_text())
        return
    if command == "delete":
        delete_user_data(user_id)
        send_message(user_id,
            "🗑 Ваши персональные данные удалены, согласие отозвано.\n\n"
            "Чтобы снова воспользоваться подбором тура — напишите «Начать».",
            keyboard=_hide_keyboard())
        return
    if command == "cancel":
        handle_cancel(user_id)
        return
    if command == "back":
        if user_id in user_data:
            _go_back(user_id)
        else:
            send_message(user_id, HINT_START)
        return

    # Button-text matching (exact match against known buttons)
    if text == BACK_BUTTON_TEXT:
        if user_id in user_data:
            _go_back(user_id)
        else:
            send_message(user_id, HINT_START)
        return
    if text == CANCEL_BUTTON_TEXT:
        handle_cancel(user_id)
        return

    # Dialog flow
    if user_id in user_data:
        handle_dialog(user_id, text, msg)
    else:
        send_message(user_id, HINT_START)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def save_state() -> None:
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
        touch_user(chat_id, meta.get("first_name", ""), meta.get("username", ""),
                   last_seen=meta.get("last_seen"))


def load_state() -> None:
    init_db()
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
    logger.info("Loaded %d sessions and %d users from SQLite (VK)", len(user_data), len(all_users))


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # 1 MB — VK events are well under this


@app.route("/")
def index() -> str:
    return "TurBot VK is running!"


@app.route("/health")
def health() -> Any:
    return jsonify({
        "status": "ok",
        "platform": "vk",
        "vk_token_configured": bool(VK_ACCESS_TOKEN),
        "vk_group_id": VK_GROUP_ID,
        "admin_id_configured": bool(ADMIN_ID),
        "lead_notify_configured": bool(LEAD_NOTIFY_IDS),
        "groq_configured": bool(GROQ_API_KEY),
        "ai_mode": AI_MODE,
        "mdt_enabled": MDT_ENABLED,
        "mdt_mode": MDT_MODE,
        "total_users": len(all_users),
        "active_sessions": len(user_data),
        "privacy_policy_configured": bool(PRIVACY_POLICY_URL),
        "data_retention_days": DATA_RETENTION_DAYS,
        "consent_mode": CONSENT_MODE,
        "demo_mode": DEMO_MODE,
        "tutu_enabled": TUTU_ENABLED,
    })


@app.route("/vk/webhook", methods=["POST"])
def vk_webhook() -> Any:
    """Handle VK Callback API events."""
    data = request.get_json(silent=True)
    if not data or "type" not in data:
        return "ok", 200

    # Optional secret-key verification
    if VK_SECRET_KEY:
        received_secret = data.get("secret", "")
        if not hmac.compare_digest(received_secret, VK_SECRET_KEY):
            logger.warning("VK webhook: invalid secret key")
            return "ok", 200

    event_type = data["type"]

    # Confirmation request (VK verifies server ownership)
    if event_type == "confirmation":
        if VK_CONFIRMATION:
            return VK_CONFIRMATION, 200
        logger.warning("VK confirmation request but VK_CONFIRMATION not set")
        return "ok", 200

    # New message from user
    if event_type == "message_new":
        try:
            _process_message(data)
        except Exception as exc:
            logger.error("Error processing VK message: %s", exc, exc_info=True)
        finally:
            save_state()

    # All other event types — acknowledge
    return "ok", 200


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

load_state()
_start_timeout_worker()
_start_followup_worker()
_start_retention_worker()

def _deferred_network_startup() -> None:
    """Keep slow network calls off module import.

    Doing this inline delayed the port bind long enough for a platform health
    check to fail the deploy — the exact bug already fixed in bot.py and never
    ported here.
    """
    try:
        _mdt_load_countries()
    except Exception as exc:
        logger.warning("VK MDT country load failed: %s", exc)


if MDT_ENABLED:
    threading.Thread(
        target=_deferred_network_startup, name="vk-startup-network", daemon=True
    ).start()

logger.info(
    "TurBot VK started (port=%s, group=%s, admin_set=%s, groq_set=%s)",
    PORT, VK_GROUP_ID, bool(ADMIN_ID), bool(GROQ_API_KEY),
)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
