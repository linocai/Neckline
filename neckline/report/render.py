"""盘后报告 markdown 渲染(plan 2.5)。纯函数,吃 `pipeline.build_report` 组装好的
结构化数据,产出 markdown 全文——不碰任何 I/O。§2.7 硬约束(LLM 输出自由对话体,
禁模板卡)只管 LLM 叙述**本身**的文风;报告整体版式(标题/表格)是系统输出、不是
LLM 输出,可以用 markdown 标题与表格排版,但 LLM 审判的叙述段落必须原文整段
呈现,不得拆解塞回枚举卡片里。
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from neckline.llm.base import search_coverage_line
from neckline.llm.judge import JudgeResult, VERDICT_PASS, VERDICT_VETO
from neckline.report.candidates import Candidate
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
from neckline.report.watchlist_check import WatchlistCheckItem
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
    parts.append(_render_candidates(candidates, judged, top_n_judged))
    parts.append(_render_watchlist(watchlist_check or []))
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


def _intel_rank_line(c: Candidate) -> Optional[str]:
    """候选情报排序理由(v1.4-③ 起三级键:行业强度排名 → 题材持续天数 → K4 黄牌数,依次
    优先;`sectorFlow` 降为并列展示,不再是排序依据,见需求 8)。K1/v1.3 老报告 `intel_rank`
    空 → None 不渲染;**旧报告没有三新键时按缺键处理**(不拿 `.get()` 的 None 默认值冒充
    "确实未参与排名"——旧报告压根没算过这件事,两者语义不同,不能混为一谈)。"""
    ir = c.intel_rank or {}
    if not ir:
        return None
    parts: List[str] = []
    has_new_keys = "industryRank" in ir   # 三新键同批加,判其一即可(见 ③-E 契约)
    if has_new_keys:
        rank = ir.get("industryRank")
        parts.append(f"行业强度排名第{rank}" if rank is not None else "行业强度未参与排名(无行业/成员<5)")
    persist = ir.get("industryPersistDays") if has_new_keys else ir.get("themePersistDays")
    if persist is not None:
        fresh = {0: "未启动", 1: "第1天(新鲜)", 2: "第2天(警惕)", 3: "第3天(警惕)"}.get(persist, f"第{persist}天")
        parts.append(f"题材持续 {fresh}")
    if has_new_keys:
        parts.append(f"K4 黄牌 {ir.get('yellowCardCount', 0)} 个")
    sf = ir.get("sectorFlow")
    if sf is not None:
        tail = "(并列展示,不参与排序)" if has_new_keys else ""
        parts.append(f"板块资金净流入 {sf:+,.0f} 万元{tail}")
    if ir.get("highElasticity"):
        parts.append("高弹板块(GEM/STAR,20cm 易波动,自行判断)")
    return "- 情报排序理由:" + " · ".join(parts) if parts else None


def _k4_flag_line(c: Candidate) -> Optional[str]:
    """K4 安检打标(avoid_flag 命中;hard_cut 命中的票已在生成时拦出池、不到这里)。"""
    if not c.k4_flags:
        return None
    return "- ⚠ K4 安检标注(机器不禁、供你判断):" + "、".join(c.k4_flags)


def _exec_hint_line(c: Candidate) -> Optional[str]:
    """执行提示(v1.4-⑤-A `exec_hint.py` 既有计算,本节起首次渲染进 markdown——
    plan §五 v1.5-③-A 候选卡结构「…→ 执行提示(不变)→ 参考三件套(新)→ …」,数据/
    判据零改动,只是新增一处展示位)。语义红线(`exec_hint.py` 模块头硬约束):回答
    「如果决定动手,怎么执行更不吃亏」,不是「该不该买」。"""
    if not c.exec_hints:
        return None
    texts = [h.get("text", "") for h in c.exec_hints if h.get("text")]
    if not texts:
        return None
    return "- 执行提示(如果决定动手,怎么做更不吃亏,非买卖建议):" + "；".join(texts)


def _render_reference_plan(c: Candidate) -> List[str]:
    """参考三件套渲染(v1.5-③-A,需求 9)——取代老四件套(买点/止损/目标/证伪条件)
    在候选卡上的位置。四态与 `reference_plan.py` ①-D 状态机逐位对应,**不许合并**
    (§3.8"没有"vs"没看"):
        · `c.reference_plan is None` —— 老报告快照(建于本字段前)或本次生成整体
          异常;`judge_skipped` 时换一句更具体的"预算耗尽未发起"理由(与下方 LLM
          审判段落的措辞呼应,不重复解释同一件事两种说法)。
        · `status="vetoed"` —— LLM 判风险大,三件套全不展示,只给不买理由;票与
          信息卡仍照留(机器不禁、人可复核,§2.0 第 3 条)。
        · `status="unavailable"` —— 生成过、本次没看清楚(未激活/调用失败/JSON
          解析失败),不是"确认无参考"。
        · `status="ok"` —— 逐件展示;某一件被夹逼拦下或本就没给时,**不画空区间、
          不写 0**,如实给出未展示原因(`buyUnavailableReason`/`exitUnavailableReason`)。
    """
    rp = c.reference_plan
    if rp is None:
        if c.judge_skipped:
            return ["**参考件**:本次预算耗尽未发起审判,因此没有参考件"
                    "(非异常状态,详见下方 LLM 审判段落)。"]
        return ["**参考件**:本报告未生成参考三件套"
                "(老报告快照建于本功能上线前,或本次生成异常;不代表已确认无参考)。"]

    status = rp.get("status")
    if status == "vetoed":
        reason = rp.get("vetoReason") or "见下方 LLM 审判叙述"
        return [f"**参考件:LLM 判风险大,本次不给参考区间**;不买理由:{reason}"]
    if status == "unavailable":
        reason = rp.get("unavailableReason") or "原因未知"
        return [f"**参考件:本次未生成**({reason})——不代表确认无参考,仅本次没看清楚。"]

    lines: List[str] = []
    buy = rp.get("buy")
    if buy:
        why = f" {buy['why']}" if buy.get("why") else ""
        stop_txt = f"{buy['stopPrice']:.2f}" if buy.get("stopPrice") is not None else "未知"
        lines.append(
            f"- **参考买入区间(参考,非指令)**:{buy['low']:.2f}~{buy['high']:.2f};"
            f"止损参考约 {stop_txt}(章程 −5%,以实际成交价为准)。{why}"
        )
    else:
        lines.append(f"- **参考买入区间**:本次未展示({rp.get('buyUnavailableReason') or '原因未知'})。")
    exit_ = rp.get("exit")
    if exit_:
        why = f" {exit_['why']}" if exit_.get("why") else ""
        lines.append(
            f"- **参考离场区间(参考,非止盈线)**:{exit_['low']:.2f}~{exit_['high']:.2f}。"
            f"{why} —— 纪律仍以回落止盈 8% 兜底。"
        )
    else:
        lines.append(f"- **参考离场区间**:本次未展示({rp.get('exitUnavailableReason') or '原因未知'})。")
    script = rp.get("script")
    lines.append(f"- **明早证伪剧本(参考,非指令)**:{script}" if script else "- **明早证伪剧本**:本次未生成。")
    disclaimer = rp.get("disclaimer")
    if disclaimer:
        lines.append(f"*{disclaimer}*")
    return lines


def _render_candidates(candidates: List[Candidate], judged: Dict[str, JudgeResult], top_n_judged: int) -> str:
    # v1.3-③-C3 语义变更:候选 = 「过完安检、值得关注的票」非「会涨的票」,终选在用户
    # (§2.3)。生成源从 K1 entry mask 退役 → 情报筛选四步管线。v1.4-③(需求 8)起排序键
    # 改三级(行业强度排名 → 题材持续天数 → K4 黄牌数),语义红线文案扩到本节(§五 v1.4
    # 「语义红线」)。**v1.5-③-A(需求 9)**:候选卡输出层老四件套(买点/止损/目标/证伪
    # 条件)退役,改参考三件套(买入/离场参考区间 + 明早证伪剧本,§2.0 第〇原则)。
    #
    # `top_n_judged`:v1.5-②-A 起「前 N 只审判 / 后 N 只只给分数」的旧分档已退役
    # (20 只全覆盖,生产恒 `top_n_judged==len(candidates)`)——本函数**不再用它
    # 区分"详情"与"表格"两种渲染规格**(全体候选统一走详情 + 全量速览表),参数
    # 仍保留在签名里只为与 `render_markdown`/`pipeline.py` 的既有调用签名保持稳定,
    # 不因这次改版牵动上一层签名。
    lines = ["## 候选(情报筛选 · 过完安检、值得花注意力的票,非买入信号,终选在你)", ""]
    if not candidates:
        lines.append("今日无候选通过情报筛选(无热门板块成员过安检,或数据缺失)。")
        lines.append("")
        return "\n".join(lines)
    lines.append(
        "> 候选 = 五板块常驻 + 当日暴起板块的成员里,过完卫生线/非次新/趋势向上安检、"
        "再过 K4 避坑安检(hard_cut 已拦出池、avoid_flag 打标)的票;**不是回测选出的买入信号**,"
        "买入/离场参考区间与明早证伪剧本均为 LLM 参考件(参考,非指令),买卖与终选在你(§2.3)。"
    )
    lines.append(
        "> **排序 = 注意力优先级,不是收益预测;排第一 ≠ 最会涨;终选权在你。**"
        "排序依次看行业强度排名(K2 拥挤探测器)→ 题材持续天数(越新鲜越靠前,H6 证据)→ "
        "K4 黄牌数(越少越靠前,风险优先非收益优先);板块资金流强度只作并列展示,不参与排序"
        "(需求 8)。"
    )
    lines.append("")

    # v1.5-③-A:「前N只/后N只」两段结构随 ②-A(20 只全覆盖)合并成一段——每票或出
    # 参考三件套、或出不买理由,不再区分"过审判的详情"与"仅形态标签的表格"两种规格。
    lines.append(f"### 候选详情({len(candidates)} 只)")
    lines.append("")
    for c in candidates:
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
        hint_line = _exec_hint_line(c)
        if hint_line:
            lines.append(hint_line)
        lines.append("")
        lines.extend(_render_reference_plan(c))
        lines.append("")
        if jr is not None:
            badge = _VERDICT_BADGE.get(jr.verdict, f"⏸ {jr.verdict}")
            lines.append(f"**LLM 审判({jr.provider or '未激活'}){' · ' + jr.model if jr.model else ''}:{badge}**")
            lines.append("")
            lines.append(jr.narrative)
            if not jr.degraded:
                # 搜索取证覆盖脚注(v1.3.4):**命中 0 条也要写出来**。搜索静默返空时
                # 模型照样能写出一段像样的判词(退回训练数据),不写这行用户分不清
                # 「搜过没消息」与「一条都没搜到」——20260721/22/23 三天 10/10 空命中
                # 就是这么无声发生的。降级判词本就没调用成功,不在此列。
                lines.append("")
                lines.append(f"*{search_coverage_line(len(jr.search_hits or []))}*")
                if jr.search_hits:
                    lines.append("")
                    lines.append("联网搜索来源:" + "、".join(f"[{h.title or h.link}]({h.link})" for h in jr.search_hits if h.link))
        elif c.judge_skipped:
            # v1.5-②-B:预算耗尽、按 rank 靠后被跳过——**如实标注,不是异常**
            # (与下方 else 分支的"真异常"刻意区分,不合并成一句话;`judgeSkipped`
            # 与 `degraded` 语义不同,见 `Candidate.judge_skipped` 字段注释)。
            lines.append(
                "**LLM 审判:本次预算耗尽未发起**"
                "(候选 LLM 审判墙钟预算已用完,按排序靠后被跳过,不代表否决,"
                "非异常状态)。"
            )
        else:
            lines.append("**LLM 审判:未执行**(异常状态,请检查 pipeline)。")
        lines.append("")
        lines.append("---")
        lines.append("")

    # v1.5-③-A:原「后 N 只」紧凑表格保留为**全部 N 只**的速览表,放在详情之后
    # (表头列不变)——不必逐只翻详情也能一眼看排序依据。
    lines.append(f"### 速览表(全部 {len(candidates)} 只)")
    lines.append("")
    # v1.4-③(需求 8):表格列补「行业排名」「黄牌数」两列(排序键①③),让排序依据在
    # 紧凑表格里也能一眼看到,不必逐只翻详情。「题材天数」沿用旧列名(=排序键②同一个量)。
    lines.append("| 排名 | 代码 | 名称 | 展示分 | 行业排名 | 题材天数 | 黄牌数 | 高弹 | K4标注 | 形态标签 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for c in candidates:
        tags = "、".join(c.pattern_tags) if c.pattern_tags else "无"
        ir = c.intel_rank or {}
        irank = ir.get("industryRank")
        irank_disp = irank if irank is not None else "-"
        persist = ir.get("industryPersistDays", ir.get("themePersistDays", "-"))
        yellow = ir.get("yellowCardCount", "-")
        he = "是" if ir.get("highElasticity") else ""
        k4 = "、".join(c.k4_flags) if c.k4_flags else ""
        lines.append(f"| {c.rank} | {c.ts_code} | {c.name} | {c.score:.1f} | {irank_disp} | "
                    f"{persist} | {yellow} | {he} | {k4} | {tags} |")
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
        if it.dispatch_alerts:
            # v1.5-④-A1:K4 派发警示(仅 A3/A3b 两码,均强价量证据)——打标展示给
            # 人判,不拦不禁(第〇原则);不推 APNs(自选不是持仓)。
            labels = "、".join(h.label for h in it.dispatch_alerts)
            lines.append(f"- ⚠ K4 派发警示(强价量证据,仅参考不禁买):{labels}")
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
            if not jr.get("degraded"):
                # 同候选审判的搜索取证脚注(v1.3.4);`search_hits` 是条数,不是全文
                # (自选体检的命中全文不单独存档,见 `apply_llm_review`)。
                lines.append("")
                lines.append(f"*{search_coverage_line(jr.get('search_hits') or 0)}*")
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
