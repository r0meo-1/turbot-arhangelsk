"""Read a consistent PostgreSQL snapshot for the existing Markdown renderer."""
import asyncio
from datetime import date, datetime
import json

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


class ProjectionReadError(RuntimeError):
    """Sanitized read failure; no SQL, credentials or source content."""


def _record(row):
    result = dict(row)
    for key, value in result.items():
        if isinstance(value, (date, datetime)):
            result[key] = value.isoformat()
    if 'candidate_json' in result:
        result['candidate_json'] = json.dumps(result['candidate_json'], allow_nan=False)
    return result


class ProjectionStore:
    """Caller owns the engine. No outbox acknowledgement or file writes.

    Read both collections in one repeatable-read, read-only transaction so a
    concurrent ingestion cannot split the projection across two snapshots.
    Cancellation of the async wrapper does not stop its background read; the
    caller must keep the engine alive until outstanding reads have finished.
    """

    def __init__(self, engine):
        if engine.dialect.name != 'postgresql':
            raise ValueError('ProjectionStore requires PostgreSQL')
        self.engine = engine

    def snapshot(self):
        try:
            with self.engine.connect().execution_options(
                isolation_level='REPEATABLE READ', postgresql_readonly=True,
            ) as connection:
                with connection.begin():
                    tasks = [_record(row) for row in connection.execute(text('''
                        SELECT * FROM task
                        ORDER BY priority_score DESC, due_date NULLS LAST,
                                 updated_at DESC, id
                    ''')).mappings()]
                    reviews = [_record(row) for row in connection.execute(text('''
                        SELECT * FROM review_queue WHERE resolved=false
                        ORDER BY created_at, id
                    ''')).mappings()]
                    return tasks, reviews
        except SQLAlchemyError:
            raise ProjectionReadError('Projection snapshot failed') from None

    async def snapshot_async(self):
        return await asyncio.to_thread(self.snapshot)
