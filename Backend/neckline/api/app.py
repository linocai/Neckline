"""Neckline API：V4 晨报（/api/v4）、只读网页（/read）和只读 MCP（/mcp）。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.routing import Route

from neckline.api.deps import require_api_token_ready, require_token
from neckline.kx.api import create_router as create_kx_router
from neckline.kx.services import close_default_services as close_kx_services
from neckline.market.services import close_default_services
from neckline.mcp.gateway import MCP_ROUTES, gateway
from neckline.mcp.status import set_mcp_serving
from neckline.morning.api import create_router as create_morning_router
from neckline.morning.web import create_router as create_read_router

VERSION = "v4.2.0"
RELEASE_SET = "v4.2.0-b124"


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_api_token_ready()
    from neckline.mcp.server import build_mcp_bundle

    bundle = build_mcp_bundle()
    app.state.mcp_bundle = bundle
    try:
        if bundle is None:
            yield
        else:
            async with bundle.server.session_manager.run():
                set_mcp_serving(True)
                try:
                    yield
                finally:
                    set_mcp_serving(False)
    finally:
        app.state.mcp_bundle = None
        await close_kx_services()
        await close_default_services()


app = FastAPI(title="Neckline", version=VERSION, lifespan=lifespan)
app.state.mcp_bundle = None
app.include_router(create_morning_router(
    require_token_dependency=require_token,
    version=VERSION,
    release_set=RELEASE_SET,
))
app.include_router(create_kx_router(require_token_dependency=require_token))
app.include_router(create_read_router())
for _index, (_path, _methods) in enumerate(MCP_ROUTES):
    app.router.routes.append(Route(
        _path, endpoint=gateway, methods=_methods, name=f"mcp-{_index}", include_in_schema=False))
