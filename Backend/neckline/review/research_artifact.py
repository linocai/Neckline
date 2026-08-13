"""Shared contract for reading and rendering offline research artifacts.

This module contains no evaluator and performs no database writes.  It is the
small production-side boundary shared by the API, review workspace, and the
offline whynotme laboratory.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, List, Mapping, Optional

from neckline.calendar import trading_days_between


DISCLAIMER = (
    "本报告是**回看审计**,只进周复盘工作台与策略线迭代输入,"
    "**不进任何在线判据**(不改 Tier、不改排序、不进哨兵)。"
    "Tier 是注意力优先级,不是收益预测;单周结果噪声很大,改权重一律走换包。"
)

ARTIFACT_PREFIX = "calibration_"


def artifact_stem(date_from: str, date_to: str) -> str:
    return f"{ARTIFACT_PREFIX}{date_from}_{date_to}"


def week_bounds(any_day: date) -> tuple:
    """Return the first and last trading days in ``any_day``'s week."""
    monday = any_day - timedelta(days=any_day.weekday())
    sunday = monday + timedelta(days=6)
    days = trading_days_between(monday, sunday)
    return (days[0], days[-1]) if days else (None, None)


def _num(value: Any, digits: int = 3) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "—"
    return f"{float(value):.{digits}f}"


def _ratio(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "—"
    return f"{float(value):.0%}"


def render_iteration_section(iteration: Optional[Mapping[str, Any]]) -> List[str]:
    """Render the iteration section already calculated by the research job."""
    out: List[str] = ["## §4 双时钟与修改建议(V2.2-④)", ""]
    if not iteration:
        out.append("(本期未产出双时钟段 —— 周度作业还没跑到这个窗口,或该段计算失败;"
                   "详见 §0 的 note。**这不是「没有建议」**,是这一段没算出来。)")
        out.append("")
        return out

    n = iteration.get("samples") or {}
    out.append(f"- 样本:选股时钟已结案 **{n.get('selectionClock', 0)}** 篮 / "
               f"交易时钟 **{n.get('tradeClock', 0)}** 笔真实买入")
    out.append(f"- 分层键:{' × '.join('`%s`' % key for key in (iteration.get('strataKey') or []))}")
    out.append("")

    selection = (iteration.get("selection") or {}).get("overall") or {}
    out.append("### §4-1 选股时钟(K8 §十六 选股侧;样本 = **全部** T1/T2,与买没买无关)")
    out.append("")
    if not selection.get("samples"):
        out.append("(本期没有已结案的选股时钟样本。)")
    else:
        tier = selection.get("tier_signal_accuracy") or {}
        out.append("- **T1/T2 入场信号正确率**:"
                   + ("、".join(f"{key} {_ratio(value.get('accuracy'))}(n={value.get('n')})"
                                for key, value in tier.items()) or "—"))
        regimes = selection.get("regime_accuracy") or {}
        out.append("- **各行情状态下的表现**:"
                   + ("、".join(f"{key} {_ratio(value.get('accuracy'))}(n={value.get('n')})"
                                for key, value in regimes.items()) or "—(D0 状态层缺行)"))
        engines = selection.get("engine_versions") or {}
        out.append("- **C/Z/Y 各版本表现**:"
                   + ("、".join(f"{key} {_ratio(value.get('accuracy'))}(n={value.get('n')})"
                                for key, value in engines.items()) or "—"))
        drivers = selection.get("driver_effectiveness") or {}
        out.append("- **主要驱动有效性**(D1 四态):"
                   + ("、".join(f"{key} n={value.get('n')}" for key, value in drivers.items())
                      or "—"))
        support = selection.get("support_and_liftoff") or {}
        out.append(f"- **支撑与启动形态**:入场区间触发 {support.get('entry_triggered')}/"
                   f"{selection.get('samples')} = {_ratio(support.get('entry_trigger_rate'))};"
                   f"D1 有落地读数 {support.get('with_d1_metrics')} 篮")
        position = support.get("by_position_verdict") or {}
        if position:
            out.append("  - 按 **D0 位置关判定**:"
                       + "、".join(f"{key} {_ratio(value.get('accuracy'))}(n={value.get('n')})"
                                  for key, value in position.items()))
        core = selection.get("core_vs_alternates") or {}
        out.append(f"- **核心与替代标的**:龙头带住 {core.get('led')}/{core.get('judged')} = "
                   f"{_ratio(core.get('led_rate'))}")
    out.append("")

    trade = iteration.get("trade") or {}
    out.append("### §4-2 交易时钟(K8 §十六 交易侧;样本 = **真实买入**)")
    out.append("")
    out.append(f"- 交易时钟:运行中 {trade.get('running', 0)} / 已结案 {trade.get('closed', 0)} "
               f"(共 {trade.get('trades', 0)} 笔)")
    consistency = trade.get("plan_consistency") or {}
    out.append(f"- **入场与预案一致性**:落在建仓区间内 {consistency.get('in_entry_zone')}/"
               f"{consistency.get('judged')} = {_ratio(consistency.get('rate'))};超过最高追价 "
               f"{consistency.get('above_max_chase')} 笔")
    exits = trade.get("exit_quality_on_thesis") or {}
    out.append(f"- **判断成立时的离场质量**:到达目标区间 {exits.get('reached_target')}/"
               f"{exits.get('judged')} = {_ratio(exits.get('rate'))}")
    decay = trade.get("exit_quality_on_decay") or {}
    out.append(f"- **上涨效率变化**:有读数 {decay.get('with_efficiency_reading')} 笔,"
               f"比值中位 {_num(decay.get('ratio_median'), 2)} —— ⚠ {decay.get('note')}")
    quality = trade.get("stop_quality_on_failure") or {}
    out.append("- **离场原因分布**:"
               + ("、".join(f"{key} {value}" for key, value in
                            (quality.get('by_close_reason') or {}).items()) or "—"))
    coverage = trade.get("note_coverage") or {}
    if coverage.get("available"):
        out.append(f"- **用户主观说明覆盖率**:{coverage.get('with_note')}/"
                   f"{coverage.get('trades')} = {_ratio(coverage.get('coverage'))} 笔带说明"
                   f"(共 {coverage.get('notes')} 条)")
    else:
        out.append(f"- **用户主观说明覆盖率**:{coverage.get('unavailable_reason') or '—'}")
    out.append("")

    out.append("### §4-3 修改建议四分类(K8 §十七)")
    out.append("")
    thresholds = iteration.get("thresholds") or {}
    if not thresholds.get("available"):
        out.append(f"🔴 **本期不给分类**:{thresholds.get('unavailableReason')}")
        for problem in thresholds.get("problems") or []:
            out.append(f"  - ⚠ 配置问题:{problem}")
        out.append("")
        out.append("下表只列**统计量**;「还没决定」与「样本不足」是两件事。")
    else:
        out.append(f"- 分界线:`min_n={thresholds.get('minN')}` / "
                   f"`retire_min_n={thresholds.get('retireMinN')}`")
    out.append("")
    rows = iteration.get("suggestions") or []
    if not rows:
        out.extend(["(本期无因素统计量 —— 没有已结案的选股时钟样本。)", ""])
        return out
    out.extend([
        "| 分层(骨架/引擎/版本) | 因素 | n | 正确率 | 本层基线 | 差 | 安慰剂 | 分类 |",
        "|---|---|--:|--:|--:|--:|---|---|",
    ])
    for row in rows:
        klass = row.get("klass") or f"**待定**(`{row.get('klassStatus')}`)"
        out.append(
            f"| `{row.get('skeletonVersion')}`/`{row.get('engineCode')}`/"
            f"`{row.get('engineVersion')}` | `{row.get('factor')}` | {row.get('n')} | "
            f"{_ratio(row.get('accuracy'))} | {_ratio(row.get('baselineAccuracy'))} | "
            f"{_num(row.get('delta'), 3)} | {row.get('placeboEdge')} | {klass} |"
        )
    out.extend(["", f"> {iteration.get('disclaimer')}", ""])
    return out


__all__ = [
    "ARTIFACT_PREFIX",
    "DISCLAIMER",
    "artifact_stem",
    "render_iteration_section",
    "week_bounds",
]
