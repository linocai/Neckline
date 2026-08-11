"""**篮子日报** markdown 渲染(plan §五 V2-⑭-A)。纯函数,吃 `pipeline.build_report`
组装好的结构化数据,产出 markdown 全文——不碰任何 I/O、不重算任何判据。§2.7 硬约束
(LLM 输出自由对话体,禁模板卡)只管 LLM 叙述**本身**的文风;报告整体版式(标题/表格)
是系统输出、不是 LLM 输出,但 LLM 的叙述段落(篮子卡的 `narrative` / 复盘的 `llmText`)
必须**原文整段呈现**,不得拆解塞回枚举卡片里。

**五段结构(顺序定死,⑭-A)**

    ① 情绪与市场语境 → ② 持仓体检(先管住手里的)→ ③ 今日篮子(V2.1 起 T1/T2,每篮一张卡)
    → ③b 今日未定档篮子 → ④ 昨日篮子复盘 → ⑤ 数据新鲜度与降级披露

**三条不许动的纪律**

1. **每段独立**:某段数据缺席只让那一段写「本段未取得 + 原因」,其余四段照出
   (§铁律「任何一段异常都不许让当日无报告」)。
2. **③ 三档全部可空是合法输出**(⑥-b-B):「今日 T1 为空」如实写出来,⛔ 不许为了让
   报告好看而放宽任何一条质量线,也⛔ 不许把空档位藏起来。
3. **③b 的两个原因码分开写**(⑥-b-C):`capacity_overflow`(机会多到装不下)与
   `below_quality_line`(今天没什么好货)是**相反的市场结论**,合并成「未入选」就把
   两件事讲成了一件;零溢出时**这一节仍在**(节在 = 算过了)。

**语义红线(§2.8-C,每处文案自查)**:排序 / Tier = **注意力优先级,不是收益预测**;
禁「推荐买入 / 建议买入 / 看好 / 值得买」;参考件每处带「参考、非指令」;离场参考
区间**不许**写成止盈线。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence

# v1.4-⑩-F:只借类型(`IndustryStrengthFreshness` 是纯 dataclass);本模块仍不碰任何
# I/O —— 新鲜度由 pipeline 查好后原样传进来,render 只读它的 `stale`/`note()`。
from neckline.report.industry_strength_store import IndustryStrengthFreshness
# V2-⑭-A:篮子日报的三段视图模型(纯 dataclass,同上只借类型;③b 原因码的**展示
# 文案**映射也在那里 —— 码本身的唯一源在 `selection/tier.py`,本模块两边都不重定义)。
from neckline.report.basket_daily import DROPPED_REASON_LABEL, BasketDaily
# V2-⑭-A:扫描层新鲜度(④ 的 `freshness.py` 早就算好了,⑭ 才接线进报告——见该模块头
# 「本文件只提供计算逻辑,不接线」)。同样只借类型。
from neckline.scan.freshness import ScanLayerFreshness
# v1.5-③-C:持仓体检节——只借类型(`HoldingK4Item`/`HoldingK4Hit` 纯 dataclass),
# 本模块仍不碰任何 I/O,数据由 pipeline 已算好的 `holding_k4_check` 原样传入渲染。
from neckline.report.holding_k4_check import HoldingK4Item
from neckline.report.intel import IntelReport
from neckline.report.news_alerts import NewsAlertsReport
from neckline.report.score_display import contribution_line
from neckline.report.sector_moneyflow import SectorMoneyflowReport
from neckline.report.sectors import SectorDataFreshness, SectorScore
from neckline.report.sentiment import SentimentDashboard
# v1.5-③-C:两档时间退出状态码单一源(§三 铁律「唯一源,不硬编字面量」)——只借
# 字符串常量,不借任何函数(判定逻辑不在本模块重跑,`HoldingK4Item.time_exit_state`
# 已是 pipeline 算好的定案值,render 只负责把它翻成人读文案)。
from neckline.sentinel.precall import (
    HARD_CAP_EXIT,
    HOLDING,
    PROFIT_EXEMPT,
    SUSPENDED_HOLD,
    TIME_EXIT_NEXT_DAY,
)

_CATEGORY_LABEL = {
    "REDUCTION": "减持", "INVESTIGATION": "立案", "BLOWUP": "暴雷", "REGULATORY": "监管",
}

def render_markdown(
    *,
    trade_date: date,
    strategy_version: str,
    generated_at: str,
    sentiment: SentimentDashboard,
    sectors: List[SectorScore],
    holding_k4_check: Optional[List[HoldingK4Item]] = None,
    intel: Optional[IntelReport] = None,
    sector_moneyflow: Optional[SectorMoneyflowReport] = None,
    news_alerts: Optional[NewsAlertsReport] = None,
    sector_freshness: Optional[SectorDataFreshness] = None,
    industry_freshness: Optional[IndustryStrengthFreshness] = None,
    scan_freshness: Optional[ScanLayerFreshness] = None,
    basket_daily: Optional[BasketDaily] = None,
) -> str:
    parts: List[str] = []
    parts.append(f"# Neckline 篮子日报 · {trade_date.isoformat()}")
    parts.append("")
    pack = (basket_daily.pack_version if basket_daily is not None else None) or "(本报告无篮子卡,选股包版本不适用)"
    parts.append(
        f"*生成时间(UTC):{generated_at} · 纪律章程版本:`{strategy_version}` · "
        f"选股包版本:`{pack}` · 16:00 后 A 股盘后数据稳定*"
    )
    parts.append("")
    parts.append(
        "> **排序 / Tier = 注意力优先级,不是收益预测**;T1 ≠ 最会涨,终选权在你。"
        "本报告的一切 LLM 产出(篮子叙述 / 竞价剧本 / 复盘解释)都是**参考件、非指令**;"
        "纪律只住纪律章程,系统永不下单。"
    )
    parts.append("")
    # 顶部醒目告警三条,**逐条独立、不合并** —— 它们是三个互不相干的故障
    # (概念板块日更 / `industry_strength_daily` 日更 / 扫描层三表批算),合并成一句
    # 读者就分不清哪个坏了。详情在 ⑤ 数据新鲜度节,这里只是让人在看任何结论**之前**
    # 先知道底下的数据是旧的。
    for line in _freshness_alert_lines(sector_freshness, industry_freshness, scan_freshness):
        parts.append(line)
        parts.append("")

    parts.append(_render_market_context(sentiment, sectors, intel, sector_moneyflow,
                                        news_alerts, sector_freshness))
    parts.append(_render_holding_check(holding_k4_check or []))
    parts.append(_render_today_baskets(basket_daily))
    parts.append(_render_dropped_baskets(basket_daily))
    parts.append(_render_out_candidates(basket_daily))
    parts.append(_render_basket_reviews(basket_daily))
    parts.append(_render_data_freshness(sector_freshness, industry_freshness, scan_freshness,
                                        basket_daily, news_alerts))
    return "\n".join(parts)


def _freshness_alert_lines(
    sector_freshness: Optional[SectorDataFreshness],
    industry_freshness: Optional[IndustryStrengthFreshness],
    scan_freshness: Optional[ScanLayerFreshness],
) -> List[str]:
    out: List[str] = []
    if sector_freshness is not None and sector_freshness.stale:
        out.append(f"> 🚨 **板块数据过期告警**:{sector_freshness.note()}")
    if industry_freshness is not None and industry_freshness.stale:
        out.append(f"> 🚨 **行业强度数据未就绪**:{industry_freshness.note()}")
    # V2-④ 的第三件独立故障:三张扫描层预计算表没跑 → 今日**根本没有驱动种子**,
    # 篮子为空是"没看"不是"没有"。⛔ 不与上面两条合并。
    if scan_freshness is not None and scan_freshness.stale:
        out.append(f"> 🚨 **市场扫描层未就绪**:{scan_freshness.note()}")
    return out


# —— ① 情绪与市场语境 ————————————————————————————————————————————————
#    ⑬-8 判定「拆候选侧、留情报侧」之后留下的五件(情绪 / 强势板块 / 情报件 C1 /
#    板块资金流 C2 / 消息面 C4)在 ⑭-A 归并成这一段 —— 它们回答的是同一个问题:
#    「今天这个市场是什么状态」。段内子标题保留,便于对照历史报告。

def _render_market_context(
    sentiment: SentimentDashboard,
    sectors: List[SectorScore],
    intel: Optional[IntelReport],
    sector_moneyflow: Optional[SectorMoneyflowReport],
    news_alerts: Optional[NewsAlertsReport],
    sector_freshness: Optional[SectorDataFreshness],
) -> str:
    parts = ["## ① 情绪与市场语境", ""]
    parts.append(_render_sentiment(sentiment))
    parts.append(_render_sectors(sectors, sector_freshness))
    parts.append(_render_intel(intel, sector_freshness))
    parts.append(_render_sector_moneyflow(sector_moneyflow))
    parts.append(_render_news_alerts(news_alerts))
    return "\n".join(parts)


def _render_sentiment(s: SentimentDashboard) -> str:
    lines = ["### 情绪仪表盘", ""]
    lines.append(f"- **明日仓位额度:{s.position_quota}**")
    lines.append(f"- 涨停家数:{s.limit_up_count} · 跌停家数:{s.limit_down_count} · 炸板率:{s.zaban_rate:.1%} · 最高连板:{s.max_consec_limit_up} 板")
    if s.prev_limit_up_premium_avg is not None:
        lines.append(f"- 昨日涨停股今日平均溢价:{s.prev_limit_up_premium_avg:+.2%}(样本 {s.prev_limit_up_sample} 只)")
    else:
        lines.append("- 昨日涨停股今日平均溢价:无数据(昨日无涨停股或数据缺失)")
    lines.append(f"- {s.quota_reason}")
    lines.append("")
    return "\n".join(lines)


def _render_sectors(sectors: List[SectorScore],
                    freshness: Optional[SectorDataFreshness] = None) -> str:
    lines = ["### 强势板块(软加权展示,不圈死选股)", ""]
    # v1.4-①-C:数据新鲜度脚注(lag>0 就说,不必等到 stale)——「今日无板块数据」这句话
    # 此前既可能是「今天真没行情」也可能是「板块表根本没更新」,两者必须能分开(§3.8)。
    note = freshness.note() if freshness is not None else ""
    if not sectors:
        lines.append("今日无概念板块数据(`ths_daily.parquet` 缺失或未覆盖该日)。")
        if note:
            lines.append("")
            lines.append(f"*{note}*")
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
    if note:
        lines.append("")
        lines.append(f"*{note}*")
    lines.append("")
    return "\n".join(lines)


# —— v1.5-③-C 持仓体检节(需求 9「今日计划拆两块:持仓股 / 候选列表」的 markdown
#    落地)——数据源 = pipeline 已算好的 `holding_k4_check`,零新增计算,只把
#    `HoldingK4Item` 已有字段渲染成人读文案。——————————————————————————————————
_TIME_EXIT_STATE_LABEL: Dict[str, str] = {
    HOLDING: "持有中",
    TIME_EXIT_NEXT_DAY: "时间退出(净浮盈非正,次日离场)",
    PROFIT_EXEMPT: "浮盈豁免(续持至硬上限)",
    HARD_CAP_EXIT: "浮盈硬上限(次日无条件离场)",
    SUSPENDED_HOLD: "判向挂起(停牌/无当日行情,复牌当日再定格)",
}


def _holding_k4_hits_line(item: HoldingK4Item) -> str:
    """K4 红黄牌(持仓侧口径 = `level` 强/普通警示,与候选侧 hard_cut/avoid_flag
    的红黄牌是不同的分类维度——持仓不存在"拦截出池",只有"警示级别",见
    `holding_k4_check.py` 模块头「分级」节)。"""
    if not item.hits:
        return "无命中"
    strong = [h.label for h in item.hits if h.level == "strong"]
    normal = [h.label for h in item.hits if h.level != "strong"]
    parts: List[str] = []
    if strong:
        parts.append("强警示:" + "、".join(strong))
    if normal:
        parts.append("普通警示(仅供参考):" + "、".join(normal))
    return "；".join(parts) if parts else "无命中"


def _render_holding_check(items: List[HoldingK4Item]) -> str:
    lines = ["## ② 持仓体检(先管住手里的)", ""]
    if not items:
        lines.append("今日无持仓。")
        lines.append("")
        return "\n".join(lines)
    for it in items:
        lines.append(f"#### {it.name}({it.ts_code})")
        lines.append("")
        if not it.has_data:
            # v1.4-①-B(§七 P0-2):当日无 EOD 行(停牌/数据缺口)→ 整份体检当天跳过,
            # `dataUnavailable` 如实标注——「没体检」与「体检过没命中」必须能分开(§3.8)。
            lines.append(f"- ⚠ 当日无 EOD 行情(停牌或数据缺口),本次体检跳过"
                          f"(D{it.d_count} 照常按交易日累计,不因停牌暂停计数)。")
            lines.append("")
            lines.append("---")
            lines.append("")
            continue
        state_label = _TIME_EXIT_STATE_LABEL.get(it.time_exit_state, it.time_exit_state)
        net_float_txt = f"{it.net_float:+.1f} 元" if it.net_float is not None else "未知"
        # V2.2-⑤:`max_hold_effective is None` = 现役章程无时间退出条款 → **如实说没有**,
        # ⛔ 不编一个「有效上限 D None」出来,也不拿默认 5 顶上。
        cap_txt = (f"(有效上限 D{it.max_hold_effective})" if it.max_hold_effective is not None
                   else "(本版章程无时间退出条款,D 计数只作记录)")
        lines.append(
            f"- D 计数:D{it.d_count}{cap_txt}"
            f" · 时间退出:{state_label}"
            f" · 净浮盈(扣双边费估算):{net_float_txt}"
        )
        if it.time_exit_locked_date:
            # 「D5 判一次定格」(审计 🔴-1):此后不再重判,硬上限例外——只如实展示定格
            # 发生的日期,不在本模块(纯函数、不碰 I/O)重算「晚了几天」这类需要交易日历
            # 的派生值。
            lines.append(f"- 时间退出判向已于 {it.time_exit_locked_date} 定格,此后不再重判(D15 硬上限例外)。")
        lines.append(f"- K4 红黄牌:{_holding_k4_hits_line(it)}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def _render_intel(intel: Optional[IntelReport],
                  freshness: Optional[SectorDataFreshness] = None) -> str:
    """情报节 C1(plan §五 v1.3-③-C1):复盘情报件——涨跌幅榜/涨停梯队/跌停榜/
    大盘量能/最强题材/题材持续天数/市值偏好/涨跌停制度偏好。**证据强度标注**
    (硬要求①)在小节标题里就写明强/弱,不是只藏在字段里。"""
    lines = ["### 情报 · 复盘情报件(C1)", ""]
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
    # v1.4-①-C:题材榜与题材持续天数直接建在 `ths_daily` 上 —— 板块数据过期时它们
    # **必须被显式标不可信**,而不是静默降级成一张看起来正常的旧榜单(§七 P0-3)。
    if freshness is not None and (freshness.stale or freshness.unavailable):
        lines.append(f"> ⚠️ **本小节不可信**:{freshness.note()}")
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
    lines = ["### 情报 · 板块资金流(C2,拥挤参考,非选股信号)", ""]
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
    """消息面节(C4,plan §五 v1.3-③-C4;⚠ V2-⑬-11 起扫描域只剩持仓)的减持/立案/暴雷/监管
    扫描。**先亮扫描状态,再列命中**——「没扫到」(未激活/调用失败)与「扫了
    没有」(确认无此类消息)必须能区分开(§硬要求),不能让读者把"下面没有列
    出任何条目"直接当成"确认干净"。"""
    lines = ["### 消息面(C4,减持/立案/暴雷/监管)", ""]
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
        # ⚠ V2-⑭-A 起扫描域 = **持仓 + 今日篮子成员**(⑬ 留下的"次级扫描域恒空"欠账
        # 在 ⑭-A 接线补上);文案跟着改口径,不能再只说"持仓"。
        lines.append("扫描范围内(持仓 + 今日篮子成员)未发现命中条目——请结合上方扫描状态判断是"
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


# —— ③ 今日篮子(V2.1 起 T1/T2,每篮一张卡)——————————————————————————————
#    数据 = ⑦ 在 D0 冻结的 `basket_cards.card_json`(经 `basket_daily.card_to_public_dict`
#    转 camel)。本节**零重算**:卡上每个数字要么机械算出、要么过了机械闸(夹逼 /
#    白名单 / 对拍),渲染层只负责把它们摆出来。


def _tier_title(tier: int) -> str:
    """档位标题 = `f"T{tier}"`(V2.1-②:由写死字典改成函数式兜底)。

    🔴 **历史回放里的 T3 必须仍显示** —— 报告渲染吃的是**冻结快照**,V2 时代的老报告
    里有 tier=3 的篮子;写死一张 `{1,2}` 的字典会让它们渲染成 `KeyError` 或凭空消失。
    函数式兜底对任何整数档位都给得出标题,天然向前向后都宽容。
    """
    return f"T{tier}"


_ROLE_LABEL = {"leader": "龙头", "core": "中军", "elastic": "弹性", "unknown": "未定"}


def _fmt_num(v: Any, fmt: str = "{:.2f}", dash: str = "—") -> str:
    """数值缺席一律 `—`,**绝不用 0 冒充**(§3.8「没有」与「没看」必须能分开)。"""
    if v is None:
        return dash
    try:
        return fmt.format(float(v))
    except (TypeError, ValueError):
        return str(v)


def _md_cell(v: Any, dash: str = "—") -> str:
    """一格 Markdown 表格文本:`|` 转义 + 换行折平。**凡是有可能装进 LLM 自由文本的
    表格格都要过这里** —— 一个未转义的竖线会把整张表切歪,而且是静默的
    (V2.2-③-C 起 ③b 的「差多少」那一格开始装模型写的位置判定理由)。"""
    if v is None or v == "":
        return dash
    return str(v).replace("|", "\\|").replace("\n", " ").strip() or dash


def _render_today_baskets(bd: Optional[BasketDaily]) -> str:
    lines = ["## ③ 今日篮子", ""]
    if bd is None or not bd.baskets_available:
        reason = (bd.baskets_unavailable_reason if bd is not None else None) or "本次未取得今日篮子。"
        lines.append(f"⚠ **本段未取得**:{reason}")
        lines.append("")
        lines.append("*(「未取得」≠「今日无篮子」——后者会在本节如实写出各档各自为空。)*")
        lines.append("")
        return "\n".join(lines)
    by_tier = bd.by_tier()
    if not bd.baskets:
        lines.append("**今日无篮子达到定档标准。** 这是合法输出,不是故障——"
                     "今天没有共同驱动清晰、成员结构够格的篮子,系统不会为了让报告好看而放宽质量线。")
        lines.append("")
        return "\n".join(lines)
    # 局部 import(同 `basket_daily.py` / `info_card.py` 的 report→selection 体例):
    # 现役档位的**单一源**是引擎,渲染层不抄第二份档位元组。
    from neckline.selection.tier import TIERS as _ACTIVE_TIERS

    # **现役档位 ∪ 快照里实际出现的档位**(V2.1-②):前者保证"今日 T1 为空"这句诚实
    # 披露不会因为当天没篮子而消失;后者保证**回放 V2 老报告时 T3 篮子照常显示**
    # (`basket_daily_json` 是冻结快照,读侧宽容)。⛔ 别退回写死元组。
    for tier in sorted(set(_ACTIVE_TIERS) | set(by_tier)):
        title = _tier_title(tier)
        lines.append(f"### {title}")
        lines.append("")
        items = by_tier.get(tier) or []
        if not items:
            # ⑥-b-B / ⑮ 信息架构:空档位**如实显示**,不隐藏。
            lines.append(f"今日 {title} 为空。")
            lines.append("")
            continue
        for b in items:
            lines.append(_render_one_basket(b))
    return "\n".join(lines)


def _render_one_basket(b: Any) -> str:
    """一张篮子卡(蓝图 4.6 十一项)。卡未就绪 → 只出篮子壳 + 明确写「卡还没生成」,
    ⛔ 不把整篮从报告里抹掉(有篮子无卡是合法中间态)。"""
    lines = [f"#### {b.name}(`{b.basket_key}` · basketId {b.basket_id})", ""]
    card = b.card
    if not card:
        reason = b.card_unavailable_reason or "card_not_ready"
        # B1 裁定:「有行但读不出」是数据事故,⛔ 不许在报告里降级成「卡未生成」。
        if reason == "card_corrupt":
            lines.append("- ⚠ **本篮卡数据损坏,已记录待排查**(`card_corrupt`)——卡行在库里,"
                         "但读不出来;卡是冻结件、不会自动重建,**等不来新的**,需要人工排查。")
        else:
            lines.append(f"- ⚠ **本篮的卡还没生成**(`{reason}`)"
                         f"——篮子与成员已冻结,卡生成是独立一段,本次未完成。")
        lines.append(f"- 成员({len(b.member_codes)}):{'、'.join(b.member_codes) or '(无)'}")
        lines.append("")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"- **共同驱动**({card.get('driverKind') or '未知'}):{card.get('driver') or '(缺)'}")
    lines.append(f"- **为什么是现在**:{card.get('whyNow') or '(缺)'}")
    lines.append(f"- 机械分 {_fmt_num(card.get('mechScore'), '{:.3f}')} · 档内第 "
                 f"{card.get('rankInTier') if card.get('rankInTier') is not None else '—'} 名"
                 f"(机械序第 {card.get('rankMech') if card.get('rankMech') is not None else '—'} 名)")
    # V2.1-④ 百分制打分卡:同一个机械分的**等价换算 + 五维贡献拆解**(⛔ 不是第二个
    # 分数、更不进任何判定)。文案唯一实现在 `report/score_display.contribution_line`,
    # 取不到打分 → **整行不出**(⛔ 不出一行「机械分 —/100」的空壳:那看起来像
    # "算过了是空的",而真相多半是这份快照生成于打分卡上线之前)。
    score_line = contribution_line(getattr(b, "score", None))
    if score_line:
        lines.append(f"- {score_line}")
    if card.get("tierReason"):
        lines.append(f"- 分层理由:{card['tierReason']}")
    if card.get("tierNote"):
        lines.append(f"- 分层备注:{card['tierNote']}")
    ev_status = card.get("evidenceStatus")
    if ev_status and ev_status != "ok":
        # ⑤ 的两段式流水单侧故障必须诚实披露(`search_unavailable` / `partial`)。
        lines.append(f"- ⚠ **证据链状态:`{ev_status}`** —— 检索侧未完整可用,下方证据不代表完整取证。")
    ev = card.get("evidence") or []
    if ev:
        lines.append("- 驱动证据(每条带来源与日期,**参考、非指令**):")
        for e in ev:
            src = e.get("source") or "未注明来源"
            day = e.get("date") or "未注明日期"
            lines.append(f"    - {e.get('claim') or '(空)'} —— {src} · {day}")
    lines.append("")

    lines.append("| 成员 | 角色(LLM / 机械) | 对拍 | RS | 建仓观察区间 | 最高追价 | 离场参考区间 | K4 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for m in card.get("members") or []:
        role = (f"{_ROLE_LABEL.get(m.get('roleLlm'), m.get('roleLlm') or '—')} / "
                f"{_ROLE_LABEL.get(m.get('roleMech'), m.get('roleMech') or '—')}")
        conflict = "⚠ 两说并存" if m.get("roleConflict") else "一致"
        zone = m.get("entryZone")
        zone_txt = (f"{_fmt_num(zone.get('low'))}~{_fmt_num(zone.get('high'))}" if isinstance(zone, dict)
                    else f"—({m.get('entryZoneUnavailableReason') or '不可得'})")
        chase = (f"+{_fmt_num(m.get('maxChase'), '{:.1f}')}%" if m.get("maxChase") is not None
                 else f"—({m.get('maxChaseUnavailableReason') or '不可得'})")
        exit_ref = m.get("exitReference")
        exit_txt = (f"{_fmt_num(exit_ref.get('low'))}~{_fmt_num(exit_ref.get('high'))}"
                    if isinstance(exit_ref, dict)
                    else f"—({m.get('exitReferenceUnavailableReason') or '不可得'})")
        lines.append(
            f"| {m.get('name') or ''}({m.get('tsCode') or ''}) | {role} | {conflict} | "
            f"{m.get('rsRank') if m.get('rsRank') is not None else '—'} | {zone_txt} | {chase} | "
            f"{exit_txt} | {m.get('k4Tag') or '—'} |"
        )
    lines.append("")
    lines.append("*建仓观察区间 / 最高追价 / 离场参考区间均为**参考件、非指令**;"
                 "离场参考区间**不是止盈线**,纪律只住纪律章程。*")
    lines.append("")

    # ⑦-K7 成员标注件(只标注,不进排序、不加分)
    tag_lines: List[str] = []
    for m in card.get("members") or []:
        for t in m.get("tags") or []:
            tag_lines.append(f"    - {m.get('tsCode')}:**{t.get('label') or t.get('code')}** —— {t.get('text') or ''}")
    if tag_lines:
        lines.append("- 成员标注(**只标注:不进排序 / 不进哨兵 / 不改去留 / 不加分**):")
        lines.extend(tag_lines)
        lines.append("")

    # ⑬-4:执行提示并入剧本上下文(**回答「怎么执行更不吃亏」,不是「该不该买」**)
    if b.exec_hints:
        lines.append("- 执行提示(**参考、非指令**;回答的是「若你决定动手,怎么执行更不吃亏」,不是「该不该买」):")
        for code, hints in b.exec_hints.items():
            for h in hints:
                lines.append(f"    - {code}:{h.get('text') or ''}(来源 `{h.get('source') or '?'}`)")
        lines.append("")

    scripts = card.get("scripts")
    if isinstance(scripts, Mapping) and any(scripts.values()):
        lines.append("- 次日竞价剧本(**参考、非指令**):")
        for key, label in (("strong", "强"), ("flat", "平"), ("weak", "弱")):
            if scripts.get(key):
                lines.append(f"    - **{label}**:{scripts[key]}")
    else:
        lines.append(f"- 次日竞价剧本:未生成({card.get('scriptsUnavailableReason') or '原因未记录'})。")
    lines.append("")

    if card.get("verificationText"):
        lines.append(f"- 验证条件(人话半份,**参考**):{card['verificationText']}")
    if card.get("invalidationText"):
        lines.append(f"- 失效条件(人话半份,**参考**):{card['invalidationText']}")
    risks = card.get("risks") or []
    if risks:
        lines.append("- 主要风险:" + "；".join(str(r) for r in risks))
    labels = card.get("disciplineLabels") or []
    if labels:
        lines.append("- 纪律标签(读现役章程,非本卡自定):" + "、".join(str(x) for x in labels))
    if card.get("narrative"):
        # §2.7:LLM 叙述**原文整段呈现**,不拆解塞回枚举卡片。
        lines.append("")
        lines.append("> " + str(card["narrative"]).replace("\n", "\n> "))
    if card.get("degraded"):
        lines.append("")
        lines.append(f"- ⚠ 本卡人话半份降级(`{card.get('llmStage')}`):结构化半份照常产出,叙述部分不可用。")
    for n in card.get("notes") or []:
        lines.append(f"- 备注:{n}")
    lines.append("")
    lines.append(f"*{card.get('disclaimer') or ''}*")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


# —— ③b 今日未定档篮子(⑥-b-C 的落点)——————————————————————————————————
#    **零溢出时这一节仍在**:节在 = 算过了。两个原因码指向相反的市场结论,
#    ⛔ 永远分开写。

def _render_dropped_baskets(bd: Optional[BasketDaily]) -> str:
    lines = ["### ③b 今日未定档篮子", ""]
    if bd is None or not bd.dropped_available:
        reason = (bd.dropped_unavailable_reason if bd is not None else None) or "本次未取得未定档篮子信息。"
        lines.append(f"⚠ **本段未取得**:{reason}")
        lines.append("")
        lines.append("*(「未取得」≠「今日无未定档篮子」——⛔ 不许把两者读成一句话。)*")
        lines.append("")
        return "\n".join(lines)
    if not bd.dropped:
        lines.append("今日无未定档篮子(**已算过**:既没有分数够却装不下的,也没有没过质量线的)。")
        lines.append("")
        return "\n".join(lines)
    # V2.2-③:③b 升级为「名 / 分 / 卡在哪一关、差多少 / 原因码」(门槛制下的
    # 未定档原因扩成多类,逐原因码计数披露,⛔ 不合并 —— 各码指向不同结论)。
    by_reason: Dict[str, int] = {}
    for d in bd.dropped:
        by_reason[d.reason] = by_reason.get(d.reason, 0) + 1
    lines.append(f"今日 {len(bd.dropped)} 个篮子未定档 —— **各原因码指向不同结论,分开看**:")
    lines.append("")
    for reason in sorted(by_reason):
        label = DROPPED_REASON_LABEL.get(reason, reason)
        lines.append(f"- **`{reason}`({by_reason[reason]} 个)**:{label}")
    lines.append("")
    lines.append("| 篮子名 | 机械分 | 卡在哪一关 | 差多少 | 原因码 |")
    lines.append("|---|---|---|---|---|")
    for d in sorted(bd.dropped, key=lambda x: (x.reason, -(x.mech_score or 0.0), x.name)):
        gate = _md_cell(getattr(d, "gate", None))
        # ⚠ V2.2-③-C(裁定 #11)起 `gate_detail` 可能带**模型写的自由中文**
        # (位置关 `unfit` 的理由),里面出现一个 `|` 就会把整张表切歪 —— 逐格转义,
        # ⛔ 不要因为"以前这里只放原因码"就省掉这一步。
        detail = _md_cell(getattr(d, "gate_detail", None))
        lines.append(
            f"| {_md_cell(d.name)} | {_fmt_num(d.mech_score, '{:.3f}')} | {gate} | "
            f"{detail} | `{d.reason}` |"
        )
    lines.append("")
    lines.append("*本节只列名 / 分 / 关口 / 原因码,**不出卡、无 basketId** —— 它们没有进 "
                 "`baskets` 表;机械分在门槛制下只是展示标度,不是定档依据。*")
    lines.append("")
    return "\n".join(lines)


# —— ③b 的第二类行:股票级 OUT(V2.3.2-②-B,K8 §六 / §十-11)————————————————
#    ⚠ 与上面那一节**刻意分开两段**:那一节现在只装「档位已满 · 未定档」
#    (`capacity_overflow` —— K8 §八 的 OUT 适用状态里没有"位置满",它不是 OUT)。

def _render_out_candidates(bd: Optional[BasketDaily]) -> str:
    lines = ["### ③b-2 今日 OUT 清单(股票级)", ""]
    if bd is None or not bd.out_candidates_available:
        reason = ((bd.out_candidates_unavailable_reason if bd is not None else None)
                  or "本次未取得 OUT 清单。")
        lines.append(f"⚠ **本段未取得**:{reason}")
        lines.append("")
        lines.append("*(「未取得」≠「今日无 OUT」——⛔ 不许把两者读成一句话。)*")
        lines.append("")
        return "\n".join(lines)
    if not bd.out_candidates:
        lines.append("今日无 OUT 候选(**已算过**:没有票在六道关口上被判出局)。")
        lines.append("")
        return "\n".join(lines)
    by_reason: Dict[str, int] = {}
    for o in bd.out_candidates:
        by_reason[o.out_reason] = by_reason.get(o.out_reason, 0) + 1
    lines.append(f"今日 {len(bd.out_candidates)} 只票判 OUT —— "
                 f"**各原因码指向不同结论,分开看**:")
    lines.append("")
    for reason in sorted(by_reason):
        label = DROPPED_REASON_LABEL.get(reason, reason)
        lines.append(f"- **`{reason}`({by_reason[reason]} 只)**:{label}")
    lines.append("")
    lines.append("| 股票 | 主引擎 | 出局关口 | 理由 | 原因码 |")
    lines.append("|---|---|---|---|---|")
    for o in sorted(bd.out_candidates, key=lambda x: (x.out_reason, x.ts_code)):
        who = f"{o.name}({o.ts_code})" if o.name else o.ts_code
        engine = "、".join(x for x in (o.engine_code, o.engine_version) if x) or "—"
        # ⚠ `out_detail` 可能带**模型写的自由中文**(证据关三值的理由),里面出现一个
        # `|` 就会把整张表切歪 —— 逐格转义(同上一节那条坑)。
        lines.append(
            f"| {_md_cell(who)} | {_md_cell(engine)} | {_md_cell(o.out_gate)} | "
            f"{_md_cell(o.out_detail)} | `{o.out_reason}` |"
        )
    lines.append("")
    lines.append("*OUT 是 K8 §六 的三个候选状态之一(T1 / T2 / OUT),**不是"
                 "「这票不行」** —— 它只说明今天它没走完六道关口。⛔ 与上一节的"
                 "「档位已满 · 未定档」不是一回事:那些篮子关口全过了,只是位置装不下。*")
    lines.append("")
    return "\n".join(lines)


# —— ④ 昨日篮子复盘(T1/T2 详复盘)——————————————————————————————————————
#    数据 = ⑨ 落 `basket_review_daily` 的九项机械判 + LLM 解释 + ⑧ 的验证状态。
#    ⚠ V2.1-②:新数据 `depth` 恒 `full`;历史 `depth='brief'` 的行**照常渲染**
#    (本节按行渲染、不按 depth 分组,天然宽容)。markdown 段标题一字未动(审计锚)。

_MECH_ITEM_LABEL = {
    "auction_vs_script": "竞价 vs 剧本", "open_direction": "开盘方向", "mfe_mae": "MFE / MAE",
    "member_alignment": "成员齐动", "leader_pull": "龙头带动", "buyability": "可买性",
    "verification_timing": "验证时点", "close_rs": "收盘相对强度", "tier_vs_outcome": "Tier vs 结果",
}
_MECH_ITEM_ORDER: Sequence[str] = (
    "auction_vs_script", "open_direction", "mfe_mae", "member_alignment", "leader_pull",
    "buyability", "verification_timing", "close_rs", "tier_vs_outcome",
)


def _pct(v: Any, dash: str = "—") -> str:
    """小数 → 百分数文案。`None` → `—`(⛔ 不用 0 冒充)。"""
    if v is None:
        return dash
    try:
        return f"{float(v):+.2%}"
    except (TypeError, ValueError):
        return str(v)


def _mech_item_summary(key: str, item: Mapping[str, Any]) -> str:
    """把 ⑨ 的一项机械判压成一行人读文案。**只读它已落库的字段,零重算**;
    每项取该项自己最要紧的那两三个数,不是笼统一句"正常"。"""
    if key == "auction_vs_script":
        branch = item.get("branch")
        hit = "卡上有该分支剧本" if item.get("script_present") else "卡上无该分支剧本"
        return f"竞价中位 {_pct(item.get('gap_median'))} → 落「{branch or '?'}」分支({hit})"
    if key == "open_direction":
        aligned = item.get("aligned")
        aligned_txt = "一致" if aligned else ("背离" if aligned is False else "无法比较")
        return (f"竞价中位 {_pct(item.get('gap_median'))} · 盘中中位 "
                f"{_pct(item.get('intraday_median'))}(方向{aligned_txt})")
    if key == "mfe_mae":
        note = item.get("note")
        base = f"MFE 中位 {_pct(item.get('mfe_median'))} · MAE 中位 {_pct(item.get('mae_median'))}(源 {item.get('mfe_source')})"
        return base + (f" —— {note}" if note else "")
    if key == "member_alignment":
        return (f"{item.get('observed')}/{item.get('member_count')} 只有行情,"
                f"涨 {item.get('up')} / 跌 {item.get('down')} / 平 {item.get('flat')},"
                f"主方向「{item.get('dominant_direction') or '?'}」")
    if key == "leader_pull":
        led = item.get("led")
        led_txt = "龙头带住了" if led else ("龙头没带住" if led is False else "无法比较")
        return (f"龙头中位 {_pct(item.get('leader_ret_median'))} vs 其余 "
                f"{_pct(item.get('others_ret_median'))} → {led_txt}")
    if key == "buyability":
        return (f"可买 {item.get('buyable')}/{item.get('member_count')} 只"
                f"(一字 {item.get('one_word')} · 涨停收盘 {item.get('limit_up')} · 无行情 {item.get('no_bar')})")
    if key == "verification_timing":
        return (f"状态 **{item.get('state')}**({item.get('state_label')});"
                f"当日 {item.get('rows')} 行(盘中 {item.get('intraday_rows')} · "
                f"EOD 定论 {'有' if item.get('has_eod_verdict') else '无'})")
    if key == "close_rs":
        return (f"超额中位 {_pct(item.get('excess_median'))}(基准 {item.get('index_code')} "
                f"{_pct(item.get('index_ret'))}),跑赢 {item.get('outperformers')} 只")
    if key == "tier_vs_outcome":
        return (f"T{item.get('tier')} · 机械分 {_fmt_num(item.get('mech_score'), '{:.3f}')} → "
                f"篮子当日收益中位 {_pct(item.get('basket_ret_median'))}"
                f"(**单日不足以判 Tier 有效性**,见 ⑨ 的样本纪律)")
    return "(本项无摘要模板)"


def _render_basket_reviews(bd: Optional[BasketDaily]) -> str:
    lines = ["## ④ 昨日篮子复盘", ""]
    if bd is None or not bd.reviews_available:
        reason = (bd.reviews_unavailable_reason if bd is not None else None) or "本次未取得昨日复盘。"
        lines.append(f"⚠ **本段未取得**:{reason}")
        lines.append("")
        return "\n".join(lines)
    if not bd.reviews:
        lines.append("今日无昨日篮子可复盘(昨日未产出篮子,或复盘引擎本次未运行)。")
        lines.append("")
        lines.append("*(⛔ 别把这句读成「昨天的篮子都没问题」——它说的是「没有复盘对象 / 没跑」。)*")
        lines.append("")
        return "\n".join(lines)
    lines.append(f"复盘对象 = **{bd.review_d0 or '上一交易日'}** 冻结的 {len(bd.reviews)} 个篮子,"
                 f"用**今日收盘**的事实回看昨天那个判断哪里对了、哪里错了。")
    lines.append("")
    for r in bd.reviews:
        lines.append(_render_one_review(r))
    return "\n".join(lines)


def _render_one_review(r: Any) -> str:
    tier_txt = f"T{r.tier}" if r.tier is not None else "T?"
    depth_txt = "详复盘" if r.depth == "full" else "简评"
    lines = [f"### {r.name}(`{r.basket_key}`,{tier_txt} · {depth_txt})", ""]
    v = r.verification
    if v:
        lines.append(f"- 验证状态:**{v.get('state')}**({v.get('label')}"
                     f"{' · 来源 ' + str(v.get('source')) if v.get('source') else ''})")
    else:
        lines.append("- 验证状态:今日无记录(⚠ 「没记录」≠「没被证伪」)。")
    mech = r.mech if isinstance(r.mech, dict) else {}
    for key in _MECH_ITEM_ORDER:
        item = mech.get(key)
        if not isinstance(item, dict):
            continue
        label = _MECH_ITEM_LABEL.get(key, key)
        if not item.get("available", True):
            lines.append(f"- {label}:**算不出**({item.get('unavailable_reason') or '原因未记录'})")
            continue
        lines.append(f"- {label}:{_mech_item_summary(key, item)}")
    if r.llm_text:
        # §2.7:LLM 解释原文整段呈现。
        lines.append("")
        lines.append("> " + str(r.llm_text).replace("\n", "\n> "))
    elif r.llm_skip_reason:
        lines.append(f"- 人话解释:本次未生成({r.llm_skip_reason})。")
    if r.degraded:
        lines.append("- ⚠ 本篮复盘的人话半份降级,机械九项照常产出。")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


# —— ⑤ 数据新鲜度与降级披露 ——————————————————————————————————————————
#    把散落各处的"这份报告哪里没看到"收在一处。**三件独立故障各占一行**
#    (板块日更 / 行业强度日更 / 扫描层批算),⛔ 不合并成一个 bool。

def _render_data_freshness(
    sector_freshness: Optional[SectorDataFreshness],
    industry_freshness: Optional[IndustryStrengthFreshness],
    scan_freshness: Optional[ScanLayerFreshness],
    bd: Optional[BasketDaily],
    news_alerts: Optional[NewsAlertsReport],
) -> str:
    lines = ["## ⑤ 数据新鲜度与降级披露", ""]
    lines.append("| 数据源 | 最新至 | 落后(交易日) | 过期 |")
    lines.append("|---|---|---|---|")
    if sector_freshness is not None:
        lines.append(f"| 概念板块(`ths_daily`) | {sector_freshness.sector_data_date or '无数据'} | "
                     f"{sector_freshness.lag_days} | {'是' if sector_freshness.stale else '否'} |")
    if industry_freshness is not None:
        lines.append(f"| 行业强度(`industry_strength_daily`) | {industry_freshness.latest_label()} | "
                     f"{industry_freshness.lag_days} | {'是' if industry_freshness.stale else '否'} |")
    if scan_freshness is not None:
        lines.append(f"| 市场扫描层(三张预计算表) | {scan_freshness.latest_label()} | "
                     f"{scan_freshness.lag_days} | {'是' if scan_freshness.stale else '否'} |")
    lines.append("")
    notes: List[str] = []
    for f in (sector_freshness, industry_freshness, scan_freshness):
        if f is not None and getattr(f, "note", None):
            note = f.note()
            if note:
                notes.append(note)
    if bd is not None:
        for key, ok, reason in (
            ("今日篮子", bd.baskets_available, bd.baskets_unavailable_reason),
            ("未定档篮子", bd.dropped_available, bd.dropped_unavailable_reason),
            ("OUT 清单", bd.out_candidates_available, bd.out_candidates_unavailable_reason),
            ("昨日复盘", bd.reviews_available, bd.reviews_unavailable_reason),
        ):
            if not ok:
                notes.append(f"{key}:{reason or '本次未取得'}")
        notes.extend(bd.notes)
    if news_alerts is not None:
        for s in news_alerts.scan_statuses:
            if not s.scanned:
                notes.append(f"消息面({s.source}):本次未扫描({s.reason or '原因未知'})——不代表确认无此类消息。")
    if notes:
        lines.append("**本报告本次没看到的东西(「没有」与「没看」分开)**:")
        for n in dict.fromkeys(notes):
            lines.append(f"- {n}")
    else:
        lines.append("本报告各数据源均新鲜、各段均已产出,无降级披露。")
    lines.append("")
    return "\n".join(lines)


__all__ = ["render_markdown"]
