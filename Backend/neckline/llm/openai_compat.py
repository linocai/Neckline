"""OpenAI 兼容 ``chat/completions`` 实现。

本类负责请求、重试、显式降级、工具回合和可选 SSE 拼装。生产工厂按数据库配置
直接实例化本类；需要联网的现役任务统一由 Tavily 在外层提供证据。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from neckline.llm.base import ChatMessage, LLMProvider, LLMResult, SearchHit

logger = logging.getLogger(__name__)


class _RetryableUpstreamStatus(RuntimeError):
    """A provider throttle response that may succeed on the existing retry path."""

    def __init__(self, status_code: int, retry_after: Optional[str] = None) -> None:
        super().__init__(f"上游 {status_code}")
        self.status_code = status_code
        try:
            parsed = float(retry_after) if retry_after is not None else 2.0
        except (TypeError, ValueError):
            parsed = 2.0
        self.retry_after_seconds = max(0.0, parsed)


_RETRYABLE_UPSTREAM_CODES = {"1302", "1305"}


def _upstream_business_code(response: Any) -> Optional[str]:
    """Return the provider business code without retaining the error body."""
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - a non-JSON error remains a normal status failure
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return str(code) if code is not None else None


def _actual_usage(raw_responses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize only provider-reported token usage.

    A tool loop is several billable upstream requests, so an auditable result is
    available only when *every* response reports a complete usage object.  This
    deliberately does not fall back to character counts or token estimation.
    """
    usages: List[Dict[str, Any]] = []
    for response in raw_responses:
        raw = response.get("usage") if isinstance(response, dict) else None
        if not isinstance(raw, dict):
            # Keep an explicit empty slot for this upstream response.  Audit
            # consumers can distinguish "no usage object" from an omitted
            # archived response even when a preceding tool round had usage.
            usages.append({})
            return {"raw_usage": {"responses": usages}, "usage_unavailable": True}
        usages.append(dict(raw))

    if not usages:
        return {"raw_usage": {"responses": []}, "usage_unavailable": True}

    def integer(value: Any) -> Optional[int]:
        if isinstance(value, bool):
            return None
        if isinstance(value, int) and value >= 0:
            return value
        return None

    prompt_values = [integer(item.get("prompt_tokens")) for item in usages]
    completion_values = [integer(item.get("completion_tokens")) for item in usages]
    total_values = [integer(item.get("total_tokens")) for item in usages]
    if any(value is None for value in prompt_values + completion_values):
        return {"raw_usage": {"responses": usages}, "usage_unavailable": True}
    # A provider may omit total_tokens while still reporting the two actual
    # components.  Adding those components is accounting, not estimation.
    total = sum(total_values) if all(value is not None for value in total_values) else (
        sum(prompt_values) + sum(completion_values)
    )
    return {
        "prompt_tokens": sum(prompt_values),
        "completion_tokens": sum(completion_values),
        "total_tokens": total,
        "raw_usage": {"responses": usages},
        "usage_unavailable": False,
    }


class OpenAICompatProvider(LLMProvider):
    api_url: str = ""
    connect_timeout: float = 6.0
    # 带联网搜索的审判/问询单次生成常要 30-60s+(2026-07-21 生产实测:25s 下 10 只
    # 审判 5 只 ReadTimeout)。短读超时+重试是治「连接卡死」的,不能把正常长生成也杀掉,
    # 故放宽到 90s;卡死场景仍由 max_attempts 全新连接重试兜住。
    read_timeout: float = 90.0
    max_attempts: int = 3
    max_tool_rounds: int = 4
    # 裸实例默认不带搜索能力；生产工厂始终关闭它并改由 Tavily 联网。
    has_web_search: bool = False
    search_engine: Optional[str] = None
    # §七 P0-44:是否走 SSE 流式(`stream: true`)。**默认 False = 既有行为逐字节
    # 不变**;唯一打开它的地方是 `factory.get_provider(task)`,判据唯一实现在
    # 工厂当前全部关闭流式；保留此开关只服务底层协议能力与单测。
    use_streaming: bool = False
    # 通用搜索协议的防御性检索词上限；不影响 messages 中的完整问题。
    max_search_query_chars = 78

    def __init__(
        self,
        api_key: Optional[str],
        model: Optional[str] = None,
        *,
        name: Optional[str] = None,
        api_url: Optional[str] = None,
        has_web_search: Optional[bool] = None,
        search_engine: Optional[str] = None,
        read_timeout: Optional[float] = None,
        use_streaming: Optional[bool] = None,
    ) -> None:
        """`name`/`api_url`/`has_web_search`/`search_engine`/`read_timeout`/
        `use_streaming` 均为可选覆盖；生产工厂从数据库 provider 行传入。

        `read_timeout`(§七 P0-40 定,P0-44 起语义**按是否流式分两种**,⚠ 别把
        两种读串了):
          · **非流式**(默认):它是「整段响应必须在这么久之内回完」的墙钟上限 ——
            90.0 有实测背书(v1.3.4:25s 下 10 只审判 5 只 ReadTimeout)。
          · **流式**(`use_streaming=True`):httpx 的 read 超时天然作用在**每次
            socket 读**上,于是它变成「**chunk 与 chunk 之间**最多能静默这么久」
            —— 吐字只要不断,整段生成多长都合法。
        两种语义下唯一来源都是 `llm/router.py::read_timeout_for_task()`
        (**⛔ 别在别处再写一份数字**);`None` 时保持类属性 90.0。

        `use_streaming`(§七 P0-44):见类属性注释。"""
        self.api_key = api_key
        self.model = model or self.default_model
        if name is not None:
            self.name = name
        if api_url is not None:
            self.api_url = api_url
        if has_web_search is not None:
            self.has_web_search = bool(has_web_search)
        if search_engine is not None:
            self.search_engine = search_engine
        if read_timeout is not None:
            self.read_timeout = float(read_timeout)
        if use_streaming is not None:
            self.use_streaming = bool(use_streaming)

    # —— 通用协议扩展钩子 ———————————————————————————————————————
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {(self.api_key or '').strip()}", "Content-Type": "application/json"}

    def _search_tools(self, search_query: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """可选的通用 ``web_search`` 工具形状；生产链路不启用此能力。"""
        if not self.has_web_search:
            return None
        web_search: Dict[str, Any] = {
            "enable": "True",
            "search_engine": self.search_engine,
            "search_result": "True",
            "count": "5",
        }
        if search_query and str(search_query).strip():
            web_search["search_query"] = str(search_query).strip()[: self.max_search_query_chars]
        return [{"type": "web_search", "web_search": web_search}]

    def _handle_tool_call(self, tool_call: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[SearchHit]]:
        """服务端搜索协议意外返回工具回合时给出防御性占位回复。"""
        return {"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": "{}"}, None

    def _extract_top_level_search_hits(self, body: Dict[str, Any]) -> List[SearchHit]:
        """解析通用协议顶层的 ``web_search`` 数组。"""
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

    def _search_engine_value(self) -> Optional[str]:
        """本次调用实际使用的搜索引擎标识(v1.5-④-A3,§七 P1-7),供 `chat()` 成功
        路径塞进 `LLMResult.search_engine`。通用默认实现:`self.has_web_search`
        为假时恒 `None`(没有引擎可言,不冒充"用了某个引擎");为真时读
        `self.search_engine`(构造时由 `llm_providers.search_engine` 列喂入)。
        该值与 ``_search_tools`` 使用同一配置源。"""
        return self.search_engine if self.has_web_search else None

    # —— 共享逻辑 ——————————————————————————————————————————————
    def chat(
        self,
        messages: List[ChatMessage],
        *,
        enable_search: bool = True,
        search_query: Optional[str] = None,
        transport: Optional[Any] = None,
    ) -> LLMResult:
        if not self.api_key:
            return LLMResult(ok=False, reason="缺少 API key", provider=self.name, model=self.model)
        try:
            import httpx  # noqa: F401  (惰性导入,未装依赖时优雅降级不崩)
        except ImportError:
            return LLMResult(ok=False, reason="httpx 未安装", provider=self.name, model=self.model)

        wire_messages: List[Dict[str, Any]] = [m.to_api() for m in messages]
        tools = self._search_tools(search_query) if enable_search else None
        all_hits: List[SearchHit] = []
        raw_responses: List[Dict[str, Any]] = []

        for _round in range(self.max_tool_rounds):
            # `use_streaming=False`(默认)时这里恒 `False`,payload 与 P0-44 之前
            # 逐字节相同 —— 检索类/所有直接 new 出来的 provider 的 wire 格式未变。
            payload: Dict[str, Any] = {
                "model": self.model, "messages": wire_messages, "stream": bool(self.use_streaming),
            }
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
            if enable_search and not all_hits:
                # 开了搜索却一条都没回来属于静默失效，必须留痕。
                logger.warning(
                    "%s 本次调用开启了联网搜索但命中 0 条(模型可能退回训练数据作答;"
                    "检索词=%s)——若持续出现,先查 search_engine 取值是否仍被上游认识",
                    self.name, (search_query or "<由供应商自行推导>"),
                )
            return LLMResult(
                ok=True, content=content, search_hits=all_hits, provider=self.name, model=self.model,
                raw_responses=raw_responses,
                # 未开搜索时恒 None(没有引擎可言,不冒充"用了某个引擎");开了搜索
                # 才读 `_search_engine_value()`(P1-7 基线捞的就是这个值)。
                search_engine=(self._search_engine_value() if enable_search else None),
                **_actual_usage(raw_responses),
            )

        return LLMResult(
            ok=False, reason=f"工具调用轮数超过上限({self.max_tool_rounds})",
            provider=self.name, model=self.model, raw_responses=raw_responses,
        )

    def _post(self, payload: Dict[str, Any], transport: Optional[Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """一次 HTTP 往返(短读超时 + 每次全新连接重试,继承 LinoN deepseek.py 姿势)。
        返回 `(body, None)` 成功,或 `(None, 降级原因)`。

        网络层异常(超时 / 连接断)与明确的 1302/1305 限流业务码走现有重试次数；
        限流优先尊重上游 `Retry-After`，缺失时短暂退避。余额不足 1113 等其他
        429、其余非 200 与响应解析异常仍是已经拿到的明确答复，当场降级、不重放。

        `payload["stream"]` 决定走哪条:流式那条的读超时语义是 **chunk 间隔**,
        见 `__init__` 的 `read_timeout` 文档。"""
        import httpx

        timeout = httpx.Timeout(self.read_timeout, connect=self.connect_timeout)
        streaming = bool(payload.get("stream"))
        outcome: Optional[Tuple[Optional[Dict[str, Any]], Optional[str]]] = None
        last_exc: Optional[BaseException] = None
        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            try:
                client_kwargs: Dict[str, Any] = {"timeout": timeout}
                if transport is not None:
                    client_kwargs["transport"] = transport
                with httpx.Client(**client_kwargs) as client:
                    outcome = (self._attempt_stream(client, payload) if streaming
                               else self._attempt_post(client, payload))
                break
            except Exception as e:  # noqa: BLE001  超时/网络/连接异常 → 换新连接重试
                last_exc = e
                retry_delay = (
                    min(e.retry_after_seconds, self.read_timeout)
                    if isinstance(e, _RetryableUpstreamStatus) else 0.0
                )
                # ⚠ 流式下把**这次已经流了多久**也打出来:它是判「是真卡死(≈ 一个
                # chunk 间隔就死)还是生成太长(流了很久才断)」的唯一现场证据。
                logger.warning(
                    "%s 调用第 %d/%d 次异常(将重试;本次已耗 %.1fs%s): %s",
                    self.name, attempt, self.max_attempts, time.monotonic() - started,
                    ",流式" if streaming else "", e,
                )
                if attempt < self.max_attempts and retry_delay:
                    time.sleep(retry_delay)
        if outcome is None:
            reason = f"调用异常 {type(last_exc).__name__}" if last_exc is not None else "调用异常"
            return None, reason
        return outcome

    def _attempt_post(self, client: Any, payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """非流式单次尝试。行为与 P0-44 之前完全一致(见 `_post` docstring)。"""
        resp = client.post(self.api_url, json=payload, headers=self._headers())
        business_code = _upstream_business_code(resp) if resp.status_code != 200 else None
        if resp.status_code == 429 and business_code in _RETRYABLE_UPSTREAM_CODES:
            raise _RetryableUpstreamStatus(429, resp.headers.get("Retry-After"))
        if resp.status_code != 200:
            suffix = f"/{business_code}" if business_code else ""
            return None, f"上游 {resp.status_code}{suffix}"
        try:
            return resp.json(), None
        except Exception as e:  # noqa: BLE001
            return None, f"响应解析异常: {e}"

    def _attempt_stream(self, client: Any, payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """流式(SSE)单次尝试 —— §七 P0-44。

        **为什么流式治得了 P0-40 治不了的那一半**:非流式下 `read_timeout` 是「整段
        生成必须在这么久之内回完」,那是一个**必须提前猜准的固定数字** —— 2026-08-05
        中午实测 173s、当晚 240s 三连超时,证明晚高峰吞吐下这个数字赌不赢。流式下
        httpx 的 read 超时作用在每次 socket 读上 = **chunk 间隔**,于是判据从「生成
        总共多久」换成「**还在不在吐字**」;后者与吞吐无关,不需要猜。

        **中途断掉不拿半截当成品**:流到一半抛异常 → 异常原样上抛给 `_post` 的重试
        循环(整次重来),⛔ 绝不把已累积的半截内容当结果返回 —— 半截 JSON 解出来
        可能正好是个“看着合法”的残缺结果，那比干净地失败危险得多。"""
        with client.stream("POST", self.api_url, json=payload, headers=self._headers()) as resp:
            if resp.status_code != 200:
                resp.read()
            business_code = _upstream_business_code(resp) if resp.status_code != 200 else None
            if resp.status_code == 429 and business_code in _RETRYABLE_UPSTREAM_CODES:
                raise _RetryableUpstreamStatus(429, resp.headers.get("Retry-After"))
            if resp.status_code != 200:
                suffix = f"/{business_code}" if business_code else ""
                return None, f"上游 {resp.status_code}{suffix}"
            return self._assemble_stream(resp.iter_lines())

    def _assemble_stream(self, lines: Iterable[str]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """把 SSE 行流拼成**与非流式响应体同形状**的 dict(`chat()` 上层零改动)。

        容错三条(都是"如实降级",不猜):
        ① **`[DONE]` 缺失不算错** —— 不少实现直接关连接了事;流正常结束就当收尾。
        ② **单条 chunk 不是合法 JSON → 跳过这一条**(计数 + DEBUG 留痕),不因为一
           条坏行丢掉整段生成。⚠ 但**全部坏掉**(一条都没解出来)不许静默返空,
           走下面的"整段 JSON 兜底",再不行就如实报解析失败。
        ③ **上游根本没按 `stream:true` 回 SSE**(自填制下完全可能:某个端点不认这个
           参数,原样回一整份 JSON)→ 用**数据本身**判(有没有 `data:` 行),不看
           `Content-Type`(缺头/写错头的实现太多),把整段当普通响应体解。
        """
        content_parts: List[str] = []
        # 少数实现最后一块给的是完整 `message` 而不是增量 `delta`;仅在**从未见过
        # delta 内容**时才用它兜底(覆盖式,不累加 —— 累加会把全文重复一遍)。
        fallback_message_content = ""
        tool_calls_by_index: Dict[int, Dict[str, Any]] = {}
        finish_reason: Optional[str] = None
        resp_id = ""
        model = self.model
        role = "assistant"
        extra_top_level: Dict[str, Any] = {}
        plain_lines: List[str] = []
        chunks = 0
        malformed = 0
        started = time.monotonic()

        for raw in lines:
            line = (raw or "").strip()
            if not line:
                continue
            if not line.startswith("data:"):
                plain_lines.append(line)   # 兜底③ 的素材(也顺带吞掉 `event:`/`:` 心跳行)
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except Exception:  # noqa: BLE001  容错②
                malformed += 1
                logger.debug("%s 流式:跳过一条解不出的 chunk(第 %d 条坏行)", self.name, malformed)
                continue
            if not isinstance(obj, dict):
                malformed += 1
                continue
            chunks += 1
            if obj.get("id"):
                resp_id = str(obj["id"])
            if obj.get("model"):
                model = str(obj["model"])
            # 顶层搜索结果若在流里出现则原样保留。
            if obj.get("web_search"):
                extra_top_level["web_search"] = obj["web_search"]
            if isinstance(obj.get("usage"), dict):
                # OpenAI-compatible SSE providers normally attach usage to the
                # final chunk.  Preserve it verbatim so chat() can normalize it
                # with the same rules as the non-streaming path.
                extra_top_level["usage"] = obj["usage"]
            choices = obj.get("choices") or []
            if not choices or not isinstance(choices[0], dict):
                continue
            ch0 = choices[0]
            if ch0.get("finish_reason"):
                finish_reason = ch0["finish_reason"]
            delta = ch0.get("delta") if isinstance(ch0.get("delta"), dict) else {}
            if delta.get("role"):
                role = str(delta["role"])
            piece = delta.get("content")
            if isinstance(piece, str) and piece:
                content_parts.append(piece)
            # ⚠ `reasoning_content`(思考型模型的思维链)**刻意不并入 content** ——
            # 非流式路径返回的也只有 `message.content`,并进来就不等价了。它照样算
            # 一次 chunk,故"还在思考"不会被 chunk 间隔超时误杀。
            msg = ch0.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                fallback_message_content = msg["content"]
            for tc in delta.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                idx = tc.get("index")
                idx = int(idx) if isinstance(idx, int) else 0
                slot = tool_calls_by_index.setdefault(
                    idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    slot["id"] = str(tc["id"])
                if tc.get("type"):
                    slot["type"] = str(tc["type"])
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] = str(fn["name"])
                if isinstance(fn.get("arguments"), str):
                    slot["function"]["arguments"] += fn["arguments"]

        if chunks == 0:
            # 兜底③:一条 `data:` 都没解出来 —— 大概率上游压根没走 SSE。
            text = "\n".join(plain_lines).strip()
            if not text:
                return None, f"流式响应为空(坏行 {malformed} 条)"
            try:
                body = json.loads(text)
            except Exception as e:  # noqa: BLE001
                return None, f"响应解析异常: {e}"
            logger.warning("%s 请求了 stream:true,但上游回的不是 SSE(已按整段响应解析)", self.name)
            return (body, None) if isinstance(body, dict) else (None, "响应结构异常: 顶层不是对象")

        content = "".join(content_parts) or fallback_message_content
        message: Dict[str, Any] = {"role": role, "content": content}
        if tool_calls_by_index:
            message["tool_calls"] = [tool_calls_by_index[k] for k in sorted(tool_calls_by_index)]
        body = {
            "id": resp_id,
            "model": model,
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
        }
        body.update(extra_top_level)
        # 生产判据埋点:这一行就是"生成超过旧的 240s 固定墙也照样活着"的现场证据。
        logger.info(
            "%s 流式生成完成:%.1fs / %d 个 chunk / %d 字%s",
            self.name, time.monotonic() - started, chunks, len(content),
            f" / 坏行 {malformed} 条" if malformed else "",
        )
        return body, None


__all__ = ["OpenAICompatProvider"]
