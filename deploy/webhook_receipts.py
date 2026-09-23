"""Inspect and reconcile fenced deliveries on a stopped service.

Run by the release owner after checking persisted leads and provider delivery.
The default operation is read-only. No payloads or secrets are printed.
"""
import argparse
import re
import sqlite3
import time
from pathlib import Path


def reconcile(conn, key, decision):
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("Expected a full receipt hash")
    with conn:
        row = conn.execute("SELECT state FROM webhook_receipts WHERE event_key=?", (key,)).fetchone()
        if not row or row[0] == "complete":
            raise ValueError("Receipt is absent or already complete")
        if decision == "complete":
            conn.execute("UPDATE webhook_receipts SET state='complete', updated_at=? WHERE event_key=?",
                         (int(time.time()), key))
        elif decision == "retry":
            conn.execute("DELETE FROM webhook_receipts WHERE event_key=?", (key,))
        else:
            raise ValueError("Unknown decision")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--receipt")
    parser.add_argument("--decision", choices=("complete", "retry"))
    parser.add_argument("--service-stopped", action="store_true")
    args = parser.parse_args()
    mutate = args.decision is not None or args.receipt is not None
    if mutate and not (args.receipt and args.decision and args.service_stopped):
        parser.error("Reconciliation requires --receipt, --decision and --service-stopped")
    uri = Path(args.database).resolve().as_uri() + ("?mode=rw" if mutate else "?mode=ro")
    with sqlite3.connect(uri, uri=True) as conn:
        if mutate:
            reconcile(conn, args.receipt, args.decision)
            print("Receipt reconciled; confirm provider redelivery before restart")
        else:
            for state, count in conn.execute("SELECT state, COUNT(*) FROM webhook_receipts GROUP BY state"):
                print(f"{state}: {count}")
            for key, state, updated in conn.execute(
                "SELECT event_key, state, updated_at FROM webhook_receipts WHERE state != 'complete' ORDER BY updated_at"
            ):
                print(f"{key} {state} {updated}")


if __name__ == "__main__":
    main()
