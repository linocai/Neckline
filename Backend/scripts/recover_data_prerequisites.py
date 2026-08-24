#!/usr/bin/env python3
"""仅补数据前置，不生成报告、不调用 LLM、不发送 APNs。

这是普通周期扫描，不使用 systemd missed-slot 语义：每次只在最近最多 60 个官方
交易日中检查只读 readiness，仅对未就绪日调用 ``daily_update.py YYYYMMDD``。
它永远不生成报告、不调用 LLM、不发送 APNs。
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

MAX_RECOVERY_TRADING_DAYS = 60


def recovery_days(through: date, *, start: date | None = None) -> list[date]:
    """返回需要补齐的未就绪交易日；只认官方日历，绝不工作日猜测。"""
    if official_is_trading_day(through) is None:
        raise RuntimeError(f"官方交易日历未覆盖 {through}，拒绝恢复")
    # 以足够宽的自然日窗口取得最后 60 个官方交易日；节假日密集时也不越界。
    begin = start or (through - timedelta(days=400))
    candidates = trading_days_between(begin, through)[-MAX_RECOVERY_TRADING_DAYS:]
    missing = [d for d in candidates if not readiness.preflight(d).ready]
    return missing


def run_daily_update(target: date) -> int:
    script = Path(__file__).with_name("daily_update.py")
    return subprocess.run([sys.executable, str(script), target.strftime("%Y%m%d")], check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--start", default=None, help="YYYYMMDD；仅用于受控测试/人工缩小扫描窗")
    args = parser.parse_args(argv)
    try:
        through = datetime.strptime(args.through, "%Y%m%d").date()
        start = datetime.strptime(args.start, "%Y%m%d").date() if args.start else None
        missing = recovery_days(through, start=start)
    except (RuntimeError, ValueError) as exc:
        print(f"恢复前置失败：{exc}", file=sys.stderr)
        return 1
    for target in missing:
        if run_daily_update(target) != 0:
            print(f"{target} 日更失败，停止恢复", file=sys.stderr)
            return 1
        if not readiness.preflight(target).ready:
            print(f"{target} 日更后仍未就绪，停止恢复", file=sys.stderr)
            return 1
    print(f"数据前置恢复完成：补齐 {len(missing)} 个交易日；未生成报告或 APNs。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
