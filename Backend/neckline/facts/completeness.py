"""事实包完整性判定 ——「今天没跑成」的第一个来源(PROJECT_PLAN §5.3.3)。

架构 §3.5 定死:**数据未到齐 → 事实包不冻结,报告标「今天没跑成」并说明缺口**。
本模块只负责把「缺口」变成一份**逐条可读的清单**;要不要冻结由 `facts/pack.py` 的
返回类型(`CompletePack | IncompletePack`)在类型上决定,⛔ 不靠谁记得检查布尔标志。

**两类上游分区,检查强度刻意不同**:

| 类 | 表 | 判据 | 为什么 |
|---|---|---|---|
| 稠密 | `daily` `daily_basic` `adj_factor` `moneyflow_dc` | 分区存在**且非空** | 全市场每天都该有几千行;0 行 = 那天没拉到 |
| 稀疏 | `limit_derived` `suspend_d` | 分区**存在**即可 | 它们本来就可能合法地只有几行甚至 0 行 —— `limit_derived` 是只存「有信号」的稀疏表,`suspend_d` 实测 20260724 只有 5 只。给稀疏表设行数下限 = 把一个平静的日子判成故障 |

⚠ **§5.3.3 原文写的是「行数在合理区间」,但 Plan 全文没有给出任何区间的上下界。**
⛔ 本片不自行发明每张表的行数下限(那是凭空造一个数);实现为「稠密表非空 / 稀疏表
存在」,并把**每个上游分区的实际行数**如实记进 `fact_packs.sources_json`——
区间真要定的那天,账在表里现成,不必回头考古。已登记进 PROJECT_PLAN §14。

⚠ **同理:§5.3.3 的「`sw_industry_member` 刷新时间不落后于 N 天」里的 N,Plan 未给值。**
本片**不发明 N**,改用两条不含阈值的判据:
    ① 硬闸 = 成分表非空(空 = 判据输入缺失,直接「今天没跑成」);
    ② 事实记账 = 当日 `daily` 里**查无申万归属**的票数记进 `market_json.swCoverage`,
       > 0 打 WARNING。归属快照真陈旧(新股没进分类表)会从这个数上直接看出来,
       而不需要先猜一个 N。这与 §5.3.4「停牌:断言而不是假设」是同一种处置。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

import polars as pl

from neckline.data.market_data import day_file_path
from neckline.db import readonly_tables

logger = logging.getLogger(__name__)

#: 稠密上游表:分区必须存在**且非空**。
DENSE_TABLES: Tuple[str, ...] = ("daily", "daily_basic", "adj_factor", "moneyflow_dc")
#: 稀疏上游表:分区存在即可(0 行是合法的当日事实,见模块 docstring)。
SPARSE_TABLES: Tuple[str, ...] = ("limit_derived", "suspend_d")
#: 元数据表:非空即可。
REQUIRED_META_TABLES: Tuple[str, ...] = ("stock_basic", "sw_industry_member")


@dataclass(frozen=True)
class Gap:
    """一条缺口。`item` 是**表名 / 判据名**(供机器分类),`detail` 是给人读的一句话。"""

    item: str
    detail: str

    def __str__(self) -> str:  # 报告里逐条列出来的那一行
        return f"{self.item}:{self.detail}"


@dataclass(frozen=True)
class SourceRecord:
    """一个上游分区的取数证据(进 `fact_packs.sources_json`)。"""

    name: str
    path: Optional[str]
    rows: Optional[int]
    mtime: Optional[str]
    metadata: Optional[Mapping[str, Any]] = None

    def to_dict(self) -> dict:
        value = {"name": self.name, "path": self.path, "rows": self.rows, "mtime": self.mtime}
        if self.metadata is not None:
            value["metadata"] = dict(self.metadata)
        return value


@dataclass(frozen=True)
class Completeness:
    trade_date: date
    gaps: Tuple[Gap, ...]
    sources: Tuple[SourceRecord, ...]

    @property
    def ok(self) -> bool:
        return not self.gaps

    def missing(self) -> List[str]:
        """给 `IncompletePack.missing` 用的逐条字符串(⛔ 不是一句笼统的「数据未到齐」)。"""
        return [str(g) for g in self.gaps]


def _partition_probe(
    table: str, trade_date: date, parquet_dir: Optional[Path]
) -> Tuple[Optional[Path], Optional[int], Optional[str]]:
    """探一个日分区:`(path, rows, mtime)`。不存在 → `(path, None, None)`。

    🛑 只读**当日那一个文件**(§12 坑 1:⛔ 不用 `get_market_slice` / `scan_table_range`
    —— 它们走 `year=*/**.parquet` 全 glob,会打开 1500+ 个 parquet footer)。"""
    path = day_file_path(table, trade_date, parquet_dir)
    if not path.exists():
        return path, None, None
    try:
        rows = int(pl.read_parquet(path).height)
    except Exception:  # noqa: BLE001  文件在但读不出 = 一样是缺口,如实报
        logger.warning("[completeness] %s 分区读取失败:%s", table, path, exc_info=True)
        return path, None, None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(
        timespec="seconds"
    )
    return path, rows, mtime


def _meta_count(table: str, db_path: Optional[Path]) -> Optional[int]:
    where = " WHERE is_current=1" if table == "sw_industry_member" else ""
    with readonly_tables(table, db_path=db_path) as conn:
        if conn is None:
            return None
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}{where}").fetchone()[0])


def _calendar_open(trade_date: date, db_path: Optional[Path]) -> Optional[bool]:
    """只认已落库的官方日历；缺表/缺日期绝不退化为工作日猜测。"""
    with readonly_tables("trade_cal", db_path=db_path) as conn:
        if conn is None:
            return None
        row = conn.execute(
            "SELECT is_open FROM trade_cal WHERE exchange='SSE' AND cal_date=?",
            (trade_date.strftime("%Y%m%d"),),
        ).fetchone()
    return None if row is None else bool(row[0])


def check(
    trade_date: date,
    *,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    require_current_sw: bool = True,
) -> Completeness:
    """逐项检查当日必备输入,返回缺口清单 + 取数证据。**不抛异常**:任何一项不满足
    都变成一条 `Gap`,由调用方决定「今天没跑成」怎么说。"""
    gaps: List[Gap] = []
    sources: List[SourceRecord] = []

    calendar_open = _calendar_open(trade_date, db_path)
    if calendar_open is None:
        gaps.append(Gap("trade_cal", f"{trade_date} 不在已落库的官方交易日历中"))
    elif not calendar_open:
        gaps.append(Gap("trade_cal", f"{trade_date} 不是交易日"))

    for table in DENSE_TABLES:
        path, rows, mtime = _partition_probe(table, trade_date, parquet_dir)
        sources.append(SourceRecord(table, str(path), rows, mtime))
        if rows is None:
            gaps.append(Gap(table, f"当日分区不存在或读不出({path})"))
        elif rows == 0:
            gaps.append(Gap(table, "当日分区 0 行(全市场稠密表不可能为空 = 那天没拉到)"))

    for table in SPARSE_TABLES:
        path, rows, mtime = _partition_probe(table, trade_date, parquet_dir)
        sources.append(SourceRecord(table, str(path), rows, mtime))
        if rows is None:
            gaps.append(Gap(table, f"当日分区不存在或读不出({path})"))

    required_meta = REQUIRED_META_TABLES if require_current_sw else tuple(
        table for table in REQUIRED_META_TABLES if table != "sw_industry_member")
    for table in required_meta:
        n = _meta_count(table, db_path)
        sources.append(SourceRecord(table, None, n, None))
        if n is None:
            gaps.append(Gap(table, "元数据表不存在(数据库未迁移)"))
        elif n == 0:
            gaps.append(Gap(table, "元数据表为空(判据输入缺失)"))

    return Completeness(trade_date=trade_date, gaps=tuple(gaps), sources=tuple(sources))


__all__ = [
    "DENSE_TABLES",
    "SPARSE_TABLES",
    "REQUIRED_META_TABLES",
    "Gap",
    "SourceRecord",
    "Completeness",
    "check",
]
