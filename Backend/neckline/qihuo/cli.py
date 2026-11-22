"""Q 期货命令行入口：`python -m neckline.qihuo.cli sync`（由 neckline-v4-qihuo-sync.timer 20:15 静默触发）。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, time
from pathlib import Path

from neckline.morning.api import v4_db_path
from neckline.morning.schedule import CST
from neckline.qihuo import services
from neckline.qihuo.sync import DEFAULT_DEADLINE, DEFAULT_POLL_SECONDS, run_sync


def _parse_clock(raw: str) -> time:
    hour, minute = raw.split(":")
    return time(int(hour), int(minute), tzinfo=CST)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Neckline Qihuo V2 晚间静默同步（零推送）")
    sub = parser.add_subparsers(dest="command", required=True)
    sync_cmd = sub.add_parser("sync", help="交易日 20:15 静默拉取 Qihuo 只读 JSON 并落库缓存")
    sync_cmd.add_argument("--db", type=Path, help="指定 v4.db 路径（默认读 NECKLINE_V4_DB_PATH）")
    sync_cmd.add_argument("--date", help="指定交易日 YYYY-MM-DD（默认当天）")
    sync_cmd.add_argument("--deadline", default=DEFAULT_DEADLINE.strftime("%H:%M"), help="温和补拉截止 HH:MM（默认 20:35）")
    sync_cmd.add_argument("--interval", type=float, default=DEFAULT_POLL_SECONDS, help="补拉间隔秒数（默认 120）")
    sync_cmd.add_argument("--once", action="store_true", help="只拉取一次，不轮询等待")
    sync_cmd.add_argument("--force", action="store_true", help="忽略交易日与 20:15～20:35 窗口限制，单次强制拉取落库（运维初次建缓存用）")

    args = parser.parse_args(argv)
    db_path = args.db or v4_db_path()
    if db_path is None:
        print(json.dumps({"status": "error", "reason": "v4_not_configured"}, ensure_ascii=False), file=sys.stderr)
        return 1

    target_date = date.fromisoformat(args.date) if args.date else None
    deadline_clock = _parse_clock(args.deadline)

    async def _run() -> dict:
        qihuo = services.current()
        try:
            return await run_sync(
                qihuo,
                db_path=db_path,
                target_date=target_date,
                deadline_clock=deadline_clock,
                poll_seconds=args.interval,
                once=args.once or args.force,
                force=args.force,
            )
        finally:
            await services.close_default_services()

    result = asyncio.run(_run())
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
