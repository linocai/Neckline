#!/usr/bin/env python3
"""Read-only, authenticated loopback serving of an owned B78 acceptance DB."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


def make_app(*, database: Path, token: str, fail_auxiliary: bool = False):
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.responses import JSONResponse
    from neckline.api.app import health
    from neckline.api.k10 import create_router
    from tests.test_v350_cli_api import explicit_bindings

    if not database.is_file() or not token:
        raise ValueError("isolated database and a nonempty temporary bearer are required")
    binding = explicit_bindings(database)

    async def authenticate(authorization: str | None = Header(default=None)):
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="acceptance token required")

    app = FastAPI()

    @app.middleware("http")
    async def readonly(request, call_next):
        if request.method not in {"GET", "HEAD"}:
            return JSONResponse({"detail": "native acceptance is read-only"}, status_code=405)
        if fail_auxiliary and not (request.url.path.startswith("/api/v1/k10/v2/reports")
                                   or request.url.path == "/api/v1/health"):
            return JSONResponse({"detail": "isolated auxiliary read failure"}, status_code=503)
        return await call_next(request)

    app.add_api_route("/api/v1/health", health, methods=["GET"])
    app.include_router(create_router(lambda: database, authenticate, lambda: database.parent / "parquet",
        current_config_binding_provider=lambda: (binding["config_id"], binding["config_revision"], None),
        current_execution_config_binding_provider=lambda: (binding["execution_id"], binding["execution_revision"], None)))
    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--fail-auxiliary", action="store_true")
    args = parser.parse_args()
    if not args.release_root.is_absolute() or not 1 <= args.port <= 65535:
        raise SystemExit("explicit absolute release root and valid loopback port required")
    root = args.release_root.resolve() / "temporary" / "integration"
    database, token_file = args.database.resolve(), args.token_file.resolve()
    if not database.is_relative_to(root) or not token_file.is_relative_to(root):
        raise SystemExit("database and bearer must be inside this release's owned integration directory")
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["DB_PATH"] = str(root / "unused-default.sqlite")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import uvicorn
    uvicorn.run(make_app(database=database, token=token_file.read_text().strip(), fail_auxiliary=args.fail_auxiliary),
                host="127.0.0.1", port=args.port, access_log=False, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
