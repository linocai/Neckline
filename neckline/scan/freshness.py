"""市场扫描层新鲜度(plan §五 V2-④「保险丝」节:`dataFreshness` 新增
`scanLayerDate`/`scanLayerLagDays`/`scanLayerStale` 三键,**与既有板块三键、
行业强度三键不合并**)。

**本文件只提供计算逻辑,不接线**:把这三键真正塞进 `ReportOut.dataFreshness`
是 `report/pipeline.py` 的事(消费三张扫描层事实表产出报告候选/篮子的那个
未来块的职责范围),本块(V2-④)只负责把"扫描层新鲜到什么程度"这件事算清楚、
函数写好,不改 `pipeline.py`(block 文件范围明确写着新造 `neckline/scan/`,
不含 `report/pipeline.py`)。

**为什么以 `corr_matrix_daily` 的 `MAX(trade_date)` 作新鲜度锚点,不是三张表
分别披露一次(那样是九个键,不是三个)**:`limit_cluster_daily` /
`leader_structure_daily` 在"今天没有涨停共振"的正常交易日会**合法地零行**
(§五 V2-④ 原文"当日无篮子是合法输出"),拿它们的 `MAX(trade_date)` 当"扫描层
跑没跑"的锚点会把"今天很安静"误判成"今天没跑"。`corr_matrix_daily` 的**概念
成分对**分支只要 `daily` 当天有数据、且存在至少一个通过板块池卫生线的概念
(现实里 ~394 个概念几乎不可能全部退化到不足 2 只成员),就会产出行——它是
三张表里"只要批算真的跑过,今天就该有行"这个信号最可靠的一张,借用它的口径
作代理不等于宣称"corr 表本身多重要"。

**零容忍(同 `industry_strength_status` 既定口径,不是新发明)**:`lag_days>0`
即 `stale=True`,不给缓冲——16:05 批算当天就该产出当天的行,不像 `ths_daily`
结构性落后 1 天需要专门容忍窗口。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional

from neckline.db import connection, init_schema
from neckline.scan.corr import TABLE as CORR_TABLE

# `scanLayerLagDays` 的哨兵值:表内**完全没有**任何行(同
# `industry_strength_store.INDUSTRY_STRENGTH_LAG_UNKNOWN`/
# `sectors.SECTOR_LAG_UNKNOWN` 既定惯例——"没有"与"没看"必须能分开,§3.8)。
SCAN_LAYER_LAG_UNKNOWN = -1


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _parse_d(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


@dataclass
class ScanLayerFreshness:
    latest_date: str    # 'YYYYMMDD';完全无数据 → ""
    lag_days: int
    stale: bool

    @property
    def unavailable(self) -> bool:
        return self.lag_days == SCAN_LAYER_LAG_UNKNOWN

    def to_public_dict(self) -> Dict[str, object]:
        return {
            "scanLayerDate": self.latest_date or None,
            "scanLayerLagDays": self.lag_days,
            "scanLayerStale": self.stale,
        }

    def note(self) -> str:
        """脚注文案(单一源,预留给未来接线方——同 `IndustryStrengthFreshness.note`/
        `SectorDataFreshness.note` 句式,新鲜时留空)。"""
        if self.unavailable:
            return "市场扫描层数据未就绪(三张预计算表均无任何数据)——今日驱动种子/篮子不可得。"
        if self.lag_days > 0:
            return f"市场扫描层数据未就绪(最新至 {self.latest_date},落后 {self.lag_days} 个交易日)——今日驱动种子/篮子不可得。"
        return ""

    def latest_label(self) -> str:
        return self.latest_date or "无数据"


def scan_layer_status(report_date: date, *, db_path: Optional[Path] = None) -> ScanLayerFreshness:
    """扫描层新鲜度(见模块 docstring 的锚点选择说明)。"""
    from neckline.calendar import trading_days_between

    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(f"SELECT MAX(trade_date) FROM {CORR_TABLE}").fetchone()
    newest_s = row[0] if row else None
    if not newest_s:
        return ScanLayerFreshness("", SCAN_LAYER_LAG_UNKNOWN, True)
    newest = _parse_d(newest_s)
    if newest >= report_date:
        return ScanLayerFreshness(newest_s, 0, False)
    lag = max(len(trading_days_between(newest, report_date)) - 1, 0)
    return ScanLayerFreshness(newest_s, lag, lag > 0)


__all__ = ["SCAN_LAYER_LAG_UNKNOWN", "ScanLayerFreshness", "scan_layer_status"]
