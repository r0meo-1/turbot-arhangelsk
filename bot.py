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
import secrets
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
    STATE_REVIEW,
    STATE_DATES,
    STATE_DESTINATION,
    STATE_ORIGIN,
    STATE_PEOPLE,
    STATE_KIDS,
    STATE_KIDS_AGES,
    STATE_INFANTS,
    STATE_PHONE,
    STATE_VK,
    PEOPLE_OPTIONS,
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
from shared.validation import (
    validate_phone, validate_people, validate_budget,
    parse_kids_ages, party_bands, party_text as _party_text,
    ages_to_db as _ages_to_db, ages_from_db as _ages_from_db,
)
from shared.templates import template_selection as _template_selection
from shared.privacy import consent_text as _shared_consent_text, privacy_text as _shared_privacy_text
from shared.log_privacy import correlation_id as _log_correlation
from shared import tutu as _tutu
from shared import version as _version
from shared.ai import generate_ai_selection as _shared_generate_ai
from shared.ai_provider import build_selection_provider
from shared.ai_chat import generate_ai_chat_reply as _generate_ai_chat_reply
from shared.ai_guardrails import redact_external_ai_text
from shared import mdt as mdt_shared
from shared import travelpayouts_links as _travelpayouts_links
from shared import travelpayouts_stats as _travelpayouts_stats
from shared import travelpayouts_transfer as _travelpayouts_transfer
from shared.runtime_metrics import event_counter_snapshot, lead_delivery_snapshot
from shared import funnel_metrics as _funnel_metrics
from shared import travel_crm_store as _travel_crm_store
from shared import travel_crm_adapter as _travel_crm_adapter
from shared import provider_status as _provider_status
from shared.telegram_webapp import (
    MiniAppValidationError, normalise_source_tag, validate_init_data, validate_trip_request,
)

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
GROQ_MODEL        = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
REGCLOUD_API_KEY  = os.getenv("REGCLOUD_API_KEY", "")
REGCLOUD_BASE_URL = os.getenv("REGCLOUD_BASE_URL", "")
REGCLOUD_MODEL    = os.getenv("REGCLOUD_MODEL", "")
# External AI is opt-in. If AI_MODE is absent, deterministic templates win.
AI_MODE           = os.getenv("AI_MODE", "template").lower().strip()
AI_CHAT_ENABLED   = os.getenv("AI_CHAT_ENABLED", "false").lower().strip() in ("1", "true", "yes")
# A second gate prevents an allowlisted beta from transmitting arbitrary text
# merely because a Groq key happens to exist on the host.
AI_CHAT_EXTERNAL_PROVIDER_ENABLED = os.getenv(
    "AI_CHAT_EXTERNAL_PROVIDER_ENABLED", "false"
).lower().strip() in ("1", "true", "yes")
# Groq documents that inference content may otherwise be retained for reliability/
# abuse monitoring. This is a manual assertion about the actual Groq Console org;
# the application cannot verify the account setting through the chat API.
GROQ_ZDR_CONFIRMED = os.getenv("GROQ_ZDR_CONFIRMED", "false").lower().strip() in (
    "1", "true", "yes"
)
AI_CHAT_MAX_CHARS = max(100, min(8000, _env_int("AI_CHAT_MAX_CHARS", 2000)))
AI_CHAT_TIMEOUT_SECONDS = max(3, min(30, _env_int("AI_CHAT_TIMEOUT_SECONDS", 15)))
# Customer-facing lead assistant is deliberately narrower than the closed /ai
# beta: it is invoked explicitly with /ask or "ИИ:", uses only non-PII trip
# fields as context, and inherits the same deterministic safety guardrails.
AI_LEAD_ASSIST_ENABLED = os.getenv(
    "AI_LEAD_ASSIST_ENABLED",
    "false",
).lower().strip() in ("1", "true", "yes")
AI_LEAD_ASSIST_MAX_CHARS = max(
    100, min(2000, _env_int("AI_LEAD_ASSIST_MAX_CHARS", 800))
)
AI_LEAD_ASSIST_WINDOW_HOURS = max(
    1, min(24 * 365, _env_int("AI_LEAD_ASSIST_WINDOW_HOURS", 24 * 30))
)
PORT                 = _env_int("PORT", 5000)
STATE_FILE           = os.getenv("STATE_FILE", "bot_state.json")
DATABASE_PATH        = os.getenv("DATABASE_PATH", "bot_state.sqlite")
TELEGRAM_SECRET_TOKEN = os.getenv("TELEGRAM_SECRET_TOKEN", "")
# HTTPS URL opened by Telegram as the bot Web App menu button.
MINI_APP_URL = os.getenv(
    "MINI_APP_URL",
    "https://r0meo-1.github.io/turbot-arhangelsk/miniapp/",
).strip()
MINI_APP_ORIGIN = os.getenv(
    "MINI_APP_ORIGIN", "https://r0meo-1.github.io"
).strip().rstrip("/")
DIALOG_TIMEOUT_HOURS = _env_int("DIALOG_TIMEOUT_HOURS", 6)
HTTP_TIMEOUT         = 15    # seconds for outbound HTTP calls


def agent_extension_token() -> str:
    """Return the Agent Desk pairing token without storing a new server secret.

    An explicit AGENT_EXTENSION_TOKEN may override it. Otherwise derive a
    dedicated token from BOT_TOKEN with HMAC so the browser credential cannot
    be reversed into the Telegram bot token.
    """
    explicit = os.getenv("AGENT_EXTENSION_TOKEN", "").strip()
    if explicit:
        return explicit
    if not BOT_TOKEN:
        return ""
    return hmac.new(
        BOT_TOKEN.encode("utf-8"),
        b"turbot-agent-desk-v1",
        "sha256",
    ).hexdigest()


def _parse_chat_ids(raw: str, *, env_name: str = "LEAD_NOTIFY_IDS") -> List[int]:
    """Parse comma-separated Telegram chat IDs; skip empty/invalid parts."""
    ids: List[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            logger.warning("Invalid chat id in %s: %r", env_name, part)
    return ids


# Closed AI beta: the master flag is OFF by default and only allowlisted chats
# can use /ai. ADMIN_ID is treated as an internal tester when the flag is on.
AI_CHAT_BETA_IDS = set(
    _parse_chat_ids(os.getenv("AI_CHAT_BETA_IDS", ""), env_name="AI_CHAT_BETA_IDS")
)
if ADMIN_ID:
    AI_CHAT_BETA_IDS.add(ADMIN_ID)


# Who receives new leads in Telegram. LEAD_NOTIFY_IDS wins if set; otherwise ADMIN_ID.
# Comma-separated chat IDs, e.g. "123456789,987654321".
_lead_notify_raw = os.getenv("LEAD_NOTIFY_IDS", "").strip()
if _lead_notify_raw:
    LEAD_NOTIFY_IDS: List[int] = list(dict.fromkeys(_parse_chat_ids(_lead_notify_raw)))
elif ADMIN_ID:
    LEAD_NOTIFY_IDS = [ADMIN_ID]
else:
    LEAD_NOTIFY_IDS = []

# Canonical lead owner. Every accepted lead is additionally delivered to this
# VK private dialog; Telegram admin notifications remain an operational copy.
LEAD_OWNER_NAME = os.getenv("LEAD_OWNER_NAME", "Наталья Ильина").strip() or "Наталья Ильина"
LEAD_OWNER_PHONE = os.getenv("LEAD_OWNER_PHONE", "+79021932923").strip() or "+79021932923"
LEAD_OWNER_VK_ID = _env_int("LEAD_OWNER_VK_ID", 112655584)
MANAGER_TOURVISOR_URL = os.getenv("MANAGER_TOURVISOR_URL", "https://pro.tourvisor.ru/").strip()
MANAGER_SLETAT_URL = os.getenv("MANAGER_SLETAT_URL", "https://sletat.ru/pro").strip()
MANAGER_QUIQUO_URL = os.getenv("MANAGER_QUIQUO_URL", "https://qui-quo.ru/").strip()
MANAGER_SLA_HINT = os.getenv(
    "MANAGER_SLA_HINT",
    "горячий лид ≤5 мин; обычный ≤15 мин; не повторять вопросы TurBot",
).strip() or "горячий лид ≤5 мин; обычный ≤15 мин; не повторять вопросы TurBot"


def _manager_quick_reply_text() -> str:
    """Canonical first reply shown inside manager lead cards."""
    return (
        f"Здравствуйте! Я {LEAD_OWNER_NAME}, «Апрель Тур». "
        "Вижу вашу заявку из TurBot, основные параметры уже сохранены. "
        "Уже смотрю варианты. Уточню только то, чего в заявке нет."
    )
VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN", "").strip()
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199").strip() or "5.199"

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
# Ставится через .env на боевом сервере: реквизиты конкретного ИП — чужие
# персональные данные, и публичному репозиторию они не принадлежат.
DATA_OPERATOR_NAME = os.getenv("DATA_OPERATOR_NAME", "ТА «АПРЕЛЬ тур»")
# Days after which a client's personal data is auto-deleted (data minimisation,
# 152-ФЗ ст. 5). Set to 0 to disable automatic retention cleanup.
DATA_RETENTION_DAYS = _env_int("DATA_RETENTION_DAYS", 180)
# Anonymous partner-click events contain no Telegram identity or contact data,
# but they still need a bounded lifetime so the SQLite file cannot grow forever.
# 0 disables event cleanup.
PARTNER_ANALYTICS_RETENTION_DAYS = _env_int("PARTNER_ANALYTICS_RETENTION_DAYS", 365)
FUNNEL_ANALYTICS_RETENTION_DAYS = _env_int("FUNNEL_ANALYTICS_RETENTION_DAYS", 365)
# Operational counters never contain customer text/IDs, but they are event rows
# and still need bounded storage. Public health uses a 30-day window.
OPS_METRICS_RETENTION_DAYS = _env_int("OPS_METRICS_RETENTION_DAYS", 90)
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
MDT_RETRY_ENABLED = os.getenv("MDT_RETRY_ENABLED", "true").lower().strip() in ("1", "true", "yes")
MDT_RETRY_POLL_SECONDS = max(5, _env_int("MDT_RETRY_POLL_SECONDS", 60))
MDT_RETRY_BASE_SECONDS = max(5, _env_int("MDT_RETRY_BASE_SECONDS", 60))
MDT_RETRY_MAX_SECONDS = max(MDT_RETRY_BASE_SECONDS, _env_int("MDT_RETRY_MAX_SECONDS", 3600))
MDT_RETRY_BATCH_SIZE = max(1, _env_int("MDT_RETRY_BATCH_SIZE", 10))
MDT_RETRY_ALERT_AFTER_SECONDS = max(0, _env_int("MDT_RETRY_ALERT_AFTER_SECONDS", 7200))

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
    "Подбор туров из Архангельска и других городов. Оставьте пожелания — менеджер поможет с выбором.",
).strip()[:120]
BOT_DESCRIPTION = os.getenv(
    "BOT_DESCRIPTION",
    "🌴 Подберём ваш следующий отдых!\n\n"
    "«АПРЕЛЬ тур», Архангельск. Укажите направление, город вылета, даты и состав туристов. "
    "Бюджет — одной суммой на человека, например 100000 ₽ или 100 тыс.\n\n"
    "Выберите связь: Telegram, телефон или VK. Проверьте заявку и нажмите «Отправить менеджеру».\n\n"
    "/start — подбор тура\n/cancel — отмена заполнения\n/help — помощь\n"
    "/privacy — персональные данные\n/delete — удалить мои данные",
).strip()[:512]

USER_HELP = (
    "🌴 <b>«АПРЕЛЬ тур»</b> — подбор отдыха без лишней суеты\n\n"
    "Я соберу короткую заявку и передам менеджеру. Обычно это 1–2 минуты.\n\n"
    "<b>Команды</b>\n"
    "/start — начать подбор\n"
    "/cancel — отменить заявку\n"
    "/ask вопрос — спросить ИИ-помощника по текущей/последней заявке\n"
    "/privacy — обработка персональных данных\n"
    "/delete — удалить мои данные\n"
    "/help — эта справка\n\n"
    "<b>В диалоге</b> — кнопки: направления, гости, способ связи "
    "(Telegram / телефон / VK), назад и отмена."
)

WELCOME_BODY = (
    "Подберём тур под ваши даты и бюджет — заявка уйдёт менеджеру.\n\n"
    "<b>Как это работает</b>\n"
    "1) несколько вопросов (куда, когда, кто, бюджет)\n"
    "2) удобный способ связи: Telegram, телефон или VK\n"
    "3) проверка заявки и отправка менеджеру\n"
    "4) менеджер напишет или позвонит\n\n"
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
CB_KIDS_PREFIX = "ka:"
CB_BUDGET_PREFIX = "bd:"
CB_CONTACT_TG = "ct:tg"
CB_CONTACT_PHONE = "ct:phone"
CB_CONTACT_VK = "ct:vk"
CB_BACK = "nav:back"
CB_CANCEL = "nav:cancel"
CB_REVIEW_PREFIX = "rv:"

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
    {"command": "ask", "description": "🤖 Вопрос ИИ по поездке"},
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
    "/analytics — общая аналитика (заявки, направления, партнёры)\n"
    "/funnel — источники → лиды → доставка менеджеру за 30 дней\n"
    "/providers — статус Sletat / Travelata / Tourvisor\n"
    "/partners [дни] [reload] — партнёрские переходы, брони и доход\n"
    "/export — экспорт завершённых заявок\n"
    "/followup — напоминания незавершившим\n"
    "/mdt [test|reload] — статус MDT CRM\n"
    "/agentdesk — токен подключения TurBot Agent Desk\n"
    "/ai <вопрос> — закрытая AI beta (если включена)\n"
    "/ai_stats — агрегированная статистика AI beta\n"
    "/ai_status — безопасный статус provider gates\n"
    "/help — эта справка\n\n"
    "В уведомлении о заявке есть кнопка «✍️ Ответить клиенту».\n"
    "HTML: <b>жирный</b>, <i>курсив</i>"
)

# Admin → client reply flow (pending text after button /send).
_admin_reply_to: Dict[int, int] = {}  # admin_chat_id → client_chat_id
_last_lead_client_id: Optional[int] = None
CB_ADMIN_REPLY_PREFIX = "ar:"
CB_AI_LEAD_PREFIX = "aiq:"
AI_LEAD_QUICK_QUESTIONS = {
    "packing": "Что взять с собой в эту поездку?",
    "hotel": "На что обратить внимание при выборе отеля для этой поездки?",
    "prep": "Что важно учесть при подготовке к этой поездке?",
}

# ---------------------------------------------------------------------------
# Groq client (created once at startup)
# ---------------------------------------------------------------------------

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
selection_ai_provider = build_selection_provider(
    AI_MODE,
    groq_api_key=GROQ_API_KEY,
    groq_model=GROQ_MODEL,
    regcloud_api_key=REGCLOUD_API_KEY,
    regcloud_base_url=REGCLOUD_BASE_URL,
    regcloud_model=REGCLOUD_MODEL,
)

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
# Seeded at import rather than left at zero: a process that has only just
# booted has genuinely not missed anything yet, and a zero would make /health
# call the bot dead for the first instants of its life — long enough for the
# watchdog to restart it into a loop. If the poller never starts at all, this
# ages out on its own and the check fires for the right reason.
_last_poll_ok: float = time.time()
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
    with _db_cursor() as cur:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.fetchone()
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
                nights INTEGER,
                people TEXT,
                kids INTEGER,
                infants INTEGER,
                budget INTEGER,
                budget_scope TEXT,
                direct_only INTEGER,
                phone TEXT,
                source_tag TEXT,
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
                nights INTEGER,
                people TEXT,
                kids INTEGER,
                infants INTEGER,
                budget INTEGER,
                budget_scope TEXT,
                direct_only INTEGER,
                phone TEXT NOT NULL,
                source_tag TEXT,
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
            # Exact ages, stored as "5,9". A JSON column would be tidier in
            # theory and unreadable in the sqlite3 shell the manager's problems
            # actually get debugged from.
            if "kids_ages" not in _cols:
                cur.execute(f"ALTER TABLE {_table} ADD COLUMN kids_ages TEXT")
            if "nights" not in _cols:
                cur.execute(f"ALTER TABLE {_table} ADD COLUMN nights INTEGER")
            if "budget_scope" not in _cols:
                cur.execute(f"ALTER TABLE {_table} ADD COLUMN budget_scope TEXT")
            if "direct_only" not in _cols:
                cur.execute(f"ALTER TABLE {_table} ADD COLUMN direct_only INTEGER")
            if "source_tag" not in _cols:
                cur.execute(f"ALTER TABLE {_table} ADD COLUMN source_tag TEXT")
        cur.execute("PRAGMA table_info(sessions)")
        if "review_token" not in {row[1] for row in cur.fetchall()}:
            cur.execute("ALTER TABLE sessions ADD COLUMN review_token TEXT")
        cur.execute("PRAGMA table_info(leads)")
        _lead_cols = {row[1] for row in cur.fetchall()}
        if "mdt_status" not in _lead_cols:
            cur.execute("ALTER TABLE leads ADD COLUMN mdt_status TEXT")
        if "mdt_attempts" not in _lead_cols:
            cur.execute("ALTER TABLE leads ADD COLUMN mdt_attempts INTEGER NOT NULL DEFAULT 0")
        if "mdt_next_retry_at" not in _lead_cols:
            cur.execute("ALTER TABLE leads ADD COLUMN mdt_next_retry_at INTEGER")
        if "mdt_synced_at" not in _lead_cols:
            cur.execute("ALTER TABLE leads ADD COLUMN mdt_synced_at INTEGER")
        if "mdt_payload" not in _lead_cols:
            cur.execute("ALTER TABLE leads ADD COLUMN mdt_payload TEXT")
        if "manager_notified_at" not in _lead_cols:
            cur.execute("ALTER TABLE leads ADD COLUMN manager_notified_at INTEGER")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_mdt_retry ON leads(mdt_status, mdt_next_retry_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_chat_id ON leads(chat_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at)"
        )
        _travel_crm_store.init_schema(cur)
        _funnel_metrics.init_schema(cur)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ops_metric_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                subject TEXT NOT NULL,
                outcome TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_ops_metric_events_created_at "
            "ON ops_metric_events(created_at)"
        )
        # Anonymous partner-click analytics. Deliberately no chat_id, username,
        # phone, Telegram payload, or affiliate URL is stored here.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS partner_clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                destination TEXT NOT NULL,
                mode TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_partner_clicks_created_at "
            "ON partner_clicks(created_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_partner_clicks_service_created "
            "ON partner_clicks(service, created_at)"
        )
        # Aggregate-only AI beta telemetry. No chat_id, prompt, response text or
        # other user data is stored here.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_chat_metrics (
                outcome TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            )
            """
        )


def _safe_ai_metric_label(value: Any, *, default: str, limit: int) -> str:
    return (
        re.sub(r"[^a-z0-9_]+", "_", str(value or default).lower()).strip("_")[:limit]
        or default
    )


def _ai_chat_outcome_label(reply: Any) -> str:
    """Return one coarse AI outcome without prompt, response or customer data."""
    reason = _safe_ai_metric_label(
        getattr(reply, "reason", "") or "none",
        default="none",
        limit=64,
    )
    if bool(getattr(reply, "handoff_required", False)):
        return f"handoff_{reason}"
    if bool(getattr(reply, "used_external_model", False)):
        return "external_ok"
    return f"fallback_{reason}"


def _ai_chat_metric_key(reply: Any, *, source: str = "beta") -> str:
    """Map an AI reply to a bounded aggregate outcome with no user content."""
    source_key = _safe_ai_metric_label(source, default="beta", limit=32)
    outcome = _ai_chat_outcome_label(reply)
    return outcome if source_key == "beta" else f"{source_key}_{outcome}"


def record_ai_chat_outcome(reply: Any, *, source: str = "beta") -> None:
    """Increment privacy-minimized AI telemetry; never store prompt/response text."""
    source_key = _safe_ai_metric_label(source, default="beta", limit=32)
    outcome_label = _ai_chat_outcome_label(reply)
    outcome = outcome_label if source_key == "beta" else f"{source_key}_{outcome_label}"
    try:
        with _db_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO ai_chat_metrics (outcome, count, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(outcome) DO UPDATE SET
                    count = count + 1,
                    updated_at = excluded.updated_at
                """,
                (outcome, int(time.time())),
            )
    except sqlite3.Error as exc:
        # Metrics are observational only and must never break a chat response.
        logger.warning("Could not store AI beta metric: %s", exc)
    _record_ops_metric("ai", source_key, outcome_label)


def ai_chat_metrics_snapshot() -> Dict[str, int]:
    """Return aggregate counters only."""
    try:
        with _db_cursor() as cur:
            cur.execute("SELECT outcome, count FROM ai_chat_metrics ORDER BY outcome")
            return {str(row["outcome"]): int(row["count"]) for row in cur.fetchall()}
    except sqlite3.Error as exc:
        logger.warning("Could not read AI beta metrics: %s", exc)
        return {}


def record_partner_click(
    service: str,
    destination: str,
    mode: str,
    *,
    source: str = "telegram_mini_app",
) -> None:
    """Persist anonymous partner-click analytics without Telegram identity."""
    service = str(service or "").strip().lower()[:32]
    destination = str(destination or "").strip()[:100]
    mode = str(mode or "").strip().lower()[:32]
    source = str(source or "").strip().lower()[:64]
    if service not in {"hotel", "esim", "transfer"}:
        return
    if mode not in {"api", "redirect", "direct"}:
        return
    if not destination or not source:
        return
    try:
        with _db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO partner_clicks "
                "(service, destination, mode, source, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (service, destination, mode, source, int(time.time())),
            )
    except sqlite3.Error as exc:
        # Partner analytics must never prevent the user from opening a link.
        logger.warning("Could not store partner click analytics: %s", exc)


def cleanup_partner_clicks(*, now: Optional[int] = None) -> int:
    """Delete anonymous partner-click events older than their retention window."""
    if PARTNER_ANALYTICS_RETENTION_DAYS <= 0:
        return 0
    current = int(time.time()) if now is None else int(now)
    cutoff = current - PARTNER_ANALYTICS_RETENTION_DAYS * 86400
    with _db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM partner_clicks WHERE created_at < ?", (cutoff,))
        deleted = max(0, int(cur.rowcount or 0))
    if deleted:
        logger.info("Partner analytics cleanup removed %d old event(s)", deleted)
    return deleted


def get_partner_analytics(days: int = 30) -> Dict[str, Any]:
    """Return aggregate partner activity without user-level attribution."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(365, days))
    now = int(time.time())
    cutoff = now - days * 86400

    with _db_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM partner_clicks WHERE created_at >= ?",
            (cutoff,),
        )
        total = int(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT service, COUNT(*) as cnt FROM partner_clicks "
            "WHERE created_at >= ? GROUP BY service ORDER BY cnt DESC, service ASC",
            (cutoff,),
        )
        by_service = [(row[0], int(row[1])) for row in cur.fetchall()]
        cur.execute(
            "SELECT mode, COUNT(*) as cnt FROM partner_clicks "
            "WHERE created_at >= ? GROUP BY mode ORDER BY cnt DESC, mode ASC",
            (cutoff,),
        )
        by_mode = [(row[0], int(row[1])) for row in cur.fetchall()]
        cur.execute(
            "SELECT destination, COUNT(*) as cnt FROM partner_clicks "
            "WHERE created_at >= ? AND destination != '' "
            "GROUP BY destination ORDER BY cnt DESC, destination ASC LIMIT 10",
            (cutoff,),
        )
        destinations = [(row[0], int(row[1])) for row in cur.fetchall()]
        cur.execute(
            "SELECT source, COUNT(*) as cnt FROM partner_clicks "
            "WHERE created_at >= ? GROUP BY source ORDER BY cnt DESC, source ASC",
            (cutoff,),
        )
        by_source = [(row[0], int(row[1])) for row in cur.fetchall()]
        cur.execute(
            "SELECT COUNT(*) FROM leads WHERE created_at >= ?",
            (cutoff,),
        )
        leads = int(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT destination, COUNT(*) as cnt FROM leads "
            "WHERE created_at >= ? AND destination IS NOT NULL AND destination != '' "
            "GROUP BY destination ORDER BY cnt DESC, destination ASC LIMIT 10",
            (cutoff,),
        )
        lead_destinations = [(row[0], int(row[1])) for row in cur.fetchall()]

    mode_counts = dict(by_mode)
    affiliate_events = mode_counts.get("api", 0) + mode_counts.get("redirect", 0)
    direct_events = mode_counts.get("direct", 0)
    affiliate_resolution_rate = (
        100.0 * affiliate_events / total if total else None
    )
    aggregate_leads_per_click = (
        100.0 * leads / total if total else None
    )
    return {
        "days": days,
        "total": total,
        "leads": leads,
        "by_service": by_service,
        "by_mode": by_mode,
        "by_source": by_source,
        "destinations": destinations,
        "lead_destinations": lead_destinations,
        "affiliate_events": affiliate_events,
        "direct_events": direct_events,
        "affiliate_resolution_rate": affiliate_resolution_rate,
        "aggregate_leads_per_click": aggregate_leads_per_click,
    }


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

def _sqlite_bool(value: Any) -> Optional[int]:
    """Normalize Python/SQLite boolean representations without turning NULL into false."""
    if value is None:
        return None
    if value is True or value == 1 or value == "1":
        return 1
    if value is False or value == 0 or value == "0":
        return 0
    return None


def set_session(chat_id: int, data: Dict[str, Any]) -> None:
    """Insert or replace a dialog session."""
    now = int(time.time())
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO sessions (
                chat_id, state, destination, origin, dates, nights, people,
                kids, kids_ages, infants, budget, budget_scope, direct_only,
                phone, review_token, source_tag, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                state=excluded.state,
                destination=excluded.destination,
                origin=excluded.origin,
                dates=excluded.dates,
                nights=excluded.nights,
                people=excluded.people,
                kids=excluded.kids,
                kids_ages=excluded.kids_ages,
                infants=excluded.infants,
                budget=excluded.budget,
                budget_scope=excluded.budget_scope,
                direct_only=excluded.direct_only,
                phone=excluded.phone,
                review_token=excluded.review_token,
                source_tag=excluded.source_tag,
                updated_at=excluded.updated_at
            """,
            (
                chat_id,
                data.get("state", ""),
                data.get("destination"),
                data.get("origin"),
                data.get("dates"),
                data.get("nights"),
                data.get("people"),
                data.get("kids"),
                _ages_to_db(data.get("kids_ages")),
                data.get("infants"),
                data.get("budget"),
                data.get("budget_scope"),
                _sqlite_bool(data.get("direct_only")),
                data.get("phone"),
                data.get("review_token"),
                data.get("source_tag"),
                data.get("updated_at", now),
            ),
        )


def update_session(chat_id: int, **kwargs) -> None:
    """Update specific fields of an existing session."""
    allowed = {
        "state", "destination", "origin", "dates", "nights", "people", "kids",
        "kids_ages", "infants", "budget", "budget_scope", "direct_only",
        "phone", "review_token", "source_tag", "updated_at",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    columns = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [chat_id]
    with _db_cursor(commit=True) as cur:
        cur.execute(f"UPDATE sessions SET {columns} WHERE chat_id = ?", values)


def get_session(chat_id: int) -> Optional[Dict[str, Any]]:
    """Return the current session for a user, normalizing SQLite booleans."""
    with _db_cursor() as cur:
        cur.execute("SELECT * FROM sessions WHERE chat_id = ?", (chat_id,))
        row = cur.fetchone()
    if not row:
        return None
    data = dict(row)
    if data.get("direct_only") is not None:
        data["direct_only"] = bool(data["direct_only"])
    return data


def session_exists(chat_id: int) -> bool:
    """Check whether a user has an active dialog session."""
    return get_session(chat_id) is not None


def delete_session(chat_id: int) -> None:
    """Remove a user's dialog session."""
    with _db_cursor(commit=True) as cur:
        lead_ids = [
            int(row[0])
            for row in cur.execute(
                "SELECT id FROM leads WHERE chat_id = ?",
                (chat_id,),
            ).fetchall()
        ]
        _travel_crm_store.delete_for_lead_ids(
            cur.connection,
            lead_ids,
            channel="telegram",
        )
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
) -> int:
    """Persist a completed tour request and return its local lead ID."""
    now = int(time.time())
    if DEMO_MODE:
        phone = mask_phone(phone)
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO leads (
                chat_id, first_name, username, destination, origin, dates, nights,
                people, kids, kids_ages, infants, budget, budget_scope, direct_only,
                phone, source_tag, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                first_name or None,
                username or None,
                info.get("destination"),
                info.get("origin"),
                info.get("dates"),
                info.get("nights"),
                info.get("people"),
                info.get("kids"),
                _ages_to_db(info.get("kids_ages")),
                info.get("infants"),
                info.get("budget"),
                info.get("budget_scope"),
                _sqlite_bool(info.get("direct_only")),
                phone,
                info.get("source_tag"),
                now,
            ),
        )
        lead_id = int(cur.lastrowid)
        try:
            crm_request = _travel_crm_adapter.trip_request_from_lead(
                lead_id=lead_id,
                info=info,
                channel="telegram",
            )
            _travel_crm_store.upsert_request(
                cur.connection,
                crm_request,
                lead_id=lead_id,
            )
            _travel_crm_store.ensure_initial_task(
                cur.connection,
                crm_request.request_id,
                datetime.utcnow(),
            )
        except Exception as exc:
            # CRM mirroring is additive. A malformed legacy field must never
            # roll back the canonical lead or prevent manager delivery.
            logger.warning(
                "CRM mirror skipped for telegram lead %s: %s",
                lead_id,
                type(exc).__name__,
            )
        return lead_id


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
    """Start a daemon that periodically enforces personal/event retention."""
    if (
        DATA_RETENTION_DAYS <= 0
        and PARTNER_ANALYTICS_RETENTION_DAYS <= 0
        and FUNNEL_ANALYTICS_RETENTION_DAYS <= 0
        and OPS_METRICS_RETENTION_DAYS <= 0
    ):
        logger.info("Data retention cleanup is disabled")
        return

    def _worker() -> None:
        while True:
            try:
                cleanup_expired_data()
                cleanup_partner_clicks()
                _funnel_metrics.cleanup(
                    _db_cursor, FUNNEL_ANALYTICS_RETENTION_DAYS
                )
                cleanup_ops_metric_events()
            except Exception as exc:
                logger.error("Error in retention worker: %s", exc)
            time.sleep(6 * 3600)  # re-check four times a day

    threading.Thread(target=_worker, daemon=True, name="data-retention").start()
    logger.info(
        "Data retention worker started "
        "(personal=%s days, partner_events=%s days, funnel_events=%s days, ops_events=%s days)",
        DATA_RETENTION_DAYS,
        PARTNER_ANALYTICS_RETENTION_DAYS,
        FUNNEL_ANALYTICS_RETENTION_DAYS,
        OPS_METRICS_RETENTION_DAYS,
    )

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
                "Telegram %d for %s: %s", resp.status_code, _log_correlation(chat_id, namespace="tg-user"), resp.text[:200],
            )
        return resp
    except requests.exceptions.RequestException as exc:
        logger.error("send_message(%s) failed: %s", _log_correlation(chat_id, namespace="tg-user"), exc)
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
    # Open the deployed Mini App from the Telegram chat menu. Keep the
    # commands fallback for environments that intentionally omit MINI_APP_URL.
    menu_button = (
        {
            "type": "web_app",
            "text": "🌐 Открыть приложение",
            "web_app": {"url": MINI_APP_URL},
        }
        if MINI_APP_URL
        else {"type": "commands"}
    )
    if not _tg_api_ok("setChatMenuButton", {"menu_button": menu_button}):
        _tg_api_ok("setChatMenuButton", {"menu_button": {"type": "commands"}})
    ensure_bot_commands()


# ---------------------------------------------------------------------------
# Admin error alerting
# ---------------------------------------------------------------------------

_last_error_alert: Dict[str, float] = {}


def _alert_admin_error(
    error_msg: str,
    exc: Optional[Exception] = None,
    *,
    alert_key: Optional[str] = None,
) -> None:
    """Send a critical error alert to the admin via Telegram (rate-limited)."""
    if not ADMIN_ERROR_ALERTS or not ADMIN_ID or not BOT_TOKEN:
        return
    # Rate-limit: don't send the same error more than once per cooldown.
    key = (alert_key or error_msg)[:100]
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


def _send_lead_to_mdt_once(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> bool:
    """Dispatch a completed request to MDT CRM and return write success."""
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


def _queue_mdt_lead(
    lead_id: int,
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> None:
    if not (MDT_RETRY_ENABLED and MDT_ENABLED and MDT_MODE == "lead" and not DEMO_MODE):
        return
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "info": dict(info),
            "phone": phone,
            "client_name": client_name,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    now = int(time.time())
    with _db_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE leads
            SET mdt_status='pending', mdt_attempts=0,
                mdt_next_retry_at=?, mdt_synced_at=NULL, mdt_payload=?
            WHERE id=?
            """,
            (now, payload, int(lead_id)),
        )


def _record_mdt_attempt(lead_id: int, ok: bool) -> None:
    now = int(time.time())
    with _db_cursor(commit=True) as cur:
        row = cur.execute(
            "SELECT mdt_attempts FROM leads WHERE id=?", (int(lead_id),)
        ).fetchone()
        attempts = int(row["mdt_attempts"] or 0) + 1 if row else 1
        if ok:
            cur.execute(
                """
                UPDATE leads
                SET mdt_status='synced', mdt_attempts=?,
                    mdt_next_retry_at=NULL, mdt_synced_at=?
                WHERE id=?
                """,
                (attempts, now, int(lead_id)),
            )
        else:
            cur.execute(
                """
                UPDATE leads
                SET mdt_status='pending', mdt_attempts=?,
                    mdt_next_retry_at=?, mdt_synced_at=NULL
                WHERE id=?
                """,
                (attempts, now + _mdt_retry_delay(attempts), int(lead_id)),
            )


def _attempt_mdt_delivery(
    lead_id: int,
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> bool:
    with _mdt_delivery_lock:
        try:
            ok = bool(_send_lead_to_mdt_once(chat_id, info, phone, client_name))
        except Exception as exc:
            logger.warning("MDT delivery failed for Telegram lead %s: %s", lead_id, exc)
            ok = False
        _record_mdt_attempt(lead_id, ok)
        return ok


def send_lead_to_mdt(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
) -> bool:
    lead_id = info.get("_local_lead_id")
    if (
        lead_id
        and MDT_RETRY_ENABLED
        and MDT_ENABLED
        and MDT_MODE == "lead"
        and not DEMO_MODE
    ):
        return _attempt_mdt_delivery(int(lead_id), chat_id, info, phone, client_name)
    return _send_lead_to_mdt_once(chat_id, info, phone, client_name)


def _deliver_mdt_lead(lead_id: int) -> bool:
    with _db_cursor() as cur:
        row = cur.execute(
            "SELECT mdt_status, mdt_payload FROM leads WHERE id=?", (int(lead_id),)
        ).fetchone()
    if not row or row["mdt_status"] == "synced" or not row["mdt_payload"]:
        return True
    try:
        payload = json.loads(row["mdt_payload"])
        info = dict(payload["info"])
        info["_local_lead_id"] = int(lead_id)
        info["_mdt_delivery_key"] = f"tg-lead-{int(lead_id)}"
        return _attempt_mdt_delivery(
            int(lead_id),
            int(payload["chat_id"]),
            info,
            str(payload["phone"]),
            payload.get("client_name"),
        )
    except Exception as exc:
        logger.warning("Invalid MDT retry payload for Telegram lead %s: %s", lead_id, exc)
        _record_mdt_attempt(int(lead_id), False)
        return False


def _retry_pending_mdt_once(now: Optional[int] = None) -> int:
    if not (MDT_RETRY_ENABLED and MDT_ENABLED and MDT_MODE == "lead" and not DEMO_MODE):
        return 0
    current = int(time.time()) if now is None else int(now)
    with _db_cursor() as cur:
        rows = cur.execute(
            """
            SELECT id FROM leads
            WHERE mdt_status='pending'
              AND COALESCE(mdt_next_retry_at, 0) <= ?
            ORDER BY id
            LIMIT ?
            """,
            (current, MDT_RETRY_BATCH_SIZE),
        ).fetchall()
    synced = 0
    for row in rows:
        if _deliver_mdt_lead(int(row["id"])):
            synced += 1
    return synced


def _mdt_retry_health(now: Optional[float] = None) -> Dict[str, Any]:
    """Return aggregate retry-queue telemetry without exposing lead PII."""
    current = int(time.time() if now is None else now)
    enabled = bool(
        MDT_RETRY_ENABLED
        and MDT_ENABLED
        and MDT_MODE == "lead"
        and not DEMO_MODE
    )
    try:
        with _db_cursor() as cur:
            row = cur.execute(
                """
                SELECT
                    COUNT(*) AS pending,
                    COALESCE(MAX(mdt_attempts), 0) AS max_attempts,
                    MIN(created_at) AS oldest_created_at,
                    MIN(mdt_next_retry_at) AS next_retry_at,
                    SUM(
                        CASE
                            WHEN COALESCE(mdt_next_retry_at, 0) <= ? THEN 1
                            ELSE 0
                        END
                    ) AS due_now
                FROM leads
                WHERE mdt_status='pending'
                """,
                (current,),
            ).fetchone()
    except sqlite3.Error as exc:
        logger.warning("Health could not read Telegram MDT retry queue: %s", exc)
        return {
            "enabled": enabled,
            "available": False,
            "pending": None,
            "due_now": None,
            "max_attempts": None,
            "oldest_pending_seconds": None,
            "next_retry_in_seconds": None,
        }

    pending = int(row["pending"] or 0) if row else 0
    oldest = row["oldest_created_at"] if row else None
    next_retry = row["next_retry_at"] if row else None
    return {
        "enabled": enabled,
        "available": True,
        "pending": pending,
        "due_now": int(row["due_now"] or 0) if row else 0,
        "max_attempts": int(row["max_attempts"] or 0) if row else 0,
        "oldest_pending_seconds": (
            max(0, current - int(oldest)) if pending and oldest is not None else None
        ),
        "next_retry_in_seconds": (
            max(0, int(next_retry) - current)
            if pending and next_retry is not None
            else None
        ),
    }


def _lead_delivery_health(now: Optional[float] = None) -> Dict[str, Any]:
    """Return 30-day Telegram and website manager-delivery aggregates."""
    current = int(time.time() if now is None else now)
    window = 30 * 86400
    cutoff = current - window
    channels: Dict[str, Any] = {}
    try:
        with _db_cursor() as cur:
            cur.execute(
                "SELECT created_at, manager_notified_at FROM leads WHERE created_at >= ?",
                (cutoff,),
            )
            channels["telegram"] = lead_delivery_snapshot(
                cur.fetchall(), window_seconds=window
            )
            try:
                cur.execute(
                    "SELECT created_at, owner_notified_at FROM website_leads WHERE created_at >= ?",
                    (cutoff,),
                )
                website_rows = cur.fetchall()
            except sqlite3.OperationalError:
                website_rows = []
            channels["website"] = lead_delivery_snapshot(
                website_rows, window_seconds=window
            )
    except sqlite3.Error as exc:
        logger.warning("Health could not read lead-delivery aggregates: %s", exc)
        return {"available": False, "window_seconds": window, "channels": {}}
    return {"available": True, "window_seconds": window, "channels": channels}


def record_funnel_event(
    channel: str,
    source: str,
    stage: str,
    outcome: str,
) -> None:
    """Persist privacy-minimized acquisition telemetry without customer identity."""
    try:
        _funnel_metrics.record(
            _db_cursor,
            channel,
            source or "direct",
            stage,
            outcome,
        )
    except sqlite3.Error as exc:
        logger.warning("Could not persist acquisition funnel event: %s", exc)


def _funnel_health(now: Optional[float] = None) -> Dict[str, Any]:
    try:
        return _funnel_metrics.snapshot(_db_cursor, now=now)
    except sqlite3.Error as exc:
        logger.warning("Health could not read acquisition funnel: %s", exc)
        return {
            "available": False,
            "window_seconds": 30 * 86400,
            "events": 0,
            "channels": {},
        }


def _record_ops_metric(category: str, subject: str, outcome: str) -> None:
    """Persist one bounded operational event without customer data."""
    safe = (
        str(category or "unknown")[:40],
        str(subject or "unknown")[:40],
        str(outcome or "unknown")[:64],
    )
    try:
        with _db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO ops_metric_events(category, subject, outcome, created_at) "
                "VALUES (?, ?, ?, ?)",
                (*safe, int(time.time())),
            )
    except sqlite3.Error as exc:
        logger.warning("Could not persist operational counter: %s", exc)


def cleanup_ops_metric_events(now: Optional[float] = None) -> int:
    """Delete old anonymous operational events; return deleted row count."""
    if OPS_METRICS_RETENTION_DAYS <= 0:
        return 0
    current = int(time.time() if now is None else now)
    cutoff = current - OPS_METRICS_RETENTION_DAYS * 86400
    try:
        with _db_cursor(commit=True) as cur:
            cur.execute("DELETE FROM ops_metric_events WHERE created_at < ?", (cutoff,))
            return int(cur.rowcount or 0)
    except sqlite3.Error as exc:
        logger.warning("Could not clean operational counters: %s", exc)
        return 0


def _ai_runtime_health(now: Optional[float] = None) -> Dict[str, Any]:
    """Return 30-day AI outcomes by fixed entrypoint without customer content."""
    current = int(time.time() if now is None else now)
    window = 30 * 86400
    cutoff = current - window
    try:
        with _db_cursor() as cur:
            rows = cur.execute(
                "SELECT subject, outcome, COUNT(*) "
                "FROM ops_metric_events "
                "WHERE category = 'ai' AND created_at >= ? "
                "GROUP BY subject, outcome",
                (cutoff,),
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("Health could not read AI runtime counters: %s", exc)
        return {"available": False, "window_seconds": window, "subjects": {}}
    return {"available": True, **event_counter_snapshot(rows, window_seconds=window)}


def _ops_event_health(now: Optional[float] = None) -> Dict[str, Any]:
    current = int(time.time() if now is None else now)
    window = 30 * 86400
    cutoff = current - window
    try:
        with _db_cursor() as cur:
            rows = cur.execute(
                "SELECT category || ':' || subject, outcome, COUNT(*) "
                "FROM ops_metric_events WHERE created_at >= ? "
                "GROUP BY category, subject, outcome",
                (cutoff,),
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("Health could not read operational counters: %s", exc)
        return {"available": False, "window_seconds": window, "subjects": {}}
    return {"available": True, **event_counter_snapshot(rows, window_seconds=window)}


def _alert_stale_mdt_retry_queue(now: Optional[float] = None) -> bool:
    """Alert the admin when the Telegram MDT retry queue is persistently stale."""
    if MDT_RETRY_ALERT_AFTER_SECONDS <= 0:
        return False
    stats = _mdt_retry_health(now)
    age = stats.get("oldest_pending_seconds")
    if (
        not stats.get("enabled")
        or not stats.get("available")
        or not stats.get("pending")
        or age is None
        or int(age) < MDT_RETRY_ALERT_AFTER_SECONDS
    ):
        return False

    message = (
        "MDT retry queue stale: "
        f"pending={int(stats.get('pending') or 0)}, "
        f"due_now={int(stats.get('due_now') or 0)}, "
        f"max_attempts={int(stats.get('max_attempts') or 0)}, "
        f"oldest={int(age)}s"
    )
    _alert_admin_error(message, alert_key="mdt_retry_queue_stale")
    return True


def _start_mdt_retry_worker() -> None:
    if not (MDT_RETRY_ENABLED and MDT_ENABLED and MDT_MODE == "lead" and not DEMO_MODE):
        return

    def _worker() -> None:
        while True:
            try:
                _retry_pending_mdt_once()
                _alert_stale_mdt_retry_queue()
            except Exception:
                logger.exception("Telegram MDT retry worker failed")
            time.sleep(MDT_RETRY_POLL_SECONDS)

    threading.Thread(target=_worker, daemon=True, name="mdt-retry").start()

def _inline_btn(text: str, callback_data: str) -> Dict[str, Any]:
    return {"text": text, "callback_data": callback_data}


def inline_keyboard(rows: List[List[Dict[str, Any]]]) -> str:
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
            [_inline_btn(BACK_BUTTON_TEXT, CB_BACK)],
            [_inline_btn(CANCEL_BUTTON_TEXT, CB_CANCEL)],
    ])


def kb_ai_lead_assist() -> str:
    """Post-lead AI shortcuts; callback payloads map only to fixed safe questions."""
    return inline_keyboard([
        [
            _inline_btn("🧳 Что взять?", f"{CB_AI_LEAD_PREFIX}packing"),
            _inline_btn("🏨 Как выбрать отель?", f"{CB_AI_LEAD_PREFIX}hotel"),
        ],
        [_inline_btn("📋 Что учесть перед поездкой?", f"{CB_AI_LEAD_PREFIX}prep")],
    ])


def kb_destinations() -> str:
    """Inline: popular destinations in two columns + cancel."""
    rows: List[List[Dict[str, Any]]] = []
    row: List[Dict[str, Any]] = []
    for i, label in enumerate(POPULAR_DESTINATIONS):
        row.append(_inline_btn(label, f"{CB_DEST_PREFIX}{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if MINI_APP_URL:
        rows.append([{
            "text": "🌐 Открыть приложение",
            "web_app": {"url": MINI_APP_URL},
        }])
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


def kb_kids_ages() -> str:
    """Inline: frequent child-age answers + manual entry + nav."""
    return inline_keyboard([
        [
            _inline_btn("🚫 Детей нет", f"{CB_KIDS_PREFIX}none"),
            _inline_btn("👶 До года", f"{CB_KIDS_PREFIX}infant"),
        ],
        [_inline_btn("✏️ Ввести вручную", f"{CB_KIDS_PREFIX}custom")],
        [
            _inline_btn(BACK_BUTTON_TEXT, CB_BACK),
            _inline_btn(CANCEL_BUTTON_TEXT, CB_CANCEL),
        ],
    ])


def _kb_choices(options: List[str], prefix: str) -> str:
    """Inline row of short choices + nav. Used for the two age-band steps."""
    rows = [[_inline_btn(o, f"{prefix}{o}") for o in options]]
    rows.append([
        _inline_btn(BACK_BUTTON_TEXT, CB_BACK),
        _inline_btn(CANCEL_BUTTON_TEXT, CB_CANCEL),
    ])
    return inline_keyboard(rows)


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
    """Generate an AI tour blurb for the client (template/Groq/REG.RU Cloud)."""
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


def _format_lead_assist_context(info: Dict[str, Any]) -> str:
    """Build a non-PII trip context for the lead assistant."""
    lines: List[str] = []
    fields = (
        ("Направление", info.get("destination")),
        ("Город вылета", info.get("origin")),
        ("Даты", info.get("dates")),
        ("Ночей", info.get("nights")),
        ("Туристов", info.get("people")),
    )
    for label, value in fields:
        if value not in (None, ""):
            lines.append(f"{label}: {value}")
    budget = info.get("budget")
    if budget not in (None, ""):
        scope = "на всю поездку" if info.get("budget_scope") == "total" else "на человека"
        lines.append(f"Бюджет: {budget} ₽ {scope}")
    if info.get("direct_only"):
        lines.append("Перелёт: предпочитается прямой")
    return "\n".join(lines)


def _latest_lead_assist_context(chat_id: int) -> Optional[Dict[str, Any]]:
    """Return recent structured lead data without contact/name fields."""
    cutoff = int(time.time()) - AI_LEAD_ASSIST_WINDOW_HOURS * 3600
    with _db_cursor() as cur:
        row = cur.execute(
            """
            SELECT destination, origin, dates, nights, people, kids, infants,
                   budget, budget_scope, direct_only, created_at
            FROM leads
            WHERE chat_id = ? AND created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (chat_id, cutoff),
        ).fetchone()
    return dict(row) if row else None


def _notify_lead_assist_handoff(
    chat_id: int,
    question: str,
    reason: str,
    info: Optional[Dict[str, Any]] = None,
) -> None:
    """Tell the human owner when the guarded AI refuses or escalates a question."""
    global _last_lead_client_id
    safe_question = redact_external_ai_text(question).strip()[:1200] or "(пустой вопрос)"
    reason_label = (reason or "human_required")[:80]
    trip_context = _format_lead_assist_context(info or {}).strip()
    context_block = (
        f"\n\nПараметры поездки:\n{trip_context}"
        if trip_context
        else ""
    )
    record_funnel_event(
        "telegram",
        str((info or {}).get("source_tag") or "direct"),
        "ai_handoff",
        "escalated",
    )
    manager_text = (
        "🤖→👩‍💼 Вопрос клиента требует менеджера\n"
        f"Telegram ID: {chat_id}\n"
        f"Причина: {reason_label}\n\n"
        f"Вопрос: {safe_question}"
        f"{context_block}\n\n"
        f"Ответить в Telegram: /send {chat_id}"
    )
    send_lead_owner_vk(manager_text)
    with _lock:
        _last_lead_client_id = chat_id
    reply_kb = kb_admin_reply(chat_id)
    for recipient in LEAD_NOTIFY_IDS:
        send_message(recipient, manager_text, reply_markup=reply_kb)


def _handle_lead_assist(
    chat_id: int,
    question: str,
    *,
    entrypoint: str = "generic",
) -> bool:
    """Answer a lead-assist question without mutating funnel state."""
    if not AI_LEAD_ASSIST_ENABLED:
        send_message(
            chat_id,
            "ИИ-помощник сейчас отключён. По заявке ответит менеджер «АПРЕЛЬ тур».",
        )
        return True

    question = str(question or "").strip()
    if not question:
        send_message(
            chat_id,
            "🤖 Напишите вопрос после команды /ask. Например:\n"
            "/ask Что взять с собой в Таиланд?",
        )
        return True
    if len(question) > AI_LEAD_ASSIST_MAX_CHARS:
        send_message(
            chat_id,
            f"🤖 Вопрос слишком длинный. Максимум {AI_LEAD_ASSIST_MAX_CHARS} символов.",
        )
        return True

    with _lock:
        current = dict(user_data.get(chat_id) or {})
    info = current or _latest_lead_assist_context(chat_id)
    if not info:
        send_message(
            chat_id,
            "Сначала заполните параметры поездки через /start, "
            "тогда ИИ сможет отвечать с учётом вашей заявки.",
        )
        return True

    send_typing(chat_id)
    reply = _generate_ai_chat_reply(
        question,
        enabled=True,
        groq_client=selection_ai_provider.client if selection_ai_provider.ready else None,
        groq_model=selection_ai_provider.model,
        verified_context=_format_lead_assist_context(info),
        timeout=float(AI_CHAT_TIMEOUT_SECONDS),
        log=logger,
    )
    entrypoint_key = _safe_ai_metric_label(entrypoint, default="generic", limit=16)
    metric_source = "lead_assist" if entrypoint_key == "generic" else f"lead_{entrypoint_key}"
    record_ai_chat_outcome(reply, source=metric_source)
    send_message(chat_id, f"🤖 {reply.text}")
    if reply.handoff_required or reply.reason in {"provider_unavailable", "provider_error"}:
        _notify_lead_assist_handoff(chat_id, question, reply.reason, info=info)
    return True


def _ai_beta_allowed(chat_id: int) -> bool:
    """Closed beta gate. No public route exists even when this is enabled."""
    return AI_CHAT_ENABLED and chat_id in AI_CHAT_BETA_IDS


def _ai_external_provider_ready() -> bool:
    """Fail closed until external free-form transfer and Groq ZDR are explicit."""
    return (
        AI_CHAT_EXTERNAL_PROVIDER_ENABLED
        and GROQ_ZDR_CONFIRMED
        and groq_client is not None
    )


def _handle_ai_beta_command(chat_id: int, text: str) -> bool:
    """Handle /ai for allowlisted internal testers without altering lead state."""
    command, _, raw_question = text.partition(" ")
    if command != "/ai" or not _ai_beta_allowed(chat_id):
        return False

    question = raw_question.strip()
    if not question:
        send_message(
            chat_id,
            "🧪 AI beta\n\nИспользование: /ai ваш вопрос о поездке\n"
            "Не отправляйте паспорт, данные карты, пароли или медицинские данные.",
        )
        return True
    if len(question) > AI_CHAT_MAX_CHARS:
        send_message(
            chat_id,
            f"🧪 AI beta: вопрос слишком длинный. Максимум {AI_CHAT_MAX_CHARS} символов.",
        )
        return True

    send_typing(chat_id)
    reply = _generate_ai_chat_reply(
        question,
        enabled=True,
        groq_client=groq_client if _ai_external_provider_ready() else None,
        groq_model=GROQ_MODEL,
        timeout=float(AI_CHAT_TIMEOUT_SECONDS),
        log=logger,
    )
    record_ai_chat_outcome(reply)
    send_message(chat_id, f"🧪 AI beta · ИИ-помощник\n\n{reply.text}")
    return True


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


def _admin_funnel(chat_id: int, arg: str) -> bool:
    data = _funnel_health()
    send_message(
        chat_id,
        _funnel_metrics.format_report(
            data,
            channels=["telegram", "website"],
            max_sources=10,
        ),
    )
    return True


def _admin_providers(chat_id: int, arg: str) -> bool:
    send_message(chat_id, _provider_status.format_report())
    return True


def _admin_ai_stats(chat_id: int, arg: str) -> bool:
    metrics = ai_chat_metrics_snapshot()
    runtime = _ai_runtime_health()
    if not metrics and not runtime.get("subjects"):
        send_message(chat_id, "🧪 AI: статистики пока нет.")
        return True

    total = sum(metrics.values())
    # Preserve the original first line for operators/scripts that already
    # recognize it; the recent privacy-safe breakdown is appended below.
    lines = [f"🧪 AI beta: {total} запросов"]
    for outcome, count in metrics.items():
        lines.append(f"• {outcome}: {count}")

    subjects = runtime.get("subjects") or {}
    if subjects:
        lines.append("")
        lines.append("📊 Последние 30 дней:")
        for source, outcomes in sorted(subjects.items()):
            source_total = sum(int(v) for v in outcomes.values())
            lines.append(f"• {source}: {source_total}")
            for outcome, count in sorted(outcomes.items()):
                lines.append(f"  - {outcome}: {count}")
    send_message(chat_id, "\n".join(lines))
    return True


def _admin_ai_status(chat_id: int, arg: str) -> bool:
    # Never print secrets or tester IDs. These booleans are sufficient to
    # diagnose why the external beta is intentionally failing closed.
    send_message(
        chat_id,
        "🧪 AI beta status\n\n"
        f"beta_enabled: {AI_CHAT_ENABLED}\n"
        f"allowlisted_testers: {len(AI_CHAT_BETA_IDS)}\n"
        f"external_provider_enabled: {AI_CHAT_EXTERNAL_PROVIDER_ENABLED}\n"
        f"groq_key_configured: {bool(GROQ_API_KEY)}\n"
        f"groq_zdr_confirmed: {GROQ_ZDR_CONFIRMED}\n"
        f"external_provider_ready: {_ai_external_provider_ready()}\n"
        f"model: {GROQ_MODEL}",
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


def _admin_agentdesk(chat_id: int, arg: str) -> bool:
    token = agent_extension_token()
    if not token:
        send_message(
            chat_id,
            "Agent Desk сейчас недоступен: BOT_TOKEN не настроен.",
        )
        return True
    send_message(
        chat_id,
        "🧩 <b>TurBot Agent Desk</b>\n\n"
        "Вставьте этот pairing-token в раздел «Подключение» расширения:\n\n"
        f"<code>{html.escape(token)}</code>\n\n"
        "Токен предназначен только для внутреннего Agent Desk. "
        "Не публикуйте его и не отправляйте клиентам.",
        parse_mode="HTML",
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

    partner_7d = get_partner_analytics(7)
    partner_30d = get_partner_analytics(30)
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

    lines.append("\n🔗 Партнёрские переходы:")
    lines.append(f"  за 7 дней: {partner_7d['total']}")
    lines.append(f"  за 30 дней: {partner_30d['total']}")
    if partner_30d["by_service"]:
        labels = {"hotel": "отели", "esim": "eSIM", "transfer": "трансферы"}
        lines.append("  По сервисам за 30 дней:")
        for service, cnt in partner_30d["by_service"]:
            lines.append(f"    {labels.get(service, service)}: {cnt}")
    if partner_30d["by_mode"]:
        mode_labels = {
            "api": "Partner Links API",
            "redirect": "affiliate redirect",
            "direct": "прямой fallback",
        }
        lines.append("  По типу ссылки:")
        for mode, cnt in partner_30d["by_mode"]:
            lines.append(f"    {mode_labels.get(mode, mode)}: {cnt}")
    if partner_30d["destinations"]:
        lines.append("  Топ направлений:")
        for destination, cnt in partner_30d["destinations"][:5]:
            lines.append(f"    {destination}: {cnt}")
    send_message(chat_id, "\n".join(lines))
    return True


def _admin_partners(chat_id: int, arg: str) -> bool:
    """Show aggregate partner-link activity for a configurable window."""
    raw = (arg or "").strip()
    tokens = raw.split() if raw else []
    force_refresh = False
    day_tokens: List[str] = []
    for token in tokens:
        lowered = token.lower()
        if lowered in {"reload", "refresh"}:
            force_refresh = True
        else:
            day_tokens.append(token)

    if len(day_tokens) > 1:
        send_message(
            chat_id,
            "Использование: /partners [дни] [reload], например /partners 30 reload",
        )
        return True

    if day_tokens:
        try:
            days = int(day_tokens[0])
        except ValueError:
            send_message(
                chat_id,
                "Использование: /partners [дни] [reload], например /partners 30 reload",
            )
            return True
        if not 1 <= days <= 365:
            send_message(chat_id, "Период /partners должен быть от 1 до 365 дней.")
            return True
    else:
        days = 30

    stats = get_partner_analytics(days)
    service_labels = {"hotel": "🏨 Отели", "esim": "📶 eSIM", "transfer": "🚕 Трансферы"}
    mode_labels = {
        "api": "Partner Links API",
        "redirect": "affiliate redirect",
        "direct": "прямой fallback",
    }

    lines = [
        f"🔗 Партнёрская аналитика · {days} дн.",
        "",
        f"Переходов: {stats['total']}",
        f"Заявок в боте за тот же период: {stats['leads']}",
    ]
    rate = stats["affiliate_resolution_rate"]
    if rate is not None:
        lines.append(f"Партнёрская ссылка получена: {rate:.1f}% событий")
    aggregate = stats["aggregate_leads_per_click"]
    if aggregate is not None:
        lines.append(f"Сопоставление объёмов: {aggregate:.1f} заявок на 100 переходов")

    if stats["by_service"]:
        lines.append("\nПо сервисам:")
        for service, cnt in stats["by_service"]:
            lines.append(f"  {service_labels.get(service, service)}: {cnt}")

    if stats["by_mode"]:
        lines.append("\nКак открывались ссылки:")
        for mode, cnt in stats["by_mode"]:
            lines.append(f"  {mode_labels.get(mode, mode)}: {cnt}")

    if stats["destinations"]:
        lines.append("\nТоп направлений по переходам:")
        for destination, cnt in stats["destinations"][:5]:
            lines.append(f"  {destination}: {cnt}")

    if stats["lead_destinations"]:
        lines.append("\nТоп направлений по заявкам:")
        for destination, cnt in stats["lead_destinations"][:5]:
            lines.append(f"  {destination}: {cnt}")

    lines.append("\n💶 Travelpayouts:")
    try:
        performance = _travelpayouts_stats.fetch_partner_performance(
            days,
            force=force_refresh,
        )
    except _travelpayouts_stats.TravelpayoutsStatsNotConfigured:
        lines.append("  API статистики не настроен — локальные клики считаются, брони/доход пока нет.")
    except _travelpayouts_stats.TravelpayoutsStatsError as exc:
        logger.warning("Travelpayouts statistics unavailable: %s", exc)
        lines.append("  Статистика временно недоступна; локальные клики выше сохранены.")
    else:
        if force_refresh:
            lines.append("  ♻️ Данные принудительно обновлены.")
        totals = performance["totals"]
        active_bookings = totals["paid"] + totals["processing"]
        lines.extend([
            f"  Брони: {totals['bookings']}",
            f"    оплачено: {totals['paid']}",
            f"    в обработке: {totals['processing']}",
            f"    отменено: {totals['canceled']}",
            f"  Подтверждённый доход: €{totals['paid_profit_eur']:.2f}",
            f"  Стоимость неотменённых броней: €{totals['booking_value_eur']:.2f}",
        ])
        affiliate_events = stats["affiliate_events"]
        if affiliate_events:
            ratio = 100.0 * active_bookings / affiliate_events
            lines.append(
                f"  Агрегат active bookings / affiliate clicks: {ratio:.1f}%"
            )

        service_rows = []
        for service, values in performance["by_service"].items():
            if not values["bookings"] and not values["paid_profit_eur"]:
                continue
            service_rows.append(
                f"  {service_labels.get(service, service)}: "
                f"{values['bookings']} брон., {values['paid']} оплач., "
                f"€{values['paid_profit_eur']:.2f}"
            )
        if service_rows:
            lines.append("  По сервисам:")
            lines.extend(service_rows)

    lines.extend([
        "",
        "ℹ️ Без user-level ID: переходы и заявки не связываются по человеку.",
        "Travelpayouts брони связывает с нашими SubID, но не с конкретным chat_id.",
        "Проценты выше — агрегат по периоду, не user-level attribution.",
    ])
    send_message(chat_id, "\n".join(lines))
    return True

def _admin_export(chat_id: int, arg: str) -> bool:
    """Export completed leads as a formatted message (last 50)."""
    with _db_cursor() as cur:
        cur.execute(
            "SELECT chat_id, first_name, destination, dates, people, budget, source_tag, phone, created_at "
            "FROM leads ORDER BY created_at DESC LIMIT 50"
        )
        rows = cur.fetchall()
    if not rows:
        send_message(chat_id, "Нет завершённых заявок для экспорта.")
        return True
    lines = [f"📋 Экспорт заявок ({len(rows)}):\n"]
    for i, row in enumerate(rows, 1):
        cid, name, dest, dates, people, budget, source_tag, phone, created = row
        when = datetime.fromtimestamp(created).strftime("%d.%m.%Y") if created else "?"
        who = name or str(cid)
        source = source_tag or "organic"
        lines.append(
            f"{i}. [{when}] {who} | {dest or '?'} | {dates or '?'} | "
            f"{people or '?'} чел | {budget or '?'}₽ | src={source} | {phone}"
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
    "/funnel":       _admin_funnel,
    "/providers":    _admin_providers,
    "/partners":     _admin_partners,
    "/export":       _admin_export,
    "/restart":      _admin_restart,
    "/send":         _admin_send,
    "/cancel_reply": _admin_cancel_reply_cmd,
    "/broadcast":    _admin_broadcast,
    "/followup":     _admin_followup,
    "/mdt":          _admin_mdt,
    "/agentdesk":    _admin_agentdesk,
    "/tutu":         _admin_tutu,
    "/ai_stats":     _admin_ai_stats,
    "/ai_status":    _admin_ai_status,
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


def handle_start(chat_id: int, first_name: str = "", source_tag: str = "") -> None:
    """Begin the tour-selection dialog and retain a validated campaign source."""
    with _lock:
        previous_source = str((user_data.get(chat_id) or {}).get("source_tag") or "")
    source_tag = normalise_source_tag(previous_source or source_tag)
    record_funnel_event("telegram", source_tag or "direct", "start", "opened")

    if CONSENT_MODE == "strict" and not has_consent(chat_id):
        with _lock:
            user_data[chat_id] = {
                "state": STATE_CONSENT,
                "source_tag": source_tag or None,
                "updated_at": int(time.time()),
            }
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
            user_data[chat_id] = {
                "state": STATE_CONSENT,
                "source_tag": source_tag or None,
                "updated_at": int(time.time()),
            }
        _mark_dirty(chat_id)
        send_message(
            chat_id,
            _welcome_text(first_name),
            parse_mode="HTML",
            reply_markup=kb_soft_start(),
        )
        return

    _begin_destination(chat_id, first_name, source_tag=source_tag)


def _begin_destination(chat_id: int, first_name: str = "", source_tag: str = "") -> None:
    """Enter the first data-collection step while preserving campaign attribution."""
    with _lock:
        previous_source = str((user_data.get(chat_id) or {}).get("source_tag") or "")
        effective_source = normalise_source_tag(previous_source or source_tag)
        user_data[chat_id] = {
            "state": STATE_DESTINATION,
            "source_tag": effective_source or None,
            "updated_at": int(time.time()),
        }
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
        _begin_destination(chat_id, first_name, source_tag=str(info.get("source_tag") or ""))
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
        "Возраст детей спрошу следующим вопросом.\n"
        "Кнопка или число от 1 до 50.",
        reply_markup=kb_people(),
        parse_mode="HTML",
    )


def _ask_kids_ages(chat_id: int) -> None:
    """Возрасты детей одним числовым ответом.

    Отдельный вопрос «дети до 12 едут?» убран: он спрашивал то, что и так
    видно из возрастов, и добавлял шаг ради ответа «да».
    """
    send_message(
        chat_id,
        "🎂 <b>Напишите возраст каждого ребёнка</b>\n\n"
        "Возраст детей укажите числами через запятую — например: <code>5, 9</code>\n"
        "Малыша можно указать словами «до года».\n"
        "Если детей нет — нажмите кнопку ниже.",
        reply_markup=kb_kids_ages(),
        parse_mode="HTML",
    )


def _ask_budget(chat_id: int) -> None:
    send_message(
        chat_id,
        "💰 <b>Бюджет на человека</b> (примерно, в рублях)\n\n"
        "Выберите кнопку или укажите одну максимальную сумму: 100000 ₽ или 100 тыс.",
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
    info["state"] = STATE_KIDS_AGES
    _ask_kids_ages(chat_id)


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


def _step_kids_ages(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    raw = (text or "").strip()
    if raw == f"{CB_KIDS_PREFIX}none":
        raw = "0"
    elif raw == f"{CB_KIDS_PREFIX}infant":
        raw = "до года"
    elif raw == f"{CB_KIDS_PREFIX}custom":
        send_message(
            chat_id,
            "✏️ Напишите возраст каждого ребёнка через запятую — например: 5, 9.",
            reply_markup=kb_nav(include_back=True),
        )
        return
    ok, ages, problem = parse_kids_ages(raw)
    if not ok:
        send_message(chat_id, problem)
        return
    info["kids_ages"] = ages
    # The bands are derived, never asked twice: two separate counts could
    # disagree with each other, one list of ages cannot.
    _, info["kids"], info["infants"] = party_bands(info)
    info["state"] = STATE_BUDGET
    send_message(chat_id, f"👥 Записал: {_esc(_party_text(info))}", parse_mode="HTML")
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
                info["budget"] = BUDGET_PRESETS[idx][1]; info["budget_open_ended"] = idx == len(BUDGET_PRESETS) - 1
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
            "Укажите одну максимальную сумму на человека, например 120000 ₽ или 120 тыс. "
            "Если бюджет 100000–120000 ₽, введите 120000. Можно выбрать кнопку ниже.",
            reply_markup=kb_budget(),
        )
        return
    info["budget"] = value; info["budget_open_ended"] = False
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
        _prepare_review(chat_id, contact, info)
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
        _prepare_review(chat_id, phone, info)
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
    _prepare_review(chat_id, phone, info)


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
    _prepare_review(chat_id, contact, info)


def _prepare_review(chat_id: int, contact: str, info: Dict[str, Any]) -> None:
    """Save a draft contact; lead creation only follows a matching send button."""
    previous = info.get("state")
    info["phone"] = mask_phone(contact) if DEMO_MODE and info.get("contact_method") == "phone" else contact
    info["state"] = STATE_REVIEW
    info["review_token"] = secrets.token_hex(8)
    _mark_dirty(chat_id, user=False)
    if previous == STATE_PHONE:
        send_message(chat_id, "Номер записан. Осталось проверить заявку.", reply_markup=hide_keyboard())
    _ask_review(chat_id, info)


def _budget_scope_text(info: Dict[str, Any]) -> str:
    return "на всю поездку" if info.get("budget_scope") == "total" else "на человека"


def _trip_details_text(info: Dict[str, Any], *, html: bool = True) -> str:
    """Keep optional Mini App details visible throughout the lead handoff."""
    text = ""
    if info.get("nights") is not None:
        nights = _esc(info["nights"]) if html else str(info["nights"])
        text += f"🌙 Ночей: {nights}\n"
    if "direct_only" in info:
        flight = "только прямой, если доступен" if info["direct_only"] else "прямой или с пересадкой"
        text += f"✈️ Перелёт: {flight}\n"
    return text


def _ask_review(chat_id: int, info: Dict[str, Any]) -> None:
    token = info["review_token"]
    budget = f"{int(info['budget']):,}".replace(",", " ")
    send_message(
        chat_id,
        "📝 <b>Проверьте заявку</b>\n\n"
        f"📍 Направление: {_esc(info.get('destination', '?'))}\n"
        f"🛫 Откуда: {_esc(info.get('origin', '?'))}\n"
        f"📅 Даты: {_esc(info.get('dates', '?'))}\n"
        f"{_trip_details_text(info)}"
        f"👥 Состав: {_esc(_party_text(info))}\n"
        f"💰 Бюджет: {'от' if info.get('budget_open_ended') else 'до'} {budget} ₽ {_budget_scope_text(info)}\n"
        f"📞 Связь: {_esc(info.get('phone', '?'))}\n\n"
        "Заявка ещё не отправлена. Если всё верно, нажмите «Отправить менеджеру».",
        parse_mode="HTML",
        reply_markup=inline_keyboard([
            [_inline_btn("✅ Отправить менеджеру", f"{CB_REVIEW_PREFIX}send:{token}")],
            [_inline_btn("✏️ Изменить", f"{CB_REVIEW_PREFIX}edit:{token}")],
            [_inline_btn(CANCEL_BUTTON_TEXT, f"{CB_REVIEW_PREFIX}cancel:{token}")],
        ]),
    )


def _step_review(chat_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    # Plain text never submits a draft accidentally. Buttons identify its version.
    _ask_review(chat_id, info)


def _process_review(chat_id: int, data: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    parts = data.split(":")
    if (len(parts) != 3 or info.get("state") != STATE_REVIEW
            or not info.get("review_token") or parts[2] != info["review_token"]):
        send_message(chat_id, "Эта кнопка устарела. Продолжите текущий шаг заявки.")
        return
    action = parts[1]
    if action == "send":
        handle_completion(chat_id, info["phone"], message, review_token=parts[2])
    elif action == "cancel":
        handle_cancel(chat_id)
    elif action == "edit":
        fields = [("Направление", STATE_DESTINATION), ("Город вылета", STATE_ORIGIN),
                  ("Даты", STATE_DATES), ("Туристы", STATE_PEOPLE),
                  ("Бюджет", STATE_BUDGET), ("Способ связи", STATE_CONTACT)]
        send_message(chat_id, "Что изменить? Затем пройдём оставшиеся шаги и снова проверим заявку.",
                     reply_markup=inline_keyboard([
                         [_inline_btn(label, f"{CB_REVIEW_PREFIX}{state}:{parts[2]}")]
                         for label, state in fields
                     ] + [[_inline_btn("◀️ К заявке", f"{CB_REVIEW_PREFIX}show:{parts[2]}")]]))
    elif action == "show":
        _ask_review(chat_id, info)
    elif action in (STATE_DESTINATION, STATE_ORIGIN, STATE_DATES, STATE_PEOPLE,
                    STATE_BUDGET, STATE_CONTACT):
        info["review_token"] = None
        info["phone"] = None
        info["state"] = action
        _prompt_for_state(chat_id, action)


# state -> step handler
STATE_HANDLERS: Dict[str, Callable[[int, str, Dict[str, Any], Dict[str, Any]], None]] = {
    STATE_CONSENT:     _step_consent,
    STATE_DESTINATION: _step_destination,
    STATE_ORIGIN:      _step_origin,
    STATE_DATES:       _step_dates,
    STATE_PEOPLE:      _step_people,
    # Вопрос про количество детей убран; сессии, стоящие на нём, отвечают на
    # следующий вопрос — про возрасты.
    STATE_KIDS:        _step_kids_ages,
    STATE_KIDS_AGES:   _step_kids_ages,
    # A session parked on the retired infants question lands here on its next
    # reply. Asking for ages is the right next thing either way, so the client
    # never notices the step disappeared from under them.
    STATE_INFANTS:     _step_kids_ages,
    STATE_BUDGET:      _step_budget,
    STATE_CONTACT:     _step_contact,
    STATE_PHONE:       _step_phone,
    STATE_VK:          _step_vk,
    STATE_REVIEW:      _step_review,
}

PREVIOUS_STATE: Dict[str, str] = {
    STATE_ORIGIN:      STATE_DESTINATION,
    STATE_DATES:       STATE_ORIGIN,
    STATE_PEOPLE:      STATE_DATES,
    STATE_KIDS:        STATE_PEOPLE,
    STATE_KIDS_AGES:   STATE_PEOPLE,
    STATE_INFANTS:     STATE_PEOPLE,
    STATE_BUDGET:      STATE_KIDS_AGES,
    STATE_CONTACT:     STATE_BUDGET,
    STATE_PHONE:       STATE_CONTACT,
    STATE_VK:          STATE_CONTACT,
    STATE_REVIEW:      STATE_CONTACT,
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
    elif state in (STATE_KIDS, STATE_KIDS_AGES, STATE_INFANTS):
        _ask_kids_ages(chat_id)
    elif state == STATE_BUDGET:
        _ask_budget(chat_id)
    elif state == STATE_CONTACT:
        _ask_contact(chat_id)
    elif state == STATE_REVIEW:
        _ask_review(chat_id, user_data[chat_id])
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
    info["review_token"] = None
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
        "✅ <b>Заявка принята!</b> Наталья Ильина, менеджер «АПРЕЛЬ тур», свяжется с вами в ближайшее время.\n"
        f"☎️ Контакт: {_esc(LEAD_OWNER_PHONE)}\n\n"
        f"📍 Направление: {_esc(info.get('destination', '?'))}\n"
        + (f"🛫 Откуда: {_esc(info['origin'])}\n" if info.get("origin") else "")
        + f"📅 Даты: {_esc(info.get('dates', '?'))}\n"
        f"{_trip_details_text(info)}"
        f"👥 Состав: {_esc(_party_text(info))}\n"
        f"💰 Бюджет: {'от' if info.get('budget_open_ended') else 'до'} {_esc(info.get('budget', '?'))} ₽ {_budget_scope_text(info)}\n"
        f"📞 Связь: {_esc(phone)}\n\n"
        "Спасибо, что выбрали нас 🌺",
        reply_markup=hide_keyboard(),
        parse_mode="HTML",
    )
    if AI_LEAD_ASSIST_ENABLED:
        send_message(
            chat_id,
            "🤖 <b>ИИ-помощник</b> может подсказать по подготовке к поездке. "
            "Выберите вопрос ниже или напишите <code>/ask ваш вопрос</code>.",
            reply_markup=kb_ai_lead_assist(),
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
        f"<i>Владелец лида: {_esc(LEAD_OWNER_NAME)} · {_esc(LEAD_OWNER_PHONE)}</i>\n\n"
        f"От: {who}\n"
        f"ID: <code>{chat_id}</code>\n"
        + (
            f"📊 Источник: <code>{_esc(info['source_tag'])}</code>\n"
            if info.get("source_tag") else ""
        )
        + f"📍 {_esc(info.get('destination', '?'))}\n"
        + (f"🛫 Откуда: {_esc(info['origin'])}\n" if info.get("origin") else "")
        + f"📅 {_esc(info.get('dates', '?'))}\n"
        f"{_trip_details_text(info)}"
        f"👥 {_esc(_party_text(info))}\n"
        f"💰 {'от' if info.get('budget_open_ended') else 'до'} {_esc(info.get('budget', '?'))} ₽ {_budget_scope_text(info)}\n"
        f"📞 Связь: <code>{_esc(phone)}</code>\n\n"
        f"Нажмите «✍️ Ответить» ниже — или /send {chat_id}\n\n"
        f"⚡ SLA: {_esc(MANAGER_SLA_HINT)}\n"
        f"💬 Быстрый ответ:\n{_esc(_manager_quick_reply_text())}"
    )


def kb_admin_reply(client_chat_id: int) -> str:
    """Inline button on lead notify: one tap to arm reply mode."""
    return inline_keyboard([[
        _inline_btn("✍️ Ответить клиенту", f"{CB_ADMIN_REPLY_PREFIX}{client_chat_id}"),
    ]])


def send_lead_owner_vk(text: str) -> bool:
    """Deliver a manager-facing message to Natalya's VK private dialog."""
    if not VK_ACCESS_TOKEN or LEAD_OWNER_VK_ID <= 0:
        logger.warning("VK lead-owner delivery is not configured")
        return False
    try:
        response = telegram_session.post(
            "https://api.vk.com/method/messages.send",
            data={
                "access_token": VK_ACCESS_TOKEN,
                "v": VK_API_VERSION,
                "peer_id": LEAD_OWNER_VK_ID,
                "random_id": secrets.randbelow(2**31 - 1) + 1,
                "message": text,
            },
            timeout=HTTP_TIMEOUT,
        )
        payload = response.json()
        if response.status_code == 200 and "error" not in payload:
            logger.info("Manager message delivered to VK owner %s", LEAD_OWNER_VK_ID)
            return True
        logger.error("VK owner delivery failed: %s", str(payload.get("error") or response.text)[:300])
    except Exception as exc:
        logger.error("VK owner delivery error: %s", exc)
    return False


def _notify_admin(
    chat_id: int,
    info: Dict[str, Any],
    phone: str,
    client_name: Optional[str],
    username: str = "",
) -> bool:
    """2. Deliver the saved lead to Natalya in VK and optional ops copies."""
    global _last_lead_client_id
    recipients = LEAD_NOTIFY_IDS

    owner_text = (
        f"🔔 Новая заявка (Telegram)\n"
        f"👩‍💼 Владелец: {LEAD_OWNER_NAME}\n\n"
        f"Клиент: {client_name or 'без имени'}"
        + (f" @{username}" if username else "")
        + f"\nTelegram ID: {chat_id}\n"
        + (f"Источник: {info['source_tag']}\n" if info.get("source_tag") else "")
        + f"📍 {info.get('destination', '?')}\n"
        + (f"🛫 Откуда: {info['origin']}\n" if info.get("origin") else "")
        + f"📅 {info.get('dates', '?')}\n"
        + _trip_details_text(info, html=False)
        + f"👥 {_party_text(info)}\n"
        + f"💰 {'от' if info.get('budget_open_ended') else 'до'} {info.get('budget', '?')} ₽ {_budget_scope_text(info)}\n"
        + f"📞 Связь клиента: {phone}\n\n"
        + "🔎 Подбор менеджеру:\n"
        + f"Tourvisor PRO: {MANAGER_TOURVISOR_URL}\n"
        + f"Sletat PRO: {MANAGER_SLETAT_URL}\n"
        + f"Qui-Quo: {MANAGER_QUIQUO_URL}\n\n"
        + f"⚡ SLA: {MANAGER_SLA_HINT}\n"
        + f"💬 Быстрый ответ:\n{_manager_quick_reply_text()}"
    )
    owner_delivered = send_lead_owner_vk(owner_text)
    if not recipients and not owner_delivered:
        logger.warning(
            "Lead from %s saved but manager delivery is unavailable",
            _log_correlation(chat_id, namespace="tg-user"),
        )
        return False

    with _lock:
        _last_lead_client_id = chat_id

    text = _format_lead_notify_text(
        chat_id, info, phone, client_name, username=username, source_label="Telegram",
    )
    reply_kb = kb_admin_reply(chat_id)
    delivered = owner_delivered
    for recipient in recipients:
        resp = send_message(recipient, text, parse_mode="HTML", reply_markup=reply_kb)
        if resp is not None and getattr(resp, "status_code", 0) == 200:
            delivered = True
            logger.info("Lead from %s delivered to Telegram manager %s", _log_correlation(chat_id, namespace="tg-user"), _log_correlation(recipient, namespace="tg-manager"))
            continue
        # Fallback without HTML if Telegram rejected parse_mode (rare).
        if resp is not None and getattr(resp, "status_code", 0) != 200:
            plain = (
                f"🔔 Новая заявка (Telegram)!\n\n"
                f"От: {client_name or 'без имени'}"
                f"{(' @' + username) if username else ''}\n"
                f"ID: {chat_id}\n"
                + (f"📊 Источник: {info['source_tag']}\n" if info.get("source_tag") else "")
                + f"📍 {info.get('destination', '?')}\n"
                + (f"🛫 Откуда: {info['origin']}\n" if info.get("origin") else "")
                + f"📅 {info.get('dates', '?')}\n"
                + _trip_details_text(info, html=False)
                + f"👥 {_party_text(info)}\n"
                f"💰 {'от' if info.get('budget_open_ended') else 'до'} {info.get('budget', '?')} ₽ {_budget_scope_text(info)}\n"
                f"📞 {phone}\n\n"
                f"Ответить: /send {chat_id}"
            )
            resp2 = send_message(recipient, plain, reply_markup=reply_kb)
            if resp2 is not None and getattr(resp2, "status_code", 0) == 200:
                delivered = True
                logger.info(
                    "Lead from %s delivered to manager %s (plain-text fallback)", _log_correlation(chat_id, namespace="tg-user"), _log_correlation(recipient, namespace="tg-manager"),
                )
                continue
        logger.error(
            "Failed to deliver lead from %s to Telegram manager %s", _log_correlation(chat_id, namespace="tg-user"), _log_correlation(recipient, namespace="tg-manager"),
        )
    return delivered


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
    # Bands come from the exact ages, so a 14-year-old is searched as an adult
    # and a one-year-old as an infant — which is what the airline will charge.
    _adults, _children, _infants = party_bands(info)
    try:
        return _tutu.search_offers(
            _tutu_settings(),
            telegram_session,
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
        logger.error("Post-completion side effects failed for %s: %s", _log_correlation(chat_id, namespace="tg-user"), exc)
        _alert_admin_error("Post-completion side effects failed", exc)


def handle_completion(chat_id: int, phone: str, message: Dict[str, Any], *, review_token: Optional[str] = None) -> None:
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
            logger.info("Concurrent completion ignored for %s", _log_correlation(chat_id, namespace="tg-user"))
            return
        if review_token is not None and (
            live.get("state") != STATE_REVIEW or live.get("review_token") != review_token
        ):
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
        lead_id = save_lead(chat_id, info, phone, first_name=first_name, username=username)
        _record_ops_metric("lead", "telegram", "accepted")
        record_funnel_event(
            "telegram", str(info.get("source_tag") or "direct"), "lead", "accepted"
        )
    except Exception as exc:
        _record_ops_metric("lead", "telegram", "save_failure")
        logger.error("Failed to save lead for %s: %s", _log_correlation(chat_id, namespace="tg-user"), exc)
        _alert_admin_error("Failed to save lead", exc)
        with _lock:
            live.pop("_completing", None)
        send_message(chat_id, "Не удалось сохранить заявку. Она ещё не отправлена. Попробуйте ещё раз.")
        if live.get("state") == STATE_REVIEW:
            _ask_review(chat_id, live)
        return

    delivery_info = dict(info)
    delivery_info["_mdt_delivery_key"] = f"tg-lead-{lead_id}"
    delivery_info["_local_lead_id"] = lead_id

    # Everything below is downstream of the durable local INSERT. A broken
    # notifier, retry-queue update or Telegram call must not strand the session
    # in _completing and tempt a later retry into creating a second local lead.
    try:
        _queue_mdt_lead(lead_id, chat_id, delivery_info, phone, client_name)
    except Exception as exc:
        logger.error("Failed to queue MDT delivery for local lead %s: %s", lead_id, exc)
        _alert_admin_error("Failed to queue MDT delivery", exc)

    try:
        _confirm_to_user(chat_id, info, phone)  # 1. Confirm to user
    except Exception as exc:
        logger.error("Failed to confirm saved lead %s to client %s: %s", lead_id, _log_correlation(chat_id, namespace="tg-user"), exc)
        _alert_admin_error("Failed to confirm saved lead to client", exc)

    # 2. Notify bot creator / admins in Telegram. Failure is operationally
    # important, but the customer's already-saved request must remain durable.
    try:
        manager_delivered = _notify_admin(
            chat_id, info, phone, client_name, username=username or ""
        )
        record_funnel_event(
            "telegram",
            str(info.get("source_tag") or "direct"),
            "manager",
            "delivered" if manager_delivered else "failed",
        )
        if manager_delivered:
            with _db_cursor(commit=True) as cur:
                cur.execute(
                    "UPDATE leads SET manager_notified_at=? "
                    "WHERE id=? AND manager_notified_at IS NULL",
                    (int(time.time()), lead_id),
                )
    except Exception as exc:
        record_funnel_event(
            "telegram", str(info.get("source_tag") or "direct"), "manager", "failed"
        )
        logger.error("Failed to notify manager about saved lead %s: %s", lead_id, exc)
        _alert_admin_error("Failed to notify manager about saved lead", exc)

    with _lock:                                       # 3. Clean up session promptly
        user_data.pop(chat_id, None)
    try:
        delete_session(chat_id)
    except Exception as exc:
        # In-memory cleanup still prevents a duplicate in this process. Alert
        # because the stale SQLite session should be cleaned operationally.
        logger.error("Failed to delete completed session for lead %s: %s", lead_id, exc)
        _alert_admin_error("Failed to delete completed session", exc)

    # 4–5. CRM + AI can be slow (network); don't block Telegram's webhook ACK.
    if SYNC_COMPLETION:
        _post_completion_side_effects(chat_id, delivery_info, phone, client_name)
    else:
        threading.Thread(
            target=_post_completion_side_effects,
            args=(chat_id, delivery_info, phone, client_name),
            daemon=True,
            name=f"complete-{chat_id}",
        ).start()

# ---------------------------------------------------------------------------
# Flask app & routes
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # 1 MB — Telegram updates are well under this


@app.after_request
def _global_security_headers(response: Response) -> Response:
    """Apply conservative browser headers without overriding stricter blueprints."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    )
    response.headers.setdefault(
        "Strict-Transport-Security",
        "max-age=31536000",
    )
    response.headers.setdefault(
        "Content-Security-Policy-Report-Only",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; form-action 'self'",
    )
    return response


def _miniapp_json(body: Dict[str, Any], status: int = 200) -> Response:
    """JSON response with narrowly scoped CORS for the GitHub Pages Mini App."""
    response = jsonify(body)
    response.status_code = status
    origin = request.headers.get("Origin", "")
    if origin and origin == MINI_APP_ORIGIN:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response


@app.route("/miniapp/submit", methods=["POST", "OPTIONS"])
def miniapp_submit() -> Response:
    """Accept a menu-button Mini App request after Telegram initData validation."""
    origin = request.headers.get("Origin", "")
    if not MINI_APP_ORIGIN or origin != MINI_APP_ORIGIN:
        return _miniapp_json({"ok": False, "error": "Origin is not allowed"}, 403)
    if request.method == "OPTIONS":
        return _miniapp_json({"ok": True}, 204)

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _miniapp_json({"ok": False, "error": "Invalid JSON body"}, 400)
    try:
        telegram_user = validate_init_data(
            str(body.get("initData") or ""), BOT_TOKEN, max_age=3600
        )
        trusted_source_tag = str(telegram_user.pop("_source_tag", "") or "")
    except MiniAppValidationError as exc:
        logger.info("Rejected Mini App initData: %s", exc)
        return _miniapp_json({"ok": False, "error": "Telegram authorization failed"}, 401)

    try:
        info = _accept_miniapp_trip(
            int(telegram_user["id"]),
            telegram_user,
            body.get("payload"),
            trusted_source_tag=trusted_source_tag,
        )
    except MiniAppValidationError as exc:
        return _miniapp_json({"ok": False, "error": str(exc)}, 400)
    except Exception:
        logger.exception("Mini App submission failed")
        return _miniapp_json({"ok": False, "error": "Could not save the request"}, 500)
    return _miniapp_json({"ok": True, "state": info.get("state")})


@app.route("/miniapp/transfer-link", methods=["POST", "OPTIONS"])
def miniapp_transfer_link() -> Response:
    """Return a Kiwitaxi partner link for an authenticated Telegram Mini App."""
    origin = request.headers.get("Origin", "")
    if not MINI_APP_ORIGIN or origin != MINI_APP_ORIGIN:
        return _miniapp_json({"ok": False, "error": "Origin is not allowed"}, 403)
    if request.method == "OPTIONS":
        return _miniapp_json({"ok": True}, 204)

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _miniapp_json({"ok": False, "error": "Invalid JSON body"}, 400)
    try:
        validate_init_data(str(body.get("initData") or ""), BOT_TOKEN, max_age=3600)
    except MiniAppValidationError as exc:
        logger.info("Rejected Mini App transfer initData: %s", exc)
        return _miniapp_json({"ok": False, "error": "Telegram authorization failed"}, 401)

    destination = str(body.get("destination") or "").strip()
    if not destination or len(destination) > 100:
        return _miniapp_json({"ok": False, "error": "Destination is invalid"}, 400)

    fallback_url = _travelpayouts_transfer.build_kiwitaxi_url(destination)
    try:
        url = _travelpayouts_transfer.create_transfer_partner_link(destination)
    except _travelpayouts_transfer.TransferLinkNotConfigured as exc:
        logger.warning("Kiwitaxi partner link not configured: %s", exc)
        record_partner_click("transfer", destination, "direct")
        return _miniapp_json({"ok": True, "url": fallback_url, "affiliate": False})
    except _travelpayouts_transfer.TransferLinkError as exc:
        logger.warning("Kiwitaxi partner link unavailable: %s", exc)
        record_partner_click("transfer", destination, "direct")
        return _miniapp_json({"ok": True, "url": fallback_url, "affiliate": False})

    record_partner_click("transfer", destination, "api")
    return _miniapp_json({"ok": True, "url": url, "affiliate": True})


@app.route("/miniapp/partner-link", methods=["POST", "OPTIONS"])
def miniapp_partner_link() -> Response:
    """Resolve Telegram Mini App partner links on the trusted backend."""
    origin = request.headers.get("Origin", "")
    if not MINI_APP_ORIGIN or origin != MINI_APP_ORIGIN:
        return _miniapp_json({"ok": False, "error": "Origin is not allowed"}, 403)
    if request.method == "OPTIONS":
        return _miniapp_json({"ok": True}, 204)

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _miniapp_json({"ok": False, "error": "Invalid JSON body"}, 400)
    try:
        validate_init_data(str(body.get("initData") or ""), BOT_TOKEN, max_age=3600)
    except MiniAppValidationError as exc:
        logger.info("Rejected Mini App partner initData: %s", exc)
        return _miniapp_json({"ok": False, "error": "Telegram authorization failed"}, 401)

    service = str(body.get("service") or "").strip().lower()
    if service not in {"hotel", "esim", "transfer"}:
        return _miniapp_json({"ok": False, "error": "Partner service is invalid"}, 400)

    destination = str(body.get("destination") or "").strip()
    if not destination or len(destination) > 100:
        return _miniapp_json({"ok": False, "error": "Destination is invalid"}, 400)

    if service == "hotel":
        try:
            nights = int(body.get("nights") or 0)
            adults = int(body.get("adults") or 2)
        except (TypeError, ValueError):
            return _miniapp_json({"ok": False, "error": "Trip parameters are invalid"}, 400)
        checkin = str(body.get("date") or "").strip()
        url, mode = _travelpayouts_links.resolve_hotel_link(
            destination,
            checkin=checkin,
            nights=nights,
            adults=adults,
        )
    elif service == "esim":
        url, mode = _travelpayouts_links.resolve_esim_link(destination)
    else:
        url = _travelpayouts_transfer.build_kiwitaxi_url(destination)
        mode = "direct"
        try:
            url = _travelpayouts_transfer.create_transfer_partner_link(destination)
            mode = "api"
        except _travelpayouts_transfer.TransferLinkNotConfigured as exc:
            logger.warning("Kiwitaxi partner link not configured: %s", exc)
        except _travelpayouts_transfer.TransferLinkError as exc:
            logger.warning("Kiwitaxi partner link unavailable: %s", exc)

    record_partner_click(service, destination, mode)
    logger.info("Mini App partner link resolved service=%s mode=%s", service, mode)
    return _miniapp_json({
        "ok": True,
        "service": service,
        "url": url,
        "affiliate": mode != "direct",
        "mode": mode,
    })


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
        # Отвечает на «я задеплоил, а изменений нет» без ssh.
        "revision": _version.REVISION,
        "uptime_seconds": _version.uptime_seconds(),
        "seconds_since_poll_ok": poll_age,
        "poll_stale_after": POLL_STALE_AFTER if BOT_MODE == "polling" else None,
        "seconds_since_update": (
            round(now - _last_update_at, 1) if _last_update_at else None
        ),
        "bot_mode": BOT_MODE,
        "mdt_retry": _mdt_retry_health(now),
        "lead_delivery": _lead_delivery_health(now),
        "ops_events": _ops_event_health(now),
        "ai_runtime": _ai_runtime_health(now),
        "acquisition_funnel": _funnel_health(now),
        "travelpayouts_stats": _travelpayouts_stats.health_snapshot(now=now),
        "ai_selection": {
            "mode": AI_MODE,
            "ready": selection_ai_provider.ready,
            "model": selection_ai_provider.model or None,
            "lead_assist_enabled": AI_LEAD_ASSIST_ENABLED,
        },
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

    # Post-lead AI shortcuts work without an active FSM session. The callback
    # payload contains only a short key; the model receives the fixed question
    # plus the same minimized saved-trip context as /ask.
    if cb_data.startswith(CB_AI_LEAD_PREFIX):
        question_key = cb_data[len(CB_AI_LEAD_PREFIX):]
        question = AI_LEAD_QUICK_QUESTIONS.get(question_key)
        if not question:
            send_message(chat_id, "Эта кнопка устарела. Напишите /ask ваш вопрос.")
            return
        _handle_lead_assist(chat_id, question, entrypoint="quick")
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

    if cb_data.startswith(CB_REVIEW_PREFIX):
        _process_review(chat_id, cb_data, synthetic, info)
        _mark_dirty(chat_id, user=False)
        return

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
        if info.get("state") not in (STATE_KIDS, STATE_KIDS_AGES, STATE_INFANTS):
            send_message(chat_id, "Сейчас это действие недоступно. Продолжите текущий шаг.")
            return
        _step_kids_ages(chat_id, cb_data, synthetic, info)
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



def _accept_miniapp_trip(
    chat_id: int,
    from_info: Dict[str, Any],
    payload: Any,
    *,
    trusted_source_tag: str = "",
) -> Dict[str, Any]:
    """Validate a Mini App request and continue at the existing contact step."""
    info = validate_trip_request(payload)
    with _lock:
        previous_source = str((user_data.get(chat_id) or {}).get("source_tag") or "")
    source_tag = normalise_source_tag(previous_source or trusted_source_tag)
    if source_tag:
        info["source_tag"] = source_tag
    first_name = str(from_info.get("first_name") or "").strip()
    username = str(from_info.get("username") or "").strip()
    _touch_user(chat_id, first_name, username)

    # The form has a required consent checkbox. Keep the same consent marker
    # as the conversational funnel and retain its review/send gate.
    set_consent(chat_id)
    info["state"] = STATE_CONTACT
    info["updated_at"] = int(time.time())
    _, info["kids"], info["infants"] = party_bands(info)
    with _lock:
        user_data[chat_id] = info
    _mark_dirty(chat_id, user=False)
    save_state()

    direct_note = (
        "\n✈️ Перелёт: только прямой, если доступен."
        if info.get("direct_only") else ""
    )
    send_message(
        chat_id,
        "✅ Параметры поездки получены из Mini App.\n"
        "Теперь выберите способ связи — после этого покажу заявку для проверки."
        + direct_note,
    )
    _ask_contact(chat_id)
    return info

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

    # Reply-keyboard Mini Apps may return web_app_data directly.
    web_app_data = message.get("web_app_data") or {}
    raw_web_app_data = web_app_data.get("data")
    if raw_web_app_data is not None:
        try:
            if not isinstance(raw_web_app_data, str) or len(raw_web_app_data) > 8192:
                raise MiniAppValidationError("Mini App payload is invalid")
            payload = json.loads(raw_web_app_data)
            _accept_miniapp_trip(chat_id, from_info, payload)
        except (json.JSONDecodeError, MiniAppValidationError) as exc:
            logger.info("Rejected Telegram web_app_data for %s: %s", _log_correlation(chat_id, namespace="tg-user"), exc)
            send_message(chat_id, "Не удалось проверить данные Mini App. Откройте форму ещё раз.")
        return

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

    # Telegram deep links arrive as "/start <payload>" (or /start@bot <payload>).
    # Accept only bounded campaign tags; malformed payloads remain unknown commands.
    start_match = re.fullmatch(
        r"/start(?:@[A-Za-z0-9_]+)?(?:\s+([A-Za-z0-9_-]{1,64}))?",
        text.strip(),
    )
    if start_match:
        handle_start(
            chat_id,
            first_name,
            source_tag=normalise_source_tag(start_match.group(1) or ""),
        )
        return

    # Normalise ordinary /cmd@botname → /cmd.
    if text.startswith("/"):
        text = text.split("@", 1)[0]

    # Closed beta command is checked before normal/admin routing. Unauthorized
    # users get the ordinary unknown-command behavior below, so no beta surface
    # is advertised publicly.
    if (text == "/ai" or text.startswith("/ai ")) and _handle_ai_beta_command(chat_id, text):
        return

    # Explicit customer lead-assist surface. It does not hijack ordinary
    # manager/client messages: only /ask or an "ИИ:" prefix invokes the model.
    if text == "/ask" or text.startswith("/ask "):
        _handle_lead_assist(chat_id, text[4:].strip(), entrypoint="ask")
        return
    if text.casefold().startswith("ии:"):
        _handle_lead_assist(
            chat_id,
            text.split(":", 1)[1].strip(),
            entrypoint="prefix",
        )
        return

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

    if text in ("/cancel", CANCEL_BUTTON_TEXT, "❌ Отмена", "❌ Отменить"):
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
        # Ordinary post-lead messages remain human-owned. External AI is only
        # invoked through the explicit /ask or "ИИ:" surfaces above.
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
            d["kids_ages"] = _ages_from_db(d.get("kids_ages"))
            if d.get("direct_only") is not None:
                d["direct_only"] = bool(d["direct_only"])
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
_start_mdt_retry_worker()

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
