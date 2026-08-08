"""复盘材料生成(plan 4D.3)。**本次任务范围内只做确定性材料**——任务指令原文:
"展示对账周报(自由叙述+表格,禁模板腔的 LLM 部分本块不做,纯确定性输出即可)"。

plan 原文允许"叙述性复盘材料可选叠加 LLM(自由对话体,缺 key 降级为纯确定性
材料)",但本次任务明确指示该 LLM 叠加层不在本块交付范围——现状即"缺 key
降级"路径本身,`build_material_text` 产出的就是这份确定性材料,不是半成品。
未来若要叠加 LLM 反思叙述,应遵循 `neckline.llm.judge` 已确立的降级链姿势
(`get_provider()` 为 None → 跳过、不阻塞)。

风格:写成连贯段落(§2.7 精神下的确定性文案惯例,同 `report/candidates.py` 的
`entry_plan_text`/`target_text`——确定性规则文案也不写成枚举卡),表格化的明细
(回合/计划核对/止损分类)由客户端从 `WeeklyReview.round_trips`/`plan_checks`/
`stop_discipline` 结构化字段自行渲染表格,本模块只产出摘要叙述段。
"""

from __future__ import annotations

from neckline.review.reconcile import (
    STOP_BREACHED,
    STOP_KEPT,
    WeeklyReview,
)


def build_material_text(review: WeeklyReview) -> str:
    stats = review.stats
    paragraphs = []

    paragraphs.append(
        f"【{review.week} 周复盘】统计区间 {review.week_start.strftime('%Y-%m-%d')} 至 "
        f"{review.week_end.strftime('%Y-%m-%d')}。"
    )

    # v1.4-⑥-A:本周若发生过章程切换,**必须显式注明切换时刻并分段计数**——否则用户看到
    # 一份混着两版阈值判出来的清单,却以为整周按 `strategy_version` 那一版判(P1-4 的病根
    # 之一是"判据换了但没人说")。文案的唯一来源是 `CharterSwitch.note`(在 reconcile 里
    # 连同分段计数一起算好),本模块不重新拼一遍分段口径。
    if review.charter_switches:
        paragraphs.append(
            "".join(sw.note for sw in review.charter_switches)
            + "本周的纪律判定**按成交时刻逐笔取当时现役的章程**,切换前后各按各的阈值判,"
            "上面的违纪清单已是分段判定后的结果。"
        )

    if review.forced_review:
        paragraphs.append(f"⚠ 强制复盘触发:{review.forced_review_reason}")

    if stats is not None:
        if stats.closed_count == 0:
            paragraphs.append("本周没有已平仓的回合(可能只有新开仓,或本周无成交),暂无胜率/盈亏统计。")
        else:
            pf_txt = f"{stats.profit_factor:.2f}" if stats.profit_factor not in (float("inf"),) else "∞(本周无亏损回合)"
            plr_txt = f"{stats.profit_loss_ratio:.2f}" if stats.profit_loss_ratio not in (float("inf"),) else "∞(本周无亏损回合)"
            paragraphs.append(
                f"本周平仓 {stats.closed_count} 回合,胜率 {stats.win_rate:.1%},盈利因子 {pf_txt},"
                f"盈亏比 {plr_txt},合计费用 ¥{stats.total_fees:,.0f},净实现盈亏 ¥{stats.realized_pnl:,.0f}"
                f"(其中亏损合计 ¥{stats.realized_loss:,.0f})。"
            )
        if stats.open_count:
            paragraphs.append(f"另有 {stats.open_count} 笔回合本次上传数据范围内仍未平仓,不计入本周盈亏统计。")

    off_plan = [c for c in review.plan_checks if c.plan_status.startswith("计划外")]
    no_report = [c for c in review.plan_checks if c.plan_status.startswith("无报告数据")]
    ledger_missing = [c for c in review.plan_checks if c.ledger_status.startswith("台账缺失")]
    ledger_mismatch = [c for c in review.plan_checks if c.ledger_status.startswith("台账记录价格不符")]
    if review.plan_checks:
        if not off_plan and not no_report:
            paragraphs.append(f"本周 {len(review.plan_checks)} 笔买入全部在当日报告候选或问询台海选池范围内,没有计划外单。")
        else:
            names = "、".join(f"{c.ts_code}({c.name})" for c in off_plan[:8])
            extra = f" 等共 {len(off_plan)} 笔" if len(off_plan) > 8 else ""
            if off_plan:
                paragraphs.append(f"计划外买入:{names}{extra},未经系统当日候选或问询台海选池放行,建议留意选股来源是否合规。")
            if no_report:
                paragraphs.append(f"另有 {len(no_report)} 笔买入所在交易日未查到系统报告存档,无法核对计划内外。")
        if ledger_missing:
            paragraphs.append(f"{len(ledger_missing)} 笔买入未在系统持仓台账登记,意味着这些仓位未被盘中持仓哨兵的止损/止盈预警覆盖。")
        if ledger_mismatch:
            paragraphs.append(f"{len(ledger_mismatch)} 笔买入的台账记录价格与交割单不符,建议核对是否录入有误。")

    breaches = [(rt, note) for rt, kind, note in review.stop_discipline if kind == STOP_BREACHED]
    kept = [rt for rt, kind, note in review.stop_discipline if kind == STOP_KEPT]
    if review.stop_discipline:
        if breaches:
            lines = "；".join(f"{rt.ts_code}({rt.name}) {rt.sell_date}卖出" for rt, _ in breaches[:8])
            paragraphs.append(f"止损纪律警示:{len(breaches)} 笔回合疑似破 -5% 止损未按纪律离场({lines})——这是历史归因中第一大死因,请重点复盘。")
        elif kept:
            paragraphs.append(f"止损纪律良好:本周 {len(kept)} 笔触及止损容差带的回合均在容差带内离场。")
        else:
            paragraphs.append("本周平仓回合均未触及止损容差带,无需止损纪律判定。")

    if review.discipline_violations:
        lines = "；".join(review.discipline_violations[:10])
        more = f"(其余 {len(review.discipline_violations) - 10} 条见明细)" if len(review.discipline_violations) > 10 else ""
        paragraphs.append(f"章程执行违纪清单:{lines}{more}")
    else:
        paragraphs.append("本周未发现仓位纪律(单笔上限/持仓只数/敞口/禁买规则)方面的违纪。")

    return "\n\n".join(paragraphs)


__all__ = ["build_material_text"]
