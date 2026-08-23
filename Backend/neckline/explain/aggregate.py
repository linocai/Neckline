"""资料聚合与日K 形态评价(V2.5.0 S9,架构 §3.3 / §八「LLM 的三个岗位」之二)。

**输出**(每只票):它是什么公司、当前消息面、在行业里的处境、位置与结构状态、
近期表现,以及**日K 形态的评价**。

    「日K 由 LLM 先看一遍并给出评价,我再自己看一遍,双重确认。」——架构 §3.3

🔴 **双盲**:本模块吃的是 `ExplainInput`,那个 DTO 里**根本没有**通道身份与排序位次
(见 `input.py`)。⛔ 本包零 import `neckline.k9`(守门 G5)。

🔴 **一只票一次调用**:清单最多 20 只。⚠ 刻意**不**把 20 只塞进同一个 prompt ——
那正是 K8 时代 §七 P0-40/P0-44 反复翻车的「大上下文 + 长结构化生成」形状;
而且一次批量调用失败会让**整天**的资料全缺,逐只调用则只缺那一只(逐只的保险丝)。

🔴 **消息面证据由调用方喂进来**(`news` 参数),⛔ 本模块不自己联网:
检索统一走 Tavily(`news_exclusion.py` 那一次调用),这里只是把已经拿到的证据
讲给用户听。**一只票只联一次网**。

⚠ **这一层的产出是给人读的散文,不是判据** —— 它不参与任何机械决策,
所以这里的自由文本是**对的**;⛔ 别把它做成打分或结论码。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from neckline.explain.input import ExplainInput
from neckline.explain.news_exclusion import NewsVerdict
from neckline.llm.base import ChatMessage, LLMProvider
from neckline.llm.json_block import split_narrative_and_json
from neckline.llm.prompt_context import TIMELINESS_RULES, date_anchor_line

logger = logging.getLogger(__name__)

EXPLAIN_SYSTEM_PROMPT = """你是「颈线」系统的盘后资料员。系统每天盘后交出一份 10-20 只的候选清单,你的任务是
把其中一只票讲成用户读得懂的东西,供他自己判断。

你要回答五件事(**只回答这五件**):
① 这是什么公司 —— 主营、所处产业环节、规模量级。
② 当前消息面 —— 只依据下面材料里给出的检索证据;材料里没有的,写"未取得相关消息",
   绝不允许凭猜测编造新闻、公告或处罚事项。
③ 在行业里的处境 —— 结合材料给的申万二级行业当日中位涨跌幅与该票的相对强度。
④ 位置与结构状态 —— 结合材料给的日K 序列:现在处在什么位置(相对近期高低)、
   什么结构(横盘 / 上升 / 破位 / 反弹)。
⑤ 近期表现 —— 最近这段时间是怎么走的。

另外单独给一句**日K 形态评价**:你看这根图,像什么形态,值得注意的是哪一段。

铁律(不可违反):
1. 你**不知道**这只票是被哪条逻辑选中的,也**不知道**它排第几 —— 材料里没有这些,
   请不要猜测、不要编造"它因为某某原因入选"。
2. 你**不做买卖建议**、不给目标价、不给止损位、不评价"值不值得买"。
   (关键价位由另一个环节负责,不是你。)
3. 只依据材料里给出的数据与证据说话;数据缺失就说缺失。

""" + TIMELINESS_RULES + """

输出格式(硬约束):先写自由叙述(连贯的分析文字,像分析师口头点评,禁止分点列表
与"技术面/资金面/消息面"这类固定分栏模板),然后另起一段,给出一个 ```json 代码块,
里面是:
{"company": "一句话", "industryContext": "一句话", "position": "一句话",
 "recent": "一句话", "klineComment": "一句话"}
五个键都必须有;确实说不出来的写"材料不足"。
"""


@dataclass(frozen=True)
class ExplainNote:
    """一只票的资料聚合结果。"""

    ts_code: str
    profile: Mapping[str, str]     # company / industryContext / position / recent
    kline_comment: str
    narrative: str = ""
    llm_ok: bool = False
    filled_by: str = ""
    reason: str = ""               # `llm_ok=False` 时的原因

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tsCode": self.ts_code, "profile": dict(self.profile),
            "klineComment": self.kline_comment, "narrative": self.narrative,
            "llmOk": self.llm_ok, "filledBy": self.filled_by, "reason": self.reason,
        }


_PROFILE_KEYS = ("company", "industryContext", "position", "recent")
_REQUIRED_KEYS = _PROFILE_KEYS + ("klineComment",)


def _material(item: ExplainInput, news: Optional[NewsVerdict]) -> str:
    """喂给模型的材料。⚠ 只装 `ExplainInput` 里有的东西 + 已经拿到的消息面证据。"""
    lines: List[str] = [date_anchor_line()]
    nm = item.name or ""
    lines.append(f"标的:{nm}({item.ts_code}),申万二级行业:{item.sw_l2_name or '未知'}")
    def _pct(v: Optional[float]) -> str:
        return "未取得" if v is None else f"{v * 100:.2f}%"
    lines.append(
        f"当日:收盘 {item.close if item.close is not None else '未取得'}、"
        f"涨跌幅 {_pct(item.ret_1d)}、振幅 {_pct(item.amp_1d)}、"
        f"换手率 {item.turnover_rate if item.turnover_rate is not None else '未取得'}、"
        f"量比 {item.volume_ratio if item.volume_ratio is not None else '未取得'}")
    lines.append(
        f"行业相对:所属申万二级当日成员涨跌幅中位数 {_pct(item.sw_l2_median_ret)},"
        f"该票相对强度 {_pct(item.rel_strength_1d)}")
    if item.bars:
        lines.append(f"日K(最近 {len(item.bars)} 个交易日,原始未复权,"
                     f"格式 日期/开/高/低/收/量):")
        lines.extend(
            f"  {b.trade_date} {b.open:g}/{b.high:g}/{b.low:g}/{b.close:g}/{b.vol:g}"
            for b in item.bars)
    else:
        lines.append("日K:未取得(事实包缺失)")
    if news is None:
        lines.append("消息面检索证据:本次未检索。")
    elif not news.evidence:
        lines.append(f"消息面检索证据:本次命中 0 条(状态 {news.state.value})——"
                     f"未取得任何搜索结果,不等于该标的无消息。")
    else:
        lines.append(f"消息面检索证据(共 {len(news.evidence)} 条,状态 {news.state.value}):")
        lines.extend(
            f"  · {e.get('title', '')}({e.get('publishDate') or '日期未知'}"
            f"{'|' + e['media'] if e.get('media') else ''})"
            for e in news.evidence)
        if news.narrative:
            lines.append(f"消息面扫描结论摘要:{news.narrative[:500]}")
    return "\n".join(lines)


def _degraded(ts_code: str, reason: str) -> ExplainNote:
    return ExplainNote(
        ts_code=ts_code, profile={k: "资料未取得" for k in _PROFILE_KEYS},
        kline_comment="日K 评价未取得", llm_ok=False, reason=reason)


def aggregate_one(
    item: ExplainInput,
    *,
    provider: Optional[LLMProvider],
    news: Optional[NewsVerdict] = None,
    transport: Optional[Any] = None,
    trade_date: Optional[date] = None,
    report_date: Optional[date] = None,
    pack_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> ExplainNote:
    """聚合一只票的资料。`provider=None` → 直接降级,**零网络调用**。

    🔴 **空成功一律判失败**(§12 坑 13 / LRN-20260816-002):模型返回了、但五个键
    没给全 → `llm_ok=False`,⛔ 不留一份「有结构、没内容」的记录冒充跑通了。
    """
    if provider is None:
        from neckline.llm.usage import record
        record(task="explain", trade_date=trade_date, report_date=report_date, pack_id=pack_id, outcome="skipped",
               failure_reason="未配置可用的 LLM provider", db_path=db_path)
        return _degraded(item.ts_code, "未配置可用的 LLM provider")
    messages = [
        ChatMessage(role="system", content=EXPLAIN_SYSTEM_PROMPT),
        ChatMessage(role="user", content=_material(item, news)),
    ]
    started = time.monotonic()
    try:
        result = provider.chat(messages, enable_search=False, transport=transport)
    except Exception as e:  # noqa: BLE001 —— 一只票炸了只缺它自己那一段
        logger.warning("[explain] %s 资料聚合调用异常", item.ts_code, exc_info=True)
        from neckline.llm.usage import record
        record(task="explain", trade_date=trade_date, report_date=report_date, pack_id=pack_id, outcome="failed",
               duration_ms=int((time.monotonic()-started)*1000), failure_reason="调用异常", db_path=db_path)
        return _degraded(item.ts_code, f"调用异常:{e}")
    from neckline.llm.usage import record
    record(task="explain", result=result, trade_date=trade_date, report_date=report_date, pack_id=pack_id,
           duration_ms=int((time.monotonic()-started)*1000), db_path=db_path)
    if not result.ok:
        return _degraded(item.ts_code, f"调用未成功:{result.reason}")
    narrative, block = split_narrative_and_json(result.content or "")
    if not isinstance(block, dict):
        note = _degraded(item.ts_code, "模型未给出结构化收尾")
        return ExplainNote(ts_code=note.ts_code, profile=note.profile,
                           kline_comment=note.kline_comment, narrative=narrative,
                           llm_ok=False, filled_by=f"{result.provider}/{result.model}",
                           reason=note.reason)
    missing = [k for k in _REQUIRED_KEYS
               if not isinstance(block.get(k), str) or not block[k].strip()]
    if missing:
        # ⛔ 空成功一律判失败(见 docstring)。
        note = _degraded(item.ts_code, f"结构化收尾缺键:{missing}")
        return ExplainNote(ts_code=note.ts_code, profile=note.profile,
                           kline_comment=note.kline_comment, narrative=narrative,
                           llm_ok=False, filled_by=f"{result.provider}/{result.model}",
                           reason=note.reason)
    return ExplainNote(
        ts_code=item.ts_code,
        profile={k: str(block[k]).strip() for k in _PROFILE_KEYS},
        kline_comment=str(block["klineComment"]).strip(),
        narrative=narrative, llm_ok=True,
        filled_by=f"{result.provider}/{result.model}",
    )


def aggregate(
    items: Sequence[ExplainInput],
    *,
    provider: Optional[LLMProvider],
    news_by_code: Optional[Mapping[str, NewsVerdict]] = None,
    transport: Optional[Any] = None,
    trade_date: Optional[date] = None,
    report_date: Optional[date] = None,
    pack_id: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[ExplainNote]:
    """逐只聚合(**按传入顺序**,调用方保证是 `ts_code` 升序)。"""
    news = dict(news_by_code or {})
    return [
        aggregate_one(it, provider=provider, news=news.get(it.ts_code), transport=transport,
                      trade_date=trade_date, report_date=report_date, pack_id=pack_id, db_path=db_path)
        for it in items
    ]


__all__ = [
    "EXPLAIN_SYSTEM_PROMPT", "ExplainNote", "aggregate_one", "aggregate",
]
