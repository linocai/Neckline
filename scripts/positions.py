#!/usr/bin/env python3
"""持仓台账 CLI(plan 阶段3 §2.4「需要一个极简持仓台账,SQLite 表+CLI 录入/清仓
命令即可,勿造重界面」)。盘中持仓哨兵读的就是这张表(`status='open'` 的行)。

用法:
    python scripts/positions.py add 600519.SH 1720.00 100 20260720 [--note "低吸建仓"]
    python scripts/positions.py close 3 1750.00 20260722
    python scripts/positions.py list              # 当前持仓(status=open)
    python scripts/positions.py list --all         # 含已清仓的全部历史

不做任何仓位纪律校验(单笔上限/最多5只/总敞口)——那是系统对候选的建议约束,
不是对用户实际操作的强制拦截;系统只审计不拦人手动录入(§3.8「系统永不自动
下单/撤单/改止损」的同一条精神延伸到这里:也不替用户拦下单)。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import ensure_data_dirs  # noqa: E402
from neckline.sentinel.positions import (  # noqa: E402
    Position,
    close_position,
    load_all_positions,
    load_open_positions,
    open_position,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("positions")


def _parse_date(s: str):
    return datetime.strptime(s, "%Y%m%d").date()


def _fmt_row(p: Position) -> str:
    base = f"#{p.id}  {p.ts_code}  买入价{p.buy_price:.2f}×{p.qty}股  买入日{p.buy_date}  [{p.status}]"
    if p.status == "closed":
        base += f"  卖出价{p.sell_price:.2f}  卖出日{p.sell_date}"
    if p.note:
        base += f"  备注:{p.note}"
    return base


def cmd_add(args: argparse.Namespace) -> int:
    pid = open_position(args.ts_code, args.buy_price, args.qty, _parse_date(args.buy_date), note=args.note)
    logger.info("已开仓记账:#%d %s 买入价%.2f×%d股 买入日%s", pid, args.ts_code, args.buy_price, args.qty, args.buy_date)
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    ok = close_position(args.position_id, args.sell_price, _parse_date(args.sell_date))
    if not ok:
        logger.error("清仓失败:未找到 id=%d 的持仓,或该持仓已清仓。用 `list --all` 核对。", args.position_id)
        return 1
    logger.info("已清仓记账:#%d 卖出价%.2f 卖出日%s", args.position_id, args.sell_price, args.sell_date)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    rows = load_all_positions() if args.all else load_open_positions()
    if not rows:
        print("(无持仓记录)" if args.all else "(当前无持仓)")
        return 0
    for p in rows:
        print(_fmt_row(p))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="开仓记账")
    p_add.add_argument("ts_code", help="如 600519.SH")
    p_add.add_argument("buy_price", type=float)
    p_add.add_argument("qty", type=int, help="股数(非手)")
    p_add.add_argument("buy_date", help="YYYYMMDD")
    p_add.add_argument("--note", default=None)
    p_add.set_defaults(func=cmd_add)

    p_close = sub.add_parser("close", help="清仓记账")
    p_close.add_argument("position_id", type=int, help="`list` 展示的 # 编号")
    p_close.add_argument("sell_price", type=float)
    p_close.add_argument("sell_date", help="YYYYMMDD")
    p_close.set_defaults(func=cmd_close)

    p_list = sub.add_parser("list", help="查看持仓")
    p_list.add_argument("--all", action="store_true", help="含已清仓的历史记录")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    ensure_data_dirs()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
