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

# override=False:已存在的环境变量(如 systemd EnvironmentFile / CI 注入)优先于 .env 文件。
# 容错:.env 存在但当前进程无读权限(ECS 上 .env 为 600 neckline:neckline,deploy 用户跑
# 维护命令时读不到)→ 绝不崩;此时配置从进程环境变量取(systemd 已注入),读不到的 key 为 None。
try:
    load_dotenv(ENV_PATH, override=False)
except OSError:
    pass

DATA_DIR = PROJECT_ROOT / "data"
PARQUET_DIR = DATA_DIR / "parquet"
DB_PATH = DATA_DIR / "neckline.db"


@dataclass(frozen=True)
class Settings:
    tushare_token: Optional[str]
    llm_provider: Optional[str]
    llm_api_key: Optional[str]
    bark_url: Optional[str] = None
    # —— 阶段4 (4A/4B) 后端服务化(plan §五 阶段4)——
    # API 鉴权:单用户共享密钥,Bearer + hmac.compare_digest,startup fail-fast len>=16。
    api_token: Optional[str] = None
    # APNs token-based 推送(复用 LinoN .p8 账号级密钥,§3.6);.p8 路径指向 ECS secret 落点。
    apns_key_id: Optional[str] = None
    apns_team_id: Optional[str] = None
    apns_bundle_id: Optional[str] = None
    apns_key_path: Optional[str] = None
    apns_use_sandbox: bool = True          # dev 直装走 sandbox 网关
    project_root: Path = PROJECT_ROOT
    data_dir: Path = DATA_DIR
    parquet_dir: Path = PARQUET_DIR
    db_path: Path = DB_PATH

    @property
    def has_api_token(self) -> bool:
        return bool(self.api_token and self.api_token.strip())

    @property
    def has_apns_config(self) -> bool:
        """APNs 凭证四要素齐全(KeyID/TeamID/BundleID/.p8 路径)才能真推;缺一 → 优雅降级不推。"""
        return bool(
            self.apns_key_id and self.apns_team_id
            and self.apns_bundle_id and self.apns_key_path
        )


def _load_settings() -> Settings:
    def _clean(v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        return v or None

    def _bool(v: Optional[str], default: bool) -> bool:
        v = (v or "").strip().lower()
        if not v:
            return default
        return v in ("1", "true", "yes", "on")

    # DB_PATH 可选覆盖(默认 data/neckline.db)。ECS 部署默认路径即 /opt/neckline/data/
    # neckline.db(相对项目根,无需设);冒烟/隔离测试可设 DB_PATH 指向临时库,不碰生产台账。
    db_override = _clean(os.environ.get("DB_PATH"))
    db_path = Path(db_override) if db_override else DB_PATH

    return Settings(
        tushare_token=_clean(os.environ.get("TUSHARE_TOKEN")),
        llm_provider=_clean(os.environ.get("LLM_PROVIDER")),
        llm_api_key=_clean(os.environ.get("LLM_API_KEY")),
        db_path=db_path,
        # 阶段3 §3.6 推送通道:Bark 推送 URL(如 https://api.day.app/<你的key>),
        # 缺省 = None,`sentinel.channels.BarkChannel` 据此优雅降级为不推送(不崩)。
        bark_url=_clean(os.environ.get("BARK_URL")),
        api_token=_clean(os.environ.get("API_TOKEN")),
        apns_key_id=_clean(os.environ.get("APNS_KEY_ID")),
        apns_team_id=_clean(os.environ.get("APNS_TEAM_ID")),
        apns_bundle_id=_clean(os.environ.get("APNS_BUNDLE_ID")),
        apns_key_path=_clean(os.environ.get("APNS_KEY_PATH")),
        apns_use_sandbox=_bool(os.environ.get("APNS_USE_SANDBOX"), True),
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
