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

**V2-②(plan §五 V2-②/§3.10-B)起,本类可直接实例化**:`neckline.llm.factory.
get_provider()` 不再只经由 `GLMProvider`/`KimiProvider` 两个子类构造 provider,
而是把 `llm_providers` 表的一行(`base_url`/`model`/`api_key`/`has_web_search`/
`search_engine`)直接喂给本类的构造函数——"自填制"下任意 OpenAI 兼容端点都能
配成一个可用 provider。为此,`_search_tools`/`_handle_tool_call`/
`_extract_top_level_search_hits`/`_search_engine_value` 四个原本要求子类必须
覆盖的钩子,在本类里各自有了一份**通用默认实现**(协议沿用 GLM 的 `web_search`
工具形状——这是本项目目前唯一有文档验证过的联网搜索协议;`has_web_search=0`
时这份通用实现直接不发 `tools`,见 `_search_tools`)。`GLMProvider`/`KimiProvider`
两个子类各自完整覆盖这四个钩子,行为与 V1 逐字节不变,不受本类新增默认实现影响。
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
    # V2-②(自填制,§3.10-B):裸实例默认不带搜索能力,由构造函数按 `llm_providers`
    # 行的 `has_web_search`/`search_engine` 两列覆盖。GLM/Kimi 子类各自完整覆盖了
    # 四个搜索钩子,不读这两个属性,不受影响。
    has_web_search: bool = False
    search_engine: Optional[str] = None
    # 检索词长度上限(防御性截断,非官方文档明确数字;原为 GLM 专属类属性,V2-②
    # 起下沉到基类供通用 provider 共享——`GLMProvider` 不再重复声明同一个数字,
    # 直接继承本值)。截断只影响检索词,不影响提问本身——问题全文照样在 messages 里。
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
    ) -> None:
        """`name`/`api_url`/`has_web_search`/`search_engine` 均为**可选覆盖**
        (默认 `None` = 不改类属性),故 `GLMProvider(api_key="sk-xxx")`/
        `KimiProvider(api_key="sk-xxx")` 这类既有调用方式**逐字节不变**——只有
        `neckline.llm.factory.get_provider()` 拿 `llm_providers` 行构造裸
        `OpenAICompatProvider` 实例时才会用到这四个新参数。"""
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

    # —— provider 特有钩子(子类可覆盖;未覆盖时走下面的通用默认实现)———————
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {(self.api_key or '').strip()}", "Content-Type": "application/json"}

    def _search_tools(self, search_query: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """通用默认实现(V2-② 新增,仅裸 `OpenAICompatProvider` 实例会走到这里;
        `GLMProvider`/`KimiProvider` 各自整体覆盖本方法,不受影响)。

        `self.has_web_search=False`(自填 provider 未勾选联网搜索)→ 直接返回
        `None`,不发 `tools`/`search_query` 两键——**不给上游不认识的参数**
        (§3.10-B 铁律,v1.3.4 案底:传一个不被认识的取值会 `ok=True` 静默返 0 条,
        比报错更难查)。

        `self.has_web_search=True` 时协议沿用 GLM 的 `web_search` 工具形状——这是
        本项目目前唯一有文档验证过、真实跑通过的联网搜索协议,`llm_providers.
        search_engine` 列的存在就是为了喂这里的 `search_engine` 键。**已知代价
        (如实登记,不是 bug)**:真正非 GLM 协议的自填端点若也勾了
        `has_web_search=1`,发过去的这份声明很可能不被对方识别——自填制把"这个
        端点认不认这份协议"的判断责任交给了配置它的人;识别不了时既有降级链
        (0 命中告警 / 非 200 / 非法 JSON)照常兜底,不会崩。

        `search_query=None` 时**不改变是否发送该键之外的其余字段**(同
        `tests/test_llm.py::TestSearchQueryOptIn` 对 GLM 锁的同一条纪律)。
        """
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
        """通用默认实现:上面的 `_search_tools` 走的是"服务端一轮出结果"协议(同
        GLM),理论上不会真的收到需要客户端处理的 `tool_call`;防御性占位回复,
        避免死循环(与 `providers/glm.py::GLMProvider._handle_tool_call` 同一姿势)。"""
        return {"role": "tool", "tool_call_id": tool_call.get("id", ""), "content": "{}"}, None

    def _extract_top_level_search_hits(self, body: Dict[str, Any]) -> List[SearchHit]:
        """通用默认实现:解析顶层 `web_search` 数组(GLM 协议形状)。对 Kimi 这类
        响应体里从不出现该键的 provider **零行为影响**——`body.get("web_search")`
        恒 `None`,循环 0 次,返回 `[]`,与 V1 完全一致。"""
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
        **需要暴露该值的子类(如 GLM)必须读与 `_search_tools` 相同的单一源常量**,
        不允许另抄一份字面量(见 `providers/glm.py::_SEARCH_ENGINE`)。"""
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
            if enable_search and not all_hits:
                # 埋点(v1.3.4):开了搜索却一条都没回来 = 静默失效,journalctl 里必须留痕。
                # 2026-07-27 实测 GLM 对无法识别的 search_engine 就是这个形状(ok=True + 0 条
                # + 不报错),生产 20260721/22/23 三天 10/10 空命中当时无人察觉。用户侧的
                # 对应露出见 `llm.base.search_coverage_line` 的调用点。
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
