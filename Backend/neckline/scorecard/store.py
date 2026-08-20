"""覆盖率成绩线的落库与读取(PROJECT_PLAN §5.8.1)。

两张表:
    · `k9_coverage_daily`   —— 一天一行(涨停普查 + 两个覆盖率 + 普查 JSON)
    · `k9_coverage_misses`  —— 每只**没被覆盖**的涨停票一行 + 归因

🔴 **NULL 不是 0**:`coverage_all` NULL = 昨天还没有清单;`coverage_in_pool` NULL =
没有 D−1 disposition(边界参数缺失)。落表与读回都必须原样保住这个区别 —— 单测
逐条锁死(§5.8.1 逐字要求)。

⛔ **本文件不 import `neckline.k9`**(守门单测扫描):覆盖率是独立于策略的尺子。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from neckline.db import connection, init_schema, readonly_tables
from neckline.scorecard.coverage import CoverageDay, Miss

logger = logging.getLogger(__name__)

DAILY_TABLE = "k9_coverage_daily"
MISS_TABLE = "k9_coverage_misses"

_DAILY_COLUMNS = (
    "trade_date, pack_id, pack_version, limit_up_count, limit_down_count, zaban_count, "
    "zaban_rate, max_consec_days, cluster_count, listing_trade_date, listing_size, "
    "covered_count, coverage_all, in_pool_denominator, covered_in_pool, coverage_in_pool, "
    "census_json, computed_at"
)
_MISS_COLUMNS = (
    "trade_date, ts_code, name, sw_l2_code, sw_l2_name, board, consec_limit_up_days, "
    "reason, detail, computed_at"
)


def _d(d: Optional[date]) -> Optional[str]:
    return None if d is None else d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_coverage_day(day: CoverageDay, *, db_path: Optional[Path] = None) -> None:
    """落一天(幂等重写)。

    ⚠ 与 `fact_packs` 的「⛔ 不许覆盖」纪律**刻意不同**:那张是审计物;本表是
    **可重算的派生结果** —— 昨天的清单在解释层补位后定稿、S6 的 disposition 补上来,
    同一天都要重算一遍才能把 `coverage_all` 从 NULL 接上。`INSERT OR REPLACE` 是
    幂等而不是改写:同一份输入必然算出同一行。
    """
    init_schema(db_path)
    payload = (
        _d(day.trade_date), day.pack_id, day.pack_version,
        day.limit_up_count, day.limit_down_count, day.zaban_count,
        day.zaban_rate, day.max_consec_days, day.cluster_count,
        _d(day.listing_trade_date), day.listing_size,
        day.covered_count, day.coverage_all,
        day.in_pool_denominator, day.covered_in_pool, day.coverage_in_pool,
        json.dumps(day.census, ensure_ascii=False, sort_keys=True), _now(),
    )
    now = _now()
    misses = [
        (_d(day.trade_date), m.ts_code, m.name, m.sw_l2_code, m.sw_l2_name, m.board,
         m.consec_limit_up_days, m.reason, m.detail, now)
        for m in day.misses
    ]
    with connection(db_path) as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO {DAILY_TABLE} ({_DAILY_COLUMNS}) "
            f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", payload)
        # 重算时先清掉旧归因:昨天的清单定稿后,原先标 `no_listing` 的行要整批换掉,
        # ⛔ 不能让新旧两代归因混在同一天里(那会让「漏掉的是哪一类票」变成一笔糊涂账)。
        conn.execute(f"DELETE FROM {MISS_TABLE} WHERE trade_date=?", (_d(day.trade_date),))
        if misses:
            conn.executemany(
                f"INSERT INTO {MISS_TABLE} ({_MISS_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?)",
                misses)


def load_coverage_days(
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
) -> List[dict]:
    """读区间的覆盖率行(降序,最近的在前)。⛔ 不把 NULL 折成 0。

    ⛔ 读函数不执行 DDL(§7.1):表还没建 → 空列表(成绩线还没跑过)。"""
    sql = f"SELECT {_DAILY_COLUMNS} FROM {DAILY_TABLE}"
    where, args = [], []
    if start is not None:
        where.append("trade_date>=?")
        args.append(_d(start))
    if end is not None:
        where.append("trade_date<=?")
        args.append(_d(end))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY trade_date DESC"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(int(limit))
    keys = [c.strip() for c in _DAILY_COLUMNS.split(",")]
    with readonly_tables(DAILY_TABLE, db_path=db_path) as conn:
        rows = [] if conn is None else conn.execute(sql, tuple(args)).fetchall()
    out = []
    for r in rows:
        rec = dict(zip(keys, r))
        rec["census_json"] = json.loads(rec["census_json"])
        out.append(rec)
    return out


def load_misses(
    trade_date: date, *, db_path: Optional[Path] = None
) -> List[dict]:
    """某日的漏检行。⛔ 读函数不执行 DDL(§7.1):表还没建 → 空列表。"""
    keys = [c.strip() for c in _MISS_COLUMNS.split(",")]
    with readonly_tables(MISS_TABLE, db_path=db_path) as conn:
        rows = [] if conn is None else conn.execute(
            f"SELECT {_MISS_COLUMNS} FROM {MISS_TABLE} WHERE trade_date=? ORDER BY ts_code",
            (_d(trade_date),),
        ).fetchall()
    return [dict(zip(keys, r)) for r in rows]


def miss_reason_counts(
    *, start: Optional[date] = None, end: Optional[date] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """区间内漏检归因的分布 `{reason: 只数}` ——「漏掉的是哪一类票」的一眼答案。

    ⚠ 名字不带 `load_` 前缀,静态守门扫不到它 —— 但它是纯读,同样归 §7.1 政策。"""
    sql = f"SELECT reason, COUNT(*) FROM {MISS_TABLE}"
    where, args = [], []
    if start is not None:
        where.append("trade_date>=?")
        args.append(_d(start))
    if end is not None:
        where.append("trade_date<=?")
        args.append(_d(end))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY reason ORDER BY COUNT(*) DESC"
    with readonly_tables(MISS_TABLE, db_path=db_path) as conn:
        if conn is None:
            return {}
        return {r[0]: int(r[1]) for r in conn.execute(sql, tuple(args)).fetchall()}


__all__ = [
    "DAILY_TABLE",
    "MISS_TABLE",
    "save_coverage_day",
    "load_coverage_days",
    "load_misses",
    "miss_reason_counts",
]
