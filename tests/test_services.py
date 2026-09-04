from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app.config import Settings
from app.domain.enums import MusicalScale, RoundResult, RoundStatus
from app.domain.models import Base, GameRound, Playlist, PlaylistTrack, Track, utc_now
from app.domain.transitions import can_transition
from app.infrastructure.db import create_db_engine, create_session_factory
from app.infrastructure.yt_dlp_client import YtDlpClient
from app.services.round_service import RoundService
from app.services.stats_service import StatsService


def services(tmp_path: Path) -> tuple[RoundService, StatsService, str, str]:
    db = tmp_path / "services.db"
    settings = Settings(
        database_url=f"sqlite:///{db.as_posix()}",
        media_dir=tmp_path / "media",
        secret_key="test-secret-with-more-than-thirty-two-characters",
    )
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session, session.begin():
        playlist = Playlist(youtube_playlist_id="PLabcdefghij", source_url="https://youtube.test")
        tracks = [
            Track(
                youtube_video_id=f"video00000{i}",
                title=f"Track {i}",
                source_url="https://youtube.test",
            )
            for i in range(6)
        ]
        session.add_all([playlist, *tracks])
        session.flush()
        session.add_all(
            PlaylistTrack(playlist_id=playlist.id, track_id=item.id, position=i)
            for i, item in enumerate(tracks)
        )
        playlist_id = playlist.id
        track_id = tracks[0].id
    return RoundService(factory, settings), StatsService(factory), playlist_id, track_id


def test_selection_avoids_recent_tracks(tmp_path: Path) -> None:
    rounds, _, playlist_id, _ = services(tmp_path)
    chosen = {rounds.create_round(playlist_id, "player").round_id for _ in range(3)}
    assert len(chosen) == 3


def test_stats_ignore_unanswered_and_group_keys(tmp_path: Path) -> None:
    rounds, stats, playlist_id, _ = services(tmp_path)
    created = rounds.create_round(playlist_id, "player")
    assert stats.for_player("player").total == 0
    factory = rounds._sessions
    with factory() as session, session.begin():
        game_round = session.get(GameRound, created.round_id)
        assert game_round is not None
        track = session.get(Track, game_round.track_id)
        assert track is not None
        track.detected_key = "C"
        track.detected_scale = MusicalScale.MAJOR
        track.analysis_version = "test"
        game_round.status = RoundStatus.READY
        game_round.revealed_at = utc_now()
    rounds.answer(created.round_id, "player", RoundResult.INCORRECT)
    result = stats.for_player("player")
    assert result.total == 1
    assert result.incorrect == 1
    assert result.by_key[0].key == "C major"


def test_audio_path_rejects_traversal(tmp_path: Path) -> None:
    rounds, _, playlist_id, _ = services(tmp_path)
    created = rounds.create_round(playlist_id, "player")
    factory = rounds._sessions
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"audio")
    with factory() as session, session.begin():
        game_round = session.get(GameRound, created.round_id)
        assert game_round is not None
        track = session.get(Track, game_round.track_id)
        assert track is not None
        track.audio_path = "../outside.mp3"
    try:
        rounds.audio_path(created.round_id, "player")
    except Exception as exc:
        assert "indisponível" in str(exc)
    else:
        raise AssertionError("path traversal was accepted")


def test_yt_dlp_falls_back_when_windows_subprocess_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = YtDlpClient(metadata_timeout=30, download_timeout=30, max_audio_bytes=50_000_000)

    async def boom(*args: object, **kwargs: object) -> object:
        raise NotImplementedError

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args[0], 0, stdout=b'{"ok": true}', stderr=b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    monkeypatch.setattr(subprocess, "run", fake_run)

    payload = asyncio.run(client._run(["--dump-single-json"], 5))
    assert payload == b'{"ok": true}'


def test_download_converts_non_mp3_to_browser_safe_mp3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = YtDlpClient(metadata_timeout=30, download_timeout=30, max_audio_bytes=50_000_000)
    destination = tmp_path / "media"
    destination.mkdir()
    source = destination / "abc123.webm"
    source.write_bytes(b"not-audio")

    async def fake_run(*args: object, **kwargs: object) -> bytes:
        return b""

    monkeypatch.setattr(client, "_run", fake_run)

    def fake_convert(src: Path, dst: Path) -> None:
        dst.write_bytes(b"converted")

    monkeypatch.setattr(client, "_convert_to_mp3", fake_convert)

    result = asyncio.run(client.download("abc123", destination))
    assert result.name == "abc123.mp3"
    assert result.read_bytes() == b"converted"


def test_playlist_metadata_deduplicates_repeated_videos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = YtDlpClient(metadata_timeout=30, download_timeout=30, max_audio_bytes=50_000_000)
    payload = {
        "id": "PLabcdefghij",
        "title": "Repeated songs",
        "entries": [
            {"id": "video000001", "title": "First", "playlist_index": 1},
            {"id": "video000001", "title": "Repeated", "playlist_index": 2},
            {"id": "video000002", "title": "Second", "playlist_index": 3},
        ],
    }

    async def fake_run(*args: object, **kwargs: object) -> bytes:
        return json.dumps(payload).encode()

    monkeypatch.setattr(client, "_run", fake_run)

    metadata = asyncio.run(client.fetch_playlist("https://youtube.test", 100))

    assert [track.video_id for track in metadata.tracks] == ["video000001", "video000002"]
    assert [track.position for track in metadata.tracks] == [1, 3]


def test_fetch_playlist_downloads_thumbnail_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media_dir = tmp_path / "media"
    client = YtDlpClient(
        metadata_timeout=30,
        download_timeout=30,
        max_audio_bytes=50_000_000,
        media_dir=media_dir,
    )
    payload = {
        "id": "PLabcdefghij",
        "title": "Playlist de capa",
        "entries": [
            {
                "id": "video000001",
                "title": "Primeira faixa",
                "thumbnail": "https://i.ytimg.com/vi/video000001/hqdefault.jpg",
                "channel": "Artista",
                "duration": 123,
            }
        ],
    }

    async def fake_run(*args: object, **kwargs: object) -> bytes:
        return json.dumps(payload).encode()

    class FakeHeaders:
        def get_content_type(self) -> str:
            return "image/jpeg"

    class FakeResponse:
        headers = FakeHeaders()

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            del exc_type, exc, tb

        def read(self) -> bytes:
            return b"thumb-bytes"

    def fake_urlopen(url: object, timeout: int | None = None) -> FakeResponse:
        del timeout
        requested = getattr(url, "full_url", url)
        assert requested == payload["entries"][0]["thumbnail"]
        return FakeResponse()

    monkeypatch.setattr(client, "_run", fake_run)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    metadata = asyncio.run(client.fetch_playlist("https://youtube.test", 10))

    assert metadata.tracks[0].thumbnail_url == "/media/thumbnails/video000001.jpg"
    assert (media_dir / "thumbnails" / "video000001.jpg").read_bytes() == b"thumb-bytes"


def test_round_state_transitions() -> None:
    assert can_transition(RoundStatus.QUEUED, RoundStatus.DOWNLOADING)
    assert can_transition(RoundStatus.DOWNLOADING, RoundStatus.AUDIO_READY)
    assert can_transition(RoundStatus.AUDIO_READY, RoundStatus.ANALYZING)
    assert can_transition(RoundStatus.ANALYZING, RoundStatus.READY)
    assert not can_transition(RoundStatus.QUEUED, RoundStatus.READY)
    assert not can_transition(RoundStatus.READY, RoundStatus.DOWNLOADING)
