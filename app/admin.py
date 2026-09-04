from __future__ import annotations

import sys
from uuid import UUID, uuid4

from sqlalchemy import select

from app.config import get_settings
from app.domain.enums import OutboxStatus, PreparationStatus
from app.domain.events import MediaDeadLetterEvent, MediaPrepareEvent
from app.domain.models import GameSession, GameSessionTrack, OutboxEvent, Track, utc_now
from app.infrastructure.db import create_db_engine, create_session_factory


def requeue_dlq_event(event_id: str) -> str:
    UUID(event_id)
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    sessions = create_session_factory(engine)
    with sessions() as db, db.begin():
        dead_letter = db.get(OutboxEvent, event_id)
        if dead_letter is None or dead_letter.topic != settings.kafka_dlq_topic:
            raise ValueError("Evento DLQ não encontrado")
        payload = MediaDeadLetterEvent.model_validate_json(dead_letter.payload_json, strict=True)
        track = db.get(Track, payload.track_id)
        game_session = db.get(GameSession, payload.game_session_id)
        position = db.scalar(
            select(GameSessionTrack).where(
                GameSessionTrack.game_session_id == payload.game_session_id,
                GameSessionTrack.position == payload.position,
            )
        )
        if track is None or game_session is None:
            raise ValueError("Agregado do evento DLQ não está mais disponível")
        if position is None:
            raise ValueError("Posição da sessão não está mais disponível")
        replay_id = str(uuid4())
        now = utc_now()
        event = MediaPrepareEvent(
            event_id=replay_id,
            event_type="media.prepare.requested.v1",
            occurred_at=now,
            track_id=track.id,
            youtube_video_id=track.youtube_video_id,
            game_session_id=game_session.id,
            position=position.position,
            priority=(
                "current" if position.position == game_session.current_position else "prefetch"
            ),
            correlation_id=payload.correlation_id,
            attempt=1,
        )
        db.add(
            OutboxEvent(
                id=replay_id,
                topic=settings.kafka_prepare_topic,
                event_type=event.event_type,
                aggregate_id=track.id,
                game_session_id=game_session.id,
                position=position.position,
                dedupe_key=f"replay:{dead_letter.id}",
                payload_json=event.model_dump_json(),
                status=OutboxStatus.PENDING,
                available_at=now,
            )
        )
        position.preparation_status = PreparationStatus.ENQUEUED
        position.last_error_code = None
    engine.dispose()
    return replay_id


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "requeue-dlq":
        raise SystemExit("uso: python -m app.admin requeue-dlq EVENT_ID")
    try:
        replay_id = requeue_dlq_event(sys.argv[2])
    except (ValueError, TypeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(replay_id)


if __name__ == "__main__":
    main()
