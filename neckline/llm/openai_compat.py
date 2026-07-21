"""OpenAI 兼容 chat/completions 共享实现(plan §3.4)。GLM(智谱)与 Kimi(Moonshot)
均是 OpenAI 兼容协议,差异只在 endpoint / model / 联网搜索工具声明与结果解析方式
——本类把「短读超时 + 每次全新连接重试 + 降级」(继承 LinoN `deepseek.py` 姿势,
见 `/Users/linotsai/Lino/LinoN/backend/app/llm/deepseek.py`)与「工具调用循环」的
共同逻辑收在一处,子类(`providers/glm.py`/`providers/kimi.py`)只需实现三个钩子。

工具调用循环上限 `max_tool_rounds`:Kimi 的 `$web_search` 内置工具要求"收到
tool_calls → 原样回传 arguments → 再调一次"的协议性回合(官方示例即此模式,
2026-07-20 网页核实,见 `providers/kimi.py` 头注释);GLM 的搜索结果直接在首轮
响应顶层 `web_search` 字段给出,通常不触发该循环。封顶防止死循环。

**诚实声明**:GLM/Kimi 的 endpoint、模型名、联网搜索 tool schema 均于 2026-07-20
按官方文档核实(各 provider 模块头注释附来源链接),但本项目没有真实 key,"真调用
成功"路径未做过活体验证——拿到 key 后应先跑一次真连烟雾测试(手工脚本,非
pytest)确认协议假设仍然成立。无 key / 无 provider 路径(§2.4 铁律)已用 MockTransport
充分覆盖,是当前唯一能验证的路径。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from neckline.llm.base import ChatMessage, LLMProvider, LLMResult, SearchHit

logger = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    api_url: str = ""
    connect_timeout: float = 6.0
    # 带联网搜索的审判/问询单次生成常要 30-60s+(2026-07-21 生产实测:25s 下 10 只
    # 审判 5 只 ReadTimeout)。短读超时+重试是治「连接卡死」的,不能把正常长生成也杀掉,
    # 故放宽到 90s;卡死场景仍由 max_attempts 全新连接重试兜住。
    read_timeout: float = 90.0
    max_attempts: int = 3
    max_tool_rounds: int = 4

    def __init__(self, api_key: Optional[str], model: Optional[str] = None) -> None:
        self.api_key = api_key
        self.model = model or self.default_model

    # —— provider 特有钩子(子类实现)——————————————————————————————
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {(self.api_key or '').strip()}", "Content-Type": "application/json"}

    def _search_tools(self) -> Optional[List[Dict[str, Any]]]:
        raise NotImplementedError

    def _handle_tool_call(self, tool_call: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[SearchHit]]:
        raise NotImplementedError

    def _extract_top_level_search_hits(self, body: Dict[str, Any]) -> List[SearchHit]:
        return []

    # —— 共享逻辑 ——————————————————————————————————————————————
    def chat(
        self,
        messages: List[ChatMessage],
        *,
        enable_search: bool = True,
        transport: Optional[Any] = None,
    ) -> LLMResult:
        if not self.api_key:
            return LLMResult(ok=False, reason="缺少 API key", provider=self.name, model=self.model)
        try:
            import httpx  # noqa: F401  (惰性导入,未装依赖时优雅降级不崩)
        except ImportError:
            return LLMResult(ok=False, reason="httpx 未安装", provider=self.name, model=self.model)

        wire_messages: List[Dict[str, Any]] = [m.to_api() for m in messages]
        tools = self._search_tools() if enable_search else None
        all_hits: List[SearchHit] = []
        raw_responses: List[Dict[str, Any]] = []

        for _round in range(self.max_tool_rounds):
            payload: Dict[str, Any] = {"model": self.model, "messages": wire_messages, "stream": False}
            if tools:
                payload["tools"] = tools
            body, err = self._post(payload, transport)
            if err is not None:
                return LLMResult(ok=False, reason=err, provider=self.name, model=self.model, raw_responses=raw_responses)
            raw_responses.append(body)

            try:
                choice = body["choices"][0]
                msg = choice.get("message") or {}
                finish_reason = choice.get("finish_reason")
            except (KeyError, IndexError, TypeError) as e:
                return LLMResult(
                    ok=False, reason=f"响应结构异常: {e}", provider=self.name, model=self.model,
                    raw_responses=raw_responses,
                )

            all_hits.extend(self._extract_top_level_search_hits(body))

            tool_calls = msg.get("tool_calls")
            if finish_reason == "tool_calls" and tool_calls:
                wire_messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tool_calls})
                for tc in tool_calls:
                    tool_msg, hit = self._handle_tool_call(tc)
                    if hit is not None:
                        all_hits.append(hit)
                    wire_messages.append(tool_msg)
                continue

            content = msg.get("content")
            if not isinstance(content, str) or not content.strip():
                return LLMResult(
                    ok=False, reason="模型输出为空", provider=self.name, model=self.model,
                    raw_responses=raw_responses,
                )
            return LLMResult(
                ok=True, content=content, search_hits=all_hits, provider=self.name, model=self.model,
                raw_responses=raw_responses,
            )

        return LLMResult(
            ok=False, reason=f"工具调用轮数超过上限({self.max_tool_rounds})",
            provider=self.name, model=self.model, raw_responses=raw_responses,
        )

    def _post(self, payload: Dict[str, Any], transport: Optional[Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """一次 HTTP 往返(短读超时 + 每次全新连接重试,继承 LinoN deepseek.py 姿势)。
        返回 `(body, None)` 成功,或 `(None, 降级原因)`。"""
        import httpx

        timeout = httpx.Timeout(self.read_timeout, connect=self.connect_timeout)
        resp = None
        last_exc: Optional[BaseException] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                client_kwargs: Dict[str, Any] = {"timeout": timeout}
                if transport is not None:
                    client_kwargs["transport"] = transport
                with httpx.Client(**client_kwargs) as client:
                    resp = client.post(self.api_url, json=payload, headers=self._headers())
                break
            except Exception as e:  # noqa: BLE001  超时/网络/连接异常 → 换新连接重试
                last_exc = e
                logger.warning("%s 调用第 %d/%d 次异常(将重试): %s", self.name, attempt, self.max_attempts, e)
        if resp is None:
            reason = f"调用异常 {type(last_exc).__name__}" if last_exc is not None else "调用异常"
            return None, reason

        if resp.status_code != 200:
            return None, f"上游 {resp.status_code}"

        try:
            return resp.json(), None
        except Exception as e:  # noqa: BLE001
            return None, f"响应解析异常: {e}"


__all__ = ["OpenAICompatProvider"]
