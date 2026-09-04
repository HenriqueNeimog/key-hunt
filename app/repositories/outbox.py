from __future__ import annotations

from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.domain.enums import OutboxStatus
from app.domain.models import OutboxEvent, utc_now


class OutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def claim_batch(self, limit: int, claim_timeout_seconds: int) -> list[OutboxEvent]:
        now = utc_now()
        abandoned = now - timedelta(seconds=claim_timeout_seconds)
        ids = list(
            self._session.scalars(
                select(OutboxEvent.id)
                .where(
                    OutboxEvent.available_at <= now,
                    or_(
                        OutboxEvent.status == OutboxStatus.PENDING,
                        (OutboxEvent.status == OutboxStatus.PUBLISHING)
                        & (OutboxEvent.claimed_at < abandoned),
                    ),
                )
                .order_by(OutboxEvent.created_at)
                .limit(limit)
            )
        )
        if not ids:
            return []
        self._session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id.in_(ids))
            .values(status=OutboxStatus.PUBLISHING, claimed_at=now)
        )
        self._session.flush()
        return list(self._session.scalars(select(OutboxEvent).where(OutboxEvent.id.in_(ids))))

    def mark_published(self, event_id: str) -> None:
        event = self._session.get(OutboxEvent, event_id)
        if event is not None:
            event.status = OutboxStatus.PUBLISHED
            event.published_at = utc_now()
            event.claimed_at = None

    def release(self, event_id: str, error_code: str, delay_seconds: float) -> None:
        event = self._session.get(OutboxEvent, event_id)
        if event is not None:
            event.status = OutboxStatus.PENDING
            event.attempt_count += 1
            event.last_error_code = error_code
            event.claimed_at = None
            event.available_at = utc_now() + timedelta(seconds=delay_seconds)
