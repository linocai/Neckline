"""V4 晨报命令行（systemd 定时器的入口）。

  python -m neckline.morning.cli init-db
  python -m neckline.morning.cli collect [--now ISO] [--bootstrap-start ISO] [--max-pages N]
  python -m neckline.morning.cli morning [--now ISO]      # 08:00：采集 → 交易日出报 → 推送
  python -m neckline.morning.cli finalize [--now ISO]     # 09:15：还没出报就记失败并推送
  python -m neckline.morning.cli show [--date YYYY-MM-DD]
  python -m neckline.morning.cli evaluations --date YYYY-MM-DD [--json]

库路径取 --db 或环境变量 NECKLINE_V4_DB_PATH，没有默认值。凭据只从环境变量读（DEEPSEEK_API_KEY、
TUSHARE_TOKEN、JIN10_MCP_TOKEN、APNS_*），不写日志。业务时钟可用 --now 显式给出。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from neckline.screening.deepseek import DeepSeekClient

from . import db
from .collect import run_collection
from .report import Pusher, finalize, load_config, note_error, run_morning
from .schedule import CST

TEMPFAIL = 75  # 让 systemd 按 Restart=on-failure 重启续跑
_LOG = logging.getLogger("neckline.morning.cli")


def quiet_http_loggers() -> None:
    """httpx 会把请求 URL 打进 INFO，APNs 的设备 token 在路径里，不能记。"""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise SystemExit("--now 必须带时区")
    return parsed


def _db_path(value: str | None) -> Path:
    raw = value or os.environ.get("NECKLINE_V4_DB_PATH")
    if not raw:
        raise SystemExit("必须给出 --db 或设置 NECKLINE_V4_DB_PATH")
    return Path(raw)


def _collection_config() -> dict:
    return json.loads((resources.files("neckline.config") / "collection-v4.json").read_text(encoding="utf-8"))


def _tokens() -> dict:
    return {key: (os.environ.get(key) or "").strip() or None for key in ("TUSHARE_TOKEN", "JIN10_MCP_TOKEN")}


def _sender():
    from neckline.push.apns import send_push
    return send_push


def _client_factory(path: Path, config):
    model = config.rules["model"]

    def make(report_id: str) -> DeepSeekClient:
        return DeepSeekClient(endpoint=model["endpoint"], model=model["name"],
                              api_key=(os.environ.get("DEEPSEEK_API_KEY") or "").strip() or None,
                              receipts=db.DbReceipts(path, report_id), timeout_seconds=float(model["timeoutSeconds"]),
                              max_attempts=int(model["maxAttempts"]))
    return make


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=1, default=str))


def _show_entry(entry: dict, index: int) -> str:
    title = entry.get("title") or entry.get("headline") or ""
    prefix = f"{entry['rank']}." if "rank" in entry else f"{index}."
    return f"{prefix} {entry.get('companyName', '')} {entry.get('companyCode', '')}｜{title}"


def _evaluation_name(row: dict) -> str:
    package = row.get("package") if isinstance(row.get("package"), dict) else {}
    company = package.get("company") if isinstance(package.get("company"), dict) else {}
    name = company.get("name")
    return name if isinstance(name, str) else ""


def _evaluations(path: Path, day: str, as_json: bool) -> int:
    """只读列出某一期的全部评估。不建表、不写库。"""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) is None:
        print("--date 须为 YYYY-MM-DD", file=sys.stderr)
        return 1
    try:
        with db.read(path) as conn:
            report = db.get_report(conn, day)
            rows = db.list_evaluations(conn, f"morning-{day}") if report else []
    except db.NotReady as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if report is None:
        print(f"{day} 没有晨报", file=sys.stderr)
        return 1
    payload = []
    for row in rows:
        payload.append({
            "company": row.get("company"),
            "name": _evaluation_name(row),
            "state": row.get("state"),
            "verdict": row.get("verdict"),
            "error": row.get("error"),
            "output": row.get("output"),
            "package": row.get("package"),
            "usage": row.get("usage"),
            "seconds": row.get("seconds"),
        })
    if as_json:
        _print(payload)
        return 0
    if not payload:
        print("没有评估")
        return 0
    for item in payload:
        verdict = item["verdict"] or "—"
        error = item["error"] or ""
        print(f"{item['company']} {item['name']} {verdict} {item['state']} {error}".rstrip())
    return 0


def _weekday_problem(path: Path, day, body: str, clock) -> None:
    if day.weekday() >= 5:
        return
    Pusher(path, _sender(), clock).notify(report_id=f"morning-{day.isoformat()}", kind="problem",
                                          title="晨报出问题了", body=body)


def _config_invalid(path: Path, now: datetime, clock, *, collect: bool) -> None:
    """筛选规则坏了：不写晨报。晨报命令仍先采集；工作日推一次，然后以 0 退出。"""
    if collect:
        try:
            run_collection(path, now=now, config=_collection_config(), tokens=_tokens(), clock=clock)
        except Exception as exc:
            _LOG.error("采集失败：%s", type(exc).__name__)
    try:
        _weekday_problem(path, now.astimezone(CST).date(), "筛选规则配置无效，今天不出报", clock)
    except Exception as exc:
        _LOG.error("推送失败：%s", type(exc).__name__)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    quiet_http_loggers()
    ap = argparse.ArgumentParser(prog="neckline.morning.cli")
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    collect = sub.add_parser("collect")
    collect.add_argument("--now")
    collect.add_argument("--bootstrap-start")
    collect.add_argument("--max-pages", type=int)
    for name in ("morning", "finalize"):
        sub.add_parser(name).add_argument("--now")
    show = sub.add_parser("show")
    show.add_argument("--date")
    evaluations = sub.add_parser("evaluations")
    evaluations.add_argument("--date", required=True)
    evaluations.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    path = _db_path(args.db)

    if args.command == "init-db":
        db.init_schema(path)
        _print({"db": str(path), "schema": db.SCHEMA_VERSION})
        return 0
    if args.command == "show":
        with db.read(path) as conn:
            report = db.get_report(conn, args.date) if args.date else db.latest_report(conn)
            runs = db.collections_since(conn, datetime(2000, 1, 1, tzinfo=timezone.utc))[-4:]
        entries = ((report or {}).get("content") or {}).get("entries", [])
        _print({"report": {k: v for k, v in (report or {}).items() if k != "content"} or None,
                "entries": [_show_entry(entry, index) for index, entry in enumerate(entries, 1)],
                "collections": [{"runId": r["run_id"], "status": r["status"],
                                 "sources": [(s["sourceKey"], s["complete"], s["error"], s["documents"]) for s in r["sources"]]}
                                for r in runs]})
        return 0
    if args.command == "evaluations":
        return _evaluations(path, args.date, args.json)

    db.init_schema(path)  # 写命令：库不存在就建，已存在且版本一致时不改动
    clock = lambda: datetime.now(timezone.utc)  # noqa: E731
    now = _now(args.now)
    if args.command == "collect":
        bootstrap = _now(args.bootstrap_start) if args.bootstrap_start else None
        _print(run_collection(path, now=now, config=_collection_config(), tokens=_tokens(),
                              bootstrap_start=bootstrap, max_pages=args.max_pages, clock=clock))
        return 0
    try:
        config = load_config()
    except Exception as exc:
        _LOG.error("筛选规则配置无效：%s", type(exc).__name__)
        _config_invalid(path, now, clock, collect=args.command == "morning")
        return 0
    pusher = Pusher(path, _sender(), clock)
    if args.command == "finalize":
        _print(finalize(path, now=now, config=config, pusher=pusher))
        return 0
    collection_config, tokens = _collection_config(), _tokens()
    try:
        result = run_morning(path, now=now, config=config, pusher=pusher, clock=clock,
                             collector=lambda at: run_collection(path, now=at, config=collection_config,
                                                                 tokens=tokens, clock=clock),
                             client_factory=_client_factory(path, config))
    except Exception as exc:  # 记下原因，交给 systemd 重启续跑；截止兜底会引用这条原因
        logging.exception("晨报出报中途出错")
        note_error(path, day=now.astimezone(CST).date(), message=f"出报中途出错：{type(exc).__name__}")
        return TEMPFAIL
    _print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
