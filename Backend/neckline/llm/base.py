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
    # v1.5-④-A3(§七 P1-7):本次调用**实际发起且成功完成**时使用的搜索引擎标识
    # (GLM `web_search.search_engine`,见 `OpenAICompatProvider._search_engine_value`
    # 钩子)。**只在成功路径填充**——`ok=False`(缺 key / 超时 / 非法响应等)时恒
    # `None`,与「老行 NULL=未记录」同一套「不确定就不填」纪律,不臆造"当时用的是
    # 哪个引擎"。无此概念的供应商(如 Kimi 的内置 `$web_search`,协议层没有可选
    # 引擎参数)同样恒 `None`。
    search_engine: Optional[str] = None
    # V2.4.2: provider 返回的真实用量。所有字段均直接来自上游响应；没有就明确
    # 标记为 unavailable，调用方不得按字符数或 prompt 长度猜 token。
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    raw_usage: Dict[str, Any] = field(default_factory=dict)
    usage_unavailable: bool = True


def search_coverage_line(hit_count: int) -> str:
    """一行「本次联网搜索取证覆盖」文案(v1.3.4;守项目铁律「『没有』和『没看』必须分开」)。

    **0 条必须显式说出来**。2026-07-27 生产实测:GLM 对无法识别的 `search_engine`
    取值返回的是 `ok=True` + 顶层 `web_search` 数组 0 条、**不报任何错**;此时模型
    照样写出一段像模像样的分析(退回训练数据),用户从文字上完全分不清「搜过了、
    确实没消息」和「压根一条都没搜到」。生产 `llm_judgments` 20260721/22/23 三天
    10/10 空命中就是这么悄悄发生的,事后才从存档里看出来。这一行是那次事故的防线。

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

        不传(None/空)时行为与历史完全一致——由供应商自己从对话里推导检索词。
        传了才有额外字段进 payload(见 `providers/glm.py::_search_tools`)。

        **为什么需要它**:2026-07-27 生产实测(问询台,已随 V2.1-① 整链退役),GLM
        推导出的检索词紧跟**最后一条 user 消息**;当时问询台的最后一条恰好是用户的
        代词提问(「这只票最近业绩怎么样?」),身份信息在更早的材料消息里救不回来
        → 搜出来的是泛泛的板块新闻,模型只好退回训练数据答。同一条链路只要把股票名
        +代码放进检索词,命中立刻全变成该股的真实新闻。详见
        `llm/prompt_context.py::search_subject_with_recency` 与
        `selection/aggregate.py` 调用点注释(现役联网链路)。"""
        raise NotImplementedError


__all__ = ["ChatMessage", "SearchHit", "LLMResult", "LLMProvider", "search_coverage_line"]
