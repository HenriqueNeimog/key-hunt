from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.dependencies import AppContainer, CsrfProtection, PlayerId, container
from app.domain.schemas import PlaylistImportRequest, RoundCreated

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


@router.post("/import", response_model=RoundCreated, status_code=status.HTTP_202_ACCEPTED)
async def import_playlist(
    payload: PlaylistImportRequest,
    player_id: PlayerId,
    _csrf: CsrfProtection,
    app: Annotated[AppContainer, Depends(container)],
) -> RoundCreated:
    playlist_id = await app.playlists.import_playlist(str(payload.url))
    created = await asyncio.to_thread(app.rounds.create_round, playlist_id, player_id)
    app.tasks.schedule(created.round_id)
    return created


@router.post(
    "/{playlist_id}/rounds",
    response_model=RoundCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_round(
    playlist_id: str,
    player_id: PlayerId,
    _csrf: CsrfProtection,
    app: Annotated[AppContainer, Depends(container)],
) -> RoundCreated:
    created = await asyncio.to_thread(app.rounds.create_round, playlist_id, player_id)
    app.tasks.schedule(created.round_id)
    return created
