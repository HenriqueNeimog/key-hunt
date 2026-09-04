from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.domain.enums import MusicalScale, OutboxStatus, PreparationStatus
from app.domain.events import MediaPrepareEvent
from app.domain.models import (
    Base,
    GameSession,
    GameSessionTrack,
    OutboxEvent,
    Playlist,
    PlaylistTrack,
    ProcessedEvent,
    Track,
    utc_now,
)
from app.infrastructure.db import create_db_engine, create_session_factory
from app.infrastructure.essentia_analyzer import KeyAnalysis
from app.infrastructure.media_processor import MediaProcessor
from app.infrastructure.messaging import EventPublisher, OutboxPublisher
from app.infrastructure.yt_dlp_client import ExternalMediaError
from app.services.round_service import RoundService
from app.services.session_service import SessionService
from tests.fakes import FakeAnalyzer, FakeMediaClient
from tests.test_api_flow import import_round, wait_ready


def _session_services(
    tmp_path: Path,
) -> tuple[SessionService, sessionmaker[Session], Settings, str]:
    database = tmp_path / "sessions.db"
    settings = Settings(
        database_url=f"sqlite:///{database.as_posix()}",
        media_dir=tmp_path / "media",
        secret_key="test-secret-with-more-than-thirty-two-characters",
        prefetch_window_size=3,
    )
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as db, db.begin():
        playlist = Playlist(youtube_playlist_id="PLsessiontest", source_url="https://youtube.test")
        tracks = [
            Track(
                youtube_video_id=f"session{i:05d}",
                title=f"Track {i}",
                source_url="https://youtube.test",
            )
            for i in range(6)
        ]
        db.add_all([playlist, *tracks])
        db.flush()
        db.add_all(
            PlaylistTrack(playlist_id=playlist.id, track_id=track.id, position=index)
            for index, track in enumerate(tracks)
        )
        playlist_id = playlist.id
    rounds = RoundService(factory, settings)
    return SessionService(factory, settings, rounds), factory, settings, playlist_id


def test_seeded_orders_are_immutable_and_sessions_can_differ(tmp_path: Path) -> None:
    service, factory, _, playlist_id = _session_services(tmp_path)
    first = service.create(playlist_id, "player", seed="first-seed")
    second = service.create(playlist_id, "player", seed="second-seed")
    with factory() as db:
        orders = []
        for session_id in (first.session_id, second.session_id):
            orders.append(
                list(
                    db.scalars(
                        select(GameSessionTrack.track_id)
                        .where(GameSessionTrack.game_session_id == session_id)
                        .order_by(GameSessionTrack.position)
                    )
                )
            )
        assert orders[0] != orders[1]
        assert all(len(order) == len(set(order)) == 6 for order in orders)


def test_window_contains_current_plus_three_and_replenishes_one(tmp_path: Path) -> None:
    service, factory, _, playlist_id = _session_services(tmp_path)
    created = service.create(playlist_id, "player", seed="window-seed")
    with factory() as db:
        initial = list(
            db.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.game_session_id == created.session_id)
                .order_by(OutboxEvent.position)
            )
        )
        assert [item.position for item in initial] == [0, 1, 2, 3]
    state = service.advance(created.session_id, "player", "advance-key-0001")
    assert state.position == 1
    with factory() as db:
        positions = list(
            db.scalars(
                select(OutboxEvent.position)
                .where(OutboxEvent.game_session_id == created.session_id)
                .order_by(OutboxEvent.position)
            )
        )
        assert positions == [0, 1, 2, 3, 4]


def test_advance_does_not_duplicate_outbox_for_failed_prefetch(tmp_path: Path) -> None:
    service, factory, _, playlist_id = _session_services(tmp_path)
    created = service.create(playlist_id, "player", seed="failed-prefetch-seed")
    with factory() as db, db.begin():
        failed = db.scalar(
            select(GameSessionTrack).where(
                GameSessionTrack.game_session_id == created.session_id,
                GameSessionTrack.position == 3,
            )
        )
        assert failed is not None
        failed.preparation_status = PreparationStatus.FAILED

    state = service.advance(created.session_id, "player", "advance-after-failure")

    assert state.position == 1
    with factory() as db:
        failed_events = list(
            db.scalars(
                select(OutboxEvent).where(
                    OutboxEvent.game_session_id == created.session_id,
                    OutboxEvent.position == 3,
                )
            )
        )
        assert len(failed_events) == 1


class FailureDownloader:
    def __init__(self, code: str) -> None:
        self.code = code

    async def download(self, video_id: str, destination_dir: Path) -> Path:
        del video_id, destination_dir
        raise ExternalMediaError(self.code, "sanitized")


def test_transient_failure_retries_and_permanent_failure_goes_to_dlq(tmp_path: Path) -> None:
    service, factory, settings, playlist_id = _session_services(tmp_path)
    first = service.create(playlist_id, "player-a", seed="retry-seed")
    with factory() as db:
        original = db.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.game_session_id == first.session_id)
            .order_by(OutboxEvent.position)
        )
        assert original is not None
        transient_event = MediaPrepareEvent.model_validate_json(original.payload_json, strict=True)
    transient = MediaProcessor(
        session_factory=factory,
        downloader=FailureDownloader("media_timeout"),
        analyzer=FakeAnalyzer(),
        settings=settings,
    )
    asyncio.run(transient.handle(transient_event))
    with factory() as db:
        retry = db.scalar(
            select(OutboxEvent).where(OutboxEvent.dedupe_key == f"followup:{original.id}")
        )
        assert retry is not None
        assert retry.topic == settings.kafka_retry_topic
        assert "sanitized" not in retry.payload_json

    second = service.create(playlist_id, "player-b", seed="dlq-seed")
    with factory() as db:
        original = db.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.game_session_id == second.session_id)
            .order_by(OutboxEvent.position)
        )
        assert original is not None
        permanent_event = MediaPrepareEvent.model_validate_json(original.payload_json, strict=True)
    permanent = MediaProcessor(
        session_factory=factory,
        downloader=FailureDownloader("invalid_video_id"),
        analyzer=FakeAnalyzer(),
        settings=settings,
    )
    asyncio.run(permanent.handle(permanent_event))
    with factory() as db:
        dlq = db.scalar(
            select(OutboxEvent).where(OutboxEvent.dedupe_key == f"followup:{original.id}")
        )
        assert dlq is not None
        assert dlq.topic == settings.kafka_dlq_topic


def test_expired_media_claim_can_be_recovered(tmp_path: Path) -> None:
    service, factory, settings, playlist_id = _session_services(tmp_path)
    created = service.create(playlist_id, "player", seed="lease-seed")
    with factory() as db:
        position = db.scalar(
            select(GameSessionTrack).where(
                GameSessionTrack.game_session_id == created.session_id,
                GameSessionTrack.position == 0,
            )
        )
        assert position is not None
        track_id = position.track_id
    processor = MediaProcessor(
        session_factory=factory,
        downloader=FakeMediaClient(),
        analyzer=FakeAnalyzer(),
        settings=settings,
    )
    assert processor._claim(track_id, "00000000-0000-0000-0000-000000000001") is True
    assert processor._claim(track_id, "00000000-0000-0000-0000-000000000002") is False
    with factory() as db, db.begin():
        track = db.get(Track, track_id)
        assert track is not None
        track.processing_claim_until = utc_now() - timedelta(seconds=1)
    assert processor._claim(track_id, "00000000-0000-0000-0000-000000000002") is True


class InspectingAnalyzer:
    def __init__(self, factory: sessionmaker[Session], track_id: str) -> None:
        self.factory = factory
        self.track_id = track_id
        self.saw_final_audio = False

    def analyze(self, audio_path: Path) -> KeyAnalysis:
        with self.factory() as db:
            track = db.get(Track, self.track_id)
            self.saw_final_audio = bool(
                track is not None
                and track.audio_path is not None
                and audio_path.name == f"{track.youtube_video_id}.mp3"
                and ".work" not in str(audio_path)
            )
        return KeyAnalysis(key="C", scale=MusicalScale.MAJOR, confidence=0.9)


def test_final_audio_is_available_before_analysis_finishes(tmp_path: Path) -> None:
    service, factory, settings, playlist_id = _session_services(tmp_path)
    created = service.create(playlist_id, "player", seed="audio-stage-seed")
    with factory() as db:
        original = db.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.game_session_id == created.session_id)
            .order_by(OutboxEvent.position)
        )
        assert original is not None
        event = MediaPrepareEvent.model_validate_json(original.payload_json, strict=True)
    analyzer = InspectingAnalyzer(factory, event.track_id)
    processor = MediaProcessor(
        session_factory=factory,
        downloader=FakeMediaClient(),
        analyzer=analyzer,
        settings=settings,
    )
    asyncio.run(processor.handle(event))
    assert analyzer.saw_final_audio is True


def test_game_markup_has_custom_player_and_native_fallback(client: TestClient) -> None:
    page = client.get("/game/example-round")
    assert page.status_code == 200
    assert '<audio id="audio-player" class="audio-engine" preload="metadata" controls>' in page.text
    assert 'id="player-progress"' in page.text
    assert 'aria-label="Voltar 5 segundos"' in page.text


def _factory(client: TestClient):  # type: ignore[no-untyped-def]
    return client.app.state.container.game_sessions._sessions


def test_session_persists_complete_private_order_and_prefetches(
    client: TestClient, csrf_headers: dict[str, str]
) -> None:
    created = import_round(client, csrf_headers)
    session_id = str(created["session_id"])
    round_id = str(created["round_id"])
    wait_ready(client, round_id)
    for _ in range(100):
        prefetch = client.get(f"/api/sessions/{session_id}/prefetch-status")
        assert prefetch.status_code == 200
        payload = prefetch.json()
        if payload["ready_ahead"] == payload["requested_ahead"]:
            break
        time.sleep(0.01)
    else:
        raise AssertionError("prefetch window did not become ready")
    factory = _factory(client)
    with factory() as db:
        game_session = db.get(GameSession, session_id)
        assert game_session is not None
        original_seed = game_session.shuffle_seed
        positions = list(
            db.scalars(
                select(GameSessionTrack)
                .where(GameSessionTrack.game_session_id == session_id)
                .order_by(GameSessionTrack.position)
            )
        )
        assert [item.position for item in positions] == list(range(len(positions)))
        assert len({item.track_id for item in positions}) == len(positions)
        assert all(item.preparation_status == PreparationStatus.READY for item in positions)
    with factory() as db:
        persisted = db.get(GameSession, session_id)
        assert persisted is not None
        assert persisted.shuffle_seed == original_seed
    public = client.get(f"/api/sessions/{session_id}")
    assert public.status_code == 200
    lowered = public.text.lower()
    assert "shuffle_seed" not in lowered
    assert "detected_key" not in lowered
    assert "detected_scale" not in lowered


def test_next_is_idempotent_and_prefetch_is_generic(
    client: TestClient, csrf_headers: dict[str, str]
) -> None:
    created = import_round(client, csrf_headers)
    session_id = str(created["session_id"])
    wait_ready(client, str(created["round_id"]))
    body = {"idempotency_key": f"next-{created['round_id']}"}
    first = client.post(f"/api/sessions/{session_id}/next", json=body, headers=csrf_headers)
    second = client.post(f"/api/sessions/{session_id}/next", json=body, headers=csrf_headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["position"] == second.json()["position"] == 1
    assert first.json()["round"]["round_id"] == second.json()["round"]["round_id"]
    prefetch = client.get(f"/api/sessions/{session_id}/prefetch-status")
    assert prefetch.status_code == 200
    assert set(prefetch.json()) == {"ready_ahead", "requested_ahead", "next_ready"}
    assert "track" not in prefetch.text.lower()
    assert "title" not in prefetch.text.lower()


class FailingPublisher(EventPublisher):
    def __init__(self) -> None:
        self.calls = 0

    async def start(self) -> None:
        return None

    async def publish(self, topic: str, key: bytes, value: bytes) -> None:
        del topic, key, value
        self.calls += 1
        raise ConnectionError("broker unavailable")

    async def close(self) -> None:
        return None


def test_kafka_unavailability_releases_event_without_loss(
    client: TestClient, csrf_headers: dict[str, str]
) -> None:
    created = import_round(client, csrf_headers)
    factory = _factory(client)
    with factory() as db, db.begin():
        event = db.scalar(
            select(OutboxEvent).where(OutboxEvent.game_session_id == created["session_id"])
        )
        assert event is not None
        event.status = OutboxStatus.PENDING
        event.available_at = utc_now()
    publisher = FailingPublisher()
    settings = client.app.state.container.settings
    outbox = OutboxPublisher(factory, publisher, settings)
    assert asyncio.run(outbox.drain_once()) >= 1
    with factory() as db:
        event = db.scalar(
            select(OutboxEvent).where(OutboxEvent.game_session_id == created["session_id"])
        )
        assert event is not None
        assert event.status == OutboxStatus.PENDING
        assert event.attempt_count >= 1


def test_abandoned_outbox_claim_is_recovered(
    client: TestClient, csrf_headers: dict[str, str]
) -> None:
    created = import_round(client, csrf_headers)
    factory = _factory(client)
    with factory() as db, db.begin():
        event = db.scalar(
            select(OutboxEvent).where(OutboxEvent.game_session_id == created["session_id"])
        )
        assert event is not None
        event.status = OutboxStatus.PUBLISHING
        event.claimed_at = utc_now() - timedelta(minutes=10)
        event.available_at = utc_now() - timedelta(minutes=10)
        event_id = event.id
    from app.repositories.outbox import OutboxRepository

    with factory() as db, db.begin():
        claimed = OutboxRepository(db).claim_batch(10, 30)
        assert event_id in {item.id for item in claimed}


def test_media_redelivery_is_idempotent(
    client: TestClient,
    csrf_headers: dict[str, str],
    fake_media: FakeMediaClient,
    fake_analyzer: FakeAnalyzer,
) -> None:
    created = import_round(client, csrf_headers)
    wait_ready(client, str(created["round_id"]))
    for _ in range(100):
        prefetch = client.get(f"/api/sessions/{created['session_id']}/prefetch-status").json()
        if prefetch["ready_ahead"] == prefetch["requested_ahead"]:
            break
        time.sleep(0.01)
    else:
        raise AssertionError("prefetch window did not become ready")
    factory = _factory(client)
    with factory() as db:
        outbox = db.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.game_session_id == created["session_id"])
            .order_by(OutboxEvent.created_at)
        )
        assert outbox is not None
        event = MediaPrepareEvent.model_validate_json(outbox.payload_json, strict=True)
        assert db.get(ProcessedEvent, event.event_id) is not None
    before = (fake_media.download_calls, fake_analyzer.calls)
    settings: Settings = client.app.state.container.settings
    processor = MediaProcessor(
        session_factory=factory,
        downloader=fake_media,
        analyzer=fake_analyzer,
        settings=settings,
    )
    asyncio.run(processor.handle(event))
    assert (fake_media.download_calls, fake_analyzer.calls) == before
