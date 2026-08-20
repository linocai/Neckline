"""报告落库(V2.5.0 S7,PROJECT_PLAN §5.10)。

两张表,**一张写、一张只读**:

| 表 | 状态 | 谁写 |
|---|---|---|
| `k9_reports` | **本版的报告表** | 本文件 `save_k9_report()`(唯一写入口) |
| `reports` | K8 时代的旧表,**冻结只读留档**(裁定 6) | **没有人** —— 写路径已随 S7 物理删除 |

🔴 **旧 `reports` 的写函数 `save_report()` 已删除。** 它装满了 K8 的 JSON blob
(`sentiment_json` / `sectors_json` / `basket_daily_json` …),V2.5.0 之后既没有生产
调用方,也不该再长出一个 —— 留着一个「谁都能调回去」的写路径,等于让「只读留档」
这条纪律靠自觉维持。表本身**不 DROP、不迁移、不回填**,历史行照旧可读(`load_report`
/ `load_report_by_str` / `latest_report_date` / `load_llm_judgments`)。

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
LEGACY_TABLE = "reports"


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

    ⚠ **只读**(`readonly_tables`,R3-🔴-2):`k9_reports` 还没建的 v2.4.2 老库
    读出来就是 `None` —— ⛔ 读一次不许把库迁移掉。复审实测的就是这个入口:
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


# ══════════════════════════════════════════════════════════════════════════
# 旧 `reports` 表 —— **只读留档**(裁定 6),⛔ 没有写函数
# ══════════════════════════════════════════════════════════════════════════

def _parse_json_field(raw: Optional[str], default: Any) -> Any:
    """历史行的 `*_json` 列容错解析(NULL / 非法 JSON → 调用方给的 `default`,
    ⛔ 不炸历史回放)。"""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


_LEGACY_COLUMNS = (
    "trade_date, report_date, generated_at, strategy_version, sentiment_json, "
    "sectors_json, candidates_json, markdown, watchlist_json, intel_json, "
    "sector_moneyflow_json, news_alerts_scan_json, data_freshness_json, basket_daily_json"
)


#: 同 `_K9_PROBE`。⚠ 这张 K8 老表的**七列都在 `_COLUMN_MIGRATIONS` 里**
#: (`report_date` / `watchlist_json` / `intel_json` / `sector_moneyflow_json` /
#: `news_alerts_scan_json` / `data_freshness_json` / `basket_daily_json`)——
#: 只探表名不够,老库缺任何一列这条 SELECT 都会炸。
_LEGACY_PROBE: Tuple[str, ...] = tuple(
    f"{LEGACY_TABLE}.{c.strip()}" for c in _LEGACY_COLUMNS.split(","))


def _legacy_row(row) -> Dict[str, Any]:
    return {
        "trade_date": row[0],
        "report_date": row[1] or row[0],
        "generated_at": row[2],
        "strategy_version": row[3],
        "sentiment": _parse_json_field(row[4], {}),
        "sectors": _parse_json_field(row[5], []),
        "candidates": _parse_json_field(row[6], []),
        "markdown": row[7],
        "watchlist": _parse_json_field(row[8], []),
        "intel": _parse_json_field(row[9], {}),
        "sector_moneyflow": _parse_json_field(row[10], {}),
        "news_alerts_scan": _parse_json_field(row[11], []),
        "data_freshness": _parse_json_field(row[12], {}),
        "basket_daily": _parse_json_field(row[13], {}),
    }


def load_report(trade_date: date, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """读一条 **K8 历史**报告行(只读留档)。

    🔴 R3-🔴-2:原先这里(经 `load_report_by_str`)会 `init_schema` —— 复审拿它
    实测过「老库 59 表 → 75 表」。现在走 `readonly_tables`,⛔ 一次读不再迁移任何库。"""
    return load_report_by_str(_d(trade_date), db_path)


def load_report_by_str(
    trade_date_str: str, db_path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    # ⚠ **逐列探**(见 `_LEGACY_PROBE`):老库缺列 = legacy → `None`,
    # ⛔ 不是当场 `ALTER TABLE` 把它迁移掉。
    with readonly_tables(*_LEGACY_PROBE, db_path=db_path) as conn:
        if conn is None:
            return None
        row = conn.execute(
            f"SELECT {_LEGACY_COLUMNS} FROM {LEGACY_TABLE} WHERE trade_date=?",
            (trade_date_str,),
        ).fetchone()
    return None if row is None else _legacy_row(row)


def latest_report_date(db_path: Optional[Path] = None) -> Optional[str]:
    """最新一条 K8 历史报告的 `trade_date`('YYYYMMDD')。⚠ **只读**(R3-🔴-2)。"""
    with readonly_tables(LEGACY_TABLE, db_path=db_path) as conn:
        if conn is None:
            return None
        row = conn.execute(f"SELECT MAX(trade_date) FROM {LEGACY_TABLE}").fetchone()
    return row[0] if row and row[0] else None


def load_llm_judgments(
    trade_date: date, db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """⚠ **V2-⑬-2 起本表停写留档**:写函数早已物理删除,本函数只服务历史归因只读。

    ⚠ **只读**(R3-🔴-2)。连 `search_engine` 一起探 —— 它是 `_COLUMN_MIGRATIONS`
    里的补列,老库缺它 = legacy → 空列表。"""
    with readonly_tables("llm_judgments.search_engine", db_path=db_path) as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT ts_code, provider, model, verdict, narrative, degraded, degrade_reason, "
            "search_hits_json, search_engine, created_at "
            "FROM llm_judgments WHERE trade_date=? ORDER BY id",
            (_d(trade_date),),
        ).fetchall()
    return [
        {
            "ts_code": r[0], "provider": r[1], "model": r[2], "verdict": r[3],
            "narrative": r[4], "degraded": bool(r[5]), "degrade_reason": r[6],
            "search_hits": _parse_json_field(r[7], []), "search_engine": r[8],
            "created_at": r[9],
        }
        for r in rows
    ]


__all__ = [
    "K9_TABLE", "LEGACY_TABLE",
    "save_k9_report", "load_k9_report", "load_k9_report_index", "latest_k9_report",
    "load_report", "load_report_by_str", "latest_report_date", "load_llm_judgments",
]
