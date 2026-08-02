from __future__ import annotations

import os
import json
import hmac
import html
import re
import sqlite3
import time
import logging
import threading
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from groq import Groq

from shared.constants import (
    STATE_BUDGET,
    STATE_CONSENT,
    STATE_CONTACT,
    STATE_DATES,
    STATE_DESTINATION,
    STATE_ORIGIN,
    STATE_PEOPLE,
    STATE_KIDS,
    STATE_INFANTS,
    STATE_PHONE,
    STATE_VK,
    PEOPLE_OPTIONS,
    KIDS_OPTIONS,
    KIDS_NONE_LABEL,
    INFANTS_OPTIONS,
    INFANTS_NONE_LABEL,
    BACK_BUTTON_TEXT,
    CANCEL_BUTTON_TEXT,
    CONSENT_YES_TEXT,
    CONSENT_NO_TEXT,
    START_BUTTON_TEXT,
    CONTACT_TG_TEXT,
    CONTACT_PHONE_TEXT,
    CONTACT_VK_TEXT,
    POPULAR_DESTINATIONS_TG,
    ORIGIN_OPTIONS_TG,
)
from shared.validation import validate_phone, validate_people, validate_budget
from shared.templates import template_selection as _template_selection
from shared.privacy import consent_text as _shared_consent_text, privacy_text as _shared_privacy_text
from shared import tutu as _tutu
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


class _TokenRedactingFilter(logging.Filter):
    """Keep the bot token out of the logs.

    The Telegram API carries the token in the URL path, and urllib3 logs the
    full URL when a connection breaks. One network blip is therefore enough to
    write the credential into journald, where it survives indefinitely and
    gets pasted verbatim into bug reports and support tickets. Observed in
    the wild, not hypothetical.

    Attached to the root handlers so it also covers libraries that log on our
    behalf — the bot's own code never prints the token.
    """

    _PATTERN = re.compile(r"bot(\d{5,}):[A-Za-z0-9_-]{20,}")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = self._PATTERN.sub(r"bot\1:<redacted>", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


for _handler in logging.root.handlers:
    _handler.addFilter(_TokenRedactingFilter())

logger = logging.getLogger("turbot")

def _env_int(name: str, default: int = 0) -> int:
    """Parse int env var; empty/invalid values fall back to default (safe for Render)."""
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        logger.warning("Invalid %s=%r — using default %s", name, raw, default)
        return default


BOT_TOKEN         = os.getenv("BOT_TOKEN", "")
ADMIN_ID          = _env_int("ADMIN_ID", 0)
GROQ_API_KEY      = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL        = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
AI_MODE           = os.getenv("AI_MODE", "groq").lower().strip()
PORT                 = _env_int("PORT", 5000)
STATE_FILE           = os.getenv("STATE_FILE", "bot_state.json")
DATABASE_PATH        = os.getenv("DATABASE_PATH", "bot_state.sqlite")
TELEGRAM_SECRET_TOKEN = os.getenv("TELEGRAM_SECRET_TOKEN", "")
DIALOG_TIMEOUT_HOURS = _env_int("DIALOG_TIMEOUT_HOURS", 6)
HTTP_TIMEOUT         = 15    # seconds for outbound HTTP calls


def _parse_chat_ids(raw: str) -> List[int]:
    """Parse comma-separated Telegram chat IDs; skip empty/invalid parts."""
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


# Who receives new leads in Telegram. LEAD_NOTIFY_IDS wins if set; otherwise ADMIN_ID.
# Comma-separated chat IDs, e.g. "123456789,987654321".
_lead_notify_raw = os.getenv("LEAD_NOTIFY_IDS", "").strip()
if _lead_notify_raw:
    LEAD_NOTIFY_IDS: List[int] = list(dict.fromkeys(_parse_chat_ids(_lead_notify_raw)))
elif ADMIN_ID:
    LEAD_NOTIFY_IDS = [ADMIN_ID]
else:
    LEAD_NOTIFY_IDS = []

# --- How updates reach the bot ----------------------------------------------
# "webhook" (default): Telegram POSTs to /webhook. Needs an inbound HTTPS path
#   from Telegram's network to this host.
# "polling": the bot pulls updates itself with getUpdates. Needs only outbound
#   access.
#
# Polling exists because some hosting networks filter Telegram's address
# ranges in BOTH directions. On one such host getWebhookInfo reported
# "Connection timed out" with updates piling up while nginx logged not a
# single request from Telegram — the webhook could never arrive, and no
# amount of configuration on this side would change that. Polling survives
# there, because the connection is opened from inside.
BOT_MODE = os.getenv("BOT_MODE", "webhook").lower().strip()
if BOT_MODE not in ("webhook", "polling"):
    BOT_MODE = "webhook"
# Long-poll window. Telegram holds the request open until an update arrives or
# this elapses, so a high value means fewer requests, not slower delivery.
POLL_TIMEOUT = _env_int("POLL_TIMEOUT", 25)
# A poller that quietly stops talking to Telegram is worse than a crash: the
# process stays up, systemd keeps printing active (running), /health keeps
# answering ok, and the outage is found by a client rather than by us. That
# happened twice. Past this many seconds without a completed getUpdates the
# bot reports itself unhealthy. One failed cycle at maximum backoff costs
# about 100s, so the default leaves room for a flaky network without crying
# wolf.
POLL_STALE_AFTER = _env_int("POLL_STALE_AFTER", 180)

# --- Tutu.ru MCP (live transport offers) -----------------------------------
# Read-only search against Tutu's public MCP server. Runs only in the
# post-completion background thread, never on the webhook critical path, and
# degrades silently to the template blurb when unavailable.
TUTU_ENABLED = os.getenv("TUTU_ENABLED", "true").lower().strip() in ("1", "true", "yes")
TUTU_ENDPOINT = os.getenv("TUTU_ENDPOINT", "https://mcp.tutu.ru/mcp").strip()
TUTU_TIMEOUT = _env_int("TUTU_TIMEOUT", 30)
TUTU_DEFAULT_ORIGIN = os.getenv("TUTU_DEFAULT_ORIGIN", "Архангельск").strip()
TUTU_MAX_OFFERS = _env_int("TUTU_MAX_OFFERS", 3)
TUTU_CACHE_TTL = _env_int("TUTU_CACHE_TTL", 900)
# Who sees the result. Client gets orientation pricing only (no checkout link —
# handing the client a "buy" button routes the sale around the agency);
# the manager gets the price anchor plus checkout links.
TUTU_SHOW_CLIENT = os.getenv("TUTU_SHOW_CLIENT", "true").lower().strip() in ("1", "true", "yes")
TUTU_SHOW_ADMIN = os.getenv("TUTU_SHOW_ADMIN", "true").lower().strip() in ("1", "true", "yes")


def _tutu_settings() -> "_tutu.TutuSettings":
    """Build settings from live env globals (mirrors the MDT pattern)."""
    return _tutu.TutuSettings(
        enabled=TUTU_ENABLED,
        endpoint=TUTU_ENDPOINT,
        timeout=TUTU_TIMEOUT,
        default_origin=TUTU_DEFAULT_ORIGIN,
        max_offers=TUTU_MAX_OFFERS,
        cache_ttl=TUTU_CACHE_TTL,
        show_client=TUTU_SHOW_CLIENT,
        show_admin=TUTU_SHOW_ADMIN,
    )


# --- Demo mode --------------------------------------------------------------
# A public portfolio instance must not quietly collect real phone numbers on
# behalf of a real registered business. In demo mode the funnel still runs end
# to end (and Tutu still returns live prices), but the bot says plainly that it
# is a showcase and stores a masked number instead of the real one.
#
# Render sets RENDER=true on every instance, so the showcase turns itself on
# without anyone remembering to flip a switch; a real deployment on a VM has no
# such variable and behaves normally. Override explicitly with DEMO_MODE.
_render_host = bool(os.getenv("RENDER"))
DEMO_MODE = os.getenv(
    "DEMO_MODE", "true" if _render_host else "false"
).lower().strip() in ("1", "true", "yes")

DEMO_NOTICE = (
    "⚠️ <b>Это демонстрационная версия</b> для портфолио.\n"
    "Заявка <b>не попадёт</b> в турагентство, а телефон не сохраняется — "
    "вводите любой номер вида +79001234567.\n"
    "Цены на перелёт при этом настоящие: они приходят из Tutu.ru."
)

# --- Personal-data compliance (152-ФЗ) ------------------------------------
# URL of the privacy policy / consent text shown to users before their personal
# data (name, phone) is collected. Operators of RF personal data MUST publish
# such a document. The bot serves its own copy at /privacy, so the link is never
# empty just because nobody hosted the document separately.
# Where this instance is reachable from outside. PUBLIC_BASE_URL is the
# portable knob (install.sh writes it); RENDER_EXTERNAL_URL is Render's own
# and needs no configuration there.
PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL", "").strip()
    or os.getenv("RENDER_EXTERNAL_URL", "").strip()
).rstrip("/")
PRIVACY_POLICY_URL = os.getenv("PRIVACY_POLICY_URL", "").strip() or (
    f"{PUBLIC_BASE_URL}/privacy" if PUBLIC_BASE_URL else ""
)
# Name of the data operator shown in the consent text.
DATA_OPERATOR_NAME = os.getenv(
    "DATA_OPERATOR_NAME",
    "ИП Замятина Мария Андреевна (ТА «АПРЕЛЬ тур», ОГРНИП 290211659807)",
)
# Days after which a client's personal data is auto-deleted (data minimisation,
# 152-ФЗ ст. 5). Set to 0 to disable automatic retention cleanup.
DATA_RETENTION_DAYS = _env_int("DATA_RETENTION_DAYS", 180)
# soft (default): no hard «Согласен» gate — short notice + flexible contact.
# strict: classic consent buttons before any questions (old behaviour).
CONSENT_MODE = os.getenv("CONSENT_MODE", "soft").lower().strip()
if CONSENT_MODE not in ("soft", "strict"):
    CONSENT_MODE = "soft"
BROADCAST_DELAY      = 0.05  # ~20 msg/s — stays under Telegram's ~30 msg/s limit
# Alert admin on critical errors (sent via Telegram message).
ADMIN_ERROR_ALERTS = os.getenv("ADMIN_ERROR_ALERTS", "true").lower().strip() in ("1", "true", "yes")
# How often to send the same error alert (seconds, to avoid spam).
ERROR_ALERT_COOLDOWN = _env_int("ERROR_ALERT_COOLDOWN", 300)

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
    MDT_REMINDER_DAYS = _env_int("MDT_REMINDER_DAYS", 1)
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
if not LEAD_NOTIFY_IDS:
    logger.warning(
        "LEAD_NOTIFY_IDS/ADMIN_ID not set — completed leads will NOT be sent to Telegram."
    )
else:
    logger.info("Lead Telegram recipients: %s", LEAD_NOTIFY_IDS)
if not TELEGRAM_SECRET_TOKEN:
    logger.warning(
        "TELEGRAM_SECRET_TOKEN is not set — the webhook accepts unauthenticated "
        "POSTs, so anyone who learns the URL can inject fake updates. Generate a "
        "random string and pass it to setWebhook."
    )
if not PRIVACY_POLICY_URL:
    logger.warning(
        "PRIVACY_POLICY_URL is not set and could not be derived — the consent "
        "text will have no policy link while the bot collects phone numbers."
    )
if DEMO_MODE:
    logger.info("DEMO_MODE is on — leads are not forwarded and phones are masked.")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POPULAR_DESTINATIONS = POPULAR_DESTINATIONS_TG

SHARE_CONTACT_TEXT = "📱 Отправить номер"

# Public profile (Telegram search / «О боте»). Limits: name 64, short 120, about 512.
BOT_DISPLAY_NAME = os.getenv("BOT_DISPLAY_NAME", "АПРЕЛЬ тур · Подбор туров").strip()[:64]
BOT_SHORT_DESCRIPTION = os.getenv(
    "BOT_SHORT_DESCRIPTION",
    "Туры из Архангельска · заявка за 1–2 минуты · перезвоним",
).strip()[:120]
BOT_DESCRIPTION = os.getenv(
    "BOT_DESCRIPTION",
    "Официальный бот турагентства «АПРЕЛЬ тур» (Архангельск).\n\n"
    "Помогу подобрать тур: направление, даты, число гостей и бюджет. "
    "Заявка уходит менеджеру — перезвоним с вариантами.\n\n"
    "Команды: /start — начать, /help — справка, /privacy — ПДн.\n"
    "ИП Замятина М.А. · ОГРНИП 290211659807",
).strip()[:512]

USER_HELP = (
    "🌴 <b>«АПРЕЛЬ тур»</b> — подбор отдыха без лишней суеты\n\n"
    "Я соберу короткую заявку и передам менеджеру. Обычно это 1–2 минуты.\n\n"
    "<b>Команды</b>\n"
    "/start — начать подбор\n"
    "/cancel — отменить заявку\n"
    "/privacy — обработка персональных данных\n"
    "/delete — удалить мои данные\n"
    "/help — эта справка\n\n"
    "<b>В диалоге</b> — кнопки: направления, гости, способ связи "
    "(Telegram / телефон / VK), назад и отмена.\n\n"
    "📋 ИП Замятина Мария Андреевна\n"
    "ТА «АПРЕЛЬ тур» · ОГРНИП 290211659807"
)

WELCOME_BODY = (
    "Подберём тур под ваши даты и бюджет — заявка уйдёт менеджеру.\n\n"
    "<b>Как это работает</b>\n"
    "1) несколько вопросов (куда, когда, кто, бюджет)\n"
    "2) удобный способ связи: Telegram, телефон или VK\n"
    "3) менеджер напишет или позвонит\n\n"
    "Около минуты. Данные — только чтобы связаться по заявке "
    "(подробнее: /privacy)."
)


def _welcome_text(first_name: str = "") -> str:
    """First-touch greeting (HTML). Name is escaped for safety."""
    if first_name:
        safe = html.escape(first_name, quote=False)
        head = f"🌴 <b>Добро пожаловать, {safe}!</b>"
    else:
        head = "🌴 <b>Добро пожаловать в «АПРЕЛЬ тур»!</b>"
    if DEMO_MODE:
        return f"{head}\n\n{DEMO_NOTICE}\n\n{WELCOME_BODY}"
    return f"{head}\n\n{WELCOME_BODY}"

HINT_START = (
    "Чтобы подобрать тур, нажмите /start\n"
    "Справка — /help · данные — /privacy"
)

# Inline callback_data (≤64 bytes). Stable codes so button labels can change freely.
CB_CONSENT_YES = "c:yes"
CB_CONSENT_NO = "c:no"
CB_START = "c:start"
CB_DEST_PREFIX = "d:"
CB_ORIGIN_PREFIX = "or:"
CB_DATE_PREFIX = "dt:"
CB_PEOPLE_PREFIX = "p:"
CB_KIDS_PREFIX = "kd:"
CB_INFANTS_PREFIX = "inf:"
CB_BUDGET_PREFIX = "bd:"
CB_CONTACT_TG = "ct:tg"
CB_CONTACT_PHONE = "ct:phone"
CB_CONTACT_VK = "ct:vk"
CB_BACK = "nav:back"
CB_CANCEL = "nav:cancel"

# Quick picks (callback suffix → value stored in the lead)
DATE_PRESETS: List[Tuple[str, str]] = [
    ("🏖 Ближайшие выходные", "ближайшие выходные"),
    ("📅 Через 1–2 недели", "через 1-2 недели"),
    ("🗓 Через месяц", "через месяц"),
    ("☀️ Лето", "лето"),
    ("❄️ Зима", "зима"),
    ("🤷 Даты гибкие", "даты гибкие"),
]
ORIGIN_OPTIONS: List[str] = ORIGIN_OPTIONS_TG
BUDGET_PRESETS: List[Tuple[str, int]] = [
    ("до 40 000 ₽", 40000),
    ("60 000 ₽", 60000),
    ("80 000 ₽", 80000),
    ("100 000 ₽", 100000),
    ("150 000 ₽", 150000),
    ("200 000+ ₽", 200000),
]

BOT_COMMANDS = [
    {"command": "start", "description": "🌴 Начать подбор тура"},
    {"command": "help", "description": "ℹ️ Справка и контакты"},
    {"command": "cancel", "description": "❌ Отменить заявку"},
    {"command": "privacy", "description": "🔒 Персональные данные"},
    {"command": "delete", "description": "🗑 Удалить мои данные"},
]


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
    "/send — ответить на <b>последнюю</b> заявку (далее пишете текст)\n"
    "/send {chat_id} — ответить этому клиенту (далее пишете текст)\n"
    "/send {chat_id} {текст} — сразу отправить\n"
    "/cancel_reply — отменить режим ответа\n"
    "/broadcast {текст} — рассылка всем\n"
    "/broadcast {направление} {текст} — рассылка по направлению\n"
    "/users — список пользователей\n"
    "/stats — статистика\n"
    "/restart — сбросить все активные сессии\n"
    "/analytics — аналитика (заявки, направления)\n"
    "/export — экспорт завершённых заявок\n"
    "/followup — напоминания незавершившим\n"
    "/mdt [test|reload] — статус MDT CRM\n"
    "/help — эта справка\n\n"
    "В уведомлении о заявке есть кнопка «✍️ Ответить клиенту».\n"
    "HTML: <b>жирный</b>, <i>курсив</i>"
)

# Admin → client reply flow (pending text after button /send).
_admin_reply_to: Dict[int, int] = {}  # admin_chat_id → client_chat_id
_last_lead_client_id: Optional[int] = None
CB_ADMIN_REPLY_PREFIX = "ar:"

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

# Liveness that means something. "The process is up" is the wrong question;
# "when did Telegram last answer us" is the right one. Written by the poller,
# read by /health. No lock: rebinding a float is atomic under the GIL, and a
# reader that catches the previous value is one poll cycle stale at worst.
_last_poll_ok: float = 0.0
_last_update_at: float = 0.0


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
                origin TEXT,
                dates TEXT,
                people TEXT,
                kids INTEGER,
                infants INTEGER,
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
                origin TEXT,
                dates TEXT,
                people TEXT,
                kids INTEGER,
                infants INTEGER,
                budget INTEGER,
                phone TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        # Additive migration for databases created before the origin step.
        for _table in ("sessions", "leads"):
            cur.execute(f"PRAGMA table_info({_table})")
            _cols = {row[1] for row in cur.fetchall()}
            if "origin" not in _cols:
                cur.execute(f"ALTER TABLE {_table} ADD COLUMN origin TEXT")
            # Age bands: existing rows predate the question, so NULL there
            # honestly means "not asked", not "zero children".
            for _c in ("kids", "infants"):
                if _c not in _cols:
                    cur.execute(f"ALTER TABLE {_table} ADD COLUMN {_c} INTEGER")
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
            INSERT INTO sessions (chat_id, state, destination, origin, dates, people,
                                  kids, infants, budget, phone, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                state=excluded.state,
                destination=excluded.destination,
                origin=excluded.origin,
                dates=excluded.dates,
                people=excluded.people,
                kids=excluded.kids,
                infants=excluded.infants,
                budget=excluded.budget,
                phone=excluded.phone,
                updated_at=excluded.updated_at
            """,
            (
                chat_id,
                data.get("state", ""),
                data.get("destination"),
                data.get("origin"),
                data.get("dates"),
                data.get("people"),
                data.get("kids"),
                data.get("infants"),
                data.get("budget"),
                data.get("phone"),
                data.get("updated_at", now),
            ),
        )


def update_session(chat_id: int, **kwargs) -> None:
    """Update specific fields of an existing session."""
    allowed = {"state", "destination", "origin", "dates", "people", "kids",
               "infants", "budget", "phone", "updated_at"}
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


def mask_phone(phone: str) -> str:
    """Keep the shape of a number without keeping the number.

    Used in demo mode so a public showcase never persists a real subscriber
    number: +79161234567 → +7916***4567.
    """
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
    """Persist a completed tour request for export/analytics."""
    now = int(time.time())
    if DEMO_MODE:
        phone = mask_phone(phone)
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO leads (
                chat_id, first_name, username, destination, origin, dates,
                people, kids, infants, budget, phone, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                info.get("infants"),
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

FOLLOWUP_DELAY_HOURS = _env_int("FOLLOWUP_DELAY_HOURS", 3)


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


def answer_callback(callback_query_id: str, text: str = "") -> None:
    """Acknowledge a callback_query so Telegram stops the loading spinner."""
    if not BOT_TOKEN or not callback_query_id:
        return
    payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:200]
    try:
        telegram_session.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
            json=payload,
            timeout=5,
        )
    except Exception as exc:
        logger.debug("answerCallbackQuery failed: %s", exc)


def clear_inline_keyboard(chat_id: int, message_id: int) -> None:
    """Remove inline buttons from a message after the user picks one."""
    if not BOT_TOKEN:
        return
    try:
        telegram_session.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageReplyMarkup",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": {"inline_keyboard": []},
            },
            timeout=5,
        )
    except Exception:
        pass


def _tg_api_ok(method: str, payload: Dict[str, Any]) -> bool:
    """POST to Bot API; return True if ok. Logs failures at warning level."""
    if not BOT_TOKEN:
        return False
    try:
        resp = telegram_session.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            return True
        logger.warning("%s failed: %s", method, resp.text[:200])
    except Exception as exc:
        logger.warning("%s error: %s", method, exc)
    return False


def ensure_bot_commands() -> None:
    """Register the slash-command menu via API (no BotFather button setup needed)."""
    if _tg_api_ok("setMyCommands", {"commands": BOT_COMMANDS}):
        logger.info("Bot commands registered (%s)", len(BOT_COMMANDS))


def ensure_bot_profile() -> None:
    """Name, short/long description and menu for search + first open in Telegram."""
    if not BOT_TOKEN:
        return
    if BOT_DISPLAY_NAME and _tg_api_ok("setMyName", {"name": BOT_DISPLAY_NAME}):
        logger.info("Bot name set: %s", BOT_DISPLAY_NAME)
    if BOT_SHORT_DESCRIPTION and _tg_api_ok(
        "setMyShortDescription", {"short_description": BOT_SHORT_DESCRIPTION},
    ):
        logger.info("Bot short description set (%s chars)", len(BOT_SHORT_DESCRIPTION))
    if BOT_DESCRIPTION and _tg_api_ok(
        "setMyDescription", {"description": BOT_DESCRIPTION},
    ):
        logger.info("Bot description set (%s chars)", len(BOT_DESCRIPTION))
    # Menu button opens the command list (familiar «☰» UX).
    _tg_api_ok(
        "setChatMenuButton",
        {"menu_button": {"type": "commands"}},
    )
    ensure_bot_commands()


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


def _inline_btn(text: str, callback_data: str) -> Dict[str, str]:
    return {"text": text, "callback_data": callback_data}


def inline_keyboard(rows: List[List[Dict[str, str]]]) -> str:
    """Build an InlineKeyboardMarkup JSON string (buttons under the message)."""
    return json.dumps({"inline_keyboard": rows})


def reply_keyboard(
    options: list,
    one_time: bool = True,
    extra_rows: Optional[list] = None,
) -> str:
    """Build a ReplyKeyboardMarkup JSON string (legacy; prefer inline_keyboard)."""
    rows = [[opt] for opt in options]
    if extra_rows:
        rows.extend(extra_rows)
    return json.dumps({
        "keyboard": rows,
        "resize_keyboard": True,
        "one_time_keyboard": one_time,
    })


def kb_consent() -> str:
    """Inline: agree / decline personal-data consent (strict mode)."""
    return inline_keyboard([[
        _inline_btn(CONSENT_YES_TEXT, CB_CONSENT_YES),
        _inline_btn(CONSENT_NO_TEXT, CB_CONSENT_NO),
    ]])


def kb_soft_start() -> str:
    """Inline: one-tap start after short privacy notice (soft mode)."""
    return inline_keyboard([[_inline_btn(START_BUTTON_TEXT, CB_START)]])


def kb_contact_methods() -> str:
    """Inline: how the manager should reach the client."""
    return inline_keyboard([
        [_inline_btn(CONTACT_TG_TEXT, CB_CONTACT_TG)],
        [_inline_btn(CONTACT_PHONE_TEXT, CB_CONTACT_PHONE)],
        [_inline_btn(CONTACT_VK_TEXT, CB_CONTACT_VK)],
        [
            _inline_btn(BACK_BUTTON_TEXT, CB_BACK),
            _inline_btn(CANCEL_BUTTON_TEXT, CB_CANCEL),
        ],
    ])


def kb_destinations() -> str:
    """Inline: popular destinations in two columns + cancel."""
    rows: List[List[Dict[str, str]]] = []
    row: List[Dict[str, str]] = []
    for i, label in enumerate(POPULAR_DESTINATIONS):
        row.append(_inline_btn(label, f"{CB_DEST_PREFIX}{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_inline_btn(CANCEL_BUTTON_TEXT, CB_CANCEL)])
    return inline_keyboard(rows)


def kb_origin() -> str:
    """Inline: departure cities in two columns + nav."""
    rows: List[List[Dict[str, str]]] = []
    row: List[Dict[str, str]] = []
    for i, label in enumerate(ORIGIN_OPTIONS):
        row.append(_inline_btn(label, f"{CB_ORIGIN_PREFIX}{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        _inline_btn(BACK_BUTTON_TEXT, CB_BACK),
        _inline_btn(CANCEL_BUTTON_TEXT, CB_CANCEL),
    ])
    return inline_keyboard(rows)


def kb_dates() -> str:
    """Inline: date presets + free-text + nav."""
    rows: List[List[Dict[str, str]]] = []
    row: List[Dict[str, str]] = []
    for i, (label, _val) in enumerate(DATE_PRESETS):
        row.append(_inline_btn(label, f"{CB_DATE_PREFIX}{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_inline_btn("✏️ Свои даты", f"{CB_DATE_PREFIX}custom")])
    rows.append([
        _inline_btn(BACK_BUTTON_TEXT, CB_BACK),
        _inline_btn(CANCEL_BUTTON_TEXT, CB_CANCEL),
    ])
    return inline_keyboard(rows)


def kb_people() -> str:
    """Inline: party size + back/cancel."""
    opts = PEOPLE_OPTIONS
    mid = (len(opts) + 1) // 2
    rows = [
        [_inline_btn(p, f"{CB_PEOPLE_PREFIX}{p}") for p in opts[:mid]],
        [_inline_btn(p, f"{CB_PEOPLE_PREFIX}{p}") for p in opts[mid:]],
        [
            _inline_btn(BACK_BUTTON_TEXT, CB_BACK),
            _inline_btn(CANCEL_BUTTON_TEXT, CB_CANCEL),
        ],
    ]
    return inline_keyboard(rows)


def _kb_choices(options: List[str], prefix: str) -> str:
    """Inline row of short choices + nav. Used for the two age-band steps."""
    rows = [[_inline_btn(o, f"{prefix}{o}") for o in options]]
    rows.append([
        _inline_btn(BACK_BUTTON_TEXT, CB_BACK),
        _inline_btn(CANCEL_BUTTON_TEXT, CB_CANCEL),
    ])
    return inline_keyboard(rows)


def kb_kids() -> str:
    return _kb_choices(KIDS_OPTIONS, CB_KIDS_PREFIX)


def kb_infants() -> str:
    return _kb_choices(INFANTS_OPTIONS, CB_INFANTS_PREFIX)


def kb_budget() -> str:
    """Inline: budget presets + free-text + nav."""
    rows: List[List[Dict[str, str]]] = []
    row: List[Dict[str, str]] = []
    for i, (label, _val) in enumerate(BUDGET_PRESETS):
        row.append(_inline_btn(label, f"{CB_BUDGET_PREFIX}{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_inline_btn("✏️ Свой бюджет", f"{CB_BUDGET_PREFIX}custom")])
    rows.append([
        _inline_btn(BACK_BUTTON_TEXT, CB_BACK),
        _inline_btn(CANCEL_BUTTON_TEXT, CB_CANCEL),
    ])
    return inline_keyboard(rows)


def kb_nav(*, include_back: bool = True) -> str:
    """Inline: back + cancel (for free-text steps)."""
    row: List[Dict[str, str]] = []
    if include_back:
        row.append(_inline_btn(BACK_BUTTON_TEXT, CB_BACK))
    row.append(_inline_btn(CANCEL_BUTTON_TEXT, CB_CANCEL))
    return inline_keyboard([row])


def contact_keyboard() -> str:
    """Reply keyboard for contact share (Telegram only supports request_contact here).

    Navigation uses inline on the same message when possible; back/cancel also
    work as reply buttons so the user can still leave the phone step.
    """
    return json.dumps({
        "keyboard": [
            [{"text": SHARE_CONTACT_TEXT, "request_contact": True}],
            [BACK_BUTTON_TEXT],
            [CANCEL_BUTTON_TEXT],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
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


def _admin_start_reply(admin_id: int, client_id: int) -> None:
    """Arm the next admin message to be forwarded to client_id."""
    global _last_lead_client_id
    with _lock:
        _admin_reply_to[admin_id] = client_id
        _last_lead_client_id = client_id
    send_message(
        admin_id,
        f"✍️ Режим ответа клиенту <code>{client_id}</code>.\n\n"
        "Напишите <b>следующим сообщением</b> текст — уйдёт клиенту.\n"
        "Отмена: /cancel_reply",
        parse_mode="HTML",
    )


def _admin_cancel_reply(admin_id: int) -> bool:
    with _lock:
        had = _admin_reply_to.pop(admin_id, None) is not None
    if had:
        send_message(admin_id, "Режим ответа отменён.")
    else:
        send_message(admin_id, "Сейчас вы никому не отвечаете.")
    return True


def _admin_deliver_pending(admin_id: int, text: str) -> bool:
    """If admin is in reply mode, forward text to the client. Returns True if handled."""
    with _lock:
        target = _admin_reply_to.get(admin_id)
    if target is None:
        return False
    resp = send_message(
        target,
        f"💬 Сообщение от менеджера «АПРЕЛЬ тур»:\n\n{text}",
    )
    ok = resp is not None and getattr(resp, "status_code", 0) == 200
    with _lock:
        _admin_reply_to.pop(admin_id, None)
    if ok:
        send_message(admin_id, f"✅ Отправлено клиенту <code>{target}</code>", parse_mode="HTML")
    else:
        send_message(
            admin_id,
            f"❌ Не удалось отправить клиенту {target}. "
            "Клиент должен был хотя бы раз написать боту (/start).",
        )
    return True


def _admin_send(chat_id: int, arg: str) -> bool:
    """Send to a user now, or arm reply mode for the next message.

    /send                         → reply to last lead (if any)
    /send {chat_id}               → arm reply to that client
    /send {chat_id} {message}     → send immediately
    """
    arg = (arg or "").strip()
    if not arg:
        target = _last_lead_client_id
        if target:
            _admin_start_reply(chat_id, target)
        else:
            send_message(
                chat_id,
                "Пока нет «последней» заявки.\n\n"
                "Использование:\n"
                "• кнопка «✍️ Ответить» в уведомлении\n"
                "• /send {chat_id}\n"
                "• /send {chat_id} текст сообщения",
            )
        return True

    parts = arg.split(" ", 1)
    try:
        target = int(parts[0])
    except ValueError:
        send_message(chat_id, "chat_id должен быть числом. Пример: /send 123456789 Здравствуйте!")
        return True

    if len(parts) < 2 or not parts[1].strip():
        _admin_start_reply(chat_id, target)
        return True

    msg = parts[1]
    resp = send_message(target, msg, parse_mode="HTML")
    ok = resp is not None and getattr(resp, "status_code", 0) == 200
    if ok:
        send_message(chat_id, f"✅ Отправлено пользователю {target}")
    else:
        send_message(
            chat_id,
            f"❌ Не удалось отправить {target}. "
            "Пользователь должен был написать боту (/start).",
        )
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


def _admin_tutu(chat_id: int, arg: str) -> bool:
    """`/tutu` — Tutu MCP status; `/tutu test` — live search smoke test."""
    if not TUTU_ENABLED:
        send_message(chat_id, "Интеграция с Tutu отключена (TUTU_ENABLED=false).")
        return True
    settings = _tutu_settings()
    lines = [
        "🚄 Tutu MCP статус:",
        f"  Endpoint: {settings.endpoint}",
        f"  Таймаут: {settings.timeout} с",
        f"  Город вылета по умолчанию: {settings.default_origin}",
        f"  Предложений: {settings.max_offers}",
        f"  Кэш TTL: {settings.cache_ttl} с",
        f"  Показывать клиенту: {'✅' if settings.show_client else '❌'}",
        f"  Показывать админу: {'✅' if settings.show_admin else '❌'}",
    ]
    if arg.strip() == "test":
        started = time.time()
        result = _tutu.search_offers(
            settings,
            telegram_session,
            destination="Египет",
            dates_raw="через месяц",
            origin=settings.default_origin,
            people=2,
            log=logger,
        )
        elapsed = time.time() - started
        if result and result.offers:
            cheapest = result.offers[0]
            lines.append(
                f"\n✅ Поиск работает ({elapsed:.1f} с): "
                f"{result.from_city} → {result.to_city}, "
                f"от {cheapest.price:.0f} {cheapest.currency}"
            )
        else:
            lines.append(f"\n❌ Поиск не вернул предложений ({elapsed:.1f} с).")
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
def _admin_cancel_reply_cmd(chat_id: int, arg: str) -> bool:
    return _admin_cancel_reply(chat_id)


ADMIN_COMMANDS: Dict[str, Callable[[int, str], bool]] = {
    "/help":         _admin_help,
    "/users":        _admin_users,
    "/stats":        _admin_stats,
    "/analytics":    _admin_analytics,
    "/export":       _admin_export,
    "/restart":      _admin_restart,
    "/send":         _admin_send,
    "/cancel_reply": _admin_cancel_reply_cmd,
    "/broadcast":    _admin_broadcast,
    "/followup":     _admin_followup,
    "/mdt":          _admin_mdt,
    "/tutu":         _admin_tutu,
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

def _strip_emoji_prefix(text: str, options: Optional[List[str]] = None) -> str:
    """If text matches a keyboard button, return the part after the emoji.

    ``options`` defaults to the destination labels; pass another keyboard's
    labels (e.g. ORIGIN_OPTIONS) to strip those instead.
    """
    text = text.strip()
    for label in (POPULAR_DESTINATIONS if options is None else options):
        if text == label:
            parts = label.split(" ", 1)
            return parts[1] if len(parts) > 1 else label
    return text


def handle_start(chat_id: int, first_name: str = "") -> None:
    """Begin the tour-selection dialog (soft notice or strict consent)."""
    if CONSENT_MODE == "strict" and not has_consent(chat_id):
        with _lock:
            user_data[chat_id] = {"state": STATE_CONSENT, "updated_at": int(time.time())}
        _mark_dirty(chat_id)
        send_message(chat_id, _welcome_text(first_name), parse_mode="HTML")
        send_message(
            chat_id,
            _consent_text(),
            reply_markup=kb_consent(),
        )
        return

    # Soft mode: welcome + one «Начать» tap (or skip if already started before).
    if CONSENT_MODE == "soft" and not has_consent(chat_id):
        with _lock:
            user_data[chat_id] = {"state": STATE_CONSENT, "updated_at": int(time.time())}
        _mark_dirty(chat_id)
        send_message(
            chat_id,
            _welcome_text(first_name),
            parse_mode="HTML",
            reply_markup=kb_soft_start(),
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
        f"🌴 Отлично{name}! Давайте подберём тур.\n\n"
        "📍 <b>Куда хотите поехать?</b>\n\n"
        "Жмите кнопку — или напишите своё направление (Сочи, Греция…).",
        reply_markup=kb_destinations(),
        parse_mode="HTML",
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
            "❌ Заявка отменена.\n\nКогда будете готовы — /start, подберём тур заново.",
            reply_markup=hide_keyboard(),
        )
    else:
        send_message(chat_id, f"Сейчас нет активной заявки.\n\n{HINT_START}")


# --- dialog steps ---------------------------------------------------------
# Each step receives the live session dict `info` (== user_data[chat_id]) so it
# can read and advance the state in place.

def _step_consent(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    """Soft start button or strict consent buttons."""
    from_info = message.get("from", {})
    first_name = from_info.get("first_name", "")

    # Soft mode: single «Начать подбор» (records light acknowledgment via consent_at).
    if text in (START_BUTTON_TEXT, CB_START, CB_CONSENT_YES, CONSENT_YES_TEXT):
        set_consent(chat_id)
        _begin_destination(chat_id, first_name)
        return

    if CONSENT_MODE == "strict" and text in (CONSENT_NO_TEXT, CB_CONSENT_NO):
        with _lock:
            user_data.pop(chat_id, None)
        _mark_dirty(chat_id, user=False)
        delete_session(chat_id)
        send_message(
            chat_id,
            "Поняли. Без согласия на обработку данных заявку оформить нельзя.\n\n"
            "Если передумаете — /start, мы на месте 🌴",
            reply_markup=hide_keyboard(),
        )
        return

    if CONSENT_MODE == "soft":
        send_message(
            chat_id,
            "Нажмите «🚀 Начать подбор», чтобы продолжить.",
            reply_markup=kb_soft_start(),
        )
    else:
        send_message(
            chat_id,
            "Нужна одна из кнопок ниже: «✅ Согласен» или «❌ Отказаться».",
            reply_markup=kb_consent(),
        )


def _ask_origin(chat_id: int) -> None:
    send_message(
        chat_id,
        "🛫 <b>Откуда вылетаете?</b>\n\n"
        "Нужно, чтобы посчитать перелёт — цена сильно зависит от города.",
        reply_markup=kb_origin(),
        parse_mode="HTML",
    )


def _ask_dates(chat_id: int) -> None:
    send_message(
        chat_id,
        "📅 <b>Когда планируете поездку?</b>\n\n"
        "Выберите вариант кнопкой или напишите свои даты "
        "(например: 15-22 июня).",
        reply_markup=kb_dates(),
        parse_mode="HTML",
    )


def _ask_people(chat_id: int) -> None:
    send_message(
        chat_id,
        "👥 <b>Сколько взрослых поедет?</b>\n\n"
        "Только взрослые, от 12 лет — про детей спрошу следующим вопросом.\n"
        "Кнопка или число от 1 до 50.",
        reply_markup=kb_people(),
        parse_mode="HTML",
    )


def _ask_kids(chat_id: int) -> None:
    send_message(
        chat_id,
        "🧒 <b>Дети до 12 лет едут?</b>\n\n"
        "У них свой тариф — без этого расчёт будет завышен.",
        reply_markup=kb_kids(),
        parse_mode="HTML",
    )


def _ask_infants(chat_id: int) -> None:
    send_message(
        chat_id,
        "👶 <b>Есть малыши до 2 лет?</b>\n\n"
        "Они летят без отдельного места и по особому тарифу.",
        reply_markup=kb_infants(),
        parse_mode="HTML",
    )


def _ask_budget(chat_id: int) -> None:
    send_message(
        chat_id,
        "💰 <b>Бюджет на человека</b> (примерно, в рублях)\n\n"
        "Выберите кнопку или введите свою сумму.",
        reply_markup=kb_budget(),
        parse_mode="HTML",
    )


def _ask_contact(chat_id: int) -> None:
    send_message(
        chat_id,
        "📞 <b>Как удобнее связаться?</b>\n\n"
        "Можно просто Telegram (этот чат) — телефон не обязателен.\n"
        "Или укажите номер / VK.",
        reply_markup=kb_contact_methods(),
        parse_mode="HTML",
    )


def _step_destination(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    dest = _strip_emoji_prefix(text)
    if dest.lower() == "другое":
        send_message(
            chat_id,
            "✍️ Напишите ваше направление:",
            reply_markup=kb_nav(include_back=True),
        )
        return
    info["destination"] = dest
    info["state"] = STATE_ORIGIN
    _ask_origin(chat_id)


def _step_origin(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    raw = (text or "").strip()

    # Preset callback: or:0 … or:N
    if raw.startswith(CB_ORIGIN_PREFIX):
        key = raw[len(CB_ORIGIN_PREFIX):]
        if not key.isdigit() or not (0 <= int(key) < len(ORIGIN_OPTIONS)):
            send_message(chat_id, "Кнопка устарела — выберите город ещё раз.",
                         reply_markup=kb_origin())
            return
        raw = ORIGIN_OPTIONS[int(key)]

    city = _strip_emoji_prefix(raw, ORIGIN_OPTIONS)
    if city.lower() in ("другой город", "другое"):
        send_message(
            chat_id,
            "✍️ Напишите город вылета:",
            reply_markup=kb_nav(include_back=True),
        )
        return
    if not city:
        _ask_origin(chat_id)
        return

    if city.strip().lower() == str(info.get("destination", "")).strip().lower():
        # Same city both ends: the flight search would return nothing and the
        # client would silently get the fallback text instead of prices.
        send_message(
            chat_id,
            f"🤔 {_esc(city)} — это и есть ваше направление.\n\n"
            "Из какого города вылетаете?",
            reply_markup=kb_origin(),
            parse_mode="HTML",
        )
        return
    info["origin"] = city
    info["state"] = STATE_DATES
    _ask_dates(chat_id)


def _step_dates(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    raw = (text or "").strip()
    # Custom-date prompt (from button or typed marker)
    if raw in (f"{CB_DATE_PREFIX}custom", "✏️ Свои даты", "свои даты"):
        send_message(
            chat_id,
            "✍️ <b>Напишите даты обычным сообщением</b>\n\n"
            "Например: <code>15-22 сентября</code> или <code>с 3 по 10 октября</code>.\n"
            "Просто отправьте текст в чат ↓",
            reply_markup=kb_nav(include_back=True),
            parse_mode="HTML",
        )
        return
    # Preset callback: dt:0 … dt:N
    if raw.startswith(CB_DATE_PREFIX):
        key = raw[len(CB_DATE_PREFIX):]
        if key.isdigit():
            idx = int(key)
            if 0 <= idx < len(DATE_PRESETS):
                raw = DATE_PRESETS[idx][1]
            else:
                send_message(chat_id, "Кнопка устарела — выберите даты ещё раз.",
                             reply_markup=kb_dates())
                return
        else:
            send_message(chat_id, "Кнопка устарела — выберите даты ещё раз.",
                         reply_markup=kb_dates())
            return
    if not raw:
        _ask_dates(chat_id)
        return

    # Confirm what was understood. Anything unparseable is also unusable for
    # the flight search, so catching it here beats quoting the client a price
    # for dates nobody could read — and it tells them the step is alive,
    # which is the complaint that prompted this: after the keyboard collapsed
    # to Back/Cancel, typing felt like it went nowhere.
    depart, ret = _tutu.resolve_dates(raw)
    if not depart:
        send_message(
            chat_id,
            "🤔 Не разобрал эти даты.\n\n"
            "Напишите так: <code>15-22 сентября</code>\n"
            "или выберите примерный период кнопкой.",
            reply_markup=kb_dates(),
            parse_mode="HTML",
        )
        return

    info["dates"] = raw
    info["state"] = STATE_PEOPLE
    send_message(chat_id, f"📅 Понял: {_esc(_human_dates(depart, ret))}")
    _ask_people(chat_id)


def _party_text(info) -> str:
    """«2 взр. + 1 реб. + 1 млад.» — the manager prices these separately."""
    parts = [f"{info.get('people', '?')} взр."]
    kids = info.get("kids") or 0
    infants = info.get("infants") or 0
    if kids:
        parts.append(f"{kids} реб. (2–11)")
    if infants:
        parts.append(f"{infants} млад. (до 2)")
    return " + ".join(parts)


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


def _step_people(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    ok, value = validate_people(text)
    if not ok:
        send_message(
            chat_id,
            "Сколько именно взрослых? Число от 1 до 50 или «5+» — удобнее кнопкой.",
            reply_markup=kb_people(),
        )
        return
    info["people"] = value
    info["state"] = STATE_KIDS
    _ask_kids(chat_id)


def _parse_choice(raw: str, prefix: str, options: List[str], none_label: str) -> Optional[int]:
    """Read a count from a callback or its plain label. None = unrecognised."""
    value = raw[len(prefix):] if raw.startswith(prefix) else raw
    value = value.strip()
    if value == none_label or value.lower() in ("нет", "без детей"):
        return 0
    if value not in options:
        return None
    digits = "".join(c for c in value if c.isdigit())
    return int(digits) if digits else 0


def _step_kids(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    count = _parse_choice((text or "").strip(), CB_KIDS_PREFIX, KIDS_OPTIONS, KIDS_NONE_LABEL)
    if count is None:
        send_message(chat_id, "Выберите вариант кнопкой ниже.", reply_markup=kb_kids())
        return
    info["kids"] = count
    if count == 0:
        # No children at all, so the infant question cannot apply — skip it
        # rather than making every childless client tap "Нет".
        info["infants"] = 0
        info["state"] = STATE_BUDGET
        _ask_budget(chat_id)
        return
    info["state"] = STATE_INFANTS
    _ask_infants(chat_id)


def _step_infants(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    count = _parse_choice((text or "").strip(), CB_INFANTS_PREFIX, INFANTS_OPTIONS, INFANTS_NONE_LABEL)
    if count is None:
        send_message(chat_id, "Выберите вариант кнопкой ниже.", reply_markup=kb_infants())
        return
    info["infants"] = count
    info["state"] = STATE_BUDGET
    _ask_budget(chat_id)


def _step_budget(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    raw = (text or "").strip()
    if raw in (f"{CB_BUDGET_PREFIX}custom", "✏️ Свой бюджет", "свой бюджет"):
        send_message(
            chat_id,
            "✍️ Напишите бюджет числом (например: 75000):",
            reply_markup=kb_nav(include_back=True),
        )
        return
    if raw.startswith(CB_BUDGET_PREFIX):
        key = raw[len(CB_BUDGET_PREFIX):]
        if key.isdigit():
            idx = int(key)
            if 0 <= idx < len(BUDGET_PRESETS):
                info["budget"] = BUDGET_PRESETS[idx][1]
                info["state"] = STATE_CONTACT
                _ask_contact(chat_id)
                return
        send_message(chat_id, "Кнопка устарела — выберите бюджет ещё раз.",
                     reply_markup=kb_budget())
        return
    ok, value = validate_budget(raw)
    if not ok:
        send_message(
            chat_id,
            "Нужна сумма числом или кнопка с бюджетом ниже.",
            reply_markup=kb_budget(),
        )
        return
    info["budget"] = value
    info["state"] = STATE_CONTACT
    _ask_contact(chat_id)


def _step_contact(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    """Choose Telegram / phone / VK as the contact channel."""
    t = (text or "").strip()
    from_info = message.get("from", {})
    username = (from_info.get("username") or "").strip()

    if t in (CONTACT_TG_TEXT, CB_CONTACT_TG, "telegram", "tg", "телеграм"):
        if username:
            contact = f"Telegram @{username}"
        else:
            contact = f"Telegram (чат id {chat_id})"
        info["contact_method"] = "telegram"
        handle_completion(chat_id, contact, message)
        return

    if t in (CONTACT_PHONE_TEXT, CB_CONTACT_PHONE, "телефон", "phone"):
        info["contact_method"] = "phone"
        info["state"] = STATE_PHONE
        send_message(
            chat_id,
            "📱 Укажите номер телефона\n"
            "(кнопка ниже или введите вручную, +7…):",
            reply_markup=contact_keyboard(),
        )
        return

    if t in (CONTACT_VK_TEXT, CB_CONTACT_VK, "vk", "вк"):
        info["contact_method"] = "vk"
        info["state"] = STATE_VK
        send_message(
            chat_id,
            "💙 Напишите ссылку или ник VK\n"
            "(например: vk.com/id123 или @nickname):",
            reply_markup=kb_nav(include_back=True),
        )
        return

    # Free-text phone typed on this step — accept as phone.
    ok, phone = validate_phone(t)
    if ok and phone:
        info["contact_method"] = "phone"
        handle_completion(chat_id, phone, message)
        return

    send_message(
        chat_id,
        "Выберите способ связи кнопкой ниже — или введите номер телефона.",
        reply_markup=kb_contact_methods(),
    )


def _step_phone(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    ok, phone = validate_phone(text)
    if not ok:
        send_message(
            chat_id,
            "Похоже, номер некорректен. Формат +7XXXXXXXXXX "
            "или кнопка «📱 Отправить номер».\n"
            "Назад — чтобы выбрать другой способ связи.",
            reply_markup=contact_keyboard(),
        )
        return
    info["contact_method"] = "phone"
    handle_completion(chat_id, phone, message)


def _step_vk(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    raw = (text or "").strip()
    if not raw or len(raw) < 2:
        send_message(
            chat_id,
            "Нужна ссылка или ник VK (например vk.com/username).",
            reply_markup=kb_nav(include_back=True),
        )
        return
    # Light normalize
    if raw.startswith("@"):
        contact = f"VK {raw}"
    elif "vk.com" in raw.lower() or "vk.ru" in raw.lower():
        contact = raw if raw.lower().startswith("http") else f"https://{raw.lstrip('/')}"
        contact = f"VK {contact}"
    else:
        contact = f"VK {raw}"
    info["contact_method"] = "vk"
    handle_completion(chat_id, contact, message)


# state -> step handler
STATE_HANDLERS: Dict[str, Callable[[int, str, Dict[str, Any], Dict[str, Any]], None]] = {
    STATE_CONSENT:     _step_consent,
    STATE_DESTINATION: _step_destination,
    STATE_ORIGIN:      _step_origin,
    STATE_DATES:       _step_dates,
    STATE_PEOPLE:      _step_people,
    STATE_KIDS:        _step_kids,
    STATE_INFANTS:     _step_infants,
    STATE_BUDGET:      _step_budget,
    STATE_CONTACT:     _step_contact,
    STATE_PHONE:       _step_phone,
    STATE_VK:          _step_vk,
}

PREVIOUS_STATE: Dict[str, str] = {
    STATE_ORIGIN:      STATE_DESTINATION,
    STATE_DATES:       STATE_ORIGIN,
    STATE_PEOPLE:      STATE_DATES,
    STATE_KIDS:        STATE_PEOPLE,
    STATE_INFANTS:     STATE_KIDS,
    STATE_BUDGET:      STATE_INFANTS,
    STATE_CONTACT:     STATE_BUDGET,
    STATE_PHONE:       STATE_CONTACT,
    STATE_VK:          STATE_CONTACT,
}


def _prompt_for_state(chat_id: int, state: str) -> None:
    """Re-ask the question for the given dialog state (used by back)."""
    if state == STATE_DESTINATION:
        send_message(
            chat_id,
            "📍 <b>Куда хотите поехать?</b>\n\n"
            "Выберите направление кнопкой или напишите своё.",
            reply_markup=kb_destinations(),
            parse_mode="HTML",
        )
    elif state == STATE_ORIGIN:
        _ask_origin(chat_id)
    elif state == STATE_DATES:
        _ask_dates(chat_id)
    elif state == STATE_PEOPLE:
        _ask_people(chat_id)
    elif state == STATE_KIDS:
        _ask_kids(chat_id)
    elif state == STATE_INFANTS:
        _ask_infants(chat_id)
    elif state == STATE_BUDGET:
        _ask_budget(chat_id)
    elif state == STATE_CONTACT:
        _ask_contact(chat_id)
    elif state == STATE_PHONE:
        send_message(
            chat_id,
            "📱 Укажите номер телефона (+7… или кнопка ниже):",
            reply_markup=contact_keyboard(),
        )
    elif state == STATE_VK:
        send_message(
            chat_id,
            "💙 Ссылка или ник VK:",
            reply_markup=kb_nav(include_back=True),
        )


def _go_back(chat_id: int) -> None:
    """Move the user one step back in the dialog."""
    info = user_data.get(chat_id, {})
    state = info.get("state")
    previous = PREVIOUS_STATE.get(state)
    if previous is None:
        send_message(
            chat_id,
            "Вы на первом шаге. Можно отменить заявку кнопкой «Отменить».",
            reply_markup=kb_nav(include_back=False) if state == STATE_DESTINATION else None,
        )
        return
    # Leaving the phone step — drop the reply contact keyboard.
    if state == STATE_PHONE:
        send_message(chat_id, "◀️ Назад", reply_markup=hide_keyboard())
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
        "✅ <b>Заявка принята!</b> Менеджер «АПРЕЛЬ тур» свяжется с вами в ближайшее время.\n\n"
        f"📍 Направление: {_esc(info.get('destination', '?'))}\n"
        + (f"🛫 Откуда: {_esc(info['origin'])}\n" if info.get("origin") else "")
        + f"📅 Даты: {_esc(info.get('dates', '?'))}\n"
        f"👥 Состав: {_esc(_party_text(info))}\n"
        f"💰 Бюджет: {_esc(info.get('budget', '?'))}₽\n"
        f"📞 Связь: {_esc(phone)}\n\n"
        "Спасибо, что выбрали нас 🌺\n\n"
        "📋 ИП Замятина Мария Андреевна\n"
        "ОГРНИП 290211659807",
        reply_markup=hide_keyboard(),
        parse_mode="HTML",
    )


def _format_lead_notify_text(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
    username: str = "",
    source_label: str = "Telegram",
) -> str:
    """Build HTML text for a new-lead Telegram notification."""
    name = _esc(client_name or "без имени")
    # Deep-link to the client when Telegram allows it.
    who = f'<a href="tg://user?id={chat_id}">{name}</a>'
    if username:
        who = f"{who} (@{_esc(username)})"
    return (
        f"🔔 <b>Новая заявка</b> ({_esc(source_label)})\n"
        f"<i>Личное уведомление администратору</i>\n\n"
        f"От: {who}\n"
        f"ID: <code>{chat_id}</code>\n"
        f"📍 {_esc(info.get('destination', '?'))}\n"
        + (f"🛫 Откуда: {_esc(info['origin'])}\n" if info.get("origin") else "")
        + f"📅 {_esc(info.get('dates', '?'))}\n"
        f"👥 {_esc(_party_text(info))}\n"
        f"💰 {_esc(info.get('budget', '?'))}₽\n"
        f"📞 Связь: <code>{_esc(phone)}</code>\n\n"
        f"Нажмите «✍️ Ответить» ниже — или /send {chat_id}"
    )


def kb_admin_reply(client_chat_id: int) -> str:
    """Inline button on lead notify: one tap to arm reply mode."""
    return inline_keyboard([[
        _inline_btn("✍️ Ответить клиенту", f"{CB_ADMIN_REPLY_PREFIX}{client_chat_id}"),
    ]])


def _notify_admin(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
    username: str = "",
) -> None:
    """2. Forward the lead to the bot creator / admins in Telegram."""
    global _last_lead_client_id
    recipients = LEAD_NOTIFY_IDS
    if not recipients:
        logger.warning(
            "Lead from chat_id=%s saved but not delivered to Telegram "
            "(set ADMIN_ID or LEAD_NOTIFY_IDS)",
            chat_id,
        )
        return

    with _lock:
        _last_lead_client_id = chat_id

    text = _format_lead_notify_text(
        chat_id, info, phone, client_name, username=username, source_label="Telegram",
    )
    reply_kb = kb_admin_reply(chat_id)
    for recipient in recipients:
        resp = send_message(recipient, text, parse_mode="HTML", reply_markup=reply_kb)
        if resp is not None and getattr(resp, "status_code", 0) == 200:
            logger.info("Lead from %s delivered to Telegram chat %s", chat_id, recipient)
            continue
        # Fallback without HTML if Telegram rejected parse_mode (rare).
        if resp is not None and getattr(resp, "status_code", 0) != 200:
            plain = (
                f"🔔 Новая заявка (Telegram)!\n\n"
                f"От: {client_name or 'без имени'}"
                f"{(' @' + username) if username else ''}\n"
                f"ID: {chat_id}\n"
                f"📍 {info.get('destination', '?')}\n"
                f"📅 {info.get('dates', '?')}\n"
                f"👥 {_party_text(info)}\n"
                f"💰 {info.get('budget', '?')}₽\n"
                f"📞 {phone}\n\n"
                f"Ответить: /send {chat_id}"
            )
            resp2 = send_message(recipient, plain, reply_markup=reply_kb)
            if resp2 is not None and getattr(resp2, "status_code", 0) == 200:
                logger.info(
                    "Lead from %s delivered to %s (plain-text fallback)", chat_id, recipient,
                )
                continue
        logger.error(
            "Failed to deliver lead from %s to Telegram chat %s", chat_id, recipient,
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


def _tutu_search(info: Dict[str, Any]) -> Optional[Any]:
    """Live transport search for a completed lead. Never raises."""
    if not TUTU_ENABLED:
        return None
    try:
        return _tutu.search_offers(
            _tutu_settings(),
            telegram_session,
            destination=info.get("destination", ""),
            dates_raw=info.get("dates", ""),
            origin=info.get("origin", ""),
            people=info.get("people", 1),
            kids=info.get("kids", 0),
            infants=info.get("infants", 0),
            budget=info.get("budget"),
            log=logger,
        )
    except Exception as exc:  # defensive: a search must never break completion
        logger.error("Tutu search failed: %s", exc)
        return None


def _send_tutu_to_admin(chat_id: int, result: Any, client_name: Optional[str]) -> None:
    """Follow-up to the manager with the price anchor and checkout links.

    Deliberately a separate message: the lead notification itself is sent
    synchronously on the critical path and must not wait for a search.
    """
    block = _tutu.format_admin_block(result)
    if not block:
        return
    who = _esc(client_name or f"chat {chat_id}")
    text = f"💼 <b>По заявке от {who}</b>{block}"
    for recipient in LEAD_NOTIFY_IDS:
        send_message(recipient, text, parse_mode="HTML")


def _post_completion_side_effects(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> None:
    """MDT push + live offers + AI blurb — off the webhook critical path."""
    try:
        send_lead_to_mdt(chat_id, info, phone, client_name)

        result = _tutu_search(info)
        client_text = ""
        if result and TUTU_SHOW_CLIENT:
            client_text = _tutu.format_client_message(result)

        if client_text:
            send_typing(chat_id)
            send_message(chat_id, client_text, parse_mode="HTML")
        else:
            # No offers (or Tutu disabled/unavailable) — the client still gets
            # the usual suggestion. Degradation must be invisible to them.
            _send_ai_blurb(chat_id, info)

        if result and TUTU_SHOW_ADMIN:
            _send_tutu_to_admin(chat_id, result, client_name)
    except Exception as exc:
        logger.error("Post-completion side effects failed for %s: %s", chat_id, exc)
        _alert_admin_error("Post-completion side effects failed", exc)


def handle_completion(chat_id: int, phone: str, message: Dict[str, Any]) -> None:
    """Finalise the request: confirm, notify admin, persist lead; defer MDT/AI.

    Guarded against concurrent completion. With ``--threads 2`` two distinct
    updates for the same chat can be processed at once — a double-tapped
    "share contact" button, or a typed number racing the contact event. Both
    would otherwise pass validation and complete, producing two leads, two
    admin pings and two CRM pushes, so the sales team calls the client twice.
    Telegram's update_id dedup does not help here: the updates are genuinely
    different. Check-and-set under the same lock that guards user_data.
    """
    with _lock:
        live = user_data.get(chat_id)
        if live is None or live.get("_completing"):
            logger.info("Concurrent completion ignored for chat_id=%s", chat_id)
            return
        live["_completing"] = True
        info = dict(live)
    info.pop("_completing", None)

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

    _confirm_to_user(chat_id, info, phone)  # 1. Confirm to user
    # 2. Notify bot creator / admins in Telegram (sync — ops must see it)
    _notify_admin(chat_id, info, phone, client_name, username=username or "")
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


def _markdown_to_html(source: str) -> str:
    """Render the small Markdown subset used by the privacy policy.

    Deliberately dependency-free: pulling a Markdown library onto a 512 MB
    instance to render one static document is not a good trade.
    """
    out: List[str] = []
    in_list = False
    for raw_line in source.splitlines():
        line = raw_line.rstrip()
        stripped = line.lstrip("> ").strip() if line.startswith(">") else line.strip()
        if not stripped:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        safe = html.escape(stripped, quote=False)
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
        safe = re.sub(r"`(.+?)`", r"<code>\1</code>", safe)
        if stripped.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{safe[2:]}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        heading = len(stripped) - len(stripped.lstrip("#"))
        if heading:
            level = min(heading + 1, 6)
            out.append(f"<h{level}>{safe.lstrip('# ')}</h{level}>")
        elif line.startswith(">"):
            out.append(f'<blockquote>{safe}</blockquote>')
        else:
            out.append(f"<p>{safe}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


@app.route("/privacy")
def privacy_page() -> Any:
    """Serve the privacy policy the bot links to in its consent text.

    Operators of RF personal data must publish this document. Hosting it from
    the bot itself means the link can never be dead just because nobody set up
    separate hosting for a single static page.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "docs", "privacy_policy.md")
    try:
        with open(path, encoding="utf-8") as fh:
            body = _markdown_to_html(fh.read())
    except OSError as exc:
        logger.error("Privacy policy is not readable at %s: %s", path, exc)
        return "Политика обработки персональных данных временно недоступна.", 503

    banner = (
        '<div class="demo">Инстанс работает в демонстрационном режиме: '
        'заявки не передаются в турагентство, телефон не сохраняется.</div>'
        if DEMO_MODE else ""
    )
    page = (
        '<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Политика обработки персональных данных — TurBot</title><style>"
        "body{max-width:760px;margin:0 auto;padding:40px 6%;"
        "font:16px/1.65 -apple-system,Segoe UI,Roboto,sans-serif;color:#15171c}"
        "h1{font-size:1.8rem;letter-spacing:-.02em;line-height:1.2}"
        "h2{font-size:1.2rem;margin-top:2em;letter-spacing:-.01em}"
        "h3{font-size:1.03rem;margin-top:1.5em}"
        "p,li{color:#39414c}ul{padding-left:1.2em}li{margin:.3em 0}"
        "code{background:#eef1f4;padding:1px 5px;border-radius:3px;font-size:.9em}"
        "blockquote{border-left:3px solid #d99a2b;background:#fdf7ec;margin:1.4em 0;"
        "padding:12px 18px;color:#4a4235}"
        ".demo{border-left:3px solid #2b7fd4;background:#eef4fb;padding:12px 18px;"
        "margin-bottom:26px;color:#26445f}"
        "</style></head><body>" + banner + body + "</body></html>"
    )
    return Response(page, mimetype="text/html; charset=utf-8")


@app.route("/health")
def health() -> Any:
    """Health endpoint that can actually fail.

    An endpoint that always returns ok is decoration. Twice this bot went deaf
    while every indicator stayed green, so in polling mode this reports the age
    of the last completed getUpdates and answers 503 once that goes stale. The
    watchdog in deploy/ keys off the status code.
    """
    try:
        leads_total = count_leads()
    except Exception:
        leads_total = -1

    now = time.time()
    # Only meaningful while polling. Under a webhook nothing is expected to
    # phone Telegram on a schedule, so silence proves nothing.
    poll_age = round(now - _last_poll_ok, 1) if BOT_MODE == "polling" else None
    stale = poll_age is not None and poll_age > POLL_STALE_AFTER
    if stale:
        logger.error(
            "Health: no successful getUpdates for %.0fs (limit %ss) — reporting degraded",
            poll_age, POLL_STALE_AFTER,
        )

    payload = jsonify({
        "status": "degraded" if stale else "ok",
        "seconds_since_poll_ok": poll_age,
        "poll_stale_after": POLL_STALE_AFTER if BOT_MODE == "polling" else None,
        "seconds_since_update": (
            round(now - _last_update_at, 1) if _last_update_at else None
        ),
        "bot_token_configured": bool(BOT_TOKEN),
        "admin_id_configured": bool(ADMIN_ID),
        "lead_notify_configured": bool(LEAD_NOTIFY_IDS),
        "lead_notify_count": len(LEAD_NOTIFY_IDS),
        "groq_configured": bool(GROQ_API_KEY),
        "ai_mode": AI_MODE,
        "mdt_enabled": MDT_ENABLED,
        "mdt_mode": MDT_MODE,
        "total_users": len(all_users),
        "active_sessions": len(user_data),
        "total_leads": leads_total,
        "privacy_policy_configured": bool(PRIVACY_POLICY_URL),
        "data_retention_days": DATA_RETENTION_DAYS,
        "consent_mode": CONSENT_MODE,
        "demo_mode": DEMO_MODE,
        "tutu_enabled": TUTU_ENABLED,
        "bot_mode": BOT_MODE,
    })
    # 503 rather than 200-with-a-sad-field: monitoring reads status codes, and
    # a body nobody parses is how the last two outages stayed invisible.
    return (payload, 503) if stale else payload


def _remember_update_id(data: Dict[str, Any]) -> bool:
    """Return True if this update_id is new; False if it is a Telegram retry."""
    update_id = data.get("update_id")
    if update_id is None:
        return True
    with _lock:
        if update_id in _seen_update_ids:
            logger.debug("Skipping duplicate update_id=%s", update_id)
            return False
        _seen_update_ids[update_id] = None
        while len(_seen_update_ids) > _SEEN_UPDATE_MAX:
            _seen_update_ids.popitem(last=False)
    return True


def _touch_user(chat_id: int, first_name: str = "", username: str = "") -> None:
    """Upsert user meta and bump open-session activity."""
    with _lock:
        meta = all_users.setdefault(chat_id, {})
        if first_name:
            meta["first_name"] = first_name
        if username:
            meta["username"] = username
        meta["last_seen"] = int(time.time())
        if chat_id in user_data:
            user_data[chat_id]["updated_at"] = int(time.time())
            session_open = True
        else:
            session_open = False
    _mark_dirty(chat_id, session=session_open)


def _process_callback(data: Dict[str, Any]) -> None:
    """Handle inline button presses (callback_query)."""
    if not _remember_update_id(data):
        return

    cq = data.get("callback_query") or {}
    cq_id = cq.get("id", "")
    cb_data = (cq.get("data") or "").strip()
    from_info = cq.get("from") or {}
    message = cq.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")

    answer_callback(cq_id)
    if chat_id is None or not cb_data:
        return

    first_name = from_info.get("first_name", "")
    username = from_info.get("username", "")
    _touch_user(chat_id, first_name, username)

    if message_id is not None:
        clear_inline_keyboard(chat_id, message_id)

    # Admin: one-tap reply from lead notification.
    if cb_data.startswith(CB_ADMIN_REPLY_PREFIX):
        if chat_id != ADMIN_ID and chat_id not in LEAD_NOTIFY_IDS:
            send_message(chat_id, "Только для администратора.")
            return
        try:
            client_id = int(cb_data[len(CB_ADMIN_REPLY_PREFIX):])
        except ValueError:
            send_message(chat_id, "Некорректная кнопка ответа.")
            return
        _admin_start_reply(chat_id, client_id)
        return

    # Navigation callbacks work from any dialog state.
    if cb_data == CB_CANCEL:
        handle_cancel(chat_id)
        return
    if cb_data == CB_BACK:
        if chat_id in user_data:
            _go_back(chat_id)
        else:
            send_message(chat_id, "Для начала работы отправьте /start")
        return

    info = user_data.get(chat_id)
    if info is None:
        send_message(chat_id, "Для начала работы отправьте /start")
        return

    # Synthetic message so step handlers can read from/user fields.
    synthetic = {"from": from_info, "chat": chat}

    if cb_data in (CB_CONSENT_YES, CB_CONSENT_NO, CB_START):
        _step_consent(chat_id, cb_data, synthetic, info)
        _mark_dirty(chat_id, user=False)
        return

    if cb_data in (CB_CONTACT_TG, CB_CONTACT_PHONE, CB_CONTACT_VK):
        if info.get("state") != STATE_CONTACT:
            send_message(chat_id, "Сейчас это действие недоступно. Продолжите текущий шаг.")
            return
        _step_contact(chat_id, cb_data, synthetic, info)
        _mark_dirty(chat_id, user=False)
        return

    if cb_data.startswith(CB_DEST_PREFIX):
        try:
            idx = int(cb_data[len(CB_DEST_PREFIX):])
            label = POPULAR_DESTINATIONS[idx]
        except (ValueError, IndexError):
            send_message(chat_id, "Кнопка устарела. Выберите направление ещё раз.",
                         reply_markup=kb_destinations())
            return
        if info.get("state") != STATE_DESTINATION:
            send_message(chat_id, "Сейчас это действие недоступно. Продолжите текущий шаг.")
            return
        _step_destination(chat_id, label, synthetic, info)
        _mark_dirty(chat_id, user=False)
        return

    if cb_data.startswith(CB_ORIGIN_PREFIX):
        if info.get("state") != STATE_ORIGIN:
            send_message(chat_id, "Сейчас это действие недоступно. Продолжите текущий шаг.")
            return
        _step_origin(chat_id, cb_data, synthetic, info)
        _mark_dirty(chat_id, user=False)
        return

    if cb_data.startswith(CB_DATE_PREFIX):
        if info.get("state") != STATE_DATES:
            send_message(chat_id, "Сейчас это действие недоступно. Продолжите текущий шаг.")
            return
        _step_dates(chat_id, cb_data, synthetic, info)
        _mark_dirty(chat_id, user=False)
        return

    if cb_data.startswith(CB_PEOPLE_PREFIX):
        people = cb_data[len(CB_PEOPLE_PREFIX):]
        if info.get("state") != STATE_PEOPLE:
            send_message(chat_id, "Сейчас это действие недоступно. Продолжите текущий шаг.")
            return
        _step_people(chat_id, people, synthetic, info)
        _mark_dirty(chat_id, user=False)
        return

    if cb_data.startswith(CB_KIDS_PREFIX):
        if info.get("state") != STATE_KIDS:
            send_message(chat_id, "Сейчас это действие недоступно. Продолжите текущий шаг.")
            return
        _step_kids(chat_id, cb_data, synthetic, info)
        _mark_dirty(chat_id, user=False)
        return

    if cb_data.startswith(CB_INFANTS_PREFIX):
        if info.get("state") != STATE_INFANTS:
            send_message(chat_id, "Сейчас это действие недоступно. Продолжите текущий шаг.")
            return
        _step_infants(chat_id, cb_data, synthetic, info)
        _mark_dirty(chat_id, user=False)
        return

    if cb_data.startswith(CB_BUDGET_PREFIX):
        if info.get("state") != STATE_BUDGET:
            send_message(chat_id, "Сейчас это действие недоступно. Продолжите текущий шаг.")
            return
        _step_budget(chat_id, cb_data, synthetic, info)
        _mark_dirty(chat_id, user=False)
        return

    send_message(chat_id, "Неизвестная кнопка. /start — начать заново.")


def _process_update(data: Dict[str, Any]) -> None:
    """Parse one Telegram message update and route it to the right handler."""
    if not _remember_update_id(data):
        return
    message = data["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    from_info = message.get("from", {})
    first_name = from_info.get("first_name", "")
    username = from_info.get("username", "")

    _touch_user(chat_id, first_name, username)

    # Shared contact (e.g. phone button)
    contact = message.get("contact")
    if contact and contact.get("phone_number"):
        phone_number = contact["phone_number"]
        info = user_data.get(chat_id, {})
        state = info.get("state")
        if state in (STATE_PHONE, STATE_CONTACT):
            info["contact_method"] = "phone"
            if state == STATE_CONTACT:
                info["state"] = STATE_PHONE
            _step_phone(chat_id, phone_number, message, info)
        else:
            send_message(chat_id, "Спасибо, но сейчас номер телефона не требуется. 📝")
        return

    # Non-text messages (photos, stickers, etc.) — contact handled above.
    if not text:
        send_message(
            chat_id,
            "Сейчас нужен текст или кнопки под сообщением 📝\n"
            f"{HINT_START if chat_id not in user_data else 'Или продолжите шаг заявки.'}",
        )
        return

    # Normalise /cmd@botname → /cmd
    if text.startswith("/"):
        text = text.split("@", 1)[0]

    # --- Admin: pending reply to client, then admin commands ---
    if chat_id == ADMIN_ID or chat_id in LEAD_NOTIFY_IDS:
        if text in ("/cancel_reply", "/cancel_send"):
            _admin_cancel_reply(chat_id)
            return
        # Next plain message after «Ответить» / `/send {id}` goes to the client.
        if not text.startswith("/") and _admin_deliver_pending(chat_id, text):
            return
        if chat_id == ADMIN_ID and handle_admin(chat_id, text):
            return

    # --- User commands ---
    if text == "/start":
        handle_start(chat_id, first_name)
        return

    if text == "/help":
        send_message(chat_id, USER_HELP, parse_mode="HTML")
        return

    if text == "/privacy":
        send_message(chat_id, _privacy_text())
        return

    if text == "/delete":
        delete_user_data(chat_id)
        send_message(
            chat_id,
            "🗑 Готово: персональные данные удалены, согласие отозвано.\n\n"
            "Снова подобрать тур — /start.",
            reply_markup=hide_keyboard(),
        )
        return

    if text == "/cancel" or text == CANCEL_BUTTON_TEXT:
        handle_cancel(chat_id)
        return

    # --- Navigation inside the dialog (reply-keyboard fallback on phone step) ---
    if text == BACK_BUTTON_TEXT:
        if chat_id in user_data:
            _go_back(chat_id)
        else:
            send_message(chat_id, HINT_START)
        return

    # --- Unknown slash-command ---
    if text.startswith("/"):
        if chat_id in user_data:
            send_message(
                chat_id,
                "Такой команды нет. /cancel — отменить заявку, /help — справка.",
            )
        else:
            send_message(chat_id, f"Такой команды нет.\n\n{HINT_START}")
        return

    # --- Dialog flow ---
    if chat_id in user_data:
        handle_dialog(chat_id, text, message)
    else:
        send_message(chat_id, HINT_START)


def _check_webhook_secret() -> bool:
    """Verify Telegram secret token if one is configured."""
    if not TELEGRAM_SECRET_TOKEN:
        return True
    header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return hmac.compare_digest(header, TELEGRAM_SECRET_TOKEN)


def dispatch_update(data: Optional[Dict[str, Any]]) -> None:
    """Route one Telegram update. Shared by the webhook and the poller.

    Both transports must behave identically — including the state flush in
    `finally`, which is what makes an in-progress dialog survive a restart.
    """
    global _last_update_at
    _last_update_at = time.time()
    try:
        if data and "callback_query" in data:
            _process_callback(data)
        elif data and "message" in data:
            _process_update(data)
    except Exception as exc:
        logger.error("Error processing update: %s", exc, exc_info=True)
        _alert_admin_error("Update processing error", exc)
    finally:
        save_state()


@app.route("/webhook", methods=["POST"])
def webhook() -> Tuple[str, int]:
    if not _check_webhook_secret():
        logger.warning("Webhook called with missing/invalid secret token")
        return "Forbidden", 403
    dispatch_update(request.get_json(silent=True))
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
# Keep import-time work light: gunicorn must bind 0.0.0.0:$PORT quickly.
# Render scans for an open port during deploy; blocking Telegram/MDT HTTP
# here caused "No open ports detected, continuing to scan...".

load_state()
_start_timeout_worker()
_start_followup_worker()
_start_retention_worker()

logger.info(
    "TurBot loaded (port=%s, admin_set=%s, groq_set=%s, webhook_secret_set=%s)",
    PORT,
    bool(ADMIN_ID),
    bool(GROQ_API_KEY),
    bool(TELEGRAM_SECRET_TOKEN),
)


_shutdown_event = threading.Event()


def _polling_worker() -> None:
    """Pull updates with getUpdates instead of waiting to be called.

    Telegram refuses getUpdates while a webhook is registered, so the webhook
    is removed first — without dropping pending updates, which would throw
    away the requests that piled up while delivery was failing.
    """
    global _last_poll_ok

    if not BOT_TOKEN:
        logger.error("Polling mode requested but BOT_TOKEN is empty — not starting")
        return

    base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    try:
        resp = telegram_session.post(
            f"{base}/deleteWebhook",
            data={"drop_pending_updates": "false"},
            timeout=HTTP_TIMEOUT,
        )
        logger.info("Polling: deleteWebhook returned HTTP %s", resp.status_code)
    except Exception as exc:
        logger.warning("Polling: deleteWebhook failed (%s) — continuing anyway", exc)

    offset = 0
    backoff = 1.0
    # Count the start as a heartbeat, or /health reports the bot stale during
    # the very first long poll.
    _last_poll_ok = time.time()
    logger.info("Polling started (long-poll timeout %ss)", POLL_TIMEOUT)

    while not _shutdown_event.is_set():
        try:
            resp = telegram_session.get(
                f"{base}/getUpdates",
                params={
                    "offset": offset,
                    "timeout": POLL_TIMEOUT,
                    # Ask only for what the bot handles; anything else would
                    # still advance the offset and waste a round trip.
                    "allowed_updates": json.dumps(["message", "callback_query"]),
                },
                # Must exceed the long-poll window, or every idle poll "fails".
                timeout=POLL_TIMEOUT + 15,
            )
            if resp.status_code == 409:
                # A webhook got set again, or a second poller is running.
                logger.warning("Polling: 409 Conflict — removing webhook and retrying")
                telegram_session.post(f"{base}/deleteWebhook", timeout=HTTP_TIMEOUT)
                _shutdown_event.wait(5)
                continue
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            # Network flakiness is expected on a filtered network; keep going
            # with a bounded backoff rather than dying and needing a restart.
            logger.warning("Polling: getUpdates failed (%s) — retry in %.0fs", exc, backoff)
            _shutdown_event.wait(backoff)
            backoff = min(backoff * 2, 60.0)
            continue

        backoff = 1.0
        if not payload.get("ok"):
            logger.error("Polling: API returned not-ok: %s", str(payload)[:200])
            _shutdown_event.wait(5)
            continue

        # A completed round trip, empty result included. This is the heartbeat:
        # it proves the link to Telegram is alive without needing a client to
        # write in.
        _last_poll_ok = time.time()

        for update in payload.get("result", []):
            # Advance the offset first, so a poison update is never re-fetched
            # forever. And catch here rather than trusting the callee: if
            # dispatch_update ever stops swallowing, this thread would die
            # silently and the bot would go deaf while the service stayed green.
            offset = max(offset, update.get("update_id", 0) + 1)
            try:
                dispatch_update(update)
            except Exception as exc:
                logger.error("Polling: update %s failed: %s",
                             update.get("update_id"), exc, exc_info=True)

    logger.info("Polling stopped")


def _deferred_network_startup() -> None:
    """Start receiving first; cosmetics and CRM warm-up come after.

    Order matters more than it looks. Profile setup calls setMyName, which
    Telegram rate-limits hard — it answers 429, the session retries it three
    times with backoff, and five such calls can grind for minutes. While that
    ran ahead of the poller, the bot accepted nothing: systemd showed
    active (running), /health returned 200, and every message went unanswered.
    Nothing about setting a description should gate reading messages.
    """
    if BOT_MODE == "polling":
        threading.Thread(target=_polling_worker, name="polling", daemon=True).start()
    try:
        ensure_bot_profile()
    except Exception as exc:
        logger.warning("ensure_bot_profile failed: %s", exc)
    if MDT_ENABLED:
        try:
            _mdt_load_countries()
        except Exception as exc:
            logger.warning("MDT country load failed: %s", exc)
    logger.info("Deferred network startup finished (mode: %s)", BOT_MODE)


threading.Thread(
    target=_deferred_network_startup,
    name="startup-network",
    daemon=True,
).start()

import signal as _signal_module

def _graceful_shutdown(signum: int, frame: Any) -> None:
    """Save state on SIGTERM/SIGINT so no data is lost during deploy."""
    logger.info("Received signal %s — saving state and exiting", signum)
    _shutdown_event.set()   # let the poller finish its current long poll
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
