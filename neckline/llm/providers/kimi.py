"""Moonshot Kimi。

endpoint / model 名 / `$web_search` 内置工具协议均于 2026-07-20 网络核实官方文档:
    · https://platform.moonshot.cn/docs/guide/use-web-search
      (base_url="https://api.moonshot.cn/v1"、`kimi-k3` 是官方示例里"推荐用于联网
      搜索"的模型名、`$web_search` 工具声明 + "原样回传 arguments"协议的完整
      Python 代码示例)
    · https://platform.moonshot.cn/docs/guide/use-kimi-api-to-complete-tool-calls

Kimi 的联网搜索走标准 OpenAI 工具调用协议,但 `$web_search` 是【内置函数】——
服务端已经执行了搜索,客户端收到 `finish_reason=="tool_calls"` 后只需把
`tool_call.function.arguments` 原样当作 tool 消息的 content 回传(不需要自己
实现搜索),再调一次 chat completions 即可拿到基于搜索结果的最终回答。本实现
把该 arguments 原样存进 `SearchHit.raw`(§2.4「搜索结果全文落 SQLite 存档」)。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from neckline.llm.base import SearchHit
from neckline.llm.openai_compat import OpenAICompatProvider

_WEB_SEARCH_TOOL_NAME = "$web_search"


class KimiProvider(OpenAICompatProvider):
    name = "kimi"
    default_model = "kimi-k3"
    api_url = "https://api.moonshot.cn/v1/chat/completions"

    def _search_tools(self) -> Optional[List[Dict[str, Any]]]:
        return [{"type": "builtin_function", "function": {"name": _WEB_SEARCH_TOOL_NAME}}]

    def _handle_tool_call(self, tool_call: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[SearchHit]]:
        fn = tool_call.get("function") or {}
        name = fn.get("name")
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except (json.JSONDecodeError, TypeError):
            args = {}

        if name == _WEB_SEARCH_TOOL_NAME:
            hit = SearchHit(raw=args if isinstance(args, dict) else {"raw": args})
            # 协议要求原样回传 arguments(Kimi 服务端已执行搜索,回声只为满足 tool 协议)
            content = args_raw if isinstance(args_raw, str) else json.dumps(args, ensure_ascii=False)
            return {"role": "tool", "tool_call_id": tool_call.get("id", ""), "name": name, "content": content}, hit

        # 未声明过的工具(不该发生),防御性占位,避免死循环
        return {"role": "tool", "tool_call_id": tool_call.get("id", ""), "name": name or "unknown", "content": "{}"}, None


__all__ = ["KimiProvider"]
