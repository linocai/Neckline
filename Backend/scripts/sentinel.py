#!/usr/bin/env python3
"""盘中哨兵常驻脚本(plan 阶段3)。本地跑,交易时段内轮询,非交易时段优雅退出/
待机:

    · 非交易日 → 直接打日志退出(没有什么好等的,不待机整天)。
    · 交易日但未到 09:30 → 待机等到开盘(适合开机自启/提前启动的场景)。
    · 交易日且已过 15:00 → 直接退出(今天的活儿干完了)。
    · 09:30–15:00 → 反复调用 `sentinel.engine.run_tick`,默认 1 分钟一拍;
      午休(11:30–13:00)降频到 `--lunch-interval`(默认 5 分钟)——盘中数据在
      午休期间不变,没必要按盘中同等频率打免费源。

用法:
    python scripts/sentinel.py                        # 常驻轮询(前台跑,Ctrl-C 退出)
    python scripts/sentinel.py --once                  # 只跑一拍就退出(手动触发/调试)
    python scripts/sentinel.py --interval 30            # 自定义轮询间隔(秒)
    python scripts/sentinel.py --mac-notify             # 额外启用 macOS 本地通知

推送通道:默认「控制台日志 + Bark(若 `.env` 配了 BARK_URL)」,见
`neckline.sentinel.channels.default_channels`。

**今日(2026-07-20)无法做盘中实盘验证**(见 PROJECT_PLAN.md 完工报告的欠账
说明)——本脚本的行为由单测(`tests/test_scripts_sentinel.py`)与
`scripts/smoke_sentinel.py`(合成历史日盘中快照回放)覆盖,真正的实盘轮询
需要用户在下一个交易日实测,见 PROJECT_PLAN.md 变更日志。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time as time_module
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Callable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.calendar import is_trading_day  # noqa: E402
from neckline.config import ensure_data_dirs  # noqa: E402
from neckline.sentinel.channels import MacNotifyChannel, PushChannel, default_channels  # noqa: E402
from neckline.sentinel.engine import run_tick  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sentinel")

_OPEN = dtime(9, 30)
_CLOSE = dtime(15, 0)
_NOON_START = dtime(11, 30)
_NOON_END = dtime(13, 0)

DEFAULT_POLL_SECONDS = 60
DEFAULT_LUNCH_POLL_SECONDS = 300


def _next_interval(now: datetime, poll_seconds: int, lunch_poll_seconds: int) -> int:
    t = now.time()
    return lunch_poll_seconds if _NOON_START <= t < _NOON_END else poll_seconds


def _seconds_until(now: datetime, target: dtime) -> float:
    target_dt = datetime.combine(now.date(), target)
    return max((target_dt - now).total_seconds(), 0.0)


def _log_tick(n: int, now: datetime, result) -> None:
    if result.skipped_non_trading:
        logger.info("第%d拍 %s:非交易时段,跳过。", n, now.strftime("%H:%M:%S"))
        return
    # ⛔ V2.4.0 P0:「退潮%s;证伪%d」两段随两个哨兵退役从本行删除。
    logger.info(
        "第%d拍 %s:关注%d只/拉到%d只行情;持仓%d个信号;"
        "本拍推送%d条(去重跳过%d条)",
        n, now.strftime("%H:%M:%S"), result.watched_codes, result.quotes_fetched,
        len(result.holding_alerts),
        len(result.pushed_events), result.skipped_duplicate,
    )


def run_loop(
    *,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
    lunch_poll_seconds: int = DEFAULT_LUNCH_POLL_SECONDS,
    once: bool = False,
    channels: Optional[List[PushChannel]] = None,
    now_fn: Callable[[], datetime] = datetime.now,
    sleep_fn: Callable[[float], None] = time_module.sleep,
    max_ticks: Optional[int] = None,
) -> int:
    """主循环,抽成可测试的函数(`now_fn`/`sleep_fn` 可注入,单测据此不真的
    `time.sleep`、也不依赖真实系统时钟)。返回进程退出码。"""
    now = now_fn()
    today = now.date()
    if not is_trading_day(today):
        logger.info("%s 非交易日,哨兵不启动。", today)
        return 0

    channels = channels if channels is not None else default_channels()

    if now.time() < _OPEN:
        wait = _seconds_until(now, _OPEN)
        logger.info("未到开盘(09:30),待机 %.0f 秒…", wait)
        sleep_fn(wait)
    elif now.time() >= _CLOSE:
        logger.info("今日已收盘(>=15:00),哨兵不再轮询,直接退出。")
        return 0

    ticks = 0
    while True:
        now = now_fn()
        if now.time() >= _CLOSE:
            logger.info("收盘,哨兵退出。本次运行共 %d 拍。", ticks)
            return 0
        result = run_tick(now, channels=channels)
        ticks += 1
        _log_tick(ticks, now, result)
        if once or (max_ticks is not None and ticks >= max_ticks):
            return 0
        sleep_fn(_next_interval(now, poll_seconds, lunch_poll_seconds))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", help="只跑一拍就退出(手动触发/调试用)")
    parser.add_argument("--interval", type=int, default=DEFAULT_POLL_SECONDS, help=f"轮询间隔秒(默认{DEFAULT_POLL_SECONDS})")
    parser.add_argument(
        "--lunch-interval", type=int, default=DEFAULT_LUNCH_POLL_SECONDS,
        help=f"午休(11:30-13:00)降频间隔秒(默认{DEFAULT_LUNCH_POLL_SECONDS})",
    )
    parser.add_argument("--mac-notify", action="store_true", help="额外启用 macOS 本地通知(osascript,可选)")
    args = parser.parse_args()

    ensure_data_dirs()
    channels = default_channels()
    if args.mac_notify:
        channels = list(channels) + [MacNotifyChannel()]
    logger.info("推送通道:%s", "、".join(c.name for c in channels))

    return run_loop(
        poll_seconds=args.interval, lunch_poll_seconds=args.lunch_interval,
        once=args.once, channels=channels,
    )


if __name__ == "__main__":
    raise SystemExit(main())
