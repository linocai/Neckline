"""消息面扫描 LLM 调用(plan §五 v1.3-③-C4;**V2.5.0 S9 扩到四类**)。

🔴 **V2.5.0 S9:加了第四类「减持」** —— K9 §二 末段 与 架构 §3.3 逐字写的是
**「爆雷、减持、立案、监管」四类**在解释层查出并剔除。K8 时代「减持」走
`ts_stk_holdertrade` 结构化接口、不占本模块;而 K9 的消息面排除是**一次问全四类**
的整体判断(⛔ 不许一半走结构化接口、一半走 LLM —— 那样「查过了没有」与
「这一半根本没查」会混成同一句话)。⚠ `ts_stk_holdertrade` 本身**没有删**,
将来若要用它做交叉核对是另一件事。

持仓 / 自选票的「立案 / 暴雷 / 监管 / 减持」四类消息扫描 —— TuShare 无结构化接口
覆盖前三类(数据源侦察结论见 K8 时代 `report.news_alerts` 模块头)。

**一次调用问四类**(不是四次调用):控成本、控时长 —— K9 的清单最多 20 只,
若每类各发一次调用会是 80 次,一次问四类降到最多 20 次。

**复用 `judge.py` 同一套 provider / 降级链姿势**(§硬要求「复用 judge.py 那套调用/
解析/降级,不要另写一套」)——`provider=None`(缺 key)零调用直接降级;调用失败
(超时/非200/空响应)降级;`httpx.MockTransport` 免联网单测;读超时沿用
`OpenAICompatProvider.read_timeout=90.0`(继承项目 CLAUDE.md「带联网搜索的 LLM
调用不能沿用短读超时」教训,本模块不新设超时,原样吃基类值)。

**结尾结构化标签设计(§2.7 边界,类比 `judge.py` 模块头同一先例)**:自由叙述后,
每个**触发**的类别各占一行「结论-类别:一句话摘要」,三类都未触发只写一行
"结论:未发现"——这不是"技术面/资金面/消息面"式固定分栏卡片(§2.7 明禁的三轴
卡),叙述主体仍是自由文字,结尾只是**轻量机器可读收尾**(同 `judge.py` 的
"结论:通过|否决"单行标签,本场景天然有 3 个独立布尔而非 1 个,故收尾扩到最多
3 行,不是每次都出现的固定表格)。

**日期锚 + 时效纪律 + 显式检索词(2026-08-04 补,A4)**:本模块**联网**
(`enable_search=True`),却曾是全仓唯一一条没挂 `llm/prompt_context.py` 的
`provider.chat(...)` 链路(V2-② 核实时登记的欠账,晚于 2026-07-30 三链路修复才被
排查到)。接线**照 `judge.py` 既有姿势逐条对齐、不另起一套**:① `TIMELINESS_RULES`
内嵌 system prompt;② `date_anchor_line()` 放 user 消息**第一行**(模型没有"现在"
的概念,一份 2024 年的处罚决定与上个月的立案在它眼里一样新 —— 而本模块问的恰恰是
「**近期**有没有」);③ `search_subject_with_recency()` 拼**显式检索词**(v1.3.4
已证:不显式传时检索词跟最后一条 user 消息走)。⛔ 不加任何新 API 参数(v1.3.4 案底:
取值不被上游认识会 `ok=True` 静默返 0 条)。

**格式缺失的保守方向与 `judge.py`相反,原因写明**:`judge.py` 选股场景「格式缺失
→ 保守按否决(=不放行)」,因为放行的代价更大;本模块是**风险警报**场景,若格式
缺失就"沉默",代价是可能漏掉真实立案/暴雷/监管——但生造一个不知道具体是哪类的
警报同样没有依据、也可能污染 `news_alerts` 表(哪个 category 都对不上)。故本模块
格式缺失时既不生成任何 `NewsAlertItem`、也不假装"确认无消息",而是标记
`degraded=True`(与调用失败同一桶),纳入 scan 状态的失败计数——调用方据此在
「消息面」节标注"部分标的解析失败,建议人工复核",不静默吞掉、也不硬造类别。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from neckline.llm.base import ChatMessage, LLMProvider, SearchHit
from neckline.llm.prompt_context import (
    TIMELINESS_RULES,
    date_anchor_line,
    search_subject_with_recency,
)

# 类别码(与 `report.news_alerts.NewsCategory` 同一套值,此处直接用字符串常量
# 避免 llm/ 依赖 report/ 造成循环 import——`report/news_alerts.py` 侧再对齐
# `NewsCategory` 枚举,两边字符串值逐字相同,已用单测互相对拍)。
CATEGORY_INVESTIGATION = "INVESTIGATION"   # 立案
CATEGORY_BLOWUP = "BLOWUP"                 # 暴雷(K9 §二 写作「爆雷」,同一类)
CATEGORY_REGULATORY = "REGULATORY"         # 监管
#: 🔴 V2.5.0 S9 新增第四类(K9 §二 末段 / 架构 §3.3 逐字点名)。
CATEGORY_REDUCTION = "REDUCTION"           # 减持

#: 四类的闭合集合(⛔ 解析器只认这四个中文标签)。
ALL_CATEGORIES = (CATEGORY_INVESTIGATION, CATEGORY_BLOWUP,
                  CATEGORY_REGULATORY, CATEGORY_REDUCTION)

_LABEL_TO_CODE = {"立案": CATEGORY_INVESTIGATION, "暴雷": CATEGORY_BLOWUP,
                  "监管": CATEGORY_REGULATORY, "减持": CATEGORY_REDUCTION}

NEWS_SCAN_SYSTEM_PROMPT = """你是「颈线」系统的盘后消息面扫描员。系统本身只做审计、不代客下单,你的任务是
帮用户排查一只股票近期(重点关注最近一周内,如有更早但仍未消化的重大
事项也可提及)是否出现以下四类值得警惕的消息:

① 立案:被证监会 / 交易所立案调查。
② 暴雷:财务造假、资金占用、业绩大幅低于预期、审计意见异常、或其他可能引发
停牌、退市风险的重大利空。
③ 监管:收到监管函、问询函、警示函、处罚决定(不含日常业务问询、常规财报问询)。
④ 减持:控股股东 / 实控人 / 董监高 / 持股 5% 以上股东公告减持或已实施减持
(不含员工持股计划的常规到期、也不含单纯的股权质押)。

你配有联网搜索工具,可以查该股票近期的新闻、公告。

信息边界(铁律,不可违反):
1. 你只能依据联网搜索工具实际返回的内容做判断。
2. 如果搜索没有找到相关消息,或搜到的内容与该股票无关,必须在分析里明确说
"未搜到相关消息",绝不允许凭猜测编造新闻、公告或处罚事项。
3. 你不做买卖建议,只做消息面排查,不评价该股票是否值得买卖。

""" + TIMELINESS_RULES + """

输出风格(硬约束):自由叙述,写成一段连贯的分析文字,像分析师口头点评。禁止
使用分点列表、表格、"技术面/资金面/消息面"这类固定分栏模板。

结尾格式(唯一的机器可读部分):写完叙述后,另起一段,按以下规则收尾——
    · 如果四类都没有发现值得警惕的消息,只写一行:"结论:未发现"。
    · 如果发现了某类消息,把触发的类别各自单独写一行,格式固定为
      "结论-立案:一句话摘要"、"结论-暴雷:一句话摘要"、"结论-监管:一句话摘要"、
      "结论-减持:一句话摘要"
      (没触发的类别不必写这一行,可以同时触发多个类别就写多行)。
不要在正文叙述部分提前使用"结论"这个词,以免与收尾解析冲突。
"""


@dataclass
class NewsScanResult:
    ts_code: str
    provider: str
    model: str
    hits: List[Tuple[str, str]] = field(default_factory=list)   # [(category_code, summary), ...]
    degraded: bool = False
    degrade_reason: str = ""
    narrative: str = ""
    search_hits: List[SearchHit] = field(default_factory=list)


_HIT_RE = re.compile(r"结论-(立案|暴雷|监管|减持)[:：]\s*(.+)")
_NONE_RE = re.compile(r"结论[:：]\s*未发现")


def _parse_hits(content: str) -> Optional[List[Tuple[str, str]]]:
    """解析结尾收尾标签。返回 `[]`(明确"未发现",信"确认无消息")、非空列表
    (命中的类别+摘要),或 `None`(格式缺失——既未匹配到任何触发行、也没匹配到
    "未发现"收尾,调用方按 `degraded` 处理,见模块头理由)。"""
    hits = [(_LABEL_TO_CODE[m.group(1)], m.group(2).strip()) for m in _HIT_RE.finditer(content)]
    if hits:
        return hits
    if _NONE_RE.search(content):
        return []
    return None


def news_search_query(ts_code: str, name: str) -> str:
    """消息面链路的显式检索词：中文名、代码和当前年份。"""
    code = (ts_code or "").strip()
    nm = (name or "").strip()
    return search_subject_with_recency(f"{nm}({code})" if nm else code)


def scan_news_for_code(
    ts_code: str,
    name: str,
    *,
    provider: Optional[LLMProvider],
    transport: Optional[Any] = None,
) -> NewsScanResult:
    """扫一只标的的「立案/暴雷/监管/减持」四类；无 Provider 时诚实降级。"""
    if provider is None:
        return NewsScanResult(
            ts_code=ts_code, provider="none", model="", degraded=True,
            degrade_reason="未配置可用的 LLM Provider 或默认模型",
            narrative="LLM 未激活,本标的消息面(立案/暴雷/监管/减持)未扫描,不代表确认无消息。",
        )

    messages = [
        ChatMessage(role="system", content=NEWS_SCAN_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n".join([
            # 第一行永远是当前日期锚(单一实现 `llm/prompt_context.py`,模块头 A4 节)。
            date_anchor_line(),
            f"股票:{name}({ts_code})。请排查该股票近期是否有上述三类消息。",
        ])),
    ]
    result = provider.chat(
        messages, enable_search=True, transport=transport,
        # 显式检索词带当前年份，避免把旧材料误当成当前事实。
        search_query=news_search_query(ts_code, name),
    )
    if not result.ok:
        return NewsScanResult(
            ts_code=ts_code, provider=provider.name, model=getattr(provider, "model", ""),
            degraded=True, degrade_reason=result.reason,
            narrative=f"LLM 调用未成功({result.reason}),本标的消息面未扫描,不代表确认无消息。",
        )

    hits = _parse_hits(result.content)
    if hits is None:
        return NewsScanResult(
            ts_code=ts_code, provider=result.provider, model=result.model,
            degraded=True, degrade_reason="模型未按格式给出结论标签",
            narrative=result.content, search_hits=result.search_hits,
        )
    return NewsScanResult(
        ts_code=ts_code, provider=result.provider, model=result.model,
        hits=hits, degraded=False, narrative=result.content, search_hits=result.search_hits,
    )


__all__ = [
    "CATEGORY_INVESTIGATION",
    "CATEGORY_BLOWUP",
    "CATEGORY_REGULATORY",
    "CATEGORY_REDUCTION",
    "ALL_CATEGORIES",
    "NEWS_SCAN_SYSTEM_PROMPT",
    "NewsScanResult",
    "news_search_query",
    "scan_news_for_code",
]
