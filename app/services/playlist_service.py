from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.domain.models import Playlist, utc_now
from app.infrastructure.yt_dlp_client import PlaylistMetadataClient
from app.repositories.playlists import PlaylistRepository
from app.repositories.tracks import TrackRepository
from app.services.url_security import normalize_playlist_url


class PlaylistService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        metadata_client: PlaylistMetadataClient,
        settings: Settings,
    ) -> None:
        self._sessions = session_factory
        self._metadata_client = metadata_client
        self._settings = settings

    async def import_playlist(self, raw_url: str) -> str:
        normalized = normalize_playlist_url(raw_url)
        cached_id = await asyncio.to_thread(self._fresh_cached_id, normalized.playlist_id)
        if cached_id is not None:
            return cached_id
        metadata = await self._metadata_client.fetch_playlist(
            normalized.url, self._settings.max_playlist_items
        )
        if metadata.playlist_id != normalized.playlist_id:
            raise ValueError("A playlist retornada não corresponde à URL solicitada")
        return await asyncio.to_thread(self._persist, normalized.url, metadata)

    def _fresh_cached_id(self, youtube_id: str) -> str | None:
        with self._sessions() as session:
            playlist = PlaylistRepository(session).get_by_youtube_id(youtube_id)
            if playlist is None:
                return None
            synced = playlist.last_synced_at
            if synced.tzinfo is None:
                synced = synced.replace(tzinfo=utc_now().tzinfo)
            age = utc_now() - synced
            has_tracks = bool(TrackRepository(session).list_for_playlist(playlist.id))
            if age <= timedelta(seconds=self._settings.playlist_cache_ttl_seconds) and has_tracks:
                return playlist.id
            return None

    def _persist(self, source_url: str, metadata: object) -> str:
        from app.infrastructure.yt_dlp_client import PlaylistMetadata

        if not isinstance(metadata, PlaylistMetadata):
            raise TypeError("Metadados de playlist inválidos")
        with self._sessions() as session, session.begin():
            playlist_repo = PlaylistRepository(session)
            track_repo = TrackRepository(session)
            playlist: Playlist = playlist_repo.create_or_update(
                youtube_playlist_id=metadata.playlist_id,
                source_url=source_url,
                title=metadata.title,
                synced_at=utc_now(),
            )
            links = []
            for item in metadata.tracks:
                if (
                    item.duration_seconds is not None
                    and item.duration_seconds > self._settings.max_track_duration_seconds
                ):
                    continue
                links.append((track_repo.upsert_metadata(item), item.position))
            if not links:
                raise ValueError("A playlist não contém faixas dentro dos limites configurados")
            track_repo.replace_playlist_links(playlist.id, links)
            return playlist.id
