"""Neckline API：V4 晨报接口（/api/v4）与只读网页（/read）。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from neckline.api.deps import require_api_token_ready, require_token
from neckline.morning.api import create_router as create_morning_router
from neckline.morning.web import create_router as create_read_router

VERSION = "v4.0.0"
RELEASE_SET = "v4.0.0-b113"


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_api_token_ready()
    yield


app = FastAPI(title="Neckline", version=VERSION, lifespan=lifespan)
app.include_router(create_morning_router(
    require_token_dependency=require_token,
    version=VERSION,
    release_set=RELEASE_SET,
))
app.include_router(create_read_router())
