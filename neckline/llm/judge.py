"""LLM 逻辑审判(plan 2.4/§2.3)。前 10 只候选过 LLM 审判(读概念板块 + 龙虎榜 +
板块年龄 + 价量结构 + 联网搜索,判催化持续性,一票否决权);后 10 只不耗 LLM——
该拆分在 `neckline/report/pipeline.py` 里做,本模块只负责"审一只"。

铁律:
    · **信息边界**——prompt 显式声明只能依据给定的结构化数据与联网搜索工具实际
      返回的内容做判断,搜索无结果必须明说,禁止编造未搜到的消息。
    · **输出自由对话体**(§2.7),禁模板卡/枚举腔——叙述部分是一段连贯自然语言,
      只在结尾加一行机器可读的结论标签。这不违反 §2.7:类比 LinoN `deepseek.chat()`
      的 `{reply, verdict}` 先例(`/Users/linotsai/Lino/LinoN/backend/app/llm/
      deepseek.py`),§3.4 也明确「问询台工具调用沿用 LinoN v1.2.1 /chat 的姿势」
      ——§2.7 禁的是内容本身写成分栏卡片(如 LinoN 旧版"技术面/资金面/消息面"三轴),
      不是禁止一个轻量的机器可读收尾标记。
    · **无 key / 无 provider / 调用失败** → 输出「LLM 未激活」占位,`degraded=True`,
      不假装分析过、不构成否决也不构成通过(§2.4:全链路必须在无 key 下优雅降级
      跑通)。
    · **保守默认**:模型输出合法但未按格式给出结论标签 → 按「否决」处理(§2.7
      "纪律不过,不放行"的精神——宁可错杀候选,不可在解析歧义时放行)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from neckline.llm.base import ChatMessage, LLMProvider, SearchHit

VERDICT_PASS = "通过"
VERDICT_VETO = "否决"
VERDICT_INACTIVE = "未激活"  # LLM 未激活/调用失败的占位态,不等价于「否决」

JUDGE_SYSTEM_PROMPT = """你是「颈线」系统的盘后候选逻辑审判员。系统本身只做审计、不代客下单,
你的判断只影响"这只候选是否留在今晚的候选池里",不构成买入建议,读者是一位短线交易者。

你会拿到一只候选股票的结构化数据:价量结构特征与形态标签、所属概念板块与是否处于当前热门
板块、近日是否上过龙虎榜及龙虎榜净买卖情况、系统给出的买点与止损计划。你还配有联网搜索工具,
可以查该股票近期的新闻、公告、题材催化。

信息边界(铁律,不可违反):
1. 你只能依据下面提供的结构化数据、以及联网搜索工具实际返回的内容做判断。
2. 如果搜索没有找到相关消息,或搜到的内容与该股票无关,必须在分析里明确说"未搜到相关消息",
绝不允许凭猜测编造新闻、公告、传闻或题材。
3. 系统的选股规则本身是一套减损纪律系统而非高胜率信号(2-5 日短线,日线频率下 A 股呈均值
回归),你的角色是排查"催化是否站得住、是否有明显利空正在发生",不是给出收益预测,不要暗示
"这只票会涨"。

输出风格(硬约束):自由叙述,写成一段连贯的分析文字,像分析师口头点评。禁止使用分点列表、
多维打分表、"技术面/资金面/消息面"这类固定分栏模板——可以自然地把这些角度揉进叙述里,但不要
用标题或项目符号分隔。

结尾格式(唯一的机器可读部分):写完叙述后,另起一行,只写"结论:通过"或"结论:否决"这两者
之一(不要多余的标点或解释,正文里不要提前出现"结论:"这个词组以免解析冲突)。"否决"意味着
你认为催化站不住、有明显利空、或消息面有硬伤,建议今晚从候选池剔除;"通过"意味着没有发现
应当剔除的理由(不代表看好,只代表没找到否决的理由)。
"""

# v1.1-C.3 自选体检专用 system prompt(`judge_candidate` 新增可选 `system_prompt`
# 参数,默认仍是上面的 `JUDGE_SYSTEM_PROMPT`,候选审判调用点零改动)。自选池是
# 用户自己主理的池子,不是"今晚候选池"——沿用候选审判"是否留在候选池"的框定语
# 并不贴合,故单独写一版语义对应"这只自选票现在的状态是否值得继续关注"的
# system prompt;结尾机器可读标签格式与 `_parse_verdict` 的正则完全一致(仍是
# "结论:通过|否决"),两套 prompt 共用同一套解析/降级/JudgeResult 逻辑。
WATCHLIST_JUDGE_SYSTEM_PROMPT = """你是「颈线」系统的自选股体检审判员。系统本身只做审计、不代客下单,
读者是一位短线交易者,这只股票是他自己选进「自选池」长期盯着的(不是系统今晚推荐的候选),你的
判断只影响"这只自选票当前状态是否值得留意/继续关注",不构成买入建议。

你会拿到这只自选票的结构化数据:价量结构特征与形态标签、所属概念板块与是否处于当前热门板块、
系统按现役规则算出的纪律核对结果、以及(若今日已同时触发母战法买点)系统给出的买点与止损计划。
你还配有联网搜索工具,可以查该股票近期的新闻、公告、题材催化。

信息边界(铁律,不可违反):
1. 你只能依据下面提供的结构化数据、以及联网搜索工具实际返回的内容做判断。
2. 如果搜索没有找到相关消息,或搜到的内容与该股票无关,必须在分析里明确说"未搜到相关消息",
绝不允许凭猜测编造新闻、公告、传闻或题材。
3. 系统的选股规则本身是一套减损纪律系统而非高胜率信号(2-5 日短线,日线频率下 A 股呈均值
回归),你的角色是排查"催化是否站得住、是否有明显利空正在发生",不是给出收益预测,不要暗示
"这只票会涨"。

输出风格(硬约束):自由叙述,写成一段连贯的分析文字,像分析师口头点评。禁止使用分点列表、
多维打分表、"技术面/资金面/消息面"这类固定分栏模板——可以自然地把这些角度揉进叙述里,但不要
用标题或项目符号分隔。

结尾格式(唯一的机器可读部分):写完叙述后,另起一行,只写"结论:通过"或"结论:否决"这两者
之一(不要多余的标点或解释,正文里不要提前出现"结论:"这个词组以免解析冲突)。"否决"意味着
你认为这只自选票近期有明显利空、或此前关注的催化已经站不住,建议用户重新评估是否继续关注;
"通过"意味着没有发现应当立即警惕的理由(不代表看好,只代表没找到应当否决的理由)。
"""


@dataclass
class JudgeResult:
    ts_code: str
    provider: str
    model: str
    verdict: str  # "通过" | "否决" | "未激活"
    narrative: str
    degraded: bool
    degrade_reason: str = ""
    search_hits: List[SearchHit] = field(default_factory=list)


def build_context_block(candidate: Any, top_list_row: Optional[Dict[str, Any]] = None) -> str:
    """组装喂给 LLM 的结构化上下文(纯文本块,不是 JSON——降低模型把它误当输出
    模板抄一份回来的概率)。`candidate` 是 `neckline.report.candidates.Candidate`,
    用 duck typing 而非强类型 import,避免循环依赖。"""
    lines = [
        f"股票:{getattr(candidate, 'name', '')}({candidate.ts_code})",
        f"现价:{candidate.close:.2f} 元;交易所板块:{candidate.board}",
        f"价量结构标签:{'、'.join(candidate.pattern_tags) if candidate.pattern_tags else '无'}",
        f"所属概念板块:{'、'.join(candidate.sector_names) if candidate.sector_names else '无同花顺概念归类'}",
    ]
    if candidate.hot_sectors:
        lines.append(f"当前处于报告热门板块:{'、'.join(candidate.hot_sectors)}")
    if top_list_row:
        net_amount = top_list_row.get("net_amount")
        net_rate = top_list_row.get("net_rate")
        reason = top_list_row.get("reason", "未知")
        net_amount_txt = f"{net_amount:.1f}" if isinstance(net_amount, (int, float)) else "未知"
        net_rate_txt = f"{net_rate:.1f}" if isinstance(net_rate, (int, float)) else "未知"
        lines.append(
            f"近日上榜龙虎榜:是。龙虎榜净买入 {net_amount_txt} 万元(占比 {net_rate_txt}%),上榜原因:{reason}"
        )
    else:
        lines.append("近日上榜龙虎榜:否(或数据未覆盖)。")
    lines.append(f"系统给出的买点计划:{candidate.entry_plan}")
    lines.append(f"系统给出的止损:{candidate.stop_loss}")
    lines.append("请结合以上信息与联网搜索,判断该股票近期是否有站得住的催化,或是否存在你判断应当剔除的理由。")
    return "\n".join(lines)


_VERDICT_RE = re.compile(r"结论[:：]\s*(通过|否决)")


def _parse_verdict(content: str) -> tuple:
    matches = list(_VERDICT_RE.finditer(content))
    if not matches:
        return VERDICT_VETO, content.strip() + "\n\n[系统提示:模型未按格式给出结论标签,保守按「否决」处理。]"
    last = matches[-1]
    narrative = (content[: last.start()] + content[last.end():]).strip()
    return last.group(1), (narrative or content.strip())


def judge_candidate(
    candidate: Any,
    *,
    provider: Optional[LLMProvider],
    top_list_row: Optional[Dict[str, Any]] = None,
    transport: Optional[Any] = None,
    system_prompt: str = JUDGE_SYSTEM_PROMPT,
) -> JudgeResult:
    """审一只候选(或复用于自选体检,见 `system_prompt`)。`provider=None`(工厂在
    无 key/无 provider 时返回 None)→ 直接走「未激活」占位,不发起任何网络调用。

    `system_prompt`(v1.1-C.3 新增,默认值不变,**候选审判调用点零改动、纯向后
    兼容扩展**):自选体检(`report.watchlist_check.apply_llm_review`)语义上不是
    "审判是否留在候选池",框定语不同,故传入
    `WATCHLIST_JUDGE_SYSTEM_PROMPT`——两套 prompt 结尾都用同一个"结论:通过|否决"
    机器可读标签,`_parse_verdict`/降级链/`JudgeResult` 结构完全共用,不重写任何
    解析或降级逻辑。"""
    if provider is None:
        return JudgeResult(
            ts_code=candidate.ts_code, provider="none", model="", verdict=VERDICT_INACTIVE,
            narrative="LLM 未激活(.env 未配置 LLM_PROVIDER/LLM_API_KEY),本候选未经过 LLM 审判,仅供参考,不构成否决或通过。",
            degraded=True, degrade_reason="未配置 LLM_PROVIDER/LLM_API_KEY",
        )

    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=build_context_block(candidate, top_list_row)),
    ]
    result = provider.chat(messages, enable_search=True, transport=transport)
    if not result.ok:
        return JudgeResult(
            ts_code=candidate.ts_code, provider=provider.name, model=getattr(provider, "model", ""),
            verdict=VERDICT_INACTIVE,
            narrative=f"LLM 调用未成功({result.reason}),本候选未经过 LLM 审判,仅供参考,不构成否决或通过。",
            degraded=True, degrade_reason=result.reason,
        )

    verdict, narrative = _parse_verdict(result.content)
    return JudgeResult(
        ts_code=candidate.ts_code, provider=result.provider, model=result.model,
        verdict=verdict, narrative=narrative, degraded=False, search_hits=result.search_hits,
    )


__all__ = [
    "JudgeResult",
    "JUDGE_SYSTEM_PROMPT",
    "WATCHLIST_JUDGE_SYSTEM_PROMPT",
    "VERDICT_PASS",
    "VERDICT_VETO",
    "VERDICT_INACTIVE",
    "build_context_block",
    "judge_candidate",
]
