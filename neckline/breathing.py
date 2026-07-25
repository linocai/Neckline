"""呼吸试验仓 T 子账(plan §五 v1.2-G,§2.1 第 3 条仓位分配「2 短线追击 + 1 呼吸底仓
试验」配套)存取 + 派生计算。

**底仓 / T 仓分离记账(G.1,子表非扩列)**:底仓是 `positions` 表的一行(一次开仓一
行,语义不变——本模块绝不写 `positions`);持有期内的多次日内 T 是「一个底仓 → N 次
T」的一对多关系,`positions` 扩列表达不了 N 笔,故落独立子表 `breathing_t_trades`。
每行 = 一次**已闭合**的 T 回合(先买后卖或先卖后买,方向仅供用户在 `note` 里自行
备注、不落结构化字段;T 盈亏无论方向一律 `compute_t_pnl` 同式计算)。

**打法标签单一源 = `decision_log.playbook_tag`**(v1.2-B ⑧)——本模块**不**存第二份
打法标签;「这个底仓是不是呼吸仓」由它名下是否有 T 子账记录体现(只有呼吸打法才会
录 T),不是本表的字段。

**费用逐笔如实入账,不硬编费率(G.2)**:`fees` 由调用方(客户端录入)给,`add_trade`
原样落库——2 万规模双边佣金 + 印花税 ≈20 元≈0.1% 只是 plan 里的**背景参考数字**,
本模块任何地方都不把它当常量使用或用来估算。

**「先手」成本优势 = 读时派生,不落列(G.3)**:`compute_base_cost_adj`/
`compute_edge_to_price` 是纯函数,输入底仓 `buy_price`/`qty` + 本模块查出的 T 子账
+ 调用方另外拿到的现价(本模块**不拉行情**,现价由 `api/app.py` 走既有
`sentinel/quotes.py:get_quotes` 路径注入,同 `PositionOut.price` 的既定姿势,不新拉
数据源)。算不出(如无实时价、或 qty 非正)时返回 `None`,调用方据此下发 JSON
`null`,不崩、不拿 0 冒充「无优势」。

**写入只经本模块函数(同 `sentinel/positions.py`/`decision_log.py` 姿势)**:
`add_trade` 会先校验 `position_id` 指向的底仓存在(FK 关联,plan G.4「T 子账 CRUD +
position_id 外键关联」)——底仓不存在则返回 `None`(API 层据此 404,不建孤儿行);
`delete_trade` 硬删除(T 子账是可误录可撤的记账明细,不是需要保留删除痕迹的核心
台账,同「误录可删」的产品定位)。本模块任何函数都不触发下单 / 撤单 / 拉行情副作用
(§3.8 铁律)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from neckline.db import connection, init_schema
from neckline.sentinel import positions as pos_store

_SELECT_COLS = "id, position_id, buy_price, sell_price, qty, fees, t_date, note, created_at"


def compute_t_pnl(buy_price: float, sell_price: float, qty: int, fees: float) -> float:
    """一次 T 回合的净盈亏(plan G.1「T 盈亏 `=(sell−buy)×qty−fees` 同式,方向仅
    备注」)。无论「先买后卖」还是「先卖后买」,统一按此式计算——方向不影响这个
    公式,只在 `note` 里由用户自行标注,不是结构化字段。"""
    return (sell_price - buy_price) * qty - fees


@dataclass
class BreathingTrade:
    id: int
    position_id: int
    buy_price: float
    sell_price: float
    qty: int
    fees: float
    t_date: str            # 'YYYYMMDD'
    note: Optional[str]
    created_at: str

    @property
    def t_pnl(self) -> float:
        return compute_t_pnl(self.buy_price, self.sell_price, self.qty, self.fees)


def compute_base_cost_adj(
    buy_price: float, qty: Optional[int], trades: Iterable[BreathingTrade]
) -> Optional[float]:
    """底仓摊薄成本(plan G.3):原始买入成本按已闭合 T 的净盈亏摊薄到底仓股数上——
    `buy_price − (ΣT净盈亏)/底仓qty`。T 净盈亏为正(做 T 赚钱)拉低有效成本,为负则
    推高(两种方向,单测均覆盖)。`qty` 非正(防御性,正常底仓恒 >0)→ 算不出,返回
    `None`。"""
    if not qty or qty <= 0:
        return None
    net = sum(t.t_pnl for t in trades)
    return buy_price - net / qty


def compute_edge_to_price(base_cost_adj: Optional[float], price: Optional[float]) -> Optional[float]:
    """「先手」距离(plan G.3):现价相对摊薄成本的优势,口径同 `PositionOut.
    distToStopPct`(相对现价的比例,`(price−基准)/price`)——现价高于摊薄成本越多,
    数值越正。`base_cost_adj`/`price` 任一缺失(如无实时价)或 `price<=0` → `None`
    (调用方下发 JSON null,不崩,不拿 0 冒充「无优势」)。"""
    if base_cost_adj is None or price is None or price <= 0:
        return None
    return (price - base_cost_adj) / price


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _row_to_trade(row) -> BreathingTrade:
    return BreathingTrade(
        id=row[0], position_id=row[1], buy_price=row[2], sell_price=row[3],
        qty=row[4], fees=row[5], t_date=row[6], note=row[7], created_at=row[8],
    )


# —— 读 ——————————————————————————————————————————————————————————————————

def get_trade(trade_id: int, db_path: Optional[Path] = None) -> Optional[BreathingTrade]:
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM breathing_t_trades WHERE id=?", (trade_id,)
        ).fetchone()
    return _row_to_trade(row) if row else None


def list_trades(position_id: int, db_path: Optional[Path] = None) -> List[BreathingTrade]:
    """某底仓名下全部 T 子账,按 T 发生日升序(plan G.4 `GET .../trades` 的
    `items`)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM breathing_t_trades WHERE position_id=? ORDER BY t_date, id",
            (position_id,),
        ).fetchall()
    return [_row_to_trade(r) for r in rows]


# —— 写(唯一写入通道,同 `sentinel/positions.py`/`decision_log.py` 姿势)——————————

def add_trade(
    position_id: int,
    buy_price: float,
    sell_price: float,
    qty: int,
    fees: float,
    t_date: Optional[date] = None,
    note: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[BreathingTrade]:
    """录入一次已闭合的 T 回合(plan G.4 `POST /breathing/{position_id}/trades`)。
    `position_id` 必须指向一个已存在的底仓(`positions` 行)——**FK 关联校验**:底仓
    不存在 → 返回 `None`(API 层据此 404,不建孤儿 T 子账行)。`fees` 原样落库(不猜、
    不按费率估算,见模块头注释)。`t_date` 缺省 → 今日。"""
    init_schema(db_path)
    if pos_store.get_position(position_id, db_path=db_path) is None:
        return None
    now = _now()
    td = _d(t_date) if t_date is not None else _d(date.today())
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO breathing_t_trades "
            "(position_id, buy_price, sell_price, qty, fees, t_date, note, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (position_id, buy_price, sell_price, qty, fees, td, note, now),
        )
        new_id = int(cur.lastrowid)
    row = get_trade(new_id, db_path=db_path)
    assert row is not None  # 刚写入,必然读得到
    return row


def delete_trade(trade_id: int, db_path: Optional[Path] = None) -> bool:
    """误录可删(plan G.4 `DELETE /breathing/trades/{id}`)。硬删除——T 子账是可撤
    的记账明细,不是需要留痕的核心台账。返回是否命中该 id(不存在 → False,API 层
    据此 404;对已删过的 id 重复调用同样返回 False,不报错——语义天然幂等)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        cur = conn.execute("DELETE FROM breathing_t_trades WHERE id=?", (trade_id,))
        return cur.rowcount > 0


__all__ = [
    "BreathingTrade",
    "compute_t_pnl",
    "compute_base_cost_adj",
    "compute_edge_to_price",
    "get_trade",
    "list_trades",
    "add_trade",
    "delete_trade",
]
