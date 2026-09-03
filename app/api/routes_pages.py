from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="home.html")


@router.get("/game/{round_id}", response_class=HTMLResponse)
async def game(request: Request, round_id: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="game.html", context={"round_id": round_id}
    )


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="stats.html")
