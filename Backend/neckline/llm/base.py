"""LLM 供应商统一接口和中立数据形状。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None  # role="assistant" 且请求工具调用时的原始结构
    tool_call_id: Optional[str] = None  # role="tool" 时必填,对应 tool_call.id
    name: Optional[str] = None  # role="tool" 时的函数名

    def to_api(self) -> Dict[str, Any]:
        """转 OpenAI 兼容 wire 格式并过滤空字段。"""
        out: Dict[str, Any] = {"role": self.role}
        if self.content is not None:
            out["content"] = self.content
        if self.tool_calls is not None:
            out["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            out["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            out["name"] = self.name
        return out


@dataclass
class SearchHit:
    """一条联网搜索命中；无法归一化的供应商字段原样放入 ``raw``。"""

    title: str = ""
    link: str = ""
    content: str = ""
    media: str = ""
    publish_date: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResult:
    ok: bool
    content: str = ""
    search_hits: List[SearchHit] = field(default_factory=list)
    reason: str = "ok"
    provider: str = ""
    model: str = ""
    raw_responses: List[Dict[str, Any]] = field(default_factory=list)
    # 仅记录供应商明确返回且调用成功时的搜索引擎标识；不确定就留空。
    search_engine: Optional[str] = None
    # V2.4.2: provider 返回的真实用量。所有字段均直接来自上游响应；没有就明确
    # 标记为 unavailable，调用方不得按字符数或 prompt 长度猜 token。
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    raw_usage: Dict[str, Any] = field(default_factory=dict)
    usage_unavailable: bool = True
    # Tavily 是独立账单；成功检索后即使后续推理失败也必须把真实 credits 带回调用点。
    tavily_credits: Optional[int] = None


def search_coverage_line(hit_count: int) -> str:
    """一行「本次联网搜索取证覆盖」文案(v1.3.4;守项目铁律「『没有』和『没看』必须分开」)。

    **0 条必须显式说出来**，避免把「没有取得搜索结果」误写成「确认没有消息」。

    收**条数**而不是命中列表:自选体检只留了条数(全文不单独存档),候选审判/问询台
    有全文,两边都得能用同一句文案,不给调用方留"要不要造个假列表"的余地。"""
    n = int(hit_count or 0)
    if n == 0:
        return "联网搜索:本次命中 0 条(未取得任何搜索结果,以上判断不含消息面核实——不等于该标的无消息)"
    return f"联网搜索:本次命中 {n} 条"


class LLMProvider(ABC):
    name: str = "base"
    default_model: str = ""

    @abstractmethod
    def chat(
        self,
        messages: List[ChatMessage],
        *,
        enable_search: bool = True,
        search_query: Optional[str] = None,
        transport: Optional[Any] = None,
    ) -> LLMResult:
        """`search_query`(v1.3.4 新增,可选):**显式指定联网搜索的检索词**。

        不传时由供应商从对话中推导；传入时必须包含明确主体，避免代词导致跑题。"""
        raise NotImplementedError


__all__ = ["ChatMessage", "SearchHit", "LLMResult", "LLMProvider", "search_coverage_line"]
