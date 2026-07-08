"""TurBot VK — Telegram-бот для турагентства «АПРЕЛЬ тур», версия для VK.com.

Самодостаточный Flask-webhook для группы ВКонтакте. Делит ту же логику диалога,
AI-подборки, согласие 152-ФЗ и интеграцию с MDT CRM с Telegram-версией (bot.py),
но использует VK Callback API и собственную SQLite-БД (DATABASE_PATH/VK_DATABASE_PATH).

Деплой: отдельный процесс на той же VM (см. deploy/vk-turbot.service).
"""
from __future__ import annotations

import os
import re
import json
import time
import random
import sqlite3
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

try:
    from groq import Groq
except ImportError:  # groq may not be installed in all environments
    Groq = None  # type: ignore

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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POPULAR_DESTINATIONS = ["Египет", "Турция", "Таиланд", "Мальдивы", "ОАЭ", "Другое"]
PEOPLE_OPTIONS = ["1", "2", "3", "4", "5+"]

BACK_BUTTON_TEXT   = "◀️ Назад"
CANCEL_BUTTON_TEXT = "❌ Отменить"
CONSENT_YES_TEXT   = "✅ Согласен"
CONSENT_NO_TEXT    = "❌ Отказаться"

USER_HELP = (
    "🤖 Я бот туристического агентства «АПРЕЛЬ тур».\n\n"
    "Помогу подобрать тур по вашим пожеланиям и передам заявку менеджеру.\n\n"
    "Команды:\n"
    "  Начать — начать подбор тура\n"
    "  Отмена — отменить текущую заявку\n"
    "  Политика — политика обработки персональных данных\n"
    "  Удалить — удалить мои данные и отозвать согласие\n"
    "  Помощь — эта справка\n\n"
    "📋 ИП Замятина Мария Андреевна\n"
    "ТА «АПРЕЛЬ тур» · ОГРНИП 290211659807"
)

# Dialog states
STATE_CONSENT     = "consent"
STATE_DESTINATION = "destination"
STATE_DATES       = "dates"
STATE_PEOPLE      = "people"
STATE_BUDGET      = "budget"
STATE_PHONE       = "phone"

TEMPLATE_INTROS: Dict[str, str] = {
    "египет": "Вас ждут древние пирамиды, кристально чистое Красное море и «всё включённое» на любой вкус.",
    "турция": "Отличное сочетание пляжного отдыха, богатой истории и гостеприимной кухни с all inclusive на побережье.",
    "таиланд": "Экзотическая природа, белоснежные пляжи, буддийские храмы и доступный комфортный отдых для всей семьи.",
    "мальдивы": "Райские острова с бирюзовой лагуной, bungalow над водой и идеальной атмосферой для романтического getaway.",
    "оаэ": "Современный комфорт, роскошные отели, шопинг и пляжи с тёплым морем — и всё это без визового оформления.",
}

TEMPLATE_PACKING = (
    "Возьмите с собой удобную обувь, купальные принадлежности, солнцезащитный крем "
    "и хорошее настроение."
)

AI_FALLBACK_MESSAGE = (
    "🌴 Спасибо за заявку! Наш менеджер подберёт для вас\n"
    "лучшие варианты туров и свяжется с вами в ближайшее время."
)

_MONTHS_RU: Dict[str, int] = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4,
    "ма": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

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
                dates TEXT,
                people TEXT,
                budget INTEGER,
                phone TEXT,
                updated_at INTEGER NOT NULL
            )
        """)
        cur.execute("PRAGMA journal_mode=WAL")


# --- session helpers ---

def set_session(chat_id: int, data: Dict[str, Any]) -> None:
    now = int(time.time())
    with _db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO sessions (chat_id, state, destination, dates, people, budget, phone, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                state=excluded.state, destination=excluded.destination,
                dates=excluded.dates, people=excluded.people,
                budget=excluded.budget, phone=excluded.phone,
                updated_at=excluded.updated_at
        """, (chat_id, data.get("state", ""), data.get("destination"),
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


def _dest_keyboard() -> str:
    rows = [[_btn(d, "primary")] for d in POPULAR_DESTINATIONS]
    rows.append([_btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _nav_keyboard(extra_top: Optional[List[Dict]] = None) -> str:
    rows: List[List[Dict[str, Any]]] = []
    if extra_top:
        rows.append(extra_top)
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary")])
    rows.append([_btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _people_keyboard() -> str:
    rows = [[_btn(p, "primary") for p in PEOPLE_OPTIONS]]
    rows.append([_btn(BACK_BUTTON_TEXT, "secondary")])
    rows.append([_btn(CANCEL_BUTTON_TEXT, "negative")])
    return _keyboard(rows)


def _consent_keyboard() -> str:
    return _keyboard([[_btn(CONSENT_YES_TEXT, "positive")], [_btn(CONSENT_NO_TEXT, "negative")]])


def _hide_keyboard() -> str:
    return _keyboard([], one_time=True)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def validate_phone(text: str) -> Tuple[bool, Optional[str]]:
    digits = re.sub(r"[^\d+]", "", text).lstrip("+")
    if digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return True, "+" + digits
    if len(digits) == 10:
        return True, "+7" + digits
    return False, None


def validate_people(text: str) -> Tuple[bool, Optional[str]]:
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
    try:
        value = int(re.sub(r"[^\d]", "", text))
        if value > 0:
            return True, value
    except (ValueError, TypeError):
        pass
    return False, None


# ---------------------------------------------------------------------------
# AI selection
# ---------------------------------------------------------------------------

def _template_selection(destination: str, dates: str, people: str, budget: str) -> str:
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
    if AI_MODE == "template" or not groq_client:
        return _template_selection(destination, dates, people, budget)
    try:
        prompt = (
            "Ты — эксперт по туризму туристического агентства «АПРЕЛЬ тур».\n\n"
            "Клиент хочет:\n"
            f"- Направление: {destination}\n- Даты: {dates}\n"
            f"- Количество человек: {people}\n- Бюджет: {budget} рублей\n\n"
            "Напиши короткое (3-4 предложения), дружелюбное сообщение с:\n"
            "- Что ожидает в этом направлении\n- Почему это отличный выбор\n"
            "- Что взять с собой\n\nИспользуй эмодзи. Не упоминай цены и конкретные отели."
        )
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL, messages=[{"role": "user", "content": prompt}],
            max_tokens=300, temperature=0.7,
        )
        ai_text = response.choices[0].message.content
        return f"🌴 Ваша подборка туров\n\n{ai_text}\n\nℹ️ Наш менеджер скоро свяжется с вами!"
    except Exception as exc:
        logger.error("Error generating AI selection: %s", exc)
        return _template_selection(destination, dates, people, budget)


# ---------------------------------------------------------------------------
# MDT CRM integration
# ---------------------------------------------------------------------------

def _mdt_base_url() -> str:
    if MDT_BASE_URL:
        return MDT_BASE_URL.rstrip("/")
    if MDT_ACCOUNT:
        return f"https://{MDT_ACCOUNT}.moidokumenti.ru"
    return ""


def _mdt_request(method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    base = _mdt_base_url()
    if not base or not MDT_API_KEY:
        return None
    try:
        resp = http_session.post(
            f"{base}/api/{method}",
            data={"params": json.dumps(params, ensure_ascii=False), "key": MDT_API_KEY},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("MDT request %s failed: %s", method, exc)
        return None


_mdt_country_cache: Dict[str, int] = {}


def _mdt_load_countries() -> None:
    result = _mdt_request("get-country-list", {})
    if result is None:
        return
    data = result.get("data", result) if isinstance(result, dict) else result
    if isinstance(data, dict):
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


def _match_country_id(destination: str) -> int:
    if not _mdt_country_cache:
        return 0
    dest_lower = destination.strip().lower()
    if not dest_lower:
        return 0
    if dest_lower in _mdt_country_cache:
        return _mdt_country_cache[dest_lower]
    for name, cid in _mdt_country_cache.items():
        if name in dest_lower or dest_lower in name:
            return cid
    return 0


def _parse_russian_dates(text: str) -> Tuple[Optional[str], Optional[str]]:
    if not text:
        return None, None
    now = time.localtime()
    current_year, current_month, current_day = now.tm_year, now.tm_mon, now.tm_mday

    def _month_from_text(s: str) -> Optional[int]:
        for prefix, month_num in _MONTHS_RU.items():
            if prefix in s.lower():
                return month_num
        return None

    def _to_ymd(day: int, month: int, year: int) -> str:
        return f"{year:04d}-{month:02d}-{day:02d}"

    year_match = re.search(r"\b(20\d{2})\b", text)
    year = int(year_match.group(1)) if year_match else current_year
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
    if from_parsed is None and to_parsed is not None:
        day_match = re.search(r"\b(\d{1,2})\b", parts[0])
        if day_match:
            from_parsed = (int(day_match.group(1)), to_parsed[1])
    if from_parsed is None or to_parsed is None:
        return None, None

    from_day, from_month = from_parsed
    to_day, to_month = to_parsed
    if not year_match:
        if from_month < current_month or (from_month == current_month and from_day < current_day):
            year = current_year + 1
    from_date = _to_ymd(from_day, from_month, year)
    to_date = _to_ymd(to_day, to_month, year)
    if to_month < from_month:
        to_date = _to_ymd(to_day, to_month, year + 1)
    return from_date, to_date


def _mdt_add_tourist_temp(name: str, phone: str) -> Optional[int]:
    result = _mdt_request("add-tourist-temp", {"name": name, "tel": phone, "tags": "VK Bot"})
    if result is None:
        return None
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
    return None


def send_preorder_to_mdt(chat_id, info, phone, client_name) -> Tuple[Optional[int], Optional[int]]:
    name = client_name or f"VK {chat_id}"
    tourist_id = _mdt_add_tourist_temp(name, phone)
    if tourist_id is None:
        return None, None
    country_id = _match_country_id(info.get("destination", ""))
    date_from, date_to = _parse_russian_dates(info.get("dates", ""))
    people_str = str(info.get("people", ""))
    persons = int(re.sub(r"[^\d]", "", people_str)) if re.sub(r"[^\d]", "", people_str) else 0
    budget = info.get("budget", 0)
    if not isinstance(budget, (int, float)):
        try:
            budget = int(re.sub(r"[^\d]", "", str(budget)))
        except (ValueError, TypeError):
            budget = 0
    comment_parts = []
    if info.get("destination"):
        comment_parts.append(f"Направление: {info['destination']}")
    if info.get("dates"):
        comment_parts.append(f"Даты: {info['dates']}")
    if info.get("people"):
        comment_parts.append(f"Человек: {info['people']}")
    if budget:
        comment_parts.append(f"Бюджет: {budget}₽")
    params: Dict[str, Any] = {
        "tourist_type": "tourist_temp", "tourist_id": tourist_id,
        "country_id1": country_id, "country_id2": 0, "country_id3": 0,
        "persons": persons, "children": 0, "children_ages": [],
        "price_from": 0, "price_to": budget,
        "comment": " | ".join(comment_parts), "wait_for_hot": 0,
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
        return preorder_id, tourist_id
    return None, None


def _mdt_notify_managers(chat_id, info, phone, client_name) -> None:
    if not MDT_MANAGER_IDS:
        return
    text_parts = []
    if client_name:
        text_parts.append(f"Клиент: {client_name}")
    for k, label in [("destination", "Направление"), ("dates", "Даты"), ("people", "Человек"), ("budget", "Бюджет")]:
        if info.get(k):
            text_parts.append(f"{label}: {info[k]}{'₽' if k == 'budget' else ''}")
    text_parts.append(f"Телефон: {phone}")
    _mdt_request("send-push", {
        "manager_ids": MDT_MANAGER_IDS,
        "title": "Новая заявка с VK-бота",
        "text": "\n".join(text_parts),
    })


def _mdt_add_reminder(preorder_id, tourist_id, manager_id, reminder_date, reminder_time="10:00:00") -> bool:
    result = _mdt_request("add-reminder", {
        "date": reminder_date, "time": reminder_time,
        "text": MDT_REMINDER_TEXT, "tourist_type": "tourist_temp",
        "tourist_id": tourist_id, "manager_id": manager_id,
        "preorder_id": preorder_id, "only_one_manager": False,
    })
    return result is not None


def _mdt_create_reminders_for_preorder(chat_id, preorder_id, tourist_id) -> None:
    if not MDT_REMINDER_ENABLED or not (preorder_id and tourist_id) or not MDT_MANAGER_IDS:
        return
    reminder_date = (datetime.now() + timedelta(days=MDT_REMINDER_DAYS)).strftime("%Y-%m-%d")
    for manager_id in MDT_MANAGER_IDS:
        _mdt_add_reminder(preorder_id, tourist_id, manager_id, reminder_date)


def _mdt_create_lead(chat_id, info, phone, client_name) -> bool:
    fields = []
    for k, label in [("destination", "Направление"), ("dates", "Даты"),
                     ("people", "Количество человек"), ("budget", "Бюджет")]:
        if info.get(k):
            fields.append({"name": label, "values": [str(info[k])]})
    result = _mdt_request("add-lead", {
        "name": client_name or f"VK {chat_id}",
        "phone": phone, "email": "", "source": MDT_SOURCE, "fields": fields,
    })
    return result is not None


def send_lead_to_mdt(chat_id, info, phone, client_name) -> None:
    if not MDT_ENABLED:
        return
    success = False
    if MDT_MODE in ("lead", "both"):
        success = _mdt_create_lead(chat_id, info, phone, client_name) or success
    preorder_id = tourist_id = None
    if MDT_MODE in ("preorder", "both"):
        preorder_id, tourist_id = send_preorder_to_mdt(chat_id, info, phone, client_name)
        success = (preorder_id is not None) or success
        _mdt_create_reminders_for_preorder(chat_id, preorder_id, tourist_id)
    if success and MDT_NOTIFY_MANAGERS:
        _mdt_notify_managers(chat_id, info, phone, client_name)


# ---------------------------------------------------------------------------
# Consent & privacy text
# ---------------------------------------------------------------------------

def _consent_text() -> str:
    policy_line = f"\n📄 Полный текст: {PRIVACY_POLICY_URL}\n" if PRIVACY_POLICY_URL else "\n"
    return (
        "🔒 Перед подбором тура нужно ваше согласие на обработку персональных данных.\n\n"
        f"Оператор: {DATA_OPERATOR_NAME}.\n\n"
        "Нажимая «✅ Согласен», вы даёте согласие на обработку ваших имени и номера телефона "
        "с целью подбора тура и связи с вами (ст. 6, 9 ФЗ-152)."
        + policy_line +
        "Вы вправе отозвать согласие и удалить данные — напишите «Удалить»."
    )


def _privacy_text() -> str:
    lines = [
        "🔒 Обработка персональных данных\n",
        f"Оператор: {DATA_OPERATOR_NAME}.",
        "Цель: подбор тура и связь с клиентом.",
        "Обрабатываемые данные: имя, номер телефона, идентификатор VK.",
    ]
    if DATA_RETENTION_DAYS > 0:
        lines.append(f"Срок хранения: до {DATA_RETENTION_DAYS} дней после обращения, затем автоматическое удаление.")
    lines.append("Права: отозвать согласие и удалить данные — напишите «Удалить».")
    if PRIVACY_POLICY_URL:
        lines.append(f"\n📄 Полный текст: {PRIVACY_POLICY_URL}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dialog handlers
# ---------------------------------------------------------------------------

def handle_start(user_id: int, first_name: str = "") -> None:
    if not has_consent(user_id):
        with _lock:
            user_data[user_id] = {"state": STATE_CONSENT, "updated_at": int(time.time())}
        _mark_dirty(user_id)
        send_message(user_id, _consent_text(), keyboard=_consent_keyboard())
        return
    _begin_destination(user_id, first_name)


def _begin_destination(user_id: int, first_name: str = "") -> None:
    with _lock:
        user_data[user_id] = {"state": STATE_DESTINATION, "updated_at": int(time.time())}
    _mark_dirty(user_id)
    name = f", {first_name}" if first_name else ""
    send_message(
        user_id,
        f"🌴 Здравствуйте{name}! Я помогу подобрать тур под ваши пожелания.\n\n"
        "📍 Куда бы вы хотели отправиться?\n\n"
        "Выберите из популярных направлений или напишите своё:",
        keyboard=_dest_keyboard(),
    )


def handle_cancel(user_id: int) -> None:
    with _lock:
        existed = user_data.pop(user_id, None) is not None
    if existed:
        _mark_dirty(user_id)
        delete_session(user_id)
        send_message(user_id, "❌ Заявка отменена. Чтобы начать заново — напишите «Начать».",
                      keyboard=_hide_keyboard())
    else:
        send_message(user_id, "Нет активной заявки. Напишите «Начать», чтобы начать.")


def _step_consent(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    if text == CONSENT_YES_TEXT:
        set_consent(user_id)
        first_name = message.get("_user_name", "")
        _begin_destination(user_id, first_name)
        return
    if text == CONSENT_NO_TEXT:
        with _lock:
            user_data.pop(user_id, None)
        _mark_dirty(user_id, user=False)
        delete_session(user_id)
        send_message(
            user_id,
            "Без согласия на обработку персональных данных мы, к сожалению, не сможем подобрать тур.\n\n"
            "Если передумаете — напишите «Начать».",
            keyboard=_hide_keyboard(),
        )
        return
    send_message(user_id, "Пожалуйста, нажмите «✅ Согласен» или «❌ Отказаться».",
                 keyboard=_consent_keyboard())


def _step_destination(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    dest = text.strip()
    if dest.lower() == "другое":
        send_message(user_id, "✍️ Напишите ваше направление:", keyboard=_nav_keyboard())
        return
    info["destination"] = dest
    info["state"] = STATE_DATES
    send_message(user_id, "📅 На какие даты планируете поездку? (например: 15-22 июня)",
                 keyboard=_nav_keyboard())


def _step_dates(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    info["dates"] = text
    info["state"] = STATE_PEOPLE
    send_message(user_id, "👥 Сколько человек будет путешествовать?", keyboard=_people_keyboard())


def _step_people(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    ok, value = validate_people(text)
    if not ok:
        send_message(user_id, "Пожалуйста, укажите число от 1 до 50 (или «5+»).",
                     keyboard=_people_keyboard())
        return
    info["people"] = value
    info["state"] = STATE_BUDGET
    send_message(user_id, "💰 Какой бюджет рассматриваете на человека? (в рублях)",
                 keyboard=_nav_keyboard())


def _step_budget(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    ok, value = validate_budget(text)
    if not ok:
        send_message(user_id, "Пожалуйста, укажите бюджет числом (например: 60000).",
                     keyboard=_nav_keyboard())
        return
    info["budget"] = value
    info["state"] = STATE_PHONE
    send_message(user_id, "📱 Укажите ваш номер телефона для связи:", keyboard=_nav_keyboard())


def _step_phone(user_id: int, text: str, message: Dict[str, Any], info: Dict[str, Any]) -> None:
    ok, phone = validate_phone(text)
    if not ok:
        send_message(user_id, "Похоже, номер некорректен. Попробуйте в формате +7XXXXXXXXXX.",
                     keyboard=_nav_keyboard())
        return
    handle_completion(user_id, phone, message)


STATE_HANDLERS: Dict[str, Callable] = {
    STATE_CONSENT:     _step_consent,
    STATE_DESTINATION: _step_destination,
    STATE_DATES:       _step_dates,
    STATE_PEOPLE:      _step_people,
    STATE_BUDGET:      _step_budget,
    STATE_PHONE:       _step_phone,
}

PREVIOUS_STATE: Dict[str, str] = {
    STATE_DATES:  STATE_DESTINATION,
    STATE_PEOPLE: STATE_DATES,
    STATE_BUDGET: STATE_PEOPLE,
    STATE_PHONE:  STATE_BUDGET,
}


def _prompt_for_state(user_id: int, state: str) -> None:
    prompts = {
        STATE_DESTINATION: ("📍 Куда бы вы хотели отправиться?", _dest_keyboard()),
        STATE_DATES: ("📅 На какие даты планируете поездку? (например: 15-22 июня)", _nav_keyboard()),
        STATE_PEOPLE: ("👥 Сколько человек будет путешествовать?", _people_keyboard()),
        STATE_BUDGET: ("💰 Какой бюджет рассматриваете на человека? (в рублях)", _nav_keyboard()),
        STATE_PHONE: ("📱 Укажите ваш номер телефона для связи:", _nav_keyboard()),
    }
    text, kb = prompts.get(state, ("Продолжите ввод:", _nav_keyboard()))
    send_message(user_id, text, keyboard=kb)


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
        send_message(user_id, "Для начала работы напишите «Начать»")
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
        "✅ Ваша заявка принята! Наш менеджер свяжется с вами в ближайшее время.\n\n"
        f"📍 Направление: {info.get('destination', '?')}\n"
        f"📅 Даты: {info.get('dates', '?')}\n"
        f"👥 Человек: {info.get('people', '?')}\n"
        f"💰 Бюджет: {info.get('budget', '?')}₽\n"
        f"📱 Телефон: {phone}\n\n"
        "Спасибо за обращение в «АПРЕЛЬ тур»! 🌺\n\n"
        "📋 ИП Замятина Мария Андреевна\nОГРНИП 290211659807",
        keyboard=_hide_keyboard(),
    )


def _notify_admin(user_id: int, info: Dict[str, Any], phone: str, client_name: Optional[str]) -> None:
    if not ADMIN_ID:
        return
    send_message(
        ADMIN_ID,
        "🔔 Новая заявка (VK)!\n\n"
        f"От: {client_name or 'без имени'} (ID: {user_id})\n"
        f"📍 {info.get('destination', '?')}\n"
        f"📅 {info.get('dates', '?')}\n"
        f"👥 {info.get('people', '?')} чел\n"
        f"💰 {info.get('budget', '?')}₽\n"
        f"📱 {phone}",
    )


def handle_completion(user_id: int, phone: str, message: Dict[str, Any]) -> None:
    info = dict(user_data.get(user_id, {}))
    client_name = message.get("_user_name") or f"VK {user_id}"

    _confirm_to_user(user_id, info, phone)
    _notify_admin(user_id, info, phone, client_name)
    send_lead_to_mdt(user_id, info, phone, client_name)

    send_typing(user_id)
    ai = generate_ai_selection(
        info.get("destination", ""), info.get("dates", ""),
        info.get("people", ""), info.get("budget", ""),
    )
    send_message(user_id, ai)

    with _lock:
        user_data.pop(user_id, None)
    delete_session(user_id)


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
            send_message(user_id, "Для начала работы напишите «Начать»")
        return

    # Button-text matching (exact match against known buttons)
    if text == BACK_BUTTON_TEXT:
        if user_id in user_data:
            _go_back(user_id)
        else:
            send_message(user_id, "Для начала работы напишите «Начать»")
        return
    if text == CANCEL_BUTTON_TEXT:
        handle_cancel(user_id)
        return

    # Dialog flow
    if user_id in user_data:
        handle_dialog(user_id, text, msg)
    else:
        send_message(user_id, "Для начала работы напишите «Начать»")


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
        "groq_configured": bool(GROQ_API_KEY),
        "ai_mode": AI_MODE,
        "mdt_enabled": MDT_ENABLED,
        "mdt_mode": MDT_MODE,
        "total_users": len(all_users),
        "active_sessions": len(user_data),
        "privacy_policy_configured": bool(PRIVACY_POLICY_URL),
        "data_retention_days": DATA_RETENTION_DAYS,
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
        if received_secret != VK_SECRET_KEY:
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

if MDT_ENABLED:
    _mdt_load_countries()

logger.info(
    "TurBot VK started (port=%s, group=%s, admin_set=%s, groq_set=%s)",
    PORT, VK_GROUP_ID, bool(ADMIN_ID), bool(GROQ_API_KEY),
)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
