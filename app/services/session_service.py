from __future__ import annotations

import random
import secrets
from pathlib import Path
from typing import cast
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.domain.enums import (
    AudioStatus,
    GameSessionStatus,
    OutboxStatus,
    PreparationStatus,
    RoundStatus,
)
from app.domain.events import MediaPrepareEvent
from app.domain.models import (
    GameRound,
    GameSession,
    GameSessionTrack,
    OutboxEvent,
    SessionAdvance,
    Track,
    utc_now,
)
from app.domain.schemas import (
    PrefetchStatusResponse,
    SessionCreated,
    SessionState,
)
from app.repositories.playlists import PlaylistRepository
from app.repositories.rounds import RoundRepository
from app.repositories.sessions import GameSessionRepository
from app.repositories.tracks import TrackRepository
from app.services.round_service import ConflictError, NotFoundError, RoundService


class SessionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        rounds: RoundService,
    ) -> None:
        self._sessions = session_factory
        self._settings = settings
        self._rounds = rounds

    def create(self, playlist_id: str, player_id: str, seed: str | None = None) -> SessionCreated:
        with self._sessions() as db, db.begin():
            if PlaylistRepository(db).get(playlist_id) is None:
                raise NotFoundError("Playlist não encontrada")
            tracks = TrackRepository(db).list_for_playlist(playlist_id)
            if not tracks:
                raise ConflictError("A playlist não tem faixas válidas")
            shuffle_seed = seed or secrets.token_hex(32)
            random.Random(shuffle_seed).shuffle(tracks)  # noqa: S311 - reproducible shuffle
            game_session = GameSession(
                player_id=player_id,
                playlist_id=playlist_id,
                shuffle_seed=shuffle_seed,
            )
            db.add(game_session)
            db.flush()
            ordered = [
                GameSessionTrack(
                    game_session_id=game_session.id,
                    track_id=track.id,
                    position=position,
                )
                for position, track in enumerate(tracks)
            ]
            db.add_all(ordered)
            current_round = RoundRepository(db).create(
                player_id=player_id,
                playlist_id=playlist_id,
                track_id=tracks[0].id,
                game_session_id=game_session.id,
                session_position=0,
            )
            self._schedule_window(db, game_session, current_round)
            return SessionCreated(
                round_id=current_round.id,
                playlist_id=playlist_id,
                status=current_round.status,
                session_id=game_session.id,
                position=0,
                total_tracks=len(tracks),
            )

    def state(self, session_id: str, player_id: str) -> SessionState:
        with self._sessions() as db:
            repo = GameSessionRepository(db)
            game_session = repo.get_owned(session_id, player_id)
            if game_session is None:
                raise NotFoundError("Sessão não encontrada")
            total = repo.count(session_id)
            game_round = self._current_round(db, game_session)
            round_state = (
                self._rounds.status(game_round.id, player_id) if game_round is not None else None
            )
            return SessionState(
                session_id=game_session.id,
                status=game_session.status,
                position=game_session.current_position,
                total_tracks=total,
                round=round_state,
                finished=game_session.status == GameSessionStatus.COMPLETED,
            )

    def advance(self, session_id: str, player_id: str, idempotency_key: str) -> SessionState:
        resulting_round_id: str | None = None
        with self._sessions() as db, db.begin():
            repo = GameSessionRepository(db)
            game_session = repo.get_owned(session_id, player_id)
            if game_session is None:
                raise NotFoundError("Sessão não encontrada")
            previous = repo.advance_for_key(session_id, idempotency_key)
            if previous is not None:
                resulting_round_id = previous.resulting_round_id
            else:
                total = repo.count(session_id)
                next_position = game_session.current_position + 1
                if next_position >= total:
                    game_session.status = GameSessionStatus.COMPLETED
                    game_session.completed_at = utc_now()
                    db.add(
                        SessionAdvance(
                            game_session_id=session_id,
                            idempotency_key=idempotency_key,
                            resulting_round_id=None,
                        )
                    )
                else:
                    changed = cast(
                        CursorResult[tuple[object, ...]],
                        db.execute(
                            update(GameSession)
                            .where(
                                GameSession.id == session_id,
                                GameSession.current_position == game_session.current_position,
                            )
                            .values(current_position=next_position, updated_at=utc_now())
                        ),
                    )
                    if changed.rowcount != 1:
                        raise ConflictError("A sessão já avançou; atualize o estado")
                    position = repo.position(session_id, next_position)
                    if position is None:
                        raise RuntimeError("Ordem persistida incompleta")
                    game_round = RoundRepository(db).create(
                        player_id=player_id,
                        playlist_id=game_session.playlist_id,
                        track_id=position.track_id,
                        game_session_id=session_id,
                        session_position=next_position,
                    )
                    self._sync_round_from_position(game_round, position)
                    game_session.current_position = next_position
                    self._schedule_window(db, game_session, game_round)
                    resulting_round_id = game_round.id
                    db.add(
                        SessionAdvance(
                            game_session_id=session_id,
                            idempotency_key=idempotency_key,
                            resulting_round_id=resulting_round_id,
                        )
                    )
        return self.state(session_id, player_id)

    def prefetch_status(self, session_id: str, player_id: str) -> PrefetchStatusResponse:
        with self._sessions() as db:
            repo = GameSessionRepository(db)
            game_session = repo.get_owned(session_id, player_id)
            if game_session is None:
                raise NotFoundError("Sessão não encontrada")
            positions = repo.positions(
                session_id,
                game_session.current_position + 1,
                game_session.current_position + self._settings.prefetch_window_size,
            )
            ready = sum(item.preparation_status == PreparationStatus.READY for item in positions)
            requested = sum(
                item.preparation_status
                in {
                    PreparationStatus.ENQUEUED,
                    PreparationStatus.DOWNLOADING,
                    PreparationStatus.AUDIO_READY,
                    PreparationStatus.ANALYZING,
                    PreparationStatus.READY,
                }
                for item in positions
            )
            return PrefetchStatusResponse(
                ready_ahead=ready,
                requested_ahead=requested,
                next_ready=bool(
                    positions and positions[0].preparation_status == PreparationStatus.READY
                ),
            )

    def _schedule_window(
        self, db: Session, game_session: GameSession, current_round: GameRound
    ) -> None:
        repo = GameSessionRepository(db)
        end = game_session.current_position + self._settings.prefetch_window_size
        for position in repo.positions(game_session.id, game_session.current_position, end):
            if self._cache_is_current(position.track):
                position.preparation_status = PreparationStatus.READY
                position.ready_at = utc_now()
                if position.position == game_session.current_position:
                    current_round.status = RoundStatus.READY
                continue
            if position.preparation_status != PreparationStatus.PENDING:
                continue
            self._enqueue(db, game_session, position)

    def _enqueue(self, db: Session, game_session: GameSession, position: GameSessionTrack) -> None:
        event_id = str(uuid4())
        occurred_at = utc_now()
        event = MediaPrepareEvent(
            event_id=event_id,
            event_type="media.prepare.requested.v1",
            occurred_at=occurred_at,
            track_id=position.track_id,
            youtube_video_id=position.track.youtube_video_id,
            game_session_id=game_session.id,
            position=position.position,
            priority=(
                "current" if position.position == game_session.current_position else "prefetch"
            ),
            correlation_id=game_session.id,
            attempt=1,
        )
        db.add(
            OutboxEvent(
                id=event_id,
                topic=self._settings.kafka_prepare_topic,
                event_type=event.event_type,
                aggregate_id=position.track_id,
                game_session_id=game_session.id,
                position=position.position,
                dedupe_key=f"prepare:{game_session.id}:{position.position}",
                payload_json=event.model_dump_json(),
                status=OutboxStatus.PENDING,
                available_at=occurred_at,
            )
        )
        position.preparation_status = PreparationStatus.ENQUEUED
        position.enqueued_at = occurred_at

    def _cache_is_current(self, track: Track) -> bool:
        if (
            track.audio_status != AudioStatus.READY
            or track.audio_path is None
            or track.detected_key is None
            or track.detected_scale is None
            or track.analysis_version != self._settings.analysis_version
        ):
            return False
        root = self._settings.resolved_media_dir
        path = (root / track.audio_path).resolve()
        return self._valid_cache_path(root, path, track.youtube_video_id)

    def _valid_cache_path(self, root: Path, path: Path, video_id: str) -> bool:
        return (
            path.parent == root
            and path.name == f"{video_id}.mp3"
            and path.is_file()
            and 0 < path.stat().st_size <= self._settings.max_audio_bytes
        )

    @staticmethod
    def _current_round(db: Session, game_session: GameSession) -> GameRound | None:
        return db.scalar(
            select(GameRound)
            .where(
                GameRound.game_session_id == game_session.id,
                GameRound.session_position == game_session.current_position,
            )
            .order_by(GameRound.created_at.desc())
        )

    @staticmethod
    def _sync_round_from_position(game_round: GameRound, position: GameSessionTrack) -> None:
        mapping = {
            PreparationStatus.DOWNLOADING: RoundStatus.DOWNLOADING,
            PreparationStatus.AUDIO_READY: RoundStatus.AUDIO_READY,
            PreparationStatus.ANALYZING: RoundStatus.ANALYZING,
            PreparationStatus.READY: RoundStatus.READY,
            PreparationStatus.FAILED: RoundStatus.FAILED,
        }
        game_round.status = mapping.get(position.preparation_status, RoundStatus.QUEUED)
