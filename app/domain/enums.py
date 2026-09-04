from enum import StrEnum


class AudioStatus(StrEnum):
    MISSING = "missing"
    DOWNLOADING = "downloading"
    READY = "ready"
    FAILED = "failed"


class RoundStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    AUDIO_READY = "audio_ready"
    ANALYZING = "analyzing"
    READY = "ready"
    FAILED = "failed"


class MusicalScale(StrEnum):
    MAJOR = "major"
    MINOR = "minor"
    UNKNOWN = "unknown"


class RoundResult(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"


class GameSessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class PreparationStatus(StrEnum):
    PENDING = "pending"
    ENQUEUED = "enqueued"
    DOWNLOADING = "downloading"
    AUDIO_READY = "audio_ready"
    ANALYZING = "analyzing"
    READY = "ready"
    FAILED = "failed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
