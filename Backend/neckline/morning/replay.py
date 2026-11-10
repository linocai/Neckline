"""离线回放一期晨报。复制只读快照后再跑，原快照不写。

  python -m neckline.morning.replay --snapshot <只读快照库> --trade-date YYYY-MM-DD \\
      --work <目录> --market-snapshot <json> --receipts <回执目录> --filings-cache <目录> \\
      [--config screening-v7.json] (--pool-snapshot <json> | --pool-seed-only) \\
      [--sector-snapshot <json>] [--limit-snapshot <json>]

业务时钟从快照里该日晨报的 started_at 起步，按真实流逝时间前进。
采集不采，推送不发。行情读快照，模型回执落在 --receipts，同样请求不重复计费。
股票池必须显式给 --pool-snapshot（按交易日构造）或 --pool-seed-only（只用别名种子）。两者都不传就停，不联网。
给了 --sector-snapshot 时，概念、池内成分和全 A 成分从这份 JSON 写入工作库，不联网。
给了 --limit-snapshot 时，只把报告日之前的涨停写入工作库。回放从不联网取涨停。
搜索和模型仍走回执目录。evaluation.search 为 null 时不搜。
密钥和模型名只从 DEEPSEEK_API_KEY、DEEPSEEK_MODEL 读取，不打印。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from neckline.evaluation.market import SnapshotSource
from neckline.evaluation.search import SearchClient
from neckline.morning import db
from neckline.morning.report import Pusher, load_config, run_morning
from neckline.morning.schedule import CST, CalendarGap, previous_trading_day
from neckline.morning.sector_recognition import install_limit_snapshot
from neckline.morning.sectors import SectorFetchError, install_sector_snapshot
from neckline.morning.stock_pool import build_pool_rows, rows_from_seed, store_rows
from neckline.screening.deepseek import DeepSeekClient, FileReceipts
from neckline.screening.enrich import pdf_fetcher

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_QUESTIONS = (
    ("value", "利好值多少"),
    ("priced", "市场已反映多少"),
    ("money", "资金为什么买或不买"),
    ("price", "价格划不划算"),
)
_TOKEN_KEYS = (
    ("prompt_tokens", "输入"),
    ("completion_tokens", "输出"),
    ("prompt_cache_hit_tokens", "缓存命中"),
    ("reasoning_tokens", "思考"),
)


class ReplayError(RuntimeError):
    pass


class ReplayClock:
    """起点是快照里的开工时刻，之后按真实用掉的秒数前进。"""

    def __init__(self, start: datetime) -> None:
        self.start = start
        self._t0 = time.monotonic()

    def __call__(self) -> datetime:
        return self.start + timedelta(seconds=time.monotonic() - self._t0)


def _required_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise ReplayError(f"缺少 {name}")
    return value


def _replace_copy(src: Path, dest: Path) -> None:
    if src.resolve() == dest.resolve():
        raise ReplayError("工作库不能是快照本身")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(dest) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    if dest.exists():
        dest.unlink()
    # 只复制内容、不带权限：快照常设成只读，工作库要能写。
    shutil.copyfile(src, dest)
    dest.chmod(0o600)


def _clear_day(conn, trade_date: str) -> None:
    """这一期从头重跑：删掉快照里这一期的晨报、评估和板块事件，免得沿用线上当时的结论。"""
    report_id = f"morning-{trade_date}"
    conn.execute("DELETE FROM evaluations WHERE report_id=?", (report_id,))
    conn.execute("DELETE FROM sector_events WHERE report_id=?", (report_id,))
    conn.execute("DELETE FROM reports WHERE trade_date=?", (trade_date,))


def _started_at(path: Path, trade_date: str) -> datetime:
    with db.read(path) as conn:
        report = db.get_report(conn, trade_date)
    if report is None or not report.get("started_at"):
        raise ReplayError(f"快照里没有 {trade_date} 的晨报开工时刻")
    started = datetime.fromisoformat(str(report["started_at"]))
    if started.tzinfo is None:
        raise ReplayError("开工时刻没有时区")
    return started


def _question_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        text = value.get("text")
        return text.strip() if isinstance(text, str) else ""
    return ""


def _question_strings(raw: object) -> dict[str, str]:
    data = raw if isinstance(raw, dict) else {}
    return {key: _question_text(data.get(key)) for key, _label in _QUESTIONS}


def _sample_entry(entry: dict) -> list[str]:
    lines = [
        f"## {entry.get('companyCode') or ''} {entry.get('companyName') or ''}".rstrip(),
        "",
        str(entry.get("title") or ""),
        "",
        f"结论：{entry.get('conclusion') or ''}",
        "",
    ]
    questions = entry.get("questions") if isinstance(entry.get("questions"), dict) else {}
    for key, label in _QUESTIONS:
        lines.append(label)
        lines.append(str(questions.get(key) or ""))
        lines.append("")
    lines.append(f"风险：{entry.get('risk') or ''}")
    lines.append("")
    lines.append("各方观点")
    views = entry.get("views") if isinstance(entry.get("views"), list) else []
    if not views:
        lines.append("（没有）")
    for view in views:
        if not isinstance(view, dict):
            continue
        lines.append(f"- {view.get('by') or ''} · {view.get('name') or ''}：{view.get('text') or ''}")
        if view.get("sourceTitle") or view.get("sourceUrl"):
            lines.append(f"  来源：{view.get('sourceTitle') or ''} {view.get('sourceUrl') or ''}".rstrip())
    lines.append("")
    lines.append("原文")
    sources = entry.get("sources") if isinstance(entry.get("sources"), list) else []
    if not sources:
        lines.append("（没有）")
    for source in sources:
        if not isinstance(source, dict):
            continue
        lines.append(
            f"- {source.get('sourceLabel') or ''} {source.get('publishedAt') or ''} "
            f"{source.get('title') or ''} {source.get('url') or ''}".rstrip())
    lines.append("")
    return lines


def _sample_skip(row: dict) -> list[str]:
    output = row.get("output") if isinstance(row.get("output"), dict) else {}
    package = row.get("package") if isinstance(row.get("package"), dict) else {}
    company = package.get("company") if isinstance(package.get("company"), dict) else {}
    name = company.get("name") if isinstance(company.get("name"), str) else ""
    shown = {
        "companyCode": row.get("company") or "",
        "companyName": name,
        "title": output.get("title") or "",
        "conclusion": output.get("conclusion") or "",
        "questions": _question_strings(output.get("questions")),
        "risk": output.get("risk") or "",
        "views": [view for view in output.get("views") or [] if isinstance(view, dict)],
        "sources": [],
    }
    lines = [f"### {row.get('company') or ''} {name}".rstrip(), "", "不推", ""]
    lines.extend(_sample_entry(shown))
    lines.append("```json")
    lines.append(json.dumps(output, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    return lines


def _samples(content: dict | None, evaluations: list[dict]) -> str:
    lines = ["# 晨报样例", ""]
    entries = (content or {}).get("entries") or []
    if not entries:
        evaluated = (content or {}).get("evaluated", 0)
        lines.append("今天没有值得推的")
        lines.append(f"评估了 {evaluated} 家公司的隔夜消息，没有值得推的")
        lines.append("")
    for entry in entries:
        if isinstance(entry, dict):
            lines.extend(_sample_entry(entry))
    skipped = [row for row in evaluations if row.get("state") == "succeeded" and row.get("verdict") == "不推"]
    skipped.sort(key=lambda row: str(row.get("company") or ""))
    lines.append("## 不推对照")
    lines.append("")
    chosen = skipped[:3]
    if not chosen:
        lines.append("这一期没有判不推的评估。")
        lines.append("")
    else:
        lines.append(f"以下 {len(chosen)} 条判了不推。数据包在 evaluations.json。")
        lines.append("")
        for row in chosen:
            lines.extend(_sample_skip(row))
    return "\n".join(lines).rstrip() + "\n"


def _tokens(stats: dict) -> dict[str, int]:
    totals = {key: 0 for key, _label in _TOKEN_KEYS}
    for block_name in ("classification", "evaluation"):
        block = stats.get(block_name) if isinstance(stats.get(block_name), dict) else {}
        usage = block.get("usage") if isinstance(block.get("usage"), dict) else {}
        for key, _label in _TOKEN_KEYS:
            totals[key] += int(usage.get(key) or 0)
    return {label: totals[key] for key, label in _TOKEN_KEYS}


def _output_leaders(row: dict) -> list[dict]:
    output = row.get("output") if isinstance(row.get("output"), dict) else {}
    leaders = []
    for item in output.get("leaders") or []:
        if not isinstance(item, dict):
            continue
        leaders.append({
            "code": str(item.get("code") or "").split(".")[0],
            "why": item.get("why") if isinstance(item.get("why"), str) else "",
            "price": item.get("price") if isinstance(item.get("price"), str) else "",
        })
    return leaders


def _sector_summary(rows: list[dict] | None) -> list[dict]:
    """每个事件的标题、板块、状态、verdict，以及评估点名的承接股。"""
    items = []
    for row in rows or []:
        package = row.get("package") if isinstance(row.get("package"), dict) else {}
        event = package.get("event") if isinstance(package.get("event"), dict) else {}
        published = event.get("firstPublished") if isinstance(event.get("firstPublished"), str) else ""
        items.append((published, str(row.get("event_id") or ""), {
            "title": row.get("title") or "",
            "sector": row.get("sector"),
            "state": row.get("state"),
            "verdict": row.get("verdict"),
            "leaders": _output_leaders(row),
        }))
    items.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in items]


def _summary(result: dict, content: dict | None, sector_rows: list[dict] | None = None) -> dict:
    stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
    if not stats and isinstance((content or {}).get("stats"), dict):
        stats = content["stats"]
    classification = stats.get("classification") if isinstance(stats.get("classification"), dict) else {}
    evaluation = stats.get("evaluation") if isinstance(stats.get("evaluation"), dict) else {}
    prepare = stats.get("prepare") if isinstance(stats.get("prepare"), dict) else {}
    return {
        "candidates": stats.get("candidates", (content or {}).get("candidates")),
        "pushed": stats.get("pushed", len((content or {}).get("entries") or [])),
        "classificationCalls": classification.get("calls"),
        "evaluationCalls": evaluation.get("calls"),
        "tokens": _tokens(stats),
        "seconds": {
            "classification": classification.get("seconds"),
            "prepare": prepare.get("seconds"),
            "evaluation": evaluation.get("seconds"),
        },
        "search": stats.get("search") if isinstance(stats.get("search"), dict) else {"enabled": False},
        "sectors": _sector_summary(sector_rows),
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _snapshot_market_caps(payload: dict, trade_date: str) -> tuple[list[dict], date]:
    try:
        day = previous_trading_day(date.fromisoformat(trade_date))
    except CalendarGap as exc:
        raise ReplayError(str(exc)) from None
    rows = payload.get("daily_basic")
    if not isinstance(rows, list):
        raise ReplayError(f"股票池快照没有 {day.isoformat()} 的 daily_basic")
    stamps = {day.strftime("%Y%m%d"), day.isoformat()}
    matched = [
        row for row in rows
        if isinstance(row, dict) and str(row.get("trade_date") or row.get("tradeDate") or "") in stamps
    ]
    if not matched:
        raise ReplayError(f"股票池快照没有 {day.isoformat()} 的 daily_basic")
    return matched, day


def _replay_rows(config, trade_date: str, *, pool_snapshot: Path | None, pool_seed_only: bool,
                 today: date | None) -> tuple[list[dict], str, list[dict], str | None, float | None]:
    rules = config.rules["companyPool"]
    if pool_seed_only:
        return rows_from_seed(config.alias_seed, summary_max_chars=int(rules["summaryMaxChars"])), "seed", [], None, None
    payload = json.loads(pool_snapshot.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ReplayError("股票池快照不是对象")
    as_of = date.fromisoformat(trade_date)
    current = today or datetime.now(CST).date()
    mv_rows, mv_day = _snapshot_market_caps(payload, trade_date)
    share = float(rules["circMv"]["excludeTopShare"])
    dropped: list[dict] = []
    rows = build_pool_rows(
        payload.get("stock_basic") or [], payload.get("stock_company") or [],
        namechange_rows=payload.get("namechange") or [], seed_rows=config.alias_seed, as_of=as_of, today=current,
        prefixes=tuple(rules["prefixes"]), exclude_name_contains=rules["excludeNameContains"],
        summary_max_chars=int(rules["summaryMaxChars"]), mv_rows=mv_rows, exclude_top_share=share,
        mv_date=mv_day, excluded=dropped)
    return rows, "snapshot", dropped, mv_day.isoformat(), share


def run_replay(*, snapshot: Path, trade_date: str, work: Path, market_snapshot: Path, receipts: Path,
               filings_cache: Path, config_path: Path | None = None,
               transport: httpx.BaseTransport | None = None, pool_snapshot: Path | None = None,
               pool_seed_only: bool = False, today: date | None = None,
               sector_snapshot: Path | None = None, limit_snapshot: Path | None = None) -> dict:
    if pool_snapshot is None and not pool_seed_only:
        raise ReplayError("要给出 --pool-snapshot 或 --pool-seed-only")
    if pool_snapshot is not None and pool_seed_only:
        raise ReplayError("--pool-snapshot 和 --pool-seed-only 只能给一个")
    if pool_snapshot is not None and not pool_snapshot.is_file():
        raise ReplayError("股票池快照不存在")
    if _DATE.fullmatch(trade_date) is None:
        raise ReplayError("trade-date 须为 YYYY-MM-DD")
    if not snapshot.is_file():
        raise ReplayError("快照库不存在")
    if not market_snapshot.is_file():
        raise ReplayError("行情快照不存在")
    if sector_snapshot is not None and not sector_snapshot.is_file():
        raise ReplayError("板块快照不存在")
    if limit_snapshot is not None and not limit_snapshot.is_file():
        raise ReplayError("涨停快照不存在")
    dest = work / f"{trade_date}.db"
    out = work / trade_date
    _replace_copy(snapshot, dest)
    db.init_schema(dest)
    started = _started_at(dest, trade_date)
    config = load_config(path=config_path) if config_path is not None else load_config()
    pool_rows, pool_kind, excluded, mv_date, share = _replay_rows(
        config, trade_date, pool_snapshot=pool_snapshot, pool_seed_only=pool_seed_only, today=today)
    with db.write(dest) as conn:
        _clear_day(conn, trade_date)
        store_rows(conn, pool_rows, now=started, source=pool_kind, excluded=excluded, mv_date=mv_date,
                   exclude_top_share=share)
        if sector_snapshot is not None:
            try:
                payload = json.loads(sector_snapshot.read_text(encoding="utf-8"))
                install_sector_snapshot(conn, payload, refreshed_at=db.utc(started),
                                        prefixes=tuple(config.rules["companyPool"]["prefixes"]))
            except (SectorFetchError, json.JSONDecodeError, OSError) as exc:
                raise ReplayError(str(exc) if isinstance(exc, SectorFetchError) else "板块快照不是对象") from None
        if limit_snapshot is not None:
            try:
                rows = json.loads(limit_snapshot.read_text(encoding="utf-8"))
                install_limit_snapshot(conn, rows, trade_date)
            except (ValueError, json.JSONDecodeError, OSError):
                raise ReplayError("涨停快照不是列表") from None
    filings_cache.mkdir(parents=True, exist_ok=True)
    api_key = _required_env("DEEPSEEK_API_KEY")
    model_name = _required_env("DEEPSEEK_MODEL")
    clock = ReplayClock(started)
    model = config.rules["model"]
    receipt_store = FileReceipts(receipts)

    def client_factory(report_id: str) -> DeepSeekClient:
        del report_id
        return DeepSeekClient(endpoint=model["endpoint"], model=model_name, api_key=api_key,
                              receipts=receipt_store, timeout_seconds=float(model["timeoutSeconds"]),
                              max_attempts=int(model["maxAttempts"]), transport=transport)

    search_cfg = config.rules["evaluation"].get("search")
    search_factory = None
    if isinstance(search_cfg, dict):
        def search_factory(report_id: str, _cfg=search_cfg) -> SearchClient:  # type: ignore[misc]
            del report_id
            return SearchClient(endpoint=_cfg["endpoint"], api_key=api_key, receipts=receipt_store,
                                timeout_seconds=float(_cfg["timeoutSeconds"]),
                                max_attempts=int(_cfg["maxAttempts"]), transport=transport)

    result = run_morning(dest, now=clock(), config=config, collector=lambda _at: {"status": "replay"},
                         client_factory=client_factory, pusher=Pusher(dest, None, clock), clock=clock,
                         fetcher=pdf_fetcher(filings_cache, config.rules["filings"]),
                         market=SnapshotSource(market_snapshot), search_factory=search_factory,
                         pool_source="database", sector_live=False)
    with db.read(dest) as conn:
        report = db.get_report(conn, trade_date)
        rows = db.list_evaluations(conn, f"morning-{trade_date}")
        sector_rows = db.list_sector_events(conn, f"morning-{trade_date}")
    content = report.get("content") if isinstance(report, dict) else None
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "report.json", report if report is not None else result)
    _write_json(out / "evaluations.json", rows)
    _write_json(out / "summary.json", _summary(result, content if isinstance(content, dict) else None,
                                               sector_rows))
    (out / "samples.md").write_text(
        _samples(content if isinstance(content, dict) else None, rows), encoding="utf-8")
    return result


def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(prog="neckline.morning.replay")
    ap.add_argument("--snapshot", required=True, type=Path)
    ap.add_argument("--trade-date", required=True)
    ap.add_argument("--work", required=True, type=Path)
    ap.add_argument("--market-snapshot", required=True, type=Path)
    ap.add_argument("--receipts", required=True, type=Path)
    ap.add_argument("--filings-cache", required=True, type=Path)
    ap.add_argument("--config", type=Path)
    pool = ap.add_mutually_exclusive_group(required=True)
    pool.add_argument("--pool-snapshot", type=Path)
    pool.add_argument("--pool-seed-only", action="store_true")
    ap.add_argument("--sector-snapshot", type=Path)
    ap.add_argument("--limit-snapshot", type=Path)
    args = ap.parse_args(argv)
    try:
        result = run_replay(snapshot=args.snapshot, trade_date=args.trade_date, work=args.work,
                            market_snapshot=args.market_snapshot, receipts=args.receipts,
                            filings_cache=args.filings_cache, config_path=args.config, transport=transport,
                            pool_snapshot=args.pool_snapshot, pool_seed_only=args.pool_seed_only,
                            sector_snapshot=args.sector_snapshot, limit_snapshot=args.limit_snapshot)
    except ReplayError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"tradeDate": args.trade_date, "state": result.get("state"),
                      "entries": result.get("entries"), "out": str(args.work / args.trade_date)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
