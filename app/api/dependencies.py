from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import Cookie, Depends, Header, HTTPException, Request

from app.config import Settings
from app.infrastructure.task_manager import TaskManager
from app.services.playlist_service import PlaylistService
from app.services.round_service import RoundService
from app.services.stats_service import StatsService

PLAYER_COOKIE = "key_hunt_player"
CSRF_COOKIE = "key_hunt_csrf"


@dataclass(frozen=True, slots=True)
class AppContainer:
    settings: Settings
    playlists: PlaylistService
    rounds: RoundService
    stats: StatsService
    tasks: TaskManager


def container(request: Request) -> AppContainer:
    return cast(AppContainer, request.app.state.container)


def sign_player(player_id: str, settings: Settings) -> str:
    signature = hmac.new(
        settings.secret_key.get_secret_value().encode(),
        player_id.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{player_id}.{signature}"


def parse_player(value: str | None, settings: Settings) -> str | None:
    if value is None:
        return None
    try:
        player_id, signature = value.rsplit(".", 1)
        UUID(player_id)
    except (ValueError, AttributeError):
        return None
    expected = sign_player(player_id, settings).rsplit(".", 1)[1]
    return player_id if hmac.compare_digest(signature, expected) else None


def get_player_id(
    app: Annotated[AppContainer, Depends(container)],
    player_cookie: Annotated[str | None, Cookie(alias=PLAYER_COOKIE)] = None,
) -> str:
    player_id = parse_player(player_cookie, app.settings)
    if player_id is None:
        raise HTTPException(status_code=401, detail="Identidade de jogador inválida")
    return player_id


def validate_csrf(
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    if (
        csrf_cookie is None
        or csrf_header is None
        or not hmac.compare_digest(csrf_cookie, csrf_header)
    ):
        raise HTTPException(status_code=403, detail="Token CSRF inválido")


PlayerId = Annotated[str, Depends(get_player_id)]
CsrfProtection = Annotated[None, Depends(validate_csrf)]


def new_identity(settings: Settings) -> tuple[str, str]:
    return sign_player(str(uuid4()), settings), secrets.token_urlsafe(32)
