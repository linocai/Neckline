"""V4 晨报接口（/api/v4，Bearer 鉴权）与就绪检查。App、网页、MCP 读的都是同一份晨报记录。"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from neckline.screening.text import plain_text

from . import db
from .schedule import CST

_LABELS = {"jin10-flash": "金十快讯", "jin10-news": "金十资讯"}


def v4_db_path() -> Path | None:
    raw = (os.environ.get("NECKLINE_V4_DB_PATH") or "").strip()
    return Path(raw) if raw else None


def source_label(doc: dict) -> str:
    return _LABELS.get(doc["sourceKey"]) or (doc["meta"].get("source") or "TuShare 新闻")


def report_out(report: dict) -> dict:
    return {"reportId": report["report_id"], "tradeDate": report["trade_date"], "status": report["status"],
            "windowStart": report["window_start"], "windowEnd": report["window_end"],
            "availableAt": report["available_at"], "failedAt": report["failed_at"],
            "failureReason": report["failure_reason"] if report["status"] == "failed" else None,
            "gapSummary": report["gap_summary"], "configVersion": report["config_version"],
            "content": report["content"]}


def _local(value: str | None) -> str | None:
    return datetime.fromisoformat(value).astimezone(CST).isoformat(timespec="minutes") if value else None


def document_out(doc: dict) -> dict:
    return {"documentId": doc["documentId"], "sourceLabel": source_label(doc), "title": doc["title"],
            "text": plain_text(doc["text"] or doc["excerpt"]), "url": doc["url"],
            "publishedAt": _local(doc["publishedAt"]), "fetchedAt": _local(doc["fetchedAt"])}


class DeviceIn(BaseModel):
    token: str = Field(min_length=32, max_length=200, pattern=r"^[0-9a-fA-F]+$")
    platform: str = Field(pattern=r"^ios$")


def _open(path_provider: Callable[[], Path | None]):
    path = path_provider()
    if path is None:
        raise HTTPException(status_code=503, detail={"reason": "v4_not_configured"})
    try:
        return db.read(path)
    except db.NotReady:
        raise HTTPException(status_code=503, detail={"reason": "v4_db_not_ready"})


def create_router(*, require_token_dependency: Callable[..., Any],
                  db_path_provider: Callable[[], Path | None] = v4_db_path) -> APIRouter:
    router = APIRouter(prefix="/api/v4")

    @router.get("/health")
    def health() -> dict:
        path = db_path_provider()
        state = "not_configured" if path is None else "ready"
        latest = None
        if path is not None:
            try:
                with db.read(path) as conn:
                    report = db.latest_report(conn)
                latest = {"tradeDate": report["trade_date"], "status": report["status"]} if report else None
            except db.NotReady:
                state = "db_not_ready"
        return {"db": state, "latestReport": latest,
                "readPage": bool((os.environ.get("NECKLINE_READ_KEY") or "").strip()),
                "modelKey": bool((os.environ.get("DEEPSEEK_API_KEY") or "").strip())}

    auth = [Depends(require_token_dependency)]

    @router.get("/reports/latest", dependencies=auth)
    def latest() -> dict:
        with _open(db_path_provider) as conn:
            report = db.latest_report(conn)
        if report is None:
            raise HTTPException(status_code=404, detail={"reason": "no_report"})
        return report_out(report)

    @router.get("/reports", dependencies=auth)
    def history(limit: int = 30) -> dict:
        with _open(db_path_provider) as conn:
            reports = db.list_reports(conn, max(1, min(limit, 120)))
        return {"reports": [{"tradeDate": r["trade_date"], "status": r["status"], "availableAt": r["available_at"],
                             "entryCount": len((r["content"] or {}).get("entries", [])),
                             "failureReason": r["failure_reason"] if r["status"] == "failed" else None}
                            for r in reports]}

    @router.get("/reports/{trade_date}", dependencies=auth)
    def by_date(trade_date: str) -> dict:
        with _open(db_path_provider) as conn:
            report = db.get_report(conn, trade_date)
        if report is None:
            raise HTTPException(status_code=404, detail={"reason": "no_report"})
        return report_out(report)

    @router.get("/documents/{document_id}", dependencies=auth)
    def document(document_id: str) -> dict:
        with _open(db_path_provider) as conn:
            doc = db.get_document(conn, document_id)
        if doc is None:
            raise HTTPException(status_code=404, detail={"reason": "no_document"})
        return document_out(doc)

    @router.post("/devices", dependencies=auth)
    def register(device: DeviceIn) -> dict:
        path = db_path_provider()
        if path is None:
            raise HTTPException(status_code=503, detail={"reason": "v4_not_configured"})
        try:
            with db.write(path) as conn:
                db.upsert_device(conn, token=device.token.lower(), platform=device.platform,
                                 at=datetime.now(timezone.utc))
        except db.NotReady:
            raise HTTPException(status_code=503, detail={"reason": "v4_db_not_ready"})
        return {"ok": True}

    return router
