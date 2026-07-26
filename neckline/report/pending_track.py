"""挂单未成交追踪(plan §五 v1.3-④,原 §五 v1.2.1-C 全文,归属改 v1.3)。

**目的**:用户习惯挂低价等回踩,记录**未成交计划**(`decision_log.status='pending'`)
的后续 N 日走势,检验用户「逆向选择:专接下坠、错过起飞」假设(飞了 = 错过 /
跌了 = 躲过)。**归 v1.3 理由**:它记的是「挂了没成交的计划后来怎么走」,不部署
就永远补不回那几周数据(与决策归因不同——归因晚做只晚出结论,数据本身仍在)。

**折进 16:35 报告管线,不新拉数据源**:`track_pending_decisions(trade_date, ...)`
由 `report/pipeline.py::build_report` 在 `if save:` 块内、报告落库后调用一次
(同 `holding_store.save_holding_eod_checks`/`news_alerts_store.save_news_alerts`
的收尾姿势)。EOD 收盘价复用既有日线数据访问层
`strategy.features.build_research_panel(trade_date, trade_date)`(前复权口径同
其它报告子模块),**不新建 Parquet 读取路径、不落 Parquet**——本追踪只落 SQLite
(`decision_pending_track` 表,schema 见 `neckline.db`)。

**追踪窗口 N 写死**:`DECISION_PENDING_TRACK_DAYS = 5`(单一源;与 `hold=5` /
D5 时间退出 horizon 同口径,覆盖短线 1-2 日打法的相关观察窗)。

**d_offset 语义(距创建后第几个交易日,不含创建当日本身)**:决策 `created_at`
当日或更早的 `trade_date` → offset 0,尚未到追踪窗口,本次跳过(不写行,留给
未来某天的报告run 再算)。offset 达到或超过 N → 该行落库后**立即**把决策
`status` 转 `expired`(未成交自动过期,追踪定格)。**自愈**:若报告管线曾断跑
导致查询当天时 offset 一次性跳过 N(如 offset=7),仍**如实**按【实际 offset】
落一行再过期——不假装观测发生在第 N 天,也绝不让决策永久卡在 pending(参照
本项目「同日重跑幂等 / 不静默卡死」的既定工程纪律)。

**只追踪当前仍 `pending` 的决策**:每次调用重新查询 `decision_log` 的
`status='pending'` 行——一旦某条决策被 `link_decision`(成交)或
`cancel_decision`(放弃)改走,状态不再是 pending,自然从下一次查询结果中消失,
不需要额外的「停止追踪」逻辑;修订链(`revise_decision` 产生的新行)有自己独立
的 `id`/`created_at`,按同一规则独立追踪,不特殊处理。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import polars as pl

from neckline import decision_log
from neckline.calendar.trading_calendar import next_trading_day, trading_days_between
from neckline.db import connection, init_schema
from neckline.strategy.features import build_research_panel

DECISION_PENDING_TRACK_DAYS = 5  # N,单一源(与 max_hold_days=5 / D5 时间退出同口径)


def _d(trade_date: date) -> str:
    return trade_date.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _offset(created_at: str, trade_date: date) -> int:
    """`trade_date` 距 `created_at`(取其日期部分)之后第几个交易日,**不含创建
    当日本身**。`created_at` 当日或更早(`trade_date` 未晚于创建日)→ 0。

    用 `next_trading_day(created_date)`(严格晚于创建日的下一交易日)作为窗口
    起点,再用 `trading_days_between(start, trade_date)` 数闭区间交易日数——
    两者都是交易日历唯一源(`neckline.calendar.trading_calendar`),不新写日期
    运算。"""
    created_date = created_at[:10]
    start = next_trading_day(created_date)
    return len(trading_days_between(start, trade_date))


def _save_track_row(
    decision_id: int,
    trade_date: date,
    d_offset: int,
    close: float,
    ret_from_plan: Optional[float],
    db_path: Optional[Path],
) -> None:
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO decision_pending_track "
            "(decision_id, trade_date, d_offset, close, ret_from_plan, recorded_at) "
            "VALUES (?,?,?,?,?,?)",
            (decision_id, _d(trade_date), d_offset, close, ret_from_plan, now),
        )


def load_track_rows(decision_id: int, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """某条决策的全部追踪快照(按 `trade_date` 升序)。供单测核对 + 未来端点
    (§v1.3 客户端契约清单「挂单追踪」)复用,读专用、无写副作用。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT decision_id, trade_date, d_offset, close, ret_from_plan, recorded_at "
            "FROM decision_pending_track WHERE decision_id=? ORDER BY trade_date",
            (decision_id,),
        ).fetchall()
    return [
        {
            "decisionId": r[0], "tradeDate": r[1], "dOffset": r[2],
            "close": r[3], "retFromPlan": r[4], "recordedAt": r[5],
        }
        for r in rows
    ]


def track_pending_decisions(
    trade_date: date,
    *,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> int:
    """16:35 报告管线收尾步骤(C.3)。对每条 `status='pending'` 的决策算距
    `created_at` 之后第几个交易日,窗口内(offset ≥ 1)落一行 `decision_pending_track`
    (同 `(decision_id, trade_date)` 幂等覆盖,同日重跑不重复);offset 达到或超过
    `DECISION_PENDING_TRACK_DAYS` → 该决策同批转 `expired`(见模块 docstring
    「自愈」段)。已 `filled`/`cancelled` 的决策不追踪(不在 `status='pending'`
    查询结果内,天然排除)。

    `close` 取自 `build_research_panel(trade_date, trade_date)`(与其它报告子
    模块同一份 EOD 面板访问层,不新拉数据源);某只票当日面板查无(停牌 / 未覆盖)
    → 本次跳过该条(不崩、不臆造,留给下次报告 run 再追)。`ret_from_plan` =
    `(close - planned_price) / planned_price`;`planned_price` 缺失(None 或 0)
    → `ret_from_plan` 落 `None`,不臆造。

    返回本次落库的追踪行数(供调用方/测试断言)。"""
    pending = decision_log.list_decisions(status=decision_log.STATUS_PENDING, db_path=db_path)
    if not pending:
        return 0

    due = [(d, _offset(d.created_at, trade_date)) for d in pending]
    due = [(d, offset) for d, offset in due if offset >= 1]
    if not due:
        return 0

    codes = sorted({d.ts_code for d, _off in due})
    panel = build_research_panel(trade_date, trade_date, with_forward=False, parquet_dir=parquet_dir)
    if panel is None or panel.is_empty():
        return 0
    sub = panel.filter(pl.col("ts_code").is_in(codes)).select(["ts_code", "close"])
    closes: Dict[str, Optional[float]] = {r["ts_code"]: r["close"] for r in sub.to_dicts()}

    written = 0
    for d, offset in due:
        close = closes.get(d.ts_code)
        if close is None:
            continue  # 当日无该票行情(停牌/未覆盖),本次跳过,不崩、不臆造
        close = float(close)
        ret_from_plan = (
            (close - d.planned_price) / d.planned_price
            if d.planned_price is not None and d.planned_price != 0
            else None
        )
        _save_track_row(d.id, trade_date, offset, close, ret_from_plan, db_path=db_path)
        written += 1
        if offset >= DECISION_PENDING_TRACK_DAYS:
            decision_log.expire_decision(d.id, db_path=db_path)
    return written


__all__ = [
    "DECISION_PENDING_TRACK_DAYS",
    "track_pending_decisions",
    "load_track_rows",
]
