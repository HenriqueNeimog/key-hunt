from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, sessionmaker

from app.domain.enums import RoundResult
from app.domain.models import GameRound
from app.domain.schemas import HistoryEntry, KeyPerformance, StatsResponse


class StatsService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def for_player(self, player_id: str) -> StatsResponse:
        with self._sessions() as session:
            statement = (
                select(GameRound)
                .options(joinedload(GameRound.track))
                .where(
                    GameRound.player_id == player_id,
                    GameRound.result.is_not(None),
                )
                .order_by(GameRound.answered_at.desc())
            )
            rounds = list(session.scalars(statement))
        correct = sum(item.result == RoundResult.CORRECT for item in rounds)
        totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        recent: list[HistoryEntry] = []
        for item in rounds:
            key = item.track.detected_key
            scale = item.track.detected_scale
            if key is None or scale is None or item.result is None or item.answered_at is None:
                continue
            label = f"{key} {scale.value}"
            totals[label][0] += 1
            totals[label][1] += int(item.result == RoundResult.CORRECT)
            if len(recent) < 10:
                recent.append(
                    HistoryEntry(
                        answered_at=item.answered_at,
                        title=item.track.title,
                        artist=item.track.artist,
                        result=item.result,
                        key=key,
                        scale=scale,
                    )
                )
        by_key = [
            KeyPerformance(
                key=key,
                total=values[0],
                correct=values[1],
                accuracy=round(values[1] / values[0] * 100, 1),
            )
            for key, values in sorted(totals.items())
        ]
        total = len(rounds)
        return StatsResponse(
            total=total,
            correct=correct,
            incorrect=total - correct,
            accuracy=round(correct / total * 100, 1) if total else 0.0,
            by_key=by_key,
            recent=recent,
        )
