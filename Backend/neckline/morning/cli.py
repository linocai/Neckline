"""V4 晨报命令行（systemd 定时器的入口）。

  python -m neckline.morning.cli init-db
  python -m neckline.morning.cli collect [--now ISO] [--bootstrap-start ISO] [--max-pages N]
  python -m neckline.morning.cli morning [--now ISO]      # 08:00：采集 → 交易日出报 → 推送
  python -m neckline.morning.cli finalize [--now ISO]     # 09:15：还没出报就记失败并推送
  python -m neckline.morning.cli show [--date YYYY-MM-DD]

库路径取 --db 或环境变量 NECKLINE_V4_DB_PATH，没有默认值。凭据只从环境变量读（DEEPSEEK_API_KEY、
TUSHARE_TOKEN、JIN10_MCP_TOKEN、APNS_*），不写日志。业务时钟可用 --now 显式给出。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
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


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
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
        _print({"report": {k: v for k, v in (report or {}).items() if k != "content"} or None,
                "entries": [f"{e['rank']}. {e['companyName']} {e['companyCode']}｜{e['headline']}"
                            for e in ((report or {}).get("content") or {}).get("entries", [])],
                "collections": [{"runId": r["run_id"], "status": r["status"],
                                 "sources": [(s["sourceKey"], s["complete"], s["error"], s["documents"]) for s in r["sources"]]}
                                for r in runs]})
        return 0

    db.init_schema(path)  # 写命令：库不存在就建，已存在且版本一致时不改动
    clock = lambda: datetime.now(timezone.utc)  # noqa: E731
    now = _now(args.now)
    if args.command == "collect":
        bootstrap = _now(args.bootstrap_start) if args.bootstrap_start else None
        _print(run_collection(path, now=now, config=_collection_config(), tokens=_tokens(),
                              bootstrap_start=bootstrap, max_pages=args.max_pages, clock=clock))
        return 0
    config = load_config()
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
