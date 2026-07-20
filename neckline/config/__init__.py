"""配置读取(plan §3.9 `config/`):读 `.env`、定义路径常量。

铁律:token / key 绝不写进任何被 git 跟踪的文件,一律从 `.env` 读。
`.env` 已在 `.gitignore`。LLM 两项(`LLM_PROVIDER`/`LLM_API_KEY`)阶段 0 允许缺省
——数据层完全不碰 LLM,缺省时以 None 优雅传递,不报错、不崩。

用法:
    from neckline.config import settings
    settings.tushare_token   # str | None
    settings.parquet_dir     # Path,已确保存在(ensure_data_dirs 后)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# neckline/config/__init__.py -> neckline/config -> neckline -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

# override=False:已存在的环境变量(如 CI 注入)优先于 .env 文件内容。
load_dotenv(ENV_PATH, override=False)

DATA_DIR = PROJECT_ROOT / "data"
PARQUET_DIR = DATA_DIR / "parquet"
DB_PATH = DATA_DIR / "neckline.db"


@dataclass(frozen=True)
class Settings:
    tushare_token: Optional[str]
    llm_provider: Optional[str]
    llm_api_key: Optional[str]
    bark_url: Optional[str] = None
    project_root: Path = PROJECT_ROOT
    data_dir: Path = DATA_DIR
    parquet_dir: Path = PARQUET_DIR
    db_path: Path = DB_PATH


def _load_settings() -> Settings:
    def _clean(v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        return v or None

    return Settings(
        tushare_token=_clean(os.environ.get("TUSHARE_TOKEN")),
        llm_provider=_clean(os.environ.get("LLM_PROVIDER")),
        llm_api_key=_clean(os.environ.get("LLM_API_KEY")),
        # 阶段3 §3.6 推送通道:Bark 推送 URL(如 https://api.day.app/<你的key>),
        # 缺省 = None,`sentinel.channels.BarkChannel` 据此优雅降级为不推送(不崩)。
        bark_url=_clean(os.environ.get("BARK_URL")),
    )


settings = _load_settings()


def reload_settings() -> Settings:
    """重新从环境变量加载(测试 / .env 热改后用)。刷新模块级 `settings` 名字。"""
    global settings
    settings = _load_settings()
    return settings


def ensure_data_dirs() -> None:
    """确保 `data/parquet/` 与 `data/neckline.db` 所在目录存在。"""
    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


__all__ = [
    "Settings",
    "settings",
    "reload_settings",
    "ensure_data_dirs",
    "PROJECT_ROOT",
    "DATA_DIR",
    "PARQUET_DIR",
    "DB_PATH",
]
