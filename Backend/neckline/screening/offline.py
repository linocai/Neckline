"""P1 离线跑：拿一份只读导出走「晨报窗口粗筛 → 按公司分类 → 按配置选择」，写出名单供用户点评。

用法（在 Backend/ 下）：
  DEEPSEEK_API_KEY=… DEEPSEEK_MODEL=… .venv/bin/python -m neckline.screening.offline \
    --archive .work/p1-offline/archive-20261016.jsonl.gz --out .work/p1-offline/m1013-r1 \
    --window-start 2026-10-12T15:00:00+08:00 --window-end 2026-10-13T08:00:00+08:00 \
    --collected-by 2026-10-13T08:01:03+08:00
业务时钟全部显式传入；凭据只从环境变量读取；已有回执的请求不会再次外呼。
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from .classify import CONTRACT, CST, classify
from .deepseek import DeepSeekClient, FileReceipts
from .pool import CompanyPool
from .prefilter import Window, prefilter
from .select import Entry, select

_TITLES = {
    "main": "名单",
    "review_types": "待定：利好、新、公司是主体，但类型不在收录清单",
    "review_relation": "待定：受益方（事件主体是别人，消息点名该公司受益）",
    "review_fresh": "待定：利好，但看不出是不是窗口内首次公开",
    "held_old": "判为旧事的利好（用来核对时效判断）",
}


def _when(ts: datetime) -> str:
    return ts.astimezone(CST).strftime("%m-%d %H:%M")


def _entry_md(n: int, e: Entry, pool: CompanyPool) -> str:
    c, ev, first = pool.companies[e.company], e.primary, e.first_message
    disclosed = f"｜首次公开 {ev.first_disclosed}" if ev.first_disclosed else ""
    src = f"{first.source_label} {_when(first.published_at)}"
    if first.url:
        src = f"[{src}]({first.url})"
    title = f"《{first.title}》" if first.title else ""
    more = f"；另有 {len(e.messages) - 1} 条同事件" if len(e.messages) > 1 else ""
    lines = [f"{n}. **{c.name} {c.short_code}**｜{ev.type}｜{ev.importance}{disclosed}",
             f"   - 事件：{ev.headline}", f"   - 理由：{ev.reason}", f"   - 来源：{src}{title}{more}"]
    for other in e.others:
        lines.append(f"   - 同公司另有：{other.headline}（{other.type}｜{other.importance}）")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--config", type=Path, default=Path(__file__).resolve().parent.parent / "config" / "screening-v1.json")
    ap.add_argument("--receipts", type=Path, default=Path(".work/p1-offline/receipts"))
    ap.add_argument("--window-start", required=True)
    ap.add_argument("--window-end", required=True)
    ap.add_argument("--collected-by", required=True)
    ap.add_argument("--endpoint", default="https://api.deepseek.com/chat/completions")
    ap.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL"))
    ap.add_argument("--compare", type=Path)
    args = ap.parse_args()
    if not args.model:
        raise SystemExit("必须显式给出模型名（--model 或 DEEPSEEK_MODEL）")

    config = json.loads(args.config.read_text(encoding="utf-8"))
    settings = config["classification"]
    docs, companies = [], []
    with gzip.open(args.archive, "rt", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row["kind"] == "doc":
                docs.append(row)
            elif row["kind"] == "company":
                companies.append(row)
    pool = CompanyPool.from_rows(companies, config["matching"]["strictShortNames"])
    profiles = {row["code"]: row["index"].get("summary") for row in companies}
    window = Window(start=datetime.fromisoformat(args.window_start), end=datetime.fromisoformat(args.window_end),
                    collected_by=datetime.fromisoformat(args.collected_by))

    started = time.monotonic()
    pre = prefilter(docs, pool, window, history_days=settings["history"]["lookbackDays"])
    prefilter_seconds = round(time.monotonic() - started, 1)
    client = DeepSeekClient(endpoint=args.endpoint, model=args.model, api_key=os.environ.get("DEEPSEEK_API_KEY"),
                            receipts=FileReceipts(args.receipts), timeout_seconds=600, max_attempts=2)
    model_started = time.monotonic()
    result = classify(pre.messages, pre.history, pool, profiles, client, window=window, settings=settings)
    model_seconds = round(time.monotonic() - model_started, 1)
    selection = select(result.by_id, result.results, config["selection"])

    usage = Counter()
    for o in result.batches:
        for key in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens"):
            usage[key] += int(o.usage.get(key) or 0)
        usage["reasoning_tokens"] += int((o.usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
    events = [e for r in result.results.values() for e in r.events]
    stats = {
        "contract": CONTRACT, "config": config["configVersion"], "model": args.model,
        "window": [window.start.isoformat(), window.end.isoformat()], "collectedBy": window.collected_by.isoformat(),
        "inWindow": pre.input_count, "duplicates": pre.duplicate_count, "unmatched": pre.unmatched_count,
        "messages": len(pre.messages), "history": len(pre.history), "companies": len(result.results),
        "calls": len(result.batches), "fromReceipt": sum(o.from_receipt for o in result.batches),
        "retries": sum(o.attempt > 1 for o in result.batches), "batchErrors": [o.error for o in result.batches if o.error],
        "prefilterSeconds": prefilter_seconds, "modelSeconds": model_seconds,
        "slowestCallSeconds": max((o.seconds for o in result.batches), default=0), "usage": dict(usage),
        "events": len(events), "gaps": [(g.company, g.error) for g in selection.gaps],
        **{name: len(entries) for name, entries in selection.sections.items()},
        "reasons": dict(selection.reasons.most_common()),
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "events.json").write_text(json.dumps([
        {**e.__dict__, "name": pool.companies[e.company].name,
         "messages": [{"id": i, "ref": result.by_id[i].ref, "source": result.by_id[i].source_label,
                       "publishedAt": result.by_id[i].published_at.isoformat(), "title": result.by_id[i].title,
                       "url": result.by_id[i].url} for i in e.ids]} for e in events], ensure_ascii=False, indent=1),
        encoding="utf-8")
    (args.out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")

    day = window.end.astimezone(CST).strftime("%m-%d")
    md = [f"# {day} 晨报（离线，窗口 {_when(window.start)} → {_when(window.end)}，规则 {config['configVersion']}）", "",
          f"窗口内消息 {pre.input_count} 条，去重 {pre.duplicate_count} 条，未点名公司池 {pre.unmatched_count} 条；"
          f"送模型 {len(pre.messages)} 条、涉及 {len(result.results)} 家公司，另附窗口前相关消息 {len(pre.history)} 条供核对；"
          f"{len(result.batches)} 次调用，模型用时 {model_seconds} 秒。"]
    if selection.gaps:
        md.append(f"缺口：{len(selection.gaps)} 家公司没能完成判断，未列入（"
                  + "、".join(pool.companies[g.company].name for g in selection.gaps) + "）。")
    for name, title in _TITLES.items():
        entries = selection.sections[name]
        md += ["", f"## {title}（{len(entries)} 家）", ""]
        md += [_entry_md(i, e, pool) for i, e in enumerate(entries, 1)]
    md += ["", "## 拦下的原因", ""]
    md += [f"- {k}：{v}" for k, v in selection.reasons.most_common()]
    if args.compare and args.compare.exists():
        md += ["", "## 对照", ""]
        by_company: dict[str, list] = {}
        for e in events:
            by_company.setdefault(e.company, []).append(e)
        for card in json.loads(args.compare.read_text(encoding="utf-8")):
            found = by_company.get(card["code"], [])
            verdict = "；".join(sorted({f"{e.relation}/{e.type}/{e.direction}/{e.fresh}" for e in found})) or (
                "只在罗列中出现" if card["code"] in result.results else "窗口内没有点名它的消息")
            md.append(f"- {card['name']} {card['code'][:6]}（{card['note']}）→ {verdict}")
    (args.out / "list.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
