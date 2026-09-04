from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import AppContainer, CsrfProtection, PlayerId, container
from app.domain.schemas import NextSessionRequest, PrefetchStatusResponse, SessionState

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("/{session_id}", response_model=SessionState)
async def session_state(
    session_id: str,
    player_id: PlayerId,
    app: Annotated[AppContainer, Depends(container)],
) -> SessionState:
    return await asyncio.to_thread(app.game_sessions.state, session_id, player_id)


@router.post("/{session_id}/next", response_model=SessionState)
async def next_track(
    session_id: str,
    payload: NextSessionRequest,
    player_id: PlayerId,
    _csrf: CsrfProtection,
    app: Annotated[AppContainer, Depends(container)],
) -> SessionState:
    state = await asyncio.to_thread(
        app.game_sessions.advance,
        session_id,
        player_id,
        payload.idempotency_key,
    )
    app.outbox.notify()
    return state


@router.get("/{session_id}/prefetch-status", response_model=PrefetchStatusResponse)
async def prefetch_status(
    session_id: str,
    player_id: PlayerId,
    app: Annotated[AppContainer, Depends(container)],
) -> PrefetchStatusResponse:
    return await asyncio.to_thread(app.game_sessions.prefetch_status, session_id, player_id)
