"""K9 现役 LLM 任务到 Provider 的纯路由。

联网只发生在 ``news_scan``，并由 Tavily 提供证据；``explain`` 与 ``playbook``
只读取已经准备好的小上下文。退役任务键不会被接受，也不会为旧设置保留兼容入口。
"""

from __future__ import annotations

from typing import Dict, Optional, Protocol, Sequence


TASK_NEWS_SCAN = "news_scan"
TASK_EXPLAIN = "explain"
TASK_PLAYBOOK = "playbook"

ALL_TASKS = (TASK_NEWS_SCAN, TASK_EXPLAIN, TASK_PLAYBOOK)
DEFAULT_SEARCH_TASKS = (TASK_NEWS_SCAN,)


class ProviderLike(Protocol):
    name: str
    enabled: bool
    has_web_search: bool


def resolve_task_provider_name(
    task: Optional[str],
    *,
    routes: Dict[str, str],
    default_provider: Optional[str],
    rows: Sequence[ProviderLike],  # noqa: ARG001 - 保持纯路由接口可直接对拍设置行
) -> Optional[str]:
    """显式任务路由优先；缺路由时使用默认 Provider。

    显式路由即使指向不存在或禁用的 Provider 也不偷偷回退，具体可用性由工厂统一
    判定。这样配置错误会如实表现为任务不可用，而不是悄悄换模型。
    """
    if task:
        routed = routes.get(task)
        if routed:
            return routed
    return default_provider


__all__ = [
    "TASK_NEWS_SCAN",
    "TASK_EXPLAIN",
    "TASK_PLAYBOOK",
    "ALL_TASKS",
    "DEFAULT_SEARCH_TASKS",
    "ProviderLike",
    "resolve_task_provider_name",
]
