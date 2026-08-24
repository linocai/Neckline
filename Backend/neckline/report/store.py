"""K9 报告落库(V2.5.0 S7,PROJECT_PLAN §5.10)。

🔴 **双日期契约⛔ 不许退化**(LRN-20260816-001,§12 坑 9):
`report_date` 管标题 / 推送 / 可见身份;`trade_date` 管 EOD 读数 / 清单 / 预案 /
审计键。周日报告 `report_date=周日`、`trade_date=紧邻上一周五`。
本文件的写入口**两个日期都是必填关键字**,⛔ 不给任何一个默认值去猜。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from neckline.db import connection, init_schema, readonly_tables

logger = logging.getLogger(__name__)

K9_TABLE = "k9_reports"


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ══════════════════════════════════════════════════════════════════════════
# k9_reports —— 唯一写入口
# ══════════════════════════════════════════════════════════════════════════

def save_k9_report(
    *,
    trade_date: date,
    report_date: date,
    state: str,
    headline: str,
    gaps: List[str],
    markdown: str,
    structured: Dict[str, Any],
    strategy: str,
    params_package_version: Optional[str],
    pack_id: Optional[str],
    pack_version: Optional[str],
    listing_size: Optional[int],
    strict_count: Optional[int],
    relaxed_count: Optional[int],
    db_path: Optional[Path] = None,
) -> None:
    """落一份报告(同 `trade_date` 幂等重写)。

    ⚠ **`listing_size=None` 与 `0` 不可互换**:`None` = 「今天没跑成」(清单根本
    没算出来),`0` = 「今天没有」(跑通了、结果为空、可以被信任)。裁定 5。
    ⚠ 两个日期都是**必填关键字** —— 双日期契约⛔ 不许靠默认值去猜(§12 坑 9)。
    """
    init_schema(db_path)
    with connection(db_path) as conn:
        conn.execute(
            f"INSERT INTO {K9_TABLE} "
            "(trade_date, report_date, state, headline, gaps_json, markdown, "
            " structured_json, strategy, params_package_version, pack_id, pack_version, "
            " listing_size, strict_count, relaxed_count, generated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(trade_date) DO UPDATE SET "
            "report_date=excluded.report_date, state=excluded.state, "
            "headline=excluded.headline, gaps_json=excluded.gaps_json, "
            "markdown=excluded.markdown, structured_json=excluded.structured_json, "
            "strategy=excluded.strategy, "
            "params_package_version=excluded.params_package_version, "
            "pack_id=excluded.pack_id, pack_version=excluded.pack_version, "
            "listing_size=excluded.listing_size, strict_count=excluded.strict_count, "
            "relaxed_count=excluded.relaxed_count, generated_at=excluded.generated_at",
            (
                _d(trade_date), _d(report_date), state, headline,
                json.dumps(list(gaps), ensure_ascii=False), markdown,
                json.dumps(structured, ensure_ascii=False, sort_keys=True),
                strategy, params_package_version, pack_id, pack_version,
                listing_size, strict_count, relaxed_count, _now(),
            ),
        )


_K9_COLUMNS = (
    "trade_date, report_date, state, headline, gaps_json, markdown, structured_json, "
    "strategy, params_package_version, pack_id, pack_version, listing_size, "
    "strict_count, relaxed_count, generated_at"
)


#: 🔴 R3-🔴-2:读路径的**表 / 列存在性探针**,逐列由 `_K9_COLUMNS` 自动派生 ——
#: 加一列而忘了加探针在结构上不可能发生。缺表 / 缺列 = 未迁移的老库 → 文档化空态。
_K9_PROBE: Tuple[str, ...] = tuple(
    f"{K9_TABLE}.{c.strip()}" for c in _K9_COLUMNS.split(","))


def _k9_row(row) -> Dict[str, Any]:
    return {
        "trade_date": row[0],
        "report_date": row[1],
        "state": row[2],
        "headline": row[3],
        "gaps": json.loads(row[4]),
        "markdown": row[5],
        "structured": json.loads(row[6]),
        "strategy": row[7],
        "params_package_version": row[8],
        "pack_id": row[9],
        "pack_version": row[10],
        "listing_size": row[11],
        "strict_count": row[12],
        "relaxed_count": row[13],
        "generated_at": row[14],
    }


def load_k9_report(
    trade_date: date, *, db_path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """查某交易日的报告。`None` = 那天没生成过(完全正常的场景)。

    ⚠ **只读**(`readonly_tables`):`k9_reports` 还没建时读出来就是 `None`，
    读一次不许把库迁移掉。
    老库 59 表,调一次本函数 → 75 表。"""
    with readonly_tables(*_K9_PROBE, db_path=db_path) as conn:
        if conn is None:
            return None
        row = conn.execute(
            f"SELECT {_K9_COLUMNS} FROM {K9_TABLE} WHERE trade_date=?",
            (_d(trade_date),),
        ).fetchone()
    return None if row is None else _k9_row(row)


def load_k9_report_index(
    start: date, end: date, *, db_path: Optional[Path] = None
) -> Dict[str, Dict[str, Any]]:
    """`[start, end]` 区间内每个交易日的报告**索引行**:`trade_date → {report_date,
    state, headline, listing_size, params_package_version, pack_version, generated_at}`。

    🔴 **刻意不带 `markdown` / `structured_json`**:S11 的复盘装订要「当时那几天的报告
    快照」,窗口动辄 40 天;把 40 份 markdown 全塞进一个响应体是几百 KB 的无用负担,
    而首行(`state` + `headline`)已经说清了那天系统在说什么。要全文按日走
    `load_k9_report(trade_date)` 点查。

    ⚠ 键不出现 = **那天没生成过报告**(⛔ 不是「那天没有清单」——后者是 `state='empty'`
    的一行,它在这里是**存在**的)。调用方必须把两者分开说。
    """
    if start > end:
        return {}
    with readonly_tables(*_K9_PROBE, db_path=db_path) as conn:   # 只读,R3-🔴-2
        if conn is None:
            return {}
        rows = conn.execute(
            f"SELECT trade_date, report_date, state, headline, listing_size, "
            f"params_package_version, pack_version, generated_at FROM {K9_TABLE} "
            f"WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
            (_d(start), _d(end)),
        ).fetchall()
    return {
        r[0]: {
            "trade_date": r[0], "report_date": r[1], "state": r[2], "headline": r[3],
            "listing_size": r[4], "params_package_version": r[5],
            "pack_version": r[6], "generated_at": r[7],
        }
        for r in rows
    }


def latest_k9_report(*, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """最新一份报告(按 `trade_date` 降序)。⚠ **只读**(R3-🔴-2):表还没建 → `None`。"""
    with readonly_tables(*_K9_PROBE, db_path=db_path) as conn:
        if conn is None:
            return None
        row = conn.execute(
            f"SELECT {_K9_COLUMNS} FROM {K9_TABLE} ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
    return None if row is None else _k9_row(row)


__all__ = [
    "K9_TABLE",
    "save_k9_report", "load_k9_report", "load_k9_report_index", "latest_k9_report",
]
