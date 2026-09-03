from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Playlist


class PlaylistRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, playlist_id: str) -> Playlist | None:
        return self._session.get(Playlist, playlist_id)

    def get_by_youtube_id(self, youtube_playlist_id: str) -> Playlist | None:
        statement = select(Playlist).where(Playlist.youtube_playlist_id == youtube_playlist_id)
        return self._session.scalar(statement)

    def create_or_update(
        self,
        *,
        youtube_playlist_id: str,
        source_url: str,
        title: str | None,
        synced_at: datetime,
    ) -> Playlist:
        playlist = self.get_by_youtube_id(youtube_playlist_id)
        if playlist is None:
            playlist = Playlist(
                youtube_playlist_id=youtube_playlist_id,
                source_url=source_url,
                title=title,
                last_synced_at=synced_at,
            )
            self._session.add(playlist)
            self._session.flush()
            return playlist
        playlist.source_url = source_url
        playlist.title = title
        playlist.last_synced_at = synced_at
        return playlist
