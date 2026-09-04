from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

from sqlalchemy import or_, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.domain.enums import AudioStatus, OutboxStatus, PreparationStatus, RoundStatus
from app.domain.events import MediaDeadLetterEvent, MediaPrepareEvent
from app.domain.models import (
    GameRound,
    GameSessionTrack,
    OutboxEvent,
    ProcessedEvent,
    Track,
    utc_now,
)
from app.infrastructure.essentia_analyzer import AnalysisError, KeyAnalyzer
from app.infrastructure.yt_dlp_client import AudioDownloader, ExternalMediaError

logger = logging.getLogger(__name__)
_PERMANENT_MEDIA_CODES = {
    "invalid_video_id",
    "playlist_unavailable",
    "audio_too_large",
    "audio_conversion_failed",
    "audio_missing",
}


class ClaimUnavailable(RuntimeError):
    pass


class MediaProcessor:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        downloader: AudioDownloader,
        analyzer: KeyAnalyzer,
        settings: Settings,
    ) -> None:
        self._sessions = session_factory
        self._downloader = downloader
        self._analyzer = analyzer
        self._settings = settings

    async def handle(self, event: MediaPrepareEvent) -> None:
        if await asyncio.to_thread(self._already_processed, event.event_id):
            return
        token = str(uuid4())
        if not await asyncio.to_thread(self._claim, event.track_id, token):
            await self._retry_or_fail(event, "track_claim_busy", permanent=False)
            return
        started = asyncio.get_running_loop().time()
        try:
            await asyncio.wait_for(
                self._prepare(event, token),
                timeout=self._settings.media_job_timeout_seconds,
            )
            await asyncio.to_thread(self._complete, event, token)
            logger.info(
                "media_prepared",
                extra={
                    "event_id": event.event_id,
                    "correlation_id": event.correlation_id,
                    "track_id": event.track_id,
                    "game_session_id": event.game_session_id,
                    "duration_seconds": round(asyncio.get_running_loop().time() - started, 3),
                },
            )
        except ExternalMediaError as exc:
            await asyncio.to_thread(self._release_claim, event.track_id, token)
            await self._retry_or_fail(event, exc.code, permanent=exc.code in _PERMANENT_MEDIA_CODES)
        except AnalysisError:
            await asyncio.to_thread(self._release_claim, event.track_id, token)
            await self._retry_or_fail(event, "analysis_failed", permanent=False)
        except Exception:
            logger.exception("media_processing_failed", extra={"event_id": event.event_id})
            await asyncio.to_thread(self._release_claim, event.track_id, token)
            await self._retry_or_fail(event, "processing_failed", permanent=False)

    async def _prepare(self, event: MediaPrepareEvent, token: str) -> None:
        cached = await asyncio.to_thread(self._cached_audio, event.track_id)
        if cached is None:
            await asyncio.to_thread(
                self._set_stage,
                event.track_id,
                PreparationStatus.DOWNLOADING,
                RoundStatus.DOWNLOADING,
            )
            work_dir = self._settings.resolved_media_dir / ".work" / token
            downloaded = await self._downloader.download(event.youtube_video_id, work_dir)
            final_path = self._settings.resolved_media_dir / f"{event.youtube_video_id}.mp3"
            await asyncio.to_thread(final_path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(downloaded.replace, final_path)
            await asyncio.to_thread(shutil.rmtree, work_dir, True)
            relative = final_path.relative_to(self._settings.resolved_media_dir)
            await asyncio.to_thread(self._save_audio, event.track_id, str(relative))
            cached = final_path
            logger.info("media_cache_miss", extra={"track_id": event.track_id})
        else:
            logger.info("media_cache_hit", extra={"track_id": event.track_id})
        await asyncio.to_thread(
            self._set_stage,
            event.track_id,
            PreparationStatus.AUDIO_READY,
            RoundStatus.AUDIO_READY,
        )
        if await asyncio.to_thread(self._analysis_current, event.track_id):
            return
        await asyncio.to_thread(
            self._set_stage,
            event.track_id,
            PreparationStatus.ANALYZING,
            RoundStatus.ANALYZING,
        )
        analysis = await asyncio.to_thread(self._analyzer.analyze, cached)
        await asyncio.to_thread(
            self._save_analysis,
            event.track_id,
            analysis.key,
            analysis.scale,
            analysis.confidence,
        )

    def _claim(self, track_id: str, token: str) -> bool:
        now = utc_now()
        lease_until = now + timedelta(seconds=self._settings.media_claim_lease_seconds)
        with self._sessions() as db, db.begin():
            result = cast(
                CursorResult[tuple[object, ...]],
                db.execute(
                    update(Track)
                    .where(
                        Track.id == track_id,
                        or_(
                            Track.processing_claim_token.is_(None),
                            Track.processing_claim_until.is_(None),
                            Track.processing_claim_until < now,
                        ),
                    )
                    .values(processing_claim_token=token, processing_claim_until=lease_until)
                ),
            )
            return result.rowcount == 1

    def _release_claim(self, track_id: str, token: str) -> None:
        with self._sessions() as db, db.begin():
            db.execute(
                update(Track)
                .where(Track.id == track_id, Track.processing_claim_token == token)
                .values(processing_claim_token=None, processing_claim_until=None)
            )

    def _cached_audio(self, track_id: str) -> Path | None:
        with self._sessions() as db, db.begin():
            track = db.get(Track, track_id)
            if track is None:
                raise ExternalMediaError("track_missing", "Faixa não encontrada")
            root = self._settings.resolved_media_dir
            expected = root / f"{track.youtube_video_id}.mp3"
            candidate = (
                (root / track.audio_path).resolve() if track.audio_path else expected.resolve()
            )
            if (
                candidate.parent == root
                and candidate.name == expected.name
                and candidate.is_file()
                and 0 < candidate.stat().st_size <= self._settings.max_audio_bytes
            ):
                track.audio_path = candidate.name
                track.audio_status = AudioStatus.READY
                return candidate
            track.audio_path = None
            track.audio_status = AudioStatus.MISSING
            return None

    def _save_audio(self, track_id: str, relative_path: str) -> None:
        with self._sessions() as db, db.begin():
            track = self._required_track(db, track_id)
            track.audio_path = relative_path
            track.audio_status = AudioStatus.READY

    def _analysis_current(self, track_id: str) -> bool:
        with self._sessions() as db:
            track = db.get(Track, track_id)
            return bool(
                track is not None
                and track.detected_key is not None
                and track.detected_scale is not None
                and track.analysis_version == self._settings.analysis_version
            )

    def _save_analysis(
        self,
        track_id: str,
        key: str,
        scale: object,
        confidence: float | None,
    ) -> None:
        from app.domain.enums import MusicalScale

        if not isinstance(scale, MusicalScale):
            raise TypeError("Escala inválida")
        with self._sessions() as db, db.begin():
            track = self._required_track(db, track_id)
            track.detected_key = key
            track.detected_scale = scale
            track.analysis_confidence = confidence
            track.analysis_version = self._settings.analysis_version
            track.analyzed_at = utc_now()

    def _set_stage(
        self, track_id: str, preparation: PreparationStatus, round_status: RoundStatus
    ) -> None:
        with self._sessions() as db, db.begin():
            db.execute(
                update(GameSessionTrack)
                .where(
                    GameSessionTrack.track_id == track_id,
                    GameSessionTrack.preparation_status != PreparationStatus.READY,
                )
                .values(preparation_status=preparation)
            )
            db.execute(
                update(GameRound)
                .where(
                    GameRound.track_id == track_id,
                    GameRound.status.not_in([RoundStatus.READY, RoundStatus.FAILED]),
                )
                .values(status=round_status, updated_at=utc_now())
            )

    def _complete(self, event: MediaPrepareEvent, token: str) -> None:
        with self._sessions() as db, db.begin():
            if db.get(ProcessedEvent, event.event_id) is not None:
                return
            now = utc_now()
            db.execute(
                update(GameSessionTrack)
                .where(GameSessionTrack.track_id == event.track_id)
                .values(
                    preparation_status=PreparationStatus.READY,
                    ready_at=now,
                    last_error_code=None,
                )
            )
            db.execute(
                update(GameRound)
                .where(GameRound.track_id == event.track_id, GameRound.status != RoundStatus.FAILED)
                .values(status=RoundStatus.READY, error_code=None, updated_at=now)
            )
            db.execute(
                update(Track)
                .where(Track.id == event.track_id, Track.processing_claim_token == token)
                .values(processing_claim_token=None, processing_claim_until=None)
            )
            db.add(ProcessedEvent(event_id=event.event_id))

    async def _retry_or_fail(
        self, event: MediaPrepareEvent, error_code: str, *, permanent: bool
    ) -> None:
        exhausted = permanent or event.attempt >= self._settings.media_max_attempts
        await asyncio.to_thread(self._persist_retry_or_failure, event, error_code, exhausted)

    def _persist_retry_or_failure(
        self, event: MediaPrepareEvent, error_code: str, exhausted: bool
    ) -> None:
        with self._sessions() as db, db.begin():
            if db.get(ProcessedEvent, event.event_id) is not None:
                return
            now = utc_now()
            next_event_id = str(uuid4())
            if exhausted:
                payload = MediaDeadLetterEvent(
                    event_id=next_event_id,
                    occurred_at=now,
                    original_event_id=event.event_id,
                    track_id=event.track_id,
                    game_session_id=event.game_session_id,
                    position=event.position,
                    correlation_id=event.correlation_id,
                    attempt=event.attempt,
                    error_code=error_code[:64],
                ).model_dump_json()
                topic = self._settings.kafka_dlq_topic
                event_type = "media.prepare.failed.v1"
                status = PreparationStatus.FAILED
                round_status = RoundStatus.FAILED
            else:
                retry = event.model_copy(
                    update={
                        "event_id": next_event_id,
                        "event_type": "media.prepare.retry.v1",
                        "occurred_at": now,
                        "attempt": event.attempt + 1,
                    }
                )
                payload = retry.model_dump_json()
                topic = self._settings.kafka_retry_topic
                event_type = retry.event_type
                status = PreparationStatus.ENQUEUED
                round_status = RoundStatus.QUEUED
            db.add(
                OutboxEvent(
                    id=next_event_id,
                    topic=topic,
                    event_type=event_type,
                    aggregate_id=event.track_id,
                    game_session_id=event.game_session_id,
                    position=event.position,
                    dedupe_key=f"followup:{event.event_id}",
                    payload_json=payload,
                    status=OutboxStatus.PENDING,
                    attempt_count=event.attempt,
                    available_at=now
                    + timedelta(
                        seconds=(
                            0
                            if exhausted
                            else min(2**event.attempt, 60) + secrets.SystemRandom().random()
                        )
                    ),
                    last_error_code=error_code[:64],
                )
            )
            db.execute(
                update(GameSessionTrack)
                .where(
                    GameSessionTrack.game_session_id == event.game_session_id,
                    GameSessionTrack.position == event.position,
                )
                .values(
                    preparation_status=status,
                    attempt_count=event.attempt,
                    last_error_code=error_code[:64],
                )
            )
            db.execute(
                update(GameRound)
                .where(
                    GameRound.game_session_id == event.game_session_id,
                    GameRound.session_position == event.position,
                )
                .values(status=round_status, error_code=error_code[:64], updated_at=now)
            )
            db.add(ProcessedEvent(event_id=event.event_id))

    def _already_processed(self, event_id: str) -> bool:
        with self._sessions() as db:
            return db.get(ProcessedEvent, event_id) is not None

    @staticmethod
    def _required_track(db: Session, track_id: str) -> Track:
        track = db.get(Track, track_id)
        if track is None:
            raise ExternalMediaError("track_missing", "Faixa não encontrada")
        return track
