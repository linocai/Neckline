"""离线回放一期晨报。复制只读快照后再跑，原快照不写。

  python -m neckline.morning.replay --snapshot <只读快照库> --trade-date YYYY-MM-DD \\
      --work <目录> --market-snapshot <json> --receipts <回执目录> --filings-cache <目录> \\
      [--config screening-v3.json]

业务时钟从快照里该日晨报的 started_at 起步，按真实流逝时间前进。
采集不采，推送不发。行情读快照，模型回执落在 --receipts，同样请求不重复计费。
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
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from neckline.evaluation.market import SnapshotSource
from neckline.morning import db
from neckline.morning.report import Pusher, load_config, run_morning
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
    shutil.copy2(src, dest)


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


def _summary(result: dict, content: dict | None) -> dict:
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
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run_replay(*, snapshot: Path, trade_date: str, work: Path, market_snapshot: Path, receipts: Path,
               filings_cache: Path, config_path: Path | None = None,
               transport: httpx.BaseTransport | None = None) -> dict:
    if _DATE.fullmatch(trade_date) is None:
        raise ReplayError("trade-date 须为 YYYY-MM-DD")
    if not snapshot.is_file():
        raise ReplayError("快照库不存在")
    if not market_snapshot.is_file():
        raise ReplayError("行情快照不存在")
    dest = work / f"{trade_date}.db"
    out = work / trade_date
    _replace_copy(snapshot, dest)
    db.init_schema(dest)
    started = _started_at(dest, trade_date)
    with db.write(dest) as conn:
        conn.execute("DELETE FROM reports WHERE trade_date=?", (trade_date,))
    filings_cache.mkdir(parents=True, exist_ok=True)
    api_key = _required_env("DEEPSEEK_API_KEY")
    model_name = _required_env("DEEPSEEK_MODEL")
    config = load_config(path=config_path) if config_path is not None else load_config()
    clock = ReplayClock(started)
    model = config.rules["model"]

    def client_factory(report_id: str) -> DeepSeekClient:
        del report_id
        return DeepSeekClient(endpoint=model["endpoint"], model=model_name, api_key=api_key,
                              receipts=FileReceipts(receipts), timeout_seconds=float(model["timeoutSeconds"]),
                              max_attempts=int(model["maxAttempts"]), transport=transport)

    result = run_morning(dest, now=clock(), config=config, collector=lambda _at: {"status": "replay"},
                         client_factory=client_factory, pusher=Pusher(dest, None, clock), clock=clock,
                         fetcher=pdf_fetcher(filings_cache, config.rules["filings"]),
                         market=SnapshotSource(market_snapshot))
    with db.read(dest) as conn:
        report = db.get_report(conn, trade_date)
        rows = db.list_evaluations(conn, f"morning-{trade_date}")
    content = report.get("content") if isinstance(report, dict) else None
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "report.json", report if report is not None else result)
    _write_json(out / "evaluations.json", rows)
    _write_json(out / "summary.json", _summary(result, content if isinstance(content, dict) else None))
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
    args = ap.parse_args(argv)
    try:
        result = run_replay(snapshot=args.snapshot, trade_date=args.trade_date, work=args.work,
                            market_snapshot=args.market_snapshot, receipts=args.receipts,
                            filings_cache=args.filings_cache, config_path=args.config, transport=transport)
    except ReplayError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"tradeDate": args.trade_date, "state": result.get("state"),
                      "entries": result.get("entries"), "out": str(args.work / args.trade_date)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
