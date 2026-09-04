from __future__ import annotations

import asyncio
import importlib
import logging
import secrets
from collections.abc import AsyncIterator, Callable
from typing import Protocol, cast

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.domain.events import MediaPrepareEvent
from app.domain.models import OutboxEvent
from app.repositories.outbox import OutboxRepository

logger = logging.getLogger(__name__)


class EventPublisher(Protocol):
    async def start(self) -> None: ...

    async def publish(self, topic: str, key: bytes, value: bytes) -> None: ...

    async def close(self) -> None: ...


class _Producer(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send_and_wait(self, topic: str, value: bytes, key: bytes) -> object: ...


class KafkaEventPublisher:
    def __init__(self, bootstrap_servers: str) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._producer: _Producer | None = None

    async def start(self) -> None:
        return None

    async def publish(self, topic: str, key: bytes, value: bytes) -> None:
        if self._producer is None:
            module = importlib.import_module("aiokafka")
            producer_type = cast(Callable[..., _Producer], module.AIOKafkaProducer)
            producer = producer_type(bootstrap_servers=self._bootstrap_servers)
            try:
                await producer.start()
            except Exception:
                await producer.stop()
                raise
            self._producer = producer
        try:
            await self._producer.send_and_wait(topic, value=value, key=key)
        except Exception:
            await self._producer.stop()
            self._producer = None
            raise

    async def close(self) -> None:
        if self._producer is not None:
            await self._producer.stop()


class DirectEventPublisher:
    def __init__(self, handler: MediaEventHandler, dlq_topic: str) -> None:
        self._handler = handler
        self._dlq_topic = dlq_topic

    async def start(self) -> None:
        return None

    async def publish(self, topic: str, key: bytes, value: bytes) -> None:
        del key
        if topic == self._dlq_topic:
            return
        event = MediaPrepareEvent.model_validate_json(value, strict=True)
        await self._handler.handle(event)

    async def close(self) -> None:
        return None


class MediaEventHandler(Protocol):
    async def handle(self, event: MediaPrepareEvent) -> None: ...


class OutboxPublisher:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        publisher: EventPublisher,
        settings: Settings,
    ) -> None:
        self._sessions = session_factory
        self._publisher = publisher
        self._settings = settings
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self._publisher.start()
        self._task = asyncio.create_task(self._run(), name="outbox-publisher")

    def notify(self) -> None:
        self._wake.set()

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self._publisher.close()

    async def drain_once(self) -> int:
        events = await asyncio.to_thread(self._claim_batch)
        for event_id, topic, aggregate_id, payload in events:
            try:
                await self._publisher.publish(topic, aggregate_id.encode(), payload.encode())
            except Exception as exc:
                logger.warning(
                    "outbox_publish_failed",
                    extra={"event_id": event_id, "error_type": type(exc).__name__},
                )
                await asyncio.to_thread(self._release, event_id, "kafka_unavailable")
            else:
                await asyncio.to_thread(self._mark_published, event_id)
        return len(events)

    async def _run(self) -> None:
        while True:
            try:
                processed = await self.drain_once()
            except Exception as exc:
                logger.warning("outbox_loop_failed", extra={"error_type": type(exc).__name__})
                processed = 0
            if processed:
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self._settings.outbox_poll_interval_seconds
                )
            except TimeoutError:
                pass

    def _claim_batch(self) -> list[tuple[str, str, str, str]]:
        with self._sessions() as db, db.begin():
            events = OutboxRepository(db).claim_batch(
                self._settings.outbox_batch_size,
                self._settings.outbox_claim_timeout_seconds,
            )
            return [(item.id, item.topic, item.aggregate_id, item.payload_json) for item in events]

    def _mark_published(self, event_id: str) -> None:
        with self._sessions() as db, db.begin():
            OutboxRepository(db).mark_published(event_id)

    def _release(self, event_id: str, error_code: str) -> None:
        with self._sessions() as db, db.begin():
            event = db.get(OutboxEvent, event_id)
            attempt = event.attempt_count if event is not None else 0
            delay = min(2**attempt, 30) + secrets.SystemRandom().random()
            OutboxRepository(db).release(event_id, error_code, delay)


class _ConsumerMessage(Protocol):
    value: bytes


class _Consumer(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def commit(self) -> None: ...

    def __aiter__(self) -> AsyncIterator[_ConsumerMessage]: ...


async def run_kafka_worker(handler: MediaEventHandler, settings: Settings) -> None:
    module = importlib.import_module("aiokafka")
    consumer_type = cast(Callable[..., _Consumer], module.AIOKafkaConsumer)
    consumer = consumer_type(
        settings.kafka_prepare_topic,
        settings.kafka_retry_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=settings.kafka_consumer_group,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for message in consumer:
            try:
                event = MediaPrepareEvent.model_validate_json(message.value, strict=True)
            except ValidationError:
                logger.error("invalid_media_event")
                await consumer.commit()
                continue
            await handler.handle(event)
            await consumer.commit()
    finally:
        await consumer.stop()
