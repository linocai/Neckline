"""按 K9 现役任务选出可用的 ``LLMProvider``。

解析链路：
    1. 读 `llm_providers` 表全部行 + `app_settings.llm_task_routes`/
       `llm_default_provider` 两列(每次调用现读,不缓存——`PUT /settings/
       providers/*`/`PUT /settings/llm-routes` 落库后下一次调用即生效,不重启,
       这条铁律与 V1 `resolve_llm()` 的"DB 覆盖现读"一脉相承)。
    2. 交给 `neckline.llm.router.resolve_task_provider_name()` 决定用哪个
       provider **名字**(有路由用路由,其余回退 `llm_default_provider`)。
    3. 按名字查行；行不存在、禁用或没 key 时返回 ``None``。
    4. 可用 → 按数据库中的端点、模型和密钥构造 ``OpenAICompatProvider``；
       provider 名字完全自填，不使用供应商品牌白名单。

``market_direction`` 与 ``news_scan`` 会额外要求 Tavily key 并套上检索包装；
``explain`` 与 ``playbook`` 不联网。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from neckline.config import Settings
from neckline.llm.base import LLMProvider
from neckline.llm.openai_compat import OpenAICompatProvider
from neckline.llm.router import (
    DEFAULT_SEARCH_TASKS,
    resolve_task_provider_name,
)
from neckline.settings_store import get_llm_routes, get_tavily_api_key, list_providers


def get_provider(
    task: Optional[str] = None,
    *,
    db_path: Optional[Path] = None,
    settings_obj: Optional[Settings] = None,  # noqa: ARG001  V1 遗留签名位,见模块头
) -> Optional[LLMProvider]:
    rows = list_providers(db_path=db_path)
    routes, default_provider = get_llm_routes(db_path=db_path)
    name = resolve_task_provider_name(task, routes=routes, default_provider=default_provider, rows=rows)
    if not name:
        return None
    row = next((r for r in rows if r.name == name), None)
    if row is None or not row.enabled or not row.api_key:
        return None
    provider = OpenAICompatProvider(
        api_key=row.api_key,
        model=row.model,
        name=row.name,
        api_url=row.base_url,
        # 联网统一走 Tavily；不向推理模型发送供应商私有搜索工具。
        has_web_search=False,
        search_engine=None,
        read_timeout=None,
        use_streaming=False,
    )
    if task in DEFAULT_SEARCH_TASKS:
        from neckline.search.tavily import TavilyGroundedProvider, TavilySearchClient

        tavily_key = get_tavily_api_key(db_path=db_path)
        if not tavily_key:
            return None
        return TavilyGroundedProvider(provider, TavilySearchClient(tavily_key))
    return provider


__all__ = ["get_provider"]
