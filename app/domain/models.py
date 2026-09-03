from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain.enums import AudioStatus, MusicalScale, RoundResult, RoundStatus


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
