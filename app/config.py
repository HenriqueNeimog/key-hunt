from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="KEY_HUNT_",
        extra="ignore",
        strict=True,
    )

    environment: str = "development"
    database_url: str = "sqlite:///./key_hunt.db"
    media_dir: Path = Path("media")
    secret_key: SecretStr = SecretStr("development-only-change-this-secret")
    playlist_cache_ttl_seconds: int = Field(default=3600, ge=0)
    max_playlist_items: int = Field(default=100, ge=1, le=1000)
    max_track_duration_seconds: int = Field(default=1800, ge=30)
    max_audio_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    download_timeout_seconds: int = Field(default=300, ge=5)
    metadata_timeout_seconds: int = Field(default=60, ge=5)
    max_concurrent_jobs: int = Field(default=2, ge=1, le=16)
    analysis_version: str = "essentia-keyextractor-v1"
    low_confidence_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    ffmpeg_binary: str = "ffmpeg"

    @field_validator("database_url")
    @classmethod
    def sqlite_only(cls, value: str) -> str:
        if not value.startswith("sqlite:///"):
            raise ValueError("O MVP aceita apenas URLs SQLite")
        return value

    @property
    def secure_cookies(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def resolved_media_dir(self) -> Path:
        return self.media_dir.resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
