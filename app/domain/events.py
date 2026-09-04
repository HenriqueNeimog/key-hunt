from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MediaPrepareEvent(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    event_id: str
    event_type: Literal["media.prepare.requested.v1", "media.prepare.retry.v1"]
    occurred_at: datetime
    track_id: str
    youtube_video_id: str
    game_session_id: str
    position: int = Field(ge=0)
    priority: Literal["current", "prefetch"]
    correlation_id: str
    attempt: int = Field(ge=1)


class MediaDeadLetterEvent(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    event_id: str
    event_type: Literal["media.prepare.failed.v1"] = "media.prepare.failed.v1"
    occurred_at: datetime
    original_event_id: str
    track_id: str
    game_session_id: str
    position: int = Field(ge=0)
    correlation_id: str
    attempt: int = Field(ge=1)
    error_code: str
