"""DeepSeek 按公司分组分类：同一公司的窗口内消息一起判断，附窗口开始前的相关消息供核对是否旧事。

合同 screening-classify-v3：种类用中文短词，并给出 channel 与 amountYuan。
"""
from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from .deepseek import ChatError, ChatUnknown, DeepSeekClient
from .pool import CompanyPool
from .prefilter import Message, Window
from .text import excerpt_for

CONTRACT = "screening-classify-v3"
CST = timezone(timedelta(hours=8))

RELATIONS = ("主体", "受益方")
DIRECTIONS = ("利好", "利空", "中性")
FRESHNESS = ("新", "旧", "不确定")
IMPORTANCE = ("高", "中", "低")
CHANNELS = ("公司公告", "媒体报道", "调研纪要", "互动平台", "第三方综述")
# 提示词里的分组只告诉模型每个种类是什么；收不收以配置为准，这里不是选择规则的默认值。
PROMPT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("收进名单（direction 写利好）", (
        "业绩预增", "订单中标", "收购投资", "上市融资", "回购增持", "股权激励", "分红",
        "控制权变更", "产品获批", "一次性收益", "热点合作",
    )),
    ("由用户判断（direction 按事实，不限利好）", ("出售资产", "股份转让", "大额采购")),
    ("不收", (
        "临床进展", "产品发布", "投产进展", "经营动态", "荣誉认证", "财务投资", "减持他股",
        "融资进展", "回购进展", "重组进展", "业绩落地", "普通合作", "小额订单", "中标候选",
        "经营数据", "立案处罚", "业绩下滑", "终止撤回", "退市风险", "减持解禁", "程序性事项",
        "行业动态", "其他",
    )),
)
EVENT_TYPES: dict[str, str] = {
    "业绩预增": "窗口内首次发出的同比增长业绩（净利增长或扭亏）：业绩预告、快报，或者同一报告期事先没有发过预告、快报的定期报告（季报、半年报、年报）。金额小、环比下降仍算。例：优博讯前三季度净利预增 72%–95%；银禧科技同比预增但第三季度环比下降；杰创智能预增但金额只有几千万。营收大降或累计利润下滑不要用本类。",
    "订单中标": "已经确定的中标、中选或签约，含金额大的意向书、备忘录、需求预测（在 reason 里写明有没有约束力），以及把产品或技术对外授权许可、拿到首付款或里程碑款的协议。例：华康洁净中标约 6500 万；海特生物滴眼液海外授权，首付款加近期里程碑 700 万美元；申昊科技收到中选通知书并按份额计算；致尚科技 5 亿美元需求预测备忘录。候选、公示、预中标不要用本类。公司自己买东西不要用本类。",
    "收购投资": "收购股权或资产、增资取得控制、合资设立新业务（含合资研发新药），以及首次公告的新建产能、新基地投资项目。首次披露即算，框架协议和筹划阶段也算；已开工项目的试车、试生产不算，用投产进展。例：苏州天脉收购控股并表；汇创达签框架协议拟收购，尚在筹划；隆华新材增资控股；花园生物与国家实验室合资研发新药；鸿富瀚拟投 10.07 亿元建机器人精密金属组件基地。",
    "上市融资": "港股或美股上市递表、申报公募 REITs、定增第一次预案、定增注册批复。按利好。例：百普赛斯再次向港交所递表；节能国祯拟申报公募 REITs；四会富仕定增获注册批复。",
    "回购增持": "首次披露回购方案，或回购股数、金额比原方案增加（从无到有也算）；控股股东或实控人首次公告增持计划。例：贝泰妮公告中的回购。上调价格上限、实施完毕不要用本类。",
    "股权激励": "股权激励草案首次披露。按利好。",
    "分红": "现金分红或中期分红。按利好。例：贝泰妮首次中期分红。",
    "控制权变更": "首次筹划或首次公告控制权变更，例如国资入主。按利好。发行完毕才形成的控股变化不要用本类。",
    "产品获批": "药品上市许可（含仿制药）、医疗器械注册证、游戏版号、汽车定点。例：福安药业仿制原料药获批上市；贝泰妮三类医疗器械注册证；中青宝游戏获版号；安培龙传感器获主机厂定点。获批临床不要用本类。",
    "一次性收益": "政府补助、委托贷款收取的利息，以及消息重点是已经确定的出售收益而不是出售行为本身。按利好。例：振芯科技政府补助 2142 万；泰和科技 1.1 亿委托贷款年息 6%。出售或剥离子公司、资产不要用本类。",
    "热点合作": "贴当前市场热点的战略合作。热点由你判断，例如 AI、算力、微短剧；没有金额也算。例：捷成股份与吉视传媒战略合作 AI 微短剧。第三方综述里顺带一提且没有金额，不要用本类。",
    "出售资产": "出售子公司或资产、剥离业务，不论金额，包括卖给实控人。方向按事实写，可以是利空或中性，不要改成利好。例：上海艾录出售老厂区；苏大维格挂牌转让亏损子公司；久量股份挂牌出售小家电子公司；运达股份挂牌转让风电项目公司；民爆光电把子公司股权卖给实控人。",
    "股份转让": "股东协议转让这家公司的股份。方向按事实写。例：汇金科技实控人协议转让 6% 股份，控制权不变。股东减持、解禁不要用本类。",
    "大额采购": "公司作为买方的大额采购，例如算力卡。方向按事实写。例：奥尼电子采购 GPU 算力卡。不要标成订单中标。",
    "临床进展": "获批临床、入组、各期临床试验。例：康泰生物 I 期临床首例入组。",
    "产品发布": "发布会、新品亮相、展会，没有订单也没有金额。例：绿盟科技新品发布会。",
    "投产进展": "试车、试生产、投产，还没有带来业绩。例：华信新材自建项目进入试车、试生产。",
    "经营动态": "没有业务增量的日常经营。例：普瑞眼科旗下医院开展新的晶体手术。",
    "荣誉认证": "荣誉、榜单、标准采标、认证。例：新晨科技入选 IDC 榜单；汤臣倍健产品成为团体标准首批采标产品。",
    "财务投资": "买基金、买理财等财务性出资。例：新国都认购产业基金份额。",
    "减持他股": "公司卖掉所持其他公司的股份。例：力源信息减持所持云汉芯城股份。",
    "融资进展": "定增方案调整、受理、问询、发行完毕，以及 IPO 的常规问询或补充材料。例：光韵达调整定增方案；辰安科技定增发行完毕；鼎捷数智被要求补充港股 IPO 备案材料。",
    "回购进展": "上调回购价格上限，或回购实施完毕。例：满坤科技上调回购价上限；天宇股份回购实施完毕。",
    "重组进展": "重组或收购仍在推进，条件没有变化。例：兆日科技公告重组有序推进。",
    "业绩落地": "同一报告期此前已经发过业绩预告或快报（h 编号里看得到，或消息里写明），这次只是正式报告的数字落地。看不到此前的预告或快报，就不是业绩落地，用业绩预增。",
    "普通合作": "不贴当前热点的合作；或第三方综述、专栏里转述的、没有金额的合作。例：西点药业与维亚生物的 AI 研发合作没有金额，且出自第三方综述。",
    "小额订单": "订单或合同金额不足 1000 万元。例：立昂技术沙特网络工程约 746 万。",
    "中标候选": "中标候选人、公示期、预中标，还没有确定拿到。第一中标候选人公示用本类。",
    "经营数据": "公司自称影响不大的涨价、产销或其他经营数据。direction 写中性。例：金丹科技产品统一提价 8%，公司称影响不大。",
    "立案处罚": "立案调查、行政处罚、监管函、问询函、诉讼或刑事判决。负面事件里附带的一点收入或损益也用本类，不要拆成利好。例：金龙机电刑事判决追回损失。",
    "业绩下滑": "业绩预减、亏损、由盈转亏，或营收大幅下降、累计净利下滑。例：聚灿光电单季净利增长，但营收大降、前三季度净利下滑。",
    "终止撤回": "终止重组、收购、发行，或撤回申请。",
    "退市风险": "退市、风险警示（ST）及相关事项。",
    "减持解禁": "股东减持这家公司的股份、限售股解禁、股份质押或冻结。协议转让不要用本类。",
    "程序性事项": "停牌、复牌、股价异常波动、股东大会或董事会的例行决议、章程修订、人事变动、例行付息。",
    "行业动态": "行业价格、供需、政策、规划、行业会议或板块消息，不是这家公司自己的事件。例：北陆药业因医药工业规划被点名。",
    "其他": "以上都不是。",
}

SYSTEM_PROMPT = """你是 A 股创业板消息分类员。用户每个交易日开盘前看一份晨报，只要公司自己的、窗口内首次披露的事件：能直接改变公司价值，或是重要的资本、资产动作。边角料、炒冷饭、沾别人光、公司自称影响不大的经营数据，都不要收。分量重但利好利空不明的资产和股权动作也要单列出来，交给用户自己判断。你只做分类，不做投资建议；不要为了让一条消息进名单而改种类或方向。收不收由程序决定，所以临床、小额订单、互动平台回复、第三方综述，只要是这家公司的具体事件，也要输出事件，不要塞进 mentions。

输入按公司分组。每组给出一家公司（六位代码、名称、主营），本期窗口内点名它的消息（编号以 m 开头），以及窗口开始之前已经出现过的相关消息（编号以 h 开头，只用来核对是不是旧事）。公司是程序按名称或代码匹配出来的，可能误匹配。消息标题可能是整篇汇总稿的总标题，讲的是别的公司；只根据正文里这家公司自己的那一节判断。

每组输出：
- events：这家公司在本期消息里涉及的事件。同一件事有多条消息时合并成一个事件，ids 列出支持它的全部 m 编号。同一条消息里的几件事要拆开，例如贝泰妮同一份公告里的医疗器械注册证、回购和首次中期分红，分成产品获批、回购增持、分红。
- mentions：只是顺带提及或误匹配的 m 编号，比如行情播报、涨跌排行、龙虎榜、融资融券、板块成分、研报或概念股罗列里顺带出现，没有关于这家公司的具体事件。
本组每个 m 编号都必须有着落：要么在事件的 ids 里，要么在 mentions 里。同一条消息里有几件事、拆成了几个事件时，这个编号可以出现在每个相关事件的 ids 里；同一个编号不能既在事件里又在 mentions 里。不要输出 h 编号。

事件字段（relation、type、direction、channel、fresh、importance 只能原样填写下面列出的词之一，不要填公司名、不要用「｜」拼接多个词，也不要加别的文字）：
- relation：只能是「主体」或「受益方」。主体是这家公司自己的经营、业绩、订单、资本运作或监管事项。受益方是事件主体是别人，消息点名这家公司会受到影响。政策、规划、行业会议、板块行情能套上很多公司，不是主体：relation 写受益方，或 type 写行业动态。受益方也要输出事件。
- direction：利好｜利空｜中性。只看事件本身对公司基本面或预期的影响，不看股价和资金。公司自称影响不大的涨价、产销等经营数据，写中性并用经营数据。其他种类不因公司「影响不大」「存在不确定性」这类措辞改成中性：收购、增资、获批、中标等事件本身是正面的，写利好（例：隆华新材增资控股，公司称对业绩影响不大，仍写利好）。出售资产、股份转让、大额采购按事实写，中性和利空都照实标，不要改成利好。
- channel：公司公告｜媒体报道｜调研纪要｜互动平台｜第三方综述。
  - 公司公告：公司公告本身。公告集锦、公告精选、快讯转述的公告，也算公司公告。
  - 媒体报道：媒体就这件事写的报道，不是公告集锦，也不是互动回复。
  - 调研纪要：投资者关系活动记录（表）、机构调研纪要、业绩说明会，公司在活动里主动介绍的经营事实。例：贝泰妮投资者关系活动介绍三类医疗器械注册证；安培龙投资者关系活动记录表写明获主机厂定点。
  - 互动平台：互动易、e 互动、上证 e 互动上，回答投资者提问的问答回复。例：法本信息在互动易答复产品通过算法备案。
  - 第三方综述：行业稿、综述、专栏、港股收盘综述里顺带提到、没有官方出处的事件。监管机构公布的批准名单或批文（游戏版号名单、药监批准等），哪怕是行业稿转述，也按公司公告。例：中青宝游戏出现在新闻出版署 9 月版号名单里，按公司公告。
- amountYuan：事件金额折成人民币元的数字；消息里没有金额就写 null。外币若消息给了人民币折算，用那个数；没给就按大致汇率估算，并在 reason 里写「约」。写数字，不要写「万元」或字符串。
- type：只能从下列种类里选一个。
{types}
- firstDisclosed：事件首次公开的时间，尽量写到「YYYY-MM-DD HH:MM」；只能判断到日期就写「YYYY-MM-DD」；判断不了写 null。
- fresh：新｜旧｜不确定。fresh 只回答「这条消息是不是窗口内第一次公开」，不回答收不收。
  - 新：事件在本期窗口内首次公开（上个交易日收盘之后）。同一件事如果出现了新的事实（这次才签约、这次才获批），按这个新事实首次公开的时间判断。
  - 旧：窗口开始之前已经公开，包括上个交易日盘中（收盘前），以及更早的。h 编号里已经出现过的同一件事，算旧。
  - 不确定：看不出首次公开的时间，也不能从 h 编号核对。看不出就写不确定，不要猜成新。
  - 稿件类型的提示：盘前要闻、公告集锦、晚间公告这类整理，列的多是上个交易日收盘后发布的公司公告，可按新判断，文中注明了更早日期的按日期；回顾、复盘、综述、券商点评、行业稿和投资专栏里提到的公司事件，大多是此前已经公开的，除非文中写明是窗口内首次公布，否则按旧或不确定。
- importance：高｜中｜低。
  - 高：可能明显改变收入、利润或估值，如金额大或占营收比例高的订单、大幅预增或扭亏、重大收购、核心产品获批、控制权变更。
  - 中：有实质内容，但影响有限或还没落地。
  - 低：影响很小或象征性，如小额订单、荣誉榜单、不贴热点的合作、公司自己说明影响不大的事项。
- headline：一句话事件标题，不超过 30 个汉字，写清这家公司做了什么或发生了什么。
- reason：一句话说明判断依据，不超过 60 个汉字，引用消息里的具体数字或事实，不得编造消息里没有的信息。外币按大致汇率估算时，这里写上「约」。

首次披露，和炒冷饭、完成类、进展类，不是一回事：
- 业绩第一次预告或快报，或者事先没有预告、快报的季报、半年报、年报首次给出同比增长，用业绩预增。同一报告期以前预告过、这次只是正式数字落地，fresh 可以是新，type 必须是业绩落地；看不到以前的预告，不要判成业绩落地。
- 回购、增持只在首次方案或规模（股数或金额）增加时用回购增持。实施完毕、上调价格上限，用回购进展。
- 定增第一次预案、注册批复，用上市融资。方案调整、受理、问询、发行完毕，用融资进展。发行完毕才出现的控股股东变化，不要写成控制权变更。
- 重组仍是「有序推进」、条件没变，用重组进展。
- 临床各阶段用临床进展。合资开一家公司去做新药研发，是新设业务，用收购投资，不要用临床进展。
- 确定的中标、中选通知、签约，用订单中标；金额大的意向书和备忘录也算，reason 里写明没有约束力。不足 1000 万元用小额订单。中标候选人、公示期、预中标用中标候选。金额未知但已经确定中标的，仍用订单中标，amountYuan 写 null。
- 公司作为买方的采购用大额采购，不要用订单中标。

只输出一个 JSON 对象：{{"companies":[{{"company":"六位代码","events":[{{"ids":["m…"],"relation":"…","type":"…","direction":"…","channel":"…","amountYuan":null,"firstDisclosed":null,"fresh":"…","importance":"…","headline":"…","reason":"…"}}],"mentions":["m…"]}}]}}"""


def system_prompt() -> str:
    listed = [name for _, names in PROMPT_GROUPS for name in names]
    if set(listed) != set(EVENT_TYPES) or len(listed) != len(set(listed)):
        raise RuntimeError("种类分组与 EVENT_TYPES 不一致")
    blocks = []
    for title, names in PROMPT_GROUPS:
        lines = [f"{title}："]
        lines.extend(f"  - {name}：{EVENT_TYPES[name]}" for name in names)
        blocks.append("\n".join(lines))
    return SYSTEM_PROMPT.format(types="\n\n".join(blocks))


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
    channel: str
    amount_yuan: int | float | None


@dataclass
class CompanyResult:
    company: str
    message_ids: list[str]
    events: list[Event] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    error: str | None = None
    retryable: bool = True  # 结果不明的调用不重做，记为缺口


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


def _amount(value: Any) -> int | float | None:
    """合法金额返回数字（整数的浮点收成 int），null 返回 None；非法时抛 ValueError。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("金额不是数字")
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def parse_batch(content: str, expected: Mapping[str, Sequence[str]]) -> dict[str, CompanyResult]:
    """逐家公司校验：每个 m 编号都有着落（可分属几个事件，不能同时算提及）、取值都在枚举内；不合格的公司整家标为缺口，不猜。"""
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
            if not isinstance(item, dict):
                problem = "事件字段无效"
                break
            fields = (_clean(item.get("relation"), RELATIONS), _clean(item.get("type"), tuple(EVENT_TYPES)),
                      _clean(item.get("direction"), DIRECTIONS), _clean(item.get("fresh"), FRESHNESS),
                      _clean(item.get("importance"), IMPORTANCE), _clean(item.get("channel"), CHANNELS))
            event_ids = item.get("ids")
            try:
                if "amountYuan" not in item:
                    raise ValueError("缺少金额")
                amount = _amount(item["amountYuan"])
            except (TypeError, ValueError):
                problem = "事件字段无效"
                break
            if None in fields or not isinstance(event_ids, list) or not event_ids:
                problem = "事件字段无效"
                break
            first = item.get("firstDisclosed")
            events.append(Event(company=code, ids=[str(i) for i in event_ids], relation=fields[0], type=fields[1],
                                direction=fields[2], first_disclosed=first if isinstance(first, str) and first else None,
                                fresh=fields[3], importance=fields[4],
                                headline=str(item.get("headline") or "").strip(),
                                reason=str(item.get("reason") or "").strip(),
                                channel=fields[5], amount_yuan=amount))
            used.extend(str(i) for i in event_ids)
        # 漏写 mentions 当作没有提及；编号是否全有着落仍由下面的核对把关。写了但不是列表才算错。
        raw_mentions = row.get("mentions", [])
        mentions = [str(i) for i in raw_mentions] if isinstance(raw_mentions, list) else None
        if problem is None and mentions is None:
            problem = "mentions 缺失"
        if problem is None:
            # 同一条消息里有几件事时，同一个 m 编号可以出现在几个事件里；但每个编号都要有着落，
            # 不能既算事件又算提及，提及里也不能重复，不能出现本组以外的编号。
            in_events = set(used)
            if (len(mentions) != len(set(mentions)) or in_events & set(mentions)
                    or in_events | set(mentions) != ids):
                problem = "消息编号没有对上"
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
            retryable = not isinstance(exc, ChatUnknown)
            return outcome, {c: CompanyResult(company=c, message_ids=wanted[c], error=f"调用失败：{exc}",
                                              retryable=retryable) for c in batch}
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
                if company_result.error and company_result.retryable:
                    retry.append(code)
        if not retry:
            break
        pending = [[code] for code in retry]  # 重试时每家公司单独一批，避免互相拖累
    return Classification(by_id=ids, results=results, batches=outcomes)
