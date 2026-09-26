import asyncio
import logging

from .logic import (
    PriorityEngine,
    TaskExtractor,
    Validator,
)


log = logging.getLogger(__name__)


class Engine:
    def __init__(self, repo):
        self.repo = repo
        self.priority = PriorityEngine()
        self.extractor = TaskExtractor()
        self.validator = Validator()

    async def process(self, message):
        score = self.priority.score(message)

        candidates = self.extractor.extract(
            message,
            score,
        )

        return await self.repo.ingest(
            message,
            candidates,
            self.validator,
        )


class OutboxWorker:
    def __init__(
        self,
        repo,
        projection,
    ):
        self.repo = repo
        self.projection = projection

    async def deliver_one(self):
        event = await self.repo.next_outbox()

        if not event:
            return False

        try:
            tasks, reviews = await asyncio.gather(
                self.repo.tasks(),
                self.repo.reviews(),
            )

            generated = self.projection.render(
                tasks,
                reviews,
            )

            await asyncio.to_thread(
                self.projection.write,
                generated,
            )

            await self.repo.mark_done(
                event["id"]
            )

            return True

        except Exception as exc:
            await self.repo.mark_failure(
                event["id"],
                event["attempts"] + 1,
                exc,
            )

            raise

    async def drain(self):
        while await self.deliver_one():
            pass

    async def run(self):
        while True:
            try:
                delivered = await self.deliver_one()

                if not delivered:
                    await asyncio.sleep(1)

            except asyncio.CancelledError:
                raise

            except Exception:
                log.exception(
                    "outbox delivery failed"
                )

                await asyncio.sleep(2)
