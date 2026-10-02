"""PostgreSQL canonical ingestion; not selected by the production service yet."""
from dataclasses import asdict
from datetime import date
import hashlib
import json
import uuid

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from workflow_engine.logic import priority_band, utcnow


class CanonicalWriteError(RuntimeError):
    """Database failure safe to report without source data or driver SQL."""


def _lock_key(key):
    digest = hashlib.sha256(('workflow-engine:task:' + key).encode()).digest()
    return int.from_bytes(digest[:8], 'big', signed=True)


class CanonicalStore:
    """Synchronous ingestion boundary with one connection/transaction per call.

    The caller owns the SQLAlchemy PostgreSQL engine and its lifecycle. This is
    deliberately not a drop-in replacement for the asynchronous SQLite worker.
    """

    def __init__(self, engine):
        if engine.dialect.name != 'postgresql':
            raise ValueError('CanonicalStore requires PostgreSQL')
        self.engine = engine

    def ingest(self, message, candidates, validator):
        candidates = list(candidates)
        keys = [candidate.dedupe_key for candidate in candidates]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate candidate keys in one extraction')
        if message.received_at.utcoffset() is None:
            raise ValueError('Message receipt must include a timezone')
        try:
            with self.engine.begin() as connection:
                # Concurrent calls wait on the source uniqueness constraint.
                # Only the winner applies candidates; a replay is a no-op.
                inserted = connection.scalar(text('''
                    INSERT INTO email_event
                        (source,external_id,thread_id,sender,subject,received_at,processed_at)
                    VALUES (:source,:external_id,:thread_id,:sender,:subject,:received_at,:now)
                    ON CONFLICT (source,external_id) DO NOTHING RETURNING id
                '''), {
                    'source': message.source, 'external_id': message.external_id,
                    'thread_id': message.thread_id, 'sender': message.sender,
                    'subject': message.subject, 'received_at': message.received_at,
                    'now': utcnow(),
                })
                if inserted is None:
                    return False
                # Row locks alone do not cover tasks that do not exist yet.
                # Acquire transaction-scoped advisory locks in numeric order so
                # overlapping candidate sets cannot reverse the lock order.
                for key in sorted({_lock_key(key) for key in keys}):
                    connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
                for candidate in candidates:
                    self._apply_candidate(connection, message, candidate, validator)
                return True
        except SQLAlchemyError:
            raise CanonicalWriteError('Canonical ingestion failed; transaction rolled back') from None

    def _apply_candidate(self, connection, message, candidate, validator):
        row = connection.execute(text('''
            SELECT * FROM task WHERE dedupe_key=:key FOR UPDATE
        '''), {'key': candidate.dedupe_key}).mappings().first()
        existing = dict(row) if row else None
        if existing and existing['due_date'] is not None:
            # Validator uses the runtime model's ISO date string contract.
            existing['due_date'] = existing['due_date'].isoformat()
        decision = validator.validate(candidate, existing)
        now = utcnow()
        if decision.action == 'REVIEW':
            connection.execute(text('''
                INSERT INTO review_queue (id,source,external_id,candidate_json,reason,created_at)
                VALUES (:id,:source,:external_id,CAST(:candidate AS JSONB),:reason,:now)
            '''), {
                'id': str(uuid.uuid4()), 'source': message.source,
                'external_id': message.external_id,
                'candidate': json.dumps(asdict(candidate), allow_nan=False),
                'reason': decision.reason, 'now': now,
            })
            return
        if decision.action not in {'ACCEPT', 'MERGE'}:
            raise ValueError('Unsupported validation decision')
        due_date = date.fromisoformat(candidate.due_date) if candidate.due_date else None
        if existing:
            task_id = existing['id']
            version = existing['version'] + 1
            score = max(existing['priority_score'], candidate.priority_score)
            connection.execute(text('''
                UPDATE task SET priority_score=:score,priority=:priority,
                    due_date=COALESCE(due_date,:due_date),
                    confidence=GREATEST(confidence,:confidence),version=:version,updated_at=:now
                WHERE id=:id
            '''), {
                'id': task_id, 'score': score, 'priority': priority_band(score),
                'due_date': due_date, 'confidence': candidate.confidence,
                'version': version, 'now': now,
            })
        else:
            task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'workflow-engine:task:{candidate.dedupe_key}'))
            version = 1
            connection.execute(text('''
                INSERT INTO task
                    (id,dedupe_key,title,project,priority_score,priority,owner,due_date,confidence,version,updated_at)
                VALUES (:id,:key,:title,:project,:score,:priority,:owner,:due_date,:confidence,:version,:now)
            '''), {
                'id': task_id, 'key': candidate.dedupe_key, 'title': candidate.title,
                'project': candidate.project, 'score': candidate.priority_score,
                'priority': candidate.priority, 'owner': candidate.owner,
                'due_date': due_date, 'confidence': candidate.confidence,
                'version': version, 'now': now,
            })
        # Exactly the same source-key encoding as the snapshot importer.
        source_key = json.dumps([message.external_id, candidate.dedupe_key],
                                ensure_ascii=False, separators=(',', ':'))
        connection.execute(text('''
            INSERT INTO task_source (task_id,source,source_key,external_id)
            VALUES (:task_id,:source,:key,:external_id)
        '''), {'task_id': task_id, 'source': message.source,
               'key': source_key, 'external_id': message.external_id})
        self._insert_outbox(connection, task_id, version, now)

    def _insert_outbox(self, connection, task_id, version, now):
        # An unexpected duplicate event is a conflict, not a successful write.
        connection.execute(text('''
            INSERT INTO outbox (id,event_key,event_type,payload_json,created_at)
            VALUES (:id,:key,'TASK_CHANGED',CAST(:payload AS JSONB),:now)
        '''), {
            'id': str(uuid.uuid4()), 'key': f'task:{task_id}:v:{version}',
            'payload': json.dumps({'task_id': task_id, 'version': version}), 'now': now,
        })
