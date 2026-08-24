"""复盘材料生成(纯确定性文案,⛔ 零 LLM —— 架构 §六明令这一层无 LLM 调用)。

不支持的判据字段不渲染为“计划外”或“违纪”结论。

⛔ **不留"本周未发现违纪"这类兜底句** —— 判据都不判了还说"没发现",是把
「这项已经不查了」讲成「你这周很干净」。K9 §六 给这一层的职责是**解析 / 装订 / 存档**;
好坏结论由用户带着材料去聊天框里得出,系统不代他下判断。

风格:写成连贯段落(确定性规则文案不写成枚举卡);逐回合明细由客户端从
`WeeklyReview.round_trips` / `closed_round_trips` 结构化字段自行渲染表格,
本模块只产出摘要叙述段。
"""

from __future__ import annotations

from neckline.review.reconcile import WeeklyReview


def build_material_text(review: WeeklyReview) -> str:
    stats = review.stats
    paragraphs = []

    paragraphs.append(
        f"【{review.week} 周复盘】统计区间 {review.week_start.strftime('%Y-%m-%d')} 至 "
        f"{review.week_end.strftime('%Y-%m-%d')}。"
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

    return "\n\n".join(paragraphs)


__all__ = ["build_material_text"]
