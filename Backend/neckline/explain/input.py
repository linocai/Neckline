"""解释层的输入 DTO(V2.5.0 S9,§5.2 边界③ / §5.5)。

🔴 **双盲的结构性保证住在这里**:`ExplainInput` 里**根本没有**通道身份与排序位次
那两类字段 —— 不是「有但不填」。字段集冻结成 `EXPLAIN_INPUT_FIELDS`,
守门单测逐字断言;⛔ 加字段必须先改那个列表。

🔴 **列表顺序也会泄漏位次**:`build_inputs()` 一律按 `ts_code` **升序**返回,
排序是本模块**唯一**的出口(单测断言它确实排了序,且乱序传入也拿到同一个序列)。

⚠ **取数来源是事实包**(冻结件),⛔ 不是 tushare / market_data:
解释层要讲「近期表现」「位置与结构」,读的是 D0 及之前若干个交易日的冻结事实包。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import polars as pl

from neckline.playbook.model import Bar

logger = logging.getLogger(__name__)

#: 交给 LLM 看的日 K 根数。
#:
#: 🔴 **这是一个工程侧的「上下文长度」,⛔ 不是待标定的策略阈值**(§8 的 22 项里没有它):
#: 它不参与任何机械判定 —— 换成 40 或 80 不会让任何一只票被选上或落选,只影响
#: 模型看到多长一段图。⚠ 与 `MAX_LOOKBACK_PACKS`(120,工程容量上限)是两回事,
#: 但必须 ≤ 它。**已如实登记进 PROJECT_PLAN §14,请用户复核**:若认为它属于
#: 「我会想去调的东西」,那它就该搬进参数包(架构 §二 判据)。
KLINE_SESSIONS = 60


@dataclass(frozen=True)
class ExplainInput:
    """交给解释层的一只票。

    🔴 **⛔ 不含** `patterns` / `primary_pattern` / `channel` / `rank` / `score` /
    `tier` / `seat_kind` / `upside_room_mech*` —— 架构 §3.3:
    「解释层收到的输入**不含通道身份与排序位次**」。

    ⚠ 这里面的量全部来自**冻结事实包**,是「今天市场发生了什么」的事实,
    ⛔ 不是策略层的产物;`rel_strength_1d` / `sw_l2_median_ret` 也一样
    (它们是事实层算的行业相对读数,见 §5.3.1 的列表)。
    """

    ts_code: str
    name: Optional[str]
    sw_l2_code: Optional[str]
    sw_l2_name: Optional[str]
    board: Optional[str]
    close: Optional[float]
    prev_close: Optional[float]
    ret_1d: Optional[float]
    amp_1d: Optional[float]
    turnover_rate: Optional[float]
    volume_ratio: Optional[float]
    circ_mv: Optional[float]
    sw_l2_median_ret: Optional[float]
    rel_strength_1d: Optional[float]
    bars: Tuple[Bar, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, object]:
        return {
            "tsCode": self.ts_code, "name": self.name,
            "swL2Code": self.sw_l2_code, "swL2Name": self.sw_l2_name,
            "board": self.board,
            "close": self.close, "prevClose": self.prev_close,
            "ret1d": self.ret_1d, "amp1d": self.amp_1d,
            "turnoverRate": self.turnover_rate, "volumeRatio": self.volume_ratio,
            "circMv": self.circ_mv,
            "swL2MedianRet": self.sw_l2_median_ret,
            "relStrength1d": self.rel_strength_1d,
            "bars": [b.to_dict() for b in self.bars],
        }


#: 🔴 **字段集冻结**(§5.2 边界③ / G5)。
EXPLAIN_INPUT_FIELDS: Tuple[str, ...] = (
    "ts_code", "name", "sw_l2_code", "sw_l2_name", "board",
    "close", "prev_close", "ret_1d", "amp_1d", "turnover_rate", "volume_ratio",
    "circ_mv", "sw_l2_median_ret", "rel_strength_1d", "bars",
)

#: ⛔ 这些词根一个都不许出现在 `ExplainInput` 的字段名里(通道身份 / 排序位次)。
EXPLAIN_INPUT_FORBIDDEN: Tuple[str, ...] = (
    "pattern", "channel", "recall", "rank", "score", "tier", "seat", "upside_room",
)

#: 从事实包里取的列(⚠ 只取这些 —— 列投影是内存红线,§12 坑 1)。
_PACK_COLUMNS: Tuple[str, ...] = (
    "trade_date", "ts_code", "name", "board", "sw_l2_code", "sw_l2_name",
    "open", "high", "low", "close", "pre_close", "vol",
    "ret_1d", "amp_1d", "turnover_rate", "volume_ratio", "circ_mv",
    "sw_l2_median_ret", "rel_strength_1d",
)


def _f(v: object) -> Optional[float]:
    try:
        return None if v is None else float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def build_inputs(
    trade_date: date,
    codes: Sequence[str],
    *,
    sessions: int,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> List[ExplainInput]:
    """从冻结事实包造解释层输入。**返回值按 `ts_code` 升序**(双盲第 ③ 条)。

    `sessions` 是**必填**的:日 K 给几根是调用方的显式决定,⛔ 本函数不替它挑一个
    (调用方一律传 `KLINE_SESSIONS`,那是它唯一的家)。

    ⚠ 某一天的包没冻结 → 那一天**缺一根 K 线**,⛔ 不补一根近似的:
    「那天没冻结」是可以被读出来的事实,由 `bars` 的根数直接看得出。
    """
    from neckline.facts import store as facts_store

    if sessions < 1:
        raise ValueError(f"sessions 必须 >= 1,收到 {sessions}")
    want = sorted(dict.fromkeys(c for c in codes if c))
    if not want:
        return []

    frames: List[pl.DataFrame] = []
    day = trade_date
    seen = 0
    # 往回找 `sessions` 个**已冻结**的包。⚠ 上界防呆:日历里的非交易日会被
    # `load_pack` 直接判「没冻结」,所以这里按自然日往回走,最多走 3 倍。
    for _ in range(sessions * 3):
        if seen >= sessions:
            break
        try:
            pack = facts_store.load_pack(day, parquet_dir=parquet_dir, db_path=db_path)
        except Exception:  # noqa: BLE001 —— 那天没冻结 = 少一根 K 线,不是崩溃
            day -= timedelta(days=1)
            continue
        try:
            rows = pack.rows.select(list(_PACK_COLUMNS)).filter(
                pl.col("ts_code").is_in(want))
        except Exception:  # noqa: BLE001 —— parquet 被保留策略裁剪了
            logger.warning("[explain] %s 的事实包 parquet 读不到,少一根 K 线", day,
                           exc_info=True)
            day -= timedelta(days=1)
            continue
        frames.append(rows)
        seen += 1
        day -= timedelta(days=1)

    if not frames:
        logger.warning("[explain] %s 起往回一份冻结事实包都读不到,输入为空", trade_date)
        return []
    frame = pl.concat(frames).sort(["ts_code", "trade_date"])

    today_rows = {
        r["ts_code"]: r
        for r in frame.filter(pl.col("trade_date") == trade_date).iter_rows(named=True)
    }
    bars_by_code: Dict[str, List[Bar]] = {c: [] for c in want}
    for r in frame.iter_rows(named=True):
        if r["ts_code"] in bars_by_code:
            bars_by_code[r["ts_code"]].append(Bar(
                trade_date=r["trade_date"].strftime("%Y%m%d"),
                open=_f(r["open"]) or 0.0, high=_f(r["high"]) or 0.0,
                low=_f(r["low"]) or 0.0, close=_f(r["close"]) or 0.0,
                vol=_f(r["vol"]) or 0.0,
            ))

    out: List[ExplainInput] = []
    for code in want:                      # 🔴 升序 —— 位次不从顺序泄漏
        r = today_rows.get(code, {})
        out.append(ExplainInput(
            ts_code=code,
            name=r.get("name"),
            sw_l2_code=r.get("sw_l2_code"), sw_l2_name=r.get("sw_l2_name"),
            board=r.get("board"),
            close=_f(r.get("close")), prev_close=_f(r.get("pre_close")),
            ret_1d=_f(r.get("ret_1d")), amp_1d=_f(r.get("amp_1d")),
            turnover_rate=_f(r.get("turnover_rate")),
            volume_ratio=_f(r.get("volume_ratio")),
            circ_mv=_f(r.get("circ_mv")),
            sw_l2_median_ret=_f(r.get("sw_l2_median_ret")),
            rel_strength_1d=_f(r.get("rel_strength_1d")),
            bars=tuple(bars_by_code.get(code, ())),
        ))
    return out


__all__ = [
    "KLINE_SESSIONS", "ExplainInput", "EXPLAIN_INPUT_FIELDS",
    "EXPLAIN_INPUT_FORBIDDEN", "build_inputs",
]
