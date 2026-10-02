import os
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
TABLES = {
    "gmail_mailbox", "gmail_watch", "gmail_notification", "email_event",
    "extraction_run", "task", "task_source", "review_queue", "outbox",
    "destination_state", "delivery_intent", "delivery", "dead_letter",
}


@pytest.fixture(scope="module")
def engine():
    url = os.environ.get("WORKFLOW_POSTGRES_MIGRATION_URL")
    if not url:
        pytest.skip("Separate PostgreSQL test database not configured")
    parsed = make_url(url)
    if parsed.host not in {"localhost", "127.0.0.1"} or not (parsed.database or "").endswith("_test"):
        pytest.fail("Schema tests require a loopback database ending in _test")
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    instance = create_engine(url, hide_parameters=True)
    yield instance
    instance.dispose()


@pytest.fixture
def empty_database(engine):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE " + ", ".join(sorted(TABLES)) + " RESTART IDENTITY CASCADE"))
    yield

