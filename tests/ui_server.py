from __future__ import annotations

from pathlib import Path

import uvicorn

from app.config import Settings
from app.domain.models import Base
from app.infrastructure.db import create_db_engine
from app.main import create_app
from tests.fakes import FakeAnalyzer, FakeMediaClient

root = Path(__file__).parent.parent / ".tmp" / "ui"
settings = Settings(
    environment="test",
    database_url=f"sqlite:///{(root / 'ui.db').as_posix()}",
    media_dir=root / "media",
    secret_key="test-secret-with-more-than-thirty-two-characters",
)
root.mkdir(parents=True, exist_ok=True)
engine = create_db_engine(settings.database_url)
Base.metadata.create_all(engine)
engine.dispose()
media = FakeMediaClient()
app = create_app(settings, metadata_client=media, downloader=media, analyzer=FakeAnalyzer())

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
