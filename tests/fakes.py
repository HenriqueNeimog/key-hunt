from __future__ import annotations

import asyncio
from pathlib import Path

from app.domain.enums import MusicalScale
from app.infrastructure.essentia_analyzer import KeyAnalysis
from app.infrastructure.yt_dlp_client import PlaylistMetadata, TrackMetadata


class FakeMediaClient:
    def __init__(self) -> None:
        self.metadata_calls = 0
        self.download_calls = 0

    async def fetch_playlist(self, url: str, max_items: int) -> PlaylistMetadata:
        del url, max_items
        self.metadata_calls += 1
        return PlaylistMetadata(
            playlist_id="PLabcdefghij",
            title="Playlist de teste",
            tracks=[
                TrackMetadata(
                    video_id="abcDEF12345",
                    title="Faixa de teste",
                    artist="Artista de teste",
                    source_url="https://www.youtube.com/watch?v=abcDEF12345",
                    thumbnail_url="https://i.ytimg.com/vi/abcDEF12345/hqdefault.jpg",
                    duration_seconds=180,
                    position=1,
                ),
                TrackMetadata(
                    video_id="xyzABC67890",
                    title="Outra faixa",
                    artist="Outro artista",
                    source_url="https://www.youtube.com/watch?v=xyzABC67890",
                    thumbnail_url=None,
                    duration_seconds=210,
                    position=2,
                ),
            ],
        )

    async def download(self, video_id: str, destination_dir: Path) -> Path:
        self.download_calls += 1
        await asyncio.to_thread(destination_dir.mkdir, parents=True, exist_ok=True)
        path = destination_dir / f"{video_id}.mp3"
        await asyncio.to_thread(path.write_bytes, b"ID3" + b"audio" * 100)
        return path


class FakeAnalyzer:
    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, audio_path: Path) -> KeyAnalysis:
        assert audio_path.is_file()
        self.calls += 1
        return KeyAnalysis(key="F#", scale=MusicalScale.MINOR, confidence=0.82)
