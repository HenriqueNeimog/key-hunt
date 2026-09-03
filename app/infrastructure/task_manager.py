from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.domain.enums import AudioStatus, RoundStatus
from app.domain.models import Track, utc_now
from app.infrastructure.essentia_analyzer import KeyAnalyzer
from app.infrastructure.yt_dlp_client import AudioDownloader, ExternalMediaError
from app.repositories.rounds import RoundRepository
from app.repositories.tracks import TrackRepository

logger = logging.getLogger(__name__)


class TaskManager:
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
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._track_locks: dict[str, asyncio.Lock] = {}

    def schedule(self, round_id: str) -> None:
        current = self._tasks.get(round_id)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(self._process(round_id), name=f"round-{round_id}")
        self._tasks[round_id] = task
        task.add_done_callback(lambda completed: self._task_finished(round_id, completed))

    async def recover(self) -> None:
        round_ids = await asyncio.to_thread(self._recoverable_round_ids)
        for round_id in round_ids:
            self.schedule(round_id)

    async def close(self) -> None:
        active = [task for task in self._tasks.values() if not task.done()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    def _task_finished(self, round_id: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(round_id, None)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.error(
                "background_round_failed",
                extra={"round_id": round_id, "error_type": type(exception).__name__},
            )

    async def _process(self, round_id: str) -> None:
        try:
            track_id = await asyncio.to_thread(self._track_id_for_round, round_id)
            if track_id is None:
                return
            lock = self._track_locks.setdefault(track_id, asyncio.Lock())
            async with self._semaphore, lock:
                await self._prepare_under_lock(round_id, track_id)
        except asyncio.CancelledError:
            raise
        except ExternalMediaError as exc:
            await asyncio.to_thread(self._fail, round_id, exc.code, True)
        except Exception as exc:
            logger.exception(
                "round_processing_error",
                extra={"round_id": round_id, "error_type": type(exc).__name__},
            )
            await asyncio.to_thread(self._fail, round_id, "processing_failed", False)

    async def _prepare_under_lock(self, round_id: str, track_id: str) -> None:
        await asyncio.to_thread(self._set_round_status, round_id, RoundStatus.DOWNLOADING)
        cached = await asyncio.to_thread(self._cached_audio_path, track_id)
        if cached is None:
            video_id = await asyncio.to_thread(self._video_id, track_id)
            if video_id is None:
                raise RuntimeError("Track disappeared")
            downloaded = await self._downloader.download(
                video_id, self._settings.resolved_media_dir
            )
            relative = downloaded.resolve().relative_to(self._settings.resolved_media_dir)
            await asyncio.to_thread(self._save_audio, track_id, str(relative))
            cached = downloaded
        await asyncio.to_thread(self._set_round_status, round_id, RoundStatus.AUDIO_READY)
        await asyncio.sleep(0)
        analysis_cached = await asyncio.to_thread(self._analysis_is_current, track_id)
        if analysis_cached:
            await asyncio.to_thread(self._set_round_status, round_id, RoundStatus.READY)
            return
        await asyncio.to_thread(self._set_round_status, round_id, RoundStatus.ANALYZING)
        analysis = await asyncio.to_thread(self._analyzer.analyze, cached)
        await asyncio.to_thread(
            self._save_analysis,
            track_id,
            analysis.key,
            analysis.scale,
            analysis.confidence,
        )
        await asyncio.to_thread(self._set_round_status, round_id, RoundStatus.READY)

    def _recoverable_round_ids(self) -> list[str]:
        with self._sessions() as session:
            return RoundRepository(session).recoverable_ids()

    def _track_id_for_round(self, round_id: str) -> str | None:
        with self._sessions() as session:
            game_round = RoundRepository(session).get(round_id)
            return game_round.track_id if game_round is not None else None

    def _video_id(self, track_id: str) -> str | None:
        with self._sessions() as session:
            track = TrackRepository(session).get(track_id)
            return track.youtube_video_id if track is not None else None

    def _cached_audio_path(self, track_id: str) -> Path | None:
        with self._sessions() as session, session.begin():
            repo = TrackRepository(session)
            track = repo.get(track_id)
            if track is None or track.audio_path is None:
                return None
            root = self._settings.resolved_media_dir
            candidate = (root / track.audio_path).resolve()
            expected_name = f"{track.youtube_video_id}.mp3"
            if (
                candidate.parent == root
                and candidate.name == expected_name
                and candidate.is_file()
                and 0 < candidate.stat().st_size <= self._settings.max_audio_bytes
            ):
                if track.audio_status != AudioStatus.READY:
                    track.audio_status = AudioStatus.READY
                return candidate
            repo.mark_audio_missing(track)
            return None

    def _save_audio(self, track_id: str, relative_path: str) -> None:
        with self._sessions() as session, session.begin():
            track = self._required_track(session, track_id)
            TrackRepository(session).mark_audio_ready(track, relative_path)

    def _analysis_is_current(self, track_id: str) -> bool:
        with self._sessions() as session:
            track = TrackRepository(session).get(track_id)
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
            raise TypeError("Invalid scale")
        with self._sessions() as session, session.begin():
            track = self._required_track(session, track_id)
            TrackRepository(session).save_analysis(
                track,
                key=key,
                scale=scale,
                confidence=confidence,
                version=self._settings.analysis_version,
                analyzed_at=utc_now(),
            )

    def _set_round_status(self, round_id: str, status: RoundStatus) -> None:
        with self._sessions() as session, session.begin():
            repo = RoundRepository(session)
            game_round = repo.get(round_id)
            if game_round is not None:
                repo.set_status(game_round, status)

    def _fail(self, round_id: str, error_code: str, audio_failure: bool) -> None:
        with self._sessions() as session, session.begin():
            rounds = RoundRepository(session)
            game_round = rounds.get(round_id)
            if game_round is None:
                return
            rounds.set_status(game_round, RoundStatus.FAILED, error_code)
            if audio_failure:
                TrackRepository(session).mark_audio_failed(game_round.track)

    @staticmethod
    def _required_track(session: Session, track_id: str) -> Track:
        track = TrackRepository(session).get(track_id)
        if track is None:
            raise RuntimeError("Track disappeared")
        return track
