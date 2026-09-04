from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.enums import (
    AudioStatus,
    GameSessionStatus,
    MusicalScale,
    OutboxStatus,
    PreparationStatus,
    RoundResult,
    RoundStatus,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Playlist(Base):
    __tablename__ = "playlists"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    youtube_playlist_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    source_url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str | None] = mapped_column(String(512))
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    track_links: Mapped[list[PlaylistTrack]] = relationship(
        back_populates="playlist", cascade="all, delete-orphan"
    )


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    youtube_video_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    artist: Mapped[str | None] = mapped_column(String(512))
    source_url: Mapped[str] = mapped_column(String(2048))
    thumbnail_url: Mapped[str | None] = mapped_column(String(2048))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    audio_path: Mapped[str | None] = mapped_column(String(512))
    audio_status: Mapped[AudioStatus] = mapped_column(
        Enum(AudioStatus, native_enum=False), default=AudioStatus.MISSING, index=True
    )
    detected_key: Mapped[str | None] = mapped_column(String(8))
    detected_scale: Mapped[MusicalScale | None] = mapped_column(
        Enum(MusicalScale, native_enum=False)
    )
    analysis_confidence: Mapped[float | None] = mapped_column(Float)
    analysis_version: Mapped[str | None] = mapped_column(String(128))
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_claim_token: Mapped[str | None] = mapped_column(String(36), index=True)
    processing_claim_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class PlaylistTrack(Base):
    __tablename__ = "playlist_tracks"
    __table_args__ = (UniqueConstraint("playlist_id", "track_id"),)

    playlist_id: Mapped[str] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"), primary_key=True
    )
    track_id: Mapped[str] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int | None] = mapped_column(Integer)

    playlist: Mapped[Playlist] = relationship(back_populates="track_links")
    track: Mapped[Track] = relationship()


class GameRound(Base):
    __tablename__ = "game_rounds"
    __table_args__ = (
        Index("ix_game_rounds_player_created", "player_id", "created_at"),
        Index("ix_game_rounds_status_updated", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    player_id: Mapped[str] = mapped_column(String(36), index=True)
    playlist_id: Mapped[str] = mapped_column(ForeignKey("playlists.id"), index=True)
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"), index=True)
    game_session_id: Mapped[str | None] = mapped_column(ForeignKey("game_sessions.id"), index=True)
    session_position: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[RoundStatus] = mapped_column(
        Enum(RoundStatus, native_enum=False), default=RoundStatus.QUEUED, index=True
    )
    revealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[RoundResult | None] = mapped_column(Enum(RoundResult, native_enum=False))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    playlist: Mapped[Playlist] = relationship()
    track: Mapped[Track] = relationship()


class GameSession(Base):
    __tablename__ = "game_sessions"
    __table_args__ = (Index("ix_game_sessions_player_created", "player_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    player_id: Mapped[str] = mapped_column(String(36), index=True)
    playlist_id: Mapped[str] = mapped_column(ForeignKey("playlists.id"), index=True)
    status: Mapped[GameSessionStatus] = mapped_column(
        Enum(GameSessionStatus, native_enum=False), default=GameSessionStatus.ACTIVE, index=True
    )
    current_position: Mapped[int] = mapped_column(Integer, default=0)
    shuffle_seed: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    playlist: Mapped[Playlist] = relationship()
    ordered_tracks: Mapped[list[GameSessionTrack]] = relationship(
        back_populates="game_session",
        cascade="all, delete-orphan",
        order_by="GameSessionTrack.position",
    )


class GameSessionTrack(Base):
    __tablename__ = "game_session_tracks"
    __table_args__ = (
        UniqueConstraint("game_session_id", "position"),
        UniqueConstraint("game_session_id", "track_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    game_session_id: Mapped[str] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True
    )
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    preparation_status: Mapped[PreparationStatus] = mapped_column(
        Enum(PreparationStatus, native_enum=False), default=PreparationStatus.PENDING, index=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    game_session: Mapped[GameSession] = relationship(back_populates="ordered_tracks")
    track: Mapped[Track] = relationship()


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_dispatch", "status", "available_at", "created_at"),
        UniqueConstraint("dedupe_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    topic: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(128))
    aggregate_id: Mapped[str] = mapped_column(String(36), index=True)
    game_session_id: Mapped[str] = mapped_column(ForeignKey("game_sessions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    dedupe_key: Mapped[str] = mapped_column(String(255))
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[OutboxStatus] = mapped_column(
        Enum(OutboxStatus, native_enum=False), default=OutboxStatus.PENDING, index=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessedEvent(Base):
    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SessionAdvance(Base):
    __tablename__ = "session_advances"
    __table_args__ = (UniqueConstraint("game_session_id", "idempotency_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    game_session_id: Mapped[str] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    resulting_round_id: Mapped[str | None] = mapped_column(ForeignKey("game_rounds.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
