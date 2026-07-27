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

    # 检索词长度上限(防御性截断,非官方文档明确数字)。截断只影响检索词,不影响
    # 提问本身——问题全文照样在 messages 里。
    max_search_query_chars = 78

    def _search_tools(self, search_query: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """**`enable`/`search_result` 发字符串 `"True"`、`count` 发字符串 `"5"` 是刻意保留的,
        不是笔误——2026-07-27 用真 key 做过 A/B 实证:GLM 会把字符串正确解析成 bool/int**
        (判别式:`enable="False"` 字符串同样能把搜索**关掉**,说明不是"非空字符串一律
        当 true"的糙转换)。改成布尔/整数对线上行为零差异,故不动——留着这段注释,
        免得后人再把它当 bug 修一遍。真正让搜索失效的是别的东西,见下。

        `search_query`(v1.3.4):不传时**返回与 v1.3.3 逐字节相同的 payload**(护栏单测
        `TestSearchQueryOptIn` 锁死);传了才多一个 `search_query` 字段,显式指定检索词。

        ⚠ **`search_engine` 取值传错会静默返 0 条**(`ok=True`、无任何报错,2026-07-27
        实测 `__bogus_engine__` 即如此)。当日同一问题实测:`search_pro` 2 条 /
        `search_std` 2 条 / `search_pro_sogou` 10 条。取值维持 `search_pro` 是用户
        2026-07-27 的决定(一天样本不足以定,先靠 `llm.base.search_coverage_line`
        + `openai_compat` 的 0 命中告警攒几天数据再拿数据说话)。
        """
        web_search: Dict[str, Any] = {
            "enable": "True",
            "search_engine": "search_pro",
            "search_result": "True",
            "count": "5",
        }
        if search_query and str(search_query).strip():
            web_search["search_query"] = str(search_query).strip()[: self.max_search_query_chars]
        return [{"type": "web_search", "web_search": web_search}]

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
