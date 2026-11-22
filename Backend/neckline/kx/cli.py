"""KX 命令行入口：`python -m neckline.kx.cli watch`（由 neckline-v4-kx-watch.timer 20:02 触发）。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, time
from pathlib import Path

from neckline.kx import services
from neckline.kx.watch import DEFAULT_DEADLINE, DEFAULT_POLL_SECONDS, run_watch
from neckline.morning.api import v4_db_path
from neckline.morning.schedule import CST


def _parse_clock(raw: str) -> time:
    hour, minute = raw.split(":")
    return time(int(hour), int(minute), tzinfo=CST)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Neckline KX V4 盘后监测与 iPhone 推送")
    sub = parser.add_subparsers(dest="command", required=True)
    watch = sub.add_parser("watch", help="交易日 20:00 后轮询 KX 盘后结果并推送 iPhone")
    watch.add_argument("--db", type=Path, help="指定 v4.db 路径（默认读 NECKLINE_V4_DB_PATH）")
    watch.add_argument("--date", help="指定交易日 YYYY-MM-DD（默认当天）")
    watch.add_argument("--deadline", default=DEFAULT_DEADLINE.strftime("%H:%M"), help="超时截止时间 HH:MM（默认 20:45）")
    watch.add_argument("--interval", type=float, default=DEFAULT_POLL_SECONDS, help="轮询间隔秒数（默认 60）")
    watch.add_argument("--once", action="store_true", help="只检查一次，不轮询等待")
    watch.add_argument("--dry-run", action="store_true", help="试跑，不实际发 APNs 推送")

    args = parser.parse_args(argv)
    db_path = args.db or v4_db_path()
    if db_path is None:
        print(json.dumps({"status": "error", "reason": "v4_not_configured"}, ensure_ascii=False), file=sys.stderr)
        return 1

    target_date = date.fromisoformat(args.date) if args.date else None
    deadline_clock = _parse_clock(args.deadline)

    async def _run() -> dict:
        kx = services.current()
        try:
            return await run_watch(
                kx,
                db_path=db_path,
                target_date=target_date,
                deadline_clock=deadline_clock,
                poll_seconds=args.interval,
                once=args.once,
                dry_run=args.dry_run,
            )
        finally:
            await services.close_default_services()

    result = asyncio.run(_run())
    print(json.dumps(result, ensure_ascii=False))
    push = result.get("push") if isinstance(result.get("push"), dict) else {}
    if result.get("status") in ("error", "push_failed") or push.get("failed", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
