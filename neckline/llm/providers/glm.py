"""智谱 GLM(BigModel 开放平台)。

endpoint / model 名 / `web_search` 工具 schema 均于 2026-07-20 网络核实官方文档:
    · https://docs.bigmodel.cn/cn/guide/tools/web-search
      (web_search 工具参数示例;响应顶层 `web_search` 数组结构 + 完整响应示例,
      示例响应里的 `model` 字段值就是 "glm-5.2")
    · https://docs.bigmodel.cn/api-reference/模型-api/对话补全 (对话补全 endpoint)
    · https://docs.bigmodel.cn/cn/guide/start/quick-start (OpenAI 兼容 base_url)

GLM 的联网搜索是"一轮出结果"模式:响应顶层 `web_search` 数组直接带命中网页列表
(title/link/content/media/publish_date/refer),不像 Kimi 需要工具调用回合——
本实现的 `_handle_tool_call` 只做防御性占位(正常情况不应被触发)。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from neckline.llm.base import SearchHit
from neckline.llm.openai_compat import OpenAICompatProvider


class GLMProvider(OpenAICompatProvider):
    name = "glm"
    default_model = "glm-5.2"
    api_url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

    def _search_tools(self) -> Optional[List[Dict[str, Any]]]:
        return [
            {
                "type": "web_search",
                "web_search": {
                    "enable": "True",
                    "search_engine": "search_pro",
                    "search_result": "True",
                    "count": "5",
                },
            }
        ]

    def _extract_top_level_search_hits(self, body: Dict[str, Any]) -> List[SearchHit]:
        hits: List[SearchHit] = []
        for item in body.get("web_search") or []:
            if not isinstance(item, dict):
                continue
            hits.append(
                SearchHit(
                    title=str(item.get("title", "")),
                    link=str(item.get("link", "")),
                    content=str(item.get("content", "")),
                    media=str(item.get("media", "")),
                    publish_date=str(item.get("publish_date", "")),
                    raw=item,
                )
            )
        return hits

    def _handle_tool_call(self, tool_call: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[SearchHit]]:
        # GLM 的搜索走顶层 web_search 字段,理论不会走到需要客户端处理的 tool_call
        # 分支;若模型仍返回未声明过的 tool_call,防御性占位回复,避免死循环。
        return {"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": "{}"}, None


__all__ = ["GLMProvider"]
