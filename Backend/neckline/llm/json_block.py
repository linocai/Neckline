"""LLM 输出中“自由叙述 + 机器可读 JSON 块”的唯一解析实现。"""

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


def split_narrative_and_json(narrative: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """拆出干净叙述和最后一个 JSON 对象；解析失败不拖垮叙述。"""
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


__all__ = ["split_narrative_and_json"]
