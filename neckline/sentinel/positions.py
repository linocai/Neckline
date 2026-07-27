"""极简持仓台账(plan 阶段3 §2.4 持仓哨兵数据源)。SQLite `positions` 表 +
CLI(`scripts/positions.py`)录入/清仓——**故意不造重界面**,字段只留持仓哨兵
真正需要的:code/买入价/数量/买入日(+ 状态/卖出价/卖出日/备注)。

一票可分批多次开仓(每次调用 `open_position` 各开一行,不合并),`status='open'`
的行是盘中持仓哨兵的监控对象;`close_position` 只改状态,不做任何盈亏计算或
下单动作(§3.8 铁律:系统永不自动下单/撤单/改止损,本表只做记账)。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from neckline.calendar import trading_days_between
from neckline.db import connection, init_schema
from neckline.review.parse import normalize_ts_code

logger = logging.getLogger(__name__)

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"

# 离场原因枚举码(v1.2-A2 熔断纪律,服务端码 + 客户端展示层换算,沿 `boardLabel` 先例)。
# 唯一源在此(schemas.py 的 `PositionCloseIn.closeReason` Literal 白名单同串,pydantic
# 约束需字面量,不能引用变量——同 decision_log 的 THESIS_TAG_CODES/schemas Literal 并存惯例)。
CLOSE_REASON_STOP_LOSS = "STOP_LOSS"       # 止损
CLOSE_REASON_TAKE_PROFIT = "TAKE_PROFIT"   # 回落止盈
CLOSE_REASON_TIME_EXIT = "TIME_EXIT"       # 时间退出(D5)
CLOSE_REASON_INVALIDATION = "INVALIDATION" # 证伪离场
CLOSE_REASON_MANUAL = "MANUAL"             # 主动离场
CLOSE_REASON_CODES = (
    CLOSE_REASON_STOP_LOSS, CLOSE_REASON_TAKE_PROFIT, CLOSE_REASON_TIME_EXIT,
    CLOSE_REASON_INVALIDATION, CLOSE_REASON_MANUAL,
)


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
    close_reason: Optional[str] = None   # v1.2-A2 离场原因枚举码;NULL=未标注(熔断走价格兜底)
    buy_fees: Optional[float] = None     # v1.3 补录开仓实付买入费用(佣金+过户费);NULL=未录
    sell_fees: Optional[float] = None    # v1.3 清仓实付卖出费用(真实回填,周复盘用真数);NULL=未录


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_position(row: sqlite3.Row) -> Position:
    return Position(
        id=row[0], ts_code=row[1], buy_price=row[2], qty=row[3], buy_date=row[4],
        status=row[5], sell_price=row[6], sell_date=row[7], note=row[8],
        close_reason=row[9], buy_fees=row[10], sell_fees=row[11],
    )


# close_reason/buy_fees/sell_fees 随 v1.2-A2/v1.3 加入投影(所有读入口都先 init_schema,
# 列必然已迁移存在,故无需像 brain.py 那样做条件投影)。
_SELECT_COLS = (
    "id, ts_code, buy_price, qty, buy_date, status, sell_price, sell_date, note, "
    "close_reason, buy_fees, sell_fees"
)


def open_position(
    ts_code: str,
    buy_price: float,
    qty: int,
    buy_date: date,
    note: Optional[str] = None,
    buy_fees: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> int:
    """开一笔仓位记账(不校验是否符合仓位纪律——§2.1 仓位上限是系统对报告候选的
    建议约束,不是对用户实际操作的强制拦截;系统只审计、不拦人手动录入)。返回新
    记录的 id。`buy_fees`(v1.3,可选):补录开仓实付买入费用(佣金+过户费),供 D5
    净浮盈判向反推佣金率 / 周复盘对账;不传 → NULL(估算走默认率兜底,见 fees.py)。

    **`ts_code` 在写入通道归一(v1.3.3 修复,生产真洞)**:此前本函数把调用方给的串
    原样落库,而 `POST /positions` 直接透传客户端 `body.code`——用户在客户端敲裸 6 位
    (`300759`)就会以裸码入库。裸码在盘中哨兵侧无碍(`quotes.to_symbol` 自己会补前缀),
    但 16:35 EOD 持仓管线(`report/holding_k4_check.py::build_holding_checks`)是拿
    `ts_code` **直接 join 行情面板**(面板是 TuShare 口径 `300759.SZ`)——裸码 join 不上
    → `has_data=False` / `close=0` / `net_float=None`,K4 派发警报永不触发、D5 判向被保守
    锁成「非浮盈次日退出」,且**全程静默无报错**。归一放在**写入通道**(而非 API 层),
    姿势与 `neckline/watchlist.py` 一致:CLI(`scripts/positions.py`)、API、未来任何调用方
    都自动吃到,不必各自记得调一次。归一唯一源 `review.parse.normalize_ts_code`
    (内部复用 `quotes.to_symbol` + `board.classify_by_code`,不新写正则)。"""
    init_schema(db_path)
    now = _now()
    ts_code = normalize_ts_code(ts_code)
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO positions (ts_code, buy_price, qty, buy_date, status, note, buy_fees, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (ts_code, buy_price, qty, _d(buy_date), STATUS_OPEN, note, buy_fees, now, now),
        )
        return int(cur.lastrowid)


def close_position(
    position_id: int,
    sell_price: float,
    sell_date: date,
    close_reason: Optional[str] = None,
    sell_fees: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """清仓记账。找不到该 id 或已是 closed 状态 → 返回 False(不抛异常,CLI 据此
    给用户一句清楚的提示,不是静默失败)。

    `close_reason`(v1.2-A2,可选):离场原因枚举码(`CLOSE_REASON_*`);不传 → 落库
    NULL(熔断评估时才走价格近似兜底判止损,见 `sentinel/circuit.py`)。**用户显式
    标注的合法码原样落库、信用户标注**;绝不代下单/撤单(§3.8,只记账)。

    **非法码防线(2026-07-27 审计 🔵-5)**:不在 `CLOSE_REASON_CODES` 白名单里的非空码
    (如小写 `'stop_loss'`、手工 SQL 写的自造串)**降为 NULL + 打 warning**,不原样落库。
    根因:`circuit._is_stop_loss_close` 对**非空** `close_reason` 一律「信标注、不用价格
    二次猜」,只认 `STOP_LOSS` —— 于是一个大小写写错的 `'stop_loss'` 会让这笔既不算止损、
    也不走价格兜底,**熔断静默失效**(审计反例 D4 实测:三笔 −6% 卖出因此不触发)。降为
    NULL 后回到价格近似兜底 = 保守方向(该算止损的仍被算上)。**降级而不是拒绝**:清仓
    是「只减」方向,系统绝不因记账问题拦住用户把仓位记平(§3.8 精神)。

    `sell_fees`(v1.3,可选):清仓实付卖出费用真数,成交后回填——**周复盘对账用真数、
    不用估数**(D5 净浮盈判向阶段的卖出费只能估,见 fees.py;真数回填在此)。不传 → NULL。"""
    init_schema(db_path)
    now = _now()
    if close_reason is not None and close_reason not in CLOSE_REASON_CODES:
        logger.warning(
            "非法 close_reason=%r(不在白名单 %s)——降为 NULL 落库,熔断评估退价格近似兜底"
            "(position_id=%s)。合法码见 neckline.sentinel.positions.CLOSE_REASON_CODES。",
            close_reason, list(CLOSE_REASON_CODES), position_id,
        )
        close_reason = None
    with connection(db_path) as conn:
        row = conn.execute("SELECT status FROM positions WHERE id=?", (position_id,)).fetchone()
        if row is None or row[0] == STATUS_CLOSED:
            return False
        conn.execute(
            "UPDATE positions SET status=?, sell_price=?, sell_date=?, close_reason=?, sell_fees=?, "
            "updated_at=? WHERE id=?",
            (STATUS_CLOSED, sell_price, _d(sell_date), close_reason, sell_fees, now, position_id),
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
    "CLOSE_REASON_STOP_LOSS",
    "CLOSE_REASON_TAKE_PROFIT",
    "CLOSE_REASON_TIME_EXIT",
    "CLOSE_REASON_INVALIDATION",
    "CLOSE_REASON_MANUAL",
    "CLOSE_REASON_CODES",
    "open_position",
    "close_position",
    "load_open_positions",
    "load_all_positions",
    "get_position",
    "count_opens_on",
    "d_count",
]
