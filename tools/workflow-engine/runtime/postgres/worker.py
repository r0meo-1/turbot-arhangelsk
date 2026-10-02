"""Opt-in Markdown projection worker; not selected by production startup."""
import asyncio

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .outbox import OutboxStore, _duration
from .projection import ProjectionStore


# One canonical board per database. Every PG Markdown writer must use this lock.
PROJECTION_LOCK = 781638427104


class ProjectionDeliveryError(RuntimeError):
    """Safe diagnostic without file contents, SQL or destination exception text."""


class MarkdownWorker:
    """Serialize snapshot-to-file delivery across cooperating worker processes.

    The projection must atomically replace its generated block (MarkdownProjection
    does). Caller owns the engine; provision at least two pool connections per
    active worker because the destination lock spans separate store transactions.
    No external API destination or exactly-once side effect is supported here.
    """

    def __init__(self, engine, projection, lease_seconds=60):
        self.queue = OutboxStore(engine)
        self.snapshots = ProjectionStore(engine)
        self.engine = engine
        self.projection = projection
        self.lease_seconds = _duration(lease_seconds)

    def deliver_one(self):
        try:
            with self.engine.begin() as guard:
                locked = guard.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),
                                      {'key': PROJECTION_LOCK})
                if not locked:
                    return 'busy'
                event = self.queue.claim(self.lease_seconds)
                if event is None:
                    return 'idle'
                event_id, token = event['id'], event['lease_owner']
                try:
                    tasks, reviews = self.snapshots.snapshot()
                    generated = self.projection.render(tasks, reviews)
                    if not self.queue.renew(event_id, token, self.lease_seconds):
                        return 'lease_lost'
                    self.projection.write(generated)
                    # Expiry after file replacement is replayable; never report
                    # success unless the same live claim was acknowledged.
                    return 'delivered' if self.queue.complete(event_id, token) else 'lease_lost'
                except Exception:
                    try:
                        self.queue.fail(event_id, token, 'projection_failed')
                    except Exception:
                        # Leave the claim to expire if even failure recording fails.
                        pass
                    raise ProjectionDeliveryError('Markdown projection delivery failed') from None
        except SQLAlchemyError:
            raise ProjectionDeliveryError('Markdown projection persistence failed') from None

    async def deliver_one_async(self):
        # Do not abandon a thread that may still own the destination lock/write.
        pending = asyncio.create_task(asyncio.to_thread(self.deliver_one))
        cancelled = False
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            # Retrieve a possible exception without masking caller cancellation.
            if not pending.cancelled():
                pending.exception()
            raise asyncio.CancelledError
        return pending.result()
