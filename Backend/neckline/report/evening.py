"""晚间编排链(V2.5.0 S7,PROJECT_PLAN §9.3 的新段序)。**批算侧**。

    16:05 拉数(`scripts/daily_update.py`,**不在本模块内**)
      → facts    架构第一层 · 事实包构建 + 冻结
      → k9       架构第二层 · 策略层(硬边界 → 四通道 → 排序 → 名额)
      → explain  架构第三层 · 解释层(**S9 未建**)
      → playbook 架构第四层 · 预案层(**S10 未建**)
      → report   报告装配 + 落库(+ APNs)

**为什么批算住这里、不塞进 `pipeline.py`**:那份是**在线路径**,P0-23 纪律要求它
只准读表、不准现算(§12 坑 1,2026-07-29 被生产 OOM 挡过一次上云)。本链的前两段
恰恰是批算大户。⛔ 别为了少一个文件把那条红线钝化掉。

**每段各自包保险丝**:任一段异常只记 WARNING + 在结果里标 `failed`,链继续往下走,
报告照出、缺席**如实披露**。⛔ 绝不因为某段失败而当日无报告 —— 唯一例外是最后那段
报告本身炸了(那才是真的没有报告,退出码必须非零)。

🔴 **还没建的段是 `not_built`,不是 `ok`**:`explain` / `playbook` 要到 S9 / S10 才
存在。给它们一个「跑过了」的绿灯,等于让报告宣称清单已经过消息面剔除 —— 那正是
`k9_runs.listing_finalized_by` 这一列要防的事。

⚠ **`segments` 只挑跑哪几段,不改顺序**:传进来的集合会按 `CHAIN_SEGMENTS` 重排,
乱序传参不会得到乱序执行(三个 oneshot 单元靠这个接缝分段跑)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SEG_FACTS = "facts"
SEG_K9 = "k9"
SEG_EXPLAIN = "explain"
SEG_PLAYBOOK = "playbook"
SEG_REPORT = "report"

#: **顺序定死**(§9.3)。⛔ 改顺序前先读模块头。
CHAIN_SEGMENTS: Tuple[str, ...] = (SEG_FACTS, SEG_K9, SEG_EXPLAIN, SEG_PLAYBOOK, SEG_REPORT)

STATUS_OK = "ok"
STATUS_FAILED = "failed"        # 跑了、炸了(保险丝吞掉,报告里如实标)
STATUS_SKIPPED = "skipped"      # 调用方没要这一段(分段跑)
STATUS_EMPTY = "empty"          # 跑了、没有可做的(**合法输出**)
STATUS_NOT_BUILT = "not_built"  # 🔴 这一层本版还没建(S9 / S10),⛔ 不是 ok


@dataclass
class EveningChainResult:
    """一次晚间链的执行结果。逐段可查 —— 「没做」「做了没东西」「做了炸了」
    「还没建」四态分开,⛔ 不许合并成一个 bool。"""

    trade_date: date
    report_date: date
    status: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    bundle: Any = None

    def ok(self, seg: str) -> bool:
        return self.status.get(seg) in (STATUS_OK, STATUS_EMPTY)


def run_evening_chain(
    trade_date: date,
    *,
    report_date: Optional[date] = None,
    segments: Sequence[str] = CHAIN_SEGMENTS,
    k9_params_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    save: bool = True,
) -> EveningChainResult:
    """跑晚间链(单进程串行)。"""
    report_date = report_date or trade_date
    wanted = [s for s in CHAIN_SEGMENTS if s in set(segments)]
    unknown = sorted(set(segments) - set(CHAIN_SEGMENTS))
    if unknown:
        raise ValueError(f"未知段名 {unknown};可用:{list(CHAIN_SEGMENTS)}")

    res = EveningChainResult(trade_date=trade_date, report_date=report_date)
    for seg in CHAIN_SEGMENTS:
        if seg not in wanted:
            res.status[seg] = STATUS_SKIPPED

    if SEG_FACTS in wanted:
        _fuse(res, SEG_FACTS, lambda: _run_facts(
            trade_date, db_path=db_path, parquet_dir=parquet_dir))
    if SEG_K9 in wanted:
        _fuse(res, SEG_K9, lambda: _run_k9(
            trade_date, k9_params_path=k9_params_path, db_path=db_path,
            parquet_dir=parquet_dir))
        # 🔴 **k9 段最清楚它自己为什么没跑**(参数未配置 / 参数无效 + 逐条缺口)。
        # 把原因**带下去**给报告段 —— 报告⛔ 不去猜别人的失败原因,也⛔ 不自己再
        # 读一遍参数文件(那会得出一个与事实无关的结论,见 `pipeline.build_report`)。
        for gap in _upstream_gaps(res):
            res.notes.append(f"k9 段:{gap}")
    for seg, slice_id in ((SEG_EXPLAIN, "S9"), (SEG_PLAYBOOK, "S10")):
        if seg in wanted:
            res.status[seg] = STATUS_NOT_BUILT
            res.notes.append(
                f"{seg} 层本版尚未建({slice_id});清单因此**未经消息面剔除**,"
                f"报告已如实标注(k9_runs.listing_finalized_by='k9')")

    if SEG_REPORT in wanted:
        # 🔴 报告段**不包保险丝**:它炸了就是真的没有报告,异常必须往上抛
        # (退出码非零,systemd 那侧看得见)。
        res.bundle = _run_report(
            trade_date, report_date=report_date,
            upstream_gaps=_upstream_gaps(res),
            db_path=db_path, parquet_dir=parquet_dir, save=save)
        res.status[SEG_REPORT] = STATUS_OK
        res.stats[SEG_REPORT] = {
            "state": res.bundle.state.value,
            "listingSize": res.bundle.listing_size,
        }
    return res


def _fuse(res: EveningChainResult, seg: str, fn) -> None:
    """一段的保险丝:炸了记 WARNING + 标 `failed`,链继续。"""
    try:
        status, stats = fn()
    except Exception as e:  # noqa: BLE001
        logger.warning("[evening] %s 段失败(已吞,链继续):%s", seg, e, exc_info=True)
        res.status[seg] = STATUS_FAILED
        res.notes.append(f"{seg} 段失败:{e}")
        return
    res.status[seg] = status
    if stats:
        res.stats[seg] = stats


def _run_facts(
    trade_date: date, *, db_path: Optional[Path], parquet_dir: Optional[Path]
) -> Tuple[str, Dict[str, Any]]:
    """事实包构建 + 冻结。**已冻结过 → 幂等跳过**(⛔ 不覆盖,§5.3.2 纪律 3)。"""
    from neckline.facts import pack as fact_pack
    from neckline.facts import store as fact_store

    try:
        existing = fact_store.load_pack(
            trade_date, parquet_dir=parquet_dir, db_path=db_path)
        return STATUS_OK, {"packId": existing.pack_id, "frozen": "already"}
    except fact_store.PackNotFrozen:
        pass

    built = fact_pack.build(trade_date, parquet_dir=parquet_dir, db_path=db_path)
    if isinstance(built, fact_pack.IncompletePack):
        # 数据未到齐 → **不冻结**;报告会因此走 `not_run` 并逐条列出缺口。
        logger.error("[evening] %s 事实包数据未到齐,不冻结:%s", trade_date, built.missing)
        return STATUS_EMPTY, {"missing": list(built.missing)}
    frozen = fact_store.freeze_pack(built, parquet_dir=parquet_dir, db_path=db_path)
    return STATUS_OK, {"packId": frozen.pack_id, "rows": frozen.row_count}


def _run_k9(
    trade_date: date, *, k9_params_path: Optional[Path],
    db_path: Optional[Path], parquet_dir: Optional[Path],
) -> Tuple[str, Dict[str, Any]]:
    """策略层。⛔ **无默认参数路径**:没传 = 参数未配置 = 报告「今天没跑成」。"""
    from neckline.k9 import params as k9_params
    from neckline.k9 import run as k9_run

    if k9_params_path is None:
        logger.error(
            "[evening] 未提供 --k9-params;策略层不跑,报告将出「今天没跑成 · 参数未配置」"
            "(裁定 5:⛔ 不使用任何默认值)")
        return STATUS_EMPTY, {"reason": "params_not_configured"}
    try:
        params = k9_params.load(k9_params_path, db_path=db_path)
    except k9_params.ParamsUnavailable as e:
        logger.error("[evening] 参数包无效,策略层不跑:%s", e.describe())
        return STATUS_EMPTY, {"reason": "params_invalid", "gaps": e.gaps()}

    result, run_id = k9_run.run_k9(
        trade_date, params=params, parquet_dir=parquet_dir, db_path=db_path)
    return STATUS_OK, {
        "runId": run_id,
        "seated": result.shortlist.size,
        "tierUsed": result.shortlist.tier_used.value,
        "capacityShort": result.shortlist.capacity_short,
    }


def _upstream_gaps(res: EveningChainResult) -> List[str]:
    """把 k9 段自己报出来的缺口交给报告段。

    ⚠ **只在本次真跑过 k9 段时才有话说**:分段跑(`--segments report`)时本次链根本
    没碰策略层,那时的权威是 `k9_runs` 里那一行 —— ⛔ 不许拿「本进程没跑 k9」当成
    「今天没跑成」的理由(那正是冒烟里抓到的 bug)。
    """
    stats = res.stats.get(SEG_K9) or {}
    gaps = list(stats.get("gaps") or [])
    reason = stats.get("reason")
    if reason == "params_not_configured":
        gaps.insert(0, "参数未配置(晚间链未拿到 --k9-params;⛔ 本系统无默认参数)")
    elif reason == "params_invalid" and not gaps:
        gaps.insert(0, "参数包无效")
    if res.status.get(SEG_K9) == STATUS_FAILED:
        gaps.append("策略层当日执行失败(见服务端日志)")
    return gaps


def _run_report(
    trade_date: date, *, report_date: date, upstream_gaps: Sequence[str],
    db_path: Optional[Path], parquet_dir: Optional[Path], save: bool,
):
    from neckline.report import pipeline as pipeline_mod
    from neckline.report import store as report_store

    bundle = pipeline_mod.build_report(
        trade_date, report_date=report_date, upstream_gaps=upstream_gaps,
        db_path=db_path, parquet_dir=parquet_dir)
    if save:
        report_store.save_k9_report(
            trade_date=bundle.trade_date, report_date=bundle.report_date,
            state=bundle.state.value, headline=bundle.headline, gaps=list(bundle.gaps),
            markdown=bundle.markdown, structured=bundle.structured,
            strategy=bundle.strategy,
            params_package_version=bundle.params_package_version,
            pack_id=bundle.pack_id, pack_version=bundle.pack_version,
            listing_size=bundle.listing_size, strict_count=bundle.strict_count,
            relaxed_count=bundle.relaxed_count, db_path=db_path,
        )
    return bundle


# ══════════════════════════════════════════════════════════════════════════
# 覆盖率的接线 —— 🔴 只能住在**编排器**里
# ══════════════════════════════════════════════════════════════════════════

def coverage_inputs(
    trade_date: date, *, strategy: str = "K9",
    db_path: Optional[Path] = None, parquet_dir: Optional[Path] = None,
):
    """把 D−1 的 K9 清单与全市场 disposition 翻译成覆盖率层的 DTO。

    🔴 **这段接线为什么在编排器里、不在 `scorecard/` 里**:守门单测断言
    `scorecard/**` **零 import** `neckline.k9` —— 尺子不许读被量的东西(§5.8.1)。
    策略侧的信息只能经**数据**通道(`k9_listing_entries` / `k9_disposition`)进来,
    而「谁去把这两边接上」是编排器的活。⛔ 别把这个函数搬进 `scorecard/`。

    返回 `(ListingSnapshot | None, [DispositionRow] | None)` ——
    两个 `None` 各自表示「昨天没有清单」/「没有 D−1 disposition」,
    覆盖率层据此写 NULL,⛔ 不是 0。
    """
    import json

    from neckline.calendar import prev_trading_day
    from neckline.k9 import store as k9_store
    from neckline.scorecard import coverage as coverage_mod

    d_1 = prev_trading_day(trade_date)
    codes = k9_store.load_listing_codes(d_1, strategy=strategy, db_path=db_path)
    listing = (
        coverage_mod.ListingSnapshot(trade_date=d_1, codes=frozenset(codes))
        if codes else None
    )

    frame = k9_store.load_disposition(d_1, parquet_dir=parquet_dir)
    if frame.is_empty():
        return listing, None
    rows = [
        coverage_mod.DispositionRow(
            ts_code=r["ts_code"],
            excluded_by=r["excluded_by"],
            recalled=bool(json.loads(r["recalled_patterns_json"] or "[]")),
            rank=r["rank"],
            seated=bool(r["seated"]),
            news_excluded=bool(r["news_excluded"]),
        )
        for r in frame.iter_rows(named=True)
    ]
    return listing, rows


__all__ = [
    "SEG_FACTS", "SEG_K9", "SEG_EXPLAIN", "SEG_PLAYBOOK", "SEG_REPORT",
    "CHAIN_SEGMENTS",
    "STATUS_OK", "STATUS_FAILED", "STATUS_SKIPPED", "STATUS_EMPTY", "STATUS_NOT_BUILT",
    "EveningChainResult", "run_evening_chain", "coverage_inputs",
]
