"""V4 进程配置。

只从环境与 `.env` 读取晨报、鉴权、推送和资讯实际用到的变量。未知变量跳过，
不因为服务器上暂时还留着旧变量而拒绝启动。已存在的环境变量优先于 `.env`。
凭据不进入 Git。

用法:
    from neckline.config import settings
    settings.api_token
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

# neckline/config/__init__.py -> neckline/config -> neckline -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

# 晨报命令行直接读其中一部分；Settings 只保留鉴权与 APNs 字段。
KNOWN_ENV_KEYS = frozenset({
    "API_TOKEN",
    "APNS_BUNDLE_ID",
    "APNS_KEY_ID",
    "APNS_KEY_PATH",
    "APNS_TEAM_ID",
    "APNS_USE_SANDBOX",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_MODEL",
    "JIN10_MCP_TOKEN",
    "KX_BASE_URL",
    "KX_PICKS_TOKEN",
    "KX_READ_KEY",
    "MAIRUI_LICENCE",
    "NECKLINE_MCP_AUTH_DB",
    "NECKLINE_MCP_PASSWORD_HASH",
    "NECKLINE_MCP_PASSWORD_SALT",
    "NECKLINE_PUBLIC_BASE",
    "NECKLINE_READ_KEY",
    "NECKLINE_V4_DB_PATH",
    "TUSHARE_TOKEN",
})


def load_env_file(path: Path | None = None) -> None:
    """把文件里的已知变量填进环境（不覆盖已有值）。未知变量与读不到的文件都忽略。"""
    target = ENV_PATH if path is None else path
    try:
        parsed = dotenv_values(target)
    except OSError:
        return
    for key, value in parsed.items():
        if key not in KNOWN_ENV_KEYS or value is None or key in os.environ:
            continue
        os.environ[key] = value


if os.environ.get("PYTHON_DOTENV_DISABLED") != "1":
    load_env_file()


@dataclass(frozen=True)
class Settings:
    api_token: Optional[str] = None
    apns_key_id: Optional[str] = None
    apns_team_id: Optional[str] = None
    apns_bundle_id: Optional[str] = None
    apns_key_path: Optional[str] = None
    apns_use_sandbox: bool = True

    @property
    def has_apns_config(self) -> bool:
        """APNs 凭证四要素齐全才能真推；缺一则不推。"""
        return bool(
            self.apns_key_id and self.apns_team_id
            and self.apns_bundle_id and self.apns_key_path
        )


def _clean(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    return text or None


def _bool(value: Optional[str], default: bool) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return default
    return text in ("1", "true", "yes", "on")


def _load_settings() -> Settings:
    return Settings(
        api_token=_clean(os.environ.get("API_TOKEN")),
        apns_key_id=_clean(os.environ.get("APNS_KEY_ID")),
        apns_team_id=_clean(os.environ.get("APNS_TEAM_ID")),
        apns_bundle_id=_clean(os.environ.get("APNS_BUNDLE_ID")),
        apns_key_path=_clean(os.environ.get("APNS_KEY_PATH")),
        apns_use_sandbox=_bool(os.environ.get("APNS_USE_SANDBOX"), True),
    )


settings = _load_settings()


def reload_settings() -> Settings:
    """重新从当前环境变量加载，并刷新模块级 `settings`。"""
    global settings
    settings = _load_settings()
    return settings


__all__ = [
    "KNOWN_ENV_KEYS",
    "Settings",
    "settings",
    "load_env_file",
    "reload_settings",
    "PROJECT_ROOT",
    "ENV_PATH",
]
