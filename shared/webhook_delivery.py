"""Durable replay fencing, without storing webhook bodies or credentials.

A crashed/failed handler has an ambiguous outcome: automatically replaying it
can repeat a notification or CRM write. Keep that receipt fenced for operator
reconciliation, return a retryable response, and never call it a success.
"""

from contextlib import closing
import hashlib
import json
import sqlite3
import time


class DeliveryPending(RuntimeError):
    """A delivery is in progress or needs reconciliation before any replay."""


def event_key(channel, account, event_id):
    """Hash only the transport identity; do not persist customer payloads."""
    raw = json.dumps([channel, str(account), str(event_id)], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _connect(path):
    conn = sqlite3.connect(path, timeout=1)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS webhook_receipts (
            event_key TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK(state IN ('processing', 'complete', 'uncertain')),
            updated_at INTEGER NOT NULL
        )""")
        conn.commit()
    except BaseException:
        conn.close()
        raise
    return conn


def run_once(path, key, handler):
    """Only acknowledge completed processing; fence ambiguous outcomes.

    No transaction/SQLite writer lock is held while the application runs.
    IDs remain recorded until explicitly reconciled/archived by an operator;
    silently evicting them would allow a delayed replay to repeat side effects.
    """
    with closing(_connect(path)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state FROM webhook_receipts WHERE event_key=?", (key,)
            ).fetchone()
            if row:
                if row[0] == "complete":
                    return False
                raise DeliveryPending("Webhook receipt requires reconciliation")
            conn.execute(
                "INSERT INTO webhook_receipts VALUES (?, 'processing', ?)",
                (key, int(time.time())),
            )
        try:
            handler()
            with conn:
                conn.execute(
                    "UPDATE webhook_receipts SET state='complete', updated_at=? WHERE event_key=?",
                    (int(time.time()), key),
                )
        except BaseException:
            # A hard process kill leaves 'processing', which is also fenced.
            try:
                with conn:
                    conn.execute(
                        "UPDATE webhook_receipts SET state='uncertain', updated_at=? WHERE event_key=?",
                        (int(time.time()), key),
                    )
            except sqlite3.Error:
                pass  # The already-committed processing fence still protects it.
            raise
    return True


def valid_object(value):
    return isinstance(value, dict)


def valid_message(message, *, telegram=False):
    if not valid_object(message):
        return False
    if any(k in message and not isinstance(message[k], str) for k in ("text", "payload")):
        return False
    if any(k in message and not valid_object(message[k]) for k in ("from", "chat", "contact")):
        return False
    if telegram:
        if not valid_object(message.get("chat")):
            return False
        identity = message["chat"].get("id")
    else:
        identity = message.get("from_id") or message.get("peer_id")
    return type(identity) is int and identity != 0


def valid_telegram(data):
    if not valid_object(data):
        return False
    if "update_id" in data and (type(data["update_id"]) is not int or data["update_id"] < 0):
        return False
    if "message" in data and "callback_query" in data:
        return False
    if "message" in data:
        return valid_message(data["message"], telegram=True)
    if "callback_query" in data:
        query = data["callback_query"]
        return (valid_object(query) and isinstance(query.get("id"), str)
                and isinstance(query.get("data", ""), str)
                and valid_object(query.get("from"))
                and type(query["from"].get("id")) is int
                and ("message" not in query or valid_message(query["message"], telegram=True)))
    return True  # Unsupported provider event types have no side effects.
