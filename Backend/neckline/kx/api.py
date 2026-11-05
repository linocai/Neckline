"""KX 看板接口：GET /api/v4/kx/board，和晨报接口同一套 Bearer。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException

from neckline.kx import services
from neckline.kx.board import BoardUnavailable, build_board
from neckline.morning import db
from neckline.morning.api import v4_db_path


def create_router(*, require_token_dependency: Callable[..., Any],
                  db_path_provider: Callable[[], Path | None] = v4_db_path) -> APIRouter:
    router = APIRouter(prefix="/api/v4/kx")

    @router.get("/board", dependencies=[Depends(require_token_dependency)])
    async def board() -> dict:
        path = db_path_provider()
        if path is None:
            raise HTTPException(status_code=503, detail={"reason": "v4_not_configured"})
        try:
            with db.read(path):
                pass
        except db.NotReady:
            raise HTTPException(status_code=503, detail={"reason": "v4_db_not_ready"}) from None
        try:
            return await build_board(services.current())
        except BoardUnavailable as error:
            raise HTTPException(status_code=503, detail={"reason": error.reason, "message": error.message}) from None

    return router
