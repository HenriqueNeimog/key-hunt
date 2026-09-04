from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.config import Settings
from app.domain.enums import PreparationStatus
from app.domain.models import Base, GameSessionTrack, OutboxEvent, Playlist, PlaylistTrack, Track
from app.infrastructure.db import create_db_engine, create_session_factory
from app.infrastructure.media_processor import MediaProcessor
from app.infrastructure.messaging import KafkaEventPublisher, OutboxPublisher, run_kafka_worker
from app.services.round_service import RoundService
from app.services.session_service import SessionService
from tests.fakes import FakeAnalyzer, FakeMediaClient


@pytest.mark.integration
def test_real_kafka_round_trip() -> None:
    aiokafka = pytest.importorskip("aiokafka")
    bootstrap = os.getenv("KEY_HUNT_KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")

    async def round_trip() -> None:
        topic = f"key-hunt.integration.{uuid4()}"
        producer = aiokafka.AIOKafkaProducer(bootstrap_servers=bootstrap)
        consumer = aiokafka.AIOKafkaConsumer(
            topic,
            bootstrap_servers=bootstrap,
            group_id=f"integration-{uuid4()}",
            auto_offset_reset="earliest",
        )
        await producer.start()
        await consumer.start()
        try:
            await producer.send_and_wait(topic, key=b"track", value=b'{"version":1}')
            message = await asyncio.wait_for(consumer.getone(), timeout=20)
            assert message.key == b"track"
            assert message.value == b'{"version":1}'
        finally:
            await consumer.stop()
            await producer.stop()

    asyncio.run(round_trip())


@pytest.mark.integration
def test_real_kafka_full_prefetch_flow(tmp_path: Path) -> None:
    pytest.importorskip("aiokafka")
    bootstrap = os.getenv("KEY_HUNT_KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    suffix = str(uuid4())
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{(tmp_path / 'flow.db').as_posix()}",
        media_dir=tmp_path / "media",
        secret_key="test-secret-with-more-than-thirty-two-characters",
        processing_mode="kafka",
        kafka_bootstrap_servers=bootstrap,
        kafka_prepare_topic=f"key-hunt.integration.prepare.{suffix}",
        kafka_retry_topic=f"key-hunt.integration.retry.{suffix}",
        kafka_dlq_topic=f"key-hunt.integration.dlq.{suffix}",
        kafka_consumer_group=f"key-hunt-integration-{suffix}",
        prefetch_window_size=1,
        media_worker_concurrency=1,
    )
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as db, db.begin():
        playlist = Playlist(youtube_playlist_id="PLintegration", source_url="https://test")
        tracks = [
            Track(
                youtube_video_id=f"kafka00000{i}",
                title=f"Kafka Track {i}",
                source_url="https://test",
            )
            for i in range(2)
        ]
        db.add_all([playlist, *tracks])
        db.flush()
        db.add_all(
            PlaylistTrack(playlist_id=playlist.id, track_id=track.id, position=index)
            for index, track in enumerate(tracks)
        )
        playlist_id = playlist.id
    rounds = RoundService(factory, settings)
    sessions = SessionService(factory, settings, rounds)
    created = sessions.create(playlist_id, "integration-player", seed="integration-seed")
    media = FakeMediaClient()
    analyzer = FakeAnalyzer()
    processor = MediaProcessor(
        session_factory=factory,
        downloader=media,
        analyzer=analyzer,
        settings=settings,
    )

    async def flow() -> None:
        worker = asyncio.create_task(run_kafka_worker(processor, settings))
        outbox = OutboxPublisher(factory, KafkaEventPublisher(bootstrap), settings)
        await outbox.start()
        try:
            await asyncio.sleep(1)
            outbox.notify()
            for _ in range(100):
                with factory() as db:
                    positions = list(
                        db.scalars(
                            select(GameSessionTrack).where(
                                GameSessionTrack.game_session_id == created.session_id
                            )
                        )
                    )
                    if positions and all(
                        item.preparation_status == PreparationStatus.READY for item in positions
                    ):
                        break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("Kafka worker did not finish the prefetch window")
            with factory() as db:
                first_event = db.scalar(
                    select(OutboxEvent)
                    .where(OutboxEvent.game_session_id == created.session_id)
                    .order_by(OutboxEvent.position)
                )
                assert first_event is not None
            before = (media.download_calls, analyzer.calls)
            publisher = KafkaEventPublisher(bootstrap)
            await publisher.start()
            try:
                await publisher.publish(
                    settings.kafka_prepare_topic,
                    first_event.aggregate_id.encode(),
                    first_event.payload_json.encode(),
                )
                await asyncio.sleep(1)
            finally:
                await publisher.close()
            assert (media.download_calls, analyzer.calls) == before
        finally:
            await outbox.close()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(flow())
    advanced = sessions.advance(
        created.session_id, "integration-player", f"next-{created.round_id}"
    )
    assert advanced.position == 1
    assert advanced.round is not None
    assert advanced.round.status.value == "ready"
    assert media.download_calls == analyzer.calls == 2
    engine.dispose()
