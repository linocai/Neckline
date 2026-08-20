"""报告装配(架构 §3.5,PROJECT_PLAN §5.10)。**只读不算。**

🛑 **在线路径只准读表、不准现算**(§12 坑 1,P0-23 纪律,2026-07-29 挡过一次上云):
本模块**只读**已经落好的东西 —— 冻结事实包、`k9_runs` / `k9_listing_entries`、
`k9_coverage_daily`。批算住 `report/evening.py` 的各段里。⛔ 别把「报告没数就顺手
算一份」写进来:那条路第一次跑没事,第 250 天全历史扫 parquet 时会被 OOM-kill,
而且不报错。

**三种状态,每天必发其一,首行即可分辨**(裁定 5 / 架构 §3.5):

| 状态 | 触发 |
|---|---|
| `has_list` 今天有这些 | 事实包已冻结 + 参数有效 + 清单 ≥1 只 |
| `empty` 今天没有 | 事实包已冻结 + 参数有效 + 清单 0 只 |
| `not_run` 今天没跑成 | 事实包未冻结 / 参数未配置或无效 / 链路异常 |

⚠ **参数未配置的日子照样发报告**(§5.10):清单段标「今天没跑成 · 参数未配置」,
而**方向背景、市场事实、覆盖率成绩线照常呈现**。日节奏不断,尺子照跑。
`not_run` 管的是**清单段**,不是整份报告。

🔴 **双日期契约⛔ 不许退化**(LRN-20260816-001):`report_date` 与 `trade_date` 是
两件事,本模块的入口两个都是**必填关键字**。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from neckline.facts import store as facts_store
from neckline.k9 import store as k9_store
from neckline.report.state import ReportState, headline, resolve_state
from neckline.scorecard import store as scorecard_store

logger = logging.getLogger(__name__)

#: 方向背景的来源键(事实层的 LLM 旁路,架构 §八 / §5.3.6)。
#: ⚠ `facts/direction_llm.py` 尚未建(S3 登记 ⑦,架构 §十 把它列为「可随时接入」)
#: → 这个键现在恒缺席,报告里如实写「未接入」,⛔ 不编一段方向解读。
DIRECTION_KEY = "direction"


@dataclass(frozen=True)
class ReportBundle:
    """一份报告的全部内容。渲染(`render.py`)与落库(`store.py`)都吃它。"""

    trade_date: date
    report_date: date
    state: ReportState
    headline: str
    gaps: Tuple[str, ...]
    strategy: str
    params_package_version: Optional[str]
    pack_id: Optional[str]
    pack_version: Optional[str]
    listing: Tuple[Dict[str, Any], ...]
    listing_size: Optional[int]
    strict_count: Optional[int]
    relaxed_count: Optional[int]
    run: Optional[Dict[str, Any]]
    market: Dict[str, Any]
    direction: Optional[Dict[str, Any]]
    coverage: Optional[Dict[str, Any]]
    markdown: str = ""
    structured: Dict[str, Any] = field(default_factory=dict)


def build_report(
    trade_date: date,
    *,
    report_date: date,
    upstream_gaps: Sequence[str] = (),
    strategy: str = "K9",
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> ReportBundle:
    """装配当日报告。**只读**,⛔ 不触发任何批算。

    🔴 **「参数有没有配好」的权威是 `k9_runs`,不是报告自己再读一遍参数文件。**
    报告段跑在**自己的进程**里(`neckline-report.service` 只跑 `--segments report`,
    它⛔ 不该也不必拿到参数包路径);而「策略层今天跑没跑成、跑在哪版参数上」
    早就记在运行账里了。报告自己去 load 一次参数文件会得到一个**与事实无关**的结论
    —— 那正是这次冒烟里抓到的 bug:策略层明明跑出了 5 只,报告却因为自己没拿到
    参数路径而宣布「今天没跑成」,同时又把那 5 只落进了库。

    `upstream_gaps` 是**上游段自己说的原因**(k9 段最清楚它为什么没跑:参数未配置 /
    参数无效 + 逐条缺口)。⛔ 报告不去猜别人的失败原因。
    """
    from neckline.report import render as render_mod

    gaps: List[str] = []

    # —— 事实包(市场事实 / 方向背景的载体)————————————————————————————————
    pack = None
    try:
        pack = facts_store.load_pack(
            trade_date, parquet_dir=parquet_dir, db_path=db_path)
    except facts_store.PackNotFrozen:
        gaps.append(f"事实包未冻结({trade_date} 数据未到齐)")
    market: Dict[str, Any] = dict(pack.market) if pack is not None else {}
    direction = market.get(DIRECTION_KEY)

    # —— 清单(⚠ 只读 `k9_runs` / `k9_listing_entries`,⛔ 不现算)——————————
    run = k9_store.load_run(trade_date, strategy=strategy, db_path=db_path)
    listing: Tuple[Dict[str, Any], ...] = ()
    listing_size: Optional[int] = None
    strict_count = relaxed_count = None
    if run is not None:
        # 🔴 **有运行账 = 策略层今天确实跑过**。此时本次进程有没有拿到参数路径
        # 是**无关的**(报告描述的是**这一天**,不是这一次调用)——
        # `upstream_gaps` 只服务于「为什么没跑成」的诊断,这里一条都不采。
        listing = tuple(k9_store.load_listing(
            trade_date, strategy=strategy, db_path=db_path))
        listing_size = len(listing)
        strict_count = sum(1 for e in listing if e["tier"] == "strict")
        relaxed_count = listing_size - strict_count
    else:
        # 没有运行账 → 「今天没跑成」。**为什么**由上游段自己说;上游没说 →
        # 如实说「没有运行记录」,⛔ 不替它编一个原因。
        gaps.extend(upstream_gaps)
        if not gaps or all("事实包未冻结" in g for g in gaps):
            gaps.append("策略层当日未产出运行记录(k9_runs 无该日行)")

    state = resolve_state(
        pack_frozen=pack is not None,
        # 🔴 有运行账 = 那天确实拿着一份**已校验**的参数包跑过(`k9/run.py` 之前
        # 必须先过 `params.load`)。⛔ 不在这里第二次判定参数是否配好。
        params_ok=run is not None,
        listing_count=listing_size,
    )
    line = headline(
        state, listing_count=listing_size, strict_count=strict_count,
        relaxed_count=relaxed_count, gaps=gaps,
    )

    # —— 覆盖率成绩线(⚠ 参数未配置的日子照样呈现,§5.10)————————————————
    coverage = _load_coverage(trade_date, db_path=db_path)

    bundle = ReportBundle(
        trade_date=trade_date,
        report_date=report_date,
        state=state,
        headline=line,
        gaps=tuple(gaps),
        strategy=strategy,
        params_package_version=None if run is None else run["params_package_version"],
        pack_id=None if pack is None else pack.pack_id,
        pack_version=None if pack is None else pack.pack_version,
        listing=listing,
        listing_size=listing_size,
        strict_count=strict_count,
        relaxed_count=relaxed_count,
        run=run,
        market=market,
        direction=direction,
        coverage=coverage,
    )
    structured = render_mod.structured(bundle)
    return ReportBundle(
        **{**bundle.__dict__,
           "markdown": render_mod.markdown(bundle, structured),
           "structured": structured}
    )


def _load_coverage(
    trade_date: date, *, db_path: Optional[Path]
) -> Optional[Dict[str, Any]]:
    """当日覆盖率(尺子)。没有 → `None`(⛔ 不编一行 0)。"""
    rows = scorecard_store.load_coverage_days(
        start=trade_date, end=trade_date, db_path=db_path)
    return rows[0] if rows else None


__all__ = ["DIRECTION_KEY", "ReportBundle", "build_report"]
