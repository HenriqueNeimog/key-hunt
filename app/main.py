from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.api.dependencies import (
    CSRF_COOKIE,
    PLAYER_COOKIE,
    AppContainer,
    new_identity,
    parse_player,
)
from app.api.routes_pages import router as pages_router
from app.api.routes_playlists import router as playlists_router
from app.api.routes_rounds import router as rounds_router
from app.api.routes_sessions import router as sessions_router
from app.api.routes_stats import router as stats_router
from app.config import Settings, get_settings
from app.infrastructure.db import create_db_engine, create_session_factory
from app.infrastructure.essentia_analyzer import EssentiaKeyAnalyzer, KeyAnalyzer
from app.infrastructure.media_processor import MediaProcessor
from app.infrastructure.messaging import (
    DirectEventPublisher,
    KafkaEventPublisher,
    OutboxPublisher,
)
from app.infrastructure.task_manager import TaskManager
from app.infrastructure.yt_dlp_client import (
    AudioDownloader,
    ExternalMediaError,
    PlaylistMetadataClient,
    YtDlpClient,
)
from app.services.playlist_service import PlaylistService
from app.services.round_service import ConflictError, NotFoundError, RoundService
from app.services.session_service import SessionService
from app.services.stats_service import StatsService
from app.services.url_security import InvalidPlaylistUrl

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)


def create_app(
    settings: Settings | None = None,
    *,
    metadata_client: PlaylistMetadataClient | None = None,
    downloader: AudioDownloader | None = None,
    analyzer: KeyAnalyzer | None = None,
) -> FastAPI:
    configured = settings or get_settings()
    engine = create_db_engine(configured.database_url)
    sessions = create_session_factory(engine)
    yt_dlp = YtDlpClient(
        metadata_timeout=configured.metadata_timeout_seconds,
        download_timeout=configured.download_timeout_seconds,
        max_audio_bytes=configured.max_audio_bytes,
        media_dir=configured.resolved_media_dir,
    )
    chosen_metadata = metadata_client or yt_dlp
    chosen_downloader = downloader or yt_dlp
    chosen_analyzer = analyzer or EssentiaKeyAnalyzer(configured.ffmpeg_binary)
    tasks = TaskManager(
        session_factory=sessions,
        downloader=chosen_downloader,
        analyzer=chosen_analyzer,
        settings=configured,
    )
    round_service = RoundService(sessions, configured)
    media_processor = MediaProcessor(
        session_factory=sessions,
        downloader=chosen_downloader,
        analyzer=chosen_analyzer,
        settings=configured,
    )
    event_publisher = (
        KafkaEventPublisher(configured.kafka_bootstrap_servers)
        if configured.processing_mode == "kafka"
        else DirectEventPublisher(media_processor, configured.kafka_dlq_topic)
    )
    outbox = OutboxPublisher(sessions, event_publisher, configured)
    container = AppContainer(
        settings=configured,
        playlists=PlaylistService(sessions, chosen_metadata, configured),
        rounds=round_service,
        stats=StatsService(sessions),
        tasks=tasks,
        game_sessions=SessionService(sessions, configured, round_service),
        outbox=outbox,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        configured.resolved_media_dir.mkdir(parents=True, exist_ok=True)
        try:
            await tasks.recover()
            await outbox.start()
        except OperationalError as exc:
            raise RuntimeError("Banco não migrado. Execute: alembic upgrade head") from exc
        yield
        await outbox.close()
        await tasks.close()
        engine.dispose()

    app = FastAPI(
        title="Key Hunt",
        version="0.1.0",
        docs_url=None if configured.environment == "production" else "/docs",
        lifespan=lifespan,
    )
    app.state.container = container
    static_dir = Path(__file__).parent / "static"
    configured.resolved_media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.mount("/media", StaticFiles(directory=configured.resolved_media_dir), name="media")
    app.include_router(pages_router)
    app.include_router(playlists_router)
    app.include_router(rounds_router)
    app.include_router(sessions_router)
    app.include_router(stats_router)

    @app.middleware("http")
    async def identity_cookies(request: Request, call_next: object) -> object:

        handler = call_next
        if not callable(handler):
            raise TypeError("Invalid ASGI handler")
        response = await handler(request)
        player = parse_player(request.cookies.get(PLAYER_COOKIE), configured)
        csrf = request.cookies.get(CSRF_COOKIE)
        if player is None or csrf is None:
            signed_player, new_csrf = new_identity(configured)
            if player is None:
                response.set_cookie(
                    PLAYER_COOKIE,
                    signed_player,
                    httponly=True,
                    secure=configured.secure_cookies,
                    samesite="lax",
                    max_age=365 * 24 * 60 * 60,
                )
            if csrf is None:
                response.set_cookie(
                    CSRF_COOKIE,
                    new_csrf,
                    httponly=False,
                    secure=configured.secure_cookies,
                    samesite="lax",
                    max_age=365 * 24 * 60 * 60,
                )
        return response

    @app.exception_handler(InvalidPlaylistUrl)
    @app.exception_handler(ValueError)
    async def bad_request(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ExternalMediaError)
    async def external_media_error(_: Request, exc: ExternalMediaError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc), "code": exc.code})

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/live", tags=["health"])
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def readiness() -> JSONResponse:
        try:
            await asyncio.to_thread(_check_database, engine)
        except OperationalError:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "database": "down"},
            )
        kafka_status = "not_configured"
        if configured.processing_mode == "kafka":
            reachable = await _kafka_reachable(configured.kafka_bootstrap_servers)
            kafka_status = "ok" if reachable else "degraded"
        return JSONResponse(content={"status": "ok", "database": "ok", "kafka": kafka_status})

    return app


def _check_database(engine: object) -> None:
    from sqlalchemy import Engine

    if not isinstance(engine, Engine):
        raise TypeError("Invalid database engine")
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


async def _kafka_reachable(bootstrap_servers: str) -> bool:
    first = bootstrap_servers.split(",", 1)[0]
    host, separator, port_text = first.rpartition(":")
    if not separator:
        return False
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port_text)), timeout=1.0
        )
        del reader
        writer.close()
        await writer.wait_closed()
    except (OSError, ValueError, TimeoutError):
        return False
    return True


app = create_app()
