"""从 `.env`(`LLM_PROVIDER`/`LLM_API_KEY`)选出可用的 `LLMProvider` 实例(plan §3.4)。

无 provider 名 / 无 key / provider 名不认识 → 返回 `None`——调用方(`judge.py`)据此
走「LLM 未激活」降级路径,不抛异常(阶段2 铁律:全链路必须在无 key 下优雅降级
跑通)。当前 `.env` 只有 `TUSHARE_TOKEN`,没有 `LLM_PROVIDER`/`LLM_API_KEY`,即
`get_provider()` 在本项目现状下恒返回 `None`——这正是需要覆盖的主路径。
"""

from __future__ import annotations

from typing import Dict, Optional, Type

from neckline.config import Settings
from neckline.config import settings as _default_settings
from neckline.llm.base import LLMProvider
from neckline.llm.providers.glm import GLMProvider
from neckline.llm.providers.kimi import KimiProvider

_PROVIDERS: Dict[str, Type[LLMProvider]] = {
    "glm": GLMProvider,
    "kimi": KimiProvider,
}


def get_provider(settings_obj: Optional[Settings] = None) -> Optional[LLMProvider]:
    s = settings_obj or _default_settings
    if not s.llm_provider or not s.llm_api_key:
        return None
    cls = _PROVIDERS.get(s.llm_provider.strip().lower())
    if cls is None:
        return None
    return cls(api_key=s.llm_api_key)


__all__ = ["get_provider"]
