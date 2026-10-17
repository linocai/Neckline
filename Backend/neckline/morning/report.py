"""交易日晨报：08:00 采集后筛选出报，写进同一份晨报记录（推送、App、网页、MCP 共用），然后推送。

规则只来自已批准的配置；没批准、日历没覆盖、过了截止时刻都如实记为失败并推「晨报出问题了」，不补跑。
局部失败（某来源没取全、某家公司模型没判出来）只剔除相关条目，名单照常交付，并写一行缺口摘要。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Mapping

from neckline.screening.classify import CONTRACT, Classification, classify
from neckline.screening.deepseek import DeepSeekClient
from neckline.screening.pool import CompanyPool
from neckline.screening.prefilter import Window, match_history, prefilter_window
from neckline.screening.select import Entry, Selection, select

from . import db
from .schedule import CST, CalendarGap, deadline, is_trading_day, morning_window

logger = logging.getLogger(__name__)

SOURCE_LABELS = {"tushare-major-news": "TuShare 新闻", "jin10-flash": "金十快讯", "jin10-news": "金十资讯"}
ERROR_LABELS = {
    "history_unavailable": "来源翻不到窗口起点", "page_limit": "翻页到上限", "request_bound_reached": "请求数到上限",
    "credential_missing": "凭据缺失", "unauthorized": "凭据被拒", "rate_limited": "被限流",
    "quota_exceeded": "达到每日调用上限",
    "transport_error": "网络错误", "bootstrap_start_missing": "没有采集起点",
}


@dataclass(frozen=True)
class LoadedConfig:
    rules: dict
    sha256: str
    pool: CompanyPool
    profiles: dict[str, str | None]


def load_config(name: str = "screening-v1.json") -> LoadedConfig:
    package = resources.files("neckline.config")
    raw = (package / name).read_bytes()
    rules = json.loads(raw)
    pool_raw = (package / rules["companyPool"]["file"]).read_bytes()
    if hashlib.sha256(pool_raw).hexdigest() != rules["companyPool"]["sha256"]:
        raise ValueError("公司池文件与配置记录的哈希不符")
    rows = [json.loads(line) for line in pool_raw.decode("utf-8").splitlines() if line.strip()]
    pool = CompanyPool.from_rows(rows, rules["matching"]["strictShortNames"])
    return LoadedConfig(rules=rules, sha256=hashlib.sha256(raw).hexdigest(), pool=pool,
                        profiles={r["code"]: r["index"].get("summary") for r in rows})


def _cst(ts: datetime) -> str:
    return ts.astimezone(CST).isoformat(timespec="minutes")


def _covered(intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> bool:
    reach = start
    for lo, hi in sorted(intervals):
        if lo > reach:
            return False
        reach = max(reach, hi)
        if reach >= end:
            return True
    return reach >= end


def coverage_gaps(runs: list[dict], start: datetime, end: datetime) -> list[str]:
    """逐来源看窗口是否被完整的采集覆盖；没覆盖的写明最后一次的原因。"""
    gaps = []
    for key, label in SOURCE_LABELS.items():
        intervals, last_error = [], None
        for run in runs:
            for source in run["sources"]:
                if source.get("sourceKey") != key:
                    continue
                if source.get("complete"):
                    intervals.append((datetime.fromisoformat(source["requestedStart"]),
                                      datetime.fromisoformat(source["requestedEnd"])))
                elif source.get("error"):
                    last_error = source["error"]
        if not _covered(intervals, start, end):
            reason = ERROR_LABELS.get(str(last_error).split(":")[0], "没取全") if last_error else "没取全"
            gaps.append(f"{label}{reason}")
    return gaps


def gap_summary(collection_gaps: list[str], selection: Selection, pool: CompanyPool) -> str | None:
    parts = []
    if collection_gaps:
        parts.append("采集不完整：" + "、".join(collection_gaps))
    if selection.gaps:
        names = "、".join(pool.companies[g.company].name for g in selection.gaps[:5])
        more = f"等 {len(selection.gaps)} 家" if len(selection.gaps) > 5 else ""
        parts.append(f"{names}{more}没能完成判断，未列入")
    return "；".join(parts) if parts else None


def _entry(rank: int, e: Entry, pool: CompanyPool) -> dict:
    company, ev = pool.companies[e.company], e.primary
    return {
        "rank": rank, "companyCode": company.short_code, "companyName": company.name, "type": ev.type,
        "importance": ev.importance, "relation": ev.relation, "fresh": ev.fresh, "headline": ev.headline,
        "reason": ev.reason, "firstDisclosed": ev.first_disclosed,
        "sources": [{"documentId": m.document_id, "sourceLabel": m.source_label, "publishedAt": _cst(m.published_at),
                     "title": m.title or None, "url": m.url} for m in e.messages],
        "others": [{"headline": o.headline, "type": o.type, "importance": o.importance} for o in e.others],
    }


def build_content(*, report_id: str, day: date, window: Window, selection: Selection, pool: CompanyPool,
                  config_version: str, gap: str | None, generated_at: datetime, stats: Mapping[str, Any]) -> dict:
    sections = selection.sections
    return {
        "reportId": report_id, "tradeDate": day.isoformat(), "kind": "morning",
        "window": {"start": _cst(window.start), "end": _cst(window.end)}, "generatedAt": _cst(generated_at),
        "configVersion": config_version, "gapSummary": gap,
        "entries": [_entry(i, e, pool) for i, e in enumerate(sections["main"], 1)],
        "review": {name: [_entry(i, e, pool) for i, e in enumerate(sections[key], 1)]
                   for name, key in (("types", "review_types"), ("relation", "review_relation"),
                                     ("fresh", "review_fresh"), ("old", "held_old"))},
        "stats": dict(stats),
    }


class Pusher:
    """给已注册的 iPhone 发「晨报已出」或「晨报出问题了」；同一期同一种只发一次。"""

    def __init__(self, db_path: Path, send: Callable[..., Any] | None, clock: Callable[[], datetime]) -> None:
        self.db_path, self.send, self.clock = db_path, send, clock

    def notify(self, *, report_id: str, kind: str, title: str, body: str) -> dict:
        with db.read(self.db_path) as conn:
            tokens = db.device_tokens(conn)
        if not tokens:
            return {"devices": 0}
        if self.send is None:
            return {"devices": len(tokens), "skipped": "apns_unavailable"}
        sent = failed = 0
        for token in tokens:
            with db.read(self.db_path) as conn:
                if db.push_done(conn, report_id=report_id, kind=kind, token=token):
                    continue
            result = self.send(token, title, body, thread_id="morning", collapse_id=f"{report_id}-{kind}"[:64])
            ok = bool(getattr(result, "ok", False))
            sent, failed = sent + ok, failed + (not ok)
            with db.write(self.db_path) as conn:
                db.log_push(conn, report_id=report_id, kind=kind, token=token, state="sent" if ok else "failed",
                            detail=None if ok else str(getattr(result, "reason", ""))[:120], at=self.clock())
        return {"devices": len(tokens), "sent": sent, "failed": failed}


def _ready_body(count: int) -> str:
    return f"{count} 家公司" if count else "今天没有符合条件的利好"


def _fail(db_path: Path, *, day: date, report_id: str, window: tuple[datetime, datetime], config: LoadedConfig,
          reason: str, now: datetime, pusher: Pusher) -> dict:
    with db.write(db_path) as conn:
        db.fail_report(conn, report_id=report_id, trade_date=day.isoformat(), window_start=window[0],
                       window_end=window[1], config_version=config.rules["configVersion"], config_sha256=config.sha256,
                       reason=reason, at=now)
        stored = db.get_report(conn, day.isoformat())
    push = pusher.notify(report_id=report_id, kind="problem", title="晨报出问题了", body=stored["failure_reason"] or reason)
    return {"tradeDate": day.isoformat(), "state": stored["status"], "reason": stored["failure_reason"], "push": push}


def run_morning(db_path: Path, *, now: datetime, config: LoadedConfig, collector: Callable[[datetime], dict],
                client_factory: Callable[[str], DeepSeekClient], pusher: Pusher,
                clock: Callable[[], datetime]) -> dict:
    """幂等：同一交易日重复运行只会续上未完成的部分；回执保证同样的模型请求不重复计费。"""
    rules = config.rules
    day = now.astimezone(CST).date()
    collection = collector(now)
    try:
        trading = is_trading_day(day)
    except CalendarGap as exc:
        return {"tradeDate": day.isoformat(), "state": "calendar_gap", "reason": str(exc), "collection": collection["status"]}
    if not trading:
        return {"tradeDate": day.isoformat(), "state": "not_trading_day", "collection": collection["status"]}
    schedule = rules["schedule"]
    start, end = morning_window(day, schedule)
    report_id = f"morning-{day.isoformat()}"
    with db.write(db_path) as conn:
        existing = db.get_report(conn, day.isoformat())
    if existing and existing["status"] == "available":
        count = len(existing["content"]["entries"])
        push = pusher.notify(report_id=report_id, kind="ready", title="晨报已出", body=_ready_body(count))
        return {"tradeDate": day.isoformat(), "state": "available", "entries": count, "push": push}
    if existing and existing["status"] == "failed":
        return {"tradeDate": day.isoformat(), "state": "failed", "reason": existing["failure_reason"]}
    if now > deadline(day, schedule):
        return _fail(db_path, day=day, report_id=report_id, window=(start, end), config=config,
                     reason=f"{schedule['deadline']} 前没能出报", now=now, pusher=pusher)
    if rules.get("approval") != "approved":
        return _fail(db_path, day=day, report_id=report_id, window=(start, end), config=config,
                     reason="筛选规则未经批准", now=now, pusher=pusher)
    with db.write(db_path) as conn:
        db.begin_report(conn, report_id=report_id, trade_date=day.isoformat(), window_start=start, window_end=end,
                        config_version=rules["configVersion"], config_sha256=config.sha256, started_at=clock())
    collected_by = clock()
    window = Window(start=start, end=end, collected_by=collected_by)
    settings = rules["classification"]
    with db.read(db_path) as conn:
        docs = list(db.iter_documents(conn, published_from=start, published_to=end, fetched_by=collected_by,
                                      include_end=True))
        runs = db.collections_since(conn, start - timedelta(days=3))
    messages, in_window, duplicates, unmatched = prefilter_window(docs, config.pool)
    del docs
    candidates = config.pool.subset({code for m in messages for code in m.companies})
    with db.read(db_path) as conn:
        history = match_history(db.iter_documents(conn, published_from=start - timedelta(days=settings["history"]["lookbackDays"]),
                                                  published_to=start, fetched_by=collected_by, include_end=False),
                                candidates)
    model_started = clock()
    result: Classification = classify(messages, history, config.pool, config.profiles, client_factory(report_id),
                                      window=window, settings=settings)
    selection = select(result.by_id, result.results, rules["selection"])
    gaps = coverage_gaps(runs, start, end)
    summary = gap_summary(gaps, selection, config.pool)
    usage: dict[str, int] = {}
    for batch in result.batches:
        for key in ("prompt_tokens", "completion_tokens"):
            usage[key] = usage.get(key, 0) + int(batch.usage.get(key) or 0)
    finished = clock()
    stats = {"contract": CONTRACT, "inWindow": in_window, "duplicates": duplicates, "unmatched": unmatched,
             "messages": len(messages), "history": len(history), "companies": len(result.results),
             "calls": len(result.batches), "fromReceipt": sum(b.from_receipt for b in result.batches),
             "modelSeconds": round((finished - model_started).total_seconds(), 1), "usage": usage,
             "gaps": [{"company": g.company, "error": g.error} for g in selection.gaps],
             "collectionGaps": gaps, "collection": collection["status"],
             **{name: len(entries) for name, entries in selection.sections.items()}}
    content = build_content(report_id=report_id, day=day, window=window, selection=selection, pool=config.pool,
                            config_version=rules["configVersion"], gap=summary, generated_at=finished, stats=stats)
    with db.write(db_path) as conn:
        db.complete_report(conn, report_id=report_id, content=content, stats=stats, gap_summary=summary,
                           available_at=finished)
    push = pusher.notify(report_id=report_id, kind="ready", title="晨报已出", body=_ready_body(len(content["entries"])))
    return {"tradeDate": day.isoformat(), "state": "available", "entries": len(content["entries"]),
            "gapSummary": summary, "push": push, "stats": stats}


def finalize(db_path: Path, *, now: datetime, config: LoadedConfig, pusher: Pusher) -> dict:
    """截止时刻（09:15）兜底：当天还没出报的，如实记为失败并推送；已出或非交易日什么都不做。"""
    day = now.astimezone(CST).date()
    try:
        if not is_trading_day(day):
            return {"tradeDate": day.isoformat(), "state": "not_trading_day"}
    except CalendarGap as exc:
        return {"tradeDate": day.isoformat(), "state": "calendar_gap", "reason": str(exc)}
    report_id = f"morning-{day.isoformat()}"
    with db.write(db_path) as conn:
        existing = db.get_report(conn, day.isoformat())
    if existing and existing["status"] in ("available", "failed"):
        return {"tradeDate": day.isoformat(), "state": existing["status"]}
    reason = f"{config.rules['schedule']['deadline']} 前没能出报"
    if existing and existing.get("failure_reason"):
        reason += f"（{existing['failure_reason']}）"
    elif existing is None:
        reason += "（出报任务没有运行）"
    return _fail(db_path, day=day, report_id=report_id, window=morning_window(day, config.rules["schedule"]),
                 config=config, reason=reason, now=now, pusher=pusher)


def note_error(db_path: Path, *, day: date, message: str) -> None:
    """出报中途出错：记下原因（状态仍是 running，供重启续跑和截止兜底引用）。"""
    with db.write(db_path) as conn:
        conn.execute("UPDATE reports SET failure_reason=? WHERE trade_date=? AND status='running'",
                     (message[:200], day.isoformat()))
