"""Authenticated, read-only status and scoped control for B92 collection."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import json
import os
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException

from neckline.k10 import store
from neckline.k10.collection_config import SOURCE_KEYS, validate_collection_config
from neckline.k10.schema import SchemaUnavailable, read_connection, require_schema
from .collection_schemas import (
    CollectionConfigurationOut, CollectionControlIn, CollectionControlOut,
    CollectionRunOut, CollectionSourceOut, CollectionStatusOut,
)


BindingProvider = Callable[[], tuple[str | None, int | None, str | None]]


def create_router(*, db_path_provider: Callable[[], Path],
                  require_token_dependency: Callable,
                  current_collection_binding_provider: BindingProvider) -> APIRouter:
    router = APIRouter(prefix="/api/v1/k10/collection", tags=["k10-collection"],
                       dependencies=[Depends(require_token_dependency)])

    @contextmanager
    def _reader(path: Path):
        try:
            with read_connection(path) as conn:
                require_schema(conn)
                yield conn
        except SchemaUnavailable as exc:
            raise HTTPException(status_code=503, detail={
                "reason": "not_configured", "message": str(exc), "missing": ["k10Schema"]}) from exc

    def _binding(path: Path):
        config_id, revision, error = current_collection_binding_provider()
        missing = []
        if config_id is None:
            missing.append("K10_COLLECTION_CONFIG_ID")
        if revision is None:
            missing.append("K10_COLLECTION_CONFIG_REVISION")
        config = None
        if not missing and error is None:
            config = store.read_execution_config(config_id=config_id, revision=revision, db_path=path)
            if config is None:
                missing.append("collection_config_revision")
            elif not validate_collection_config(config["payload"]).ready:
                missing.append("collection_config_invalid")
                config = None
        if error:
            missing.append("collection_binding_invalid")
        return config, CollectionConfigurationOut(
            state="configured" if config else "not_configured",
            configId=config_id, revision=revision, missing=missing)

    def _source(key: str, checkpoint: dict | None, path: Path,
                *, overview: bool = False) -> CollectionSourceOut:
        source = checkpoint or {}
        watermark = store.latest_source_watermark(source_key=key, db_path=path) if overview else None
        env_name = "TUSHARE_TOKEN" if key == "tushare-major-news" else "JIN10_MCP_TOKEN"
        return CollectionSourceOut(
            sourceKey=key, state=str(source.get("state") or "unavailable"),
            lastSuccessAt=watermark["fetchedAt"] if watermark else source.get("lastSuccessAt"),
            coverageThrough=watermark["successCutoffAt"] if watermark else source.get("coverageThrough"),
            observedStartAt=source.get("observedStartAt"),
            observedEndAt=source.get("observedEndAt"),
            limitations=list(source.get("limitations") or []),
            credentialConfigured=bool(os.environ.get(env_name, "").strip()),
        )

    def _status() -> CollectionStatusOut:
        path = db_path_provider()
        with _reader(path) as conn:
            rows = conn.execute(
                "SELECT task_id,status,stage,payload_json,checkpoint_json,created_at,updated_at "
                "FROM k10_tasks WHERE kind='collect_news' ORDER BY created_at DESC,task_id DESC LIMIT 20"
            ).fetchall()
        config, configuration = _binding(path)
        control = store.task_control_status(kind="collect_news", db_path=path)
        runs: list[CollectionRunOut] = []
        for row in rows:
            try:
                payload, checkpoint = json.loads(row[3]), json.loads(row[4])
            except (TypeError, ValueError):
                payload, checkpoint = {}, {}
            sources = checkpoint.get("sources") if isinstance(checkpoint, dict) else None
            outcomes = [_source(key, sources.get(key) if isinstance(sources, dict) else None, path)
                        for key in SOURCE_KEYS]
            runs.append(CollectionRunOut(
                taskId=str(row[0]), slotAt=str(payload.get("slotAt") or ""),
                status=str(row[1]), stage=row[2],
                startedAt=checkpoint.get("executionStartedAt") if isinstance(checkpoint, dict) else None,
                completedAt=str(row[6]) if row[1] in {"completed", "failed", "not_configured", "cancelled"} else None,
                sourceOutcomes=outcomes,
            ))
        latest = runs[0] if runs else None
        latest_sources = {source.sourceKey: source.model_dump() for source in latest.sourceOutcomes} if latest else {}
        sources = [_source(key, latest_sources.get(key), path, overview=True) for key in SOURCE_KEYS]
        return CollectionStatusOut(
            configuration=configuration,
            control=CollectionControlOut(state=control["state"],
                reasonCode=control["reasonCode"], changedAt=control["changedAt"]),
            sources=sources,
            activeTasks=[run for run in runs if run.status in {"queued", "running"}],
            latestRuns=runs[:5],
        )

    @router.get("/status", response_model=CollectionStatusOut)
    def status() -> CollectionStatusOut:
        return _status()

    @router.post("/control", response_model=CollectionStatusOut)
    def control(body: CollectionControlIn) -> CollectionStatusOut:
        path = db_path_provider()
        if body.state == "open":
            config, _ = _binding(path)
            if config is None:
                raise HTTPException(status_code=409,
                    detail={"reason": "collection_not_configured",
                            "message": "采集配置未绑定或无效"})
        store.set_collection_control(
            state=body.state,
            reason_code="user_opened" if body.state == "open" else "user_paused",
            changed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            changed_by="authenticated_api", db_path=path)
        return _status()

    return router


__all__ = ["create_router"]
