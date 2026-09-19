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
import base64
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
    STATE_NIGHTS,
    STATE_HOTEL,
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
from shared.vk_miniapp import build_open_app_button, create_blueprint
from shared.telegram_webapp import MiniAppValidationError, normalise_source_tag
from shared import tutu as _tutu
from shared import tourvisor as _tourvisor
from shared import travelata as _travelata
from shared import tour_providers as _tour_providers
from shared import version as _version
from shared.validation import (
    validate_phone, validate_people, validate_budget,
    parse_kids_ages, party_bands, party_text as _party_text,
    ages_to_db as _ages_to_db, ages_from_db as _ages_from_db,
)
from shared.templates import template_selection as _template_selection
from shared.privacy import consent_text as _shared_consent_text, privacy_text as _shared_privacy_text
from shared.log_privacy import correlation_id as _log_correlation
from shared.ai import generate_ai_selection as _shared_generate_ai
from shared.ai_provider import build_selection_provider
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
GROQ_MODEL        = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
REGCLOUD_API_KEY  = os.getenv("REGCLOUD_API_KEY", "")
REGCLOUD_BASE_URL = os.getenv("REGCLOUD_BASE_URL", "")
REGCLOUD_MODEL    = os.getenv("REGCLOUD_MODEL", "")
AI_MODE           = os.getenv("AI_MODE", "template").lower().strip()
PORT              = _env_int("VK_PORT", _env_int("PORT", 5100))
DATABASE_PATH     = os.getenv("VK_DATABASE_PATH", os.getenv("DATABASE_PATH", "vk_bot_state.sqlite"))
ADMIN_ID          = _env_int("ADMIN_ID", 0)
ADMIN_ERROR_ALERTS = os.getenv("ADMIN_ERROR_ALERTS", "true").lower().strip() in ("1", "true", "yes")
ERROR_ALERT_COOLDOWN = max(0, _env_int("ERROR_ALERT_COOLDOWN", 300))
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

LEAD_OWNER_NAME = os.getenv("LEAD_OWNER_NAME", "Наталья Ильина").strip() or "Наталья Ильина"
LEAD_OWNER_PHONE = os.getenv("LEAD_OWNER_PHONE", "+79021932923").strip() or "+79021932923"
LEAD_OWNER_VK_ID = _env_int("LEAD_OWNER_VK_ID", 112655584)
MANAGER_TOURVISOR_URL = os.getenv("MANAGER_TOURVISOR_URL", "https://pro.tourvisor.ru/").strip()
MANAGER_SLETAT_URL = os.getenv("MANAGER_SLETAT_URL", "https://sletat.ru/pro").strip()
MANAGER_QUIQUO_URL = os.getenv("MANAGER_QUIQUO_URL", "https://qui-quo.ru/").strip()

# MDT CRM (same env vars as Telegram bot)
MDT_ENABLED    = os.getenv("MDT_ENABLED", "false").lower().strip() in ("1", "true", "yes")
MDT_ACCOUNT    = os.getenv("MDT_ACCOUNT", "")
MDT_API_KEY    = os.getenv("MDT_API_KEY", "")
MDT_SOURCE     = os.getenv("VK_MDT_SOURCE", "VK Bot").strip() or "VK Bot"
MDT_BASE_URL   = os.getenv("MDT_BASE_URL", "")
MDT_MODE       = os.getenv("VK_MDT_MODE", os.getenv("MDT_MODE", "lead")).lower().strip()
MDT_NOTIFY_MANAGERS = os.getenv("MDT_NOTIFY_MANAGERS", "false").lower().strip() in ("1", "true", "yes")
MDT_MANAGER_IDS = [int(x.strip()) for x in os.getenv("MDT_MANAGER_IDS", "").split(",") if x.strip()]
MDT_REMINDER_ENABLED = os.getenv("MDT_REMINDER_ENABLED", "true").lower().strip() in ("1", "true", "yes")
try:
    MDT_REMINDER_DAYS = _env_int("MDT_REMINDER_DAYS", 1)
except (ValueError, TypeError):
    MDT_REMINDER_DAYS = 1
MDT_REMINDER_TEXT = os.getenv("MDT_REMINDER_TEXT", "Позвонить по заявке с VK-бота")
MDT_RETRY_ENABLED = os.getenv("VK_MDT_RETRY_ENABLED", "true").lower().strip() in ("1", "true", "yes")
MDT_RETRY_POLL_SECONDS = max(5, _env_int("VK_MDT_RETRY_POLL_SECONDS", 60))
MDT_RETRY_BASE_SECONDS = max(5, _env_int("VK_MDT_RETRY_BASE_SECONDS", 60))
MDT_RETRY_MAX_SECONDS = max(MDT_RETRY_BASE_SECONDS, _env_int("VK_MDT_RETRY_MAX_SECONDS", 3600))
MDT_RETRY_BATCH_SIZE = max(1, _env_int("VK_MDT_RETRY_BATCH_SIZE", 10))

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
if TOURVISOR_ENABLED and not TOURVISOR_TOKEN:
    logger.warning("VK Tourvisor requested but TOURVISOR_TOKEN is empty; disabling live search")
    TOURVISOR_ENABLED = False


def _tourvisor_jwt_expired(token: str) -> bool:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False
        payload_raw = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_raw).decode("utf-8"))
        exp = payload.get("exp")
        return isinstance(exp, (int, float)) and float(exp) <= time.time()
    except Exception:
        return False


if TOURVISOR_ENABLED and _tourvisor_jwt_expired(TOURVISOR_TOKEN):
    logger.warning("VK Tourvisor JWT is expired; disabling live search UI")
    TOURVISOR_ENABLED = False
TOURVISOR_BASE_URL = os.getenv(
    "TOURVISOR_BASE_URL", "https://api.tourvisor.ru/search/api/v1"
).strip()
TOURVISOR_TIMEOUT = _env_int("TOURVISOR_TIMEOUT", 15)
TOURVISOR_POLL_INTERVAL = _env_int("TOURVISOR_POLL_INTERVAL", 3)
TOURVISOR_MAX_WAIT = _env_int("TOURVISOR_MAX_WAIT", 30)
TOURVISOR_MAX_OFFERS = _env_int("TOURVISOR_MAX_OFFERS", 15)
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


# --- Alternative package-tour providers ------------------------------------
# Travelata partner API (June 2026+). Access is granted individually through
# Travelpayouts; keep credentials only in the server environment.
TRAVELATA_USERNAME = os.getenv("TRAVELATA_USERNAME", "").strip()
TRAVELATA_PASSWORD = os.getenv("TRAVELATA_PASSWORD", "").strip()
TRAVELATA_ENABLED = os.getenv(
    "VK_TRAVELATA_ENABLED",
    "true" if TRAVELATA_USERNAME and TRAVELATA_PASSWORD else "false",
).lower().strip() in ("1", "true", "yes")
TRAVELATA_BASE_URL = os.getenv(
    "TRAVELATA_BASE_URL", "https://api-gateway.travelata.ru"
).strip()
TRAVELATA_TIMEOUT = _env_int("TRAVELATA_TIMEOUT", 15)
TRAVELATA_MAX_OFFERS = _env_int("TRAVELATA_MAX_OFFERS", 15)


def _travelata_settings() -> "_travelata.TravelataSettings":
    return _travelata.TravelataSettings(
        enabled=TRAVELATA_ENABLED,
        username=TRAVELATA_USERNAME,
        password=TRAVELATA_PASSWORD,
        base_url=TRAVELATA_BASE_URL,
        timeout=TRAVELATA_TIMEOUT,
        max_offers=TRAVELATA_MAX_OFFERS,
    )


TOUR_PROVIDER_ORDER = tuple(
    name.strip().lower()
    for name in os.getenv("TOUR_PROVIDER_ORDER", "travelata,tourvisor").split(",")
    if name.strip()
)
TOUR_SEARCH_MAX_OFFERS = max(1, _env_int("TOUR_SEARCH_MAX_OFFERS", 15))


def _tour_provider_settings() -> "_tour_providers.ProviderSettings":
    return _tour_providers.ProviderSettings(
        order=TOUR_PROVIDER_ORDER,
        travelata=_travelata_settings(),
        tourvisor=_tourvisor_settings(),
    )


TOUR_SEARCH_ENABLED = _tour_provider_settings().enabled


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

POPULAR_DESTINATIONS = ["Турция", "Египет", "ОАЭ"]
DIRECTION_UNDECIDED_LABEL = "🌴 Не определился"
UNDECIDED_DESTINATION = "Не определился — нужна консультация"
DEST_HOT_TOURS_LABEL = "🔥 Горящие туры"
DEST_DIRECT_FLIGHTS_LABEL = "🛫 Прямые"
STATE_REVIEW = "review"

DATE_PRESETS: List[Tuple[str, str]] = [
    ("🔥 Ближ. 2 недели", "ближайшие 2 недели"),
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
    ("до 100 000 ₽", 100000),
    ("до 150 000 ₽", 150000),
    ("до 200 000 ₽", 200000),
    ("до 300 000 ₽", 300000),
]
PARTY_PRESET_2_ADULTS = "👫 2 взрослых"
PARTY_PRESET_1_ADULT = "👤 1 взрослый"
PARTY_PRESET_2_PLUS_1 = "👨‍👩‍👧 2 взр. + 1 реб."
PARTY_PRESET_2_PLUS_2 = "👨‍👩‍👧‍👦 2 взр. + 2 дет."
PARTY_PRESET_OTHER = "👥 Другой состав"

DATE_CUSTOM_LABEL = "✏️ Свои даты"
NIGHTS_PRESETS: List[Tuple[str, str]] = [
    ("6–7 ночей", "6-7"),
    ("8–9 ночей", "8-9"),
    ("10–12 ночей", "10-12"),
    ("13–14 ночей", "13-14"),
]
NIGHTS_CUSTOM_LABEL = "✏️ Своя длительность"
BUDGET_ANY_LABEL = "🤷 Любой бюджет"
BUDGET_CUSTOM_LABEL = "✏️ Свой бюджет"
CONTACT_VK_CHAT_LABEL = "💙 VK (этот чат)"
REVIEW_CONFIRM_TEXT = "✅ Отправить заявку"
TOUR_SEARCH_BUTTON_TEXT = "🔎 Показать отели и цены"
TOUR_MORE_BUTTON_TEXT = "🔄 Ещё варианты"
TOUR_SEND_MANAGER_TEXT = "💬 Отправить менеджеру"
TOUR_SEND_MANAGER_LEGACY_TEXT = "💬 Нужна помощь"
TOUR_SEND_SELECTED_TEXT = "✅ Отправить этот вариант"
CONTACT_OTHER_TEXT = "📞 Способ связи"
CONTACT_OTHER_LEGACY_TEXT = "📱 Телефон или MAX"
TOUR_CHEAPER_TEXT = "💰 Дешевле"
TOUR_BETTER_TEXT = "⭐ Лучше"
TOUR_ALL_INCLUSIVE_TEXT = "🍽 Всё включено"
TOUR_HOTEL_INFO_TEXT = "🏨 Об отеле и пляже"
TOUR_SIMILAR_TEXT = "🏨 Похожие отели"
TOUR_COMPARE_TEXT = "⚖️ Сравнить варианты"
TOUR_EDIT_DATES_TEXT = "📅 Даты"
TOUR_EDIT_BUDGET_TEXT = "💰 Бюджет"
TOUR_SHOW_OVER_BUDGET_TEXT = "Показать дороже"
REVIEW_HOTEL_TEXT = "🏨 Отель"
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
    "Я помогу быстро подобрать идеальный тур под ваш отдых ✨\n\n"
    "Уточню всего несколько деталей (можно отвечать кнопками):\n"
    "📍 Направление — страна, курорт или помогу с выбором\n"
    "🛫 Город вылета — Архангельск, Москва, СПб\n"
    "📅 Даты — желаемый месяц или точные дни поездки\n"
    "👥 Состав туристов — сколько взрослых и детей\n"
    "💰 Бюджет — примерная сумма на поездку\n\n"
    "Займёт меньше минуты. Нажмите кнопку ниже, чтобы начать 👇"
)

HINT_START = "Чтобы подобрать тур, напишите «Начать» или нажмите кнопку.\nСправка — «Помощь»."

# ---------------------------------------------------------------------------
# Groq client
# ---------------------------------------------------------------------------

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY and Groq else None
selection_ai_provider = build_selection_provider(
    AI_MODE,
    groq_api_key=GROQ_API_KEY,
    groq_model=GROQ_MODEL,
    regcloud_api_key=REGCLOUD_API_KEY,
    regcloud_base_url=REGCLOUD_BASE_URL,
    regcloud_model=REGCLOUD_MODEL,
)

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
                nights TEXT,
                dates_are_trip INTEGER,
                hotel_query TEXT,
                people TEXT,
                kids INTEGER,
                infants INTEGER,
                budget INTEGER,
                budget_scope TEXT,
                source TEXT,
                source_tag TEXT,
                vk_ref TEXT,
                vk_platform TEXT,
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
                nights TEXT,
                dates_are_trip INTEGER,
                hotel_query TEXT,
                people TEXT,
                kids INTEGER,
                infants INTEGER,
                budget INTEGER,
                budget_scope TEXT,
                source TEXT,
                source_tag TEXT,
                vk_ref TEXT,
                vk_platform TEXT,
                phone TEXT NOT NULL,
                needs_consultation INTEGER NOT NULL DEFAULT 0,
                selected_tour TEXT,
                mdt_status TEXT,
                mdt_attempts INTEGER NOT NULL DEFAULT 0,
                mdt_next_retry_at INTEGER,
                mdt_synced_at INTEGER,
                mdt_preorder_id INTEGER,
                mdt_tourist_id INTEGER,
                created_at INTEGER NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS miniapp_drafts (
                chat_id INTEGER PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS maintenance_migrations (
                name TEXT PRIMARY KEY,
                applied_at INTEGER NOT NULL
            )
        """)
        # Additive migration for databases created before the origin step.
        for _t in ("sessions", "leads"):
            cur.execute(f"PRAGMA table_info({_t})")
            _cols = {r[1] for r in cur.fetchall()}
            if "origin" not in _cols:
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN origin TEXT")
            if "nights" not in _cols:
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN nights TEXT")
            if "dates_are_trip" not in _cols:
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN dates_are_trip INTEGER")
            if "hotel_query" not in _cols:
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN hotel_query TEXT")
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
            for _c in ("source", "source_tag", "vk_ref", "vk_platform"):
                if _c not in _cols:
                    cur.execute(f"ALTER TABLE {_t} ADD COLUMN {_c} TEXT")
            if _t == "leads":
                if "mdt_status" not in _cols:
                    cur.execute("ALTER TABLE leads ADD COLUMN mdt_status TEXT")
                if "mdt_attempts" not in _cols:
                    cur.execute("ALTER TABLE leads ADD COLUMN mdt_attempts INTEGER NOT NULL DEFAULT 0")
                if "mdt_next_retry_at" not in _cols:
                    cur.execute("ALTER TABLE leads ADD COLUMN mdt_next_retry_at INTEGER")
                if "mdt_synced_at" not in _cols:
                    cur.execute("ALTER TABLE leads ADD COLUMN mdt_synced_at INTEGER")
                if "mdt_preorder_id" not in _cols:
                    cur.execute("ALTER TABLE leads ADD COLUMN mdt_preorder_id INTEGER")
                if "mdt_tourist_id" not in _cols:
                    cur.execute("ALTER TABLE leads ADD COLUMN mdt_tourist_id INTEGER")
        # One-time production repair for the single lead that the pre-acknowledgement
        # MDT client falsely marked `synced` after receiving an error JSON. Production
        # diagnostics identified it as lead 35, the only synced row, with zero attempts;
        # the CRM UI confirmed no corresponding inquiry exists. The migration marker
        # makes this repair idempotent across restarts and future deploys.
        repair_name = "20260915_requeue_false_mdt_sync_lead_35"
        cur.execute("SELECT 1 FROM maintenance_migrations WHERE name = ?", (repair_name,))
        if cur.fetchone() is None:
            repair_now = int(time.time())
            cur.execute(
                """
                UPDATE leads
                SET mdt_status='pending', mdt_attempts=0,
                    mdt_next_retry_at=?, mdt_synced_at=NULL
                WHERE id=35 AND mdt_status='synced' AND mdt_attempts=0
                """,
                (repair_now,),
            )
            if cur.rowcount:
                logger.warning("Requeued one false-synced MDT lead after acknowledgement fix")
            cur.execute(
                "INSERT INTO maintenance_migrations (name, applied_at) VALUES (?, ?)",
                (repair_name, repair_now),
            )

        # The CRM UI later confirmed that this exact lead already exists as
        # inquiry #1815 with external key vk-lead-35. Stop retrying that row: a
        # retry cannot improve an already-created CRM record and could create a
        # duplicate on endpoints that do not enforce external-key uniqueness.
        confirm_name = "20260915_confirm_existing_mdt_lead_35"
        cur.execute("SELECT 1 FROM maintenance_migrations WHERE name = ?", (confirm_name,))
        if cur.fetchone() is None:
            confirm_now = int(time.time())
            cur.execute(
                """
                UPDATE leads
                SET mdt_status='synced', mdt_next_retry_at=NULL, mdt_synced_at=?
                WHERE id=35 AND mdt_status='pending' AND mdt_attempts > 0
                """,
                (confirm_now,),
            )
            if cur.rowcount:
                logger.warning("Stopped retries for MDT lead 35 after CRM confirmation")
            cur.execute(
                "INSERT INTO maintenance_migrations (name, applied_at) VALUES (?, ?)",
                (confirm_name, confirm_now),
            )

        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_chat_id ON leads(chat_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_mdt_retry ON leads(mdt_status, mdt_next_retry_at)")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.fetchone()


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
            INSERT INTO sessions (chat_id, state, destination, origin, dates, nights,
                                  dates_are_trip, people,
                                  hotel_query,
                                  kids, kids_ages, infants, budget, budget_scope,
                                  source, source_tag, vk_ref, vk_platform, phone,
                                  needs_consultation, selected_tour, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                state=excluded.state, destination=excluded.destination,
                origin=excluded.origin,
                dates=excluded.dates, nights=excluded.nights,
                dates_are_trip=excluded.dates_are_trip, people=excluded.people,
                hotel_query=excluded.hotel_query,
                kids=excluded.kids, kids_ages=excluded.kids_ages,
                infants=excluded.infants,
                budget=excluded.budget, budget_scope=excluded.budget_scope,
                source=excluded.source, source_tag=excluded.source_tag,
                vk_ref=excluded.vk_ref, vk_platform=excluded.vk_platform,
                phone=excluded.phone,
                needs_consultation=excluded.needs_consultation,
                selected_tour=excluded.selected_tour,
                updated_at=excluded.updated_at
        """, (chat_id, data.get("state", ""), data.get("destination"),
              data.get("origin"),
              data.get("dates"), data.get("nights"),
              None if data.get("dates_are_trip") is None else int(bool(data.get("dates_are_trip"))),
              data.get("people"), data.get("hotel_query"),
              data.get("kids"), _ages_to_db(data.get("kids_ages")),
              data.get("infants"), data.get("budget"),
              data.get("budget_scope"), data.get("source"), data.get("source_tag"),
              data.get("vk_ref"), data.get("vk_platform"), data.get("phone"),
              int(bool(data.get("needs_consultation"))),
              _tour_to_db(data.get("selected_tour")),
              data.get("updated_at", now)))


def get_session(chat_id: int) -> Optional[Dict[str, Any]]:
    with _db_cursor() as cur:
        cur.execute("SELECT * FROM sessions WHERE chat_id = ?", (chat_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def _decode_session_state(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Convert SQLite session columns back to the in-memory dialog shape."""
    data = dict(raw)
    data.pop("chat_id", None)
    data["kids_ages"] = _ages_from_db(data.get("kids_ages"))
    data["needs_consultation"] = bool(data.get("needs_consultation"))
    if data.get("dates_are_trip") is not None:
        data["dates_are_trip"] = bool(data["dates_are_trip"])
    data["selected_tour"] = _tour_from_db(data.get("selected_tour"))
    return data


def _restore_session_from_db(chat_id: int) -> Optional[Dict[str, Any]]:
    """Hydrate a persisted dialog when the process cache does not have it."""
    raw = get_session(chat_id)
    if raw is None:
        return None
    data = _decode_session_state(raw)
    with _lock:
        user_data[chat_id] = data
    return data


def delete_session(chat_id: int) -> None:
    with _db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM sessions WHERE chat_id = ?", (chat_id,))


_MINIAPP_SNAPSHOT_FIELDS = (
    "destination", "origin", "dates", "nights", "dates_are_trip",
    "hotel_query", "people", "kids", "kids_ages", "infants",
    "budget", "budget_scope", "source", "source_tag", "vk_ref", "vk_platform",
    "needs_consultation",
)


def _save_miniapp_snapshot(chat_id: int, info: Dict[str, Any]) -> None:
    payload = {key: info.get(key) for key in _MINIAPP_SNAPSHOT_FIELDS}
    now = int(time.time())
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO miniapp_drafts (chat_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                payload=excluded.payload, updated_at=excluded.updated_at
            """,
            (chat_id, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), now),
        )


def _load_miniapp_snapshot(chat_id: int) -> Optional[Dict[str, Any]]:
    with _db_cursor() as cur:
        cur.execute("SELECT payload FROM miniapp_drafts WHERE chat_id = ?", (chat_id,))
        row = cur.fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row[0])
    except (TypeError, ValueError):
        logger.warning("Invalid Mini App snapshot for %s", _log_correlation(chat_id, namespace="vk-user"))
        return None
    if not isinstance(payload, dict):
        return None
    payload["state"] = STATE_REVIEW
    payload["updated_at"] = int(time.time())
    return payload


def _delete_miniapp_snapshot(chat_id: int) -> None:
    with _db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM miniapp_drafts WHERE chat_id = ?", (chat_id,))


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
) -> int:
    """Persist a completed request and return its durable local lead id."""
    now = int(time.time())
    if DEMO_MODE:
        phone = _tutu_mask_phone(phone)
    # Durable retry is intentionally scoped to add-lead mode. Retrying a
    # multi-step preorder/both transaction without server-side idempotency can
    # duplicate the part that already succeeded. Those modes keep the legacy
    # one-shot path until MDT exposes an idempotency key or lookup API.
    mdt_status = "pending" if MDT_ENABLED and MDT_MODE == "lead" and not DEMO_MODE else "disabled"
    mdt_next_retry_at = now if mdt_status == "pending" else None
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO leads (
                chat_id, first_name, username, destination, origin, dates, nights,
                dates_are_trip,
                people, hotel_query, kids, kids_ages, infants, budget, budget_scope,
                source, source_tag, vk_ref, vk_platform, phone,
                needs_consultation, selected_tour,
                mdt_status, mdt_attempts, mdt_next_retry_at, mdt_synced_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                first_name or None,
                username or None,
                info.get("destination"),
                info.get("origin"),
                info.get("dates"),
                info.get("nights"),
                None if info.get("dates_are_trip") is None else int(bool(info.get("dates_are_trip"))),
                info.get("people"), info.get("hotel_query"),
                info.get("kids"),
                _ages_to_db(info.get("kids_ages")),
                info.get("infants"),
                info.get("budget"),
                info.get("budget_scope"),
                info.get("source"),
                info.get("source_tag"),
                info.get("vk_ref"),
                info.get("vk_platform"),
                phone,
                int(bool(info.get("needs_consultation"))),
                _tour_to_db(info.get("selected_tour")),
                mdt_status, 0, mdt_next_retry_at, None, now,
            ),
        )
        return int(cur.lastrowid)


def _lead_row_to_info(row: Dict[str, Any]) -> Dict[str, Any]:
    info = dict(row)
    info["kids_ages"] = _ages_from_db(info.get("kids_ages"))
    info["needs_consultation"] = bool(info.get("needs_consultation"))
    if info.get("dates_are_trip") is not None:
        info["dates_are_trip"] = bool(info["dates_are_trip"])
    info["selected_tour"] = _tour_from_db(info.get("selected_tour"))
    info["_mdt_delivery_key"] = f"vk-lead-{info['id']}"
    return info


_ops_alert_lock = threading.Lock()
_last_ops_alert: Dict[str, float] = {}


def _mdt_delivery_health(now: Optional[float] = None) -> Dict[str, Any]:
    """Return aggregate VK MDT delivery telemetry without customer data."""
    current = int(time.time() if now is None else now)
    try:
        with _db_cursor() as cur:
            row = cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN mdt_status='synced' THEN 1 ELSE 0 END) AS synced,
                    SUM(CASE WHEN mdt_status='failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN mdt_status='pending' THEN 1 ELSE 0 END) AS pending,
                    SUM(
                        CASE
                            WHEN mdt_status IS NULL OR mdt_status='' OR mdt_status='unset'
                            THEN 1 ELSE 0
                        END
                    ) AS unset_count,
                    MAX(
                        CASE WHEN mdt_status='failed' THEN created_at ELSE NULL END
                    ) AS latest_failed_created_at
                FROM leads
                """
            ).fetchone()
    except sqlite3.Error as exc:
        logger.warning("Health could not read VK MDT delivery state: %s", exc)
        return {
            "enabled": bool(MDT_ENABLED and not DEMO_MODE),
            "mode": MDT_MODE,
            "available": False,
            "total": None,
            "synced": None,
            "failed": None,
            "pending": None,
            "unset": None,
            "latest_failed_seconds": None,
        }

    latest_failed = row["latest_failed_created_at"] if row else None
    return {
        "enabled": bool(MDT_ENABLED and not DEMO_MODE),
        "mode": MDT_MODE,
        "available": True,
        "total": int(row["total"] or 0) if row else 0,
        "synced": int(row["synced"] or 0) if row else 0,
        "failed": int(row["failed"] or 0) if row else 0,
        "pending": int(row["pending"] or 0) if row else 0,
        "unset": int(row["unset_count"] or 0) if row else 0,
        "latest_failed_seconds": (
            max(0, current - int(latest_failed)) if latest_failed is not None else None
        ),
    }


def _notify_ops_alert(message: str, *, alert_key: str) -> bool:
    """Send a rate-limited, PII-free VK/MDT operations alert to Telegram."""
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not ADMIN_ERROR_ALERTS or not bot_token or not LEAD_NOTIFY_IDS:
        return False

    now = time.time()
    key = str(alert_key or "vk_ops")[:100]
    with _ops_alert_lock:
        if _last_ops_alert.get(key, 0) > now - ERROR_ALERT_COOLDOWN:
            return False
        _last_ops_alert[key] = now

    delivered = False
    text = f"⚠️ VK/MDT: {message}"
    for recipient in LEAD_NOTIFY_IDS:
        try:
            resp = http_session.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": recipient, "text": text},
                timeout=5,
            )
            delivered = delivered or resp.status_code == 200
            if resp.status_code != 200:
                logger.warning(
                    "VK ops alert failed for Telegram manager %s: HTTP %s",
                    _log_correlation(recipient, namespace="tg-manager"),
                    resp.status_code,
                )
        except Exception as exc:
            logger.warning("VK ops alert failed for Telegram manager %s: %s", _log_correlation(recipient, namespace="tg-manager"), exc)
    return delivered


def _alert_mdt_preorder_failure() -> bool:
    """Report a one-shot preorder failure without exposing the failed lead."""
    stats = _mdt_delivery_health()
    if stats.get("available"):
        message = (
            "MDT preorder write failed; "
            f"failed={int(stats.get('failed') or 0)}, "
            f"synced={int(stats.get('synced') or 0)}, "
            f"mode={stats.get('mode') or MDT_MODE}"
        )
    else:
        message = f"MDT preorder write failed; mode={MDT_MODE}"
    return _notify_ops_alert(message, alert_key="vk_mdt_preorder_failed")


def _record_mdt_preorder_result(
    lead_id: int,
    preorder_id: Optional[int],
    tourist_id: Optional[int],
) -> None:
    """Persist the one-shot VK preorder outcome without storing more PII.

    Preorder writes are intentionally not retried because MDT creates a temp
    tourist and then a preorder. Retrying that transaction after a partial
    success can duplicate CRM entities. Persisting the returned IDs gives us a
    durable, non-sensitive production proof instead.
    """
    synced = preorder_id is not None
    now = int(time.time())
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE leads
            SET mdt_status=?, mdt_attempts=1, mdt_next_retry_at=NULL,
                mdt_synced_at=?, mdt_preorder_id=?, mdt_tourist_id=?
            WHERE id=?
            """,
            (
                "synced" if synced else "failed",
                now if synced else None,
                preorder_id,
                tourist_id,
                lead_id,
            ),
        )
    if not synced:
        _alert_mdt_preorder_failure()


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
        cur.execute("DELETE FROM miniapp_drafts WHERE chat_id = ?", (chat_id,))
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
    if keyboard is None and user_id not in (ADMIN_ID, LEAD_OWNER_VK_ID):
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


def _message_id(response: Any) -> Optional[int]:
    """Extract a VK message id from old and new messages.send responses."""
    if isinstance(response, int):
        return response
    if isinstance(response, dict):
        value = response.get("message_id")
        if isinstance(value, int):
            return value
    return None


def edit_message(
    user_id: int,
    message_id: Optional[int],
    text: str,
    keyboard: Optional[str] = None,
) -> bool:
    """Edit a bot message; callers keep a send fallback for older VK clients."""
    if not message_id:
        return False
    params: Dict[str, Any] = {
        "peer_id": user_id,
        "message_id": message_id,
        "message": text,
    }
    if keyboard is not None:
        params["keyboard"] = _downgrade_if_needed(user_id, keyboard)
    return _vk_api("messages.edit", **params) is not None


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
        if offer.get("departure"):
            bits.append(f"из {offer['departure']}")
        if offer.get("nights"):
            bits.append(_tourvisor.nights_label(offer["nights"]))
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
    inline: bool = False,
) -> str:
    return json.dumps({
        "one_time": one_time,
        "inline": inline,
        "buttons": rows,
    })


def _btn(label: str, color: str = "secondary", payload: Optional[Dict] = None) -> Dict[str, Any]:
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
    return _keyboard([
        [_btn("Турция", "primary"), _btn("Египет", "primary"), _btn("ОАЭ", "primary")],
        [_btn(DEST_HOT_TOURS_LABEL, "positive"), _btn(DEST_DIRECT_FLIGHTS_LABEL, "primary"), _btn("🌍 Другие", "secondary")],
        [_btn(DIRECTION_UNDECIDED_LABEL, "secondary"), _btn(CANCEL_BUTTON_TEXT, "negative")],
    ])


def _dest_more_keyboard() -> str:
    return _keyboard([
        [_btn("Таиланд", "primary"), _btn("Мальдивы", "primary"), _btn("Сочи", "primary")],
        [_btn("Калининград", "primary"), _btn("Абхазия", "primary"), _btn("✍️ Свой вариант", "secondary")],
        [_btn(BACK_BUTTON_TEXT, "secondary"), _btn(CANCEL_BUTTON_TEXT, "negative")],
    ])


def _dest_direct_keyboard() -> str:
    return _keyboard([
        [_btn("Турция", "primary"), _btn("Египет", "primary"), _btn("Сочи", "primary")],
        [_btn("Калининград", "primary"), _btn("Минск", "primary"), _btn("✍️ Свой вариант", "secondary")],
        [_btn(BACK_BUTTON_TEXT, "secondary"), _btn(CANCEL_BUTTON_TEXT, "negative")],
    ])


def _nav_keyboard(extra_top: Optional[List[Dict]] = None) -> str:
    rows: List[List[Dict[str, Any]]] = []
    if extra_top:
        rows.append(extra_top)
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary"), _btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _dates_keyboard() -> str:
    labels = [label for label, _ in DATE_PRESETS] + [DATE_CUSTOM_LABEL]
    rows = _chunk_buttons(labels, "primary", 2)
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary"), _btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _nights_keyboard() -> str:
    labels = [label for label, _ in NIGHTS_PRESETS] + [NIGHTS_CUSTOM_LABEL]
    rows = _chunk_buttons(labels, "primary", 2)
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary"), _btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _people_keyboard() -> str:
    rows = [
        [_btn(PARTY_PRESET_2_ADULTS, "primary"), _btn(PARTY_PRESET_1_ADULT, "primary")],
        [_btn(PARTY_PRESET_2_PLUS_1, "primary"), _btn(PARTY_PRESET_2_PLUS_2, "primary")],
        [_btn(PARTY_PRESET_OTHER, "secondary")],
        [_btn(BACK_BUTTON_TEXT, "secondary"), _btn(CANCEL_BUTTON_TEXT, "negative")],
    ]
    return _keyboard(rows)


def _kids_ages_keyboard() -> str:
    return _nav_keyboard([_btn(NO_KIDS_BUTTON_TEXT, "positive")])


def _budget_keyboard() -> str:
    labels = [label for label, _ in BUDGET_PRESETS]
    rows = _chunk_buttons(labels, "primary", 2)
    rows.append([_btn(BUDGET_ANY_LABEL, "secondary"), _btn(BUDGET_CUSTOM_LABEL, "secondary")])
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary"), _btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _review_keyboard() -> str:
    rows: List[List[Dict[str, Any]]] = []
    if TOUR_SEARCH_ENABLED:
        rows.append([_btn(TOUR_SEARCH_BUTTON_TEXT, "positive")])
        rows.append([_btn(TOUR_SEND_MANAGER_TEXT, "secondary")])
    else:
        rows.append([_btn(REVIEW_CONFIRM_TEXT, "positive")])
    rows.append([
        _btn(REVIEW_HOTEL_TEXT, "secondary"),
        _btn(TOUR_EDIT_DATES_TEXT, "secondary"),
        _btn(TOUR_EDIT_BUDGET_TEXT, "secondary"),
    ])
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary"), _btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _tour_search_wait_keyboard() -> str:
    return _keyboard([[_btn(CANCEL_BUTTON_TEXT, "negative")]])


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
        _btn(BACK_BUTTON_TEXT, "secondary"),
        _btn(CANCEL_BUTTON_TEXT, "negative"),
    ])
    return _keyboard(rows)


def _selected_tour_keyboard() -> str:
    return _keyboard([
        [_btn(TOUR_SEND_SELECTED_TEXT, "positive")],
        [_btn(TOUR_HOTEL_INFO_TEXT, "primary")],
        [_btn(BACK_BUTTON_TEXT, "secondary"), _btn(CANCEL_BUTTON_TEXT, "negative")],
    ])


def _no_tours_keyboard(show_over_budget: bool = False) -> str:
    rows: List[List[Dict[str, Any]]] = []
    if show_over_budget:
        rows.append([_btn(TOUR_SHOW_OVER_BUDGET_TEXT, "primary")])
    rows.extend([
        [_btn(TOUR_SEND_MANAGER_TEXT, "positive")],
        [_btn(TOUR_EDIT_DATES_TEXT, "secondary"), _btn(TOUR_EDIT_BUDGET_TEXT, "secondary")],
        [_btn(BACK_BUTTON_TEXT, "secondary"), _btn(CANCEL_BUTTON_TEXT, "negative")],
    ])
    return _keyboard(rows)


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
    rows: List[List[Dict[str, Any]]] = []
    miniapp_button = build_open_app_button(
        os.getenv("VK_MINI_APP_ID", ""),
        VK_GROUP_ID,
        enabled=bool(os.getenv("VK_MINI_APP_SECRET", "").strip()),
    )
    if miniapp_button:
        rows.append([miniapp_button])
    rows.append([_btn(START_BUTTON_TEXT, "positive")])
    return _keyboard(rows)


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
        groq_client=selection_ai_provider.client,
        groq_model=selection_ai_provider.model,
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
        source=(os.getenv("VK_MDT_SOURCE", "VK Bot").strip() or "VK Bot"),
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


def send_lead_to_mdt(chat_id, info, phone, client_name) -> bool:
    return mdt_shared.dispatch_lead(
        _mdt_settings(),
        chat_id,
        info,
        phone,
        client_name,
        _mdt_country_cache,
        _mdt_request,
        log=logger,
    )


_mdt_delivery_lock = threading.Lock()


def _mdt_retry_delay(attempts: int) -> int:
    exponent = max(0, min(int(attempts) - 1, 16))
    return min(MDT_RETRY_MAX_SECONDS, MDT_RETRY_BASE_SECONDS * (2 ** exponent))


def _deliver_mdt_lead(lead_id: int) -> bool:
    if not MDT_ENABLED or DEMO_MODE:
        return True
    with _mdt_delivery_lock:
        with _db_cursor() as cur:
            cur.execute("SELECT * FROM leads WHERE id = ?", (lead_id,))
            row = cur.fetchone()
        if row is None:
            return True
        record = dict(row)
        if record.get("mdt_status") in ("synced", "disabled"):
            return True
        info = _lead_row_to_info(record)
        try:
            success = bool(send_lead_to_mdt(
                int(record["chat_id"]), info, str(record.get("phone") or ""),
                record.get("first_name") or f"VK {record['chat_id']}",
            ))
        except Exception as exc:
            logger.error("MDT durable delivery crashed for lead %s: %s", lead_id, exc)
            success = False
        now = int(time.time())
        if success:
            with _db_cursor(commit=True) as cur:
                cur.execute(
                    "UPDATE leads SET mdt_status='synced', mdt_synced_at=?, "
                    "mdt_next_retry_at=NULL WHERE id=?", (now, lead_id))
            logger.info("MDT durable delivery synced local lead %s", lead_id)
            return True
        attempts = int(record.get("mdt_attempts") or 0) + 1
        next_retry = now + _mdt_retry_delay(attempts)
        with _db_cursor(commit=True) as cur:
            cur.execute(
                "UPDATE leads SET mdt_status='pending', mdt_attempts=?, "
                "mdt_next_retry_at=? WHERE id=?", (attempts, next_retry, lead_id))
        logger.warning(
            "MDT delivery pending for local lead %s (attempt %s, retry in %ss)",
            lead_id, attempts, max(0, next_retry - now))
        return False


def _retry_pending_mdt_leads() -> int:
    if not MDT_ENABLED or not MDT_RETRY_ENABLED or DEMO_MODE:
        return 0
    now = int(time.time())
    with _db_cursor() as cur:
        cur.execute(
            "SELECT id FROM leads WHERE mdt_status='pending' "
            "AND COALESCE(mdt_next_retry_at, 0) <= ? ORDER BY id LIMIT ?",
            (now, MDT_RETRY_BATCH_SIZE))
        ids = [int(row[0]) for row in cur.fetchall()]
    synced = 0
    for lead_id in ids:
        if _deliver_mdt_lead(lead_id):
            synced += 1
    return synced


def _start_mdt_retry_worker() -> None:
    if not MDT_ENABLED or not MDT_RETRY_ENABLED or DEMO_MODE:
        return
    def _worker() -> None:
        # Give startup network/config work a chance to finish before replaying
        # rows left pending by a previous process.
        time.sleep(MDT_RETRY_POLL_SECONDS)
        while True:
            try:
                _retry_pending_mdt_leads()
            except Exception as exc:
                logger.error("MDT retry worker failed: %s", exc)
            time.sleep(MDT_RETRY_POLL_SECONDS)
    threading.Thread(target=_worker, daemon=True, name="vk-mdt-retry").start()
    logger.info("MDT retry worker started (%ss poll)", MDT_RETRY_POLL_SECONDS)


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


def _safe_source_tag(value: Any) -> str:
    """Return a bounded campaign tag without letting malformed VK ref break the bot."""
    try:
        return normalise_source_tag(value)
    except MiniAppValidationError:
        return ""


def _first_touch_source_tag(user_id: int, incoming: Any = "") -> str:
    """Preserve the first valid campaign tag for the active lead."""
    with _lock:
        previous = (user_data.get(user_id) or {}).get("source_tag")
    return _safe_source_tag(previous) or _safe_source_tag(incoming)


def handle_start(
    user_id: int, first_name: str = "", source_tag: str = ""
) -> None:
    source_tag = _first_touch_source_tag(user_id, source_tag)
    if CONSENT_MODE == "strict" and not has_consent(user_id):
        with _lock:
            user_data[user_id] = {
                "state": STATE_CONSENT,
                "source_tag": source_tag or None,
                "updated_at": int(time.time()),
            }
        _mark_dirty(user_id)
        send_message(user_id, _welcome_text(first_name))
        send_message(user_id, _consent_text(), keyboard=_consent_keyboard())
        return

    if CONSENT_MODE == "soft" and not has_consent(user_id):
        with _lock:
            user_data[user_id] = {
                "state": STATE_CONSENT,
                "source_tag": source_tag or None,
                "updated_at": int(time.time()),
            }
        _mark_dirty(user_id)
        send_message(
            user_id,
            _welcome_text(first_name),
            keyboard=_soft_start_keyboard(),
        )
        return

    _begin_destination(user_id, first_name, source_tag=source_tag)


def _begin_destination(
    user_id: int, first_name: str = "", source_tag: str = ""
) -> None:
    source_tag = _first_touch_source_tag(user_id, source_tag)
    with _lock:
        user_data[user_id] = {
            "state": STATE_DESTINATION,
            "source_tag": source_tag or None,
            "updated_at": int(time.time()),
        }
    _mark_dirty(user_id)
    name = f", {first_name}" if first_name else ""
    send_message(
        user_id,
        f"🌴 Отлично{name}! Давайте подберём тур ✨\n\n"
        "Шаг 1 из 5 · направление\n\n"
        "📍 Куда хотите поехать?\n\n"
        "Выберите популярное направление кнопкой или напишите своё:",
        keyboard=_dest_keyboard(),
    )


def handle_cancel(user_id: int) -> None:
    snapshot_existed = _load_miniapp_snapshot(user_id) is not None
    with _lock:
        existed = user_data.pop(user_id, None) is not None
    _delete_miniapp_snapshot(user_id)
    if existed or snapshot_existed:
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
    popular = [label for label, value in ORIGIN_PRESETS if value != "Другой город"]
    rows = _chunk_buttons(popular, "primary", 2)
    rows.append([_btn("Другой город", "secondary")])
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary"),
                 _btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _ask_origin(user_id: int) -> None:
    send_message(
        user_id,
        "Шаг 2 из 5 · город вылета\n\n"
        "🛫 Откуда удобнее вылетать?\n\n"
        "Выберите город кнопкой или напишите свой:",
        keyboard=_origin_keyboard(),
    )


def _ask_dates(user_id: int) -> None:
    send_message(
        user_id,
        "Шаг 3 из 5 · даты поездки\n\n"
        "📅 Когда планируете отпуск?\n\n"
        "Выберите период кнопкой или напишите свои даты (например: 15-22 сентября):",
        keyboard=_dates_keyboard(),
    )


def _ask_nights(user_id: int, dates: Optional[str] = None) -> None:
    dates_prefix = f"📅 Вылет: {dates}\n\n" if dates else ""
    send_message(
        user_id,
        dates_prefix + "Шаг 3 из 5 · длительность\n\n"
        "🌙 На сколько ночей планируете поездку?\n\n"
        "Выберите вариант кнопкой или напишите число/диапазон:",
        keyboard=_nights_keyboard(),
    )


def _ask_hotel(user_id: int) -> None:
    send_message(
        user_id,
        "🏨 Напишите точное название отеля.\n\n"
        "Если нужен любой подходящий отель — нажмите «Назад».",
        keyboard=_nav_keyboard(),
    )


def _ask_people(user_id: int, dates: Optional[str] = None) -> None:
    dates_prefix = f"📅 Понял: {dates}\n\n" if dates else ""
    send_message(
        user_id,
        dates_prefix + "Шаг 4 из 5 · состав туристов\n\n"
        "👥 Кто поедет отдыхать?\n\n"
        "Выберите готовый состав кнопкой или укажите число взрослых (1–50):",
        keyboard=_people_keyboard(),
    )


def _ask_kids_ages(user_id: int) -> None:
    """Возрасты детей одним числовым ответом.

    Отдельный вопрос «дети до 12 едут?» убран: он спрашивал то, что и так
    видно из возрастов.
    """
    send_message(
        user_id,
        "Шаг 4 из 5 · состав туристов\n\n"
        "🎂 Возраст детей укажите через запятую (например: 5, 9):\n\n"
        "Если детей нет — нажмите кнопку ниже 👇",
        keyboard=_kids_ages_keyboard(),
    )


def _ask_budget(user_id: int, party: Optional[str] = None) -> None:
    party_prefix = f"👥 Записал: {party}\n\n" if party else ""
    send_message(
        user_id,
        party_prefix + "Шаг 5 из 5 · бюджет\n\n"
        "💰 Примерный бюджет на всю поездку (₽)?\n\n"
        "Выберите комфортную планку или введите свою сумму:",
        keyboard=_budget_keyboard(),
    )


def _ask_contact(user_id: int) -> None:
    send_message(
        user_id,
        "📞 Как удобнее связаться с вами?\n\n"
        "Можно продолжить в VK (этот чат) или указать телефон / MAX:",
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
        _begin_destination(
            user_id,
            first_name,
            source_tag=str(info.get("source_tag") or ""),
        )
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
    dest = (text or "").strip()
    dest_lower = dest.lower()

    if dest == DEST_HOT_TOURS_LABEL or dest_lower in ("горящие туры", "горящие", "горящий тур", "горящие туры из архангельска", "🔥 горящие туры", "🔥 горящие"):
        origin = info.get("origin") or "Архангельск"
        if not DEMO_MODE:
            send_message(
                user_id,
                "🔥 Горящие туры показываем только по актуальным данным. "
                "Выберите конкретное направление, и я проверю реальные варианты.",
                keyboard=_dest_keyboard(),
            )
            return
        hot_offers = _tourvisor.get_hot_tours(origin)
        info["_tour_offers"] = [offer.__dict__ if hasattr(offer, "__dict__") else offer for offer in hot_offers]
        info["_tour_offers_base"] = list(info["_tour_offers"])
        info["_tour_page"] = 0
        info["destination"] = "🔥 Горящие туры"
        info["origin"] = origin
        info["dates"] = "ближайшие дни"
        info["nights"] = 7
        info["people"] = "2"
        info["state"] = STATE_REVIEW
        _send_tour_results_page(user_id, 0)
        return

    if dest == DEST_DIRECT_FLIGHTS_LABEL or dest_lower in ("прямые вылеты", "прямые рейсы", "куда летаем", "прямой рейс", "прямые", "🛫 прямые вылеты", "🛫 прямые"):
        origin = info.get("origin") or "Архангельск"
        directs = _tourvisor.get_direct_destinations(origin)
        lines = [f"🛫 Прямые чартерные рейсы из {origin}:\n"]
        for d in directs:
            price_str = f"{d['min_price']:,} ₽".replace(",", " ")
            lines.append(f"• {d['country']} (от {price_str}) — {d['resorts']}")
            lines.append(f"   🗓 Вылеты: {d['days']}\n")
        lines.append("Выберите направление кнопкой ниже или напишите своё:")
        send_message(user_id, "\n".join(lines), keyboard=_dest_direct_keyboard())
        return

    if dest_lower in ("🌍 другие", "другие", "другое", "ещё", "другие направления", "ещё направления", "популярные", "еще"):
        send_message(
            user_id,
            "🌍 Другие популярные направления:\n\n"
            "Выберите вариант кнопкой ниже или напишите своё направление:",
            keyboard=_dest_more_keyboard(),
        )
        return

    if dest_lower in ("✍️ свой вариант", "свой вариант", "своё", "написать своё", "свой", "свое направление", "своё направление", "напишите своё"):
        send_message(
            user_id,
            "✍️ Напишите страну, курорт или город, куда хотите поехать:",
            keyboard=_nav_keyboard(),
        )
        return

    if dest == DIRECTION_UNDECIDED_LABEL or dest_lower in ("не определился", "не знаю", "консультация", "🌴 не определился"):
        info["destination"] = UNDECIDED_DESTINATION
        info["needs_consultation"] = True
        info["state"] = STATE_ORIGIN
        _ask_origin(user_id)
        return

    info["destination"] = dest
    info["state"] = STATE_ORIGIN
    _ask_origin(user_id)


def _step_origin(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    raw_city = (text or "").strip()
    city = dict(ORIGIN_PRESETS).get(raw_city, raw_city)
    if city.lower() in ("другой город", "другое"):
        send_message(
            user_id,
            "✍️ Напишите город вылета.\n\n"
            "Можно указать два варианта через запятую, например: "
            "Челябинск, Екатеринбург.",
            keyboard=_nav_keyboard(),
        )
        return
    if not city:
        _ask_origin(user_id)
        return
    cities = [
        dict(ORIGIN_PRESETS).get(part.strip(), part.strip())
        for part in re.split(r"\s+(?:или|либо)\s+|[,;/]", city, flags=re.I)
        if part.strip()
    ]
    cities = list(dict.fromkeys(cities))[:2]
    if any(part.lower() in ("другой город", "другое") for part in cities):
        send_message(user_id, "✍️ Напишите один или два города вылета:", keyboard=_nav_keyboard())
        return
    if any(part.strip().lower() == str(info.get("destination", "")).strip().lower() for part in cities):
        # Same city both ends: the search returns nothing and the client
        # silently gets fallback text instead of prices.
        send_message(
            user_id,
            f"🤔 {city} — это и есть ваше направление.\n\n"
            "Из какого города вылетаете?",
            keyboard=_origin_keyboard(),
        )
        return
    info["origin"] = " / ".join(cities)
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
    chosen_preset = raw in preset_map
    if chosen_preset:
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
    human_dates = _human_dates(depart, ret)
    try:
        duration = (
            datetime.strptime(ret, "%Y-%m-%d") - datetime.strptime(depart, "%Y-%m-%d")
        ).days if ret else 0
    except (TypeError, ValueError):
        duration = 0
    if duration >= 4 and not chosen_preset:
        # A wide range such as 15–22 September is naturally a complete trip.
        # Keep accepting it so returning clients are not forced through a new step.
        info["nights"] = str(duration)
        info["dates_are_trip"] = True
        info["state"] = STATE_PEOPLE
        _ask_people(user_id, human_dates)
        return
    info["dates_are_trip"] = False
    info["state"] = STATE_NIGHTS
    _ask_nights(user_id, human_dates)


def _step_nights(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    raw = (text or "").strip()
    if raw in (NIGHTS_CUSTOM_LABEL, "своя длительность"):
        send_message(
            user_id,
            "✍️ Напишите количество ночей, например: 11–12:",
            keyboard=_nav_keyboard(),
        )
        return
    raw = dict(NIGHTS_PRESETS).get(raw, raw)
    values = [int(value) for value in re.findall(r"\d+", raw)]
    if not values or any(value < 1 or value > 28 for value in values[:2]):
        send_message(
            user_id,
            "Укажите длительность от 1 до 28 ночей, например: 11–12.",
            keyboard=_nights_keyboard(),
        )
        return
    start = values[0]
    end = values[1] if len(values) > 1 else start
    info["nights"] = f"{min(start, end)}-{max(start, end)}" if start != end else str(start)
    info["dates_are_trip"] = False
    info["state"] = STATE_PEOPLE
    _ask_people(user_id)


def _step_hotel(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    hotel = (text or "").strip()
    if len(hotel) < 3:
        send_message(user_id, "Напишите название отеля полностью.", keyboard=_nav_keyboard())
        return
    info["hotel_query"] = hotel
    info["state"] = STATE_REVIEW
    _ask_review(user_id)


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
    raw = (text or "").strip()

    if raw in (PARTY_PRESET_2_ADULTS, "2 взрослых", "2 взр", "вдвоем", "вдвоём", "пара"):
        info["people"] = "2"
        info["kids_ages"] = []
        info["kids"] = 0
        info["infants"] = 0
        info["state"] = STATE_BUDGET
        _ask_budget(user_id, _party_text(info))
        return

    if raw in (PARTY_PRESET_1_ADULT, "1 взрослый", "1 взр", "один", "одна", "я один", "я одна"):
        info["people"] = "1"
        info["kids_ages"] = []
        info["kids"] = 0
        info["infants"] = 0
        info["state"] = STATE_BUDGET
        _ask_budget(user_id, _party_text(info))
        return

    if raw in (PARTY_PRESET_2_PLUS_1, "2+1", "2 взр + 1 реб", "2 взр. + 1 реб."):
        info["people"] = "2"
        info["state"] = STATE_KIDS_AGES
        send_message(
            user_id,
            "Шаг 4 из 6 · состав туристов\n\n"
            "🎂 Сколько лет ребёнку?\n\n"
            "Возраст ребёнка укажите числом: 5 (или «до года»):\n"
            "Если детей нет — нажмите «👶 Детей нет».",
            keyboard=_kids_ages_keyboard(),
        )
        return

    if raw in (PARTY_PRESET_2_PLUS_2, "2+2", "2 взр + 2 дет", "2 взр. + 2 дет."):
        info["people"] = "2"
        info["state"] = STATE_KIDS_AGES
        send_message(
            user_id,
            "Шаг 4 из 6 · состав туристов\n\n"
            "🎂 Напишите возраст каждого ребёнка\n\n"
            "Возраст детей укажите через запятую: 5, 9.\n"
            "Если детей нет — нажмите «👶 Детей нет».",
            keyboard=_kids_ages_keyboard(),
        )
        return

    if raw in (PARTY_PRESET_OTHER, "другой", "другой состав"):
        send_message(
            user_id,
            "✍️ Напишите количество взрослых числом (1–50):",
            keyboard=_nav_keyboard(),
        )
        return

    ok, value = validate_people(text)
    if not ok:
        send_message(
            user_id,
            "Сколько ВЗРОСЛЫХ? Число от 1 до 50 или кнопка с составом.",
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
    if raw in (BUDGET_ANY_LABEL, "любой", "любой бюджет", "любая", "не знаю", "предложите", "предложите варианты"):
        info["budget"] = None
        info["budget_open_ended"] = True
        info["budget_scope"] = "total"
        info["state"] = STATE_REVIEW
        _ask_review(user_id)
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
            "Укажите одну максимальную сумму на всю поездку: например 120000 ₽ или 120 тыс. "
            "Если бюджет 100000–120000 ₽, введите 120000. Можно выбрать кнопку ниже.",
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
    budget_prefix = "от" if info.get("budget_open_ended") else "до"
    budget_suffix = "на всю поездку" if info.get("budget_scope") == "total" else "на человека"
    if info.get("budget") is None or str(info.get("budget")).lower() in ("любой", "none"):
        budget_line = "💰 Бюджет: предложите варианты"
    else:
        raw_budget = info.get("budget", 0)
        budget_formatted = f"{raw_budget:,}".replace(",", " ") if isinstance(raw_budget, (int, float)) else str(raw_budget)
        budget_line = f"💰 Бюджет: {budget_prefix} {budget_formatted} ₽ {budget_suffix}"
    nights_line = (
        f"\n🌙 Длительность: {_tourvisor.nights_label(info['nights'])}"
        if info.get("nights") else ""
    )
    hotel_line = f"\n🏨 Отель: {info['hotel_query']}" if info.get("hotel_query") else ""
    action_hint = (
        "✨ Нажмите кнопку «🔎 Показать отели и цены», чтобы увидеть актуальные варианты 👇"
        if TOUR_SEARCH_ENABLED else
        "✨ Автопоиск цен сейчас недоступен. Проверьте параметры и отправьте заявку менеджеру 👇"
    )
    summary = (
        "Проверьте заявку:\n\n"
        f"📍 Направление: {info.get('destination', '—')}\n"
        f"🛫 Вылет: {info.get('origin', '—')}\n"
        f"📅 Даты: {info.get('dates', '—')}"
        f"{nights_line}{hotel_line}\n"
        f"👥 Состав: {_party_text(info)}\n"
        f"{budget_line}"
        f"{consultation}\n\n"
        f"{action_hint}"
    )
    response = send_message(
        user_id,
        summary,
        keyboard=_review_keyboard(),
    )
    info["_review_message_id"] = _message_id(response)


def _tour_search_wait_text(status: str) -> str:
    return (
        "🔎 Ищу актуальные туры по вашей заявке\n\n"
        "✅ Параметры приняты\n"
        f"{status}\n\n"
        "Обычно поиск занимает 10–20 секунд. Можно не держать чат открытым — "
        "я пришлю варианты сюда."
    )


def _tour_search_wait_animation(user_id: int, marker: str, message_id: int) -> None:
    """Keep VK's native typing indicator alive and gently update one message."""
    statuses = (
        "⏳ Запрашиваю предложения у туроператоров…",
        "✈️ Сверяю даты, длительность и состав туристов…",
        "💰 Сравниваю доступные варианты по цене…",
    )
    for status in statuses:
        time.sleep(4)
        with _lock:
            live = user_data.get(user_id)
            active = bool(live and live.get("_tour_search_marker") == marker)
        if not active:
            return
        send_typing(user_id)
        edit_message(
            user_id,
            message_id,
            _tour_search_wait_text(status),
            keyboard=_tour_search_wait_keyboard(),
        )
        # The API may finish between the active check and messages.edit.
        # Restore the final state so a late animation tick never wins the race.
        with _lock:
            live = user_data.get(user_id)
            active = bool(live and live.get("_tour_search_marker") == marker)
            final_text = str((live or {}).get("_tour_wait_final_text") or "")
        if not active and final_text:
            edit_message(user_id, message_id, final_text, keyboard=_hide_keyboard())
            return


def _tour_search_worker(
    user_id: int,
    marker: str,
    snapshot: Dict[str, Any],
    wait_message_id: Optional[int] = None,
) -> None:
    """Search in the background and only answer while this review is current."""
    origins = [part.strip() for part in str(snapshot.get("origin") or "").split("/") if part.strip()]
    origins = origins or [str(snapshot.get("origin") or "")]
    results = []
    provider_names: List[str] = []
    for origin in origins[:2]:
        origin_snapshot = dict(snapshot)
        origin_snapshot["origin"] = origin
        provider_result, provider_name = _tour_providers.search_tours(
            _tour_provider_settings(), http_session, origin_snapshot, log=logger,
        )
        results.append(provider_result)
        if provider_name:
            provider_names.append(provider_name)
    combined = [offer for result in results for offer in result.offers]
    # Curated offers are demo fixtures, never a substitute for an empty or
    # failed upstream search in the live agency funnel.
    if not combined and DEMO_MODE:
        dest_val = snapshot.get("destination") or ""
        combined = _tourvisor.get_hot_tours(
            snapshot.get("origin") or "Архангельск",
            destination=dest_val,
            limit=TOUR_SEARCH_MAX_OFFERS,
        )
    combined.sort(key=lambda offer: offer.price + offer.fuel_charge)
    result = _tourvisor.SearchResult(
        offers=combined[:TOUR_SEARCH_MAX_OFFERS],
        error="; ".join(result.error for result in results if result.error),
        search_id=next((result.search_id for result in results if result.search_id), None),
    )
    with _lock:
        live = user_data.get(user_id)
        if live is None or live.get("_tour_search_marker") != marker:
            return
        live.pop("_tour_searching", None)
        live.pop("_tour_search_marker", None)
        if live.get("state") != STATE_REVIEW:
            return
        final_text = (
            f"✅ Подборка готова — найдено вариантов: {len(result.offers)}."
            if result.offers else
            "🔎 Поиск завершён. Готовых вариантов по заданным параметрам не нашлось."
        )
        live["_tour_wait_final_text"] = final_text

    if result.offers:
        edit_message(
            user_id,
            wait_message_id,
            final_text,
            keyboard=_hide_keyboard(),
        )
        with _lock:
            live = user_data.get(user_id)
            if live is None or live.get("state") != STATE_REVIEW:
                return
            offers = [offer.__dict__.copy() for offer in result.offers]
            live["_tour_offers_base"] = offers
            live["_tour_offers"] = list(offers)
            live["_tour_provider"] = ",".join(dict.fromkeys(provider_names))
            live["_tour_page"] = 0
            live.pop("selected_tour", None)
        if snapshot.get("_show_over_budget"):
            send_message(
                user_id,
                "⚠️ Эти варианты дороже указанного бюджета. Показываю их только по вашему запросу.",
                keyboard=_hide_keyboard(),
            )
        _send_tour_results_page(user_id, 0)
        return

    logger.info("VK package tour search returned no offers for %s: %s", _log_correlation(user_id, namespace="vk-user"), result.error)
    edit_message(
        user_id,
        wait_message_id,
        final_text,
        keyboard=_hide_keyboard(),
    )
    can_show_over = bool(
        result.search_id and snapshot.get("budget")
        and not snapshot.get("budget_open_ended")
    )
    if can_show_over:
        message_text = (
            f"До {_budget_summary(snapshot).replace('до ', '', 1)} вариантов не нашлось.\n\n"
            "Могу отдельно показать ближайшие предложения дороже бюджета."
        )
    else:
        message_text = (
            "По заданным параметрам готовых вариантов сейчас не нашлось.\n\n"
            "Можно изменить даты или бюджет, либо попросить помощи менеджера."
        )
    send_message(user_id, message_text, keyboard=_no_tours_keyboard(can_show_over))


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
        selected = bool(live.get("selected_tour"))

    numbers = list(range(offset + 1, offset + len(offers) + 1))
    keyboard = _tour_results_keyboard(numbers, selected=selected)
    result = _tourvisor.SearchResult(
        offers=[_tourvisor.TourOffer(**offer) for offer in offers]
    )
    header = f"📄 Страница {page + 1} из {page_count} (варианты {offset + 1}–{offset + len(offers)} из {len(pool)}):\n\n"
    content = _tourvisor.format_client_message(result, start_index=offset + 1)
    send_message(
        user_id,
        header + content,
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
    if offer.get("departure"):
        parts.append(f"🛫 Вылет из {offer['departure']}")
    trip_details = []
    if offer.get("date"):
        trip_details.append(_tourvisor.display_date(offer["date"]))
    if offer.get("nights"):
        trip_details.append(_tourvisor.nights_label(offer["nights"]))
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
    if info.get("budget") is None or str(info.get("budget")).lower() in ("любой", "none"):
        return "любой (предложить варианты)"
    suffix = " на всю поездку" if info.get("budget_scope") == "total" else ""
    prefix = "от " if info.get("budget_open_ended") else "до "
    try:
        amount = f"{int(info.get('budget')):,}".replace(",", " ")
    except (TypeError, ValueError):
        amount = str(info.get("budget", "?"))
    return f"{prefix}{amount} ₽{suffix}"


def _actualize_selected_tour_worker(user_id: int, tour_id: str) -> None:
    with _lock:
        live = user_data.get(user_id)
        selected = dict((live or {}).get("selected_tour") or {})
        if (
            live is None
            or live.get("state") != STATE_REVIEW
            or str(selected.get("tour_id") or "") != str(tour_id or "")
        ):
            return

    actual = _tour_providers.actualize_offer(
        _tour_provider_settings(), http_session, selected, log=logger,
    )

    with _lock:
        live = user_data.get(user_id)
        current = dict((live or {}).get("selected_tour") or {})
        if (
            live is None
            or live.get("state") != STATE_REVIEW
            or str(current.get("tour_id") or "") != str(tour_id or "")
        ):
            return

        confirmed = bool(actual.get("confirmed"))
        total_price = int(actual.get("total_price") or 0)
        if confirmed and total_price > 0:
            current["price"] = total_price
            current["fuel_charge"] = 0
            current["currency"] = str(actual.get("currency") or current.get("currency") or "RUB")
        current["actualization_status"] = str(actual.get("status") or "unknown")
        current["actualization_confirmed"] = confirmed
        current["actualized_at"] = str(actual.get("actualized_at") or "")
        live["selected_tour"] = current

        if confirmed and total_price > 0:
            for key in ("_tour_offers", "_tour_offers_base"):
                pool = list(live.get(key) or [])
                for item in pool:
                    if str(item.get("tour_id") or "") == str(tour_id or ""):
                        item["price"] = current["price"]
                        item["fuel_charge"] = 0
                        item["currency"] = current["currency"]
                        item["actualization_status"] = current["actualization_status"]
                        item["actualization_confirmed"] = True
                        item["actualized_at"] = current["actualized_at"]
                live[key] = pool

    if actual.get("confirmed"):
        intro = "✅ Слетать.ру перепроверил выбранный тур."
        price_line = f"\n💰 Актуализированная цена: {_compact_tour_price(current)} за тур"
    elif actual.get("status") == "unavailable":
        intro = "⚠️ При актуализации предложение не подтвердилось."
        price_line = "\nМенеджер сможет проверить ближайшую альтернативу."
    else:
        intro = "ℹ️ Автоматически подтвердить наличие сейчас не удалось."
        price_line = "\nЦена и наличие остаются предварительными до проверки менеджером."

    send_message(
        user_id,
        f"{intro}\n\n{_selected_tour_summary({'selected_tour': current})}\n\n"
        f"{actual['flight_status']}\n"
        f"{actual['hotel_status']}"
        f"{price_line}\n\n"
        "Можно передать этот вариант Наталье для финальной проверки 👇",
        keyboard=_selected_tour_keyboard(),
    )


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

    provider = str(offer.get("provider") or "").lower()
    if provider == "sletat" and offer.get("provider_search_id"):
        send_message(
            user_id,
            f"✅ Вы выбрали вариант №{number}:\n\n{_selected_tour_summary({'selected_tour': offer})}\n\n"
            "⏳ Перепроверяю цену и наличие в Слетать.ру…",
            keyboard=_hide_keyboard(),
        )
        if SYNC_COMPLETION:
            _actualize_selected_tour_worker(user_id, str(offer.get("tour_id") or ""))
        else:
            threading.Thread(
                target=_actualize_selected_tour_worker,
                args=(user_id, str(offer.get("tour_id") or "")),
                daemon=True,
                name=f"vk-tour-actualize-{user_id}",
            ).start()
        return

    actual = _tour_providers.actualize_offer(
        _tour_provider_settings(), http_session, offer, log=logger,
    )
    send_message(
        user_id,
        f"✅ Вы выбрали вариант №{number}:\n\n{_selected_tour_summary({'selected_tour': offer})}\n\n"
        f"{actual['flight_status']}\n"
        f"{actual['hotel_status']}\n\n"
        "Цена и наличие предварительные до проверки менеджером. "
        "Можно передать вариант Наталье 👇",
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
        prev_mode = (live or {}).get("_tour_filter_mode")
        current_page = int((live or {}).get("_tour_page") or 0)
    if not base:
        send_message(user_id, "Сначала нажмите «Показать варианты».", keyboard=_review_keyboard())
        return

    if mode == "cheaper":
        offers = sorted(base, key=_offer_total_price)
    elif mode == "better":
        offers = sorted(
            base,
            key=lambda offer: (-int(offer.get("category") or 0), -float(offer.get("rating") or 0), _offer_total_price(offer)),
        )
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

    with _lock:
        live = user_data.get(user_id)
        if live is None or live.get("state") != STATE_REVIEW:
            return
        page_count = max(1, (len(offers) + 2) // 3)
        if prev_mode == mode:
            target_page = (current_page + 1) % page_count
        else:
            target_page = 0
        live["_tour_filter_mode"] = mode
        live["_tour_offers"] = list(offers)
        live["_tour_page"] = target_page
        live.pop("selected_tour", None)

    _send_tour_results_page(user_id, target_page)


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


def _show_hotel_info(user_id: int) -> None:
    with _lock:
        live = user_data.get(user_id) or {}
        selected = live.get("selected_tour")
        if not selected and live.get("_tour_offers"):
            selected = live["_tour_offers"][0]
    if not isinstance(selected, dict):
        send_message(user_id, "Сначала выберите тур из подборки.", keyboard=_tour_results_keyboard())
        return

    hotel_name = str(selected.get("hotel") or "Отель")
    category = int(selected.get("category") or 0)
    stars = f" {category}★" if category else ""
    region = str(selected.get("region") or "")
    details = _tourvisor.get_hotel_details(hotel_name, region=region)
    lines = [
        f"🏨 {hotel_name}{stars} ({region})",
        f"⭐ Рейтинг TopHotels: {details['rating']}/5 · {details['recommend_pct']}% реком. (отзывов: {details['reviews_count']})\n",
        f"🏖 Пляж: {details['beach']}",
        f"🏊 Бассейны: {details['pools']}",
        f"🍽 Питание: {details['meal_concept']}",
        f"👶 Детям: {details['kids']}",
        f"📶 Интернет: {details['wifi']}\n",
        "Зафиксировать этот отель в заявку менеджеру?",
    ]
    send_message(user_id, "\n".join(lines), keyboard=_selected_tour_keyboard())


def _start_tour_search(
    user_id: int,
    info: Dict[str, Any],
    *,
    ignore_budget: bool = False,
) -> None:
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
    info.pop("_tour_wait_final_text", None)
    info["_tour_searching"] = True
    info["_tour_search_marker"] = marker
    snapshot = dict(info)
    if ignore_budget:
        snapshot["budget_open_ended"] = True
        snapshot["_show_over_budget"] = True
    waiting_text = _tour_search_wait_text("⏳ Запрашиваю предложения у туроператоров…")
    wait_message_id = info.get("_review_message_id")
    if not edit_message(
        user_id,
        wait_message_id,
        waiting_text,
        keyboard=_tour_search_wait_keyboard(),
    ):
        response = send_message(
            user_id,
            waiting_text,
            keyboard=_tour_search_wait_keyboard(),
        )
        wait_message_id = _message_id(response)
    send_typing(user_id)
    if SYNC_COMPLETION:
        _tour_search_worker(user_id, marker, snapshot, wait_message_id)
    else:
        if wait_message_id:
            threading.Thread(
                target=_tour_search_wait_animation,
                args=(user_id, marker, wait_message_id),
                daemon=True,
                name=f"vk-tour-wait-{user_id}",
            ).start()
        threading.Thread(
            target=_tour_search_worker,
            args=(user_id, marker, snapshot, wait_message_id),
            daemon=True,
            name=f"vk-tour-search-{user_id}",
        ).start()


def _step_review(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    if text in (
        TOUR_SEARCH_BUTTON_TEXT,
        "🔎 Показать отели и цены",
        "Показать отели и цены",
        "🔎 Показать варианты",
        "Показать варианты",
        "показать варианты",
        "показать туры",
        "показать отели",
    ):
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
    if text == TOUR_SHOW_OVER_BUDGET_TEXT:
        _start_tour_search(user_id, info, ignore_budget=True)
        return
    if text == REVIEW_HOTEL_TEXT:
        info["state"] = STATE_HOTEL
        _ask_hotel(user_id)
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
    if text in (TOUR_HOTEL_INFO_TEXT, "описание отеля", "об отеле", "пляж"):
        _show_hotel_info(user_id)
        return
    if text == TOUR_SIMILAR_TEXT:
        _show_similar_tours(user_id)
        return
    if text == TOUR_COMPARE_TEXT:
        _compare_tours(user_id)
        return
    if text in (
        REVIEW_CONFIRM_TEXT,
        TOUR_SEND_MANAGER_TEXT,
        TOUR_SEND_MANAGER_LEGACY_TEXT,
        TOUR_SEND_SELECTED_TEXT,
    ):
        info.pop("_tour_searching", None)
        info.pop("_tour_search_marker", None)
        info["contact_method"] = "vk"
        client_name = message.get("_user_name") or f"VK {user_id}"
        handle_completion(user_id, f"VK (чат id {user_id}) · {client_name}", message)
        return
    if text in (CONTACT_PHONE_TEXT, "телефон", "phone", "📱 телефон"):
        info["contact_method"] = "phone"
        info["state"] = STATE_PHONE
        send_message(
            user_id,
            "📱 Укажите номер телефона (+7…):",
            keyboard=_nav_keyboard(),
        )
        return
    if text in (CONTACT_OTHER_TEXT, CONTACT_OTHER_LEGACY_TEXT, "📞 Способ связи", "📞 Связь", "связь", "способ связи"):
        info["state"] = STATE_CONTACT
        _ask_contact(user_id)
        return
    if text == CONTACT_MAX_TEXT:
        info["contact_method"] = "max"
        info["state"] = STATE_MAX
        _ask_max_contact(user_id)
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
    STATE_NIGHTS:      _nights_keyboard,
    STATE_HOTEL:       _nav_keyboard,
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
    STATE_NIGHTS:      _step_nights,
    STATE_HOTEL:       _step_hotel,
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
    STATE_NIGHTS:      STATE_DATES,
    STATE_HOTEL:       STATE_REVIEW,
    STATE_PEOPLE:      STATE_NIGHTS,
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
    elif state == STATE_NIGHTS:
        _ask_nights(user_id)
    elif state == STATE_HOTEL:
        _ask_hotel(user_id)
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


def _get_previous_state(info: Dict[str, Any]) -> Optional[str]:
    state = info.get("state")
    if state == STATE_REVIEW:
        if info.get("destination") == "🔥 Горящие туры":
            return STATE_DESTINATION
        return STATE_BUDGET
    if state == STATE_BUDGET:
        # If user has kids in party, go back to kids ages; otherwise skip back directly to people!
        if info.get("kids_ages") or info.get("kids"):
            return STATE_KIDS_AGES
        return STATE_PEOPLE
    if state in (STATE_KIDS_AGES, STATE_KIDS, STATE_INFANTS):
        return STATE_PEOPLE
    if state == STATE_PEOPLE:
        if info.get("dates_are_trip") is True:
            return STATE_DATES
        return STATE_NIGHTS
    return PREVIOUS_STATE.get(state)


def _go_back(user_id: int) -> None:
    info = user_data.get(user_id, {})
    state = info.get("state")
    previous = _get_previous_state(info)
    if previous is None or state == STATE_DESTINATION:
        send_message(user_id, "Вы на первом шаге. Можно отменить заявку кнопкой «Отмена».", keyboard=_dest_keyboard())
        return
    info["state"] = previous
    _mark_dirty(user_id, user=False)
    _prompt_for_state(user_id, previous)


def handle_dialog(user_id: int, text: str, message: Dict[str, Any]) -> None:
    info = user_data.get(user_id) or _restore_session_from_db(user_id) or {}
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
        "✅ Заявка принята! Наталья Ильина, менеджер «АПРЕЛЬ тур», свяжется с вами.\n"
        f"☎️ Контакт: {LEAD_OWNER_PHONE}\n\n"
        f"📍 Направление: {info.get('destination', '?')}\n"
        + (f"🛫 Откуда: {info['origin']}\n" if info.get("origin") else "")
        + f"📅 Даты: {info.get('dates', '?')}\n"
        + (f"🌙 Длительность: {_tourvisor.nights_label(info['nights'])}\n" if info.get("nights") else "")
        + (f"🏨 Отель: {info['hotel_query']}\n" if info.get("hotel_query") else "")
        + f"👥 Состав: {_party_text(info)}\n"
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
    vk_chat_link = f"https://vk.com/gim{VK_GROUP_ID}?sel={user_id}" if VK_GROUP_ID else f"https://vk.com/im?sel={user_id}"
    vk_profile_link = f"https://vk.com/id{user_id}"
    text = (
        "🔔 Новая заявка (VK)!\n\n"
        f"От: {client_name or 'без имени'}\n"
        f"💬 Диалог: {vk_chat_link}\n"
        f"👤 Профиль: {vk_profile_link}\n\n"
        + (f"📊 Источник: {info['source_tag']}\n" if info.get("source_tag") else "")
        + f"📍 Направление: {info.get('destination', '?')}\n"
        + (f"🛫 Откуда: {info['origin']}\n" if info.get("origin") else "")
        + f"📅 Даты: {info.get('dates', '?')}\n"
        + (f"🌙 Ночи: {_tourvisor.nights_label(info['nights'])}\n" if info.get("nights") else "")
        + (f"🏨 Отель: {info['hotel_query']}\n" if info.get("hotel_query") else "")
        + f"👥 Состав: {_party_text(info)}\n"
        f"💰 Бюджет: {_budget_summary(info)}\n"
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
                logger.info("VK lead from %s delivered to Telegram manager %s", _log_correlation(user_id, namespace="vk-user"), _log_correlation(recipient, namespace="tg-manager"))
            else:
                logger.error(
                    "Telegram notify failed for %s→%s: %s",
                    _log_correlation(user_id, namespace="vk-user"), _log_correlation(recipient, namespace="tg-manager"), resp.text[:200],
                )
        except Exception as exc:
            logger.error("Telegram notify error for VK lead %s: %s", _log_correlation(user_id, namespace="vk-user"), exc)


def _notify_admin(user_id: int, info: Dict[str, Any], phone: str, client_name: Optional[str]) -> None:
    """Deliver every VK lead to Natalya's private messages plus optional ops copies."""
    _notify_admin_telegram(user_id, info, phone, client_name)
    selected = _selected_tour_summary(info)
    owner_message = (
        "🔔 Новая заявка (VK)!\n"
        f"👩‍💼 Владелец: {LEAD_OWNER_NAME}\n\n"
        f"От: {client_name or 'без имени'} (ID: {user_id})\n"
        f"💬 Диалог: https://vk.com/gim{VK_GROUP_ID}?sel={user_id}\n"
        f"👤 Профиль: https://vk.com/id{user_id}\n\n"
        + (f"📊 Источник: {info['source_tag']}\n" if info.get("source_tag") else "")
        + f"📍 {info.get('destination', '?')}\n"
        + (f"🛫 Откуда: {info['origin']}\n" if info.get("origin") else "")
        + f"📅 {info.get('dates', '?')}\n"
        + (f"🌙 {_tourvisor.nights_label(info['nights'])}\n" if info.get("nights") else "")
        + (f"🏨 {info['hotel_query']}\n" if info.get("hotel_query") else "")
        + f"👥 {_party_text(info)}\n"
        + f"💰 {_budget_summary(info)}\n"
        + f"📞 Связь клиента: {phone}"
        + (f"\n\n🎯 Выбранный тур:\n{selected}" if selected else "")
        + "\n\n🔎 Подбор менеджеру:\n"
        + f"Tourvisor PRO: {MANAGER_TOURVISOR_URL}\n"
        + f"Sletat PRO: {MANAGER_SLETAT_URL}\n"
        + f"Qui-Quo: {MANAGER_QUIQUO_URL}"
    )
    owner_result = send_message(LEAD_OWNER_VK_ID, owner_message) if LEAD_OWNER_VK_ID else None
    if owner_result is None:
        logger.error("VK lead from %s was not delivered to owner PM", _log_correlation(user_id, namespace="vk-user"))

    if ADMIN_ID and ADMIN_ID != LEAD_OWNER_VK_ID:
        send_message(
            ADMIN_ID,
            "🔔 Новая заявка (VK)!\n\n"
            f"От: {client_name or 'без имени'} (ID: {user_id})\n"
            + (f"📊 Источник: {info['source_tag']}\n" if info.get("source_tag") else "")
            + f"📍 {info.get('destination', '?')}\n"
            + (f"🛫 Откуда: {info['origin']}\n" if info.get("origin") else "")
            + f"📅 {info.get('dates', '?')}\n"
            + (f"🌙 {_tourvisor.nights_label(info['nights'])}\n" if info.get("nights") else "")
            + (f"🏨 {info['hotel_query']}\n" if info.get("hotel_query") else "")
            + f"👥 {_party_text(info)}\n"
            f"💰 {_budget_summary(info)}\n"
            f"📞 Связь: {phone}"
            + (f"\n\n🎯 Выбранный тур:\n{selected}" if selected else ""),
        )
    elif not LEAD_NOTIFY_IDS and owner_result is None:
        logger.warning("VK lead from %s has no working manager delivery channel", _log_correlation(user_id, namespace="vk-user"))


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
            origin=str(info.get("origin", "")).split("/")[0].strip(),
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
                json={
                    "chat_id": recipient,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=HTTP_TIMEOUT,
            )
        except Exception as exc:
            logger.error("VK Tutu admin notify failed: %s", exc)


def _post_completion_side_effects(
    user_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
    lead_id: Optional[int] = None,
) -> None:
    """MDT push + live offers + AI blurb — off the VK Callback hot path."""
    try:
        delivery_info = info
        if lead_id is not None:
            delivery_info = dict(info)
            delivery_info["_mdt_delivery_key"] = f"vk-lead-{lead_id}"
        if lead_id is not None and MDT_MODE == "lead":
            _deliver_mdt_lead(lead_id)
        elif MDT_ENABLED and not DEMO_MODE:
            # preorder/both remain one-shot because their multi-call transaction
            # cannot be retried safely without server-side idempotency. Capture
            # the production VK preorder IDs so diagnostics can prove the write
            # without exposing customer data.
            if lead_id is not None and MDT_MODE == "preorder":
                preorder_id, tourist_id = send_preorder_to_mdt(
                    user_id, delivery_info, phone, client_name
                )
                _record_mdt_preorder_result(lead_id, preorder_id, tourist_id)
            else:
                send_lead_to_mdt(user_id, delivery_info, phone, client_name)

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
        logger.error("VK post-completion side effects failed for %s: %s", _log_correlation(user_id, namespace="vk-user"), exc)


def handle_completion(user_id: int, phone: str, message: Dict[str, Any]) -> None:
    # VK can deliver separate, valid events almost simultaneously. Guarding
    # completion prevents duplicate leads and duplicate manager notifications.
    with _lock:
        live = user_data.get(user_id)
        if live is None or live.get("_completing"):
            logger.info("Concurrent VK completion ignored for %s", _log_correlation(user_id, namespace="vk-user"))
            return
        live["_completing"] = True
        info = dict(live)
    info.pop("_completing", None)
    client_name = message.get("_user_name") or f"VK {user_id}"

    lead_id: Optional[int] = None
    try:
        lead_id = save_lead(user_id, info, phone, first_name=client_name)
    except Exception as exc:
        logger.error("Failed to save VK lead for %s: %s", _log_correlation(user_id, namespace="vk-user"), exc)

    _confirm_to_user(user_id, info, phone)
    _notify_admin(user_id, info, phone, client_name)
    with _lock:
        user_data.pop(user_id, None)
    delete_session(user_id)
    _delete_miniapp_snapshot(user_id)

    if SYNC_COMPLETION:
        _post_completion_side_effects(user_id, info, phone, client_name, lead_id)
    else:
        threading.Thread(
            target=_post_completion_side_effects,
            args=(user_id, info, phone, client_name, lead_id),
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
    "проверить заявку": "review", "проверить": "review",
    "помощь": "help", "справка": "help",
    "политика": "privacy",
    "удалить": "delete",
    "аналитика": "analytics", "статистика": "analytics",
    "экспорт": "export", "заявки": "export",
    "рассылка": "broadcast",
    "напоминания": "followup",
    "crm статус": "crm_status", "mdt статус": "crm_status",
}


def _mdt_admin_status_text() -> str:
    """Return a PII-free summary of the latest local MDT delivery state."""
    with _db_cursor() as cur:
        row = cur.execute(
            """
            SELECT id, mdt_status, mdt_attempts, mdt_preorder_id,
                   mdt_tourist_id, mdt_synced_at, created_at
            FROM leads ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    if row is None:
        return "🔧 CRM статус\nЗаявок пока нет."

    now = int(time.time())
    age_seconds = max(0, now - int(row["created_at"] or now))
    age_minutes = age_seconds // 60
    preorder_id = row["mdt_preorder_id"]
    tourist_id = row["mdt_tourist_id"]
    synced_at = row["mdt_synced_at"]
    return (
        "🔧 CRM статус\n"
        f"Локальная заявка: #{int(row['id'])}\n"
        f"MDT: {row['mdt_status'] or 'unset'}\n"
        f"Попытки: {int(row['mdt_attempts'] or 0)}\n"
        f"Preorder ID: {int(preorder_id) if preorder_id is not None else '—'}\n"
        f"Tourist ID: {int(tourist_id) if tourist_id is not None else '—'}\n"
        f"Синхронизация: {'есть' if synced_at else 'нет'}\n"
        f"Возраст записи: {age_minutes} мин."
    )


def _remember_client_capabilities(user_id: int, event: Dict[str, Any]) -> None:
    """Прочитать client_info из message_new: умеет ли клиент inline-кнопки."""
    info = event.get("object", {}).get("client_info")
    if not isinstance(info, dict) or "inline_keyboard" not in info:
        return
    if info.get("inline_keyboard"):
        _NO_INLINE.discard(user_id)
    else:
        _NO_INLINE.add(user_id)


def _process_app_payload(event: Dict[str, Any]) -> None:
    """Restore a saved Mini App draft after VKWebAppSendPayload.

    Callback secret validation happens in ``vk_webhook`` before this helper is
    scheduled. The event is still bound to the configured community and Mini
    App so a payload from another installed app cannot open somebody else's
    draft. No lead is created here; the user still confirms the review in chat.
    """
    obj = event.get("object")
    if not isinstance(obj, dict):
        logger.warning("VK app_payload ignored: invalid object")
        return

    try:
        user_id = int(obj.get("user_id") or 0)
        app_id = int(obj.get("app_id") or 0)
        event_group_id = int(event.get("group_id") or 0)
        configured_app_id = int(str(os.getenv("VK_MINI_APP_ID", "")).strip() or "0")
    except (TypeError, ValueError):
        logger.warning("VK app_payload ignored: invalid identity")
        return

    if (
        user_id <= 0
        or configured_app_id <= 0
        or app_id != configured_app_id
        or (VK_GROUP_ID > 0 and event_group_id != VK_GROUP_ID)
    ):
        logger.warning("VK app_payload ignored: wrong app/community")
        return

    raw_payload = obj.get("payload")
    if isinstance(raw_payload, str):
        try:
            payload = json.loads(raw_payload)
        except (TypeError, ValueError):
            logger.warning("VK app_payload ignored: malformed payload")
            return
    elif isinstance(raw_payload, dict):
        payload = raw_payload
    else:
        logger.warning("VK app_payload ignored: missing payload")
        return

    if (
        not isinstance(payload, dict)
        or payload.get("command") != "miniapp_review"
        or payload.get("version") != 1
    ):
        logger.info("VK app_payload ignored: unsupported command")
        return

    snapshot = _load_miniapp_snapshot(user_id)
    if snapshot is None:
        logger.info("VK app_payload review ignored: snapshot_missing")
        return

    with _lock:
        user_data[user_id] = snapshot
    set_session(user_id, snapshot)
    _ask_review(user_id)
    logger.info("VK app_payload review restored")


def _process_message(message: Dict[str, Any]) -> None:
    """Process one VK message_new event."""
    msg = message.get("object", {}).get("message", message.get("message", {}))
    user_id = msg.get("from_id") or msg.get("peer_id")
    if not user_id:
        return
    text = (msg.get("text") or "").strip()
    incoming_source_tag = _safe_source_tag(msg.get("ref"))
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
            if incoming_source_tag and not user_data[user_id].get("source_tag"):
                user_data[user_id]["source_tag"] = incoming_source_tag
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
        if command == "crm_status":
            send_message(user_id, _mdt_admin_status_text())
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
                cur.execute(
                    "SELECT chat_id, destination, dates, people, budget, source_tag, phone "
                    "FROM leads ORDER BY created_at DESC LIMIT 50"
                )
                rows = cur.fetchall()
            if not rows:
                send_message(user_id, "Нет завершённых заявок для экспорта.")
                return
            lines = [f"📋 Экспорт ({len(rows)}):\n"]
            for i, (cid, dest, dates, people, budget, source_tag, phone) in enumerate(rows, 1):
                source = source_tag or "organic"
                lines.append(
                    f"{i}. {dest or '?'} | {dates or '?'} | {people or '?'} чел | "
                    f"{budget or '?'}₽ | src={source} | {phone}"
                )
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
        handle_start(user_id, name, source_tag=incoming_source_tag)
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
    if command == "review":
        # `sessions` is mutable FSM state. Keep Mini App's saved review separate
        # so chat navigation cannot destroy it.
        snapshot = _load_miniapp_snapshot(user_id)
        if snapshot is not None:
            with _lock:
                user_data[user_id] = snapshot
            set_session(user_id, snapshot)
            _ask_review(user_id)
            return

        # Backward compatibility for drafts saved before miniapp_drafts existed.
        info = user_data.get(user_id) or _restore_session_from_db(user_id)
        if info and info.get("source") == "vk_mini_app":
            destination = str(info.get("destination") or "")
            legacy_complete = bool(
                destination
                and destination not in (DEST_HOT_TOURS_LABEL, DEST_DIRECT_FLIGHTS_LABEL)
                and info.get("origin") and info.get("dates") and info.get("people")
                and (info.get("budget") is not None or info.get("budget_open_ended"))
            )
            if legacy_complete:
                info["state"] = STATE_REVIEW
                info.pop("selected_tour", None)
                info.pop("_tour_offers", None)
                info.pop("_tour_offers_base", None)
                info["updated_at"] = int(time.time())
                set_session(user_id, info)
                _ask_review(user_id)
                return
            send_message(
                user_id,
                "Черновик Mini App был изменён старой навигацией. "
                "Откройте приложение, проверьте параметры и сохраните их ещё раз — "
                "после этого команда «Проверить заявку» восстановит их независимо от кнопок чата.",
                keyboard=_soft_start_keyboard(),
            )
            return

        state = (info or {}).get("state")
        if state:
            _prompt_for_state(user_id, state)
        else:
            send_message(user_id, HINT_START, keyboard=_soft_start_keyboard())
        return

    if command == "menu":
        # Повторить текущий вопрос вместе с его кнопками. Mini App persists its
        # review in SQLite, so recover it if the process cache is empty (for
        # example after a restart or when returning from the embedded app).
        info = user_data.get(user_id) or _restore_session_from_db(user_id)
        state = (info or {}).get("state")
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
        live = user_data.get(user_id)
        if (
            live
            and live.get("state") == STATE_REVIEW
            and live.get("selected_tour")
            and live.get("_tour_offers")
        ):
            page = int(live.get("_tour_page") or 0)
            live.pop("selected_tour", None)
            _mark_dirty(user_id, user=False)
            _send_tour_results_page(user_id, page)
        elif user_id in user_data:
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
        if incoming_source_tag:
            # vk.me?...ref=<campaign> arrives on message_new. Treat that
            # explicit referral as a fresh acquisition entry point.
            handle_start(user_id, name, source_tag=incoming_source_tag)
        elif has_completed_lead(user_id):
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
            if d.get("dates_are_trip") is not None:
                d["dates_are_trip"] = bool(d["dates_are_trip"])
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

def _save_miniapp_draft(user_id: int, info: Dict[str, Any]) -> None:
    """Persist a Mini App review draft and make it the active chat draft.

    Clicking Save in the Mini App is an explicit request to continue with
    those parameters, so an unfinished chat draft is replaced instead of
    forcing the user through a cancel-and-retry loop. Only a draft that is
    already being completed is protected from replacement.
    """
    _, info["kids"], info["infants"] = party_bands(info)
    info["state"] = STATE_REVIEW
    set_consent(user_id)
    with _lock:
        previous = user_data.get(user_id)
        if previous and previous.get("_completing"):
            raise MiniAppValidationError("Draft completion in progress")
        source_tag = _safe_source_tag((previous or {}).get("source_tag"))
        if source_tag:
            info["source_tag"] = source_tag

        info["updated_at"] = int(time.time())
        _save_miniapp_snapshot(user_id, info)
        set_session(user_id, info)
        user_data[user_id] = info


app = Flask(__name__)
app.register_blueprint(create_blueprint(
    _save_miniapp_draft,
    lambda: (os.getenv("VK_MINI_APP_SECRET", ""), os.getenv("VK_MINI_APP_ID", ""), VK_GROUP_ID),
))
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # 1 MB — VK events are well under this


@app.route("/")
def index() -> str:
    return "TurBot VK is running!"


@app.route("/health")
@app.route("/vk/health")
def health() -> Any:
    return jsonify({
        "status": "ok",
        "platform": "vk",
        "revision": _version.REVISION,
        "uptime_seconds": _version.uptime_seconds(),
        "mdt_delivery": _mdt_delivery_health(),
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

    if event_type == "app_payload":
        # ACK the Callback API immediately. The user-facing VK API call runs
        # off the request thread, so a slow messages.send cannot make VK retry
        # the same callback and duplicate the review message.
        threading.Thread(
            target=_process_app_payload,
            args=(data,),
            daemon=True,
            name="vk-miniapp-payload",
        ).start()
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
_start_mdt_retry_worker()

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