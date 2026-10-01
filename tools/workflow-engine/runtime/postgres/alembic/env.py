import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool


url = os.environ.get("WORKFLOW_POSTGRES_MIGRATION_URL", "")
if not url:
    raise RuntimeError("WORKFLOW_POSTGRES_MIGRATION_URL is required")
if make_url(url).get_backend_name() != "postgresql":
    raise RuntimeError("Workflow migration requires PostgreSQL")

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, transactional_ddl=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
