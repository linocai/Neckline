"""参考件三件套落库(plan §五 v1.5-①-E,需求 9 配套:「参考件落库,将来与实际走势/
成交对拍 = LLM 参谋成绩单,给 P3-11 归因供维度」)。`reference_plans` 表读写单一通道
(schema 见 `neckline.db`);生成侧唯一实现在 `report/reference_plan.py`,本模块只读写、
不做任何领域计算。

**依赖方向 = store → reference_plan 单向**(同 `holding_store.py`/`news_alerts_store.py`
体例):本模块 import `reference_plan.ReferencePlan`,反过来不成立。

**只在调用方判定该写时才写**(同 `store.save_llm_judgment`/`holding_store.
save_holding_eod_checks` 惯例)——本函数自身不做 `save` 判断,`pipeline.py` 按
`if save:` 才调用本函数,预览/单测(`save=False`)不产生副作用。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline.db import connection, init_schema
from neckline.report.reference_plan import ReferencePlan

_COLUMNS = (
    "trade_date", "ts_code", "status", "verdict", "close", "limit_up", "limit_down",
    "buy_low", "buy_high", "buy_clamp", "buy_why", "stop_price", "stop_pct",
    "exit_low", "exit_high", "exit_clamp", "exit_why", "script_text", "veto_reason",
    "provider", "model", "degraded", "degrade_reason", "created_at",
)


def _d(trade_date: date) -> str:
    return trade_date.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_reference_plans(trade_date: date, plans: List[ReferencePlan], db_path: Optional[Path] = None) -> None:
    """把当日参考件逐票落库(`INSERT OR REPLACE`,幂等覆盖同 `(trade_date, ts_code)`
    ——同日重跑逐位相同,①验收⑦)。空 → 不写(同 `holding_store` 惯例)。"""
    if not plans:
        return
    init_schema(db_path)
    td = _d(trade_date)
    now = _now()
    placeholders = ",".join(["?"] * len(_COLUMNS))
    with connection(db_path) as conn:
        for p in plans:
            conn.execute(
                f"INSERT OR REPLACE INTO reference_plans ({','.join(_COLUMNS)}) VALUES ({placeholders})",
                (
                    td, p.ts_code, p.status, p.verdict, p.close, p.limit_up, p.limit_down,
                    p.buy_low, p.buy_high, p.buy_clamp, p.buy_why, p.stop_price, p.stop_pct,
                    p.exit_low, p.exit_high, p.exit_clamp, p.exit_why, p.script_text, p.veto_reason,
                    p.provider, p.model, 1 if p.degraded else 0, p.degrade_reason, now,
                ),
            )


def _row_to_dict(row: tuple) -> Dict[str, Any]:
    d = dict(zip(_COLUMNS, row))
    d["degraded"] = bool(d["degraded"])
    return d


def load_reference_plan(trade_date: date, ts_code: str, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """查某票某日的参考件落库行(审计/对拍用,本版不接任何报表消费方,§七 P3-11 挂账)。
    查一个从未生成过参考件的 (日期,代码) 是正常场景 → `None`。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {','.join(_COLUMNS)} FROM reference_plans WHERE trade_date=? AND ts_code=?",
            (_d(trade_date), ts_code),
        ).fetchone()
    return _row_to_dict(row) if row else None


def load_reference_plans(trade_date: date, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """查某日全部参考件落库行(按 `ts_code` 排序,审计/对拍用)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {','.join(_COLUMNS)} FROM reference_plans WHERE trade_date=? ORDER BY ts_code",
            (_d(trade_date),),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


__all__ = ["save_reference_plans", "load_reference_plan", "load_reference_plans"]
