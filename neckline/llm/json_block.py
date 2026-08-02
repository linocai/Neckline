"""LLM 输出里「自由叙述 + 机器可读 JSON 块」的**唯一解析实现**。

**从 `report/reference_plan.py` 原地搬来(V2-⑤,行为逐字节不变)**,原因两条:
    1. 它是**通用的 LLM 输出解析件**,不是"候选参考件"这个业务的一部分 ——
       V2 的驱动聚合层(`selection/aggregate.py`)要解析同一种形状的输出
       (叙述 + ```json 块),按项目 CLAUDE.md「唯一实现,不许各抄一份」的纪律
       必须共用同一份围栏正则,不能在 `selection/` 里再抄一份。
    2. `report/reference_plan.py` 按 plan §五 V2-⑬-3 将**停用**;把通用件留在
       一个计划停用的模块里,等于给未来的删除动作埋一颗雷。搬到 `llm/` 之后,
       ⑬ 删 `reference_plan.py` 不会带走这份解析器。

**这不是 `llm/` 反向依赖 `report/`**(那条禁令仍然有效):本模块零 import
`neckline.report.*`;是 `report/reference_plan.py` 反过来 import 它并原样再导出
`split_narrative_and_reference_json`(既有调用方与 60 个既有单测因此逐字不动)。
`judge_candidate(narrative_splitter=…)` 的依赖注入体例同样**保持不变** ——
注入的是"这条链路的输出该怎么剥",而剥围栏这一步的实现从今往后只有这一份。

⚠ **v1.5.1 案底(判定线 review 🟡-1,不得忘)**:机器可读标签后面一旦还挂内容,
`_parse_verdict` 的 last-match 锚点就被架空。**凡标签后面还挂 JSON 的调用方,
必须先用本模块剥掉那段 JSON、再去解析结论标签**,顺序不可颠倒。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

_JSON_FENCE_RE = re.compile(r"```json\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
# 残留围栏清理(v1.5.1 判定线 review 🟢-4 的两个化妆缺口):① 围栏**未闭合**(输出被
# 截断)时 `_JSON_FENCE_RE` 匹配不到、裸 JSON 也解析不动,老实现把「```json {"buy": …」
# 半截原样摊给用户;② 多围栏时只删了最后一个,前面的残留在叙述里。两者都不影响解析
# 结果(仍取最后一个**闭合**围栏),只违 §2.7「不把 JSON 摊给用户」的观感。
_JSON_FENCE_UNCLOSED_RE = re.compile(r"```json\b.*\Z", re.DOTALL | re.IGNORECASE)
_JSON_FENCE_MARK_RE = re.compile(r"```json", re.IGNORECASE)


def _strip_residual_json_fences(text: str) -> str:
    """把叙述里**所有** ```json 围栏(闭合的全删 + 末尾未闭合的那一截删到结尾)剥净。
    一个围栏标记都没有时**原样返回**(不做 strip)——degraded 占位文案/无围栏输出必须
    逐字节透传,这条由 `test_no_json_anywhere_returns_none_and_original_text_untouched`
    锁死。"""
    if not _JSON_FENCE_MARK_RE.search(text):
        return text
    return _JSON_FENCE_UNCLOSED_RE.sub("", _JSON_FENCE_RE.sub("", text)).strip()


def _extract_last_json_fence(text: str) -> Optional[Tuple[str, int, int]]:
    matches = list(_JSON_FENCE_RE.finditer(text))
    if not matches:
        return None
    m = matches[-1]
    return m.group(1), m.start(), m.end()


def _extract_bare_trailing_json(text: str) -> Optional[Tuple[Dict[str, Any], int]]:
    """无围栏时容忍"末尾裸 JSON 对象"(①-B)。用 `json.JSONDecoder.raw_decode` 逐个
    候选起点(文本内每一个 `{`)去试解析,取**第一个**能让解析恰好吃到(去除尾部空白
    后的)字符串末尾的起点——这自然就是"跨越到文本结尾的那个最外层对象",不必手写
    括号计数器去猜嵌套边界。"""
    stripped = text.rstrip()
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(stripped):
        if ch != "{":
            continue
        try:
            obj, end = decoder.raw_decode(stripped, idx)
        except json.JSONDecodeError:
            continue
        if end == len(stripped) and isinstance(obj, dict):
            return obj, idx
    return None


def split_narrative_and_reference_json(narrative: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """把模型输出(格式定死:自由叙述 → "结论:"标签 → 空行 → ```json 三件套围栏块)
    拆成 `(干净叙述, 解析出的 dict 或 None)`。取**最后一个**围栏块;无围栏时容忍
    "末尾裸 JSON 对象"。解析失败(围栏在、内容非法 JSON;或两种形式都没找到)→
    `(去除围栏后的叙述或原文, None)`——**绝不能让三件套解析失败拖累叙述本身**
    (项目 CLAUDE.md「解析失败→参考件为 null+理由字段,绝不让解析失败拖垮审判结论」)。
    degraded 占位文案(LLM未激活/调用失败)天然无围栏也无裸 JSON,原样返回、零影响。

    **v1.5.1 起本函数在 `_parse_verdict` 之前跑**(作为
    `judge_candidate(narrative_splitter=...)` 注入进去,判定线 review 🟡-1):入参因此是
    **含"结论:"标签的原始输出**,返回的"干净叙述"里标签仍在、随后由 `_parse_verdict`
    去掉。顺序不可再颠倒——先解析 verdict 会让 JSON 里的自由中文("若跌破证伪线则
    结论:否决"这类)劫持 last-match 锚点、静默翻转结论。本函数只认围栏/裸 JSON 边界,
    多一个标签不影响任何分支。

    **V2-⑤ 起第二个消费方**:`selection/aggregate.py` 的两段式 LLM 编排(检索段的
    证据链 JSON / 推理段的篮子 JSON)。那两条链路**没有结论标签**,只有"叙述 + JSON",
    是本函数分支集合的真子集,行为无需任何改动。函数名里的 `reference` 是历史命名
    (它诞生于参考件三件套),不表示只服务那一条链路 —— 改名要动既有单测与
    `judge_candidate` 注入点,收益不抵风险,原样保留。
    """
    fence = _extract_last_json_fence(narrative)
    if fence is not None:
        raw, _start, _end = fence
        # 🟢-4:清理时把**所有**围栏剥净(不只解析用的那一个),含未闭合的半截。
        cleaned = _strip_residual_json_fences(narrative)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return cleaned, None
        return cleaned, (parsed if isinstance(parsed, dict) else None)

    bare = _extract_bare_trailing_json(narrative)
    if bare is not None:
        obj, idx = bare
        return _strip_residual_json_fences(narrative[:idx].rstrip()), obj

    return _strip_residual_json_fences(narrative), None


__all__ = ["split_narrative_and_reference_json"]
