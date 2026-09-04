from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.domain.enums import RoundResult, RoundStatus
from app.domain.models import GameRound, utc_now
from app.domain.transitions import can_transition


class RoundRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        player_id: str,
        playlist_id: str,
        track_id: str,
        game_session_id: str | None = None,
        session_position: int | None = None,
    ) -> GameRound:
        game_round = GameRound(
            player_id=player_id,
            playlist_id=playlist_id,
            track_id=track_id,
            game_session_id=game_session_id,
            session_position=session_position,
            status=RoundStatus.QUEUED,
        )
        self._session.add(game_round)
        self._session.flush()
        return game_round

    def get(self, round_id: str) -> GameRound | None:
        statement = (
            select(GameRound)
            .options(joinedload(GameRound.track), joinedload(GameRound.playlist))
            .where(GameRound.id == round_id)
        )
        return self._session.scalar(statement)

    def get_owned(self, round_id: str, player_id: str) -> GameRound | None:
        statement = (
            select(GameRound)
            .options(joinedload(GameRound.track), joinedload(GameRound.playlist))
            .where(GameRound.id == round_id, GameRound.player_id == player_id)
        )
        return self._session.scalar(statement)

    def recent_track_ids(self, player_id: str, playlist_id: str, limit: int = 5) -> list[str]:
        statement = (
            select(GameRound.track_id)
            .where(
                GameRound.player_id == player_id,
                GameRound.playlist_id == playlist_id,
            )
            .order_by(GameRound.created_at.desc())
            .limit(limit)
        )
        return list(self._session.scalars(statement))

    def set_status(
        self, game_round: GameRound, status: RoundStatus, error_code: str | None = None
    ) -> None:
        if not can_transition(game_round.status, status):
            raise ValueError(
                f"Transição de rodada inválida: {game_round.status.value} -> {status.value}"
            )
        game_round.status = status
        game_round.error_code = error_code

    def reveal(self, game_round: GameRound) -> None:
        if game_round.revealed_at is None:
            game_round.revealed_at = utc_now()

    def answer(self, game_round: GameRound, result: RoundResult) -> None:
        game_round.result = result
        game_round.answered_at = utc_now()

    def recoverable_ids(self) -> list[str]:
        statement = select(GameRound.id).where(
            GameRound.status.in_(
                [
                    RoundStatus.QUEUED,
                    RoundStatus.DOWNLOADING,
                    RoundStatus.AUDIO_READY,
                    RoundStatus.ANALYZING,
                ]
            )
        )
        return list(self._session.scalars(statement))
