#!/usr/bin/env python3
"""持仓台账 CLI(plan 阶段3 §2.4「需要一个极简持仓台账,SQLite 表+CLI 录入/清仓
命令即可,勿造重界面」)。盘中持仓哨兵读的就是这张表(`status='open'` 的行)。

用法:
    python scripts/positions.py add 600519.SH 1720.00 100 20260720 [--note "低吸建仓"]
    python scripts/positions.py close 3 1750.00 20260722 [--reason STOP_LOSS]
    python scripts/positions.py list              # 当前持仓(status=open)
    python scripts/positions.py list --all         # 含已清仓的全部历史

`close` 后**自动跑一次熔断评估**(2026-07-27 审计 🔵-6:此前只挂在 API 端点,CLI 补录的
第 3 笔止损不会当场触发熔断)——纯提醒层,只建触发行 + 发提醒,绝不代下单/撤单。

不做任何仓位纪律校验(单笔上限/最多5只/总敞口)——那是系统对候选的建议约束,
不是对用户实际操作的强制拦截;系统只审计不拦人手动录入(§3.8「系统永不自动
下单/撤单/改止损」的同一条精神延伸到这里:也不替用户拦下单)。

**v2.0.0(⑩-A/D)**:`add`/`close` 改走 `neckline.positions_entry`(与 API
`POST /positions`/`POST /positions/{id}/close` 共用同一份编排——entry_snapshots
冻结 / position_plans 继承 / user_actions 自动记账,CLI 与 API 两条入口行为
逐位一致,不因调用方是谁而不同)。实时报价只在 `buy_date` 是今天时才尝试拉取
(历史补录不该被"此刻"的行情污染快照),CLI 场景无网络也不阻断记账(best-effort,
异常已被 `positions_entry` 内部吞掉)。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline import positions_entry  # noqa: E402
from neckline.config import ensure_data_dirs  # noqa: E402
from neckline.sentinel.positions import (  # noqa: E402
    CLOSE_REASON_CODES,
    Position,
    load_all_positions,
    load_open_positions,
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


def _resolve_quote_for_cli(ts_code: str, buy_date):
    """CLI 侧的 best-effort 实时报价(同 `api/app.py::_resolve_quote_one` 姿势,
    但没有 `_QUOTES_FN` 注入钩子——CLI 直接调 `sentinel.quotes.get_quote`)。只在
    买入日=今天才取(历史补录不该被"此刻"行情污染快照);任何失败(含无网络)
    → `None`,不阻断记账。"""
    if buy_date != date.today():
        return None
    try:
        from neckline.sentinel.quotes import get_quote
        return get_quote(ts_code)
    except Exception:  # noqa: BLE001
        logger.warning("CLI 开仓快照拉实时价失败(quote 落 None,不影响记账)", exc_info=True)
        return None


def cmd_add(args: argparse.Namespace) -> int:
    buy_date = _parse_date(args.buy_date)
    quote = _resolve_quote_for_cli(args.ts_code, buy_date)
    result = positions_entry.record_buy(
        args.ts_code, args.buy_price, args.qty, buy_date, note=args.note, quote=quote,
    )
    logger.info("已开仓记账:#%d %s 买入价%.2f×%d股 买入日%s", result.position_id, args.ts_code, args.buy_price, args.qty, args.buy_date)
    if result.source_basket_key:
        logger.info("  来源篮子:%s(%s)Tier%s 角色=%s", result.source_basket_name, result.source_basket_key, result.tier, result.role)
    else:
        logger.info("  独立买入(当日现役卡里未查到该票)")
    if result.plan_deviation_notice:
        logger.warning("  %s", result.plan_deviation_notice)
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    sell_date = _parse_date(args.sell_date)
    ok = positions_entry.record_sell(args.position_id, args.sell_price, sell_date,
                                      close_reason=args.close_reason)
    if not ok:
        logger.error("清仓失败:未找到 id=%d 的持仓,或该持仓已清仓。用 `list --all` 核对。", args.position_id)
        return 1
    logger.info("已清仓记账:#%d 卖出价%.2f 卖出日%s", args.position_id, args.sell_price, args.sell_date)
    # V2.2-⑤-B(裁定 #8 熔断整体退役):CLI 与 API 走**同一段**连续止损纯提醒编排
    # (唯一实现 `positions_entry.notice_consecutive_stops_after_close`,零状态、零锁、
    # 尽力而为),行为不再取决于「从哪个口子录的」——2026-07-27 审计 🔵-6 补的那条
    # 「两个入口同一段」的纪律原样保留,只是被提醒的东西从"熔断"变成了一条提醒。
    positions_entry.notice_consecutive_stops_after_close(args.position_id, sell_date=sell_date)
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

    p_close = sub.add_parser("close", help="清仓记账(清仓后自动跑一次熔断评估)")
    p_close.add_argument("position_id", type=int, help="`list` 展示的 # 编号")
    p_close.add_argument("sell_price", type=float)
    p_close.add_argument("sell_date", help="YYYYMMDD")
    # 离场原因(可选):唯一源 `positions.CLOSE_REASON_CODES`;不传 → NULL(熔断走价格近似
    # 兜底)。argparse choices 直接吃白名单,CLI 侧不可能写出非法码(与 store 层白名单防线
    # 互补,后者管脚本/手工 SQL 那条路)。
    p_close.add_argument("--reason", dest="close_reason", default=None,
                         choices=list(CLOSE_REASON_CODES),
                         help="离场原因码(不传=未标注,熔断按卖出价近似判止损)")
    p_close.set_defaults(func=cmd_close)

    p_list = sub.add_parser("list", help="查看持仓")
    p_list.add_argument("--all", action="store_true", help="含已清仓的历史记录")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    ensure_data_dirs()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
