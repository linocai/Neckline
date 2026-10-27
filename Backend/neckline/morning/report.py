"""交易日晨报：08:00 采集后筛选出报，写进同一份晨报记录（推送、App、网页、MCP 共用），然后推送。

规则只来自已批准的配置；没批准、日历没覆盖、过了截止时刻都如实记为失败并推「晨报出问题了」，不补跑。
局部失败（某来源没取全、某家公司模型没判出来）只剔除相关条目，名单照常交付，并写一行缺口摘要。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from neckline.evaluation.evaluate import EvaluationRun, evaluate_candidates
from neckline.evaluation.market import TuShareSource
from neckline.evaluation.prepare import build_package
from neckline.evaluation.prompt import CONTRACT as EVALUATION_CONTRACT
from neckline.filings.fetch import FetchResult
from neckline.screening.classify import CONTRACT, Classification, classify
from neckline.screening.deepseek import DeepSeekClient
from neckline.screening.enrich import DbFilingReads, EnrichStats, enrich_filings, pdf_fetcher, purge_old_pdfs
from neckline.screening.pool import CompanyPool
from neckline.screening.prefilter import Message, Window, match_history, prefilter_window
from neckline.screening.select import Entry, Selection, select, validate_rules
from neckline.screening.text import section_heading

from . import db
from .schedule import CST, CalendarGap, deadline, is_trading_day, morning_window

logger = logging.getLogger(__name__)

SOURCE_LABELS = {"tushare-major-news": "TuShare 新闻", "jin10-flash": "金十快讯", "jin10-news": "金十资讯",
                 "cninfo-announcement": "巨潮公告"}
# 后来才上线的来源：它上线之前的窗口没有采集记录，不能因此报「没取全」；有过记录就和其他来源一样核对覆盖。
LATE_SOURCES = frozenset({"cninfo-announcement"})
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


_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _require(obj: object, key: str, label: str) -> object:
    if not isinstance(obj, dict) or key not in obj:
        raise ValueError(f"配置缺少 {label}")
    return obj[key]


def _require_dict(obj: object, key: str, label: str) -> dict:
    value = _require(obj, key, label)
    if not isinstance(value, dict):
        raise ValueError(f"{label} 类型不对")
    return value


def _require_str(obj: object, key: str, label: str) -> str:
    value = _require(obj, key, label)
    if not isinstance(value, str):
        raise ValueError(f"{label} 类型不对")
    return value


def _require_int(obj: object, key: str, label: str) -> int:
    value = _require(obj, key, label)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} 类型不对")
    return value


def _require_number(obj: object, key: str, label: str) -> int | float:
    value = _require(obj, key, label)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 类型不对")
    return value


def _require_min_int(obj: object, key: str, label: str, minimum: int) -> int:
    value = _require_int(obj, key, label)
    if value < minimum:
        raise ValueError(f"{label} 须 ≥ {minimum}")
    return value


def _require_positive_number(obj: object, key: str, label: str) -> int | float:
    value = _require_number(obj, key, label)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} 须为正数")
    return value


def _require_hhmm(obj: object, key: str, label: str) -> str:
    value = _require_str(obj, key, label)
    if _HHMM.fullmatch(value) is None:
        raise ValueError(f"{label} 须为 HH:MM")
    return value


def _minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _validate_screening_rules(rules: object) -> dict:
    """加载时核对晨报实际会读到的每个字段；缺了或类型不对就拒绝整份配置。"""
    if not isinstance(rules, dict):
        raise ValueError("配置类型不对")
    _require_str(rules, "configVersion", "configVersion")
    _require_str(rules, "approval", "approval")
    schedule = _require_dict(rules, "schedule", "schedule")
    _require_hhmm(schedule, "previousClose", "schedule.previousClose")
    _require_hhmm(schedule, "windowEnd", "schedule.windowEnd")
    _require_hhmm(schedule, "deadline", "schedule.deadline")
    model = _require_dict(rules, "model", "model")
    _require_str(model, "endpoint", "model.endpoint")
    _require_str(model, "name", "model.name")
    _require_positive_number(model, "timeoutSeconds", "model.timeoutSeconds")
    _require_min_int(model, "maxAttempts", "model.maxAttempts", 1)
    pool = _require_dict(rules, "companyPool", "companyPool")
    _require_str(pool, "file", "companyPool.file")
    _require_str(pool, "sha256", "companyPool.sha256")
    matching = _require_dict(rules, "matching", "matching")
    names = _require(matching, "strictShortNames", "matching.strictShortNames")
    if not isinstance(names, list) or any(not isinstance(item, str) for item in names):
        raise ValueError("matching.strictShortNames 类型不对")
    selection = _require_dict(rules, "selection", "selection")
    try:
        validate_rules(selection)
    except ValueError as exc:
        raise ValueError(f"selection：{exc}") from exc
    classification = _require_dict(rules, "classification", "classification")
    # 调用次数、并发、批量与长度预算：0 或负数会让出报静默变成空转，必须为正。
    for key in ("batchMaxCompanies", "batchMaxChars", "concurrency", "maxAttempts", "profileChars"):
        _require_min_int(classification, key, f"classification.{key}", 1)
    excerpt = _require_dict(classification, "excerpt", "classification.excerpt")
    # lead=0 是「不取导语」、window=0 是「只取命中词本身」，历史摘录就这样用；其余是长度预算，须为正。
    for key, minimum in (("lead", 0), ("window", 0), ("limit", 1), ("minChars", 1), ("blockMaxChars", 1)):
        _require_min_int(excerpt, key, f"classification.excerpt.{key}", minimum)
    history = _require_dict(classification, "history", "classification.history")
    # lookbackDays=0 或 maxItems=0 是「不带旧消息核对」；snippetChars=0 是「只带标题」；负数都会切错片段。
    for key in ("lookbackDays", "maxItems", "window", "snippetChars"):
        _require_min_int(history, key, f"classification.history.{key}", 0)
    options = _require(classification, "modelOptions", "classification.modelOptions")
    if not isinstance(options, dict):
        raise ValueError("classification.modelOptions 类型不对")
    filings = _require_dict(rules, "filings", "filings")
    for key in ("maxBytes", "maxPages", "concurrency", "cacheDays"):
        _require_min_int(filings, key, f"filings.{key}", 1)
    _require_positive_number(filings, "timeoutSeconds", "filings.timeoutSeconds")
    evaluation = _require_dict(rules, "evaluation", "evaluation")
    # 不联网必须写明，不能靠缺省。联网接上之前只允许 null。
    if "search" not in evaluation:
        raise ValueError("配置缺少 evaluation.search（不联网须显式写 null）")
    if evaluation["search"] is not None:
        raise ValueError("evaluation.search 须为 null")
    contract = _require_str(evaluation, "contract", "evaluation.contract")
    if contract != EVALUATION_CONTRACT:
        raise ValueError("evaluation.contract 须为 evaluation-v1")
    cutoff = _require_hhmm(evaluation, "cutoff", "evaluation.cutoff")
    if _minutes(cutoff) >= _minutes(schedule["deadline"]):
        raise ValueError("evaluation.cutoff 须早于 schedule.deadline")
    _require_min_int(evaluation, "concurrency", "evaluation.concurrency", 1)
    model_options = _require(evaluation, "modelOptions", "evaluation.modelOptions")
    if not isinstance(model_options, dict):
        raise ValueError("evaluation.modelOptions 类型不对")
    package_limits = _require_dict(evaluation, "package", "evaluation.package")
    for key in ("pdfMaxPages", "originalChars", "openingChars", "headingsMax", "mediaChars", "maxMedia", "pdfMaxBytes"):
        _require_min_int(package_limits, key, f"evaluation.package.{key}", 1)
    market_limits = _require_dict(evaluation, "market", "evaluation.market")
    for key in ("concurrency", "timeoutSeconds"):
        _require_min_int(market_limits, key, f"evaluation.market.{key}", 1)
    return rules


def load_config(name: str = "screening-v3.json", *, path: Path | None = None) -> LoadedConfig:
    package = resources.files("neckline.config")
    raw = path.read_bytes() if path is not None else (package / name).read_bytes()
    rules = _validate_screening_rules(json.loads(raw))
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
    """相邻切片差 1 秒（下一片从上片结束后 1 秒开始），这一秒不算缺口。"""
    reach = start
    step = timedelta(seconds=1)
    for lo, hi in sorted(intervals):
        if lo > reach + step:
            return False
        reach = max(reach, hi)
        if reach >= end:
            return True
    return reach >= end


def _covered_pair(pair: object) -> tuple[datetime, datetime] | None:
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        return None
    if not isinstance(pair[0], str) or not isinstance(pair[1], str):
        return None
    try:
        lo, hi = datetime.fromisoformat(pair[0]), datetime.fromisoformat(pair[1])
    except ValueError:
        return None
    if lo.tzinfo is None or hi.tzinfo is None:
        return None
    return lo, hi


def coverage_gaps(runs: list[dict], start: datetime, end: datetime) -> list[str]:
    """逐来源看窗口是否被完整的采集覆盖；没覆盖的写明最后一次的原因。

    不完整的记录若带 covered 切片，这些切片也算覆盖。没有该字段的旧记录只看 complete。
    后来才上线的来源（LATE_SOURCES）在传入的采集记录里一条都没有时跳过：它上线之前的窗口本来就没有采过。
    """
    gaps = []
    for key, label in SOURCE_LABELS.items():
        if key in LATE_SOURCES and not any(source.get("sourceKey") == key for run in runs for source in run["sources"]):
            continue
        intervals, last_error = [], None
        for run in runs:
            for source in run["sources"]:
                if source.get("sourceKey") != key:
                    continue
                if source.get("complete"):
                    intervals.append((datetime.fromisoformat(source["requestedStart"]),
                                      datetime.fromisoformat(source["requestedEnd"])))
                else:
                    for pair in source.get("covered") or []:
                        parsed = _covered_pair(pair)
                        if parsed is not None:
                            intervals.append(parsed)
                    if source.get("error"):
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


_CODE = re.compile(r"\d{6}(?:\.[A-Za-z]{2})?")
# 汇总稿的分节：「【简称：…】」，或行首「简称(六位代码)：…」。
_SECTION = re.compile(r"【[^】\n]{1,30}[：:][^】\n]{1,120}】|(?:^|\n)[^\S\n]*[^\s：:（(]{2,12}[（(]\d{6}(?:\.[A-Za-z]{2})?[）)][：:]")


def source_title(message: Message, company_code: str, pool: CompanyPool) -> str | None:
    """汇总稿点名多家公司时，用来源标题显示这家公司所在分节的小标题。

    点名池内不足两家，或正文切不出至少两家的分节时，保留原文标题。
    切得出分节但找不到这家公司的那一节，在原标题后注明「（汇总稿）」。不改消息本身。
    """
    original = message.title or None
    # 池外公司（如主板）也会占分节：正文本身切得出两节以上，就按汇总稿处理，哪怕池内只点名一家。
    sections = len(_SECTION.findall(message.text or ""))
    if len(message.companies) < 2 and sections < 2:
        return original
    found: dict[str, str] = {}
    for code in message.companies:
        company = pool.companies.get(code)
        if company is None:
            continue
        names = [company.name, *company.aliases, *message.companies.get(code, [])]
        heading = section_heading(message.text, [n for n in names if n and not _CODE.fullmatch(n)])
        if heading:
            found[code] = heading
    if len(found) < 2 and sections < 2:
        return original
    if company_code in found:
        return found[company_code]
    base = message.title or ""
    if base.endswith("（汇总稿）"):
        return base
    return f"{base}（汇总稿）" if base else "（汇总稿）"


def _entry(rank: int, e: Entry, pool: CompanyPool) -> dict:
    company, ev = pool.companies[e.company], e.primary
    return {
        "rank": rank, "companyCode": company.short_code, "companyName": company.name, "type": ev.type,
        "relation": ev.relation, "fresh": ev.fresh, "headline": ev.headline,
        "reason": ev.reason, "firstDisclosed": ev.first_disclosed,
        "sources": [{"documentId": f"{m.document_id}@{m.revision}", "sourceLabel": m.source_label,
                     "publishedAt": _cst(m.published_at), "title": source_title(m, e.company, pool), "url": m.url}
                    for m in e.messages],
        "others": [{"headline": o.headline, "type": o.type} for o in e.others],
    }


def build_content(*, report_id: str, day: date, window: Window, selection: Selection, pool: CompanyPool,
                  config_version: str, gap: str | None, generated_at: datetime, stats: Mapping[str, Any]) -> dict:
    return {
        "reportId": report_id, "tradeDate": day.isoformat(), "kind": "morning",
        "window": {"start": _cst(window.start), "end": _cst(window.end)}, "generatedAt": _cst(generated_at),
        "configVersion": config_version, "gapSummary": gap,
        "entries": [_entry(i, e, pool) for i, e in enumerate(selection.candidates, 1)],
        "stats": dict(stats),
    }


class Pusher:
    """给已注册的 iPhone 发「晨报已出」或「晨报出问题了」；同一期同一种只发一次。"""

    def __init__(self, db_path: Path, send: Callable[..., Any] | None, clock: Callable[[], datetime]) -> None:
        self.db_path, self.send, self.clock = db_path, send, clock

    def notify(self, *, report_id: str, kind: str, title: str, body: str) -> dict:
        lock_path = Path(str(self.db_path) + ".push.lock")
        with lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                return self._notify_locked(report_id=report_id, kind=kind, title=title, body=body)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _notify_locked(self, *, report_id: str, kind: str, title: str, body: str) -> dict:
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


def _ready_body(count: int, gap: str | None = None) -> str:
    text = f"{count} 家公司" if count else "今天没有符合条件的利好"
    if count == 0 and gap:
        text += "（有缺口）"
    return text


def _push_ready(pusher: Pusher, report_id: str, count: int, gap: str | None) -> dict:
    return pusher.notify(report_id=report_id, kind="ready", title="晨报已出", body=_ready_body(count, gap))


def _calendar_gap(day: date, exc: CalendarGap, pusher: Pusher, collection: str | None) -> dict:
    """交易日历没覆盖到这一年：工作日推一次，周末不推。不写晨报记录。"""
    result: dict[str, Any] = {"tradeDate": day.isoformat(), "state": "calendar_gap", "reason": str(exc)}
    if collection is not None:
        result["collection"] = collection
    if day.weekday() < 5:
        result["push"] = pusher.notify(report_id=f"morning-{day.isoformat()}", kind="problem", title="晨报出问题了",
                                       body=f"交易日历未覆盖 {day.year} 年，今天不出报")
    return result


def _stored_ready(pusher: Pusher, report_id: str, stored: Mapping[str, Any]) -> dict:
    entries = (stored.get("content") or {}).get("entries") or []
    return {"tradeDate": stored["trade_date"], "state": "available", "entries": len(entries),
            "gapSummary": stored.get("gap_summary"),
            "push": _push_ready(pusher, report_id, len(entries), stored.get("gap_summary"))}


def _run_evaluation(db_path: Path, *, report_id: str, day: date, window: Window, candidates: list,
                    config: LoadedConfig, client: DeepSeekClient, clock: Callable[[], datetime],
                    pdf: Callable[[str], FetchResult]) -> EvaluationRun:
    """select 之后准备并评估。没有候选时也返回一份空统计。"""
    settings = config.rules["evaluation"]
    if not candidates:
        return evaluate_candidates([], report_id=report_id, db_path=db_path, prepare=lambda _candidate: {},
                                   client=client, settings=settings, clock=clock)
    market = TuShareSource((os.environ.get("TUSHARE_TOKEN") or "").strip() or None,
                           timeout=float(settings["market"]["timeoutSeconds"]), now=clock)
    pool_view = SimpleNamespace(pool=config.pool, profiles=config.profiles)

    def prepare(candidate: object) -> dict:
        package = build_package(candidate, trade_date=day, window=window, pool=pool_view, market=market,
                                filings_store=DbFilingReads(db_path), fetcher=pdf,
                                settings=settings["package"], clock=clock)
        package["report"] = {
            "tradeDate": day.isoformat(),
            "windowStart": window.start.astimezone(CST).isoformat(timespec="seconds"),
            "windowEnd": window.end.astimezone(CST).isoformat(timespec="seconds"),
        }
        return package

    try:
        return evaluate_candidates(candidates, report_id=report_id, db_path=db_path, prepare=prepare,
                                   client=client, settings=settings, clock=clock)
    finally:
        market.close()


def _fail(db_path: Path, *, day: date, report_id: str, window: tuple[datetime, datetime], config: LoadedConfig,
          reason: str, now: datetime, pusher: Pusher) -> dict:
    with db.write(db_path) as conn:
        db.fail_report(conn, report_id=report_id, trade_date=day.isoformat(), window_start=window[0],
                       window_end=window[1], config_version=config.rules["configVersion"], config_sha256=config.sha256,
                       reason=reason, at=now)
        stored = db.get_report(conn, day.isoformat())
    if stored and stored["status"] == "available":
        return _stored_ready(pusher, report_id, stored)
    if not stored or stored["status"] != "failed":
        return {"tradeDate": day.isoformat(), "state": stored["status"] if stored else "running",
                "reason": stored.get("failure_reason") if stored else reason}
    push = pusher.notify(report_id=report_id, kind="problem", title="晨报出问题了", body=stored["failure_reason"] or reason)
    return {"tradeDate": day.isoformat(), "state": stored["status"], "reason": stored["failure_reason"], "push": push}


def run_morning(db_path: Path, *, now: datetime, config: LoadedConfig, collector: Callable[[datetime], dict],
                client_factory: Callable[[str], DeepSeekClient], pusher: Pusher,
                clock: Callable[[], datetime],
                fetcher: Callable[[str], FetchResult] | None = None) -> dict:
    """幂等：同一交易日重复运行只会续上未完成的部分；回执保证同样的模型请求不重复计费。

    先看当天报告：已有 running 就是续跑，不再采集，消息边界用首次写入的 started_at。
    """
    rules = config.rules
    day = now.astimezone(CST).date()
    report_id = f"morning-{day.isoformat()}"
    with db.read(db_path) as conn:
        existing = db.get_report(conn, day.isoformat())
    resuming = bool(existing and existing["status"] == "running")
    if resuming:
        collection = {"status": "skipped_resume"}
    else:
        collection = collector(now)
    try:
        trading = is_trading_day(day)
    except CalendarGap as exc:
        return _calendar_gap(day, exc, pusher, collection["status"])
    if not trading:
        return {"tradeDate": day.isoformat(), "state": "not_trading_day", "collection": collection["status"]}
    schedule = rules["schedule"]
    start, end = morning_window(day, schedule)
    if existing and existing["status"] == "available":
        return _stored_ready(pusher, report_id, existing)
    if existing and existing["status"] == "failed":
        return {"tradeDate": day.isoformat(), "state": "failed", "reason": existing["failure_reason"]}
    if now > deadline(day, schedule):
        return _fail(db_path, day=day, report_id=report_id, window=(start, end), config=config,
                     reason=f"{schedule['deadline']} 前没能出报", now=now, pusher=pusher)
    if rules.get("approval") != "approved":
        return _fail(db_path, day=day, report_id=report_id, window=(start, end), config=config,
                     reason="筛选规则未经批准", now=now, pusher=pusher)
    if resuming:
        collected_by = datetime.fromisoformat(existing["started_at"])
    else:
        collected_by = clock()
        with db.write(db_path) as conn:
            db.begin_report(conn, report_id=report_id, trade_date=day.isoformat(), window_start=start, window_end=end,
                            config_version=rules["configVersion"], config_sha256=config.sha256, started_at=collected_by)
            stored = db.get_report(conn, day.isoformat())
        collected_by = datetime.fromisoformat(stored["started_at"])
    window = Window(start=start, end=end, collected_by=collected_by)
    settings = rules["classification"]
    with db.read(db_path) as conn:
        docs = list(db.iter_documents(conn, published_from=start, published_to=end, fetched_by=collected_by,
                                      include_end=True))
        runs = db.collections_since(conn, start - timedelta(days=3))
    messages, in_window, duplicates, unmatched = prefilter_window(docs, config.pool)
    del docs
    filings_dir = db_path.parent / "filings"
    filings_settings = rules["filings"]
    purge_old_pdfs(filings_dir, cache_days=int(filings_settings["cacheDays"]), now=clock())
    pdf = fetcher or pdf_fetcher(filings_dir, filings_settings)
    filing_stats: EnrichStats = enrich_filings(
        messages, store=DbFilingReads(db_path), fetcher=pdf, settings=filings_settings, clock=clock)
    candidates = config.pool.subset({code for m in messages for code in m.companies})
    with db.read(db_path) as conn:
        history = match_history(db.iter_documents(conn, published_from=start - timedelta(days=settings["history"]["lookbackDays"]),
                                                  published_to=start, fetched_by=collected_by, include_end=False),
                                candidates)
    client = client_factory(report_id)
    model_started = clock()
    result: Classification = classify(messages, history, config.pool, config.profiles, client,
                                      window=window, settings=settings)
    classified_at = clock()
    if result.fatal:
        return _fail(db_path, day=day, report_id=report_id, window=(start, end), config=config,
                     reason=result.fatal, now=clock(), pusher=pusher)
    if messages and not result.results:
        # 有候选消息却一家也没判：绝不当成「今天没有利好」交出空名单。
        return _fail(db_path, day=day, report_id=report_id, window=(start, end), config=config,
                     reason="有候选消息但没有任何判断结果", now=clock(), pusher=pusher)
    if result.results and all(item.error for item in result.results.values()):
        reason = next(item.error for item in result.results.values() if item.error)
        return _fail(db_path, day=day, report_id=report_id, window=(start, end), config=config,
                     reason=reason, now=clock(), pusher=pusher)
    selection = select(result.by_id, result.results, rules["selection"])
    evaluation = _run_evaluation(db_path, report_id=report_id, day=day, window=window,
                                 candidates=selection.candidates, config=config, client=client, clock=clock, pdf=pdf)
    if evaluation.fatal:
        return _fail(db_path, day=day, report_id=report_id, window=(start, end), config=config,
                     reason=evaluation.fatal, now=clock(), pusher=pusher)
    gaps = coverage_gaps(runs, start, end)
    summary = gap_summary(gaps, selection, config.pool)
    usage: dict[str, int] = {}
    for batch in result.batches:
        for key in ("prompt_tokens", "completion_tokens"):
            usage[key] = usage.get(key, 0) + int(batch.usage.get(key) or 0)
    finished = clock()
    stats = {"contract": CONTRACT, "inWindow": in_window, "duplicates": duplicates, "unmatched": unmatched,
             "messages": len(messages), "history": len(history), "companies": len(result.results),
             "classification": {"calls": len(result.batches),
                                "fromReceipt": sum(b.from_receipt for b in result.batches),
                                "usage": usage,
                                "seconds": round((classified_at - model_started).total_seconds(), 1)},
             "evaluation": evaluation.stats, "prepareSeconds": evaluation.prepare_seconds,
             "gaps": [{"company": g.company, "error": g.error} for g in selection.gaps],
             "collectionGaps": gaps, "collection": collection["status"],
             "candidates": len(selection.candidates), "filings": filing_stats.as_dict()}
    if finished > deadline(day, schedule):
        return _fail(db_path, day=day, report_id=report_id, window=(start, end), config=config,
                     reason=f"{schedule['deadline']} 前没能出报", now=finished, pusher=pusher)
    content = build_content(report_id=report_id, day=day, window=window, selection=selection, pool=config.pool,
                            config_version=rules["configVersion"], gap=summary, generated_at=finished, stats=stats)
    with db.write(db_path) as conn:
        updated = db.complete_report(conn, report_id=report_id, content=content, stats=stats, gap_summary=summary,
                                     available_at=finished)
    if updated == 0:
        with db.read(db_path) as conn:
            stored = db.get_report(conn, day.isoformat())
        if stored and stored["status"] == "available":
            return _stored_ready(pusher, report_id, stored)
        return {"tradeDate": day.isoformat(), "state": stored["status"] if stored else "running",
                "reason": stored.get("failure_reason") if stored else None}
    push = _push_ready(pusher, report_id, len(content["entries"]), summary)
    return {"tradeDate": day.isoformat(), "state": "available", "entries": len(content["entries"]),
            "gapSummary": summary, "push": push, "stats": stats}


def finalize(db_path: Path, *, now: datetime, config: LoadedConfig, pusher: Pusher) -> dict:
    """截止时刻（09:15）兜底：当天还没出报的，如实记为失败并推送。

    已经出完或已经失败的，再推一次，只补没送到的设备。非交易日什么都不做。
    """
    day = now.astimezone(CST).date()
    try:
        if not is_trading_day(day):
            return {"tradeDate": day.isoformat(), "state": "not_trading_day"}
    except CalendarGap as exc:
        return _calendar_gap(day, exc, pusher, None)
    report_id = f"morning-{day.isoformat()}"
    with db.read(db_path) as conn:
        existing = db.get_report(conn, day.isoformat())
    if existing and existing["status"] == "available":
        return _stored_ready(pusher, report_id, existing)
    if existing and existing["status"] == "failed":
        push = pusher.notify(report_id=report_id, kind="problem", title="晨报出问题了",
                             body=existing["failure_reason"] or "晨报出问题了")
        return {"tradeDate": day.isoformat(), "state": "failed", "reason": existing["failure_reason"], "push": push}
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
