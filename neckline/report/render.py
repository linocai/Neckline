"""盘后报告 markdown 渲染(plan 2.5)。纯函数,吃 `pipeline.build_report` 组装好的
结构化数据,产出 markdown 全文——不碰任何 I/O。§2.7 硬约束(LLM 输出自由对话体,
禁模板卡)只管 LLM 叙述**本身**的文风;报告整体版式(标题/表格)是系统输出、不是
LLM 输出,可以用 markdown 标题与表格排版,但 LLM 审判的叙述段落必须原文整段
呈现,不得拆解塞回枚举卡片里。
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from neckline.llm.judge import JudgeResult, VERDICT_PASS, VERDICT_VETO
from neckline.report.candidates import Candidate
from neckline.report.sectors import SectorScore
from neckline.report.sentiment import SentimentDashboard
from neckline.report.watchlist_check import WatchlistCheckItem

_VERDICT_BADGE = {VERDICT_PASS: "✅ 通过", VERDICT_VETO: "🚫 否决"}


def render_markdown(
    *,
    trade_date: date,
    strategy_version: str,
    generated_at: str,
    sentiment: SentimentDashboard,
    sectors: List[SectorScore],
    candidates: List[Candidate],
    judged: Dict[str, JudgeResult],
    top_n_judged: int,
    watchlist_check: Optional[List[WatchlistCheckItem]] = None,
) -> str:
    parts: List[str] = []
    parts.append(f"# Neckline 盘后报告 · {trade_date.isoformat()}")
    parts.append("")
    parts.append(f"*生成时间(UTC):{generated_at} · 策略大脑版本:`{strategy_version}` · 16:00 后 A 股盘后数据稳定*")
    parts.append("")
    parts.append(
        "> 规则 v1 是一套经回测验证的**减损纪律系统,不是正 alpha**(见 `research/stage1_report.md`)。"
        "本报告的候选排序与评分只是展示排序,不构成收益预测,买卖决策请以纪律章程为准。"
    )
    parts.append("")

    parts.append(_render_sentiment(sentiment))
    parts.append(_render_sectors(sectors))
    parts.append(_render_candidates(candidates, judged, top_n_judged))
    parts.append(_render_watchlist(watchlist_check or []))
    return "\n".join(parts)


def _render_sentiment(s: SentimentDashboard) -> str:
    lines = ["## 情绪仪表盘", ""]
    lines.append(f"- **明日仓位额度:{s.position_quota}**")
    lines.append(f"- 涨停家数:{s.limit_up_count} · 跌停家数:{s.limit_down_count} · 炸板率:{s.zaban_rate:.1%} · 最高连板:{s.max_consec_limit_up} 板")
    if s.prev_limit_up_premium_avg is not None:
        lines.append(f"- 昨日涨停股今日平均溢价:{s.prev_limit_up_premium_avg:+.2%}(样本 {s.prev_limit_up_sample} 只)")
    else:
        lines.append("- 昨日涨停股今日平均溢价:无数据(昨日无涨停股或数据缺失)")
    lines.append(f"- {s.quota_reason}")
    lines.append("")
    return "\n".join(lines)


def _render_sectors(sectors: List[SectorScore]) -> str:
    lines = ["## 强势板块(软加权展示,不圈死选股)", ""]
    if not sectors:
        lines.append("今日无概念板块数据(`ths_daily.parquet` 缺失或未覆盖该日)。")
        lines.append("")
        return "\n".join(lines)
    lines.append("| 排名 | 板块 | 20日动量 | 板块年龄(连续站上MA20天数) | 报告层加分 |")
    lines.append("|---|---|---|---|---|")
    for s in sectors:
        lines.append(f"| {s.rank} | {s.name} | {s.ret_20d:+.1%} | {s.board_age} | {s.bonus:+.1f} |")
    lines.append("")
    lines.append(
        "*板块只加分不圈死——全市场强势形态票均可入池;年龄加分只对启动 1-5 天的板块生效"
        "(阶段1研究:此信号弱,报告层软加权,不进硬评分门槛,见 `research/stage1_report.md` P2 节)。*"
    )
    lines.append("")
    return "\n".join(lines)


def _render_candidates(candidates: List[Candidate], judged: Dict[str, JudgeResult], top_n_judged: int) -> str:
    lines = ["## 候选", ""]
    if not candidates:
        lines.append("今日无候选通过母战法规则筛选。")
        lines.append("")
        return "\n".join(lines)

    judged_list = candidates[:top_n_judged]
    scored_only = candidates[top_n_judged:]

    lines.append(f"### 前 {len(judged_list)} 只 · LLM 逻辑审判")
    lines.append("")
    for c in judged_list:
        jr = judged.get(c.ts_code)
        lines.append(f"#### {c.rank}. {c.name}({c.ts_code}) —— 排序分 {c.score:.1f}")
        lines.append("")
        lines.append(f"- 现价:{c.close:.2f} 元 · 形态标签:{'、'.join(c.pattern_tags) if c.pattern_tags else '无'}")
        if c.hot_sectors:
            lines.append(f"- 命中热门板块:{'、'.join(c.hot_sectors)}")
        lines.append(f"- **买点**:{c.entry_plan}")
        lines.append(f"- **止损**:{c.stop_loss}")
        lines.append(f"- **目标**:{c.target}")
        lines.append(f"- **证伪条件**:{c.invalidation_text}")
        lines.append("")
        if jr is not None:
            badge = _VERDICT_BADGE.get(jr.verdict, f"⏸ {jr.verdict}")
            lines.append(f"**LLM 审判({jr.provider or '未激活'}){' · ' + jr.model if jr.model else ''}:{badge}**")
            lines.append("")
            lines.append(jr.narrative)
            if jr.search_hits:
                lines.append("")
                lines.append("联网搜索来源:" + "、".join(f"[{h.title or h.link}]({h.link})" for h in jr.search_hits if h.link))
        else:
            lines.append("**LLM 审判:未执行**(异常状态——前10只理应全部审判,请检查 pipeline)。")
        lines.append("")
        lines.append("---")
        lines.append("")

    if scored_only:
        lines.append(f"### 后 {len(scored_only)} 只 · 仅评分与形态标签(不耗 LLM)")
        lines.append("")
        lines.append("| 排名 | 代码 | 名称 | 排序分 | 形态标签 |")
        lines.append("|---|---|---|---|---|")
        for c in scored_only:
            tags = "、".join(c.pattern_tags) if c.pattern_tags else "无"
            lines.append(f"| {c.rank} | {c.ts_code} | {c.name} | {c.score:.1f} | {tags} |")
        lines.append("")

    return "\n".join(lines)


def _render_watchlist(items: List[WatchlistCheckItem]) -> str:
    """自选体检节(plan §2.3 v1.1 拍板 / §五 v1.1-C.3)。**独立一节,不是候选**——
    标题与候选节明确分开,不与候选榜混排。"""
    lines = ["## 自选体检(用户自选池,独立于候选榜)", ""]
    if not items:
        lines.append("自选池为空(App「自选」板块可添加)。")
        lines.append("")
        return "\n".join(lines)

    for it in items:
        light = "🟢 可动" if it.green_light else "🔴 禁买"
        badge = " · 🔔 状态变化" if it.status_changed else ""
        pin = " · 📌 已点名" if it.pinned else ""
        lines.append(f"#### {it.name}({it.ts_code}){badge}{pin}")
        lines.append("")
        if not it.has_data:
            lines.append(f"- {it.disqualifiers[0] if it.disqualifiers else '当日无数据。'}")
            lines.append("")
            lines.append("---")
            lines.append("")
            continue
        lines.append(
            f"- 现价:{it.close:.2f} 元 · 展示排序分:{it.score:.1f} · 纪律红绿灯:{light}"
            f" · 形态标签:{'、'.join(it.pattern_tags) if it.pattern_tags else '无'}"
        )
        if it.disqualifiers:
            lines.append("- 禁买原因:" + ";".join(it.disqualifiers))
        if it.hot_sectors:
            lines.append(f"- 命中热门板块:{'、'.join(it.hot_sectors)}")
        if it.buy_point_triggered:
            lines.append(f"- **买点**:{it.entry_plan}")
            lines.append(f"- **止损**:{it.stop_loss}")
            lines.append(f"- **目标**:{it.target}")
            lines.append(f"- **证伪条件**:{it.invalidation_text}")
        else:
            lines.append("- 今日未触发母战法买点(仅供关注)。")
        lines.append("")
        if it.llm_judgment is not None:
            jr = it.llm_judgment
            badge2 = _VERDICT_BADGE.get(jr["verdict"], f"⏸ {jr['verdict']}")
            lines.append(f"**LLM 审判(状态变化 / 已点名才审):{badge2}**")
            lines.append("")
            lines.append(jr["narrative"])
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


__all__ = ["render_markdown"]
