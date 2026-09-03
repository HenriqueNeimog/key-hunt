from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from app.domain.enums import MusicalScale, RoundResult, RoundStatus


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class PlaylistImportRequest(StrictModel):
    url: HttpUrl


class RoundCreated(StrictModel):
    round_id: str
    playlist_id: str
    status: RoundStatus


class TrackPublic(StrictModel):
    title: str
    artist: str | None
    thumbnail_url: str | None
    duration_seconds: int | None


class RoundStatusResponse(StrictModel):
    round_id: str
    playlist_id: str
    status: RoundStatus
    track: TrackPublic
    audio_url: str | None
    can_reveal: bool
    error: str | None


class RevealResponse(StrictModel):
    key: str
    scale: MusicalScale
    confidence: float | None
    low_confidence: bool


class RoundResultRequest(StrictModel):
    result: RoundResult

    @field_validator("result", mode="before")
    @classmethod
    def parse_result(cls, value: object) -> RoundResult:
        if not isinstance(value, str):
            raise ValueError("O resultado deve ser correct ou incorrect")
        try:
            return RoundResult(value)
        except ValueError as exc:
            raise ValueError("O resultado deve ser correct ou incorrect") from exc


class RoundResultResponse(StrictModel):
    accepted: Literal[True] = True
    result: RoundResult


class KeyPerformance(StrictModel):
    key: str
    total: int
    correct: int
    accuracy: float


class HistoryEntry(StrictModel):
    answered_at: datetime
    title: str
    artist: str | None
    result: RoundResult
    key: str
    scale: MusicalScale


class StatsResponse(StrictModel):
    total: int
    correct: int
    incorrect: int
    accuracy: float
    by_key: list[KeyPerformance]
    recent: list[HistoryEntry]


PlaylistUrl = Annotated[str, Field(min_length=1, max_length=2048)]
