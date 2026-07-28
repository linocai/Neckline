"""持仓 K4 体检 + D5 净浮盈快照存取(plan §五 v1.3-②)。`holding_eod_check` 表的读写
单一通道(schema 见 `neckline.db`)。三个消费方:

    ① 16:35 报告管线(`report/pipeline.py`)算好每持仓的 K4 命中 + D5 净浮盈 → `save_holding_eod_checks`。
    ② `GET /positions`(`api/app.py`)读**最近一份**快照嵌 `PositionOut.k4Advisory[]` + scenarioReviewPending
       + **定格判向**(不在请求期重建 250 日面板、也不重判时间退出)—— `load_latest_checks_by_position`。
    ③ **次日 9:25:30 `sentinel/precall.py`** 读**定格判向** —— `locked_time_exit_map` /
       `locked_state_provider`。

**两档时间退出「D5 判一次定格」(2026-07-27 审计 🔴-1 修复,用户拍板方案 A)**:16:35 首次遇到
`d_count ≥ max_hold_days` 的那天,用**当日 EOD 收盘**净浮盈判一次向,写进
`time_exit_locked_state`/`_date`/`_net_float` 三列,并在此后每天的行里**原样带过来**(每行自描述
「今天生效的判向是哪天、按多少净浮盈定格的」)。三个消费点一律读定格值,**不再用当日最新净浮盈
重判**——旧写法(precall 读最近一份 `net_float` 重判)会让「D5 判该走、用户没走、D6 转浮盈」的
违纪在 D7 被系统改口豁免。D15 硬上限不受定格影响,仍按 `d_count` 无条件判。

`latest_net_float_map` 保留作**审计/展示**用(「这单最近一份 EOD 净浮盈多少」),**不再参与任何
判向**——判向只认定格值。

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
    time_exit_state/max_hold_effective/has_strong/scenario_review/hits_public() +
    定格三件 time_exit_locked_state/_date/_net_float)。空 → 不写。"""
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
                "k4_hits_json, has_strong, scenario_review, time_exit_locked_state, "
                "time_exit_locked_date, time_exit_locked_net_float, data_unavailable, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    it.position_id, td, it.d_count, it.net_float, it.time_exit_state,
                    it.max_hold_effective, json.dumps(it.hits_public(), ensure_ascii=False),
                    1 if it.has_strong else 0, 1 if it.scenario_review else 0,
                    getattr(it, "time_exit_locked_state", None),
                    getattr(it, "time_exit_locked_date", None),
                    getattr(it, "time_exit_locked_net_float", None),
                    # v1.4-①-B:当日无 EOD 行 → 整份体检被跳过,这一位必须落库(否则
                    # `GET /positions` 读快照时分不清「空牌」是没命中还是没体检)。
                    0 if getattr(it, "has_data", True) else 1,
                    now,
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
    嵌 `k4Advisory[]` + `scenarioReviewPending` + 读**定格判向**(`time_exit_locked_state`,
    审计 🔴-1:请求期绝不用实时价重判时间退出)。无快照的持仓(刚开仓未体检)不在返回集,
    调用方回退空数组 + 定格 None。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT position_id, trade_date, d_count, net_float, time_exit_state, "
            "max_hold_effective, k4_hits_json, has_strong, scenario_review, "
            "time_exit_locked_state, time_exit_locked_date, time_exit_locked_net_float, "
            "data_unavailable FROM holding_eod_check ORDER BY position_id, trade_date"
        ).fetchall()
    out: Dict[int, Dict[str, Any]] = {}
    for r in rows:  # 按 trade_date 升序遍历,同 position 后者覆盖 → 最终留最大 trade_date 那份
        out[int(r[0])] = {
            "position_id": int(r[0]), "trade_date": r[1], "d_count": r[2],
            "net_float": r[3], "time_exit_state": r[4], "max_hold_effective": r[5],
            "hits": _parse_hits(r[6]), "has_strong": bool(r[7]), "scenario_review": bool(r[8]),
            "time_exit_locked_state": r[9], "time_exit_locked_date": r[10],
            "time_exit_locked_net_float": r[11],
            # v1.4-①-B:`None` = 老快照未记录这一位(**不是** False)——「不知道」与
            # 「体检过了」不可混同,调用方按 None 透出 null,不猜。
            "data_unavailable": (None if r[12] is None else bool(r[12])),
        }
    return out


def locked_time_exit_map(db_path: Optional[Path] = None) -> Dict[int, Dict[str, Any]]:
    """每个 position_id 的**定格判向**记录(审计 🔴-1「D5 判一次定格」的读侧单一通道)。

    取该持仓**最早**一条带非空 `time_exit_locked_state` 的行(= 定格发生那天;此后各日的行
    只是把它原样带过来,取最早者即取到判向的源头,`time_exit_locked_date` 亦是那天)。
    返回 `{position_id: {"state","date","net_float"}}`;从未定格的持仓不在返回集。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT position_id, time_exit_locked_state, time_exit_locked_date, "
            "time_exit_locked_net_float FROM holding_eod_check "
            "WHERE time_exit_locked_state IS NOT NULL ORDER BY position_id, trade_date DESC"
        ).fetchall()
    out: Dict[int, Dict[str, Any]] = {}
    for pid, state, locked_date, nf in rows:  # trade_date 降序 → 后写的(最早那天)最终覆盖
        out[int(pid)] = {"state": state, "date": locked_date, "net_float": nf}
    return out


def time_exit_due_map(db_path: Optional[Path] = None) -> Dict[int, Dict[str, str]]:
    """每个 position_id **最早**被系统判「该走」的那一天(供周复盘时间退出违纪审计,🔵-9)。

    判据 = `holding_eod_check.time_exit_state` 落在 actionable 两态之一
    (`time_exit_next_day` / `hard_cap_exit`)的最早 `trade_date`。**刻意用每日记录的
    `time_exit_state` 而非定格列**:①两档下该列从定格日起就是定格判向,等价;②单档
    现役 K1 根本不定格,但「D5 无条件时间退出」同样是 §2.1 第 2 条纪律,用这一列两档单档
    都覆盖得到,不必写两套。返回 `{position_id: {"kind","decision_date"}}`(未被判过该走
    的持仓不在返回集)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT position_id, trade_date, time_exit_state FROM holding_eod_check "
            "WHERE time_exit_state IN ('time_exit_next_day','hard_cap_exit') "
            "ORDER BY position_id, trade_date DESC"
        ).fetchall()
    out: Dict[int, Dict[str, str]] = {}
    for pid, td, state in rows:  # trade_date 降序 → 最早那天最后写入,最终留最早
        out[int(pid)] = {"kind": state, "decision_date": td}
    return out


def locked_state_provider(db_path: Optional[Path] = None) -> Callable[[Any], Optional[str]]:
    """构造 precall `scan_time_exits(locked_state_provider=...)` 用的 provider(审计 🔴-1)。
    一次性把定格判向读成 dict,provider 按 `position.id` O(1) 查——查无(尚未定格 / 刚开仓
    未体检)→ None,`resolve_time_exit` 保守判 time_exit_next_day(豁免需正向证据)。"""
    locked = locked_time_exit_map(db_path=db_path)
    return lambda pos: (locked.get(getattr(pos, "id", None)) or {}).get("state")


def data_unavailable_provider(db_path: Optional[Path] = None) -> Callable[[Any], bool]:
    """构造 precall `scan_time_exits(data_unavailable_provider=...)` 用的 provider
    (v1.4-①-B / §七 P0-2)。= 该持仓在**最近一份** 16:35 体检里是不是「当日无 EOD 行」。

    **为什么盘前要用它**:9:26 汇总推送会把「D5 该走」推到用户锁屏,而停牌票今天根本卖不掉
    —— 这是 P0-2 病根最尖锐的形态。查无快照(刚开仓未体检)/ 老快照未记这一位(`None`)→
    **返回 False**(保守:维持既有推送行为,豁免需正向证据,同 `locked_state_provider` 姿势)。
    盘前用「昨日 EOD 那一份」是当下能拿到的最好信号(当日 EOD 尚未产生);停牌通常连续,
    误差方向是「复牌当天少推一次」,而不是「催卖一只卖不掉的票」。"""
    snaps = load_latest_checks_by_position(db_path=db_path)
    return lambda pos: bool((snaps.get(getattr(pos, "id", None)) or {}).get("data_unavailable") or False)


def latest_net_float_map(db_path: Optional[Path] = None) -> Dict[int, Optional[float]]:
    """每个 position_id 的**最近一份** net_float。⚠ **审计 🔴-1 之后:纯审计/展示用,
    不再参与任何时间退出判向**(判向只认 `locked_time_exit_map` 的定格值)。NULL(停牌/
    无 EOD 数据当日算不出净浮盈)如实留 None。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT position_id, net_float FROM holding_eod_check ORDER BY position_id, trade_date"
        ).fetchall()
    out: Dict[int, Optional[float]] = {}
    for pid, nf in rows:  # 升序遍历,后者覆盖 → 留最大 trade_date 那份
        out[int(pid)] = (float(nf) if nf is not None else None)
    return out


__all__ = [
    "save_holding_eod_checks",
    "load_latest_checks_by_position",
    "locked_time_exit_map",
    "locked_state_provider",
    "data_unavailable_provider",
    "time_exit_due_map",
    "latest_net_float_map",
]
