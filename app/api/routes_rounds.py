from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import AppContainer, CsrfProtection, PlayerId, container
from app.domain.schemas import (
    RevealResponse,
    RoundResultRequest,
    RoundResultResponse,
    RoundStatusResponse,
)

router = APIRouter(prefix="/api/rounds", tags=["rounds"])
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


@router.get("/{round_id}/status", response_model=RoundStatusResponse)
async def round_status(
    round_id: str,
    player_id: PlayerId,
    app: Annotated[AppContainer, Depends(container)],
) -> RoundStatusResponse:
    return await asyncio.to_thread(app.rounds.status, round_id, player_id)


def _file_chunks(path: Path, start: int, length: int) -> Iterator[bytes]:
    remaining = length
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(64 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get("/{round_id}/audio")
async def round_audio(
    round_id: str,
    player_id: PlayerId,
    app: Annotated[AppContainer, Depends(container)],
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> Response:
    path = await asyncio.to_thread(app.rounds.audio_path, round_id, player_id)
    size = await asyncio.to_thread(lambda: path.stat().st_size)
    start, end = 0, size - 1
    response_status = status.HTTP_200_OK
    if range_header is not None:
        match = _RANGE.fullmatch(range_header.strip())
        if match is None:
            raise HTTPException(status_code=416, detail="Intervalo de áudio inválido")
        first, last = match.groups()
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        elif last:
            suffix = int(last)
            start = max(size - suffix, 0)
        if start > end or start >= size:
            raise HTTPException(
                status_code=416,
                detail="Intervalo de áudio fora do arquivo",
                headers={"Content-Range": f"bytes */{size}"},
            )
        response_status = status.HTTP_206_PARTIAL_CONTENT
    length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Cache-Control": "private, max-age=3600",
    }
    if response_status == status.HTTP_206_PARTIAL_CONTENT:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        _file_chunks(path, start, length),
        status_code=response_status,
        media_type="audio/mpeg",
        headers=headers,
    )


@router.post("/{round_id}/reveal", response_model=RevealResponse)
async def reveal_round(
    round_id: str,
    player_id: PlayerId,
    _csrf: CsrfProtection,
    app: Annotated[AppContainer, Depends(container)],
) -> RevealResponse:
    return await asyncio.to_thread(app.rounds.reveal, round_id, player_id)


@router.post("/{round_id}/result", response_model=RoundResultResponse)
async def save_result(
    round_id: str,
    payload: RoundResultRequest,
    player_id: PlayerId,
    _csrf: CsrfProtection,
    app: Annotated[AppContainer, Depends(container)],
) -> RoundResultResponse:
    return await asyncio.to_thread(app.rounds.answer, round_id, player_id, payload.result)
