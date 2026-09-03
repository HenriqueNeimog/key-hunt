from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.models import Base
from app.infrastructure.db import create_db_engine
from app.main import create_app
from tests.fakes import FakeAnalyzer, FakeMediaClient


@pytest.fixture
def fake_media() -> FakeMediaClient:
    return FakeMediaClient()


@pytest.fixture
def fake_analyzer() -> FakeAnalyzer:
    return FakeAnalyzer()


@pytest.fixture
def client(
    tmp_path: Path, fake_media: FakeMediaClient, fake_analyzer: FakeAnalyzer
) -> Iterator[TestClient]:
    database = tmp_path / "test.db"
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database.as_posix()}",
        media_dir=tmp_path / "media",
        secret_key="test-secret-with-more-than-thirty-two-characters",
        playlist_cache_ttl_seconds=3600,
        max_concurrent_jobs=1,
    )
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    app = create_app(
        settings,
        metadata_client=fake_media,
        downloader=fake_media,
        analyzer=fake_analyzer,
    )
    with TestClient(app) as test_client:
        test_client.get("/")
        yield test_client


@pytest.fixture
def csrf_headers(client: TestClient) -> dict[str, str]:
    token = client.cookies.get("key_hunt_csrf")
    assert token is not None
    return {"X-CSRF-Token": token}
