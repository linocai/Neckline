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

**V2-②(plan §3.10-B)起本类降级为"预置参考实现"**:`neckline.llm.factory.
get_provider()` 不再 import 本类,自填制下的通用 provider 一律走
`OpenAICompatProvider` 的通用默认实现(该实现协议沿用 GLM 的"服务端一轮出结果"
形状,与本类的工具调用回合协议不同——两者**不兼容**,这是本类协议本身无法被
"自填任意端点"泛化吸收的地方,如实登记不当 bug)。本类保留纯粹是为了让既有
单测(`test_llm.py` 等)继续有一个"内置工具调用回合"协议的具体测试替身,行为
不受本次改动影响。
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

    def _search_tools(self, search_query: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """`search_query` 在 Kimi 侧**只能忽略,这是协议决定的、不是漏实现**:`$web_search`
        是内置函数,检索词由模型自己在 `tool_call.function.arguments` 里给出(客户端只负责
        原样回传),声明处没有可注入查询词的参数位。故本方法返回值与是否传 `search_query`
        无关——恒等于 v1.3.3 的 payload。"""
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
