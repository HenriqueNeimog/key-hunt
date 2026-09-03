from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.enums import AudioStatus, MusicalScale
from app.domain.models import PlaylistTrack, Track
from app.infrastructure.yt_dlp_client import TrackMetadata


class TrackRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, track_id: str) -> Track | None:
        return self._session.get(Track, track_id)

    def get_by_video_id(self, video_id: str) -> Track | None:
        return self._session.scalar(select(Track).where(Track.youtube_video_id == video_id))

    def upsert_metadata(self, item: TrackMetadata) -> Track:
        track = self.get_by_video_id(item.video_id)
        if track is None:
            track = Track(
                youtube_video_id=item.video_id,
                title=item.title,
                artist=item.artist,
                source_url=item.source_url,
                thumbnail_url=item.thumbnail_url,
                duration_seconds=item.duration_seconds,
            )
            self._session.add(track)
            self._session.flush()
            return track
        track.title = item.title
        track.artist = item.artist
        track.source_url = item.source_url
        track.thumbnail_url = item.thumbnail_url
        track.duration_seconds = item.duration_seconds
        return track

    def replace_playlist_links(
        self, playlist_id: str, tracks: list[tuple[Track, int | None]]
    ) -> None:
        self._session.execute(delete(PlaylistTrack).where(PlaylistTrack.playlist_id == playlist_id))
        self._session.add_all(
            PlaylistTrack(playlist_id=playlist_id, track_id=track.id, position=position)
            for track, position in tracks
        )

    def list_for_playlist(self, playlist_id: str) -> list[Track]:
        statement = (
            select(Track)
            .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
            .where(PlaylistTrack.playlist_id == playlist_id)
            .order_by(PlaylistTrack.position.asc().nulls_last())
        )
        return list(self._session.scalars(statement))

    def mark_downloading(self, track: Track) -> None:
        track.audio_status = AudioStatus.DOWNLOADING

    def mark_audio_ready(self, track: Track, relative_path: str) -> None:
        track.audio_path = relative_path
        track.audio_status = AudioStatus.READY

    def mark_audio_missing(self, track: Track) -> None:
        track.audio_path = None
        track.audio_status = AudioStatus.MISSING

    def mark_audio_failed(self, track: Track) -> None:
        track.audio_status = AudioStatus.FAILED

    def save_analysis(
        self,
        track: Track,
        *,
        key: str,
        scale: MusicalScale,
        confidence: float | None,
        version: str,
        analyzed_at: datetime,
    ) -> None:
        track.detected_key = key
        track.detected_scale = scale
        track.analysis_confidence = confidence
        track.analysis_version = version
        track.analyzed_at = analyzed_at
