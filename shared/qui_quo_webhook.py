"""Safe one-way Qui-Quo -> TurBot webhook receiver.

Qui-Quo currently provides no request signature or shared-secret header. The
configured random URL path is therefore a compensating control, not proof of
origin. The receiver validates and durably records only non-PII event metadata,
acks quickly, and projects CRM activity asynchronously.

The provider quote id is an external identifier. It is never assumed to equal a
TurBot crm_quotes.quote_id: a manager-authorized explicit link maps it to a
TurBot request and CRM store.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from decimal import Decimal, DecimalException
from typing import Any, Callable
from urllib.parse import parse_qs

from flask import Blueprint, Response, jsonify, request


logger = logging.getLogger("turbot.qui_quo")

SUPPORTED_EVENTS = frozenset(
    {
        "first_quote_open",
        "quote_open",
        "item_click",
        "item_order",
        "item_deposit",
    }
)
ITEM_EVENTS = frozenset({"item_click", "item_order", "item_deposit"})

DEFAULT_MAX_BODY_BYTES = 256 * 1024
DEFAULT_POLL_SECONDS = 5
DEFAULT_BATCH_SIZE = 20
MAX_ITEMS = 200
MAX_ID_LENGTH = 128
MAX_DECIMAL_TEXT = 32
MAX_DEPOSIT_INTEGER_DIGITS = 13
MAX_DEPOSIT_FRACTION_DIGITS = 4

_worker_lock = threading.Lock()
_worker_started = False


class QuiQuoValidationError(ValueError):
    """Bounded public validation failure with a stable non-sensitive code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class QuiQuoLinkConflict(ValueError):
    """The same external quote is already linked to another TurBot request."""


def _safe_id(value: Any, field: str) -> str:
    if isinstance(value, bool):
        raise QuiQuoValidationError(f"{field}_invalid")
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise QuiQuoValidationError(f"{field}_invalid")
    if not text or len(text) > MAX_ID_LENGTH:
        raise QuiQuoValidationError(f"{field}_invalid")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
    if any(ch not in allowed for ch in text):
        raise QuiQuoValidationError(f"{field}_invalid")
    return text


def _safe_store(value: Any) -> str:
    store = str(value or "").strip().lower()
    if store not in {"main", "vk"}:
        raise ValueError("invalid_store")
    return store


def _decimal_text(value: Any, field: str) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        raise QuiQuoValidationError(f"{field}_invalid")

    raw = str(value).strip()
    if not raw or len(raw) > MAX_DECIMAL_TEXT:
        raise QuiQuoValidationError(f"{field}_invalid")

    try:
        parsed = Decimal(raw)
    except (DecimalException, ValueError):
        raise QuiQuoValidationError(f"{field}_invalid") from None

    if not parsed.is_finite() or parsed < 0:
        raise QuiQuoValidationError(f"{field}_invalid")

    exponent = parsed.as_tuple().exponent
    adjusted = parsed.adjusted() if parsed else 0
    if adjusted >= MAX_DEPOSIT_INTEGER_DIGITS:
        raise QuiQuoValidationError(f"{field}_invalid")
    if exponent < -MAX_DEPOSIT_FRACTION_DIGITS:
        raise QuiQuoValidationError(f"{field}_invalid")

    try:
        rendered = format(parsed.normalize(), "f")
    except DecimalException:
        raise QuiQuoValidationError(f"{field}_invalid") from None
    if len(rendered) > MAX_DECIMAL_TEXT:
        raise QuiQuoValidationError(f"{field}_invalid")
    return rendered


def _extract_payload(max_body_bytes: int) -> dict[str, Any]:
    content_length = request.content_length
    if content_length is not None and content_length > max_body_bytes:
        raise QuiQuoValidationError("payload_too_large")

    mimetype = (request.mimetype or "").lower()

    if mimetype == "application/json":
        raw = request.get_data(cache=True)
        if len(raw) > max_body_bytes:
            raise QuiQuoValidationError("payload_too_large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise QuiQuoValidationError("invalid_json") from None

    elif mimetype == "application/x-www-form-urlencoded":
        raw = request.get_data(cache=True)
        if len(raw) > max_body_bytes:
            raise QuiQuoValidationError("payload_too_large")
        try:
            form = parse_qs(
                raw.decode("utf-8"),
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=4,
            )
        except (UnicodeDecodeError, ValueError):
            raise QuiQuoValidationError("invalid_form") from None
        if set(form) != {"json"} or len(form["json"]) != 1:
            raise QuiQuoValidationError("invalid_form")
        encoded = form["json"][0]
        if len(encoded.encode("utf-8")) > max_body_bytes:
            raise QuiQuoValidationError("payload_too_large")
        try:
            payload = json.loads(encoded)
        except json.JSONDecodeError:
            raise QuiQuoValidationError("invalid_json") from None

    elif mimetype == "multipart/form-data":
        if content_length is None:
            raise QuiQuoValidationError("content_length_required")
        if content_length > max_body_bytes:
            raise QuiQuoValidationError("payload_too_large")
        if set(request.form.keys()) != {"json"}:
            raise QuiQuoValidationError("invalid_form")
        encoded = request.form.get("json", "")
        if len(encoded.encode("utf-8")) > max_body_bytes:
            raise QuiQuoValidationError("payload_too_large")
        try:
            payload = json.loads(encoded)
        except json.JSONDecodeError:
            raise QuiQuoValidationError("invalid_json") from None

    else:
        raise QuiQuoValidationError("unsupported_content_type")

    if not isinstance(payload, dict):
        raise QuiQuoValidationError("invalid_json")
    return payload


def _selected_item(items: list[dict[str, Any]], item_pos: str) -> dict[str, Any] | None:
    for item in items:
        if "pos" not in item:
            continue
        try:
            if _safe_id(item.get("pos"), "item_pos") == item_pos:
                return item
        except QuiQuoValidationError:
            continue

    if item_pos.isdigit():
        index = int(item_pos)
        if 0 <= index < len(items):
            return items[index]
    return None


def sanitize_event(raw: dict[str, Any]) -> dict[str, str | int | bool]:
    """Validate an event and return only metadata safe for durable storage.

    Compatibility policy: unknown fields are ignored. Known identity/container
    fields are validated strictly. Client names, phones, email addresses,
    comments, customer details and arbitrary tour payload are never returned.
    """

    is_test = raw.get("is_test", False)
    if not isinstance(is_test, bool):
        raise QuiQuoValidationError("is_test_invalid")
    if is_test:
        return {"is_test": True}

    event_type = raw.get("event")
    if not isinstance(event_type, str) or event_type not in SUPPORTED_EVENTS:
        raise QuiQuoValidationError("event_invalid")

    quote = raw.get("quote")
    client = raw.get("client")
    manager = raw.get("manager")
    items_raw = raw.get("items", [])

    if not isinstance(quote, dict):
        raise QuiQuoValidationError("quote_invalid")
    if not isinstance(client, dict):
        raise QuiQuoValidationError("client_invalid")
    if not isinstance(manager, dict):
        raise QuiQuoValidationError("manager_invalid")
    if not isinstance(items_raw, list) or len(items_raw) > MAX_ITEMS:
        raise QuiQuoValidationError("items_invalid")
    if any(not isinstance(item, dict) for item in items_raw):
        raise QuiQuoValidationError("items_invalid")

    quote_id = _safe_id(quote.get("id"), "quote_id")
    client_id = _safe_id(client.get("id"), "client_id")
    manager_id = _safe_id(manager.get("id"), "manager_id")

    item_pos = ""
    deposit_amount = ""
    if event_type in ITEM_EVENTS:
        item_pos = _safe_id(raw.get("item_pos"), "item_pos")
        item = _selected_item(items_raw, item_pos)
        if item is None:
            raise QuiQuoValidationError("item_pos_unknown")
        if event_type == "item_deposit":
            deposit_amount = _decimal_text(
                item.get("deposit_amount"), "deposit_amount"
            )

    semantic_parts = [event_type, quote_id, client_id, item_pos]
    if event_type == "item_deposit":
        semantic_parts.append(deposit_amount)
    semantic_source = "\x1f".join(semantic_parts).encode("utf-8")
    event_key = hashlib.sha256(semantic_source).hexdigest()

    return {
        "is_test": False,
        "event_key": event_key,
        "event_type": event_type,
        "quote_id": quote_id,
        "client_id": client_id,
        "manager_id": manager_id,
        "item_pos": item_pos,
        "deposit_amount": deposit_amount,
        "item_count": len(items_raw),
    }


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def cleanup_before(conn: sqlite3.Connection, cutoff: int) -> None:
    """Delete old provider identifiers in the same retention transaction."""
    if _table_exists(conn, "qui_quo_events"):
        conn.execute(
            "DELETE FROM qui_quo_events WHERE received_at < ?",
            (int(cutoff),),
        )
    if _table_exists(conn, "qui_quo_quote_links"):
        conn.execute(
            "DELETE FROM qui_quo_quote_links WHERE linked_at < ?",
            (int(cutoff),),
        )


def erase_requests(conn: sqlite3.Connection, request_ids: list[str]) -> None:
    """Erase all provider identifiers linked to canonical TurBot requests."""

    ids = [str(value).strip() for value in request_ids if str(value).strip()]
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)

    links_exist = _table_exists(conn, "qui_quo_quote_links")
    events_exist = _table_exists(conn, "qui_quo_events")
    if links_exist:
        quote_rows = conn.execute(
            f"SELECT quote_id FROM qui_quo_quote_links "
            f"WHERE request_id IN ({placeholders})",
            ids,
        ).fetchall()
        quote_ids = [str(row[0]) for row in quote_rows]
        if events_exist and quote_ids:
            quote_placeholders = ",".join("?" for _ in quote_ids)
            conn.execute(
                f"DELETE FROM qui_quo_events "
                f"WHERE quote_id IN ({quote_placeholders})",
                quote_ids,
            )
        conn.execute(
            f"DELETE FROM qui_quo_quote_links "
            f"WHERE request_id IN ({placeholders})",
            ids,
        )
    if events_exist:
        conn.execute(
            f"DELETE FROM qui_quo_events "
            f"WHERE request_id IN ({placeholders})",
            ids,
        )


def link_quote(
    conn: sqlite3.Connection,
    quote_id: str,
    request_id: str,
    store: str,
    *,
    now: int | None = None,
) -> bool:
    """Explicitly associate one external Qui-Quo quote with a TurBot request."""
    safe_quote = _safe_id(quote_id, "quote_id")
    safe_request = _safe_id(request_id, "request_id")
    safe_store = _safe_store(store)
    stamp = int(time.time()) if now is None else int(now)

    existing = conn.execute(
        """
        SELECT request_id, store
        FROM qui_quo_quote_links
        WHERE quote_id=?
        """,
        (safe_quote,),
    ).fetchone()
    if existing is not None:
        if str(existing[0]) == safe_request and str(existing[1]) == safe_store:
            conn.execute(
                """
                UPDATE qui_quo_events
                SET status='pending', next_retry_at=0
                WHERE quote_id=? AND status='unmatched'
                """,
                (safe_quote,),
            )
            return False
        raise QuiQuoLinkConflict("quote_already_linked")

    conn.execute(
        """
        INSERT INTO qui_quo_quote_links (quote_id, request_id, store, linked_at)
        VALUES (?, ?, ?, ?)
        """,
        (safe_quote, safe_request, safe_store, stamp),
    )
    conn.execute(
        """
        UPDATE qui_quo_events
        SET status='pending', next_retry_at=0
        WHERE quote_id=? AND status='unmatched'
        """,
        (safe_quote,),
    )
    return True


class QuiQuoInbox:
    """Durable, PII-minimized inbox and CRM activity projector."""

    def __init__(
        self,
        db_cursor: Callable[..., Any],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        poll_seconds: int = DEFAULT_POLL_SECONDS,
        projector: Callable[[str, str, str, str, int], None] | None = None,
    ) -> None:
        self._db_cursor = db_cursor
        self.batch_size = max(1, int(batch_size))
        self.poll_seconds = max(1, int(poll_seconds))
        self._projector = projector

    def init_schema(self) -> None:
        with self._db_cursor(commit=True) as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qui_quo_events (
                    event_key TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    quote_id TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    manager_id TEXT NOT NULL,
                    item_pos TEXT NOT NULL DEFAULT '',
                    deposit_amount TEXT NOT NULL DEFAULT '',
                    item_count INTEGER NOT NULL DEFAULT 0,
                    received_at INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_retry_at INTEGER NOT NULL DEFAULT 0,
                    processed_at INTEGER,
                    request_id TEXT,
                    last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_qui_quo_events_pending
                ON qui_quo_events(status, next_retry_at, received_at)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_qui_quo_events_quote
                ON qui_quo_events(quote_id, status)
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS qui_quo_quote_links (
                    quote_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    store TEXT NOT NULL,
                    linked_at INTEGER NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_qui_quo_links_request
                ON qui_quo_quote_links(request_id, store)
                """
            )

    def enqueue(self, event: dict[str, str | int | bool]) -> bool:
        if event.get("is_test") is True:
            return False
        now = int(time.time())
        with self._db_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO qui_quo_events (
                    event_key, event_type, quote_id, client_id, manager_id,
                    item_pos, deposit_amount, item_count, received_at,
                    status, attempts, next_retry_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, 0)
                """,
                (
                    event["event_key"],
                    event["event_type"],
                    event["quote_id"],
                    event["client_id"],
                    event["manager_id"],
                    event["item_pos"],
                    event["deposit_amount"],
                    int(event["item_count"]),
                    now,
                ),
            )
            return cur.rowcount == 1

    @staticmethod
    def _summary(row: sqlite3.Row | tuple[Any, ...]) -> str:
        event_type = str(row[1])
        quote_id = str(row[2])
        item_pos = str(row[3] or "")
        deposit_amount = str(row[4] or "")
        summary = f"Qui-Quo: {event_type}; quote={quote_id}"
        if item_pos:
            summary += f"; item={item_pos}"
        if event_type == "item_deposit" and deposit_amount:
            summary += f"; deposit={deposit_amount}"
        return summary[:500]

    def _project(
        self,
        store: str,
        request_id: str,
        activity_id: str,
        summary: str,
        created_at: int,
    ) -> None:
        if self._projector is not None:
            self._projector(store, request_id, activity_id, summary, created_at)
            return

        if store != "main":
            raise RuntimeError("external_projector_required")
        with self._db_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO crm_activities (
                    activity_id, request_id, activity_type, summary, created_at
                ) VALUES (?, ?, 'note', ?, ?)
                """,
                (activity_id, request_id, summary, int(created_at)),
            )

    def process_pending_once(self, *, now: int | None = None) -> int:
        stamp = int(time.time()) if now is None else int(now)
        with self._db_cursor() as cur:
            rows = cur.execute(
                """
                SELECT event_key, event_type, quote_id, item_pos, deposit_amount,
                       attempts, received_at
                FROM qui_quo_events
                WHERE status IN ('pending', 'retry', 'unmatched')
                  AND COALESCE(next_retry_at, 0) <= ?
                ORDER BY received_at, event_key
                LIMIT ?
                """,
                (stamp, self.batch_size),
            ).fetchall()

        processed = 0
        for row in rows:
            event_key = str(row[0])
            try:
                with self._db_cursor() as cur:
                    match = cur.execute(
                        """
                        SELECT request_id, store
                        FROM qui_quo_quote_links
                        WHERE quote_id=?
                        """,
                        (str(row[2]),),
                    ).fetchone()

                if match is None:
                    with self._db_cursor(commit=True) as cur:
                        cur.execute(
                            """
                            UPDATE qui_quo_events
                            SET status='unmatched',
                                next_retry_at=?,
                                processed_at=NULL,
                                last_error=''
                            WHERE event_key=?
                            """,
                            (stamp + max(60, self.poll_seconds), event_key),
                        )
                    continue

                request_id = str(match[0])
                store = str(match[1])
                activity_id = "qui-quo:" + event_key
                self._project(
                    store,
                    request_id,
                    activity_id,
                    self._summary(row),
                    int(row[6]),
                )
                with self._db_cursor(commit=True) as cur:
                    cur.execute(
                        """
                        UPDATE qui_quo_events
                        SET status='processed', processed_at=?, request_id=?,
                            last_error='', next_retry_at=0
                        WHERE event_key=?
                        """,
                        (stamp, request_id, event_key),
                    )
                processed += 1

            except Exception as exc:
                attempts = int(row[5] or 0) + 1
                delay = min(3600, 2 ** min(attempts, 10))
                try:
                    with self._db_cursor(commit=True) as cur:
                        cur.execute(
                            """
                            UPDATE qui_quo_events
                            SET status='retry', attempts=?, next_retry_at=?,
                                last_error=?
                            WHERE event_key=?
                            """,
                            (
                                attempts,
                                stamp + delay,
                                type(exc).__name__[:80],
                                event_key,
                            ),
                        )
                except Exception as state_exc:
                    logger.error(
                        "Qui-Quo retry state update failed: %s",
                        type(state_exc).__name__,
                    )
        return processed

    def worker_loop(self) -> None:
        while True:
            try:
                self.process_pending_once()
            except Exception as exc:
                logger.error(
                    "Qui-Quo inbox worker iteration failed: %s",
                    type(exc).__name__,
                )
            time.sleep(self.poll_seconds)


def _json_response(body: dict[str, Any], status: int = 200) -> Response:
    response = jsonify(body)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def create_blueprint(
    db_cursor: Callable[..., Any],
    *,
    secret_getter: Callable[[], str] | None = None,
    max_body_bytes: int | None = None,
    start_worker: bool = True,
    projector: Callable[[str, str, str, str, int], None] | None = None,
) -> Blueprint:
    """Create the inbound webhook blueprint.

    A secret shorter than 32 characters is treated as misconfiguration and the
    receiver returns 503 rather than exposing a guessable webhook path.
    """

    inbox = QuiQuoInbox(
        db_cursor,
        batch_size=int(os.getenv("QUI_QUO_BATCH_SIZE", str(DEFAULT_BATCH_SIZE))),
        poll_seconds=int(
            os.getenv("QUI_QUO_POLL_SECONDS", str(DEFAULT_POLL_SECONDS))
        ),
        projector=projector,
    )
    inbox.init_schema()

    limit = int(
        max_body_bytes
        if max_body_bytes is not None
        else os.getenv(
            "QUI_QUO_MAX_BODY_BYTES",
            str(DEFAULT_MAX_BODY_BYTES),
        )
    )
    if limit < 1024:
        limit = 1024

    get_secret = secret_getter or (
        lambda: os.getenv("QUI_QUO_WEBHOOK_SECRET", "").strip()
    )

    blueprint = Blueprint("qui_quo_webhook", __name__)

    @blueprint.post("/qq-webhook/<secret_path>")
    def qui_quo_webhook(secret_path: str) -> Response:
        configured = get_secret()
        if len(configured) < 32:
            return _json_response(
                {"success": False, "error": "not_configured"},
                503,
            )
        if not secrets.compare_digest(secret_path, configured):
            return _json_response(
                {"success": False, "error": "not_found"},
                404,
            )

        try:
            raw = _extract_payload(limit)
            event = sanitize_event(raw)
            if event.get("is_test") is True:
                return _json_response({"success": True})
            inserted = inbox.enqueue(event)
        except QuiQuoValidationError as exc:
            error = exc.code
            status = (
                413
                if error == "payload_too_large"
                else 415
                if error == "unsupported_content_type"
                else 400
            )
            return _json_response(
                {"success": False, "error": error},
                status,
            )
        except Exception as exc:
            logger.error(
                "Qui-Quo request persistence failed: %s",
                type(exc).__name__,
            )
            return _json_response(
                {"success": False, "error": "temporarily_unavailable"},
                503,
            )

        return _json_response(
            {"success": True, "duplicate": not inserted}
        )

    if start_worker:
        global _worker_started
        with _worker_lock:
            if not _worker_started:
                _worker_started = True
                threading.Thread(
                    target=inbox.worker_loop,
                    daemon=True,
                    name="qui-quo-inbox",
                ).start()
                logger.info("Qui-Quo inbox worker started")

    return blueprint
