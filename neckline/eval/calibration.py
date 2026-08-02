"""周度校准报告(plan §五 V2-⑨-C:「周度出**校准报告**(进 ⑫ 的周复盘工作台)」)。

**边界**:本模块只**装配 + 渲染**,`scripts/weekly_calibration.py` 负责跑。
⛔ **不接报告管线**(那是 ⑭ 的活),也不写任何表 —— 校准报告是**读侧产物**,
它读 `basket_review_daily` / `baskets` / `basket_verification` 等已冻结的事实,
不产生新的库内状态。

**报告结构**(渲染顺序即阅读顺序)::

    §0 口径与样本(先说清楚这份报告基于多少天、多少篮、有没有降级)
    §1 分层成绩单(每个 `pack_version × verification_ruleset_version` 一节)
    §2 安慰剂对照臂(⑨-C2:随机同规模篮子 + 满仓持有基准)
    §3 数据诚实度(存拍覆盖 / 复盘降级 / 未判定篮子 —— 缺口摆在明面上)

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
from typing import Any, Dict, List, Optional, Sequence

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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "specVersion": self.spec_version,
            "dateFrom": self.date_from, "dateTo": self.date_to,
            "generatedAt": self.generated_at,
            "nTradingDays": self.n_trading_days, "nBaskets": self.n_baskets,
            "strata": [s.to_dict() for s in self.strata],
            "placebo": [p.to_dict() for p in self.placebo],
            "honesty": dict(self.honesty), "notes": list(self.notes),
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
    out.append(f"- 分层维度:`pack_version` × `verification_ruleset_version`"
               f"(共 {len(r.strata)} 层)")
    for n in r.notes:
        out.append(f"- ⚠ {n}")
    out.append("")

    out.append("## §1 分层成绩单")
    out.append("")
    if not r.strata:
        out.append("(本期无分层数据)")
        out.append("")
    for s in r.strata:
        out.append(f"### 包 `{s.pack_version}` × 条件集 `{s.ruleset_version}`")
        out.append("")
        out.append(f"- 样本:{s.n_days} 个交易日 / {s.n_baskets} 个篮子")
        t = s.tier
        med = t.get("median_outcome") or {}
        out.append("- **Tier 单调性**:"
                   + "、".join(f"T{k} {_pct(med.get(k))}(n={t.get('observed', {}).get(k, 0)})"
                               for k in (1, 2, 3))
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
    return "\n".join(out)


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
    "CalibrationReport", "build_report", "render_markdown", "write_report", "week_bounds",
]
