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
import threading
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from flask import Response, jsonify, request

import bot as _bot
from shared import mdt as mdt_shared

app = _bot.app
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


def _safe_text(value: Any, max_len: int) -> str:
    text = str(value or "").strip()
    if len(text) > max_len:
        raise ValueError("too_long")
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
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
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
            return int(cur.lastrowid), False
        except Exception as exc:
            # sqlite3.IntegrityError is deliberately not imported just for this
            # branch.  Verify the unique request key before treating the error
            # as an idempotent replay; otherwise propagate the real DB failure.
            cur.execute("SELECT id FROM website_leads WHERE request_key=?", (request_key,))
            row = cur.fetchone()
            if row:
                return int(row[0]), True
            raise exc


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
                str(row[1] or ""),
                str(row[2] or ""),
                str(row[3] or ""),
                str(row[4] or ""),
                str(row[5] or ""),
                str(row[6] or ""),
                int(row[7] or 0),
                str(row[8] or "new"),
                str(row[9] or ""),
                str(row[10] or ""),
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
        return _agent_json_response({
            "ok": True,
            "leadId": lead_id,
            "status": status,
            "followUpOn": follow_up_on,
            "updatedAt": now,
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
                url = _safe_text(item.get("url"), 1000)
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
