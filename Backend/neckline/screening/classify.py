"""DeepSeek 按公司分组分类：同一公司的窗口内消息一起判断，附窗口开始前的相关消息供核对是否旧事。"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from .deepseek import ChatError, DeepSeekClient
from .pool import CompanyPool
from .prefilter import Message, Window
from .text import excerpt_for

CONTRACT = "screening-classify-v2"
CST = timezone(timedelta(hours=8))

RELATIONS = ("主体", "受益方")
DIRECTIONS = ("利好", "利空", "中性")
FRESHNESS = ("新", "旧", "不确定")
IMPORTANCE = ("高", "中", "低")
EVENT_TYPES: dict[str, str] = {
    "订单合同": "公司作为卖方或服务方签订或中标的合同、订单，或有金额的框架协议",
    "业绩向好": "业绩预增、业绩快报或定期报告同比明显增长、扭亏为盈",
    "并购扩张": "收购资产或股权、对外投资、新建或扩建产能、重大项目落地",
    "政策受益": "政策、监管或行业规划直接利好公司主营业务",
    "商业化进展": "新产品发布、获批、认证、注册证、量产、首次交付、进入重要客户供应链、技术突破落地",
    "经营数据": "公司自身的产品提价、产销量或经营数据变化",
    "回购增持": "公司回购股份，控股股东、实控人或高管增持",
    "战略合作": "签署战略合作协议，没有金额或金额不明",
    "控制权变更": "控股股东或实际控制人变更，如国资入主",
    "股权激励": "股权激励、员工持股计划",
    "融资发行": "定增、可转债、配股等融资事项及其进展",
    "行业动态": "行业层面的价格、供需、政策变化，不是公司自身的事件",
    "立案处罚": "立案调查、行政处罚、监管函、问询函、诉讼或刑事判决",
    "业绩下滑": "业绩预减、亏损、由盈转亏",
    "终止撤回": "终止重组、收购、发行，或撤回申请",
    "退市风险": "退市、风险警示（ST）及相关事项",
    "减持解禁": "股东减持、限售股解禁、股份质押或冻结",
    "程序性事项": "停牌、复牌、股价异常波动公告、股东大会或董事会决议、章程修订、人事变动、例行付息等",
    "其他": "以上都不是",
}

SYSTEM_PROMPT = """你是 A 股创业板消息分类员。用户每个交易日开盘前看一份晨报，只关心两件事：一是利好；二是新，即事件在本期窗口内（上个交易日收盘之后）首次公开、市场还没有交易过。你只做分类，不做投资建议。

输入按公司分组。每组给出一家公司（六位代码、名称、主营），本期窗口内点名它的消息（编号以 m 开头），以及窗口开始之前已经出现过的相关消息（编号以 h 开头，只用来核对是不是旧事）。公司是程序按名称或代码匹配出来的，可能误匹配。

每组输出：
- events：这家公司在本期消息里涉及的事件。同一件事有多条消息时合并成一个事件，ids 列出支持它的全部 m 编号。
- mentions：只是顺带提及或误匹配的 m 编号，比如行情播报、涨跌排行、龙虎榜、融资融券、板块成分、研报或概念股罗列里顺带出现，没有关于这家公司的具体事件。
本组每个 m 编号必须出现且只出现一次：要么在某个事件的 ids 里，要么在 mentions 里。不要输出 h 编号。

事件字段：
- relation：主体｜受益方。主体是指这家公司就是事件主体（公司公告，公司自身的经营、业绩、资本运作、监管事项）；受益方是指事件主体是别人，但消息明确点名这家公司，并说明它会受到具体影响（如客户、合作方、供应商）。
- type：只能从下列标签中选一个：
{types}
  公司作为买方的采购（设备、算力卡、原材料等）不算订单合同，按用途归入并购扩张（扩产或投入新业务）或其他。意向协议、备忘录、需求预测等没有约束力的，在 reason 里写明。
- direction：利好｜利空｜中性。只看事件本身对公司基本面或预期的影响；股价涨跌、资金流向不是依据。公司自己说明影响不大的，按中性。
- firstDisclosed：事件首次公开的时间，尽量写到「YYYY-MM-DD HH:MM」；只能判断到日期就写「YYYY-MM-DD」；判断不了写 null。
- fresh：新｜旧｜不确定。
  - 新：事件在本期窗口内首次公开。已有事项出现了实质性新进展（获批、签约、交付、业绩落地等），按新进展首次公开的时间判断。
  - 旧：事件在窗口开始之前已经公开，包括上个交易日盘中（收盘前）公开的，以及更早的。h 编号里已经出现过的同一件事，算旧。
  - 不确定：看不出首次公开的时间，也无法从 h 编号核对。
  - 稿件类型的提示：盘前要闻、公告集锦、晚间公告这类整理，列的多是上个交易日收盘后发布的公司公告，可按新判断，文中注明了更早日期的按日期；回顾、复盘、综述、券商点评、行业稿和投资专栏里提到的公司事件，大多是此前已经公开的，除非文中写明是窗口内首次公布，否则按旧或不确定。
- importance：高｜中｜低。
  - 高：可能明显改变公司收入、利润或估值预期，如金额大或占营收比例高的订单、大幅预增或扭亏、重大并购、核心产品获批或量产。
  - 中：有实质内容，但影响有限或尚待落地。
  - 低：影响很小或象征性，如小额合同、荣誉奖项、常规合作、公司自己说明影响不大的事项。
- headline：一句话事件标题，不超过 30 个汉字，写清这家公司做了什么或发生了什么。
- reason：一句话说明判断依据，不超过 60 个汉字，引用消息里的具体数字或事实，不得编造消息里没有的信息。

只输出一个 JSON 对象：{{"companies":[{{"company":"六位代码","events":[{{"ids":["m…"],"relation":"…","type":"…","direction":"…","firstDisclosed":null,"fresh":"…","importance":"…","headline":"…","reason":"…"}}],"mentions":["m…"]}}]}}"""


def system_prompt() -> str:
    types = "\n".join(f"  - {name}：{desc}" for name, desc in EVENT_TYPES.items())
    return SYSTEM_PROMPT.format(types=types)


@dataclass
class Event:
    company: str  # 300002.SZ
    ids: list[str]
    relation: str
    type: str
    direction: str
    first_disclosed: str | None
    fresh: str
    importance: str
    headline: str
    reason: str


@dataclass
class CompanyResult:
    company: str
    message_ids: list[str]
    events: list[Event] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class BatchOutcome:
    index: int
    companies: list[str]
    attempt: int
    usage: Mapping[str, Any] = field(default_factory=dict)
    seconds: float = 0.0
    from_receipt: bool = False
    error: str | None = None


@dataclass
class Classification:
    by_id: dict[str, Message]
    results: dict[str, CompanyResult]
    batches: list[BatchOutcome]


def _fmt(ts: datetime) -> str:
    return ts.astimezone(CST).strftime("%m-%d %H:%M")


def _full(ts: datetime) -> str:
    return ts.astimezone(CST).strftime("%Y-%m-%d %H:%M")


def render_company(code: str, pool: CompanyPool, profile_summary: str | None, messages: Sequence[tuple[str, Message]],
                   history: Sequence[tuple[str, Message]], settings: Mapping[str, Any]) -> str:
    """一家公司一组；消息多时按 blockMaxChars 等比缩短每条摘录（不低于 minChars），保证一次调用装得下。"""
    ex = settings["excerpt"]
    limit = ex["limit"]
    while True:
        block = _render_company(code, pool, profile_summary, messages, history, settings, limit)
        if len(block) <= ex["blockMaxChars"] or limit <= ex["minChars"]:
            return block
        limit = max(ex["minChars"], int(limit * ex["blockMaxChars"] / len(block) * 0.95))


def _render_company(code: str, pool: CompanyPool, profile_summary: str | None, messages: Sequence[tuple[str, Message]],
                    history: Sequence[tuple[str, Message]], settings: Mapping[str, Any], limit: int) -> str:
    company = pool.companies[code]
    lines = [f"### {company.short_code} {company.name}"]
    if profile_summary:
        lines.append(f"主营：{profile_summary[: settings['profileChars']]}")
    lines.append("本期消息：")
    ex = settings["excerpt"]
    for mid, message in messages:
        names = sorted(set(message.companies.get(code, [])), key=len, reverse=True)
        body = excerpt_for(message.text, names, lead=min(ex["lead"], limit), window=ex["window"], limit=limit)
        head = f"[{mid}] {message.source_label}｜{_fmt(message.published_at)}"
        if message.title:
            head += f"｜{message.title}"
        lines.append(head)
        if body and body != message.title:
            lines.append(body)
    lines.append("窗口开始前的相关消息（只用于核对是否旧事）：")
    if not history:
        lines.append("（无）")
    hx = settings["history"]
    for hid, message in history:
        names = sorted(set(message.companies.get(code, [])), key=len, reverse=True)
        snippet = excerpt_for(message.text, names, lead=0, window=hx["window"], limit=hx["snippetChars"])
        title = message.title or ""
        lines.append(f"[{hid}] {_fmt(message.published_at)}｜{message.source_label}｜{title}｜{snippet}".rstrip("｜"))
    return "\n".join(lines)


def pick_history(code: str, pool: CompanyPool, items: Sequence[tuple[str, Message]], limit: int) -> list[tuple[str, Message]]:
    """核对用的旧消息：优先标题里点名该公司的（多是公司自身的新闻），同等时取最近的；按时间顺序给出。"""
    name = pool.companies[code].name
    ranked = sorted(items, key=lambda pair: (name in pair[1].title, pair[1].published_at, pair[0]))
    return sorted(ranked[-limit:] if limit else [], key=lambda pair: (pair[1].published_at, pair[0]))


def user_prompt(window: Window, blocks: Sequence[str]) -> str:
    head = (f"本期窗口：上个交易日收盘 {_full(window.start)} 至 {_full(window.end)}（北京时间），窗口内首次公开的才算新。\n"
            f"共 {len(blocks)} 家公司，逐家输出。\n\n")
    return head + "\n\n".join(blocks)


def make_batches(sizes: Mapping[str, int], order: Sequence[str], *, max_companies: int, max_chars: int) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    total = 0
    for code in order:
        if current and (len(current) >= max_companies or total + sizes[code] > max_chars):
            batches.append(current)
            current, total = [], 0
        current.append(code)
        total += sizes[code]
    if current:
        batches.append(current)
    return batches


def _clean(value: Any, allowed: Sequence[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def parse_batch(content: str, expected: Mapping[str, Sequence[str]]) -> dict[str, CompanyResult]:
    """逐家公司校验：每个 m 编号恰好出现一次、取值都在枚举内；不合格的公司整家标为缺口，不猜。"""
    results = {code: CompanyResult(company=code, message_ids=list(ids)) for code, ids in expected.items()}
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = None
    rows = data.get("companies") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        for result in results.values():
            result.error = "输出不是约定的 JSON"
        return results
    by_short = {code.split(".")[0]: code for code in expected}
    seen: set[str] = set()
    for row in rows:
        code = by_short.get(str((row or {}).get("company") or "")[:6]) if isinstance(row, dict) else None
        if code is None or code in seen:
            continue
        seen.add(code)
        result = results[code]
        ids = set(expected[code])
        events, used, problem = [], [], None
        for item in row.get("events") or []:
            fields = (_clean(item.get("relation"), RELATIONS), _clean(item.get("type"), tuple(EVENT_TYPES)),
                      _clean(item.get("direction"), DIRECTIONS), _clean(item.get("fresh"), FRESHNESS),
                      _clean(item.get("importance"), IMPORTANCE)) if isinstance(item, dict) else (None,)
            event_ids = item.get("ids") if isinstance(item, dict) else None
            if None in fields or not isinstance(event_ids, list) or not event_ids:
                problem = "事件字段无效"
                break
            first = item.get("firstDisclosed")
            events.append(Event(company=code, ids=[str(i) for i in event_ids], relation=fields[0], type=fields[1],
                                direction=fields[2], first_disclosed=first if isinstance(first, str) and first else None,
                                fresh=fields[3], importance=fields[4],
                                headline=str(item.get("headline") or "").strip(),
                                reason=str(item.get("reason") or "").strip()))
            used.extend(str(i) for i in event_ids)
        mentions = [str(i) for i in (row.get("mentions") or [])] if isinstance(row.get("mentions"), list) else None
        if problem is None and mentions is None:
            problem = "mentions 缺失"
        if problem is None:
            accounted = used + mentions
            if sorted(accounted) != sorted(ids):
                problem = "消息编号没有恰好各出现一次"
        if problem:
            result.error = problem
            continue
        result.events, result.mentions = events, mentions
    for code, result in results.items():
        if code not in seen and result.error is None:
            result.error = "输出里没有这家公司"
    return results


def classify(messages: Sequence[Message], history: Sequence[Message], pool: CompanyPool,
             profiles: Mapping[str, str], client: DeepSeekClient, *, window: Window,
             settings: Mapping[str, Any]) -> Classification:
    ids = {f"m{i:04d}": m for i, m in enumerate(messages, 1)}
    hids = {f"h{i:04d}": m for i, m in enumerate(history, 1)}
    per_company: dict[str, list[tuple[str, Message]]] = {}
    for mid, message in ids.items():
        for code in message.companies:
            per_company.setdefault(code, []).append((mid, message))
    hist_company: dict[str, list[tuple[str, Message]]] = {}
    for hid, message in hids.items():
        for code in message.companies:
            hist_company.setdefault(code, []).append((hid, message))
    blocks = {code: render_company(code, pool, profiles.get(code), per_company[code],
                                   pick_history(code, pool, hist_company.get(code, []), settings["history"]["maxItems"]),
                                   settings)
              for code in per_company}
    order = sorted(per_company, key=lambda c: (min(m.published_at for _, m in per_company[c]), c))
    expected = {code: [mid for mid, _ in per_company[code]] for code in per_company}
    system = system_prompt()
    results: dict[str, CompanyResult] = {}
    outcomes: list[BatchOutcome] = []

    def run(index: int, batch: list[str], attempt: int) -> tuple[BatchOutcome, dict[str, CompanyResult]]:
        outcome = BatchOutcome(index=index, companies=batch, attempt=attempt)
        prompt = [{"role": "system", "content": system},
                  {"role": "user", "content": user_prompt(window, [blocks[c] for c in batch])}]
        wanted = {c: expected[c] for c in batch}
        try:
            result = client.complete(prompt, settings["modelOptions"])
        except ChatError as exc:
            outcome.error = str(exc)
            return outcome, {c: CompanyResult(company=c, message_ids=wanted[c], error=f"调用失败：{exc}") for c in batch}
        outcome.usage, outcome.seconds, outcome.from_receipt = result.usage, result.seconds, result.from_receipt
        if result.finish_reason not in ("stop", None):
            outcome.error = f"finish:{result.finish_reason}"
        return outcome, parse_batch(result.content, wanted)

    pending = make_batches({c: len(b) for c, b in blocks.items()}, order,
                           max_companies=settings["batchMaxCompanies"], max_chars=settings["batchMaxChars"])
    for attempt in range(1, settings["maxAttempts"] + 1):
        with ThreadPoolExecutor(max_workers=settings["concurrency"]) as executor:
            done = list(executor.map(lambda pair: run(pair[0], pair[1], attempt), enumerate(pending)))
        retry: list[str] = []
        for outcome, batch_results in done:
            outcomes.append(outcome)
            for code, company_result in batch_results.items():
                results[code] = company_result
                if company_result.error:
                    retry.append(code)
        if not retry:
            break
        pending = [[code] for code in retry]  # 重试时每家公司单独一批，避免互相拖累
    return Classification(by_id=ids, results=results, batches=outcomes)
