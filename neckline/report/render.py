"""盘后报告 markdown 渲染(plan 2.5)。纯函数,吃 `pipeline.build_report` 组装好的
结构化数据,产出 markdown 全文——不碰任何 I/O。§2.7 硬约束(LLM 输出自由对话体,
禁模板卡)只管 LLM 叙述**本身**的文风;报告整体版式(标题/表格)是系统输出、不是
LLM 输出,可以用 markdown 标题与表格排版,但 LLM 审判的叙述段落必须原文整段
呈现,不得拆解塞回枚举卡片里。

⚠ **V2-⑬ 过渡态**:V1 的「候选节 + 参考件三件套展示位 + 执行提示位 + 老四件套 +
自选体检节」已按 §五 V2-⑬-1/3/4/6/8/11 整段删除,而**篮子日报的新版式是 ⑭-A 的活**。
此刻的报告 = 情绪 → 强势板块 → 持仓体检 → 情报件 → 板块资金流 → 消息面 + 两条
数据新鲜度告警。**这是先建后拆的中间状态。**
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

# v1.4-⑩-F:只借类型(`IndustryStrengthFreshness` 是纯 dataclass);本模块仍不碰任何
# I/O —— 新鲜度由 pipeline 查好后原样传进来,render 只读它的 `stale`/`note()`。
from neckline.report.industry_strength_store import IndustryStrengthFreshness
# v1.5-③-C:持仓体检节——只借类型(`HoldingK4Item`/`HoldingK4Hit` 纯 dataclass),
# 本模块仍不碰任何 I/O,数据由 pipeline 已算好的 `holding_k4_check` 原样传入渲染。
from neckline.report.holding_k4_check import HoldingK4Item
from neckline.report.intel import IntelReport
from neckline.report.news_alerts import NewsAlertsReport
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
    # v1.4-①-C:板块数据过期 → **报告顶部醒目告警**(§七 P0-3)。放在这里而不是塞进
    # 板块节里,是因为受影响的是「当日暴起板块」候选路 + 题材持续天数 + 五常驻板块解析
    # 三处,读者需要在看任何板块相关结论**之前**先知道底下的数据是旧的。
    if sector_freshness is not None and sector_freshness.stale:
        parts.append(f"> 🚨 **板块数据过期告警**:{sector_freshness.note()}")
        parts.append("")
    # v1.4-⑩-E(§七 P0-23):行业强度预计算表缺当日行 → 同样顶部醒目告警。**与板块过期
    # 分开两条,不合并** —— 两个独立故障(概念板块日更 vs `industry_strength_daily` 日更),
    # 合并成一句读者就分不清哪个坏了。这也是 ⑩-E「报告级披露已覆盖当日全部消费方」那句的
    # 兑现:持仓卡不再各加一个 available 位(避免契约膨胀)。
    if industry_freshness is not None and industry_freshness.stale:
        parts.append(f"> 🚨 **行业强度数据未就绪**:{industry_freshness.note()}")
        parts.append("")

    parts.append(_render_sentiment(sentiment))
    parts.append(_render_sectors(sectors, sector_freshness))
    # v1.5-③-C:持仓体检排在候选**之前**(镜像客户端 v1.1-E.1「持仓管理优先于选新票」
    # 的同一顺序;需求 9「今日计划拆两块:持仓股 / 候选列表」在 markdown 侧的落地)。
    parts.append(_render_holding_check(holding_k4_check or []))
    parts.append(_render_intel(intel, sector_freshness))
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


def _render_sectors(sectors: List[SectorScore],
                    freshness: Optional[SectorDataFreshness] = None) -> str:
    lines = ["## 强势板块(软加权展示,不圈死选股)", ""]
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
    lines = ["## 持仓体检(先管住手里的)", ""]
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
        lines.append(
            f"- D 计数:D{it.d_count}(有效上限 D{it.max_hold_effective})"
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
