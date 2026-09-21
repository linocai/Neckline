#!/usr/bin/env python3
"""Serve a preserved B76 acceptance database on an authenticated loopback URL."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import uvicorn
from fastapi import FastAPI, Header, HTTPException, status


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def make_app(*, database: Path, token: str) -> FastAPI:
    if not database.is_file():
        raise ValueError(f"acceptance database does not exist: {database}")
    if not token:
        raise ValueError("loopback token must not be empty")

    backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend))
    from neckline.api.app import health
    from neckline.api.k10 import create_router
    from neckline.k10 import store

    with store.read_connection(database) as connection:
        config = connection.execute(
            "SELECT config_id,revision FROM k10_run_config_revisions ORDER BY created_at DESC,revision DESC LIMIT 1"
        ).fetchone()
        execution = connection.execute(
            "SELECT config_id,revision FROM k10_execution_config_revisions ORDER BY created_at DESC,revision DESC LIMIT 1"
        ).fetchone()
    if config is None or execution is None:
        raise ValueError("acceptance database lacks active configuration bindings")

    async def require_token(authorization: str | None = Header(default=None)) -> None:
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="acceptance token required")

    app = FastAPI()
    # Reuse the production health handler: the client gates every report read
    # on this endpoint before it makes any K10 request.
    app.add_api_route("/api/v1/health", health, methods=["GET"])
    app.include_router(create_router(
        lambda: database,
        require_token,
        lambda: database.parent / "parquet",
        current_config_binding_provider=lambda: (str(config[0]), int(config[1]), None),
        current_execution_config_binding_provider=lambda: (str(execution[0]), int(execution[1]), None),
    ))
    return app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--token", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if args.host not in LOOPBACK_HOSTS:
        raise SystemExit("B76 acceptance server may bind only to a loopback host")
    if not 1 <= args.port <= 65535:
        raise SystemExit("port must be between 1 and 65535")
    uvicorn.run(make_app(database=args.database.resolve(), token=args.token), host=args.host, port=args.port,
                access_log=False, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
