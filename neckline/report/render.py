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
from neckline.report.intel import IntelReport
from neckline.report.news_alerts import NewsAlertsReport
from neckline.report.sector_moneyflow import SectorMoneyflowReport
from neckline.report.sectors import SectorScore
from neckline.report.sentiment import SentimentDashboard
from neckline.report.watchlist_check import WatchlistCheckItem

_CATEGORY_LABEL = {
    "REDUCTION": "减持", "INVESTIGATION": "立案", "BLOWUP": "暴雷", "REGULATORY": "监管",
}

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
    intel: Optional[IntelReport] = None,
    sector_moneyflow: Optional[SectorMoneyflowReport] = None,
    news_alerts: Optional[NewsAlertsReport] = None,
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
    parts.append(_render_intel(intel))
    parts.append(_render_sector_moneyflow(sector_moneyflow))
    parts.append(_render_news_alerts(news_alerts))
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


def _intel_rank_line(c: Candidate) -> Optional[str]:
    """候选情报排序理由(v1.3-③-C3;K1 老候选 `intel_rank` 空 → None 不渲染)。"""
    ir = c.intel_rank or {}
    if not ir:
        return None
    parts: List[str] = []
    sf = ir.get("sectorFlow")
    if sf is not None:
        parts.append(f"板块资金净流入 {sf:+,.0f} 万元")
    persist = ir.get("themePersistDays")
    if persist is not None:
        fresh = {0: "未启动", 1: "第1天(新鲜)", 2: "第2天(警惕)", 3: "第3天(警惕)"}.get(persist, f"第{persist}天")
        parts.append(f"题材持续 {fresh}")
    if ir.get("highElasticity"):
        parts.append("高弹板块(GEM/STAR,20cm 易波动,自行判断)")
    return "- 情报排序理由:" + " · ".join(parts) if parts else None


def _k4_flag_line(c: Candidate) -> Optional[str]:
    """K4 安检打标(avoid_flag 命中;hard_cut 命中的票已在生成时拦出池、不到这里)。"""
    if not c.k4_flags:
        return None
    return "- ⚠ K4 安检标注(机器不禁、供你判断):" + "、".join(c.k4_flags)


def _render_candidates(candidates: List[Candidate], judged: Dict[str, JudgeResult], top_n_judged: int) -> str:
    # v1.3-③-C3 语义变更:候选 = 「过完安检、值得关注的票」非「会涨的票」,终选在用户
    # (§2.3)。生成源从 K1 entry mask 退役 → 情报筛选四步管线;四件套保留但是**情报维度**,
    # 不是买入信号(不标「推荐买点」)。
    lines = ["## 候选(情报筛选 · 过完安检、值得关注的票,非买入信号,终选在你)", ""]
    if not candidates:
        lines.append("今日无候选通过情报筛选(无热门板块成员过安检,或数据缺失)。")
        lines.append("")
        return "\n".join(lines)
    lines.append(
        "> 候选 = 五板块常驻 + 当日暴起板块的成员里,过完卫生线/非次新/趋势向上安检、"
        "再过 K4 避坑安检(hard_cut 已拦出池、avoid_flag 打标)的票;**不是回测选出的买入信号**,"
        "四件套为情报维度参考,买卖与终选在你(§2.3)。"
    )
    lines.append("")

    judged_list = candidates[:top_n_judged]
    scored_only = candidates[top_n_judged:]

    lines.append(f"### 前 {len(judged_list)} 只 · LLM 逻辑审判")
    lines.append("")
    for c in judged_list:
        jr = judged.get(c.ts_code)
        lines.append(f"#### {c.rank}. {c.name}({c.ts_code}) —— 展示分 {c.score:.1f}")
        lines.append("")
        lines.append(f"- 现价:{c.close:.2f} 元 · 形态标签:{'、'.join(c.pattern_tags) if c.pattern_tags else '无'}")
        if c.hot_sectors:
            lines.append(f"- 命中热门板块:{'、'.join(c.hot_sectors)}")
        intel_line = _intel_rank_line(c)
        if intel_line:
            lines.append(intel_line)
        k4_line = _k4_flag_line(c)
        if k4_line:
            lines.append(k4_line)
        lines.append(f"- **参考买点(非推荐)**:{c.entry_plan}")
        lines.append(f"- **参考止损**:{c.stop_loss}")
        lines.append(f"- **参考目标**:{c.target}")
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
        lines.append(f"### 后 {len(scored_only)} 只 · 仅情报排序与形态标签(不耗 LLM)")
        lines.append("")
        lines.append("| 排名 | 代码 | 名称 | 展示分 | 题材天数 | 高弹 | K4标注 | 形态标签 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for c in scored_only:
            tags = "、".join(c.pattern_tags) if c.pattern_tags else "无"
            ir = c.intel_rank or {}
            persist = ir.get("themePersistDays", "-")
            he = "是" if ir.get("highElasticity") else ""
            k4 = "、".join(c.k4_flags) if c.k4_flags else ""
            lines.append(f"| {c.rank} | {c.ts_code} | {c.name} | {c.score:.1f} | {persist} | {he} | {k4} | {tags} |")
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


def _render_intel(intel: Optional[IntelReport]) -> str:
    """情报节 C1(plan §五 v1.3-③-C1):复盘情报件——涨跌幅榜/涨停梯队/跌停榜/
    大盘量能/最强题材/题材持续天数/市值偏好/涨跌停制度偏好。**证据强度标注**
    (硬要求①)在小节标题里就写明强/弱,不是只藏在字段里。"""
    lines = ["## 情报 · 复盘情报件(C1)", ""]
    if intel is None:
        lines.append("情报节未生成。")
        lines.append("")
        return "\n".join(lines)
    lines.append(f"*{intel.evidence_note}*")
    lines.append("")
    if intel.warnings:
        lines.append("⚠ " + "；".join(intel.warnings))
        lines.append("")

    lines.append("### 涨跌幅榜(EOD 硬数据)")
    lines.append("")
    if not intel.gainers and not intel.losers:
        lines.append("当日无数据。")
        lines.append("")
    else:
        lines.append("| 涨幅榜 | 涨跌幅 | | 跌幅榜 | 涨跌幅 |")
        lines.append("|---|---|---|---|---|")
        for i in range(max(len(intel.gainers), len(intel.losers))):
            g = intel.gainers[i] if i < len(intel.gainers) else None
            l = intel.losers[i] if i < len(intel.losers) else None
            g_cell = f"{g.name}({g.ts_code})" if g else ""
            g_pct = f"{g.pct_chg:+.2f}%" if g else ""
            l_cell = f"{l.name}({l.ts_code})" if l else ""
            l_pct = f"{l.pct_chg:+.2f}%" if l else ""
            lines.append(f"| {g_cell} | {g_pct} | | {l_cell} | {l_pct} |")
        lines.append("")

    lines.append("### 涨停梯队 / 跌停榜(EOD 硬数据)")
    lines.append("")
    if intel.limit_up_ladder:
        ladder_str = "、".join(f"{r.consec_days}连板×{r.count}只" for r in intel.limit_up_ladder)
        lines.append(f"- 涨停梯队:{ladder_str}")
    else:
        lines.append("- 涨停梯队:当日无涨停股。")
    if intel.limit_down:
        shown = "、".join(f"{m.name}({m.ts_code}){m.pct_chg:+.2f}%" for m in intel.limit_down[:15])
        more = f" 等,共 {intel.limit_down_total_count} 只" if intel.limit_down_total_count > 15 else ""
        lines.append(f"- 跌停榜(前 15):{shown}{more}")
    else:
        lines.append("- 跌停榜:当日无跌停股。")
    lines.append("")

    lines.append("### 大盘量能(EOD 硬数据)")
    lines.append("")
    mv = intel.market_volume
    if mv is None:
        lines.append("当日 index_daily 数据缺失,已留空。")
    else:
        lines.append(
            f"- 沪深两市合计成交额:{mv.total_amount_yi:,.1f} 亿元"
            f"(上证 {mv.sh_amount_yi:,.1f} 亿 + 深证 {mv.sz_amount_yi:,.1f} 亿)"
        )
        lines.append(f"- 5 日均成交额:{mv.ma5_amount_yi:,.1f} 亿元(样本 {mv.sample_days} 个交易日)")
    lines.append("")

    lines.append("### 最强题材(概念板块成分依赖,弱证据,仅供参考)")
    lines.append("")
    if intel.excluded_boards_note:
        lines.append(f"*{intel.excluded_boards_note}*")
        lines.append("")
    if not intel.top_themes:
        lines.append("当日无题材数据。")
        lines.append("")
    else:
        lines.append("| 板块 | 板块年龄 | 20日动量 | 持续性 | 核心龙头 |")
        lines.append("|---|---|---|---|---|")
        for t in intel.top_themes:
            leaders = "、".join(f"{l.name}{'(涨停)' if l.is_limit_up else ''}{l.pct_chg:+.1f}%" for l in t.leaders)
            lines.append(f"| {t.name} | {t.board_age} 天 | {t.ret_20d:+.1%} | {t.persistence_label} | {leaders or '无'} |")
        lines.append("")
        if intel.theme_persistence_distribution:
            dist = "、".join(f"{k} {v} 个" for k, v in intel.theme_persistence_distribution.items())
            lines.append(f"*题材持续天数分布:{dist}*")
            lines.append("")

    lines.append("### 市值偏好 / 涨跌停制度偏好(当日涨停股,EOD 硬数据)")
    lines.append("")
    if intel.mv_preference:
        mv_str = "、".join(f"{b.label} {b.count} 只({b.pct_of_total:.0%})" for b in intel.mv_preference)
        lines.append(f"- 市值分布:{mv_str}")
    else:
        lines.append("- 市值分布:当日无数据。")
    if intel.limit_regime_preference:
        regime_str = "、".join(f"{b.label} {b.count} 只({b.pct_of_total:.0%})" for b in intel.limit_regime_preference)
        lines.append(f"- 涨跌停幅度分布:{regime_str}")
    else:
        lines.append("- 涨跌停幅度分布:当日无数据。")
    lines.append("")
    return "\n".join(lines)


def _render_sector_moneyflow(report: Optional[SectorMoneyflowReport]) -> str:
    """情报节 C2(plan §五 v1.3-③-C2):板块资金流展示——**拥挤情报件,非选股
    信号**(K2 判决:板块层有效但无次日领先性)。"""
    lines = ["## 情报 · 板块资金流(C2,拥挤参考,非选股信号)", ""]
    if report is None or not report.available:
        reason = report.unavailable_reason if report else "板块资金流节未生成。"
        lines.append(reason)
        lines.append("")
        return "\n".join(lines)

    lines.append(f"*{report.evidence_note}*")
    lines.append("")
    if report.excluded_boards_note:
        lines.append(f"*{report.excluded_boards_note}*")
        lines.append("")
    if not report.top_inflow and not report.top_outflow:
        lines.append("当日无板块命中 moneyflow_dc,已留空。")
        lines.append("")
        return "\n".join(lines)

    lines.append("| 净流入榜 | 净流入(万元) | | 净流出榜 | 净流入(万元) |")
    lines.append("|---|---|---|---|---|")
    for i in range(max(len(report.top_inflow), len(report.top_outflow))):
        a = report.top_inflow[i] if i < len(report.top_inflow) else None
        b = report.top_outflow[i] if i < len(report.top_outflow) else None
        a_cell = f"{a.name}" if a else ""
        a_val = f"{a.net_inflow_wan:+,.0f}" if a else ""
        b_cell = f"{b.name}" if b else ""
        b_val = f"{b.net_inflow_wan:+,.0f}" if b else ""
        lines.append(f"| {a_cell} | {a_val} | | {b_cell} | {b_val} |")
    lines.append("")
    return "\n".join(lines)


def _render_news_alerts(report: Optional[NewsAlertsReport]) -> str:
    """消息面节(C4,plan §五 v1.3-③-C4):持仓 + 自选票的减持/立案/暴雷/监管
    扫描。**先亮扫描状态,再列命中**——「没扫到」(未激活/调用失败)与「扫了
    没有」(确认无此类消息)必须能区分开(§硬要求),不能让读者把"下面没有列
    出任何条目"直接当成"确认干净"。"""
    lines = ["## 消息面(C4,持仓 + 自选票扫描:减持/立案/暴雷/监管)", ""]
    if report is None:
        lines.append("消息面节未生成。")
        lines.append("")
        return "\n".join(lines)
    lines.append(f"*{report.evidence_note}*")
    lines.append("")

    for s in report.scan_statuses:
        label = {"tushare_holdertrade": "减持(TuShare 结构化)", "llm": "立案/暴雷/监管(LLM 联网搜索)"}.get(
            s.source, s.source
        )
        if not s.scanned:
            lines.append(f"- ⚠ {label}:**本次未扫描**({s.reason or '原因未知'})——不代表确认无此类消息。")
        elif s.reason:
            lines.append(f"- {label}:已扫描,但 {s.reason}")
        else:
            extra = f"(标的数 {s.codes_total})" if s.codes_total else ""
            lines.append(f"- {label}:已扫描{extra},以下为命中(空 = 确认无此类消息)。")
    lines.append("")

    if not report.items:
        lines.append("扫描范围内(持仓 + 自选)未发现命中条目——请结合上方扫描状态判断是"
                     "「确认干净」还是「本次未扫描」。")
        lines.append("")
        return "\n".join(lines)

    lines.append("| 代码 | 名称 | 类别 | 摘要 | 来源 |")
    lines.append("|---|---|---|---|---|")
    for it in report.items:
        cat = _CATEGORY_LABEL.get(it.category, it.category)
        lines.append(f"| {it.ts_code} | {it.name} | {cat} | {it.summary} | {it.source} |")
    lines.append("")
    return "\n".join(lines)


__all__ = ["render_markdown"]
