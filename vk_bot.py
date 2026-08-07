"""TurBot VK — бот турагентства «АПРЕЛЬ тур» для VK.com.

Самодостаточный Flask-webhook для группы ВКонтакте. Паритет с bot.py:
soft/strict согласие, кнопки на всех шагах (даты, бюджет, люди),
связь VK / телефон / MAX, лиды в Telegram админу, MDT CRM.

Деплой: отдельный процесс (см. deploy/vk-turbot.service).
"""
from __future__ import annotations

import os
import re
import json
import hmac
import time
import random
import sqlite3
import logging
import threading
from contextlib import contextmanager
from datetime import datetime
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
    STATE_KIDS,
    STATE_KIDS_AGES,
    STATE_INFANTS,
    STATE_PHONE,
    STATE_MAX,
    PEOPLE_OPTIONS,
    BACK_BUTTON_TEXT,
    CANCEL_BUTTON_TEXT,
    CONSENT_YES_TEXT,
    CONSENT_NO_TEXT,
    START_BUTTON_TEXT,
    CONTACT_MAX_TEXT,
    MAX_PROFILE_HINT,
    CONTACT_PHONE_TEXT,
    CONTACT_VK_TEXT,
    POPULAR_DESTINATIONS_PLAIN,
)
from shared import tutu as _tutu
from shared import tourvisor as _tourvisor
from shared import version as _version
from shared.validation import (
    validate_phone, validate_people, validate_budget,
    parse_kids_ages, party_bands, party_text as _party_text,
    ages_to_db as _ages_to_db, ages_from_db as _ages_from_db,
)
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


def _env_int(name: str, default: int = 0) -> int:
    """Parse an int env var; empty or invalid values fall back to the default.

    `int(os.getenv("X", "12"))` only uses its default when the variable is
    ABSENT. A .env copied from .env.example is full of keys that are present
    and empty, and `int("")` raises — which kills the process at import and
    shows up as a bare gunicorn exit code 3. Already fixed in bot.py; this is
    the same guard, ported.
    """
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        logger.warning("Invalid %s=%r — using default %s", name, raw, default)
        return default

VK_ACCESS_TOKEN      = os.getenv("VK_ACCESS_TOKEN", "")
VK_GROUP_ID          = _env_int("VK_GROUP_ID", 0)
VK_CONFIRMATION      = os.getenv("VK_CONFIRMATION", "")
VK_API_VERSION       = os.getenv("VK_API_VERSION", "5.199")
VK_SECRET_KEY        = os.getenv("VK_SECRET_KEY", "")  # optional callback secret
VK_API_BASE          = "https://api.vk.com/method/"

GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL        = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
AI_MODE           = os.getenv("AI_MODE", "template").lower().strip()
PORT              = _env_int("VK_PORT", _env_int("PORT", 5100))
DATABASE_PATH     = os.getenv("VK_DATABASE_PATH", os.getenv("DATABASE_PATH", "vk_bot_state.sqlite"))
ADMIN_ID          = _env_int("ADMIN_ID", 0)
DIALOG_TIMEOUT_HOURS = _env_int("DIALOG_TIMEOUT_HOURS", 6)
HTTP_TIMEOUT      = 15

# Most enquiries are adults-only. Keep the common answer one tap away while
# still collecting exact ages when children are travelling.
NO_KIDS_BUTTON_TEXT = "👶 Детей нет"


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
    MDT_REMINDER_DAYS = _env_int("MDT_REMINDER_DAYS", 1)
except (ValueError, TypeError):
    MDT_REMINDER_DAYS = 1
MDT_REMINDER_TEXT = os.getenv("MDT_REMINDER_TEXT", "Позвонить по заявке с VK-бота")

if MDT_MODE not in ("lead", "preorder", "both"):
    logger.warning("MDT_MODE '%s' is unknown, defaulting to 'lead'", MDT_MODE)
    MDT_MODE = "lead"

# 152-ФЗ compliance
# The Telegram bot serves the policy at /privacy on the same host, so VK can
# link to it. Without this the consent text has no policy link at all while the
# bot collects phone numbers — the gap only showed up once VK went live.
PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL", "").strip()
    or os.getenv("RENDER_EXTERNAL_URL", "").strip()
).rstrip("/")
PRIVACY_POLICY_URL = os.getenv("PRIVACY_POLICY_URL", "").strip() or (
    f"{PUBLIC_BASE_URL}/privacy" if PUBLIC_BASE_URL else ""
)
DATA_OPERATOR_NAME = os.getenv(
    "DATA_OPERATOR_NAME",
    "ТА «АПРЕЛЬ тур»",
)
DATA_RETENTION_DAYS = _env_int("DATA_RETENTION_DAYS", 180)
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
# VK_TUTU_* override the shared TUTU_* for this bot alone. Both bots read one
# .env, but they serve different audiences: the Telegram instance is a
# portfolio showcase where live prices are the point, while VK takes real
# enquiries and the agency may not want ticket prices quoted there at all.
_tutu_default = os.getenv("TUTU_ENABLED", "true")
TUTU_ENABLED = os.getenv("VK_TUTU_ENABLED", _tutu_default).lower().strip() in ("1", "true", "yes")
TUTU_ENDPOINT = os.getenv("TUTU_ENDPOINT", "https://mcp.tutu.ru/mcp").strip()
TUTU_TIMEOUT = _env_int("TUTU_TIMEOUT", 30)
TUTU_DEFAULT_ORIGIN = os.getenv("TUTU_DEFAULT_ORIGIN", "Архангельск").strip()
TUTU_MAX_OFFERS = _env_int("TUTU_MAX_OFFERS", 3)
TUTU_CACHE_TTL = _env_int("TUTU_CACHE_TTL", 900)
TUTU_SHOW_CLIENT = os.getenv(
    "VK_TUTU_SHOW_CLIENT", os.getenv("TUTU_SHOW_CLIENT", "true")
).lower().strip() in ("1", "true", "yes")
TUTU_SHOW_ADMIN = os.getenv(
    "VK_TUTU_SHOW_ADMIN", os.getenv("TUTU_SHOW_ADMIN", "true")
).lower().strip() in ("1", "true", "yes")

# --- Tourvisor package tours ------------------------------------------------
# The token enables the integration by default; the VK-specific flag can turn
# it off instantly without removing credentials during a rollout.
TOURVISOR_TOKEN = os.getenv("TOURVISOR_TOKEN", "").strip()
TOURVISOR_ENABLED = os.getenv(
    "VK_TOURVISOR_ENABLED", "true" if TOURVISOR_TOKEN else "false"
).lower().strip() in ("1", "true", "yes")
TOURVISOR_BASE_URL = os.getenv(
    "TOURVISOR_BASE_URL", "https://api.tourvisor.ru/search/api/v1"
).strip()
TOURVISOR_TIMEOUT = _env_int("TOURVISOR_TIMEOUT", 15)
TOURVISOR_POLL_INTERVAL = _env_int("TOURVISOR_POLL_INTERVAL", 3)
TOURVISOR_MAX_WAIT = _env_int("TOURVISOR_MAX_WAIT", 30)
TOURVISOR_MAX_OFFERS = _env_int("TOURVISOR_MAX_OFFERS", 9)
TOURVISOR_CAROUSEL_IMAGES = os.getenv(
    "VK_TOURVISOR_CAROUSEL_IMAGES", "true"
).lower().strip() in ("1", "true", "yes")


def _tourvisor_settings() -> "_tourvisor.TourvisorSettings":
    return _tourvisor.TourvisorSettings(
        enabled=TOURVISOR_ENABLED,
        token=TOURVISOR_TOKEN,
        base_url=TOURVISOR_BASE_URL,
        timeout=TOURVISOR_TIMEOUT,
        poll_interval=TOURVISOR_POLL_INTERVAL,
        max_wait=TOURVISOR_MAX_WAIT,
        max_offers=TOURVISOR_MAX_OFFERS,
    )


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
DIRECTION_UNDECIDED_LABEL = "🌴 Не определился"
UNDECIDED_DESTINATION = "Не определился — нужна консультация"
STATE_REVIEW = "review"

# Quick picks (label on keyboard → value stored in lead). Short labels are
# deliberate: VK's Android client clips two-column inline buttons aggressively.
DATE_PRESETS: List[Tuple[str, str]] = [
    ("🏖 Выходные", "ближайшие выходные"),
    ("📅 Этот месяц", "в этом месяце"),
    ("🗓 След. месяц", "следующий месяц"),
    ("🤷 Гибкие даты", "даты гибкие"),
]
ORIGIN_PRESETS: List[Tuple[str, str]] = [
    ("Архангельск", "Архангельск"),
    ("Москва", "Москва"),
    ("Петербург", "Санкт-Петербург"),
    ("Другой город", "Другой город"),
]
BUDGET_PRESETS: List[Tuple[str, int]] = [
    ("до 150 000 ₽", 150000),
    ("до 200 000 ₽", 200000),
    ("до 300 000 ₽", 300000),
    ("до 400 000 ₽", 400000),
]
DATE_CUSTOM_LABEL = "✏️ Свои даты"
BUDGET_CUSTOM_LABEL = "✏️ Свой бюджет"
CONTACT_VK_CHAT_LABEL = "💙 VK (этот чат)"
REVIEW_CONFIRM_TEXT = "✅ Отправить заявку"
TOUR_SEARCH_BUTTON_TEXT = "🔎 Показать варианты"
TOUR_MORE_BUTTON_TEXT = "🔄 Ещё варианты"
TOUR_SEND_MANAGER_TEXT = "💬 Нужна помощь"
TOUR_SEND_SELECTED_TEXT = "✅ Отправить этот вариант"
CONTACT_OTHER_TEXT = "📱 Телефон или MAX"
TOUR_CHEAPER_TEXT = "💰 Дешевле"
TOUR_BETTER_TEXT = "⭐ Лучше"
TOUR_ALL_INCLUSIVE_TEXT = "🍽 Всё включено"
TOUR_SIMILAR_TEXT = "🏨 Похожие отели"
TOUR_COMPARE_TEXT = "⚖️ Сравнить варианты"
TOUR_EDIT_DATES_TEXT = "📅 Даты"
TOUR_EDIT_BUDGET_TEXT = "💰 Бюджет"
REVIEW_EDIT_DATES_TEXT = "✏️ Изменить даты"
REVIEW_EDIT_BUDGET_TEXT = "✏️ Изменить бюджет"
NEW_SELECTION_BUTTON_TEXT = "🧳 Новый подбор"

USER_HELP = (
    "🌴 «АПРЕЛЬ тур» — подбор отдыха\n\n"
    "Соберу короткую заявку и передам менеджеру. Можно почти всё кнопками.\n\n"
    "Команды:\n"
    "  Начать — подбор тура\n"
    "  Отмена — отменить заявку\n"
    "  Политика — персональные данные\n"
    "  Удалить — стереть мои данные\n"
    "  Кнопки — вернуть кнопки, если они пропали\n"
    "  Помощь — эта справка\n\n"
    "Связь: VK / телефон / MAX — на выбор.\n\n"
    "ТА «АПРЕЛЬ тур»"
)

WELCOME_BODY = (
    "Подберём тур под даты и бюджет — заявка уйдёт менеджеру.\n\n"
    "Как это работает:\n"
    "1) несколько вопросов (можно кнопками)\n"
    "2) удобный способ связи: VK, телефон или MAX\n"
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
                kids INTEGER,
                infants INTEGER,
                budget INTEGER,
                budget_scope TEXT,
                phone TEXT,
                needs_consultation INTEGER NOT NULL DEFAULT 0,
                selected_tour TEXT,
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
                kids INTEGER,
                infants INTEGER,
                budget INTEGER,
                budget_scope TEXT,
                phone TEXT NOT NULL,
                needs_consultation INTEGER NOT NULL DEFAULT 0,
                selected_tour TEXT,
                created_at INTEGER NOT NULL
            )
        """)
        # Additive migration for databases created before the origin step.
        for _t in ("sessions", "leads"):
            cur.execute(f"PRAGMA table_info({_t})")
            _cols = {r[1] for r in cur.fetchall()}
            if "origin" not in _cols:
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN origin TEXT")
            if "kids_ages" not in _cols:
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN kids_ages TEXT")
            for _c in ("kids", "infants"):
                if _c not in _cols:
                    cur.execute(f"ALTER TABLE {_t} ADD COLUMN {_c} INTEGER")
            if "needs_consultation" not in _cols:
                cur.execute(
                    f"ALTER TABLE {_t} ADD COLUMN needs_consultation INTEGER NOT NULL DEFAULT 0"
                )
            if "selected_tour" not in _cols:
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN selected_tour TEXT")
            if "budget_scope" not in _cols:
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN budget_scope TEXT")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_chat_id ON leads(chat_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at)")
        cur.execute("PRAGMA journal_mode=WAL")


# --- session helpers ---

def _tour_to_db(value: Any) -> Optional[str]:
    if not isinstance(value, dict) or not value:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _tour_from_db(raw: Any) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None

def set_session(chat_id: int, data: Dict[str, Any]) -> None:
    now = int(time.time())
    with _db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO sessions (chat_id, state, destination, origin, dates, people,
                                  kids, kids_ages, infants, budget, budget_scope, phone,
                                  needs_consultation, selected_tour, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                state=excluded.state, destination=excluded.destination,
                origin=excluded.origin,
                dates=excluded.dates, people=excluded.people,
                kids=excluded.kids, kids_ages=excluded.kids_ages,
                infants=excluded.infants,
                budget=excluded.budget, budget_scope=excluded.budget_scope,
                phone=excluded.phone,
                needs_consultation=excluded.needs_consultation,
                selected_tour=excluded.selected_tour,
                updated_at=excluded.updated_at
        """, (chat_id, data.get("state", ""), data.get("destination"),
              data.get("origin"),
              data.get("dates"), data.get("people"),
              data.get("kids"), _ages_to_db(data.get("kids_ages")),
              data.get("infants"), data.get("budget"),
              data.get("budget_scope"), data.get("phone"),
              int(bool(data.get("needs_consultation"))),
              _tour_to_db(data.get("selected_tour")),
              data.get("updated_at", now)))


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
                people, kids, kids_ages, infants, budget, budget_scope, phone,
                needs_consultation, selected_tour, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                first_name or None,
                username or None,
                info.get("destination"),
                info.get("origin"),
                info.get("dates"),
                info.get("people"),
                info.get("kids"),
                _ages_to_db(info.get("kids_ages")),
                info.get("infants"),
                info.get("budget"),
                info.get("budget_scope"),
                phone,
                int(bool(info.get("needs_consultation"))),
                _tour_to_db(info.get("selected_tour")),
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


def has_completed_lead(chat_id: int) -> bool:
    """Whether the user has a saved request and should see the repeat-flow hint."""
    with _db_cursor() as cur:
        cur.execute("SELECT 1 FROM leads WHERE chat_id = ? LIMIT 1", (chat_id,))
        return cur.fetchone() is not None


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


# Клиенты, которые сами сообщили, что inline-клавиатуру не показывают. VK
# присылает client_info с каждым message_new (с версии API 5.103), и его
# документация прямо просит слать то, что собеседник способен отобразить.
# Множество, а не «список поддерживающих»: неизвестный клиент считается
# современным, иначе одно пропущенное поле лишит кнопок всех.
_NO_INLINE: set = set()


def _downgrade_if_needed(user_id: int, keyboard: str) -> str:
    """Старому клиенту отдать обычную клавиатуру вместо inline.

    Лучше кнопки, которые сворачиваются, чем сообщение вообще без кнопок.
    """
    if user_id not in _NO_INLINE:
        return keyboard
    try:
        data = json.loads(keyboard)
    except (TypeError, ValueError):
        return keyboard
    if not data.get("inline"):
        return keyboard
    data["inline"] = False
    return json.dumps(data)


def _keyboard_for_state(user_id: int) -> Optional[str]:
    """Клавиатура текущего шага — чтобы любое сообщение вело дальше.

    Клиент жаловался, что кнопки то есть, то нет. Так и было: клавиатуру
    передавали руками, и два десятка сообщений уходили вообще без неё —
    человек оставался в диалоге без единого способа продолжить, кроме как
    угадать нужное слово. Строится по состоянию, а не по месту вызова:
    забыть аргумент можно, забыть состояние — нет.
    """
    state = (user_data.get(user_id) or {}).get("state")
    if state is None:
        # Диалога нет: единственный осмысленный следующий шаг — начать.
        return _soft_start_keyboard()
    builder = _STATE_KEYBOARDS.get(state)
    return builder() if builder else _nav_keyboard()


def send_message(
    user_id: int,
    text: str,
    keyboard: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Send a text message via VK messages.send."""
    if not VK_ACCESS_TOKEN:
        logger.error("VK_ACCESS_TOKEN not set — cannot send message")
        return None
    if keyboard is None and user_id != ADMIN_ID:
        # Админу кнопки клиента ни к чему: он получает уведомления и ответы на
        # команды, а не проходит воронку.
        keyboard = _keyboard_for_state(user_id)
    params: Dict[str, Any] = {
        "user_id": user_id,
        "message": text,
        "random_id": random.randint(0, 2**31),
    }
    if keyboard:
        params["keyboard"] = _downgrade_if_needed(user_id, keyboard)
    return _vk_api("messages.send", **params)


_vk_photo_cache: Dict[str, str] = {}
_vk_photo_cache_lock = threading.Lock()


def _upload_vk_message_photo(user_id: int, url: str) -> str:
    """Upload one trusted Tourvisor image to VK and return owner_id_photo_id."""
    if not TOURVISOR_CAROUSEL_IMAGES or not str(url or "").startswith(("http://", "https://")):
        return ""
    with _vk_photo_cache_lock:
        cached = _vk_photo_cache.get(url)
    if cached:
        return cached
    try:
        image = http_session.get(url, timeout=10)
        image.raise_for_status()
        content_type = image.headers.get("Content-Type", "image/jpeg").split(";", 1)[0]
        if not content_type.startswith("image/") or len(image.content) > 10 * 1024 * 1024:
            return ""
        server = _vk_api("photos.getMessagesUploadServer", peer_id=user_id) or {}
        upload_url = server.get("upload_url")
        if not upload_url:
            return ""
        uploaded = http_session.post(
            upload_url,
            files={"photo": ("tour.jpg", image.content, content_type)},
            timeout=HTTP_TIMEOUT,
        )
        uploaded.raise_for_status()
        payload = uploaded.json()
        saved = _vk_api(
            "photos.saveMessagesPhoto",
            server=payload.get("server"),
            photo=payload.get("photo"),
            hash=payload.get("hash"),
        )
        if not isinstance(saved, list) or not saved:
            return ""
        photo_id = f"{saved[0]['owner_id']}_{saved[0]['id']}"
        with _vk_photo_cache_lock:
            if len(_vk_photo_cache) >= 128:
                _vk_photo_cache.pop(next(iter(_vk_photo_cache)))
            _vk_photo_cache[url] = photo_id
        return photo_id
    except Exception as exc:
        logger.info("VK carousel photo skipped: %s", exc)
        return ""


def _compact_tour_price(offer: Dict[str, Any]) -> str:
    try:
        amount = int(offer.get("price") or 0) + int(offer.get("fuel_charge") or 0)
    except (TypeError, ValueError):
        amount = 0
    currency = str(offer.get("currency") or "RUB").upper()
    suffix = "₽" if currency in ("RUB", "RUR") else currency
    return f"{amount:,}".replace(",", " ") + f" {suffix}"


def send_tour_carousel(
    user_id: int,
    offers: List[Dict[str, Any]],
    offset: int,
    active_check: Optional[Callable[[], bool]] = None,
) -> bool:
    """Send a native VK carousel. False lets the caller use a text fallback."""
    elements = []
    for local_index, offer in enumerate(offers):
        number = offset + local_index + 1
        title = f"{offer.get('hotel') or 'Отель'}"
        category = int(offer.get("category") or 0)
        if category:
            title += f" {category}★"
        bits = [str(offer.get("region") or "").strip()]
        if offer.get("nights"):
            bits.append(f"{offer['nights']} ночей")
        if offer.get("meal"):
            bits.append(_tourvisor.meal_label(offer["meal"]))
        bits.append("от " + _compact_tour_price(offer))
        element: Dict[str, Any] = {
            "title": title[:80],
            "description": " · ".join(bit for bit in bits if bit)[:80],
            "buttons": [
                _btn(
                    f"Выбрать №{number}",
                    "primary",
                    {"command": "tour_select", "number": number,
                     "tour_id": offer.get("tour_id") or ""},
                )
            ],
        }
        photo_id = _upload_vk_message_photo(user_id, str(offer.get("picture_url") or ""))
        if photo_id:
            element["photo_id"] = photo_id
            element["action"] = {"type": "open_photo"}
        elements.append(element)
    if not elements:
        return False
    if active_check is not None and not active_check():
        return False
    template = json.dumps({"type": "carousel", "elements": elements}, ensure_ascii=False)
    response = _vk_api(
        "messages.send",
        user_id=user_id,
        message="Актуальные варианты по вашей заявке",
        template=template,
        random_id=random.randint(0, 2**31),
    )
    return response is not None


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

def _keyboard(
    rows: List[List[Dict[str, Any]]],
    one_time: bool = False,
    inline: bool = True,
) -> str:
    """Собрать JSON клавиатуры VK.

    inline по умолчанию, потому что обычная клавиатура живёт ПОД полем ввода и
    сворачивается, стоит пользователю коснуться поля, чтобы что-то напечатать.
    Возвращается она кнопкой с четырьмя точками, о которой никто не догадывается
    — со стороны выглядит так, будто бот потерял кнопки.

    Inline-клавиатура прикреплена к самому сообщению и остаётся в истории. Цена
    — жёсткий лимит: 10 кнопок, до 6 рядов по 5. Все клавиатуры этого бота в
    него укладываются (максимум 9), и тест это стережёт.
    """
    return json.dumps({
        "one_time": one_time,
        "inline": inline,
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
    rows.append([_btn(DIRECTION_UNDECIDED_LABEL, "secondary")])
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


def _kids_ages_keyboard() -> str:
    return _nav_keyboard([_btn(NO_KIDS_BUTTON_TEXT, "positive")])


def _budget_keyboard() -> str:
    labels = [label for label, _ in BUDGET_PRESETS] + [BUDGET_CUSTOM_LABEL]
    rows = _chunk_buttons(labels, "primary", 2)
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary")])
    rows.append([_btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _review_keyboard() -> str:
    rows: List[List[Dict[str, Any]]] = []
    if TOURVISOR_ENABLED:
        rows.append([_btn(TOUR_SEARCH_BUTTON_TEXT, "positive")])
        rows.append([_btn(TOUR_SEND_MANAGER_TEXT, "secondary")])
    else:
        rows.append([_btn(REVIEW_CONFIRM_TEXT, "positive")])
    rows.extend([
        [_btn(CONTACT_OTHER_TEXT, "secondary")],
        [_btn(REVIEW_EDIT_DATES_TEXT, "secondary")],
        [_btn(REVIEW_EDIT_BUDGET_TEXT, "secondary")],
        [_btn(BACK_BUTTON_TEXT, "secondary"), _btn(CANCEL_BUTTON_TEXT, "negative")],
    ])
    return _keyboard(rows)


def _tour_results_keyboard(
    select_numbers: Optional[List[int]] = None,
    selected: bool = False,
) -> str:
    rows: List[List[Dict[str, Any]]] = []
    if select_numbers:
        rows.append([
            _btn(f"№{number}", "primary", {"command": "tour_select", "number": number})
            for number in select_numbers
        ])
    rows.append([
        _btn(TOUR_CHEAPER_TEXT, "secondary"),
        _btn(TOUR_BETTER_TEXT, "secondary"),
    ])
    rows.append([
        _btn(TOUR_ALL_INCLUSIVE_TEXT, "secondary"),
        _btn(TOUR_MORE_BUTTON_TEXT, "secondary"),
    ])
    send_label = TOUR_SEND_SELECTED_TEXT if selected else TOUR_SEND_MANAGER_TEXT
    rows.append([_btn(send_label, "positive")])
    rows.append([
        _btn(TOUR_EDIT_DATES_TEXT, "secondary"),
        _btn(TOUR_EDIT_BUDGET_TEXT, "secondary"),
    ])
    return _keyboard(rows)


def _selected_tour_keyboard() -> str:
    return _keyboard([
        [_btn(TOUR_SEND_SELECTED_TEXT, "positive")],
        [_btn(TOUR_SIMILAR_TEXT, "primary")],
        [_btn(TOUR_COMPARE_TEXT, "secondary")],
        [_btn(CONTACT_OTHER_TEXT, "secondary")],
        [_btn(REVIEW_EDIT_DATES_TEXT, "secondary")],
        [_btn(REVIEW_EDIT_BUDGET_TEXT, "secondary")],
    ])


def _contact_keyboard() -> str:
    return _keyboard([
        [_btn(CONTACT_VK_CHAT_LABEL, "positive")],
        [_btn(CONTACT_PHONE_TEXT, "primary")],
        [_btn(CONTACT_MAX_TEXT, "primary")],
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
    # Намеренно НЕ inline: скрывать в сообщении нечего, а вот убрать клавиатуру
    # под полем ввода надо — у клиентов, общавшихся с прежней версией, она там
    # осталась висеть.
    return _keyboard([], one_time=True, inline=False)


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
        f"🌴 Отлично{name}! Давайте подберём тур.\n"
        "Шаг 1 из 6 · направление\n\n"
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
            # Раньше здесь пряталась клавиатура — сообщение называло кнопку и
            # тут же её убирало.
            keyboard=_soft_start_keyboard(),
        )
    else:
        send_message(user_id, f"Сейчас нет активной заявки.\n\n{HINT_START}")


def _origin_keyboard() -> str:
    rows = _chunk_buttons([label for label, _ in ORIGIN_PRESETS], "primary", 2)
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary"),
                 _btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _ask_origin(user_id: int) -> None:
    send_message(
        user_id,
        "Шаг 2 из 6 · город вылета\n\n"
        "🛫 Откуда вылетаете?\n\n"
        "Нужно, чтобы посчитать перелёт — цена сильно зависит от города.",
        keyboard=_origin_keyboard(),
    )


def _ask_dates(user_id: int) -> None:
    send_message(
        user_id,
        "Шаг 3 из 6 · даты\n\n"
        "📅 Когда планируете поездку?\n\n"
        "Кнопка или свои даты (например: 15-22 июня):",
        keyboard=_dates_keyboard(),
    )


def _ask_people(user_id: int, dates: Optional[str] = None) -> None:
    dates_prefix = f"📅 Понял: {dates}\n\n" if dates else ""
    send_message(
        user_id,
        dates_prefix + "Шаг 4 из 6 · состав туристов\n\n"
        "👥 Сколько взрослых поедет?\n\n"
        "Возраст детей спрошу следующим вопросом.\n"
        "Кнопка или число 1–50:",
        keyboard=_people_keyboard(),
    )


def _ask_kids_ages(user_id: int) -> None:
    """Возрасты детей одним числовым ответом.

    Отдельный вопрос «дети до 12 едут?» убран: он спрашивал то, что и так
    видно из возрастов.
    """
    send_message(
        user_id,
        "Шаг 4 из 6 · состав туристов\n\n"
        "🎂 Напишите возраст каждого ребёнка\n\n"
        "Возраст детей укажите через запятую: 5, 9.\n"
        "Малыша можно указать словами «до года».\n"
        "Если детей нет — нажмите кнопку ниже.",
        keyboard=_kids_ages_keyboard(),
    )


def _ask_budget(user_id: int, party: Optional[str] = None) -> None:
    party_prefix = f"👥 Записал: {party}\n\n" if party else ""
    send_message(
        user_id,
        party_prefix + "Шаг 5 из 6 · бюджет\n\n"
        "💰 Общий бюджет на всю поездку (примерно, ₽)\nКнопка или своя сумма:",
        keyboard=_budget_keyboard(),
    )


def _ask_contact(user_id: int) -> None:
    send_message(
        user_id,
        "Шаг 6 из 6 · способ связи\n\n"
        "📞 Как удобнее связаться?\n\n"
        "Можно просто VK (этот чат) — телефон не обязателен.\n"
        "Или телефон / MAX.",
        keyboard=_contact_keyboard(),
    )


# Персональная ссылка MAX — единственный надёжный идентификатор помимо номера:
# @никнеймов у личных профилей нет.
_MAX_LINK = re.compile(r"(?:https?://)?(?:www\.)?max\.ru/u/[\w-]+", re.I)


def _ask_max_contact(user_id: int) -> None:
    send_message(
        user_id,
        "🟣 Напишите номер телефона (+7…) или ссылку на ваш профиль MAX.\n\n"
        f"Ссылку можно взять так: {MAX_PROFILE_HINT}.",
        keyboard=_nav_keyboard(),
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
    if dest == DIRECTION_UNDECIDED_LABEL:
        info["destination"] = UNDECIDED_DESTINATION
        info["needs_consultation"] = True
        info["state"] = STATE_ORIGIN
        _ask_origin(user_id)
        return
    if dest.lower() == "другое":
        send_message(user_id, "✍️ Напишите ваше направление:", keyboard=_nav_keyboard())
        return
    info["destination"] = dest
    info["state"] = STATE_ORIGIN
    _ask_origin(user_id)


def _step_origin(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    raw_city = (text or "").strip()
    city = dict(ORIGIN_PRESETS).get(raw_city, raw_city)
    if city.lower() in ("другой город", "другое"):
        send_message(user_id, "✍️ Напишите город вылета:", keyboard=_nav_keyboard())
        return
    if not city:
        _ask_origin(user_id)
        return
    if city.strip().lower() == str(info.get("destination", "")).strip().lower():
        # Same city both ends: the search returns nothing and the client
        # silently gets fallback text instead of prices.
        send_message(
            user_id,
            f"🤔 {city} — это и есть ваше направление.\n\n"
            "Из какого города вылетаете?",
            keyboard=_origin_keyboard(),
        )
        return
    info["origin"] = city
    info["state"] = STATE_DATES
    _ask_dates(user_id)


def _step_dates(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    raw = (text or "").strip()
    if raw in (DATE_CUSTOM_LABEL, "свои даты"):
        send_message(
            user_id,
            "✍️ Напишите даты обычным сообщением\n\n"
            "Например: 15-22 сентября или с 3 по 10 октября.\n"
            "Просто отправьте текст в чат ↓",
            keyboard=_nav_keyboard(),
        )
        return
    preset_map = {label: val for label, val in DATE_PRESETS}
    if raw in preset_map:
        raw = preset_map[raw]
    if not raw:
        _ask_dates(user_id)
        return

    # Read back what was understood. Unparseable text is also unusable for the
    # flight search, and silence after the keyboard shrinks to Back/Cancel is
    # exactly what made this step feel broken to the first real user.
    depart, ret = _tutu.resolve_dates(raw)
    if not depart:
        send_message(
            user_id,
            "🤔 Не разобрал эти даты.\n\n"
            "Напишите так: 15-22 сентября\n"
            "или выберите примерный период кнопкой.",
            keyboard=_dates_keyboard(),
        )
        return

    info["dates"] = raw
    info["state"] = STATE_PEOPLE
    _ask_people(user_id, _human_dates(depart, ret))


def _human_dates(depart: str, ret: Optional[str] = None) -> str:
    """ISO → «15 сентября» / «15–22 сентября», for reading back to the client."""
    months = ("января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря")
    try:
        d = datetime.strptime(depart, "%Y-%m-%d")
    except (TypeError, ValueError):
        return depart or ""
    out = f"{d.day} {months[d.month - 1]}"
    if ret:
        try:
            r = datetime.strptime(ret, "%Y-%m-%d")
        except (TypeError, ValueError):
            return out
        if r.month == d.month:
            out = f"{d.day}–{r.day} {months[d.month - 1]}"
        else:
            out = f"{d.day} {months[d.month - 1]} – {r.day} {months[r.month - 1]}"
    return out


def _step_people(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    ok, value = validate_people(text)
    if not ok:
        send_message(
            user_id,
            "Сколько ВЗРОСЛЫХ? Число от 1 до 50 или «5+» — удобнее кнопкой.",
            keyboard=_people_keyboard(),
        )
        return
    info["people"] = value
    info["state"] = STATE_KIDS_AGES
    _ask_kids_ages(user_id)


def _parse_choice(raw: str, options: List[str], none_label: str) -> Optional[int]:
    """Read a count from a button label. None means unrecognised."""
    value = (raw or "").strip()
    if value == none_label or value.lower() in ("нет", "без детей"):
        return 0
    if value not in options:
        return None
    digits = "".join(c for c in value if c.isdigit())
    return int(digits) if digits else 0


def _step_kids_ages(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    raw = "0" if (text or "").strip() == NO_KIDS_BUTTON_TEXT else (text or "")
    ok, ages, problem = parse_kids_ages(raw)
    if not ok:
        send_message(user_id, problem)
        return
    info["kids_ages"] = ages
    _, info["kids"], info["infants"] = party_bands(info)
    info["state"] = STATE_BUDGET
    _ask_budget(user_id, _party_text(info))


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
        info["budget_open_ended"] = False
        info["budget_scope"] = "total"
        info["state"] = STATE_REVIEW
        _ask_review(user_id)
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
    info["budget_open_ended"] = False
    info["budget_scope"] = "total"
    info["state"] = STATE_REVIEW
    _ask_review(user_id)


def _ask_review(user_id: int) -> None:
    info = user_data.get(user_id, {})
    consultation = "\n💬 Нужна консультация по направлению." if info.get("needs_consultation") else ""
    budget_prefix = "От" if info.get("budget_open_ended") else "До"
    budget_suffix = "на всю поездку" if info.get("budget_scope") == "total" else "на человека"
    send_message(
        user_id,
        "Проверьте заявку:\n\n"
        f"📍 {info.get('destination', '—')}\n"
        f"🛫 Вылет: {info.get('origin', '—')}\n"
        f"📅 Даты: {info.get('dates', '—')}\n"
        f"👥 {_party_text(info)}\n"
        f"💰 {budget_prefix} {info.get('budget', '—')} ₽ {budget_suffix}"
        f"{consultation}\n\n"
        "Всё верно?",
        keyboard=_review_keyboard(),
    )


def _tour_search_worker(user_id: int, marker: str, snapshot: Dict[str, Any]) -> None:
    """Search in the background and only answer while this review is current."""
    result = _tourvisor.search_tours(
        _tourvisor_settings(), http_session, snapshot, log=logger,
    )
    with _lock:
        live = user_data.get(user_id)
        if live is None or live.get("_tour_search_marker") != marker:
            return
        live.pop("_tour_searching", None)
        live.pop("_tour_search_marker", None)
        if live.get("state") != STATE_REVIEW:
            return

    if result.offers:
        with _lock:
            live = user_data.get(user_id)
            if live is None or live.get("state") != STATE_REVIEW:
                return
            offers = [offer.__dict__.copy() for offer in result.offers]
            live["_tour_offers_base"] = offers
            live["_tour_offers"] = list(offers)
            live["_tour_page"] = 0
            live.pop("selected_tour", None)
        _send_tour_results_page(user_id, 0)
        return

    logger.info("VK Tourvisor search returned no offers for %s: %s", user_id, result.error)
    send_message(
        user_id,
        "По заданным параметрам готовых вариантов сейчас не нашлось.\n\n"
        "Можно изменить даты или бюджет, либо отправить заявку — менеджер "
        "проверит чартеры и предложения, которых нет в автоматическом поиске.",
        keyboard=_review_keyboard(),
    )


def _tour_results_active(user_id: int) -> bool:
    with _lock:
        live = user_data.get(user_id)
        return bool(live and live.get("state") == STATE_REVIEW and live.get("_tour_offers"))


def _send_tour_results_page(user_id: int, page: int) -> None:
    with _lock:
        live = user_data.get(user_id)
        if live is None or live.get("state") != STATE_REVIEW:
            return
        pool = list(live.get("_tour_offers") or [])
        if not pool:
            send_message(user_id, "Сначала нажмите «Показать варианты».", keyboard=_review_keyboard())
            return
        page_count = max(1, (len(pool) + 2) // 3)
        page = page % page_count
        offset = page * 3
        offers = pool[offset:offset + 3]
        live["_tour_page"] = page

    active_check = lambda: _tour_results_active(user_id)
    carousel_sent = send_tour_carousel(user_id, offers, offset, active_check=active_check)
    if not active_check():
        return
    with _lock:
        selected = bool((user_data.get(user_id) or {}).get("selected_tour"))
    numbers = [] if carousel_sent else list(range(offset + 1, offset + len(offers) + 1))
    keyboard = _tour_results_keyboard(numbers, selected=selected)
    if not carousel_sent:
        fallback = _tourvisor.SearchResult(
            offers=[_tourvisor.TourOffer(**offer) for offer in offers]
        )
        send_message(
            user_id,
            _tourvisor.format_client_message(fallback, start_index=offset + 1),
            keyboard=keyboard,
        )
        return
    send_message(
        user_id,
        f"Показаны варианты {offset + 1}–{offset + len(offers)} из {len(pool)}.\n"
        "Выберите тур или передайте подборку менеджеру.",
        keyboard=keyboard,
    )


def _selected_tour_summary(info: Dict[str, Any]) -> str:
    offer = info.get("selected_tour")
    if not isinstance(offer, dict):
        return ""
    title = str(offer.get("hotel") or "Выбранный тур")
    category = int(offer.get("category") or 0)
    if category:
        title += f" {category}★"
    parts = [f"🏨 {title}"]
    if offer.get("region"):
        parts.append(f"📍 {offer['region']}")
    trip_details = []
    if offer.get("date"):
        trip_details.append(_tourvisor.display_date(offer["date"]))
    if offer.get("nights"):
        trip_details.append(f"{offer['nights']} ночей")
    if trip_details:
        parts.append("📅 " + " · ".join(trip_details))
    if offer.get("meal"):
        parts.append(f"🍽 {_tourvisor.meal_label(offer['meal'])}")
    if offer.get("room"):
        parts.append(f"🛏 Номер: {offer['room']}")
    parts.append(f"💰 {_compact_tour_price(offer)} за тур")
    if offer.get("operator"):
        parts.append(f"Туроператор: {offer['operator']}")
    if offer.get("tour_id"):
        parts.append(f"ID предложения: {offer['tour_id']}")
    return "\n".join(parts)


def _budget_summary(info: Dict[str, Any]) -> str:
    suffix = " на всю поездку" if info.get("budget_scope") == "total" else ""
    prefix = "от " if info.get("budget_open_ended") else "до "
    try:
        amount = f"{int(info.get('budget')):,}".replace(",", " ")
    except (TypeError, ValueError):
        amount = str(info.get("budget", "?"))
    return f"{prefix}{amount} ₽{suffix}"


def _select_tour(user_id: int, number: int) -> None:
    with _lock:
        live = user_data.get(user_id)
        pool = list((live or {}).get("_tour_offers") or [])
        if live is None or live.get("state") != STATE_REVIEW or not (1 <= number <= len(pool)):
            offer = None
        else:
            offer = dict(pool[number - 1])
            live["selected_tour"] = offer
    if offer is None:
        send_message(user_id, "Этот вариант уже недоступен. Запустите поиск ещё раз.", keyboard=_review_keyboard())
        return
    send_message(
        user_id,
        f"✅ Вы выбрали вариант №{number}:\n\n{_selected_tour_summary({'selected_tour': offer})}\n\n"
        "Перед бронированием менеджер проверит наличие и окончательную цену.",
        keyboard=_selected_tour_keyboard(),
    )


def _offer_total_price(offer: Dict[str, Any]) -> int:
    try:
        return int(offer.get("price") or 0) + int(offer.get("fuel_charge") or 0)
    except (TypeError, ValueError):
        return 10**15


def _base_tour_offers(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(info.get("_tour_offers_base") or info.get("_tour_offers") or [])


def _show_tour_view(
    user_id: int,
    offers: List[Dict[str, Any]],
    intro: str,
    *,
    keep_selected: bool = False,
) -> None:
    with _lock:
        live = user_data.get(user_id)
        if live is None or live.get("state") != STATE_REVIEW:
            return
        live["_tour_offers"] = list(offers)
        live["_tour_page"] = 0
        if not keep_selected:
            live.pop("selected_tour", None)
    send_message(user_id, intro, keyboard=_hide_keyboard())
    _send_tour_results_page(user_id, 0)


def _apply_tour_filter(user_id: int, mode: str) -> None:
    with _lock:
        live = user_data.get(user_id)
        base = _base_tour_offers(live or {})
    if not base:
        send_message(user_id, "Сначала нажмите «Показать варианты».", keyboard=_review_keyboard())
        return

    if mode == "cheaper":
        offers = sorted(base, key=_offer_total_price)
        intro = "💰 Сначала показываю самые доступные варианты."
    elif mode == "better":
        offers = sorted(
            base,
            key=lambda offer: (-int(offer.get("category") or 0), _offer_total_price(offer)),
        )
        intro = "⭐ Сначала показываю отели с более высокой категорией."
    else:
        offers = [offer for offer in base if _tourvisor.is_all_inclusive(offer.get("meal"))]
        if not offers:
            send_message(
                user_id,
                "Среди найденных туров нет вариантов «всё включено». "
                "Попробуйте другой бюджет или даты.",
                keyboard=_tour_results_keyboard(),
            )
            return
        offers.sort(key=_offer_total_price)
        intro = "🍽 Оставил только варианты с питанием «всё включено»."
    _show_tour_view(user_id, offers, intro)


def _show_similar_tours(user_id: int) -> None:
    with _lock:
        live = user_data.get(user_id) or {}
        selected = live.get("selected_tour")
        base = _base_tour_offers(live)
    if not isinstance(selected, dict):
        send_message(user_id, "Сначала выберите один из туров.", keyboard=_tour_results_keyboard())
        return
    selected_id = str(selected.get("tour_id") or "")
    selected_hotel = str(selected.get("hotel") or "").casefold()
    candidates = [
        offer for offer in base
        if not (
            (selected_id and str(offer.get("tour_id") or "") == selected_id)
            or str(offer.get("hotel") or "").casefold() == selected_hotel
        )
    ]
    region = str(selected.get("region") or "").casefold()
    same_region = [offer for offer in candidates if str(offer.get("region") or "").casefold() == region]
    if same_region:
        candidates = same_region
    category = int(selected.get("category") or 0)
    price = _offer_total_price(selected)
    candidates.sort(key=lambda offer: (
        abs(int(offer.get("category") or 0) - category),
        abs(_offer_total_price(offer) - price),
    ))
    if not candidates:
        send_message(user_id, "Похожих отелей в этой выдаче больше нет.", keyboard=_selected_tour_keyboard())
        return
    _show_tour_view(
        user_id,
        candidates,
        "🏨 Вот наиболее похожие отели из найденной подборки.",
        keep_selected=True,
    )


def _compare_tours(user_id: int) -> None:
    with _lock:
        live = user_data.get(user_id) or {}
        selected = live.get("selected_tour")
        base = _base_tour_offers(live)
    if not isinstance(selected, dict):
        send_message(user_id, "Сначала выберите один из туров.", keyboard=_tour_results_keyboard())
        return
    selected_id = str(selected.get("tour_id") or "")
    selected_hotel = str(selected.get("hotel") or "").casefold()
    alternatives = [
        offer for offer in base
        if not (
            (selected_id and str(offer.get("tour_id") or "") == selected_id)
            or str(offer.get("hotel") or "").casefold() == selected_hotel
        )
    ]
    alternatives.sort(key=_offer_total_price)
    comparison = [dict(selected)] + alternatives[:2]
    _show_tour_view(
        user_id,
        comparison,
        "⚖️ Сравните выбранный тур с двумя доступными альтернативами.",
        keep_selected=True,
    )


def _start_tour_search(user_id: int, info: Dict[str, Any]) -> None:
    if not TOURVISOR_ENABLED or not TOURVISOR_TOKEN:
        send_message(
            user_id,
            "Автоматический поиск сейчас недоступен. Отправьте заявку — менеджер подберёт варианты.",
            keyboard=_review_keyboard(),
        )
        return
    if info.get("needs_consultation"):
        send_message(
            user_id,
            "Чтобы искать автоматически, сначала нужно выбрать направление. "
            "Можно отправить заявку — менеджер поможет определиться.",
            keyboard=_review_keyboard(),
        )
        return
    if info.get("_tour_searching"):
        send_message(user_id, "Поиск уже идёт — обычно это занимает 10–20 секунд.")
        return

    marker = f"{time.time_ns()}-{random.randint(1000, 9999)}"
    info.pop("selected_tour", None)
    info.pop("_tour_offers", None)
    info.pop("_tour_offers_base", None)
    info.pop("_tour_page", None)
    info["_tour_searching"] = True
    info["_tour_search_marker"] = marker
    snapshot = dict(info)
    send_message(
        user_id,
        "🔎 Ищу актуальные туры у туроператоров. Обычно это занимает 10–20 секунд…",
    )
    if SYNC_COMPLETION:
        _tour_search_worker(user_id, marker, snapshot)
    else:
        threading.Thread(
            target=_tour_search_worker,
            args=(user_id, marker, snapshot),
            daemon=True,
            name=f"vk-tour-search-{user_id}",
        ).start()


def _step_review(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    if text == TOUR_SEARCH_BUTTON_TEXT:
        _start_tour_search(user_id, info)
        return
    selected_match = re.fullmatch(
        r"(?:✅\s*)?(?:(?:Выбрать|Тур)\s*)?№?\s*(\d+)",
        (text or "").strip(),
        re.I,
    )
    if selected_match:
        _select_tour(user_id, int(selected_match.group(1)))
        return
    if text == TOUR_MORE_BUTTON_TEXT:
        _send_tour_results_page(user_id, int(info.get("_tour_page") or 0) + 1)
        return
    if text == TOUR_CHEAPER_TEXT:
        _apply_tour_filter(user_id, "cheaper")
        return
    if text == TOUR_BETTER_TEXT:
        _apply_tour_filter(user_id, "better")
        return
    if text == TOUR_ALL_INCLUSIVE_TEXT:
        _apply_tour_filter(user_id, "all_inclusive")
        return
    if text == TOUR_SIMILAR_TEXT:
        _show_similar_tours(user_id)
        return
    if text == TOUR_COMPARE_TEXT:
        _compare_tours(user_id)
        return
    if text in (REVIEW_CONFIRM_TEXT, TOUR_SEND_MANAGER_TEXT, TOUR_SEND_SELECTED_TEXT):
        info.pop("_tour_searching", None)
        info.pop("_tour_search_marker", None)
        info["contact_method"] = "vk"
        client_name = message.get("_user_name") or f"VK {user_id}"
        handle_completion(user_id, f"VK (чат id {user_id}) · {client_name}", message)
        return
    if text == CONTACT_OTHER_TEXT:
        info["state"] = STATE_CONTACT
        _ask_contact(user_id)
        return
    if text in (REVIEW_EDIT_DATES_TEXT, TOUR_EDIT_DATES_TEXT):
        info.pop("_tour_searching", None)
        info.pop("_tour_search_marker", None)
        info.pop("selected_tour", None)
        info.pop("_tour_offers", None)
        info.pop("_tour_offers_base", None)
        info["state"] = STATE_DATES
        _ask_dates(user_id)
        return
    if text in (REVIEW_EDIT_BUDGET_TEXT, TOUR_EDIT_BUDGET_TEXT):
        info.pop("_tour_searching", None)
        info.pop("_tour_search_marker", None)
        info.pop("selected_tour", None)
        info.pop("_tour_offers", None)
        info.pop("_tour_offers_base", None)
        info["state"] = STATE_BUDGET
        _ask_budget(user_id)
        return
    _ask_review(user_id)


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

    if t in (CONTACT_MAX_TEXT, "max", "макс", "мах"):
        info["contact_method"] = "max"
        info["state"] = STATE_MAX
        _ask_max_contact(user_id)
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


def _step_max(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    """Принять номер или ссылку на профиль MAX — @ника у личных профилей нет."""
    raw = (text or "").strip()

    link = _MAX_LINK.search(raw)
    if link:
        url = link.group(0)
        if not url.lower().startswith("http"):
            url = "https://" + url
        info["contact_method"] = "max"
        handle_completion(user_id, f"MAX {url}", message)
        return

    ok, phone = validate_phone(raw)
    if ok and phone:
        # Номер и есть основной способ найти человека в MAX. Префикс оставлен
        # намеренно: менеджер видит и номер, и то, где клиент ждёт сообщение.
        info["contact_method"] = "max"
        handle_completion(user_id, f"MAX {phone}", message)
        return

    if raw.startswith("@"):
        send_message(
            user_id,
            "В MAX у личных профилей нет @никнеймов — по ним человека не найти.\n"
            "Пришлите номер телефона (+7…) или ссылку вида max.ru/u/…",
            keyboard=_nav_keyboard(),
        )
        return

    send_message(
        user_id,
        "Нужен номер телефона (+7…) или ссылка на профиль MAX (max.ru/u/…).\n"
        f"Ссылка берётся так: {MAX_PROFILE_HINT}.",
        keyboard=_nav_keyboard(),
    )


# Клавиатура на каждое состояние. Рядом со STATE_HANDLERS намеренно: новый шаг
# добавляют сюда же, и «шаг без кнопок» становится заметным при чтении.
_STATE_KEYBOARDS: Dict[str, Callable[[], str]] = {
    STATE_CONSENT:     _consent_keyboard,
    STATE_DESTINATION: _dest_keyboard,
    STATE_ORIGIN:      _origin_keyboard,
    STATE_DATES:       _dates_keyboard,
    STATE_PEOPLE:      _people_keyboard,
    STATE_KIDS:        _kids_ages_keyboard,
    STATE_KIDS_AGES:   _kids_ages_keyboard,
    STATE_INFANTS:     _kids_ages_keyboard,
    STATE_BUDGET:      _budget_keyboard,
    STATE_REVIEW:      _review_keyboard,
    STATE_CONTACT:     _contact_keyboard,
    STATE_PHONE:       _nav_keyboard,
    STATE_MAX:         _nav_keyboard,
}

STATE_HANDLERS: Dict[str, Callable] = {
    STATE_CONSENT:     _step_consent,
    STATE_DESTINATION: _step_destination,
    STATE_ORIGIN:      _step_origin,
    STATE_DATES:       _step_dates,
    STATE_PEOPLE:      _step_people,
    # Вопрос про количество детей убран; сессии на нём отвечают уже на
    # следующий вопрос — про возрасты.
    STATE_KIDS:        _step_kids_ages,
    STATE_KIDS_AGES:   _step_kids_ages,
    # Sessions parked on the retired infants question land here on their next
    # reply; asking for ages is the right next thing either way.
    STATE_INFANTS:     _step_kids_ages,
    STATE_BUDGET:      _step_budget,
    STATE_REVIEW:      _step_review,
    STATE_CONTACT:     _step_contact,
    STATE_PHONE:       _step_phone,
    STATE_MAX:         _step_max,
    # Сессии, застрявшие на прежнем телеграм-шаге, возвращаются к выбору
    # способа связи: спрашивать у них @ник, которого в MAX нет, бессмысленно.
    "telegram_handle": _step_contact,
}

PREVIOUS_STATE: Dict[str, str] = {
    STATE_ORIGIN:      STATE_DESTINATION,
    STATE_DATES:       STATE_ORIGIN,
    STATE_PEOPLE:      STATE_DATES,
    STATE_KIDS:        STATE_PEOPLE,
    STATE_KIDS_AGES:   STATE_PEOPLE,
    STATE_INFANTS:     STATE_PEOPLE,
    STATE_BUDGET:      STATE_KIDS_AGES,
    STATE_REVIEW:      STATE_BUDGET,
    STATE_CONTACT:     STATE_REVIEW,
    STATE_PHONE:       STATE_CONTACT,
    STATE_MAX:         STATE_CONTACT,
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
    elif state in (STATE_KIDS, STATE_KIDS_AGES, STATE_INFANTS):
        _ask_kids_ages(user_id)
    elif state == STATE_BUDGET:
        _ask_budget(user_id)
    elif state == STATE_REVIEW:
        _ask_review(user_id)
    elif state == STATE_CONTACT:
        _ask_contact(user_id)
    elif state == STATE_PHONE:
        send_message(user_id, "📱 Укажите номер телефона (+7…):", keyboard=_nav_keyboard())
    elif state == STATE_MAX:
        _ask_max_contact(user_id)
    elif state == "telegram_handle":
        _ask_contact(user_id)
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
    selected = _selected_tour_summary(info)
    send_message(
        user_id,
        "✅ Заявка принята! Менеджер «АПРЕЛЬ тур» свяжется с вами.\n\n"
        f"📍 Направление: {info.get('destination', '?')}\n"
        + (f"🛫 Откуда: {info['origin']}\n" if info.get("origin") else "")
        + f"📅 Даты: {info.get('dates', '?')}\n"
        f"👥 Состав: {_party_text(info)}\n"
        f"💰 Бюджет: {_budget_summary(info)}\n"
        f"📞 Связь: {phone}\n"
        + (f"\n🎯 Выбранный вариант:\n{selected}\n" if selected else "")
        + "\n"
        "Спасибо, что выбрали нас 🌺",
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
    selected = _selected_tour_summary(info)
    text = (
        "🔔 Новая заявка (VK)!\n\n"
        f"От: {client_name or 'без имени'}\n"
        f"VK ID: {user_id}\n"
        f"📍 {info.get('destination', '?')}\n"
        + (f"🛫 Откуда: {info['origin']}\n" if info.get("origin") else "")
        + f"📅 {info.get('dates', '?')}\n"
        f"👥 {_party_text(info)}\n"
        f"💰 {_budget_summary(info)}\n"
        f"📞 Связь: {phone}"
        + (f"\n\n🎯 Выбранный тур:\n{selected}" if selected else "")
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
        selected = _selected_tour_summary(info)
        # Legacy: also ping ADMIN_ID inside VK (if it is a VK user id).
        send_message(
            ADMIN_ID,
            "🔔 Новая заявка (VK)!\n\n"
            f"От: {client_name or 'без имени'} (ID: {user_id})\n"
            f"📍 {info.get('destination', '?')}\n"
            + (f"🛫 Откуда: {info['origin']}\n" if info.get("origin") else "")
            + f"📅 {info.get('dates', '?')}\n"
            f"👥 {_party_text(info)}\n"
            f"💰 {_budget_summary(info)}\n"
            f"📞 Связь: {phone}"
            + (f"\n\n🎯 Выбранный тур:\n{selected}" if selected else ""),
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
    if not TUTU_ENABLED or info.get("needs_consultation"):
        return None
    # Bands come from the exact ages, so a 14-year-old is searched as an adult
    # and a one-year-old as an infant — which is what the airline will charge.
    _adults, _children, _infants = party_bands(info)
    try:
        return _tutu.search_offers(
            _tutu_settings(), http_session,
            destination=info.get("destination", ""),
            dates_raw=info.get("dates", ""),
            origin=info.get("origin", ""),
            people=_adults,
            kids=_children,
            infants=_infants,
            budget=info.get("budget"),
            budget_is_total=info.get("budget_scope") == "total",
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

        # The client already chose a complete package. Sending an unrelated
        # flight-only estimate or an AI placeholder after confirmation would
        # make the successful selection look as if it had been lost.
        if info.get("selected_tour"):
            return

        result = _tutu_search(info)
        client_text = ""
        if result and TUTU_SHOW_CLIENT:
            # VK renders no markup at all, so the HTML variant would show tags.
            client_text = _tutu.format_client_message(result, markup="plain")

        send_typing(user_id)
        if client_text:
            send_message(user_id, client_text, keyboard=_hide_keyboard())
        else:
            # Tutu off or unavailable — the client still gets a suggestion.
            send_message(
                user_id,
                generate_ai_selection(
                    info.get("destination", ""), info.get("dates", ""),
                    info.get("people", ""), info.get("budget", ""),
                ),
                keyboard=_hide_keyboard(),
            )

        if result and TUTU_SHOW_ADMIN:
            _send_tutu_to_admin(user_id, result, client_name)
    except Exception as exc:
        logger.error("VK post-completion side effects failed for %s: %s", user_id, exc)


def handle_completion(user_id: int, phone: str, message: Dict[str, Any]) -> None:
    # VK can deliver separate, valid events almost simultaneously. Guarding
    # completion prevents duplicate leads and duplicate manager notifications.
    with _lock:
        live = user_data.get(user_id)
        if live is None or live.get("_completing"):
            logger.info("Concurrent VK completion ignored for user_id=%s", user_id)
            return
        live["_completing"] = True
        info = dict(live)
    info.pop("_completing", None)
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

FOLLOWUP_DELAY_HOURS = _env_int("FOLLOWUP_DELAY_HOURS", 3)


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
    # Явный способ вернуть кнопки словом. Клавиатура теперь приходит с каждым
    # сообщением, но человек, у которого кнопки «пропали», ищет команду, а не
    # догадывается написать что угодно.
    "кнопки": "menu", "меню": "menu", "продолжить": "menu", "где кнопки": "menu",
    "помощь": "help", "справка": "help",
    "политика": "privacy",
    "удалить": "delete",
    "аналитика": "analytics", "статистика": "analytics",
    "экспорт": "export", "заявки": "export",
    "рассылка": "broadcast",
    "напоминания": "followup",
}


def _remember_client_capabilities(user_id: int, event: Dict[str, Any]) -> None:
    """Прочитать client_info из message_new: умеет ли клиент inline-кнопки."""
    info = event.get("object", {}).get("client_info")
    if not isinstance(info, dict) or "inline_keyboard" not in info:
        return
    if info.get("inline_keyboard"):
        _NO_INLINE.discard(user_id)
    else:
        _NO_INLINE.add(user_id)


def _process_message(message: Dict[str, Any]) -> None:
    """Process one VK message_new event."""
    msg = message.get("object", {}).get("message", message.get("message", {}))
    user_id = msg.get("from_id") or msg.get("peer_id")
    if not user_id:
        return
    text = (msg.get("text") or "").strip()
    try:
        button_payload = json.loads(msg.get("payload") or "{}")
    except (TypeError, ValueError):
        button_payload = {}
    if button_payload.get("command") == "tour_select" and button_payload.get("number"):
        text = f"Выбрать №{button_payload['number']}"
    _remember_client_capabilities(user_id, message)

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
    if text == NEW_SELECTION_BUTTON_TEXT:
        command = "start"
    elif text == START_BUTTON_TEXT or text_lower in ("🚀 начать подбор", "начать подбор"):
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
                cur.execute("SELECT chat_id, destination, dates, people, budget, phone FROM leads ORDER BY created_at DESC LIMIT 50")
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
    if command == "menu":
        # Повторить текущий вопрос вместе с его кнопками. Ничего не меняет в
        # состоянии: человек застрял на шаге, а не хочет его пройти заново.
        state = (user_data.get(user_id) or {}).get("state")
        if state:
            _prompt_for_state(user_id, state)
        else:
            send_message(user_id, HINT_START, keyboard=_soft_start_keyboard())
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
        if has_completed_lead(user_id):
            # A manager now owns this conversation. Do not interrupt a normal
            # reply such as "спасибо" with the bot's repeat-selection prompt.
            # The explicit "Начать" command above still starts a new request.
            return
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
            d["kids_ages"] = _ages_from_db(d.get("kids_ages"))
            d["needs_consultation"] = bool(d.get("needs_consultation"))
            d["selected_tour"] = _tour_from_db(d.get("selected_tour"))
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
        "revision": _version.REVISION,
        "uptime_seconds": _version.uptime_seconds(),
    })


@app.route("/vk/webhook", methods=["POST"])
def vk_webhook() -> Any:
    """Handle VK Callback API events."""
    data = request.get_json(silent=True)
    if not data or "type" not in data:
        return "ok", 200

    event_type = data["type"]

    # VK's address-verification payload contains no secret. It must be
    # answered before validating regular event deliveries.
    if event_type == "confirmation":
        if VK_CONFIRMATION:
            return VK_CONFIRMATION, 200
        logger.warning("VK confirmation request but VK_CONFIRMATION not set")
        return "ok", 200

    if not VK_SECRET_KEY:
        logger.error("VK webhook rejected: VK_SECRET_KEY is not configured")
        return "Service unavailable", 503
    received_secret = data.get("secret", "")
    if not hmac.compare_digest(received_secret, VK_SECRET_KEY):
        logger.warning("VK webhook: invalid secret key")
        return "Forbidden", 403

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
