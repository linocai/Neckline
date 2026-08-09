"""周度校准报告(plan §五 V2-⑨-C:「周度出**校准报告**(进 ⑫ 的周复盘工作台)」)。

**边界**:本模块只**装配 + 渲染**,`scripts/weekly_calibration.py` 负责跑。
⛔ **不接报告管线**(那是 ⑭ 的活),也不写任何表 —— 校准报告是**读侧产物**,
它读 `basket_review_daily` / `baskets` / `basket_verification` 等已冻结的事实,
不产生新的库内状态。

**报告结构**(渲染顺序即阅读顺序)::

    §0 口径与样本(先说清楚这份报告基于多少天、多少篮、有没有降级)
    §1 分层成绩单(每个 `骨架 × 引擎 × 版本 × 条件集` 一节;V2.2-④-C 从两键扩到四键)
    §2 安慰剂对照臂(⑨-C2:随机同规模篮子 + 满仓持有基准)
    §3 数据诚实度(存拍覆盖 / 复盘降级 / 未判定篮子 —— 缺口摆在明面上)
    §4 双时钟与修改建议(V2.2-④:选股时钟八项 / 交易时钟六项 / 四分类建议)

**两条文案纪律**(与 §红线 5 一致,渲染层逐条守):

    · 样本不足**只报样本数、不报结论**(`Verdict.conclusive=False` 时渲染
      `verdict.text` 原文,⛔ 不许在旁边补一句"不过看起来还行")。
    · Tier = **注意力优先级,不是收益预测**;不许出现"推荐 / 建议买入 / 看好"类表述。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from neckline.calendar import trading_days_between
from neckline.eval.metrics import (
    MIN_CONCLUSION_DAYS, BasketRecord, StratumReport, evaluate, load_basket_panel,
)
from neckline.eval.placebo import PLACEBO_DRAWS, PlaceboReport, run_placebo

logger = logging.getLogger(__name__)

REPORT_SPEC_VERSION = "weekly_calibration_v1"

DISCLAIMER = (
    "本报告是**回看审计**,只进周复盘工作台与策略线迭代输入,"
    "**不进任何在线判据**(不改 Tier、不改排序、不进哨兵)。"
    "Tier 是注意力优先级,不是收益预测;单周结果噪声很大,改权重一律走换包。"
)


@dataclass
class CalibrationReport:
    spec_version: str
    date_from: str
    date_to: str
    generated_at: str
    n_trading_days: int
    n_baskets: int
    strata: List[StratumReport] = field(default_factory=list)
    placebo: List[PlaceboReport] = field(default_factory=list)
    honesty: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    #: V2.2-④:双时钟成绩单 + 四分类修改建议(`eval/iteration.build_iteration_report`
    #: 的原样产物)。**空 dict = 本期没跑到 / 算不出**,⛔ 不拿空段冒充"没有建议"。
    iteration: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "specVersion": self.spec_version,
            "dateFrom": self.date_from, "dateTo": self.date_to,
            "generatedAt": self.generated_at,
            "nTradingDays": self.n_trading_days, "nBaskets": self.n_baskets,
            "strata": [s.to_dict() for s in self.strata],
            "placebo": [p.to_dict() for p in self.placebo],
            "honesty": dict(self.honesty), "notes": list(self.notes),
            "iteration": dict(self.iteration),
            "disclaimer": DISCLAIMER,
        }


def _honesty(records: Sequence[BasketRecord]) -> Dict[str, Any]:
    """数据诚实度:缺口摆在明面上,不藏在小字里。"""
    capture = {"intraday": 0, "eod_approx": 0, "mixed": 0, "unknown": 0}
    no_review = no_card = degraded = not_evaluated = 0
    for r in records:
        if r.review_mech is None:
            no_review += 1
        else:
            src = (r.mech_item("mfe_mae") or {}).get("mfe_source") or "unknown"
            capture[src] = capture.get(src, 0) + 1
            if r.review_degraded:
                degraded += 1
        if r.card is None:
            no_card += 1
        if r.verification_state is None:
            not_evaluated += 1
    return {
        "baskets": len(records),
        "withoutReview": no_review, "withoutCard": no_card,
        "llmDegradedReviews": degraded, "notEvaluated": not_evaluated,
        "mfeSource": capture,
        "note": ("`withoutReview` = 那天复盘没跑过;`notEvaluated` = ⑧ 那一拍没跑过。"
                 "两者都是**运维缺口**,不是策略失败,故一律单独计数、不进任何比率的分母。"),
    }


def build_iteration_section(
    date_from: str,
    date_to: str,
    *,
    placebo: Optional[Sequence[PlaceboReport]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """V2.2-④ 的那一段:读**已结案**的选股时钟 + 交易时钟,出成绩单与四分类建议。

    **只读**(同本模块既有边界:装配 + 渲染,零写库)。窗口按 **D0**(选股时钟)与
    **开仓日**(交易时钟)取,与分层成绩单同一个区间口径。
    """
    from neckline.eval.iteration import build_iteration_report, resolve_thresholds
    from neckline.review.selection_clock import list_closures
    from neckline.review.trade_clock import list_trade_clocks, note_coverage

    closures = list_closures(date_from, date_to, db_path=db_path)
    clocks = list_trade_clocks(date_from=date_from, date_to=date_to, db_path=db_path)
    coverage = note_coverage(date_from=date_from, date_to=date_to, db_path=db_path)
    thresholds, problems = resolve_thresholds(db_path=db_path)
    return build_iteration_report(
        closures, clocks=clocks, placebo=list(placebo or ()),
        note_coverage=coverage, thresholds=thresholds, threshold_problems=problems,
        db_path=db_path,
    )


def build_report(
    date_from: Any,
    date_to: Any,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    with_tradable: bool = True,
    with_placebo: bool = True,
    draws: int = PLACEBO_DRAWS,
    now: Optional[date] = None,
) -> CalibrationReport:
    """装配一份周度校准报告(区间按 **D0** 取)。**永不抛异常**:任何一段炸了只记 note。"""
    lo = date_from if isinstance(date_from, str) else date_from.strftime("%Y%m%d")
    hi = date_to if isinstance(date_to, str) else date_to.strftime("%Y%m%d")
    rep = CalibrationReport(
        spec_version=REPORT_SPEC_VERSION, date_from=lo, date_to=hi,
        generated_at=(now or date.today()).strftime("%Y%m%d"),
        n_trading_days=0, n_baskets=0,
    )
    try:
        records = load_basket_panel(lo, hi, db_path=db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calibration] 面板装配失败", exc_info=True)
        rep.notes.append(f"面板装配失败:{type(exc).__name__}: {exc}")
        return rep

    rep.n_baskets = len(records)
    rep.n_trading_days = len({r.d0 for r in records})
    rep.honesty = _honesty(records)
    if not records:
        rep.notes.append(f"[{lo}, {hi}] 区间内没有任何已冻结的篮子,本期无可校准对象")
        # ⚠ 「没有篮子」**不等于**「没有交易」:手动开的仓照样有交易时钟。§4 段
        # 因此在这条早退路径上也要装配一次,⛔ 别让它跟着篮子一起消失。
        try:
            rep.iteration = build_iteration_section(lo, hi, db_path=db_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[calibration] 双时钟 / 四分类段计算失败(空篮子路径)", exc_info=True)
            rep.notes.append(f"双时钟 / 四分类段计算失败:{type(exc).__name__}: {exc}")
        return rep

    try:
        rep.strata = evaluate(records, db_path=db_path, parquet_dir=parquet_dir,
                              with_tradable=with_tradable)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calibration] 分层成绩单计算失败", exc_info=True)
        rep.notes.append(f"分层成绩单计算失败:{type(exc).__name__}: {exc}")

    if with_placebo:
        try:
            rep.placebo = run_placebo(records, draws=draws, db_path=db_path,
                                      parquet_dir=parquet_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[calibration] 安慰剂对照臂计算失败", exc_info=True)
            rep.notes.append(f"安慰剂对照臂计算失败:{type(exc).__name__}: {exc}")

    # —— V2.2-④:双时钟成绩单 + 四分类建议(整段包保险丝,炸了只记 note)————
    try:
        rep.iteration = build_iteration_section(lo, hi, placebo=rep.placebo, db_path=db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[calibration] 双时钟 / 四分类段计算失败", exc_info=True)
        rep.notes.append(f"双时钟 / 四分类段计算失败:{type(exc).__name__}: {exc}")

    if rep.n_trading_days < MIN_CONCLUSION_DAYS:
        rep.notes.append(
            f"本期只有 {rep.n_trading_days} 个交易日的样本(结论线 {MIN_CONCLUSION_DAYS} 天):"
            f"全部「谁更好」的判断都只会给样本数,不给结论"
        )
    return rep


# ══════════════════════════════════════════════════════════════════════════
# 渲染(markdown;文件形态先行,同 §2.3「CLI/文件形态先行」的既有体例)
# ══════════════════════════════════════════════════════════════════════════

def _pct(x: Any, nd: int = 2) -> str:
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return "—"
    return f"{float(x) * 100:+.{nd}f}%"


def _num(x: Any, nd: int = 3) -> str:
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return "—"
    return f"{float(x):.{nd}f}"


def _ratio(x: Any) -> str:
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return "—"
    return f"{float(x):.0%}"


def render_markdown(report: CalibrationReport) -> str:
    r = report
    out: List[str] = []
    out.append(f"# 周度校准报告 · {r.date_from} → {r.date_to}")
    out.append("")
    out.append(f"> {DISCLAIMER}")
    out.append("")
    out.append("## §0 口径与样本")
    out.append("")
    out.append(f"- 报告规格:`{r.spec_version}`,生成于 {r.generated_at}")
    out.append(f"- 样本:**{r.n_trading_days} 个交易日 / {r.n_baskets} 个篮子**"
               f"(结论线 = {MIN_CONCLUSION_DAYS} 个交易日)")
    out.append(f"- 分层维度:`skeleton_version` × `engine_code` × `engine_version` × "
               f"`verification_ruleset_version`(共 {len(r.strata)} 层;V2.2-④-C 从两键扩到"
               f"四键,历史 `LEGACY` 样本仍按老分层算、⛔ 未并进新层)")
    for n in r.notes:
        out.append(f"- ⚠ {n}")
    out.append("")

    out.append("## §1 分层成绩单")
    out.append("")
    if not r.strata:
        out.append("(本期无分层数据)")
        out.append("")
    for s in r.strata:
        out.append(f"### 骨架 `{s.pack_version}` × 引擎 `{s.engine_code}`/`{s.engine_version}`"
                   f" × 条件集 `{s.ruleset_version}`")
        out.append("")
        out.append(f"- 样本:{s.n_days} 个交易日 / {s.n_baskets} 个篮子")
        t = s.tier
        med = t.get("median_outcome") or {}
        obs = t.get("observed") or {}
        # V2.1-②:档位按**本分层数据里实际出现的**那些渲染,⛔ 不写死 `(1,2,3)`。
        # 写死会两头出错:两档时代凭空多一行「T3 —(n=0)」(读起来像"今天 T3 没样本",
        # 真相是 T3 已取消 —— 把系统缺席讲成了实质性结论);而若改写死 `(1,2)`,
        # 历史 K7-pack-v1 分层里真实存在的 T3 样本又会从成绩单上消失(伪造归因)。
        tiers_here = sorted(set(med) | set(obs) | set(t.get("counts") or {}))
        out.append("- **Tier 单调性**:"
                   + ("、".join(f"T{k} {_pct(med.get(k))}(n={obs.get(k, 0)})"
                                for k in tiers_here) or "(本层无档位样本)")
                   + f" → {'成立' if t.get('monotonic') else '不成立' if t.get('monotonic') is not None else '判不了'}")
        out.append(f"  - 判断:{s.tier_verdict.get('text')}")
        out.append(f"  - ⚠ {t.get('note')}")
        res = s.resonance
        out.append(f"- **篮子共振率**:{res.get('resonant')}/{res.get('judged')} = "
                   f"{_ratio(res.get('rate'))}(门槛:{res.get('threshold_rule')};"
                   f"另有 {res.get('unjudged')} 篮判不了)")
        v = s.verification
        dist = v.get("distribution") or {}
        out.append(f"- **验证率**:已验证 {_ratio(v.get('verified_rate'))} / "
                   f"被证伪 {_ratio(v.get('falsified_rate'))};"
                   f"四态分布 " + "、".join(f"{k} {val}" for k, val in dist.items())
                   + f";`not_evaluated` {v.get('not_evaluated')} 篮(不进分母)")
        ld = s.leader
        out.append(f"- **龙头带动**:带住 {ld.get('led')}/{ld.get('judged')} = "
                   f"{_ratio(ld.get('led_rate'))},龙头−其余中位差 {_pct(ld.get('spread_median'))}"
                   f"(无对照组的篮子 {ld.get('no_peer_group')} 个)")
        tr = s.tradable
        out.append(f"- **可交易收益**(判分唯一源 `exit_sim`):中位 {_pct(tr.get('median'))} / "
                   f"均值 {_pct(tr.get('mean'))},胜率 {_ratio(tr.get('win_rate'))};"
                   f"成交 {tr.get('member_fills')} 笔、买不进 {tr.get('member_not_filled')} 笔、"
                   f"窗口未走完 {tr.get('member_unfinished')} 笔(后两类**不进均值**)")
        if tr.get("fill_reasons"):
            out.append("  - 未成交原因分布:"
                       + "、".join(f"{k} {n}" for k, n in (tr.get("fill_reasons") or {}).items()))
        sel = s.selected
        if sel.get("available"):
            out.append(f"- **已选 vs 未选**:已选 {sel.get('selected')} 篮 {_pct(sel.get('selected_outcome'))} "
                       f"vs 未选 {sel.get('not_selected')} 篮 {_pct(sel.get('not_selected_outcome'))}")
        else:
            out.append(f"- **已选 vs 未选**:本期无用户选择记录(`{sel.get('reason')}`)—— "
                       f"指标位已建好,空值如实,⛔ 不拿「没人选过」当对照结论")
        con = s.contribution
        for label, key in (("驱动类型", "by_driver_kind"), ("市场环境", "by_market_regime"),
                           ("证据状态", "by_evidence_status"), ("角色(可交易收益)", "by_role_tradable")):
            buckets = con.get(key) or {}
            if buckets:
                out.append(f"- **按{label}**:"
                           + "、".join(f"{k} {_pct(v.get('median'))}(n={v.get('n')})"
                                       for k, v in buckets.items()))
        out.append("")

    out.append("## §2 安慰剂对照臂(⑨-C2)")
    out.append("")
    if not r.placebo:
        out.append("(本期未产出对照臂)")
        out.append("")
    for p in r.placebo:
        d = p.to_dict()
        out.append(f"### 包 `{p.pack_version}`（每日抽样 {p.draws} 次）")
        out.append("")
        out.append(f"- 真实篮子(**无追价上限口径**,与随机臂对齐):中位 "
                   f"{_pct(d['real']['median'])}(n={d['real']['n']} 天)")
        out.append(f"- 臂 A · 随机同规模篮子:逐日中位数的中位 {_pct(d['randomArm']['median'])} "
                   f"(n={d['randomArm']['n']} 天)")
        out.append(f"- 臂 B · 满仓持有基准:中位 {_pct(d['buyAndHoldArm']['median'])} "
                   f"(n={d['buyAndHoldArm']['n']} 天)")
        out.append(f"- **vs 随机**:{(p.vs_random or {}).get('text')}")
        out.append(f"- **vs 不作为**:{(p.vs_hold or {}).get('text')}")
        out.append(f"- ⚠ {d.get('note')}")
        if p.per_day:
            out.append("")
            out.append("| 交易日 | 真实篮数 | 真实收益 | 随机臂中位 | 随机臂 p10/p90 | 真实在随机分布的分位 | 满仓持有 |")
            out.append("|---|--:|--:|--:|--:|--:|--:|")
            for row in p.per_day:
                a = row.get("randomArm") or {}
                q = a.get("quantiles") or {}
                b = row.get("buyAndHoldArm") or {}
                out.append(
                    f"| {row.get('tradeDate')} | {row.get('realBaskets')} | {_pct(row.get('real'))} | "
                    f"{_pct(a.get('median'))} | {_pct(q.get('p10'))} / {_pct(q.get('p90'))} | "
                    f"{'—' if row.get('realPercentileInRandom') is None else str(row['realPercentileInRandom']) + '%'} | "
                    f"{_pct((b.get('quantiles') or {}).get('p50'))} |"
                )
        out.append("")

    out.append("## §3 数据诚实度")
    out.append("")
    h = r.honesty
    out.append(f"- 篮子总数 {h.get('baskets')};其中**没有复盘记录** {h.get('withoutReview')} 个、"
               f"**没有卡** {h.get('withoutCard')} 个、**没有验证判定** {h.get('notEvaluated')} 个")
    out.append(f"- LLM 解释缺席(降级)的复盘:{h.get('llmDegradedReviews')} 份")
    src = h.get("mfeSource") or {}
    out.append("- MFE/MAE 数据来源:"
               + "、".join(f"{k} {v}" for k, v in src.items() if v)
               + "(`eod_approx` = 缺存拍,幅度可信、**时刻未知**)")
    out.append(f"- ⚠ {h.get('note')}")
    out.append("")

    out.extend(render_iteration_section(r.iteration))
    return "\n".join(out)


def render_iteration_section(it: Optional[Mapping[str, Any]]) -> List[str]:
    """§4 双时钟与修改建议(V2.2-④)。**移交件复用同一份排版**,⛔ 不另写第二份。"""
    out: List[str] = ["## §4 双时钟与修改建议(V2.2-④)", ""]
    if not it:
        out.append("(本期未产出双时钟段 —— 周度作业还没跑到这个窗口,或该段计算失败;"
                   "详见 §0 的 note。**这不是「没有建议」**,是这一段没算出来。)")
        out.append("")
        return out

    n = it.get("samples") or {}
    out.append(f"- 样本:选股时钟已结案 **{n.get('selectionClock', 0)}** 篮 / "
               f"交易时钟 **{n.get('tradeClock', 0)}** 笔真实买入")
    out.append(f"- 分层键:{' × '.join('`%s`' % k for k in (it.get('strataKey') or []))}")
    out.append("")

    sel = (it.get("selection") or {}).get("overall") or {}
    out.append("### §4-1 选股时钟(K8 §十六 选股侧;样本 = **全部** T1/T2,与买没买无关)")
    out.append("")
    if not sel.get("samples"):
        out.append("(本期没有已结案的选股时钟样本。)")
    else:
        tier = sel.get("tier_signal_accuracy") or {}
        out.append("- **T1/T2 入场信号正确率**:"
                   + ("、".join(f"{k} {_ratio(v.get('accuracy'))}(n={v.get('n')})"
                                for k, v in tier.items()) or "—"))
        reg = sel.get("regime_accuracy") or {}
        out.append("- **各行情状态下的表现**:"
                   + ("、".join(f"{k} {_ratio(v.get('accuracy'))}(n={v.get('n')})"
                                for k, v in reg.items()) or "—(D0 状态层缺行)"))
        eng = sel.get("engine_versions") or {}
        out.append("- **C/Z/Y 各版本表现**:"
                   + ("、".join(f"{k} {_ratio(v.get('accuracy'))}(n={v.get('n')})"
                                for k, v in eng.items()) or "—"))
        drv = sel.get("driver_effectiveness") or {}
        out.append("- **主要驱动有效性**(D1 四态):"
                   + ("、".join(f"{k} n={v.get('n')}" for k, v in drv.items()) or "—"))
        sup = sel.get("support_and_liftoff") or {}
        out.append(f"- **支撑与启动形态**:入场区间触发 {sup.get('entry_triggered')}/"
                   f"{sel.get('samples')} = {_ratio(sup.get('entry_trigger_rate'))};"
                   f"D1 有落地读数 {sup.get('with_d1_metrics')} 篮")
        pv = sup.get("by_position_verdict") or {}
        if pv:
            out.append("  - 按 **D0 位置关判定**(§七 P3-49 的证据面):"
                       + "、".join(f"{k} {_ratio(v.get('accuracy'))}(n={v.get('n')})"
                                   for k, v in pv.items()))
        core = sel.get("core_vs_alternates") or {}
        out.append(f"- **核心与替代标的**:龙头带住 {core.get('led')}/{core.get('judged')} = "
                   f"{_ratio(core.get('led_rate'))}")
    out.append("")

    tr = it.get("trade") or {}
    out.append("### §4-2 交易时钟(K8 §十六 交易侧;样本 = **真实买入**)")
    out.append("")
    out.append(f"- 交易时钟:运行中 {tr.get('running', 0)} / 已结案 {tr.get('closed', 0)} "
               f"(共 {tr.get('trades', 0)} 笔)")
    pc = tr.get("plan_consistency") or {}
    out.append(f"- **入场与预案一致性**:落在建仓区间内 {pc.get('in_entry_zone')}/"
               f"{pc.get('judged')} = {_ratio(pc.get('rate'))};超过最高追价 "
               f"{pc.get('above_max_chase')} 笔")
    eq = tr.get("exit_quality_on_thesis") or {}
    out.append(f"- **判断成立时的离场质量**:到达目标区间 {eq.get('reached_target')}/"
               f"{eq.get('judged')} = {_ratio(eq.get('rate'))}")
    dec = tr.get("exit_quality_on_decay") or {}
    out.append(f"- **上涨效率变化**:有读数 {dec.get('with_efficiency_reading')} 笔,"
               f"比值中位 {_num(dec.get('ratio_median'), 2)} —— ⚠ {dec.get('note')}")
    sq = tr.get("stop_quality_on_failure") or {}
    out.append("- **离场原因分布**:"
               + ("、".join(f"{k} {v}" for k, v in (sq.get('by_close_reason') or {}).items())
                  or "—"))
    cov = tr.get("note_coverage") or {}
    if cov.get("available"):
        out.append(f"- **用户主观说明覆盖率**(§七 P3-28):{cov.get('with_note')}/"
                   f"{cov.get('trades')} = {_ratio(cov.get('coverage'))} 笔带说明"
                   f"(共 {cov.get('notes')} 条)")
    else:
        out.append(f"- **用户主观说明覆盖率**(§七 P3-28):{cov.get('unavailable_reason') or '—'}")
    out.append("")

    out.append("### §4-3 修改建议四分类(K8 §十七)")
    out.append("")
    th = it.get("thresholds") or {}
    if not th.get("available"):
        out.append(f"🔴 **本期不给分类**:{th.get('unavailableReason')}")
        for p in th.get("problems") or []:
            out.append(f"  - ⚠ 配置问题:{p}")
        out.append("")
        out.append("下表只列**统计量**(每行的 `建议` 列写明还缺哪两个数)——"
                   "「还没决定」与「样本不足」是两件事,⛔ 不许混成一句话。")
    else:
        out.append(f"- 分界线(**用户拍板、经四道闸进包**):`min_n={th.get('minN')}` / "
                   f"`retire_min_n={th.get('retireMinN')}`")
    out.append("")
    rows = it.get("suggestions") or []
    if not rows:
        out.append("(本期无因素统计量 —— 没有已结案的选股时钟样本。)")
        out.append("")
        return out
    out.append("| 分层(骨架/引擎/版本) | 因素 | n | 正确率 | 本层基线 | 差 | 安慰剂 | 分类 |")
    out.append("|---|---|--:|--:|--:|--:|---|---|")
    for row in rows:
        klass = row.get("klass") or f"**待定**(`{row.get('klassStatus')}`)"
        out.append(
            f"| `{row.get('skeletonVersion')}`/`{row.get('engineCode')}`/"
            f"`{row.get('engineVersion')}` | `{row.get('factor')}` | {row.get('n')} | "
            f"{_ratio(row.get('accuracy'))} | {_ratio(row.get('baselineAccuracy'))} | "
            f"{_num(row.get('delta'), 3)} | {row.get('placeboEdge')} | {klass} |"
        )
    out.append("")
    out.append(f"> {it.get('disclaimer')}")
    out.append("")
    return out


def write_report(
    report: CalibrationReport,
    out_dir: Path,
    *,
    stem: Optional[str] = None,
) -> Dict[str, Path]:
    """把报告落成 `.md` + `.json` 两份(前者给人读,后者给 ⑫ 周复盘工作台接线)。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    name = stem or f"calibration_{report.date_from}_{report.date_to}"
    md_path = out_dir / f"{name}.md"
    json_path = out_dir / f"{name}.json"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2,
                                    sort_keys=True), encoding="utf-8")
    return {"markdown": md_path, "json": json_path}


def week_bounds(any_day: date) -> tuple:
    """含 `any_day` 的那一周(周一 → 周日)里的**交易日**首尾。无交易日 → `(None, None)`。"""
    monday = any_day - __import__("datetime").timedelta(days=any_day.weekday())
    sunday = monday + __import__("datetime").timedelta(days=6)
    days = trading_days_between(monday, sunday)
    return (days[0], days[-1]) if days else (None, None)


__all__ = [
    "REPORT_SPEC_VERSION", "DISCLAIMER",
    "CalibrationReport", "build_report", "build_iteration_section",
    "render_markdown", "render_iteration_section", "write_report", "week_bounds",
]
