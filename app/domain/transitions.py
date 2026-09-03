from app.domain.enums import RoundStatus

_ALLOWED: dict[RoundStatus, frozenset[RoundStatus]] = {
    RoundStatus.QUEUED: frozenset({RoundStatus.DOWNLOADING, RoundStatus.FAILED}),
    RoundStatus.DOWNLOADING: frozenset(
        {RoundStatus.DOWNLOADING, RoundStatus.AUDIO_READY, RoundStatus.FAILED}
    ),
    RoundStatus.AUDIO_READY: frozenset(
        {RoundStatus.DOWNLOADING, RoundStatus.ANALYZING, RoundStatus.READY, RoundStatus.FAILED}
    ),
    RoundStatus.ANALYZING: frozenset(
        {RoundStatus.DOWNLOADING, RoundStatus.READY, RoundStatus.FAILED}
    ),
    RoundStatus.READY: frozenset({RoundStatus.READY}),
    RoundStatus.FAILED: frozenset(),
}


def can_transition(current: RoundStatus, target: RoundStatus) -> bool:
    return target in _ALLOWED[current]
