from pathlib import Path

path = Path("vk_bot.py")
text = path.read_text(encoding="utf-8")

table_anchor = '''        cur.execute("""
            CREATE TABLE IF NOT EXISTS miniapp_drafts (
                chat_id INTEGER PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        # Additive migration for databases created before the origin step.
'''
table_replacement = '''        cur.execute("""
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
'''
if "CREATE TABLE IF NOT EXISTS maintenance_migrations" not in text:
    if table_anchor not in text:
        raise SystemExit("maintenance table anchor not found")
    text = text.replace(table_anchor, table_replacement, 1)

index_anchor = '''        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_chat_id ON leads(chat_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_mdt_retry ON leads(mdt_status, mdt_next_retry_at)")
        cur.execute("PRAGMA journal_mode=WAL")
'''
index_replacement = '''        # One-time production repair for the single lead that the pre-acknowledgement
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

        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_chat_id ON leads(chat_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_mdt_retry ON leads(mdt_status, mdt_next_retry_at)")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.fetchone()
'''
if "20260915_requeue_false_mdt_sync_lead_35" not in text:
    if index_anchor not in text:
        raise SystemExit("repair anchor not found")
    text = text.replace(index_anchor, index_replacement, 1)

path.write_text(text, encoding="utf-8")

test_path = Path("tests/test_vk_bot.py")
tests = test_path.read_text(encoding="utf-8")
marker = "def test_false_synced_mdt_lead_35_is_requeued_once"
if marker not in tests:
    tests += '''


def test_false_synced_mdt_lead_35_is_requeued_once(tmp_path, monkeypatch):
    db_path = tmp_path / "repair.sqlite"
    monkeypatch.setattr(bot, "DATABASE_PATH", str(db_path))

    # First initialization creates schema and records the repair as already
    # checked. Remove just the marker so the fixture can reproduce the exact
    # production state found by diagnostics.
    bot.init_db()
    repair_name = "20260915_requeue_false_mdt_sync_lead_35"
    with bot._db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM maintenance_migrations WHERE name = ?", (repair_name,))
        cur.execute(
            """
            INSERT INTO leads (
                id, chat_id, phone, mdt_status, mdt_attempts,
                mdt_next_retry_at, mdt_synced_at, created_at
            ) VALUES (35, 424242, 'VK test', 'synced', 0, NULL, 123, 123)
            """
        )

    bot.init_db()
    with bot._db_cursor() as cur:
        row = cur.execute(
            "SELECT mdt_status, mdt_attempts, mdt_next_retry_at, mdt_synced_at "
            "FROM leads WHERE id=35"
        ).fetchone()
        marker_row = cur.execute(
            "SELECT applied_at FROM maintenance_migrations WHERE name = ?", (repair_name,)
        ).fetchone()

    assert row["mdt_status"] == "pending"
    assert row["mdt_attempts"] == 0
    assert row["mdt_next_retry_at"] is not None
    assert row["mdt_synced_at"] is None
    assert marker_row is not None

    # The marker prevents the repair from firing twice after a later successful
    # sync or a process restart.
    with bot._db_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE leads SET mdt_status='synced', mdt_next_retry_at=NULL WHERE id=35"
        )
    bot.init_db()
    with bot._db_cursor() as cur:
        row = cur.execute("SELECT mdt_status FROM leads WHERE id=35").fetchone()
    assert row["mdt_status"] == "synced"
'''
    test_path.write_text(tests, encoding="utf-8")
