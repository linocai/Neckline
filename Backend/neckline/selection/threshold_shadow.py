"""阈值影子台账的**唯一写入实现**(plan §五 V2.3.2-①-D;策略线裁定 5 的落点)。

**回答的问题**:那些**未经用户确认**、已于 V2.3.2 退出机械硬否决的市场关 / 板块关
阈值,「若仍按硬门跑,今天本可通过 / 本可否决多少候选」——这是裁定 6「恢复硬否决的
七项提交」里第五、第六项(单关通过率 / 联合通过率)唯一的数据来源。

🔴 **只增不改、只读不写回**(裁定 5 逐字):本模块零 `UPDATE` / 零 `DELETE` / 零
`INSERT OR REPLACE`(`tests/test_v2_schema_guard.py::_APPEND_ONLY_TABLES` 守门),
且**绝不回写** `baskets` / `tier_history` / `basket_cards` / `selection_clock` ——
历史影子结果不得改动当时的正式选股结论。

🔴 **两个「影子」⛔ 不许混名**(plan 〇b 红线 5):
  · **阈值影子**(本模块)= 「这条待定阈值若按硬门跑,本可通过 / 本可否决」,
    单位 = **候选 × 阈值键**,表 `threshold_shadow_evals`;
  · **OUT 研究影子对照**(`review/out_shadow.py`)= 「被判 OUT 的票 D1 实际走成什么样」,
    单位 = **OUT 票 × D1**,表 `out_shadow_daily`。
两者不共表、不共命名前缀、不共产物段。

**写入时机**:③ 关口跑完、⑥ 定档落库**之后**(要 `final_tier` —— 裁定 4 的第五项
「对最终 T1/T2 数量的影响」靠它)。整段由调用方**独立 try/except**,失败只 WARNING
—— 旁路件不许掀翻晚间链。

**读数从哪来**:`gates.BasketGateSummary.threshold_readings`(由
`gates.collect_threshold_readings()` **与关口判定解耦**地算出)。⛔ 本模块不自己算读数、
不自己判 `enforcement` —— 那两件事的唯一实现都在 `gates.py`。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from neckline.db import connection, init_schema

logger = logging.getLogger(__name__)

TABLE = "threshold_shadow_evals"

_COLUMNS = (
    "trade_date, candidate_key, engine_code, engine_version, skeleton_version, "
    "threshold_key, reading, threshold_value, would_pass, unavailable_reason, "
    "llm_verdict, regime, final_tier, created_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(d: date | str) -> str:
    return d if isinstance(d, str) else d.strftime("%Y%m%d")


def _llm_verdict_of(summary: Any, gate: str) -> Optional[str]:
    """该关 evidence 半边的 LLM 三值(没走到 / 没给 → `None`)。

    ⛔ 只从 `gate_evaluations` 那份留痕(`GateCheck.evidence`)里取,**不重新解析一遍
    模型输出** —— 影子行与关口行必须讲同一句话。"""
    for c in getattr(summary, "checks", ()) or ():
        if getattr(c, "gate", None) != gate:
            continue
        v = (getattr(c, "evidence", None) or {}).get(f"{gate}_verdict")
        if v:
            return str(v)
    return None


def save_threshold_shadow(
    outcome: Any,
    *,
    tier_by_candidate: Optional[Mapping[str, Optional[int]]] = None,
    regime: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    """把一天全部候选的 evidence 阈值拟判写进 `threshold_shadow_evals`。返回写入行数。

    `outcome`:`gates.GateDayOutcome`(鸭子类型;只读 `trade_date` / `summaries`)。
    `tier_by_candidate`:`basket_key → 最终档位`(`1`/`2`);**不在这张表里的候选 =
    OUT / 未定档 → `final_tier` 落 NULL**。⛔ 别用 `0` 表示 OUT —— 那会和"档位 0"
    混掉,而 NULL 的含义在 SQL 里是明确的。
    `regime`:D0 行情状态;缺行传 `None`(⛔ 不填默认态)。

    🔴 **对「全部进关候选」写行**,含被硬门拒掉的那些 —— 那正是裁定 3 写死的分母
    (「进入市场关、板块关**之前**的召回候选或篮子」)。⛔ 不许因为前一道硬门先拒
    就跳过后面的读数。
    """
    trade_date = _d(getattr(outcome, "trade_date", "") or "")
    summaries: Mapping[str, Any] = getattr(outcome, "summaries", {}) or {}
    tiers = dict(tier_by_candidate or {})
    now = _now()

    rows: List[tuple] = []
    for key in sorted(summaries):
        s = summaries[key]
        readings: Sequence[Any] = getattr(s, "threshold_readings", ()) or ()
        if not readings:
            continue
        final_tier = tiers.get(key)
        for r in readings:
            gate = getattr(r, "gate", "")
            would = getattr(r, "would_pass", None)
            rows.append((
                trade_date, key,
                getattr(s, "engine_code", None), getattr(s, "engine_version", None),
                getattr(s, "skeleton_version", None),
                getattr(r, "threshold_key", ""),
                getattr(r, "reading", None), getattr(r, "threshold_value", None),
                None if would is None else int(bool(would)),
                getattr(r, "unavailable_reason", "") or "",
                _llm_verdict_of(s, gate), regime,
                None if final_tier is None else int(final_tier),
                now,
            ))
    if not rows:
        return 0
    init_schema(db_path)
    with connection(db_path) as conn:
        conn.executemany(
            f"INSERT INTO {TABLE} ({_COLUMNS}) "
            f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows,
        )
    return len(rows)


def load_threshold_shadow(
    date_from: date | str, date_to: date | str, *,
    threshold_key: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """读一个闭区间内的影子行(升序 id = 写入序)。①-E 的通过率报告吃它。"""
    lo, hi = _d(date_from), _d(date_to)
    sql = f"SELECT id, {_COLUMNS} FROM {TABLE} WHERE trade_date>=? AND trade_date<=?"
    args: List[Any] = [lo, hi]
    if threshold_key:
        sql += " AND threshold_key=?"
        args.append(threshold_key)
    sql += " ORDER BY id ASC"
    init_schema(db_path)
    with connection(db_path) as conn:
        raw = conn.execute(sql, tuple(args)).fetchall()
    keys = ["id"] + [c.strip() for c in _COLUMNS.split(",")]
    return [dict(zip(keys, r)) for r in raw]


__all__ = ["TABLE", "save_threshold_shadow", "load_threshold_shadow"]
