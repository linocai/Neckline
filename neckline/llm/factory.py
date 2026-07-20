"""选出可用的 `LLMProvider` 实例(plan §3.4)。

**解析优先级(阶段4 拍板,2026-07-20):DB 覆盖 → `.env` 兜底**——App 设置屏改的
provider/key 落 `app_settings` 表(`neckline.settings_store`),每次调用现读故运行时
生效不重启;DB 未设时回退 `.env`(`LLM_PROVIDER`/`LLM_API_KEY`)。

无 provider / 无 key / provider 名不认识 → 返回 `None`——调用方(`judge.py`/问询台)据此
走「LLM 未激活」降级路径,不抛异常(铁律:全链路必须在无 key 下优雅降级跑通)。

**db_path 参数**:默认 `None` 走生产库(`settings.db_path`);单测/ECS 隔离库显式传入。
`settings_obj` 仍保留(供纯 `.env` 单测注入),两者一起喂 `resolve_llm`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Type

from neckline.config import Settings
from neckline.llm.base import LLMProvider
from neckline.llm.providers.glm import GLMProvider
from neckline.llm.providers.kimi import KimiProvider
from neckline.settings_store import resolve_llm

_PROVIDERS: Dict[str, Type[LLMProvider]] = {
    "glm": GLMProvider,
    "kimi": KimiProvider,
}


def get_provider(
    settings_obj: Optional[Settings] = None, db_path: Optional[Path] = None
) -> Optional[LLMProvider]:
    provider_name, api_key = resolve_llm(default_settings=settings_obj, db_path=db_path)
    if not provider_name or not api_key:
        return None
    cls = _PROVIDERS.get(provider_name.strip().lower())
    if cls is None:
        return None
    return cls(api_key=api_key)


__all__ = ["get_provider", "_PROVIDERS"]
