"""Durable PostgreSQL Gmail state; not selected by the production service."""
from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from workflow_engine.logic import utcnow


class GmailStateError(RuntimeError):
    """Privacy-safe database error."""


def _history(value):
    raw = str(value).strip()
    if not raw.isascii() or not raw.isdigit():
        raise ValueError('History ID must be unsigned decimal digits')
    raw = raw.lstrip('0') or '0'
    if len(raw) > 20:
        raise ValueError('History ID exceeds the PostgreSQL schema precision')
    return Decimal(raw)


def _record(row):
    if row is None:
        return None
    result = dict(row)
    result['history_id'] = str(result['history_id'])
    for key in ('updated_at', 'received_at', 'processed_at'):
        if result.get(key) is not None:
            result[key] = result[key].isoformat()
    return result


class GmailStore:
    """Synchronous state methods; caller owns the engine and service integration."""

    def __init__(self, engine):
        if engine.dialect.name != 'postgresql':
            raise ValueError('GmailStore requires PostgreSQL')
        self.engine = engine

    @contextmanager
    def _transaction(self):
        try:
            with self.engine.begin() as connection:
                yield connection
        except SQLAlchemyError:
            raise GmailStateError('Gmail state operation failed; transaction rolled back') from None

    def gmail_history_id(self, mailbox='me'):
        with self._transaction() as db:
            history = db.scalar(text('SELECT history_id FROM gmail_mailbox WHERE mailbox=:mailbox'),
                                {'mailbox': mailbox})
            return str(history) if history is not None else None

    def advance_gmail_history_id(self, history_id, mailbox='me'):
        history = _history(history_id)
        with self._transaction() as db:
            changed = db.scalar(text('''
                INSERT INTO gmail_mailbox (mailbox,history_id,updated_at)
                VALUES (:mailbox,:history,:now)
                ON CONFLICT (mailbox) DO UPDATE
                    SET history_id=EXCLUDED.history_id,updated_at=EXCLUDED.updated_at
                    WHERE gmail_mailbox.history_id < EXCLUDED.history_id
                RETURNING mailbox
            '''), {'mailbox': mailbox, 'history': history, 'now': utcnow()})
            return changed is not None

    def gmail_watch_state(self, mailbox='me'):
        with self._transaction() as db:
            return _record(db.execute(text('SELECT * FROM gmail_watch WHERE mailbox=:mailbox'),
                                      {'mailbox': mailbox}).mappings().first())

    def record_gmail_watch(self, *, topic_name, history_id, expiration_ms, mailbox='me'):
        history = _history(history_id)
        raw = str(expiration_ms)
        if not raw.isascii() or not raw.isdigit() or len(raw) > 19:
            raise ValueError('Watch expiration must be a positive integer')
        expiration = int(raw)
        if not 0 < expiration <= 2**63 - 1:
            raise ValueError('Watch expiration is outside the PostgreSQL range')
        if not isinstance(topic_name, str) or not topic_name.strip():
            raise ValueError('Watch topic is required')
        with self._transaction() as db:
            changed = db.scalar(text('''
                INSERT INTO gmail_watch (mailbox,topic_name,history_id,expiration_ms,updated_at)
                VALUES (:mailbox,:topic,:history,:expiration,:now)
                ON CONFLICT (mailbox) DO UPDATE SET
                    topic_name=EXCLUDED.topic_name,history_id=EXCLUDED.history_id,
                    expiration_ms=EXCLUDED.expiration_ms,updated_at=EXCLUDED.updated_at
                WHERE gmail_watch.expiration_ms < EXCLUDED.expiration_ms
                RETURNING mailbox
            '''), {'mailbox': mailbox, 'topic': topic_name, 'history': history,
                   'expiration': expiration, 'now': utcnow()})
            # Watch receipt is not proof that its history has been ingested.
            return changed is not None

    def record_gmail_notification(self, *, pubsub_message_id, history_id, mailbox='me'):
        history = _history(history_id)
        if not isinstance(pubsub_message_id, str) or not 1 <= len(pubsub_message_id.strip()) <= 256:
            raise ValueError('Invalid Pub/Sub message ID')
        with self._transaction() as db:
            notification = db.scalar(text('''
                INSERT INTO gmail_notification (mailbox,pubsub_message_id,history_id,received_at)
                VALUES (:mailbox,:message,:history,:now)
                ON CONFLICT DO NOTHING RETURNING id
            '''), {'mailbox': mailbox, 'message': pubsub_message_id.strip(),
                   'history': history, 'now': utcnow()})
            return notification is not None

    def pending_gmail_notification(self, mailbox='me'):
        with self._transaction() as db:
            return _record(db.execute(text('''
                SELECT * FROM gmail_notification WHERE mailbox=:mailbox AND status='pending'
                ORDER BY history_id DESC,id DESC LIMIT 1
            '''), {'mailbox': mailbox}).mappings().first())

    def mark_gmail_notifications_through(self, history_id, mailbox='me'):
        history = _history(history_id)
        with self._transaction() as db:
            result = db.execute(text('''
                UPDATE gmail_notification SET status='done',processed_at=:now
                WHERE mailbox=:mailbox AND status='pending' AND history_id<=:history
            '''), {'mailbox': mailbox, 'history': history, 'now': utcnow()})
            return result.rowcount
