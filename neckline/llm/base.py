"""LLM 供应商统一接口(plan 2.4/§3.4)。`ChatMessage`/`LLMResult`/`SearchHit` 是
GLM/Kimi 两家 provider 共用的中立数据形状,`LLMProvider` 是可插拔的抽象基类。"""

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
        """转 OpenAI 兼容 wire 格式,过滤 None 字段(GLM/Kimi 均走此协议,§3.4)。"""
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
    """一条联网搜索命中(§2.4「搜索结果全文落 SQLite 存档」的存档单元)。字段尽量
    填,不同供应商能给到的字段不同——GLM 顶层 `web_search` 数组信息完整(title/
    link/content/media/publish_date);Kimi 的 `$web_search` 内置工具协议只回传
    tool_call 的 arguments(结构未公开文档化),原样存进 `raw`,不强行套字段。"""

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


class LLMProvider(ABC):
    name: str = "base"
    default_model: str = ""

    @abstractmethod
    def chat(
        self,
        messages: List[ChatMessage],
        *,
        enable_search: bool = True,
        transport: Optional[Any] = None,
    ) -> LLMResult:
        raise NotImplementedError


__all__ = ["ChatMessage", "SearchHit", "LLMResult", "LLMProvider"]
