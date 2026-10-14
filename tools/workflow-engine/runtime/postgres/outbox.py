"""Leased PostgreSQL outbox persistence; no external delivery or service wiring."""
from contextlib import contextmanager
import uuid

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


MAX_ATTEMPTS = 8
QUEUE_DESTINATION = 'canonical-outbox'
FAILURE_CODES = {'delivery_failed', 'projection_failed', 'destination_unavailable'}


class OutboxStateError(RuntimeError):
    """Privacy-safe persistence failure."""


def _duration(seconds):
    if type(seconds) is not int or not 1 <= seconds <= 3600:
        raise ValueError('Lease duration must be an integer from 1 to 3600 seconds')
    return seconds


class OutboxStore:
    """Caller owns the PostgreSQL engine. Tokens identify claims, not workers.

    Every successful claim returns a fresh lease_owner token, including recovery
    by the same worker. Pass it to renew/complete/fail. No operation calls an
    external destination; external idempotency needs a separate delivery adapter.
    """

    def __init__(self, engine):
        if engine.dialect.name != 'postgresql':
            raise ValueError('OutboxStore requires PostgreSQL')
        self.engine = engine

    @contextmanager
    def _transaction(self):
        try:
            with self.engine.begin() as db:
                yield db
        except SQLAlchemyError:
            raise OutboxStateError('Outbox operation failed; transaction rolled back') from None

    def claim(self, lease_seconds=60):
        seconds = _duration(lease_seconds)
        with self._transaction() as db:
            # Crashes consume attempts too. Bound cleanup to avoid a long queue
            # sweep and never steal an unexpired final attempt from its worker.
            db.execute(text('''
                WITH exhausted AS (
                    SELECT id FROM outbox WHERE attempts >= :maximum AND
                        (status='pending' OR (status='processing' AND lease_until<=clock_timestamp()))
                    ORDER BY created_at,id LIMIT 100 FOR UPDATE SKIP LOCKED
                ), changed AS (
                    UPDATE outbox o SET status='dead',lease_owner=NULL,lease_until=NULL,
                        last_error='attempts_exhausted'
                    FROM exhausted e WHERE o.id=e.id RETURNING o.id,o.attempts
                )
                INSERT INTO dead_letter (outbox_id,destination,reason,attempts,created_at)
                SELECT id,:destination,'attempts_exhausted',attempts,clock_timestamp() FROM changed
                ON CONFLICT (outbox_id,destination) DO UPDATE SET
                    reason=EXCLUDED.reason,attempts=EXCLUDED.attempts,
                    created_at=EXCLUDED.created_at,retried_at=NULL
            '''), {'maximum': MAX_ATTEMPTS, 'destination': QUEUE_DESTINATION})
            row = db.execute(text('''
                WITH ready AS (
                    SELECT id FROM outbox WHERE attempts < :maximum AND
                        (status='pending' OR (status='processing' AND lease_until<=clock_timestamp()))
                    ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED
                )
                UPDATE outbox o SET status='processing',attempts=o.attempts+1,
                    lease_owner=:token,lease_until=clock_timestamp()+(:seconds * interval '1 second')
                FROM ready r WHERE o.id=r.id RETURNING o.*
            '''), {'maximum': MAX_ATTEMPTS, 'token': str(uuid.uuid4()), 'seconds': seconds}).mappings().first()
            return dict(row) if row else None

    @staticmethod
    def _lock(db, event_id):
        # Lock first, then check wall-clock expiry in the following statement.
        # A statement-start timestamp could authorize a claim that expired while
        # waiting for an unrelated transaction to release this row lock.
        db.execute(text('SELECT id FROM outbox WHERE id=:id FOR UPDATE'), {'id': event_id})

    def renew(self, event_id, token, lease_seconds=60):
        seconds = _duration(lease_seconds)
        with self._transaction() as db:
            self._lock(db, event_id)
            return db.scalar(text('''
                UPDATE outbox SET lease_until=GREATEST(lease_until,
                    clock_timestamp()+(:seconds * interval '1 second'))
                WHERE id=:id AND status='processing' AND lease_owner=:token
                    AND lease_until>clock_timestamp() RETURNING id
            '''), {'id': event_id, 'token': token, 'seconds': seconds}) is not None

    def complete(self, event_id, token):
        with self._transaction() as db:
            self._lock(db, event_id)
            return db.scalar(text('''
                UPDATE outbox SET status='done',lease_owner=NULL,lease_until=NULL,last_error=NULL
                WHERE id=:id AND status='processing' AND lease_owner=:token
                    AND lease_until>clock_timestamp() RETURNING id
            '''), {'id': event_id, 'token': token}) is not None

    def fail(self, event_id, token, error_code='delivery_failed'):
        if error_code not in FAILURE_CODES:
            raise ValueError('Use an approved failure code, not exception text')
        with self._transaction() as db:
            self._lock(db, event_id)
            row = db.execute(text('''
                UPDATE outbox SET status=CASE WHEN attempts>=:maximum THEN 'dead' ELSE 'pending' END,
                    lease_owner=NULL,lease_until=NULL,last_error=:reason
                WHERE id=:id AND status='processing' AND lease_owner=:token
                    AND lease_until>clock_timestamp() RETURNING status,attempts
            '''), {'id': event_id, 'token': token, 'reason': error_code,
                   'maximum': MAX_ATTEMPTS}).mappings().first()
            if row is None:
                return False
            if row['status'] == 'dead':
                db.execute(text('''
                    INSERT INTO dead_letter (outbox_id,destination,reason,attempts,created_at)
                    VALUES (:id,:destination,:reason,:attempts,clock_timestamp())
                    ON CONFLICT (outbox_id,destination) DO UPDATE SET
                        reason=EXCLUDED.reason,attempts=EXCLUDED.attempts,
                        created_at=EXCLUDED.created_at,retried_at=NULL
                '''), {'id': event_id, 'destination': QUEUE_DESTINATION,
                       'reason': error_code, 'attempts': row['attempts']})
            return True

    def retry_dead(self, event_id):
        """Explicit operator retry; does not run automatically or deliver anything."""
        with self._transaction() as db:
            self._lock(db, event_id)
            changed = db.scalar(text('''
                UPDATE outbox SET status='pending',attempts=0,last_error=NULL,
                    lease_owner=NULL,lease_until=NULL
                WHERE id=:id AND status='dead' AND EXISTS (
                    SELECT 1 FROM dead_letter WHERE outbox_id=:id AND destination=:destination
                        AND retried_at IS NULL
                ) RETURNING id
            '''), {'id': event_id, 'destination': QUEUE_DESTINATION})
            if changed is None:
                return False
            db.execute(text('''
                UPDATE dead_letter SET retried_at=clock_timestamp()
                WHERE outbox_id=:id AND destination=:destination
            '''), {'id': event_id, 'destination': QUEUE_DESTINATION})
            return True
