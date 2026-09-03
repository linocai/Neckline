#!/usr/bin/env python3
"""仅补数据前置，不生成报告、不调用 LLM、不发送 APNs。

这是普通周期扫描，不使用 systemd missed-slot 语义：定时运行只检查今天的 ``fp-4``，
避免历史缺口阻断当天报告；显式 ``--start`` 才允许人工扫描最近最多 60 个官方交易日。
它只重拉缺失/不完整分区，永远不生成报告、不调用 LLM、不发送 APNs。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.calendar import official_is_trading_day, trading_days_between
from neckline.facts import readiness
from neckline.facts.v4 import PACK_VERSION

MAX_RECOVERY_TRADING_DAYS = 60


def _today() -> date:
    return date.today()


def scheduled_recovery_date(today: date) -> date:
    """Use the same Mon–Thu/Sunday report-date contract as the evening chain."""
    return today - timedelta(days=2) if today.weekday() == 6 else today


def recovery_days(through: date, *, start: date | None = None) -> list[date]:
    """Return missing fp-4 days without letting old history block today."""
    calendar_state = official_is_trading_day(through)
    if calendar_state is None:
        raise RuntimeError(f"官方交易日历未覆盖 {through}，拒绝恢复")
    if start is None:
        if not calendar_state:
            return []
        candidates = [through]
    else:
        candidates = trading_days_between(start, through)[-MAX_RECOVERY_TRADING_DAYS:]
    missing = [
        d for d in candidates
        if not readiness.preflight(d, pack_version=PACK_VERSION).ready
    ]
    return missing


def run_daily_update(target: date) -> int:
    script = Path(__file__).with_name("daily_update.py")
    return subprocess.run(
        [sys.executable, str(script), target.strftime("%Y%m%d"), "--retry-incomplete"],
        check=False,
    ).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through", default=None)
    parser.add_argument("--start", default=None, help="YYYYMMDD；仅用于受控测试/人工缩小扫描窗")
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="仅给 systemd：周日恢复前一周五，其余槽位恢复当天",
    )
    args = parser.parse_args(argv)
    try:
        if args.scheduled and (args.through or args.start):
            raise ValueError("--scheduled 不能与 --through/--start 同时使用")
        through = (
            scheduled_recovery_date(_today())
            if args.scheduled
            else datetime.strptime(args.through, "%Y%m%d").date()
            if args.through
            else _today()
        )
        start = datetime.strptime(args.start, "%Y%m%d").date() if args.start else None
        missing = recovery_days(through, start=start)
    except (RuntimeError, ValueError) as exc:
        print(f"恢复前置失败：{exc}", file=sys.stderr)
        return 1
    for target in missing:
        if run_daily_update(target) != 0:
            print(f"{target} 日更失败，停止恢复", file=sys.stderr)
            return 1
        if not readiness.preflight(target, pack_version=PACK_VERSION).ready:
            print(f"{target} 日更后仍未就绪，停止恢复", file=sys.stderr)
            return 1
    print(f"数据前置恢复完成：补齐 {len(missing)} 个交易日；未生成报告或 APNs。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
