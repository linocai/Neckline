"""极简持仓台账(plan 阶段3 §2.4 持仓哨兵数据源)。SQLite `positions` 表 +
CLI(`scripts/positions.py`)录入/清仓——**故意不造重界面**,字段只留持仓哨兵
真正需要的:code/买入价/数量/买入日(+ 状态/卖出价/卖出日/备注)。

一票可分批多次开仓(每次调用 `open_position` 各开一行,不合并),`status='open'`
的行是盘中持仓哨兵的监控对象;`close_position` 只改状态,不做任何盈亏计算或
下单动作(§3.8 铁律:系统永不自动下单/撤单/改止损,本表只做记账)。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from neckline.calendar import trading_days_between
from neckline.db import connection, init_schema

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"


@dataclass
class Position:
    id: int
    ts_code: str
    buy_price: float
    qty: int
    buy_date: str          # 'YYYYMMDD'
    status: str
    sell_price: Optional[float]
    sell_date: Optional[str]
    note: Optional[str]


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_position(row: sqlite3.Row) -> Position:
    return Position(
        id=row[0], ts_code=row[1], buy_price=row[2], qty=row[3], buy_date=row[4],
        status=row[5], sell_price=row[6], sell_date=row[7], note=row[8],
    )


_SELECT_COLS = "id, ts_code, buy_price, qty, buy_date, status, sell_price, sell_date, note"


def open_position(
    ts_code: str,
    buy_price: float,
    qty: int,
    buy_date: date,
    note: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    """开一笔仓位记账(不校验是否符合仓位纪律——§2.1 仓位上限是系统对报告候选的
    建议约束,不是对用户实际操作的强制拦截;系统只审计、不拦人手动录入)。返回新
    记录的 id。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO positions (ts_code, buy_price, qty, buy_date, status, note, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ts_code, buy_price, qty, _d(buy_date), STATUS_OPEN, note, now, now),
        )
        return int(cur.lastrowid)


def close_position(
    position_id: int,
    sell_price: float,
    sell_date: date,
    db_path: Optional[Path] = None,
) -> bool:
    """清仓记账。找不到该 id 或已是 closed 状态 → 返回 False(不抛异常,CLI 据此
    给用户一句清楚的提示,不是静默失败)。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        row = conn.execute("SELECT status FROM positions WHERE id=?", (position_id,)).fetchone()
        if row is None or row[0] == STATUS_CLOSED:
            return False
        conn.execute(
            "UPDATE positions SET status=?, sell_price=?, sell_date=?, updated_at=? WHERE id=?",
            (STATUS_CLOSED, sell_price, _d(sell_date), now, position_id),
        )
        return True


def load_open_positions(db_path: Optional[Path] = None) -> List[Position]:
    """全部 `status='open'` 持仓(盘中持仓哨兵的监控对象),按开仓日升序。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM positions WHERE status=? ORDER BY buy_date, id",
            (STATUS_OPEN,),
        ).fetchall()
    return [_row_to_position(r) for r in rows]


def load_all_positions(db_path: Optional[Path] = None) -> List[Position]:
    """全部持仓(含已清仓),供 CLI `list --all` 展示历史用。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(f"SELECT {_SELECT_COLS} FROM positions ORDER BY buy_date, id").fetchall()
    return [_row_to_position(r) for r in rows]


def get_position(position_id: int, db_path: Optional[Path] = None) -> Optional[Position]:
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(f"SELECT {_SELECT_COLS} FROM positions WHERE id=?", (position_id,)).fetchone()
    return _row_to_position(row) if row else None


def count_opens_on(trade_date: date, db_path: Optional[Path] = None) -> int:
    """`buy_date == trade_date` 的持仓记录条数(不论 open/closed)——供报告「漏录兜底」
    (plan v1.1-B.4)判断「今日台账是否有新增开仓」。当日买当日又清仓也算「有补录」
    (用户确实录了),故不限 status。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE buy_date=?", (_d(trade_date),)
        ).fetchone()
    return int(row[0]) if row else 0


def d_count(buy_date: date, trade_date: date) -> int:
    """持仓 D 计数(plan v1.1 铁律「D 计数单一源」)。**买入日 = D1**,交易日历口径
    (市场交易日,不因个股停牌而减);唯一实现放这里,服务端算好随 `PositionOut.dCount`
    下发,客户端不重算日历(杜绝双端漂移)。

        d_count = len(trading_days_between(buy_date, trade_date))   # 闭区间交易日数

    容差场景(plan B.1 明列,加单测):
        · 周末 / 长假录入的 buy_date(非交易日):`trading_days_between` 只数区间内
          真交易日,故从「buy_date 之后第一个交易日」起计 D1,不会把周末算进去。
        · 停牌日:D 计数按**市场**交易日走(停牌是个股事件,不缩短市场日历),故停牌
          期间 D 计数照常递增——与规则 v1「持有满 max_hold_days 交易日即时间退出」同口径。
        · `trade_date < buy_date`(异常/未来买入):区间空 → 0(防御,生产不该出现)。

    buy_date 均在近数交易日内,不触发 CLAUDE.md 记的 trade_cal 覆盖范围外逐自然日刷屏坑。
    """
    return len(trading_days_between(buy_date, trade_date))


__all__ = [
    "Position",
    "STATUS_OPEN",
    "STATUS_CLOSED",
    "open_position",
    "close_position",
    "load_open_positions",
    "load_all_positions",
    "get_position",
    "count_opens_on",
    "d_count",
]
