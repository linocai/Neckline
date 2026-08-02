"""选出可用的 `LLMProvider` 实例(plan §五 V2-②,取代 V1 §3.4「GLM/Kimi 枚举」)。

**V2 解析链路(自填制,§3.10-B)**:
    1. 读 `llm_providers` 表全部行 + `app_settings.llm_task_routes`/
       `llm_default_provider` 两列(每次调用现读,不缓存——`PUT /settings/
       providers/*`/`PUT /settings/llm-routes` 落库后下一次调用即生效,不重启,
       这条铁律与 V1 `resolve_llm()` 的"DB 覆盖现读"一脉相承)。
    2. 交给 `neckline.llm.router.resolve_task_provider_name()` 决定用哪个
       provider **名字**(有路由用路由;检索类任务缺路由挑一个 has_web_search
       的启用中 provider;其余缺路由回退 `llm_default_provider`)。
    3. 按名字查行;行不存在 / `enabled=0` / `api_key` 未设 → 整体判「不可用」,
       返回 `None`(**全链路必须在无 key/被禁用下优雅降级跑通**,§2.0/§3.8 铁律
       一字不变,调用方——`judge.py`/`api/inquiry.py`/`report/pipeline.py`等——
       据此走既有降级路径,不用改)。
    4. 可用 → 直接构造裸 `OpenAICompatProvider`(`base_url`/`model`/`api_key`/
       `has_web_search`/`search_engine` 由行给),**不再要求 provider 名字必须是
       "glm"/"kimi" 这类白名单值**——任意 OpenAI 兼容端点都能配。

`GLMProvider`/`KimiProvider`(`llm/providers/{glm,kimi}.py`)**不是这条解析链路
的一部分**——本模块不 import 这两个类,它们降级为预置参考实现(见各自模块头
注释),只服务于既有单测的"具体测试替身"这一用途。

`task`:见 `neckline.llm.router` 的任务常量(`TASK_INQUIRY` 等);传 `None`(默认)
走"缺路由回退默认 provider"分支,适合尚未纳入 V2 任务分工、只需要"随便一个能用
的 provider"的旧调用点(如 V1 `report/pipeline.py` 候选审判,该管线将在 ⑬ 块
被篮子引擎取代,不必现在补任务语义)。

`db_path`:默认 `None` 走生产库;单测/ECS 隔离库显式传入。
`settings_obj`:V1 遗留参数,**V2 起不驱动任何解析逻辑**——自填制下不存在
".env 单 provider" 这个概念,保留纯粹是为了不破坏既有调用方签名(`report/
pipeline.py`/`tests/conftest.py` 等仍可能按老习惯传入而不报错)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from neckline.config import Settings
from neckline.llm.base import LLMProvider
from neckline.llm.openai_compat import OpenAICompatProvider
from neckline.llm.router import resolve_task_provider_name
from neckline.settings_store import get_llm_routes, list_providers


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
    return OpenAICompatProvider(
        api_key=row.api_key,
        model=row.model,
        name=row.name,
        api_url=row.base_url,
        has_web_search=row.has_web_search,
        search_engine=row.search_engine,
    )


__all__ = ["get_provider"]
