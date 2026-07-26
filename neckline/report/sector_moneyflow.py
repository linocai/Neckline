"""板块资金流展示(plan §五 v1.3-③-C2)。16:35 报告「情报」节的 C2 部分——把
`moneyflow_dc`(东财口径,个股级,2023-09-11 起)按板块成分聚合(sum net_amount),
排序展示板块层资金净流入 / 净流出榜。

**定位(硬要求,代码与展示文案均须写明,别让后人当选股信号用)**:本模块是**拥挤
情报件,不是选股信号**——STRATEGY_LAB K2 判决「板块层资金/动量有效(能识别当前
热门板块)但无次日领先性(不能预测明天谁涨)」,故本模块只做"今天钱在往哪个板块
流"的展示,**不参与任何评分 / 候选筛选**,`report/candidates.py` 的评分与 entry
mask 不读本模块任何输出。

**证据强度(§硬要求①)**:`net_amount` 本身是 moneyflow_dc 的 EOD 硬数据(强);但
"这只股票属于哪个板块"依赖同花顺概念板块成分(`ths_member`,当前快照、无日期
字段,K2「成分洞」)——**板块归属这一步是弱证据**,故每条 `SectorMoneyflowItem`
标 `evidenceStrength="constituent"`(与 `holding_k4_check.K4AdvisoryOut`/`intel.
ThemeItem` 同一套词表)。

**板块池卫生线(硬要求②)**:聚合前先用 `board_pool.apply_hygiene` 剔除资格/宽基
成分类标签板块(融资融券/深股通/专精特新等,成分动辄上千只,当拥挤度信号用等于
没信号)——与 C1 `intel.py` 复用同一份卫生线常量,不另起一份。

**2023-09 前无数据(硬要求,不臆造)**:`moneyflow_dc` 覆盖仅 2023-09-11 起(见
`research/k3_report.md` B0 节实测记录);早于此日期 / 当日数据缺失,一律返回
`available=False` + 诚实原因,`top_inflow`/`top_outflow` 留空,不猜测、不用 0 填充。

**落盘**:纯读 + 内存聚合,不写任何 Parquet(不违反「落盘一律走 write_table_day」
铁律——因为压根没有新落盘;落库走 `report/store.py` 既有 `reports` 表新增的
`sector_moneyflow_json` 列,同 `watchlist_json` 先例)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline.data.market_data import get_market_slice
from neckline.report.board_pool import apply_hygiene, count_members, invert_member_map
from neckline.report.sectors import load_index_names, load_member_map

# moneyflow_dc 真实覆盖起始日(见 research/k3_report.md B0 节:"moneyflow_dc 仅
# 2023-09-11 起")。仅用于给"当日无数据"一个更诚实具体的原因文案,不参与任何
# 功能性判断——功能上完全靠 `get_market_slice(...).is_empty()` 判断当日有无数据
# (对早于此日期的任何交易日,该表本就没有分区文件,is_empty() 天然为真)。
MONEYFLOW_COVERAGE_START = date(2023, 9, 11)

SECTOR_MONEYFLOW_TOP_N = 15   # 净流入/净流出榜各自展示条数

EVIDENCE_NOTE = (
    "板块层资金净流入用于展示当前资金拥挤度,并非选股信号"
    "(STRATEGY_LAB K2 判决:板块层资金/动量有效但无次日领先性,不进任何评分/候选筛选)。"
    "净流入数值本身(moneyflow_dc)为 EOD 硬数据,但板块归属依赖概念板块成分快照"
    "(ths_member,K2「成分洞」)——归属这一步为弱证据,标 constituent、仅供参考。"
)


@dataclass
class SectorMoneyflowItem:
    index_code: str
    name: str
    net_inflow_wan: float     # 板块成分股 net_amount(万元)加总,东财口径
    member_count: int         # 参与加总的成分股数(当日有 moneyflow_dc 数据的),供读者判断规模偏差
    rank: int                 # 榜内序号(净流入/净流出榜各自独立编号,1 起)
    evidence_strength: str = "constituent"   # 板块归属依赖成分快照,恒弱证据(见模块 docstring)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "code": self.index_code, "name": self.name,
            "netInflowWan": round(self.net_inflow_wan, 1),
            "memberCount": self.member_count, "rank": self.rank,
            "evidenceStrength": self.evidence_strength,
        }


@dataclass
class SectorMoneyflowReport:
    trade_date: date
    available: bool
    unavailable_reason: str = ""
    top_inflow: List[SectorMoneyflowItem] = field(default_factory=list)
    top_outflow: List[SectorMoneyflowItem] = field(default_factory=list)
    excluded_boards_note: str = ""
    evidence_note: str = EVIDENCE_NOTE

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "tradeDate": self.trade_date.isoformat(),
            "available": self.available,
            "unavailableReason": self.unavailable_reason,
            "topInflow": [i.to_public_dict() for i in self.top_inflow],
            "topOutflow": [i.to_public_dict() for i in self.top_outflow],
            "excludedBoardsNote": self.excluded_boards_note,
            "evidenceNote": self.evidence_note,
        }


def empty_sector_moneyflow_report(trade_date: date, reason: str) -> SectorMoneyflowReport:
    """`pipeline.py` 兜底工厂(同 `intel.empty_intel_report` 姿势,外层保险丝)。"""
    return SectorMoneyflowReport(trade_date=trade_date, available=False, unavailable_reason=reason)


def compute_sector_moneyflow(
    trade_date: date,
    *,
    member_map: Optional[Dict[str, List[str]]] = None,
    index_names: Optional[Dict[str, str]] = None,
    parquet_dir: Optional[Path] = None,
    top_n: int = SECTOR_MONEYFLOW_TOP_N,
) -> SectorMoneyflowReport:
    """板块资金流 I/O 入口。`moneyflow_dc` 当日无数据(早于覆盖起始日 / 当日数据
    缺失)→ `available=False`,不臆造。板块池先过卫生线(与 C1 同一份),
    再按板块成分聚合 `net_amount`(万元)排序取 top/bottom N。"""
    mf = get_market_slice(trade_date, table="moneyflow_dc", parquet_dir=parquet_dir)
    if mf.is_empty():
        reason = (
            f"moneyflow_dc 覆盖仅自 {MONEYFLOW_COVERAGE_START.isoformat()} 起,"
            f"该日早于覆盖范围,不臆造。"
            if trade_date < MONEYFLOW_COVERAGE_START else
            "moneyflow_dc 当日无数据(数据管线缺口或非交易日),已留空。"
        )
        return SectorMoneyflowReport(trade_date=trade_date, available=False, unavailable_reason=reason)

    member_map = member_map if member_map is not None else load_member_map(parquet_dir=parquet_dir)
    index_names = index_names if index_names is not None else load_index_names(parquet_dir=parquet_dir)
    counts = count_members(member_map)
    hygiene = apply_hygiene(index_names, counts)
    audit = hygiene.audit_lines()
    excluded_note = ("板块池卫生线已剔除:" + "；".join(audit)) if audit else ""

    inv = invert_member_map(member_map)
    net_by_code = dict(zip(mf["ts_code"].to_list(), mf["net_amount"].to_list()))

    rows: List[tuple] = []   # (index_code, name, total_net, member_count)
    for idx_code in hygiene.kept:
        members = inv.get(idx_code, [])
        vals = [net_by_code[c] for c in members if c in net_by_code]
        if not vals:
            continue
        rows.append((idx_code, index_names.get(idx_code, idx_code), sum(vals), len(vals)))

    if not rows:
        return SectorMoneyflowReport(
            trade_date=trade_date, available=True,
            unavailable_reason="当日无板块的成分股命中 moneyflow_dc(数据对不上,已留空)。",
            excluded_boards_note=excluded_note,
        )

    inflow_sorted = sorted(rows, key=lambda r: r[2], reverse=True)
    outflow_sorted = sorted(rows, key=lambda r: r[2])

    top_inflow = [
        SectorMoneyflowItem(index_code=c, name=n, net_inflow_wan=total, member_count=cnt, rank=i)
        for i, (c, n, total, cnt) in enumerate(inflow_sorted[:top_n], start=1)
    ]
    top_outflow = [
        SectorMoneyflowItem(index_code=c, name=n, net_inflow_wan=total, member_count=cnt, rank=i)
        for i, (c, n, total, cnt) in enumerate(outflow_sorted[:top_n], start=1)
    ]

    return SectorMoneyflowReport(
        trade_date=trade_date, available=True,
        top_inflow=top_inflow, top_outflow=top_outflow,
        excluded_boards_note=excluded_note,
    )


__all__ = [
    "SectorMoneyflowItem",
    "SectorMoneyflowReport",
    "MONEYFLOW_COVERAGE_START",
    "SECTOR_MONEYFLOW_TOP_N",
    "EVIDENCE_NOTE",
    "compute_sector_moneyflow",
    "empty_sector_moneyflow_report",
]
