from __future__ import annotations

import asyncio
import shutil
import socket
import sys
import time

from sqlalchemy import text

from app.config import Settings, get_settings
from app.infrastructure.db import create_db_engine, create_session_factory
from app.infrastructure.essentia_analyzer import EssentiaKeyAnalyzer
from app.infrastructure.media_processor import MediaProcessor
from app.infrastructure.messaging import run_kafka_worker
from app.infrastructure.yt_dlp_client import YtDlpClient


def build_processor(settings: Settings) -> MediaProcessor:
    engine = create_db_engine(settings.database_url)
    sessions = create_session_factory(engine)
    media = YtDlpClient(
        metadata_timeout=settings.metadata_timeout_seconds,
        download_timeout=settings.download_timeout_seconds,
        max_audio_bytes=settings.max_audio_bytes,
    )
    return MediaProcessor(
        session_factory=sessions,
        downloader=media,
        analyzer=EssentiaKeyAnalyzer(settings.ffmpeg_binary),
        settings=settings,
    )


def healthcheck(settings: Settings) -> int:
    try:
        settings.resolved_media_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.resolved_media_dir / ".worker-health"
        probe.touch()
        probe.unlink()
        engine = create_db_engine(settings.database_url)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
        host, separator, port_text = settings.kafka_bootstrap_servers.rpartition(":")
        if not separator:
            return 1
        with socket.create_connection((host, int(port_text)), timeout=2):
            pass
    except (OSError, ValueError):
        return 1
    return 0


async def run() -> None:
    settings = get_settings()
    settings.resolved_media_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_cleanup_stale_work, settings)
    processor = build_processor(settings)
    await asyncio.gather(
        *(run_kafka_worker(processor, settings) for _ in range(settings.media_worker_concurrency))
    )


def _cleanup_stale_work(settings: Settings) -> None:
    work_root = settings.resolved_media_dir / ".work"
    if not work_root.is_dir():
        return
    cutoff = time.time() - settings.media_claim_lease_seconds
    for candidate in work_root.iterdir():
        if candidate.is_dir() and candidate.stat().st_mtime < cutoff:
            shutil.rmtree(candidate)


def main() -> None:
    settings = get_settings()
    if sys.argv[1:] == ["--healthcheck"]:
        raise SystemExit(healthcheck(settings))
    asyncio.run(run())


if __name__ == "__main__":
    main()
