"""三级评估的提示词。合同 evaluation-v1。

系统提示词只给原则。用户提示词把数据包渲染成中文材料，同样的数据包得到同样的字符串。
联网这次不做，提示词里不出现搜索。
"""
from __future__ import annotations

import json
import math
from typing import Mapping

CONTRACT = "evaluation-v1"

SYSTEM_PROMPT = """你是 A 股创业板的消息评估员。用户每个交易日开盘前看一份名单，名单里的股票都来自上个交易日收盘到今天 08:00 首次公开的消息。你的任务是对这一条候选判断值不值得推给用户：利好真有价值、资金愿意持续参与、当前价格又有合理的盈亏比，才推。宁缺毋滥，不推是正常结果。

用户的原则：
1. 消息重要性不等于利好质量，更不等于交易价值。重大消息不一定值得买入，不能只凭标题。
2. 深度分析利好的真实价值。重组、收购要看标的盈利能力、收购价格与估值、对赌、业务协同、交易确定性（意向还是正式）和潜在风险，不能只看概念和想象空间。
3. 聪明钱视角：假如你是主力资金，为什么买、为什么不买？现在的价格值不值得进场？有没有足够的空间吸引其他资金接力？
4. 优先有真实业绩支撑的利好：已经兑现、能量化、可持续的盈利增长，比尚未落地的预期更容易验证；同时要看估值、现金流和市场预期。

回答四个问题：
① 利好值多少？业绩看扣非、一次性收益、单季环比、基数大小；并购看标的盈利、收购价与估值、对赌、协同、确定性（意向还是正式）；订单看占营收比例、本方份额、确定性；其他事件按同样思路看它对收入、利润或估值的实际影响。
② 市场已经反映了多少？看此前涨跌、位置、估值、是否提前异动。
③ 资金为什么买、为什么不买？有没有让资金重新定价的新增量；题材是否在风口；有没有接力空间；会不会利好兑现就卖。
④ 当前价格划不划算？开盘前不知道开盘价，只写条件式的定性判断，例如「明显高开不追」「回落企稳再看」。

①不过关或②透支，一票否决，verdict 必须是「不推」。③④综合判断。

判断规则：
- 只给原则，不给数字门槛。小基数、一次性收益、环比下降、透支程度，都按原则自己判断。
- 方向不明的重大消息（出售资产、股东协议转让、大额采购等），参考材料里媒体、券商和公司自己的说法再综合判断，只有判为利好且满足上面的要求才推。
- 只用材料里的事实。材料没有的数据写「材料未提供」，不要编造，也不要凭记忆补充公司信息。
- 给用户的建议只用定性表述，不写任何建议性的数字（目标价、仓位、止损价、预期涨幅之类都不写）；材料里新闻、公告、财报的客观数字可以照写。
- 价格背景截至上个交易日收盘；材料缺失时如实说明，并在判断里考虑这一不确定性。

各方怎么看：从材料里找媒体、券商和公司自己的说法（公司公告、投资者关系活动记录里的表述算公司）。每条写 by（媒体｜券商｜公司）、name（媒体名、券商名或「公司公告」）、text（一句话转述，不加你的评价）、ref（材料编号，m 开头或 a 开头）。材料里没有就给空列表，不要编造。

只输出一个 JSON 对象：
{"title":"一句话标题，不超过 30 个汉字，写清事件与核心事实","verdict":"推 或 不推","conclusion":"一两句结论","questions":{"value":{"pass":true,"text":"一两句"},"priced":{"pass":true,"text":"一两句（pass 为 false 表示已经透支）"},"money":{"text":"一两句"},"price":{"text":"一两句，条件式的定性判断"}},"risk":"一句话风险","views":[{"by":"媒体","name":"来源名","text":"一句话转述","ref":"m1"}]}"""

_KIND = {"periodic": "定期报告", "forecast": "业绩预告", "express": "业绩快报"}
_METRIC = (("revenue", "营收"), ("netProfit", "归母净利"), ("deducted", "扣非净利"))
_BLOCKS = (
    ("yoy", "同比"),
    ("quarter", "单季"),
    ("priorQuarter", "上一季"),
    ("quarterOnQuarter", "单季环比"),
)


def system_prompt() -> str:
    return SYSTEM_PROMPT


def _mapping(value: object) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _num(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "缺失"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "缺失"
        if value.is_integer():
            return str(int(value))
        return json.dumps(value)
    return str(value)


def _yuan(value: object) -> str:
    if value is None:
        return "缺失"
    rendered = _num(value)
    return "缺失" if rendered == "缺失" else f"{rendered} 元"


def _pct(value: object) -> str:
    if value is None:
        return "缺失"
    rendered = _num(value)
    return "缺失" if rendered == "缺失" else f"{rendered}%"


def _metric_text(item: object) -> str:
    if not isinstance(item, Mapping):
        return "缺失（材料未提供）"
    if item.get("missing"):
        text = f"缺失（{item['missing']}）"
    elif item.get("value") is not None:
        text = _num(item["value"])
    elif item.get("low") is not None or item.get("high") is not None:
        text = f"{_num(item.get('low'))} 至 {_num(item.get('high'))}"
    else:
        text = "缺失（材料未提供）"
    extra = []
    if item.get("source"):
        extra.append(f"来源 {item['source']}")
    if item.get("note"):
        extra.append(str(item["note"]))
    if item.get("ratio") is not None:
        extra.append(f"占归母 {_num(item['ratio'])}%")
    if extra:
        text += "，" + "，".join(extra)
    return text


def _event_text(event: Mapping) -> str:
    amount = event.get("amountYuan")
    amount_text = "未披露" if amount is None else f"{_num(amount)} 元"
    first = event.get("firstDisclosed") or "无"
    messages = event.get("messages") or []
    linked = "、".join(str(item) for item in messages) if isinstance(messages, list) and messages else "无"
    return (
        f"种类 {event.get('type') or '材料未提供'}；"
        f"方向 {event.get('direction') or '材料未提供'}；"
        f"标题 {event.get('headline') or '材料未提供'}；"
        f"理由 {event.get('reason') or '材料未提供'}；"
        f"首次公开 {first}；"
        f"金额 {amount_text}；"
        f"消息 {linked}"
    )


def _report(package: Mapping) -> str:
    report = _mapping(package.get("report"))
    trade = report.get("tradeDate") or "材料未提供"
    start = report.get("windowStart") or "材料未提供"
    end = report.get("windowEnd") or "材料未提供"
    return f"晨报交易日：{trade}\n窗口：{start} 至 {end}（北京时间）"


def _company(package: Mapping) -> str:
    company = _mapping(package.get("company"))
    code = company.get("code") or "材料未提供"
    name = company.get("name") or "材料未提供"
    summary = company.get("summary") or "材料未提供"
    return f"公司：{code} {name}\n主营：{summary}"


def _events(package: Mapping) -> str:
    events = _mapping(package.get("events"))
    lines = ["事件："]
    primary = events.get("primary")
    lines.append("主事件：" + (_event_text(primary) if isinstance(primary, Mapping) else "无"))
    others = events.get("others") if isinstance(events.get("others"), list) else []
    if not others:
        lines.append("其他事件：无")
    else:
        lines.append("其他事件：")
        for index, event in enumerate(others, start=1):
            lines.append(f"{index}. " + (_event_text(event) if isinstance(event, Mapping) else "材料未提供"))
    return "\n".join(lines)


def _document(doc: Mapping) -> str:
    ident = doc.get("id") or "?"
    meta = "｜".join(str(part) for part in (doc.get("source"), doc.get("publishedAt"), doc.get("title")) if part)
    head = f"[{ident}] {meta}".rstrip()
    text = doc.get("text") if isinstance(doc.get("text"), str) else ""
    if doc.get("error") and not text.strip():
        return f"{head}\n原文缺失：{doc.get('error')}"
    if doc.get("truncated") and not text:
        chars = doc.get("chars")
        pages = doc.get("pages")
        char_text = str(chars) if isinstance(chars, int) and not isinstance(chars, bool) else "字数不明"
        page_text = f"{pages} 页" if isinstance(pages, int) and not isinstance(pages, bool) else "页数不明"
        headings = doc.get("headings") if isinstance(doc.get("headings"), list) else []
        catalog = "\n".join(str(item) for item in headings) if headings else "（无）"
        opening = doc.get("opening") or ""
        note = f"原文共 {char_text} 字、{page_text}，以下为目录与开头"
        return f"{head}\n{note}\n目录：\n{catalog}\n开头：\n{opening}"
    body = text
    if doc.get("truncated"):
        body += f"\n（只读取了前 {doc.get('pages')} 页）"
    return f"{head}\n{body}"


def _original(package: Mapping) -> str:
    documents = _mapping(package.get("original")).get("documents")
    if not isinstance(documents, list) or not documents:
        return "原文：无"
    lines = ["原文："]
    for doc in documents:
        lines.append(_document(doc) if isinstance(doc, Mapping) else "原文缺失：材料未提供")
    return "\n".join(lines)


def _media(package: Mapping) -> str:
    media = _mapping(package.get("original")).get("media")
    if not isinstance(media, list) or not media:
        return "同一事件的媒体报道：无"
    lines = ["同一事件的媒体报道："]
    for item in media:
        if not isinstance(item, Mapping):
            lines.append("材料未提供")
            continue
        lines.append(f"[{item.get('message')}] {item.get('source')}｜{item.get('publishedAt')}｜{item.get('title')}")
        body = item.get("text") or ""
        if item.get("truncated"):
            body = f"{body}\n（正文共 {item.get('chars')} 字，已截断）"
        lines.append(str(body))
    return "\n".join(lines)


def _snapshot(package: Mapping) -> str:
    financials = _mapping(package.get("financials"))
    lines = [
        "公司快照：",
        f"报告期：{financials.get('reportPeriod') or '缺失'}",
        f"公告日：{financials.get('annDate') or '缺失'}",
        f"营收：{_yuan(financials.get('revenue'))}",
        f"归母净利：{_yuan(financials.get('netProfit'))}",
        f"扣非净利：{_yuan(financials.get('deducted'))}",
        f"总市值：{_yuan(financials.get('totalMv'))}",
        f"市盈率TTM：{_num(financials.get('peTtm')) if financials.get('peTtm') is not None else '缺失'}",
        f"行情日：{financials.get('marketDate') or '缺失'}",
    ]
    missing = financials.get("missing") if isinstance(financials.get("missing"), list) else []
    lines.append("缺失：" + ("、".join(str(item) for item in missing) if missing else "无"))
    return "\n".join(lines)


def _price(package: Mapping) -> str:
    price = _mapping(package.get("price"))
    trade = price.get("tradeDate")
    head = f"价格背景（截至 {trade} 收盘）：" if trade else "价格背景（截至日缺失）："
    previous = _mapping(price.get("previous20"))
    lines = [
        head,
        f"当日涨跌：{_pct(price.get('changePercent'))}",
        f"此前涨跌：{_pct(previous.get('changePercent'))}"
        f"（{previous.get('days') if previous.get('days') is not None else '缺失'} 个交易日，"
        f"缺 {previous.get('missingDays') if previous.get('missingDays') is not None else '缺失'} 天）",
        f"日均成交额：{_yuan(previous.get('averageAmountYuan'))}",
    ]
    if previous.get("note"):
        lines.append(f"涨跌说明：{previous['note']}")
    lines.append(
        "相对均线："
        f"5 日 {price.get('ma5') or '缺失'}，"
        f"10 日 {price.get('ma10') or '缺失'}，"
        f"20 日 {price.get('ma20') or '缺失'}"
    )
    if price.get("maNote"):
        lines.append(f"均线说明：{price['maNote']}")
    return "\n".join(lines)


def _metric_lines(title: str, block: object) -> list[str]:
    data = _mapping(block)
    return [f"{title}："] + [f"{label}：{_metric_text(data.get(key))}" for key, label in _METRIC]


def _earnings(package: Mapping) -> str:
    earnings = package.get("earnings")
    if not isinstance(earnings, Mapping):
        return "业绩另算：无"
    kind = _KIND.get(str(earnings.get("kind") or ""), earnings.get("kind") or "材料未提供")
    period = earnings.get("period") or "材料未提供"
    message = earnings.get("message") or "材料未提供"
    lines = [f"业绩另算（{kind}，期次 {period}，来源消息 {message}）："]
    for key, title in _BLOCKS:
        lines.extend(_metric_lines(title, earnings.get(key)))
    if "ytdYoy" in earnings:
        lines.extend(_metric_lines("年初至报告期末同比", earnings.get("ytdYoy")))
    lines.append(f"归母减扣非：{_metric_text(earnings.get('netMinusDeducted'))}")
    non_recurring = _mapping(earnings.get("nonRecurring"))
    lines.append(f"非经常性损益（本报告期）：{_metric_text(non_recurring.get('current'))}")
    lines.append(f"非经常性损益（年初至报告期末）：{_metric_text(non_recurring.get('ytd'))}")
    lines.append(f"基数（上年同期归母净利）：{_metric_text(_mapping(earnings.get('base')).get('netProfit'))}")
    return "\n".join(lines)


def _gaps(package: Mapping) -> str:
    gaps = package.get("gaps")
    if not isinstance(gaps, list) or not gaps:
        return "备料缺口：无"
    return "备料缺口：\n" + "\n".join(f"- {item}" for item in gaps)


def user_prompt(package: Mapping) -> str:
    """把一条数据包渲染成评估材料。不读取数据包以外的状态。"""
    if not isinstance(package, Mapping):
        raise TypeError("数据包不是对象")
    sections = (
        _report(package), _company(package), _events(package), _original(package), _media(package),
        _snapshot(package), _price(package), _earnings(package), _gaps(package),
    )
    return "\n\n".join(sections)
