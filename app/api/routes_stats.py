from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import AppContainer, PlayerId, container
from app.domain.schemas import StatsResponse

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("", response_model=StatsResponse)
async def stats(
    player_id: PlayerId,
    app: Annotated[AppContainer, Depends(container)],
) -> StatsResponse:
    return await asyncio.to_thread(app.stats.for_player, player_id)
