"""KX 接口：看板（GET /api/v4/kx/board，和晨报接口同一套 Bearer）与推荐接收（POST /api/v4/kx/picks，单独的 Bearer）。"""
from __future__ import annotations

import hmac
import json
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from neckline.kx import picks, services, settings
from neckline.kx.board import BoardUnavailable, build_board
from neckline.morning import db
from neckline.morning.api import v4_db_path


def _picks_authorized(header: str) -> bool:
    expected = settings.picks_token()
    prefix = "Bearer "
    if expected is None or not header.startswith(prefix):
        return False
    return hmac.compare_digest(header[len(prefix):].strip().encode(), expected.encode())


def _invalid(reason: str) -> JSONResponse:
    return JSONResponse({"status": "invalid", "reason": reason}, status_code=400)


def create_router(*, require_token_dependency: Callable[..., Any],
                  db_path_provider: Callable[[], Path | None] = v4_db_path) -> APIRouter:
    router = APIRouter(prefix="/api/v4/kx")

    @router.get("/board", dependencies=[Depends(require_token_dependency)])
    async def board() -> dict:
        path = db_path_provider()
        if path is None:
            raise HTTPException(status_code=503, detail={"reason": "v4_not_configured"})
        try:
            return await build_board(services.current(), path)
        except BoardUnavailable as error:
            raise HTTPException(status_code=503, detail={"reason": error.reason, "message": error.message}) from None
        except db.NotReady:
            raise HTTPException(status_code=503, detail={"reason": "v4_db_not_ready"}) from None

    @router.post("/picks")
    async def receive_picks(request: Request) -> JSONResponse:
        if not _picks_authorized(request.headers.get("authorization", "")):
            return JSONResponse({"status": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return _invalid("Content-Type 必须是 application/json")
        body = await request.body()
        if len(body) > picks.MAX_BODY:
            return _invalid("请求体过大")
        try:
            payload = json.loads(body)
            parsed = picks.parse_request(payload)
        except ValueError as error:  # json.JSONDecodeError 与 PickInvalid 都是 ValueError
            return _invalid(str(error) if isinstance(error, picks.PickInvalid) else "请求体不是有效的 JSON")
        path = db_path_provider()
        if path is None:
            return JSONResponse({"status": "unavailable", "reason": "v4_not_configured"}, status_code=503)
        status, reply = await picks.record(services.current(), path, parsed)
        return JSONResponse(reply, status_code=status)

    return router
