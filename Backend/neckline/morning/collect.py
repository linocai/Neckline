"""每天 08:00、20:00 采集：三个来源各自从上次完整覆盖处（减回看时长）取到现在，存进晨报库。

某个来源失败不影响其他来源；没有覆盖记录的来源必须显式给出起点（首次上线的 bootstrap），不猜。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from neckline.news import jin10, tushare
from neckline.news.model import SourceOutcome

from . import db

logger = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))
SOURCES = ("tushare-major-news", "jin10-flash", "jin10-news")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _fetch(key: str, *, start: datetime, end: datetime, settings: Mapping[str, Any], jin10_settings: Mapping[str, Any],
           tokens: Mapping[str, str | None], jin10_factory: Callable[..., jin10.Jin10Client],
           tushare_post: tushare.Post | None, clock: Callable[[], datetime], max_pages: int | None) -> SourceOutcome:
    if key == "tushare-major-news":
        post = tushare_post or tushare.http_post(float(settings["timeoutSeconds"]))
        return tushare.fetch_major_news(post, tokens.get("TUSHARE_TOKEN"), start=start, end=end,
                                        max_requests=max_pages or int(settings["maxRequests"]), clock=clock)
    token = tokens.get("JIN10_MCP_TOKEN")
    if not token:
        outcome = SourceOutcome(source_key=key, requested_start=start, requested_end=end)
        outcome.error = "credential_missing"
        return outcome
    with jin10_factory(token=token, endpoint=jin10_settings["endpoint"], protocol_version=jin10_settings["protocolVersion"],
                       timeout_seconds=float(settings["timeoutSeconds"])) as client:
        return jin10.fetch_list(client, key, start=start, end=end, max_pages=max_pages or int(settings["maxPages"]),
                                clock=clock)


def run_collection(db_path: Path, *, now: datetime, config: Mapping[str, Any], tokens: Mapping[str, str | None],
                   bootstrap_start: datetime | None = None, max_pages: int | None = None,
                   jin10_factory: Callable[..., jin10.Jin10Client] = jin10.Jin10Client,
                   tushare_post: tushare.Post | None = None, clock: Callable[[], datetime] = _utc_now) -> dict:
    run_id = "collect-" + now.astimezone(CST).strftime("%Y%m%dT%H%M%S")
    with db.write(db_path) as conn:
        db.begin_collection(conn, run_id=run_id, slot_at=now, started_at=clock())
        marks = {key: db.source_watermark(conn, key) for key in SOURCES}
    summaries = []
    for key in SOURCES:
        settings = config["sources"][key]
        mark = marks[key]
        if mark is None and bootstrap_start is None:
            outcome = SourceOutcome(source_key=key, requested_start=now, requested_end=now)
            outcome.error = "bootstrap_start_missing"
        else:
            start = bootstrap_start if mark is None else mark - timedelta(seconds=int(settings["lookbackSeconds"]))
            try:
                outcome = _fetch(key, start=start, end=now, settings=settings, jin10_settings=config["jin10"],
                                 tokens=tokens, jin10_factory=jin10_factory, tushare_post=tushare_post,
                                 clock=clock, max_pages=max_pages)
            except Exception as exc:  # 异常正文可能带来源私有内容，只记类型
                logger.warning("采集 %s 失败：%s", key, type(exc).__name__)
                outcome = SourceOutcome(source_key=key, requested_start=start, requested_end=now)
                outcome.error = f"source_failed:{type(exc).__name__}"
        with db.write(db_path) as conn:
            added = db.store_documents(conn, outcome.documents)
        summaries.append({
            "sourceKey": key, "requestedStart": outcome.requested_start.isoformat(),
            "requestedEnd": outcome.requested_end.isoformat(), "complete": outcome.complete, "error": outcome.error,
            "requests": outcome.requests, "documents": len(outcome.documents), "newVersions": added,
            "rejected": outcome.rejected,
            "oldestSeen": outcome.oldest_seen.isoformat() if outcome.oldest_seen else None,
        })
    if all(s["complete"] for s in summaries):
        status = "completed"
    elif any(s["documents"] for s in summaries):
        status = "partial"
    else:
        status = "failed"
    with db.write(db_path) as conn:
        db.finish_collection(conn, run_id=run_id, status=status, sources=summaries, finished_at=clock())
    return {"runId": run_id, "status": status, "sources": summaries}
