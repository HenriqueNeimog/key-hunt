from __future__ import annotations

import random
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.domain.enums import RoundResult, RoundStatus
from app.domain.models import GameRound
from app.domain.schemas import (
    RevealResponse,
    RoundCreated,
    RoundResultResponse,
    RoundStatusResponse,
    TrackPublic,
)
from app.repositories.playlists import PlaylistRepository
from app.repositories.rounds import RoundRepository
from app.repositories.tracks import TrackRepository


class NotFoundError(RuntimeError):
    pass


class ConflictError(RuntimeError):
    pass


class RoundService:
    def __init__(self, session_factory: sessionmaker[Session], settings: Settings) -> None:
        self._sessions = session_factory
        self._settings = settings

    def create_round(self, playlist_id: str, player_id: str) -> RoundCreated:
        with self._sessions() as session, session.begin():
            if PlaylistRepository(session).get(playlist_id) is None:
                raise NotFoundError("Playlist não encontrada")
            tracks = TrackRepository(session).list_for_playlist(playlist_id)
            if not tracks:
                raise ConflictError("A playlist não tem faixas válidas")
            rounds = RoundRepository(session)
            recent = set(rounds.recent_track_ids(player_id, playlist_id))
            candidates = [track for track in tracks if track.id not in recent] or tracks
            track = random.SystemRandom().choice(candidates)
            game_round = rounds.create(
                player_id=player_id, playlist_id=playlist_id, track_id=track.id
            )
            return RoundCreated(
                round_id=game_round.id,
                playlist_id=playlist_id,
                status=game_round.status,
            )

    def status(self, round_id: str, player_id: str) -> RoundStatusResponse:
        with self._sessions() as session:
            game_round = self._owned(RoundRepository(session), round_id, player_id)
            audio_available = game_round.status in {
                RoundStatus.AUDIO_READY,
                RoundStatus.ANALYZING,
                RoundStatus.READY,
            }
            error = (
                "Não foi possível preparar esta faixa. Tente outra música."
                if game_round.status == RoundStatus.FAILED
                else None
            )
            return RoundStatusResponse(
                round_id=game_round.id,
                playlist_id=game_round.playlist_id,
                status=game_round.status,
                track=TrackPublic(
                    title=game_round.track.title,
                    artist=game_round.track.artist,
                    thumbnail_url=game_round.track.thumbnail_url,
                    duration_seconds=game_round.track.duration_seconds,
                ),
                audio_url=f"/api/rounds/{game_round.id}/audio" if audio_available else None,
                can_reveal=game_round.status == RoundStatus.READY,
                error=error,
            )

    def reveal(self, round_id: str, player_id: str) -> RevealResponse:
        with self._sessions() as session, session.begin():
            repo = RoundRepository(session)
            game_round = self._owned(repo, round_id, player_id)
            if game_round.status != RoundStatus.READY:
                raise ConflictError("A análise ainda não terminou")
            track = game_round.track
            if track.detected_key is None or track.detected_scale is None:
                raise ConflictError("A análise ainda não está disponível")
            repo.reveal(game_round)
            confidence = track.analysis_confidence
            return RevealResponse(
                key=track.detected_key,
                scale=track.detected_scale,
                confidence=confidence,
                low_confidence=(
                    confidence is None or confidence < self._settings.low_confidence_threshold
                ),
            )

    def answer(self, round_id: str, player_id: str, result: RoundResult) -> RoundResultResponse:
        with self._sessions() as session, session.begin():
            repo = RoundRepository(session)
            game_round = self._owned(repo, round_id, player_id)
            if game_round.revealed_at is None:
                raise ConflictError("Revele o tom antes de responder")
            if game_round.result is not None:
                raise ConflictError("Esta rodada já foi respondida")
            repo.answer(game_round, result)
            return RoundResultResponse(result=result)

    def audio_path(self, round_id: str, player_id: str) -> Path:
        with self._sessions() as session:
            game_round = self._owned(RoundRepository(session), round_id, player_id)
            relative = game_round.track.audio_path
            if relative is None:
                raise ConflictError("O áudio ainda não está pronto")
            media_root = self._settings.resolved_media_dir
            candidate = (media_root / relative).resolve()
            if candidate.parent != media_root or not candidate.is_file():
                raise NotFoundError("Arquivo de áudio indisponível")
            return candidate

    @staticmethod
    def _owned(repo: RoundRepository, round_id: str, player_id: str) -> GameRound:
        game_round = repo.get_owned(round_id, player_id)
        if game_round is None:
            raise NotFoundError("Rodada não encontrada")
        return game_round
