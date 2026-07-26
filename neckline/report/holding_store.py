"""持仓 K4 体检 + D5 净浮盈快照存取(plan §五 v1.3-②)。`holding_eod_check` 表的读写
单一通道(schema 见 `neckline.db`)。三个消费方:

    ① 16:35 报告管线(`report/pipeline.py`)算好每持仓的 K4 命中 + D5 净浮盈 → `save_holding_eod_checks`。
    ② `GET /positions`(`api/app.py`)读**最近一份**快照嵌 `PositionOut.k4Advisory[]` + scenarioReviewPending
       (像 watchlist 读体检快照,不在请求期重建 250 日面板)—— `load_latest_checks_by_position`。
    ③ **次日 9:25:30 `sentinel/precall.py`** 的 net_float_provider 读**最近一份** net_float
       (= v1.3-① 留的 seam 接线点)—— `latest_net_float_map` / `net_float_provider`。

**为何用「最近一份」net_float**:D5 收盘净浮盈是 EOD 量,precall 9:25:30 时当日 D5 收盘未出,
故读上一交易日 16:35 落的那份(该持仓最大 trade_date 的快照)。这修复 v1.3-① 留的
「provider 恒 None → 激活后所有单子保守判非浮盈、浮盈豁免形同虚设」的地基缺口。

本模块只读写,不做任何 K4 计算(计算在 `report/holding_k4_check.py`)、不触发下单(§3.8)。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from neckline.db import connection, init_schema


def _d(trade_date: date) -> str:
    return trade_date.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_holding_eod_checks(trade_date: date, items: List[Any], db_path: Optional[Path] = None) -> None:
    """把当日各持仓 K4 体检项落库(幂等覆盖同 (position_id, trade_date))。`items` 为
    `report.holding_k4_check.HoldingK4Item`(duck-typed:需 position_id/d_count/net_float/
    time_exit_state/max_hold_effective/has_strong/scenario_review/hits_public())。空 → 不写。"""
    if not items:
        return
    init_schema(db_path)
    td = _d(trade_date)
    now = _now()
    with connection(db_path) as conn:
        for it in items:
            conn.execute(
                "INSERT OR REPLACE INTO holding_eod_check "
                "(position_id, trade_date, d_count, net_float, time_exit_state, max_hold_effective, "
                "k4_hits_json, has_strong, scenario_review, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    it.position_id, td, it.d_count, it.net_float, it.time_exit_state,
                    it.max_hold_effective, json.dumps(it.hits_public(), ensure_ascii=False),
                    1 if it.has_strong else 0, 1 if it.scenario_review else 0, now,
                ),
            )


def _parse_hits(raw: Optional[str]) -> List[Dict[str, Any]]:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def load_latest_checks_by_position(db_path: Optional[Path] = None) -> Dict[int, Dict[str, Any]]:
    """每个 position_id 的**最近一份**(最大 trade_date)K4 体检快照。供 `GET /positions`
    嵌 `k4Advisory[]` + `scenarioReviewPending`。无快照的持仓(刚开仓未体检)不在返回集,
    调用方回退空数组。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT position_id, trade_date, d_count, net_float, time_exit_state, "
            "max_hold_effective, k4_hits_json, has_strong, scenario_review "
            "FROM holding_eod_check ORDER BY position_id, trade_date"
        ).fetchall()
    out: Dict[int, Dict[str, Any]] = {}
    for r in rows:  # 按 trade_date 升序遍历,同 position 后者覆盖 → 最终留最大 trade_date 那份
        out[int(r[0])] = {
            "position_id": int(r[0]), "trade_date": r[1], "d_count": r[2],
            "net_float": r[3], "time_exit_state": r[4], "max_hold_effective": r[5],
            "hits": _parse_hits(r[6]), "has_strong": bool(r[7]), "scenario_review": bool(r[8]),
        }
    return out


def latest_net_float_map(db_path: Optional[Path] = None) -> Dict[int, Optional[float]]:
    """每个 position_id 的**最近一份** net_float(供 precall net_float_provider)。NULL(停牌/
    无 EOD 数据当日算不出净浮盈)如实留 None,precall 侧退保守判非浮盈。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT position_id, net_float FROM holding_eod_check ORDER BY position_id, trade_date"
        ).fetchall()
    out: Dict[int, Optional[float]] = {}
    for pid, nf in rows:  # 升序遍历,后者覆盖 → 留最大 trade_date 那份
        out[int(pid)] = (float(nf) if nf is not None else None)
    return out


def net_float_provider(db_path: Optional[Path] = None) -> Callable[[Any], Optional[float]]:
    """构造 precall `scan_time_exits(net_float_provider=...)` 用的 provider(§五 v1.3-② seam
    接线)。一次性把最近一份 net_float 读成 dict,provider 按 `position.id` O(1) 查——修复
    v1.3-① 留的「provider 恒 None → 浮盈豁免形同虚设」。查无(刚开仓未体检)→ None(保守)。"""
    nf_map = latest_net_float_map(db_path=db_path)
    return lambda pos: nf_map.get(getattr(pos, "id", None))


__all__ = [
    "save_holding_eod_checks",
    "load_latest_checks_by_position",
    "latest_net_float_map",
    "net_float_provider",
]
