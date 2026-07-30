"""LLM 提示词的共享件:当前日期锚 + 时效纪律 + 检索词时效引导(v1.5.2)。

**为什么单起一个模块**:三条 LLM 链路——问询台(`api/inquiry.py`)、候选/自选审判
(`llm/judge.py`)、参考件三件套(`report/reference_plan.py`)——需要的是**同一件**东西:
告诉模型"现在是哪一天"、以及"引用材料必须带日期"。**只此一份实现**,三处 import;
**不许各抄一份**(抄三份 = 三种日期口径,正是本次故障的同类病)。

**故障回顾(2026-07-30 用户报障 + 生产 `inquiry_log` 实证)**:问询 603298 时,回答把
**2024 年研报的目标价**当成现行参照。查生产日志确认**联网是通的**(两次问询 search_hits
2489 / 8565 字节,含 2025-06 的命中),但命中新旧混杂;而三处提示词**一处都没告诉模型
今天几号** —— 模型没有"现在"的概念,一份 2024 年的研报和一份上个月的公告在它眼里一样新。
所以修在提示词层:①给日期锚 ②给时效纪律 ③检索词里带年份引导。

**时区与交易日历一律走 `neckline.calendar`**(`CN_TZ` / `next_trading_day`,项目
CLAUDE.md「市场时刻的时区/收盘唯一源」)——本模块不自己写 `timezone(timedelta(hours=8))`,
也不硬编任何日期。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from neckline.calendar import CN_TZ, next_trading_day

logger = logging.getLogger(__name__)

# 周一=0 … 周日=6
_CN_WEEKDAY = ("一", "二", "三", "四", "五", "六", "日")


# —— 时效纪律(三条 system prompt 共用的单一源;文案里出现"目标价"是**禁止性表述**,
#    不是在教模型给目标价 —— 语义红线扫描按否定句放行,同 `reference_plan` prompt 体例)——
TIMELINESS_RULES = """时效纪律(与信息边界同级,不可违反):
1. **引用联网检索到的任何材料,都要带上该材料的日期**(例:「据 2026 年 5 月的公告…」);
若某条检索结果本身没有标注日期,就明说「该来源未标注日期,时效不明」。
2. **明显过时的数据(距今超过半年)必须明示**「这是截至 X 的旧数据,时效有限」,
**不得**把它当作现在的情况直接陈述。
3. **旧研报的目标价 / 估值区间只能作为历史参照提及**(且必须注明是哪一年的),
**不得**表述为现行定价基准,也不得据此推导当下的价格判断。"""


def today_cn() -> date:
    """北京时间的"今天"(`CN_TZ` 唯一源,A 股无夏令时)。"""
    return datetime.now(CN_TZ).date()


def _cn_date(d: date) -> str:
    return f"{d.year}年{d.month}月{d.day}日(周{_CN_WEEKDAY[d.weekday()]})"


def next_trading_day_or_none(d: date) -> Optional[date]:
    """`next_trading_day` 的安全包装:算不出(日历数据异常)→ `None` 而不是抛异常。
    日期锚是**提示词装饰**,绝不该因为日历缺一段就掀翻一次 LLM 调用。"""
    try:
        return next_trading_day(d)
    except Exception:  # noqa: BLE001 —— 见 docstring
        logger.warning("日期锚:算不出 %s 的下一交易日,本次锚不含该项", d, exc_info=True)
        return None


def date_anchor_line(ref_date: Optional[date] = None, *, today: Optional[date] = None,
                     name_tomorrow: bool = False) -> str:
    """喂给 LLM 的**当前日期锚**(v1.5.2,三处上下文的第一行)。

    `ref_date`:本次分析的基准交易日。`None` = 就按今天(问询台/审判的常规用法);
    传值且**与今天不同**时(补跑历史日 / 回放),锚会**同时说明两个日期**并要求模型
    按基准日的信息判断——不假装今天是那天(不撒谎),也不让模型误以为基准日就是今天。

    `name_tomorrow=True`(参考件链路用):额外点名「明早/次日 = 下一交易日 X」——
    ①-C 的证伪剧本写的就是"明早开盘怎么做",不点名的话模型可能按自然日的"明天"理解,
    而周五生成的报告,"明早"其实是下周一。

    `today` 仅供单测注入,生产恒取 `today_cn()`。
    """
    now = today or today_cn()
    base = ref_date or now
    parts = [f"今天是 {_cn_date(now)}"]
    if ref_date is not None and ref_date != now:
        # 两个方向措辞不同:基准日在过去 = 补跑/回放(按那天的信息判);基准日在未来
        # (极少见,如手工预生成)= 那天还没到,只能按截至今天的信息判。**都不撒谎**。
        note = ("系补跑/回放历史日,请按该日及之前的信息判断" if ref_date < now
                else "该交易日尚未到来,请按截至今天的信息判断")
        parts.append(f"本次分析的基准交易日是 {_cn_date(base)}({note})")
    nd = next_trading_day_or_none(base)
    if nd is None:
        parts.append("下一交易日暂时算不出(交易日历数据异常)")
    else:
        parts.append(f"下一交易日是 {_cn_date(nd)}")
    line = ";".join(parts) + "。"
    if name_tomorrow and nd is not None:
        line += f"下文说的「明早 / 次日开盘」一律指 {_cn_date(nd)} 开盘,不是别的日子。"
    return line


def recency_hint(today: Optional[date] = None) -> str:
    """检索词里的时效引导词(如 `2026 最新`)。年份**动态取**,不硬编。"""
    return f"{(today or today_cn()).year} 最新"


def search_subject_with_recency(subject: str, tail: str = "", today: Optional[date] = None) -> str:
    """拼检索词 = 「**主体** + 时效引导词 + 追加语」。

    **时效引导词紧跟主体、刻意不放最末**:GLM 侧 `max_search_query_chars=78`
    (`llm/providers/glm.py`)会**截尾**,而问询台的追加语是用户原话(常常几十个字),
    放最末的时效词在长问句下会被整段切掉 = 等于没加。放主体后面则恒在截断窗口内。

    ⚠ **只改检索词文本,不加任何新 API 参数**(v1.3.4 案底:取值/参数不被上游认识时会
    `ok=True` **静默返 0 条**,文字上完全看不出来)。
    """
    hint = recency_hint(today)
    head = f"{(subject or '').strip()} {hint}".strip()
    tail = (tail or "").strip()
    return f"{head} {tail}".strip() if tail else head


__all__ = [
    "TIMELINESS_RULES",
    "today_cn",
    "next_trading_day_or_none",
    "date_anchor_line",
    "recency_hint",
    "search_subject_with_recency",
]
