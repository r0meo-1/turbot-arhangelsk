# Closed-test receipt review

`tools/receipt_review.py` is an offline operator tool. It imports no bot, provider
or messaging client and has no resend action. It is not wired into production,
a scheduler or the bot's retention worker. Use only a metadata snapshot from a
stopped, one-account synthetic test, never production SQLite.

## Operator decisions

1. `list` shows transport outcome separately from manual review. `accepted` is
   API acceptance, not human acknowledgement. Inspect failed/uncertain/missing
   destinations, and overdue unreviewed records (7 days), including partial
   delivery. Never retry an uncertain send blindly.
2. Match the event/channel to the private test notice (its `Квитанция:` line uses the same event ID) and its actual /ack or
   direct visual observation. A timestamp alone is not sufficient where more
   than one candidate exists. If identity is ambiguous, leave it unresolved.
3. `review --action received_confirmed --reason personal_ack` (or visual_review)
   records a separate human observation for accepted/uncertain channels only.
   It cannot turn an explicit refusal or unconfigured destination into delivery.
4. `closed_undelivered` with reason test_completed or handled_separately closes
   a case without claiming it arrived. Use a short synthetic evidence reference,
   never raw message text, credentials, phone numbers or arbitrary notes.
5. Preview, inspect, then repeat with global `--apply`. Identical reviews are
   idempotent; conflicting reviews are rejected. Source outcomes stay intact.
   `audit` lists decisions even after transport receipts are purged.

## Closed-test retention defaults

- Unreviewed/corrupt records: retain, flag overdue after 7 days, investigate.
- Fully reviewed events: retain for 7 days after the last channel review.
- Audit: retain for 30 days after decision and until receipt deletion.
- Backups: retain at least 7 days; explicit prune command, scoped to backups of
  that exact database. This is not secure erasure or a legal retention policy.
- Never delete `webhook_receipts`, offsets or replay fences in this tool.

All inspection and purge operations default to preview. Before a modifying
review/purge, the CLI creates a SQLite backup (mode 600). Keep the closed root
mode 700 and use umask 077. Deletion from SQLite does not erase other snapshots,
filesystem history or original source data. Backups require their own prune.
Do not run the tool against a database with active writers; source test receipts
are preserved and only the marked snapshot is changed. No automatic expiry job
is installed. Unresolved incidents require operator attention, not silent expiry.

## CLI

The existing database must resolve inside a directory marked by
`.closed-receipt-test`. Put global arguments, including --apply, before command.

```bash
umask 077
receipt_test_root=/opt/turbot-ai-sandbox-20261021.Nl5PtB/receipt-review-run-20261021
receipt_test_python=/opt/turbot-ai-sandbox-20261021.Nl5PtB/repo/.venv/bin/python
receipt_test_tool=/opt/turbot-ai-sandbox-20261021.Nl5PtB/repo/tools/receipt_review.py
"$receipt_test_python" "$receipt_test_tool" --closed-root "$receipt_test_root" --database "$receipt_test_root/operator.sqlite" list
"$receipt_test_python" "$receipt_test_tool" --closed-root "$receipt_test_root" --database "$receipt_test_root/operator.sqlite" audit
"$receipt_test_python" "$receipt_test_tool" --closed-root "$receipt_test_root" --database "$receipt_test_root/operator.sqlite" purge
"$receipt_test_python" "$receipt_test_tool" --closed-root "$receipt_test_root" --database "$receipt_test_root/operator.sqlite" prune-backups
```

For a new reviewed case, use its exact event/channel from list and a real
observation reference. The following is a template, not a command to assert
unobserved delivery:

```bash
"$receipt_test_python" "$receipt_test_tool" --closed-root "$receipt_test_root" --database "$receipt_test_root/operator.sqlite" review --event EVENT_ID --channel CHANNEL --action closed_undelivered --reason test_completed --evidence SYNTHETIC_CASE_REFERENCE
```

Add `--apply` before review/purge/prune-backups only after inspecting the preview.
To restore, stop all writers, preserve the current snapshot and restore the
selected private SQLite backup to the snapshot path. Never restore it over
production or reset webhook replay state to retry an uncertain send.

The 2026-10-21 evaluation used a copy of one actually accepted test receipt with
personal /ack and three injected incidents. Future dates were simulated in that
copy; real days did not elapse. It verified holding unresolved/partial incidents,
receipt/audit/backup retention, restore, read-only preview and preserved replay
fences. The original test database hash remained unchanged. The separate
operator.sqlite snapshot is retained for inspection; no new messages were sent.
