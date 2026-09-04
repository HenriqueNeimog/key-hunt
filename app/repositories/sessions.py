from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.domain.models import GameSession, GameSessionTrack, SessionAdvance


class GameSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, session_id: str) -> GameSession | None:
        return self._session.get(GameSession, session_id)

    def get_owned(self, session_id: str, player_id: str) -> GameSession | None:
        statement = select(GameSession).where(
            GameSession.id == session_id, GameSession.player_id == player_id
        )
        return self._session.scalar(statement)

    def position(self, session_id: str, position: int) -> GameSessionTrack | None:
        statement = (
            select(GameSessionTrack)
            .options(joinedload(GameSessionTrack.track))
            .where(
                GameSessionTrack.game_session_id == session_id,
                GameSessionTrack.position == position,
            )
        )
        return self._session.scalar(statement)

    def positions(self, session_id: str, start: int, end: int) -> list[GameSessionTrack]:
        statement = (
            select(GameSessionTrack)
            .options(joinedload(GameSessionTrack.track))
            .where(
                GameSessionTrack.game_session_id == session_id,
                GameSessionTrack.position >= start,
                GameSessionTrack.position <= end,
            )
            .order_by(GameSessionTrack.position)
        )
        return list(self._session.scalars(statement))

    def count(self, session_id: str) -> int:
        statement = (
            select(func.count())
            .select_from(GameSessionTrack)
            .where(GameSessionTrack.game_session_id == session_id)
        )
        return int(self._session.scalar(statement) or 0)

    def advance_for_key(self, session_id: str, key: str) -> SessionAdvance | None:
        return self._session.scalar(
            select(SessionAdvance).where(
                SessionAdvance.game_session_id == session_id,
                SessionAdvance.idempotency_key == key,
            )
        )
