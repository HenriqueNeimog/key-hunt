from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.config import get_settings
from app.domain.enums import PreparationStatus
from app.domain.models import GameSessionTrack, OutboxEvent, Playlist, PlaylistTrack, Track
from app.infrastructure.db import create_db_engine, create_session_factory
from app.services.round_service import RoundService
from app.services.session_service import SessionService


def _tone_mp3(media_dir: Path, video_id: str, frequency: float) -> None:
    wav_path = media_dir / f"{video_id}.wav"
    mp3_path = media_dir / f"{video_id}.mp3"
    sample_rate = 44100
    media_dir.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        for index in range(sample_rate * 3):
            value = int(12000 * math.sin(2 * math.pi * frequency * index / sample_rate))
            output.writeframesraw(struct.pack("<h", value))
    subprocess.run(  # noqa: S603 - fixed ffmpeg smoke-test command
        [  # noqa: S607 - fixed executable in the controlled container
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(wav_path),
            str(mp3_path),
        ],
        check=True,
    )
    wav_path.unlink()


def seed() -> None:
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    factory = create_session_factory(engine)
    marker = uuid4().hex[:10]
    video_ids = [f"smoke{marker}a", f"smoke{marker}b"]
    for video_id, frequency in zip(video_ids, (261.63, 329.63), strict=True):
        _tone_mp3(settings.resolved_media_dir, video_id, frequency)
    with factory() as db, db.begin():
        playlist = Playlist(
            youtube_playlist_id=f"PL{marker}",
            source_url="https://www.youtube.com/playlist?list=smoke",
            title="Compose smoke playlist",
        )
        tracks = [
            Track(
                youtube_video_id=video_id,
                title=f"Smoke Track {index + 1}",
                artist="Key Hunt",
                source_url=f"https://www.youtube.com/watch?v={video_id}",
            )
            for index, video_id in enumerate(video_ids)
        ]
        db.add_all([playlist, *tracks])
        db.flush()
        db.add_all(
            PlaylistTrack(playlist_id=playlist.id, track_id=track.id, position=index)
            for index, track in enumerate(tracks)
        )
        playlist_id = playlist.id
    rounds = RoundService(factory, settings)
    created = SessionService(factory, settings, rounds).create(
        playlist_id, "compose-smoke-player", seed="compose-smoke-seed"
    )
    print(created.model_dump_json())
    engine.dispose()


def check(session_id: str) -> None:
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    factory = create_session_factory(engine)
    with factory() as db:
        positions = list(
            db.scalars(
                select(GameSessionTrack)
                .where(GameSessionTrack.game_session_id == session_id)
                .order_by(GameSessionTrack.position)
            )
        )
        outbox = list(
            db.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.game_session_id == session_id)
                .order_by(OutboxEvent.position)
            )
        )
        print(
            json.dumps(
                {
                    "statuses": [item.preparation_status.value for item in positions],
                    "all_ready": all(
                        item.preparation_status == PreparationStatus.READY for item in positions
                    ),
                    "outbox": [
                        {"status": item.status.value, "attempts": item.attempt_count}
                        for item in outbox
                    ],
                }
            )
        )
    engine.dispose()


if __name__ == "__main__":
    if sys.argv[1:] == ["seed"]:
        seed()
    elif len(sys.argv) == 3 and sys.argv[1] == "check":
        check(sys.argv[2])
    else:
        raise SystemExit("usage: compose_smoke.py seed|check SESSION_ID")
