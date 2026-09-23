"""Production WSGI wrapper that adds the public Aprel Tour website lead API.

The Telegram bot remains the underlying Flask application.  Website leads are
stored locally before any network side effect, then delivered to MDT CRM with a
durable retry queue.  The public endpoint intentionally has no shared secret:
the website is static, so a secret embedded in JavaScript would not be secret.
Instead it uses a strict Origin allow-list, a honeypot, rate limiting,
idempotency, validation, and the same protected MDT credentials as the bot.
"""

from __future__ import annotations

import csv
import html
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Response, jsonify, request

import bot as _bot
from shared import mdt as mdt_shared
from shared import travel_crm_adapter as _travel_crm_adapter
from shared import travel_crm_store as _travel_crm_store
from shared.telegram_webapp import MiniAppValidationError, validate_init_data
from shared.runtime_metrics import lead_delivery_snapshot
from shared.travel_crm import (
    Activity,
    ActivityType,
    BookingOutcome,
    ManagerTask,
    OutcomeStatus,
    Quote,
    QuoteReaction,
    QuoteReactionEvent,
    TaskStatus,
    TaskType,
    timeline_to_dict,
)

app = _bot.app
from shared.manager_web import manager_web

if "manager_web" not in app.blueprints:
    app.register_blueprint(manager_web)
logger = logging.getLogger("turbot.website")

_WEBSITE_SOURCE = os.getenv("WEBSITE_MDT_SOURCE", "Website Aprel Tour").strip() or "Website Aprel Tour"
_ALLOWED_ORIGINS = {
    value.strip().rstrip("/")
    for value in os.getenv(
        "WEBSITE_ALLOWED_ORIGINS",
        "https://r0meo1.ru,https://www.r0meo1.ru",
    ).split(",")
    if value.strip()
}
_RATE_LIMIT = max(1, int(os.getenv("WEBSITE_LEAD_RATE_LIMIT", "5")))
_RATE_WINDOW_SECONDS = max(60, int(os.getenv("WEBSITE_LEAD_RATE_WINDOW_SECONDS", "600")))
_WORKER_ENABLED = os.getenv("WEBSITE_LEAD_WORKER_ENABLED", "true").lower().strip() in {
    "1", "true", "yes", "on"
}
_AGENT_EXTENSION_TOKEN = _bot.agent_extension_token()
_MANAGER_TELEGRAM_IDS = set(
    _bot._parse_chat_ids(
        os.getenv("MANAGER_TELEGRAM_IDS", ""),
        env_name="MANAGER_TELEGRAM_IDS",
    )
)
if _bot.ADMIN_ID:
    _MANAGER_TELEGRAM_IDS.add(_bot.ADMIN_ID)

_rate_lock = threading.Lock()
_rate_hits: Dict[str, List[float]] = {}
_worker_lock = threading.Lock()
_worker_started = False


def _init_schema() -> None:
    with _bot._db_cursor(commit=True) as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS website_leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                phone TEXT NOT NULL,
                destination TEXT NOT NULL,
                origin TEXT NOT NULL,
                dates TEXT NOT NULL,
                people TEXT NOT NULL,
                budget INTEGER NOT NULL,
                consent_at INTEGER NOT NULL,
                utm_source TEXT,
                utm_medium TEXT,
                utm_campaign TEXT,
                utm_content TEXT,
                utm_term TEXT,
                created_at INTEGER NOT NULL,
                mdt_status TEXT NOT NULL DEFAULT 'pending',
                mdt_attempts INTEGER NOT NULL DEFAULT 0,
                mdt_next_retry_at INTEGER,
                mdt_synced_at INTEGER,
                mdt_payload TEXT NOT NULL,
                owner_status TEXT NOT NULL DEFAULT 'pending',
                owner_attempts INTEGER NOT NULL DEFAULT 0,
                owner_next_retry_at INTEGER,
                owner_notified_at INTEGER,
                crm_status TEXT NOT NULL DEFAULT 'new',
                crm_note TEXT NOT NULL DEFAULT '',
                crm_followup_on TEXT NOT NULL DEFAULT '',
                crm_updated_at INTEGER
            )
            """
        )
        cur.execute("PRAGMA table_info(website_leads)")
        columns = {str(row[1]) for row in cur.fetchall()}
        migrations = {
            "owner_status": "TEXT NOT NULL DEFAULT 'pending'",
            "owner_attempts": "INTEGER NOT NULL DEFAULT 0",
            "owner_next_retry_at": "INTEGER",
            "owner_notified_at": "INTEGER",
            "crm_status": "TEXT NOT NULL DEFAULT 'new'",
            "crm_note": "TEXT NOT NULL DEFAULT ''",
            "crm_followup_on": "TEXT NOT NULL DEFAULT ''",
            "crm_updated_at": "INTEGER",
        }
        for name, ddl in migrations.items():
            if name not in columns:
                cur.execute(f"ALTER TABLE website_leads ADD COLUMN {name} {ddl}")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_website_leads_mdt_retry "
            "ON website_leads(mdt_status, mdt_next_retry_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_website_leads_owner_retry "
            "ON website_leads(owner_status, owner_next_retry_at)"
        )
        _travel_crm_store.init_schema(cur)


def _funnel_source(payload: Dict[str, Any]) -> str:
    """Return bounded marketing attribution without any customer field."""
    source = str(payload.get("utm_source") or "direct").strip()
    campaign = str(payload.get("utm_campaign") or "").strip()
    return f"{source}:{campaign}" if campaign else source


def _safe_text(value: Any, max_len: int) -> str:
    text = str(value or "").strip()
    if len(text) > max_len:
        raise ValueError("too_long")
    return text



_SENSITIVE_CANDIDATE_QUERY_KEYS = {
    "token", "accesstoken", "authtoken", "auth", "authorization",
    "session", "sessionid", "key", "apikey", "secret", "clientsecret",
    "password", "passwd", "signature", "sign", "code",
    "authorizationcode", "oauthcode",
}


def _normalized_query_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _sanitize_candidate_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 1000:
        return ""
    try:
        parsed = urlsplit(raw)
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=100)
    except (TypeError, ValueError):
        return ""
    if parsed.scheme.casefold() not in {"http", "https"} or not hostname:
        return ""
    if username is not None or password is not None:
        return ""

    filtered = [
        (key, val)
        for key, val in pairs
        if _normalized_query_key(key) not in _SENSITIVE_CANDIDATE_QUERY_KEYS
    ]
    return urlunsplit((
        parsed.scheme.casefold(),
        parsed.netloc,
        parsed.path,
        urlencode(filtered, doseq=True),
        "",
    ))


def _csv_safe_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    significant = text.lstrip()
    if significant and significant[0] in "=+-@":
        return "'" + text
    return text

def _origin() -> str:
    return request.headers.get("Origin", "").strip().rstrip("/")


def _origin_allowed() -> bool:
    return _origin() in _ALLOWED_ORIGINS


def _agent_origin() -> str:
    return request.headers.get("Origin", "").strip().rstrip("/")


def _agent_extension_origin_allowed() -> bool:
    origin = _agent_origin()
    return origin.startswith(("chrome-extension://", "extension://", "edge-extension://"))


def _agent_json_response(body: Dict[str, Any], status: int = 200) -> Response:
    response = jsonify(body)
    response.status_code = status
    origin = _agent_origin()
    if _agent_extension_origin_allowed():
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, X-Telegram-Init-Data"
        )
        response.headers["Access-Control-Max-Age"] = "600"
    response.headers["Cache-Control"] = "no-store"
    return response


def _agent_csv_response(content: str, filename: str) -> Response:
    response = Response("\ufeff" + content, content_type="text/csv; charset=utf-8")
    origin = _agent_origin()
    if _agent_extension_origin_allowed():
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Expose-Headers"] = "Content-Disposition"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _agent_extension_authorized() -> bool:
    if not _AGENT_EXTENSION_TOKEN:
        return False
    raw = request.headers.get("Authorization", "").strip()
    scheme, _, value = raw.partition(" ")
    return (
        scheme.casefold() == "bearer"
        and bool(value)
        and secrets.compare_digest(value.strip(), _AGENT_EXTENSION_TOKEN)
    )


def _telegram_manager_authorization_error() -> str:
    """Validate a short-lived Telegram Mini App identity for manager CRM access."""
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        return "missing"
    try:
        user = validate_init_data(init_data, _bot.BOT_TOKEN, max_age=3600)
    except MiniAppValidationError:
        return "invalid"
    return "" if int(user["id"]) in _MANAGER_TELEGRAM_IDS else "forbidden"


def _json_response(body: Dict[str, Any], status: int = 200) -> Response:
    response = jsonify(body)
    response.status_code = status
    origin = _origin()
    if origin in _ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Max-Age"] = "600"
    response.headers["Cache-Control"] = "no-store"
    return response


def _client_key() -> str:
    # Production gunicorn is bound to 127.0.0.1 and reached through nginx, so
    # X-Forwarded-For is supplied by our reverse proxy rather than a direct
    # internet client.  Nothing here is persisted; it only backs the memory
    # rate limiter.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:64]
    return (request.remote_addr or "unknown")[:64]


def _rate_allowed(key: str) -> bool:
    now = time.time()
    cutoff = now - _RATE_WINDOW_SECONDS
    with _rate_lock:
        hits = [stamp for stamp in _rate_hits.get(key, []) if stamp >= cutoff]
        if len(hits) >= _RATE_LIMIT:
            _rate_hits[key] = hits
            return False
        hits.append(now)
        _rate_hits[key] = hits
        if len(_rate_hits) > 5000:
            stale = [k for k, values in _rate_hits.items() if not values or values[-1] < cutoff]
            for stale_key in stale[:1000]:
                _rate_hits.pop(stale_key, None)
        return True


def _validate_payload(raw: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(raw, dict):
        return None, "invalid_json"

    try:
        # Honeypot is intentionally validated before rate limiting.  A bot that
        # fills every visible/hidden field gets a harmless success response and
        # cannot spend the real-client rate budget.
        company = _safe_text(raw.get("company", ""), 120)
        if company:
            return {"_honeypot": True}, None

        if raw.get("consent") is not True:
            return None, "consent_required"

        name = _safe_text(raw.get("name"), 80)
        destination = _safe_text(raw.get("destination"), 100)
        origin = _safe_text(raw.get("origin"), 100)
        dates = _safe_text(raw.get("dates"), 120)
        people_raw = _safe_text(raw.get("people"), 16)
        budget_raw = _safe_text(raw.get("budget"), 32)
        if not all((name, destination, origin, dates, people_raw, budget_raw)):
            return None, "required_fields"

        phone_ok, phone = _bot.validate_phone(_safe_text(raw.get("phone"), 40))
        if not phone_ok or not phone:
            return None, "invalid_phone"
        people_ok, people = _bot.validate_people(people_raw)
        if not people_ok or not people:
            return None, "invalid_people"
        budget_ok, budget = _bot.validate_budget(budget_raw)
        if not budget_ok or budget is None:
            return None, "invalid_budget"

        request_id = _safe_text(raw.get("requestId") or raw.get("request_id"), 128)
        if request_id and not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", request_id):
            return None, "invalid_request_id"
        if not request_id:
            request_id = secrets.token_urlsafe(18)

        utm = {
            key: _safe_text(raw.get(key), 160)
            for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
        }
    except ValueError:
        return None, "field_too_long"

    now = int(time.time())
    return {
        "request_id": request_id,
        "name": name,
        "phone": phone,
        "destination": destination,
        "origin": origin,
        "dates": dates,
        "people": people,
        "budget": int(budget),
        "consent_at": now,
        "created_at": now,
        **utm,
    }, None


def _mdt_settings() -> mdt_shared.MDTSettings:
    return mdt_shared.MDTSettings(
        enabled=_bot.MDT_ENABLED,
        account=_bot.MDT_ACCOUNT,
        api_key=_bot.MDT_API_KEY,
        source=_WEBSITE_SOURCE,
        base_url=_bot.MDT_BASE_URL,
        mode="lead",
        notify_managers=False,
        timeout=_bot.HTTP_TIMEOUT,
        name_prefix="Website",
        tourist_tags="Website",
        push_title="Новая заявка с сайта",
    )


def _retry_delay(attempts: int) -> int:
    exponent = max(0, min(int(attempts) - 1, 16))
    return min(
        _bot.MDT_RETRY_MAX_SECONDS,
        _bot.MDT_RETRY_BASE_SECONDS * (2 ** exponent),
    )


def _deliver_lead(lead_id: int) -> bool:
    if _bot.DEMO_MODE or not _bot.MDT_ENABLED:
        return False

    with _bot._db_cursor() as cur:
        cur.execute(
            "SELECT mdt_status, mdt_attempts, mdt_payload FROM website_leads WHERE id=?",
            (int(lead_id),),
        )
        row = cur.fetchone()
    if not row:
        return False
    if row[0] == "synced":
        return True

    try:
        payload = json.loads(row[2])
        info = {
            "destination": payload["destination"],
            "origin": payload["origin"],
            "dates": payload["dates"],
            "people": payload["people"],
            "budget": payload["budget"],
            "budget_scope": "total",
            "_local_lead_id": int(lead_id),
            "_mdt_delivery_key": f"web-lead-{int(lead_id)}",
        }
        success = mdt_shared.create_lead(
            _mdt_settings(),
            int(lead_id),
            info,
            str(payload["phone"]),
            str(payload["name"]),
            request_fn=_bot._mdt_request,
            log=logger,
        )
    except Exception as exc:
        logger.warning("Website MDT delivery failed for lead %s: %s", lead_id, type(exc).__name__)
        success = False

    now = int(time.time())
    attempts = int(row[1] or 0) + 1
    with _bot._db_cursor(commit=True) as cur:
        if success:
            cur.execute(
                """
                UPDATE website_leads
                SET mdt_status='synced', mdt_attempts=?, mdt_next_retry_at=NULL,
                    mdt_synced_at=?
                WHERE id=?
                """,
                (attempts, now, int(lead_id)),
            )
        else:
            cur.execute(
                """
                UPDATE website_leads
                SET mdt_status='pending', mdt_attempts=?, mdt_next_retry_at=?
                WHERE id=?
                """,
                (attempts, now + _retry_delay(attempts), int(lead_id)),
            )
    return success


def _owner_notification_text(lead_id: int, payload: Dict[str, Any]) -> str:
    utm = "/".join(
        value for value in (
            payload.get("utm_source", ""),
            payload.get("utm_medium", ""),
            payload.get("utm_campaign", ""),
        ) if value
    )
    candidates = payload.get("agent_candidates") or []
    candidate_lines = []
    for index, candidate in enumerate(candidates[:10], 1):
        if not isinstance(candidate, dict):
            continue
        title = str(candidate.get("title") or "Тур").strip()
        url = str(candidate.get("url") or "").strip()
        selection = str(candidate.get("selection") or "").strip()
        detail = " · ".join(value for value in (title, selection[:240], url) if value)
        if detail:
            candidate_lines.append(f"{index}. {detail}")
    candidate_block = (
        "\n\n📌 Кандидаты из Agent Desk:\n" + "\n".join(candidate_lines)
        if candidate_lines else ""
    )

    return (
        f"🌐 Новая заявка с сайта\n"
        f"👩‍💼 Владелец: {_bot.LEAD_OWNER_NAME}\n"
        f"ID: web-lead-{int(lead_id)}\n"
        f"Клиент: {payload['name']}\n"
        f"Телефон: {payload['phone']}\n"
        f"Направление: {payload['destination']}\n"
        f"Вылет: {payload['origin']}\n"
        f"Даты: {payload['dates']}\n"
        f"Людей: {payload['people']}\n"
        f"Бюджет: {int(payload['budget'])} ₽"
        + (f"\nUTM: {utm}" if utm else "")
        + "\n\n🔎 Подбор менеджеру:\n"
        + f"Tourvisor PRO: {_bot.MANAGER_TOURVISOR_URL}\n"
        + f"Sletat PRO: {_bot.MANAGER_SLETAT_URL}\n"
        + f"Qui-Quo: {_bot.MANAGER_QUIQUO_URL}"
        + candidate_block
    )


def _deliver_owner_notification(lead_id: int) -> bool:
    with _bot._db_cursor() as cur:
        cur.execute(
            "SELECT owner_status, owner_attempts, mdt_payload FROM website_leads WHERE id=?",
            (int(lead_id),),
        )
        row = cur.fetchone()
    if not row:
        return False
    if row[0] == "synced":
        return True

    try:
        payload = json.loads(row[2])
        success = bool(_bot.send_lead_owner_vk(_owner_notification_text(lead_id, payload)))
    except Exception as exc:
        logger.warning(
            "Website lead VK owner notification failed for lead %s: %s",
            lead_id,
            type(exc).__name__,
        )
        success = False

    now = int(time.time())
    attempts = int(row[1] or 0) + 1
    _bot.record_funnel_event(
        "website",
        _funnel_source(payload),
        "manager",
        "delivered" if success else "failed",
    )
    with _bot._db_cursor(commit=True) as cur:
        if success:
            cur.execute(
                """
                UPDATE website_leads
                SET owner_status='synced', owner_attempts=?,
                    owner_next_retry_at=NULL, owner_notified_at=?
                WHERE id=?
                """,
                (attempts, now, int(lead_id)),
            )
        else:
            cur.execute(
                """
                UPDATE website_leads
                SET owner_status='pending', owner_attempts=?,
                    owner_next_retry_at=?
                WHERE id=?
                """,
                (attempts, now + _retry_delay(attempts), int(lead_id)),
            )
    return success


def _notify_managers(lead_id: int, payload: Dict[str, Any]) -> None:
    utm = "/".join(
        value for value in (
            payload.get("utm_source", ""),
            payload.get("utm_medium", ""),
            payload.get("utm_campaign", ""),
        ) if value
    )
    lines = [
        "🌐 <b>Новая заявка с сайта</b>",
        f"ID: web-lead-{int(lead_id)}",
        f"Клиент: {html.escape(str(payload['name']))}",
        f"Телефон: {html.escape(str(payload['phone']))}",
        f"Направление: {html.escape(str(payload['destination']))}",
        f"Вылет: {html.escape(str(payload['origin']))}",
        f"Даты: {html.escape(str(payload['dates']))}",
        f"Людей: {html.escape(str(payload['people']))}",
        f"Бюджет: {int(payload['budget'])} ₽",
    ]
    if utm:
        lines.append(f"UTM: {html.escape(utm)}")
    text = "\n".join(lines)
    for chat_id in _bot.LEAD_NOTIFY_IDS:
        try:
            _bot.send_message(chat_id, text)
        except Exception as exc:
            logger.warning("Website lead manager notification failed: %s", type(exc).__name__)


def _process_new_lead(lead_id: int, payload: Dict[str, Any]) -> None:
    _deliver_owner_notification(lead_id)
    _notify_managers(lead_id, payload)
    _deliver_lead(lead_id)


def _kick_delivery(lead_id: int, payload: Dict[str, Any]) -> None:
    threading.Thread(
        target=_process_new_lead,
        args=(int(lead_id), dict(payload)),
        daemon=True,
        name=f"website-lead-{int(lead_id)}",
    ).start()


def _cleanup_old_leads() -> None:
    days = int(getattr(_bot, "DATA_RETENTION_DAYS", 0) or 0)
    if days <= 0:
        return
    cutoff = int(time.time()) - days * 86400
    with _bot._db_cursor(commit=True) as cur:
        lead_ids = [
            int(row[0])
            for row in cur.execute(
                "SELECT id FROM website_leads WHERE created_at < ?",
                (cutoff,),
            ).fetchall()
        ]
        _travel_crm_store.delete_for_lead_ids(
            cur.connection,
            lead_ids,
            channel="website",
        )
        cur.execute("DELETE FROM website_leads WHERE created_at < ?", (cutoff,))


def _retry_worker() -> None:
    while True:
        try:
            if not _bot.DEMO_MODE:
                now = int(time.time())
                with _bot._db_cursor() as cur:
                    cur.execute(
                        """
                        SELECT id FROM website_leads
                        WHERE owner_status='pending'
                          AND COALESCE(owner_next_retry_at, 0) <= ?
                        ORDER BY id
                        LIMIT ?
                        """,
                        (now, int(_bot.MDT_RETRY_BATCH_SIZE)),
                    )
                    owner_due = [int(row[0]) for row in cur.fetchall()]
                for lead_id in owner_due:
                    _deliver_owner_notification(lead_id)

                if _bot.MDT_ENABLED:
                    with _bot._db_cursor() as cur:
                        cur.execute(
                            """
                            SELECT id FROM website_leads
                            WHERE mdt_status='pending'
                              AND COALESCE(mdt_next_retry_at, 0) <= ?
                            ORDER BY id
                            LIMIT ?
                            """,
                            (now, int(_bot.MDT_RETRY_BATCH_SIZE)),
                        )
                        mdt_due = [int(row[0]) for row in cur.fetchall()]
                    for lead_id in mdt_due:
                        _deliver_lead(lead_id)
                _cleanup_old_leads()
        except Exception as exc:
            logger.warning("Website lead retry worker error: %s", type(exc).__name__)
        time.sleep(max(5, int(_bot.MDT_RETRY_POLL_SECONDS)))


def _start_worker_once() -> None:
    global _worker_started
    if not _WORKER_ENABLED:
        return
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
        threading.Thread(
            target=_retry_worker,
            daemon=True,
            name="website-mdt-retry",
        ).start()
        logger.info("Website lead delivery retry worker started")


def _store_lead(payload: Dict[str, Any]) -> Tuple[int, bool]:
    request_key = "website:" + str(payload["request_id"])
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with _bot._db_cursor(commit=True) as cur:
        try:
            cur.execute(
                """
                INSERT INTO website_leads (
                    request_key, name, phone, destination, origin, dates, people,
                    budget, consent_at, utm_source, utm_medium, utm_campaign,
                    utm_content, utm_term, created_at, mdt_status, mdt_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    request_key,
                    payload["name"],
                    payload["phone"],
                    payload["destination"],
                    payload["origin"],
                    payload["dates"],
                    payload["people"],
                    payload["budget"],
                    payload["consent_at"],
                    payload.get("utm_source", ""),
                    payload.get("utm_medium", ""),
                    payload.get("utm_campaign", ""),
                    payload.get("utm_content", ""),
                    payload.get("utm_term", ""),
                    payload["created_at"],
                    serialized,
                ),
            )
            lead_id = int(cur.lastrowid)
            try:
                crm_request = _travel_crm_adapter.trip_request_from_lead(
                    lead_id=lead_id,
                    info=payload,
                    channel="website",
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
            except Exception as crm_exc:
                logger.warning(
                    "CRM mirror skipped for website lead %s: %s",
                    lead_id,
                    type(crm_exc).__name__,
                )
        except Exception as exc:
            # sqlite3.IntegrityError is deliberately not imported just for this
            # branch.  Verify the unique request key before treating the error
            # as an idempotent replay; otherwise propagate the real DB failure.
            cur.execute("SELECT id FROM website_leads WHERE request_key=?", (request_key,))
            row = cur.fetchone()
            if row:
                lead_id = int(row[0])
                duplicate = True
            else:
                _bot._record_ops_metric("lead", "website", "save_failure")
                raise exc
        else:
            duplicate = False
    outcome = "duplicate" if duplicate else "accepted"
    _bot._record_ops_metric("lead", "website", outcome)
    _bot.record_funnel_event(
        "website",
        _funnel_source(payload),
        "lead",
        outcome,
    )
    return lead_id, duplicate


if "agent_extension_leads" not in app.view_functions:

    @app.route("/agent-extension/leads", methods=["GET", "OPTIONS"])
    def agent_extension_leads() -> Response:
        if request.method == "OPTIONS":
            return _agent_json_response({"ok": True}, 204)
        if not _AGENT_EXTENSION_TOKEN:
            return _agent_json_response(
                {"ok": False, "error": "agent_extension_disabled"}, 503
            )
        if not _agent_extension_authorized():
            return _agent_json_response({"ok": False, "error": "unauthorized"}, 401)

        try:
            limit = max(1, min(int(request.args.get("limit", "30")), 100))
        except (TypeError, ValueError):
            limit = 30

        with _bot._db_cursor() as cur:
            cur.execute(
                """
                SELECT id, name, phone, destination, origin, dates, people, budget,
                       created_at, owner_status, crm_status, crm_note, crm_followup_on,
                       crm_updated_at, mdt_payload
                FROM website_leads
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()

        leads = []
        for row in rows:
            candidates = []
            try:
                stored = json.loads(row[14] or "{}")
                candidates = stored.get("agent_candidates") or []
            except (TypeError, ValueError, json.JSONDecodeError):
                candidates = []
            leads.append({
                "id": int(row[0]),
                "name": str(row[1] or ""),
                "phone": str(row[2] or ""),
                "destination": str(row[3] or ""),
                "origin": str(row[4] or ""),
                "dates": str(row[5] or ""),
                "people": str(row[6] or ""),
                "budget": int(row[7] or 0),
                "createdAt": int(row[8] or 0),
                "ownerStatus": str(row[9] or ""),
                "status": str(row[10] or "new"),
                "note": str(row[11] or ""),
                "followUpOn": str(row[12] or ""),
                "updatedAt": int(row[13] or 0),
                "candidateCount": len(candidates) if isinstance(candidates, list) else 0,
            })
        return _agent_json_response({"ok": True, "leads": leads})


if "agent_extension_export" not in app.view_functions:

    @app.route("/agent-extension/export.csv", methods=["GET", "OPTIONS"])
    def agent_extension_export() -> Response:
        if request.method == "OPTIONS":
            return _agent_json_response({"ok": True}, 204)
        if not _AGENT_EXTENSION_TOKEN:
            return _agent_json_response(
                {"ok": False, "error": "agent_extension_disabled"}, 503
            )
        if not _agent_extension_authorized():
            return _agent_json_response({"ok": False, "error": "unauthorized"}, 401)

        with _bot._db_cursor() as cur:
            cur.execute(
                """
                SELECT id, name, phone, destination, origin, dates, people, budget,
                       crm_status, crm_note, crm_followup_on, created_at, crm_updated_at
                FROM website_leads
                ORDER BY created_at DESC, id DESC
                """
            )
            rows = cur.fetchall()

        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow([
            "ID", "Имя", "Телефон", "Направление", "Вылет", "Даты", "Туристы",
            "Бюджет", "Статус", "Заметка", "Следующий контакт", "Создано", "Обновлено",
        ])
        for row in rows:
            writer.writerow([
                int(row[0]),
                _csv_safe_cell(row[1]),
                _csv_safe_cell(row[2]),
                _csv_safe_cell(row[3]),
                _csv_safe_cell(row[4]),
                _csv_safe_cell(row[5]),
                _csv_safe_cell(row[6]),
                int(row[7] or 0),
                _csv_safe_cell(row[8] or "new"),
                _csv_safe_cell(row[9]),
                _csv_safe_cell(row[10]),
                int(row[11] or 0),
                int(row[12] or 0),
            ])

        stamp = time.strftime("%Y-%m-%d")
        return _agent_csv_response(
            stream.getvalue(),
            f"turbot-leads-{stamp}.csv",
        )


if "agent_extension_status" not in app.view_functions:

    @app.route("/agent-extension/status", methods=["POST", "OPTIONS"])
    def agent_extension_status() -> Response:
        if request.method == "OPTIONS":
            return _agent_json_response({"ok": True}, 204)
        if not _AGENT_EXTENSION_TOKEN:
            return _agent_json_response(
                {"ok": False, "error": "agent_extension_disabled"}, 503
            )
        if not _agent_extension_authorized():
            return _agent_json_response({"ok": False, "error": "unauthorized"}, 401)
        if not request.is_json:
            return _agent_json_response({"ok": False, "error": "json_required"}, 415)

        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            return _agent_json_response({"ok": False, "error": "invalid_json"}, 400)
        try:
            lead_id = int(raw.get("leadId") or 0)
        except (TypeError, ValueError):
            lead_id = 0
        status = str(raw.get("status") or "").strip().lower()
        allowed = {"new", "working", "waiting", "won", "lost"}
        if lead_id <= 0 or status not in allowed:
            return _agent_json_response({"ok": False, "error": "invalid_status"}, 400)
        try:
            note = _safe_text(raw.get("note"), 500)
        except ValueError:
            return _agent_json_response({"ok": False, "error": "note_too_long"}, 400)

        follow_up_on = str(raw.get("followUpOn") or "").strip()
        if follow_up_on:
            try:
                date.fromisoformat(follow_up_on)
            except ValueError:
                return _agent_json_response(
                    {"ok": False, "error": "invalid_followup_date"}, 400
                )

        now = int(time.time())
        with _bot._db_cursor(commit=True) as cur:
            cur.execute(
                """
                UPDATE website_leads
                SET crm_status=?, crm_note=?, crm_followup_on=?, crm_updated_at=?
                WHERE id=?
                """,
                (status, note, follow_up_on, now, lead_id),
            )
            changed = int(cur.rowcount or 0)
        if not changed:
            return _agent_json_response({"ok": False, "error": "lead_not_found"}, 404)

        request_id = f"web-lead-{lead_id}"
        with _bot._db_cursor(commit=True) as cur:
            timeline = _travel_crm_store.load_timeline(cur.connection, request_id)
            if timeline is not None:
                summary = f"Статус: {status}"
                if note:
                    summary += f" · {note}"
                _travel_crm_store.append_activity(
                    cur.connection,
                    Activity(
                        activity_id="activity-" + secrets.token_hex(10),
                        request_id=request_id,
                        type=ActivityType.STATUS_CHANGE,
                        summary=summary,
                        created_at=datetime.utcnow(),
                    ),
                )
                if follow_up_on:
                    follow_date = date.fromisoformat(follow_up_on)
                    _travel_crm_store.upsert_task(
                        cur.connection,
                        ManagerTask(
                            task_id=f"{request_id}:followup:{follow_up_on}",
                            request_id=request_id,
                            type=TaskType.NEXT_CONTACT,
                            due_at=datetime.combine(follow_date, datetime.min.time()),
                            created_at=datetime.utcnow(),
                            priority=2,
                            note="Связаться с клиентом",
                        ),
                    )
                if status in {"won", "lost", "waiting"}:
                    outcome_status = {
                        "won": OutcomeStatus.WON,
                        "lost": OutcomeStatus.LOST,
                        "waiting": OutcomeStatus.PAUSED,
                    }[status]
                    _travel_crm_store.set_outcome(
                        cur.connection,
                        request_id,
                        BookingOutcome(
                            status=outcome_status,
                            reason=note,
                            decided_at=datetime.utcnow(),
                        ),
                    )
                if status in {"won", "lost"}:
                    cur.execute(
                        "UPDATE crm_tasks SET status=? WHERE request_id=? AND status=?",
                        (
                            TaskStatus.DONE.value,
                            request_id,
                            TaskStatus.TODO.value,
                        ),
                    )

        return _agent_json_response({
            "ok": True,
            "leadId": lead_id,
            "status": status,
            "followUpOn": follow_up_on,
            "updatedAt": now,
        })




def _agent_crm_guard() -> Response | None:
    if _agent_extension_authorized():
        return None

    telegram_error = _telegram_manager_authorization_error()
    if telegram_error == "":
        return None
    if telegram_error == "forbidden":
        return _agent_json_response(
            {"ok": False, "error": "manager_forbidden"}, 403
        )
    if not _AGENT_EXTENSION_TOKEN and not _bot.BOT_TOKEN:
        return _agent_json_response(
            {"ok": False, "error": "agent_extension_disabled"}, 503
        )
    return _agent_json_response({"ok": False, "error": "unauthorized"}, 401)


_VK_DATABASE_PATH = (
    os.getenv("VK_DATABASE_PATH", "vk_bot_state.sqlite").strip()
    or "vk_bot_state.sqlite"
)


def _agent_crm_db_path(store: str) -> Path:
    if store == "main":
        raw = Path(str(_bot.DATABASE_PATH)).expanduser()
        return raw if raw.is_absolute() else (Path.cwd() / raw).resolve()
    if store != "vk":
        raise ValueError("unsupported CRM store")
    raw = Path(str(_VK_DATABASE_PATH)).expanduser()
    if raw.is_absolute():
        return raw
    main = _agent_crm_db_path("main")
    return (main.parent / raw).resolve()


def _agent_crm_vk_is_main() -> bool:
    return _agent_crm_db_path("vk") == _agent_crm_db_path("main")


def _agent_crm_store_for_request(request_id: str) -> str:
    return "vk" if request_id.startswith("vk-lead-") and not _agent_crm_vk_is_main() else "main"


@contextmanager
def _agent_crm_cursor(store: str, *, commit: bool = False):
    """Yield a cursor for the CRM store without allowing client-chosen DB paths."""

    if store == "main" or _agent_crm_vk_is_main():
        with _bot._db_cursor(commit=commit) as cur:
            yield cur
        return

    path = _agent_crm_db_path("vk")
    if not path.exists():
        yield None
        return

    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        yield cur
        if commit:
            conn.commit()
    except Exception:
        if commit:
            conn.rollback()
        raise
    finally:
        conn.close()


def _agent_crm_find_entity_store(table: str, key_column: str, key_value: str):
    if table not in {"crm_tasks", "crm_quotes"}:
        raise ValueError("unsupported CRM lookup table")
    if key_column not in {"task_id", "quote_id"}:
        raise ValueError("unsupported CRM lookup key")

    matches = []
    stores = ("main",) if _agent_crm_vk_is_main() else ("main", "vk")
    for store in stores:
        with _agent_crm_cursor(store) as cur:
            if cur is None:
                continue
            try:
                row = cur.execute(
                    f"SELECT request_id FROM {table} WHERE {key_column} = ?",
                    (key_value,),
                ).fetchone()
            except sqlite3.OperationalError:
                logger.warning("Agent CRM %s store schema unavailable", store)
                continue
            if row is not None:
                matches.append((store, str(row[0])))
    if len(matches) == 1:
        return matches[0]
    return None


def _agent_parse_datetime(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        return datetime.utcnow()
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _agent_crm_client_summary(cur: Any, request_id: str) -> Dict[str, Any]:
    row = cur.execute(
        """
        SELECT lead_id, channel
        FROM crm_trip_requests
        WHERE request_id=?
        """,
        (request_id,),
    ).fetchone()
    if not row or row[0] is None:
        return {"leadId": None, "name": "", "phone": "", "username": ""}

    lead_id = int(row[0])
    channel = str(row[1] or "")
    if channel == "website":
        lead = cur.execute(
            "SELECT name, phone FROM website_leads WHERE id=?",
            (lead_id,),
        ).fetchone()
        if not lead:
            return {"leadId": lead_id, "name": "", "phone": "", "username": ""}
        return {
            "leadId": lead_id,
            "name": str(lead[0] or ""),
            "phone": str(lead[1] or ""),
            "username": "",
        }

    if channel in {"telegram", "vk"}:
        lead = cur.execute(
            "SELECT first_name, phone, username FROM leads WHERE id=?",
            (lead_id,),
        ).fetchone()
        if not lead:
            return {"leadId": lead_id, "name": "", "phone": "", "username": ""}
        return {
            "leadId": lead_id,
            "name": str(lead[0] or ""),
            "phone": str(lead[1] or ""),
            "username": str(lead[2] or ""),
        }

    return {"leadId": lead_id, "name": "", "phone": "", "username": ""}


def _agent_crm_today_items(store: str, end_of_day: datetime):
    result = []
    with _agent_crm_cursor(store) as cur:
        if cur is None:
            return result
        try:
            tasks = _travel_crm_store.due_tasks(cur.connection, end_of_day)
        except sqlite3.OperationalError:
            logger.warning("Agent CRM %s store has no readable CRM task schema", store)
            return result

        for task in tasks:
            timeline = _travel_crm_store.load_timeline(cur.connection, task.request_id)
            if timeline is None:
                continue
            trip = timeline.request
            result.append((
                task.priority,
                task.due_at,
                task.created_at,
                {
                    "taskId": task.task_id,
                    "requestId": task.request_id,
                    "type": task.type.value,
                    "priority": task.priority,
                    "dueAt": task.due_at.replace(
                        tzinfo=timezone.utc
                    ).isoformat().replace("+00:00", "Z"),
                    "note": task.note,
                    "channel": trip.attribution.channel,
                    "sourceTag": trip.attribution.source_tag,
                    "destination": trip.primary_destination,
                    "origin": trip.departure_city,
                    "dates": trip.dates_text,
                    "adults": trip.adults,
                    "childAges": [child.age for child in trip.children],
                    "budget": trip.budget_amount,
                    "budgetScope": trip.budget_scope.value,
                    "client": _agent_crm_client_summary(cur, task.request_id),
                },
            ))
    return result


def _agent_crm_summary(window_seconds: int = 86400) -> Dict[str, Any]:
    """Return bounded aggregate lead and delivery telemetry without customer data."""
    now = int(time.time())
    cutoff = now - window_seconds
    channel_counts: Dict[str, int] = {}
    delivery_rows = []
    stores = ("main",) if _agent_crm_vk_is_main() else ("main", "vk")

    for store in stores:
        with _agent_crm_cursor(store) as cur:
            if cur is None:
                continue
            try:
                grouped = cur.execute(
                    """
                    SELECT channel, COUNT(*)
                    FROM crm_trip_requests
                    WHERE created_at >= ?
                    GROUP BY channel
                    """,
                    (cutoff,),
                ).fetchall()
                for channel, count in grouped:
                    safe_channel = str(channel or "unknown")[:32]
                    channel_counts[safe_channel] = (
                        channel_counts.get(safe_channel, 0) + int(count or 0)
                    )

                delivery_rows.extend(cur.execute(
                    """
                    SELECT lead.created_at, lead.manager_notified_at
                    FROM crm_trip_requests AS request
                    JOIN leads AS lead ON lead.id = request.lead_id
                    WHERE request.created_at >= ?
                      AND request.channel IN ('telegram', 'vk')
                    """,
                    (cutoff,),
                ).fetchall())
                if store == "main":
                    delivery_rows.extend(cur.execute(
                        """
                        SELECT lead.created_at, lead.owner_notified_at
                        FROM crm_trip_requests AS request
                        JOIN website_leads AS lead ON lead.id = request.lead_id
                        WHERE request.created_at >= ?
                          AND request.channel = 'website'
                        """,
                        (cutoff,),
                    ).fetchall())
            except sqlite3.OperationalError:
                logger.warning("Agent CRM %s summary schema unavailable", store)

    delivery = lead_delivery_snapshot(
        delivery_rows,
        window_seconds=window_seconds,
    )
    return {
        "windowSeconds": window_seconds,
        "newLeads": sum(channel_counts.values()),
        "channels": [
            {"channel": channel, "count": channel_counts[channel]}
            for channel in sorted(channel_counts)
        ],
        "delivery": {
            "managerNotified": delivery["manager_notified"],
            "pending": delivery["pending_manager_delivery"],
            "p95Seconds": delivery["latency_seconds"]["p95"],
        },
    }


if "agent_extension_crm_summary" not in app.view_functions:

    @app.route("/agent-extension/crm/summary", methods=["GET", "OPTIONS"])
    def agent_extension_crm_summary() -> Response:
        if request.method == "OPTIONS":
            return _agent_json_response({"ok": True}, 204)
        denied = _agent_crm_guard()
        if denied is not None:
            return denied
        return _agent_json_response({"ok": True, "summary": _agent_crm_summary()})


if "agent_extension_crm_today" not in app.view_functions:

    @app.route("/agent-extension/crm/today", methods=["GET", "OPTIONS"])
    def agent_extension_crm_today() -> Response:
        if request.method == "OPTIONS":
            return _agent_json_response({"ok": True}, 204)
        denied = _agent_crm_guard()
        if denied is not None:
            return denied

        try:
            now_raw = str(request.args.get("now") or "").strip()
            now = _agent_parse_datetime(now_raw) if now_raw else datetime.utcnow()
            tz_offset_minutes = int(request.args.get("tzOffsetMinutes") or 0)
            if not -840 <= tz_offset_minutes <= 840:
                raise ValueError("timezone offset out of range")
            limit = max(1, min(100, int(request.args.get("limit") or 100)))
        except (TypeError, ValueError):
            return _agent_json_response(
                {"ok": False, "error": "invalid_today_query"}, 400
            )

        # Browser getTimezoneOffset() is UTC - local time. Compute the manager's
        # local end-of-day, then convert that boundary back to UTC for storage.
        local_now = now - timedelta(minutes=tz_offset_minutes)
        local_end_of_day = local_now.replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
        end_of_day = local_end_of_day + timedelta(minutes=tz_offset_minutes)

        rows = _agent_crm_today_items("main", end_of_day)
        if not _agent_crm_vk_is_main():
            rows.extend(_agent_crm_today_items("vk", end_of_day))
        rows.sort(key=lambda item: (item[0], item[1], item[2], item[3]["requestId"]))
        items = [item[3] for item in rows[:limit]]
        return _agent_json_response({"ok": True, "tasks": items})


if "agent_extension_crm_timeline" not in app.view_functions:

    @app.route("/agent-extension/crm/timeline", methods=["GET", "OPTIONS"])
    def agent_extension_crm_timeline() -> Response:
        if request.method == "OPTIONS":
            return _agent_json_response({"ok": True}, 204)
        denied = _agent_crm_guard()
        if denied is not None:
            return denied

        request_id = str(request.args.get("requestId") or "").strip()
        if not request_id or len(request_id) > 120:
            return _agent_json_response(
                {"ok": False, "error": "invalid_request_id"}, 400
            )
        store = _agent_crm_store_for_request(request_id)
        with _agent_crm_cursor(store) as cur:
            if cur is None:
                timeline = None
            else:
                timeline = _travel_crm_store.load_timeline(
                    cur.connection, request_id
                )
                payload = timeline_to_dict(timeline) if timeline is not None else None
                if payload is not None:
                    payload["client"] = _agent_crm_client_summary(cur, request_id)
        if timeline is None:
            return _agent_json_response(
                {"ok": False, "error": "request_not_found"}, 404
            )
        return _agent_json_response({
            "ok": True,
            "timeline": payload,
        })


if "agent_extension_crm_task" not in app.view_functions:

    @app.route("/agent-extension/crm/task", methods=["POST", "OPTIONS"])
    def agent_extension_crm_task() -> Response:
        if request.method == "OPTIONS":
            return _agent_json_response({"ok": True}, 204)
        denied = _agent_crm_guard()
        if denied is not None:
            return denied
        if not request.is_json:
            return _agent_json_response({"ok": False, "error": "json_required"}, 415)
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            return _agent_json_response({"ok": False, "error": "invalid_json"}, 400)

        task_id = str(raw.get("taskId") or "").strip()
        status_raw = str(raw.get("status") or "").strip().lower()
        if task_id and status_raw:
            try:
                status = TaskStatus(status_raw)
            except ValueError:
                return _agent_json_response(
                    {"ok": False, "error": "invalid_task_status"}, 400
                )
            request_id = str(raw.get("requestId") or "").strip()
            resolved = (
                (_agent_crm_store_for_request(request_id), request_id)
                if request_id
                else _agent_crm_find_entity_store("crm_tasks", "task_id", task_id)
            )
            if resolved is None:
                return _agent_json_response(
                    {"ok": False, "error": "task_not_found_or_ambiguous"}, 404
                )
            store, request_id = resolved
            with _agent_crm_cursor(store, commit=True) as cur:
                changed = bool(cur) and _travel_crm_store.set_task_status(
                    cur.connection, task_id, status
                )
            if not changed:
                return _agent_json_response(
                    {"ok": False, "error": "task_not_found"}, 404
                )
            return _agent_json_response({
                "ok": True,
                "taskId": task_id,
                "status": status.value,
            })

        request_id = str(raw.get("requestId") or "").strip()
        type_raw = str(raw.get("type") or "").strip().lower()
        if not request_id:
            return _agent_json_response(
                {"ok": False, "error": "invalid_request_id"}, 400
            )
        try:
            task_type = TaskType(type_raw)
            due_at = _agent_parse_datetime(raw.get("dueAt"))
            priority = int(raw.get("priority") or 2)
            note = _safe_text(raw.get("note"), 500)
            task = ManagerTask(
                task_id="task-" + secrets.token_hex(10),
                request_id=request_id,
                type=task_type,
                due_at=due_at,
                created_at=datetime.utcnow(),
                priority=priority,
                note=note,
            )
        except (TypeError, ValueError):
            return _agent_json_response(
                {"ok": False, "error": "invalid_task"}, 400
            )

        store = _agent_crm_store_for_request(request_id)
        with _agent_crm_cursor(store, commit=True) as cur:
            if cur is None or _travel_crm_store.load_timeline(cur.connection, request_id) is None:
                return _agent_json_response(
                    {"ok": False, "error": "request_not_found"}, 404
                )
            _travel_crm_store.upsert_task(cur.connection, task)
        return _agent_json_response({
            "ok": True,
            "taskId": task.task_id,
            "requestId": request_id,
        })


if "agent_extension_crm_quote" not in app.view_functions:

    @app.route("/agent-extension/crm/quote", methods=["POST", "OPTIONS"])
    def agent_extension_crm_quote() -> Response:
        if request.method == "OPTIONS":
            return _agent_json_response({"ok": True}, 204)
        denied = _agent_crm_guard()
        if denied is not None:
            return denied
        if not request.is_json:
            return _agent_json_response({"ok": False, "error": "json_required"}, 415)
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            return _agent_json_response({"ok": False, "error": "invalid_json"}, 400)

        try:
            request_id = _safe_text(raw.get("requestId"), 120)
            hotel = _safe_text(raw.get("hotel"), 240)
            operator = _safe_text(raw.get("operator"), 160)
            carrier = _safe_text(raw.get("carrier"), 160)
            meal_plan = _safe_text(raw.get("mealPlan"), 80)
            price_amount = int(raw.get("priceAmount") or 0)
            currency = (_safe_text(raw.get("currency") or "RUB", 8) or "RUB").upper()
            reaction = QuoteReaction(
                str(raw.get("reaction") or QuoteReaction.DRAFT.value).strip().lower()
            )
            if not request_id or not hotel or price_amount <= 0:
                raise ValueError("required")
            quote = Quote(
                quote_id="quote-" + secrets.token_hex(10),
                request_id=request_id,
                hotel=hotel,
                operator=operator,
                carrier=carrier,
                meal_plan=meal_plan,
                price_amount=price_amount,
                currency=currency,
                calculated_at=datetime.utcnow(),
                reaction=reaction,
            )
        except (TypeError, ValueError):
            return _agent_json_response(
                {"ok": False, "error": "invalid_quote"}, 400
            )

        store = _agent_crm_store_for_request(request_id)
        with _agent_crm_cursor(store, commit=True) as cur:
            if cur is None or _travel_crm_store.load_timeline(cur.connection, request_id) is None:
                return _agent_json_response(
                    {"ok": False, "error": "request_not_found"}, 404
                )
            _travel_crm_store.append_quote(cur.connection, quote)
        return _agent_json_response({
            "ok": True,
            "quoteId": quote.quote_id,
            "requestId": request_id,
        })


if "agent_extension_crm_reaction" not in app.view_functions:

    @app.route("/agent-extension/crm/reaction", methods=["POST", "OPTIONS"])
    def agent_extension_crm_reaction() -> Response:
        if request.method == "OPTIONS":
            return _agent_json_response({"ok": True}, 204)
        denied = _agent_crm_guard()
        if denied is not None:
            return denied
        if not request.is_json:
            return _agent_json_response({"ok": False, "error": "json_required"}, 415)
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            return _agent_json_response({"ok": False, "error": "invalid_json"}, 400)
        quote_id = str(raw.get("quoteId") or "").strip()
        try:
            reaction = QuoteReaction(str(raw.get("reaction") or "").strip().lower())
            note = _safe_text(raw.get("note"), 500)
        except ValueError:
            return _agent_json_response(
                {"ok": False, "error": "invalid_reaction"}, 400
            )
        if not quote_id:
            return _agent_json_response(
                {"ok": False, "error": "invalid_quote_id"}, 400
            )
        request_id = str(raw.get("requestId") or "").strip()
        resolved = (
            (_agent_crm_store_for_request(request_id), request_id)
            if request_id
            else _agent_crm_find_entity_store("crm_quotes", "quote_id", quote_id)
        )
        if resolved is None:
            return _agent_json_response(
                {"ok": False, "error": "quote_not_found_or_ambiguous"}, 404
            )
        store, request_id = resolved
        with _agent_crm_cursor(store, commit=True) as cur:
            if cur is None:
                return _agent_json_response(
                    {"ok": False, "error": "quote_not_found"}, 404
                )
            quote_row = cur.execute(
                "SELECT request_id FROM crm_quotes WHERE quote_id = ?",
                (quote_id,),
            ).fetchone()
            if quote_row is None or str(quote_row[0]) != request_id:
                return _agent_json_response(
                    {"ok": False, "error": "quote_not_found"}, 404
                )
            event = QuoteReactionEvent(
                event_id="reaction-" + secrets.token_hex(10),
                quote_id=quote_id,
                request_id=request_id,
                reaction=reaction,
                note=note,
                created_at=datetime.utcnow(),
            )
            _travel_crm_store.append_quote_reaction(cur.connection, event)
        return _agent_json_response({
            "ok": True,
            "eventId": event.event_id,
            "requestId": request_id,
        })


if "agent_extension_crm_activity" not in app.view_functions:

    @app.route("/agent-extension/crm/activity", methods=["POST", "OPTIONS"])
    def agent_extension_crm_activity() -> Response:
        if request.method == "OPTIONS":
            return _agent_json_response({"ok": True}, 204)
        denied = _agent_crm_guard()
        if denied is not None:
            return denied
        if not request.is_json:
            return _agent_json_response({"ok": False, "error": "json_required"}, 415)
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            return _agent_json_response({"ok": False, "error": "invalid_json"}, 400)
        request_id = str(raw.get("requestId") or "").strip()
        try:
            activity_type = ActivityType(
                str(raw.get("type") or ActivityType.NOTE.value).strip().lower()
            )
            summary = _safe_text(raw.get("summary"), 1000)
        except ValueError:
            return _agent_json_response(
                {"ok": False, "error": "invalid_activity"}, 400
            )
        if not request_id or not summary:
            return _agent_json_response(
                {"ok": False, "error": "invalid_activity"}, 400
            )
        activity = Activity(
            activity_id="activity-" + secrets.token_hex(10),
            request_id=request_id,
            type=activity_type,
            summary=summary,
            created_at=datetime.utcnow(),
        )
        store = _agent_crm_store_for_request(request_id)
        with _agent_crm_cursor(store, commit=True) as cur:
            if cur is None or _travel_crm_store.load_timeline(cur.connection, request_id) is None:
                return _agent_json_response(
                    {"ok": False, "error": "request_not_found"}, 404
                )
            _travel_crm_store.append_activity(cur.connection, activity)
        return _agent_json_response({
            "ok": True,
            "activityId": activity.activity_id,
            "requestId": request_id,
        })


if "agent_extension_crm_outcome" not in app.view_functions:

    @app.route("/agent-extension/crm/outcome", methods=["POST", "OPTIONS"])
    def agent_extension_crm_outcome() -> Response:
        if request.method == "OPTIONS":
            return _agent_json_response({"ok": True}, 204)
        denied = _agent_crm_guard()
        if denied is not None:
            return denied
        if not request.is_json:
            return _agent_json_response({"ok": False, "error": "json_required"}, 415)
        raw = request.get_json(silent=True)
        if not isinstance(raw, dict):
            return _agent_json_response({"ok": False, "error": "invalid_json"}, 400)

        request_id = str(raw.get("requestId") or "").strip()
        try:
            status = OutcomeStatus(str(raw.get("status") or "").strip().lower())
            reason = _safe_text(raw.get("reason"), 500)
        except ValueError:
            return _agent_json_response(
                {"ok": False, "error": "invalid_outcome"}, 400
            )
        if not request_id:
            return _agent_json_response(
                {"ok": False, "error": "invalid_request_id"}, 400
            )

        now = datetime.utcnow()
        store = _agent_crm_store_for_request(request_id)
        with _agent_crm_cursor(store, commit=True) as cur:
            if cur is None or _travel_crm_store.load_timeline(cur.connection, request_id) is None:
                return _agent_json_response(
                    {"ok": False, "error": "request_not_found"}, 404
                )
            _travel_crm_store.set_outcome(
                cur.connection,
                request_id,
                BookingOutcome(status=status, reason=reason, decided_at=now),
            )
            _travel_crm_store.append_activity(
                cur.connection,
                Activity(
                    activity_id="activity-" + secrets.token_hex(10),
                    request_id=request_id,
                    type=ActivityType.STATUS_CHANGE,
                    summary=(
                        f"Результат: {status.value}"
                        + (f" · {reason}" if reason else "")
                    ),
                    created_at=now,
                ),
            )
            if status in {OutcomeStatus.WON, OutcomeStatus.LOST}:
                cur.execute(
                    "UPDATE crm_tasks SET status=? WHERE request_id=? AND status=?",
                    (
                        TaskStatus.DONE.value,
                        request_id,
                        TaskStatus.TODO.value,
                    ),
                )

        return _agent_json_response({
            "ok": True,
            "requestId": request_id,
            "status": status.value,
        })


if "agent_extension_lead" not in app.view_functions:

    @app.route("/agent-extension/lead", methods=["POST", "OPTIONS"])
    def agent_extension_lead() -> Response:
        if request.method == "OPTIONS":
            return _agent_json_response({"ok": True}, 204)

        if not _AGENT_EXTENSION_TOKEN:
            return _agent_json_response(
                {"ok": False, "error": "agent_extension_disabled"}, 503
            )
        if not _agent_extension_authorized():
            return _agent_json_response({"ok": False, "error": "unauthorized"}, 401)
        if _bot.DEMO_MODE:
            return _agent_json_response({"ok": False, "error": "leads_disabled"}, 503)
        if not request.is_json:
            return _agent_json_response({"ok": False, "error": "json_required"}, 415)
        if not _rate_allowed("agent:" + _client_key()):
            return _agent_json_response({"ok": False, "error": "rate_limited"}, 429)

        raw = request.get_json(silent=True)
        payload, error = _validate_payload(raw)
        if error:
            return _agent_json_response({"ok": False, "error": error}, 400)
        assert payload is not None

        candidates = []
        for item in (raw.get("candidates") or [])[:10]:
            if not isinstance(item, dict):
                continue
            try:
                title = _safe_text(item.get("title"), 180)
                raw_url = _safe_text(item.get("url"), 1000)
                url = _sanitize_candidate_url(raw_url)
                selection = _safe_text(item.get("selection"), 1200)
            except ValueError:
                continue
            if title or url or selection:
                candidates.append(
                    {"title": title, "url": url, "selection": selection}
                )

        payload["agent_candidates"] = candidates
        payload["utm_source"] = "agent_extension"
        payload["utm_medium"] = "browser_sidepanel"
        payload["utm_content"] = _safe_text(raw.get("active_service"), 80)

        lead_id, duplicate = _store_lead(payload)
        if not duplicate:
            _kick_delivery(lead_id, payload)
        return _agent_json_response(
            {
                "ok": True,
                "leadId": lead_id,
                "status": "accepted",
                "duplicate": duplicate,
            },
            200 if duplicate else 202,
        )


if "website_lead" not in app.view_functions:

    @app.route("/website/lead", methods=["POST", "OPTIONS"])
    def website_lead() -> Response:
        if not _origin_allowed():
            return _json_response({"ok": False, "error": "origin_not_allowed"}, 403)

        if request.method == "OPTIONS":
            return _json_response({"ok": True}, 204)

        if _bot.DEMO_MODE:
            return _json_response({"ok": False, "error": "website_leads_disabled"}, 503)

        if not request.is_json:
            return _json_response({"ok": False, "error": "json_required"}, 415)

        payload, error = _validate_payload(request.get_json(silent=True))
        if error:
            _bot._record_ops_metric("lead", "website", "validation_reject")
            return _json_response({"ok": False, "error": error}, 400)
        assert payload is not None

        if payload.get("_honeypot"):
            return _json_response({"ok": True, "status": "accepted"}, 202)

        if not _rate_allowed(_client_key()):
            return _json_response({"ok": False, "error": "rate_limited"}, 429)

        lead_id, duplicate = _store_lead(payload)
        if not duplicate:
            _kick_delivery(lead_id, payload)

        return _json_response(
            {
                "ok": True,
                "leadId": lead_id,
                "status": "accepted",
                "duplicate": duplicate,
            },
            200 if duplicate else 202,
        )


_init_schema()
_start_worker_once()
