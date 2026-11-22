"""Q 期货看板接口：GET /api/v4/qihuo/board，复用统一 Bearer 鉴权。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from neckline.morning import db
from neckline.morning.api import v4_db_path
from neckline.qihuo import services
from neckline.qihuo.board import QihuoUnavailable, build_board


def create_router(*, require_token_dependency: Callable[..., Any],
                  db_path_provider: Callable[[], Path | None] = v4_db_path) -> APIRouter:
    router = APIRouter(prefix="/api/v4/qihuo")

    @router.get("/board", dependencies=[Depends(require_token_dependency)])
    async def board(refresh: bool = Query(default=False)) -> dict:
        path = db_path_provider()
        if path is None:
            raise HTTPException(status_code=503, detail={"reason": "v4_not_configured"})
        try:
            with db.read(path):
                pass
        except db.NotReady:
            raise HTTPException(status_code=503, detail={"reason": "v4_db_not_ready"}) from None
        try:
            return await build_board(services.current(), db_path=path, force_refresh=refresh)
        except QihuoUnavailable as error:
            raise HTTPException(
                status_code=503,
                detail={"reason": error.reason, "message": error.message},
            ) from None

    return router
