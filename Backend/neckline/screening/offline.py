"""离线回放：只读打开一份 V4 晨报库快照，按线上同一套流程重跑某一期的一级初筛。

用法（在 Backend/ 下）：
  DEEPSEEK_API_KEY=… DEEPSEEK_MODEL=… .venv/bin/python -m neckline.screening.offline \
    --db .work/replay-4.2.0/replay.db --trade-date 2026-10-22 \
    --receipts .work/replay-4.2.0/receipts --filings-cache .work/replay-4.2.0/filings \
    --out .work/replay-4.2.0/out-1022
不给 --collected-by 时，用快照 reports 表里该日的 started_at。
默认配置是 neckline/config/screening-v3.json。凭据只从环境变量读取，不打印。
快照以只读方式打开，PDF 读数写在内存里，不写回快照。
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from neckline.morning import db
from neckline.morning.report import load_config
from neckline.morning.schedule import morning_window
from neckline.screening.enrich import MemoryFilingReads, enrich_filings, pdf_fetcher, purge_old_pdfs

from .classify import CONTRACT, CST, classify
from .deepseek import DeepSeekClient, FileReceipts
from .pool import CompanyPool
from .prefilter import Window, match_history, prefilter_window
from .select import Entry, select


def _when(ts: datetime) -> str:
    return ts.astimezone(CST).strftime("%m-%d %H:%M")


def _candidate(entry: Entry, pool: CompanyPool) -> dict:
    company, event = pool.companies[entry.company], entry.primary
    return {
        "code": company.short_code, "name": company.name, "type": event.type, "direction": event.direction,
        "fresh": event.fresh, "headline": event.headline, "reason": event.reason,
        "sources": [{"source": message.source_label, "publishedAt": message.published_at.isoformat(),
                     "title": message.title, "url": message.url} for message in entry.messages],
        "others": [{"headline": other.headline, "type": other.type, "direction": other.direction,
                    "fresh": other.fresh, "reason": other.reason} for other in entry.others],
    }


def _entry_md(n: int, entry: Entry, pool: CompanyPool) -> str:
    company, event, first = pool.companies[entry.company], entry.primary, entry.first_message
    src = f"{first.source_label} {_when(first.published_at)}"
    if first.url:
        src = f"[{src}]({first.url})"
    title = f"《{first.title}》" if first.title else ""
    more = f"；另有 {len(entry.messages) - 1} 条同事件" if len(entry.messages) > 1 else ""
    lines = [f"{n}. **{company.name} {company.short_code}**｜{event.type}｜{event.fresh}",
             f"   - 事件：{event.headline}", f"   - 理由：{event.reason}", f"   - 来源：{src}{title}{more}"]
    for other in entry.others:
        lines.append(f"   - 同公司另有：{other.headline}（{other.type}）")
    return "\n".join(lines)


def _collected_by(db_path: Path, trade_date: str, given: str | None) -> datetime:
    if given:
        return datetime.fromisoformat(given)
    with db.read(db_path) as conn:
        report = db.get_report(conn, trade_date)
    if report is None or not report.get("started_at"):
        raise SystemExit(f"快照里没有 {trade_date} 的晨报，无法确定 collected-by")
    return datetime.fromisoformat(report["started_at"])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="用 V4 库快照离线重跑一期一级初筛")
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--trade-date", required=True)
    ap.add_argument("--collected-by", default=None)
    ap.add_argument("--config", type=Path, default=Path(__file__).resolve().parent.parent / "config" / "screening-v3.json")
    ap.add_argument("--receipts", type=Path, required=True)
    ap.add_argument("--filings-cache", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL"))
    args = ap.parse_args(argv)
    if not args.model:
        raise SystemExit("必须显式给出模型名（--model 或 DEEPSEEK_MODEL）")
    day = date.fromisoformat(args.trade_date)
    loaded = load_config(path=args.config)
    rules = loaded.rules
    collected_by = _collected_by(args.db, day.isoformat(), args.collected_by)
    start, end = morning_window(day, rules["schedule"])
    window = Window(start=start, end=end, collected_by=collected_by)
    settings = rules["classification"]
    pool: CompanyPool = loaded.pool
    with db.read(args.db) as conn:
        docs = list(db.iter_documents(conn, published_from=start, published_to=end, fetched_by=collected_by,
                                      include_end=True))
        messages, in_window, duplicates, unmatched = prefilter_window(docs, pool)
        history = match_history(
            db.iter_documents(conn, published_from=start - timedelta(days=settings["history"]["lookbackDays"]),
                              published_to=start, fetched_by=collected_by, include_end=False),
            pool.subset({code for message in messages for code in message.companies}))
    filings = rules["filings"]
    clock = lambda: datetime.now(timezone.utc)  # noqa: E731 — 缓存过期看墙上时钟，窗口用 collected_by
    purge_old_pdfs(args.filings_cache, cache_days=int(filings["cacheDays"]), now=clock())
    enrich_stats = enrich_filings(messages, store=MemoryFilingReads(), fetcher=pdf_fetcher(args.filings_cache, filings),
                                  settings=filings, clock=clock)
    client = DeepSeekClient(endpoint=rules["model"]["endpoint"], model=args.model,
                            api_key=os.environ.get("DEEPSEEK_API_KEY"), receipts=FileReceipts(args.receipts),
                            timeout_seconds=float(rules["model"]["timeoutSeconds"]),
                            max_attempts=int(rules["model"]["maxAttempts"]))
    result = classify(messages, history, pool, loaded.profiles, client, window=window, settings=settings)
    selection = select(result.by_id, result.results, rules["selection"])
    usage: Counter = Counter()
    for batch in result.batches:
        for key in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens"):
            usage[key] += int(batch.usage.get(key) or 0)
    events = [event for company in result.results.values() for event in company.events]
    stats = {
        "contract": CONTRACT, "config": rules["configVersion"], "model": args.model,
        "window": [window.start.isoformat(), window.end.isoformat()], "collectedBy": window.collected_by.isoformat(),
        "inWindow": in_window, "duplicates": duplicates, "unmatched": unmatched,
        "messages": len(messages), "history": len(history), "companies": len(result.results),
        "calls": len(result.batches), "fromReceipt": sum(batch.from_receipt for batch in result.batches),
        "tokens": {"prompt": usage["prompt_tokens"], "completion": usage["completion_tokens"],
                   "cacheHit": usage["prompt_cache_hit_tokens"]},
        "seconds": round(sum(batch.seconds for batch in result.batches), 3),
        "enrich": enrich_stats.as_dict(), "candidates": len(selection.candidates),
        "gaps": [(gap.company, gap.error) for gap in selection.gaps],
        "reasons": dict(selection.reasons.most_common()),
    }
    candidates = [_candidate(entry, pool) for entry in selection.candidates]
    event_rows = [{
        "company": event.company, "name": pool.companies[event.company].name, "type": event.type,
        "direction": event.direction, "fresh": event.fresh, "headline": event.headline, "reason": event.reason,
        "channel": event.channel, "amountYuan": event.amount_yuan, "ids": event.ids,
        "sources": [{"id": mid, "source": result.by_id[mid].source_label, "title": result.by_id[mid].title,
                     "url": result.by_id[mid].url, "publishedAt": result.by_id[mid].published_at.isoformat()}
                    for mid in event.ids],
    } for event in events]
    lines = [f"# {day.isoformat()} 晨报（离线，窗口 {_when(window.start)} → {_when(window.end)}，规则 {rules['configVersion']}）",
             "",
             f"窗口内消息 {in_window} 条，去重 {duplicates} 条，未点名公司池 {unmatched} 条；"
             f"送模型 {len(messages)} 条、涉及 {len(result.results)} 家公司；"
             f"{len(result.batches)} 次调用。", ""]
    if selection.gaps:
        lines.append("缺口：" + "、".join(pool.companies[gap.company].name for gap in selection.gaps) + "。")
        lines.append("")
    lines.append(f"## 候选（{len(selection.candidates)} 家）")
    lines.append("")
    lines.extend(_entry_md(i, entry, pool) for i, entry in enumerate(selection.candidates, 1))
    lines += ["", "## 拦下的原因", ""]
    lines.extend(f"- {reason}：{count}" for reason, count in selection.reasons.most_common())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "candidates.json").write_text(json.dumps(candidates, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "events.json").write_text(json.dumps(event_rows, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "list.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
