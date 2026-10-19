"""KX 相关环境配置。每次调用时读环境，缺失或格式不对一律当作未配置，不让 API 起不来。"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "https://linotsai.top"
_READ_KEY = re.compile(r"[A-Za-z0-9_-]{32,128}")
PICKS_TOKEN_MIN = 16


def base_url() -> str:
    """KX 的公开地址（到域名为止）。必须是 https，不带路径、账号与查询；不合格退回默认值。"""
    raw = (os.environ.get("KX_BASE_URL") or "").strip().rstrip("/")
    if not raw:
        return DEFAULT_BASE_URL
    parts = urlsplit(raw)
    if parts.scheme != "https" or not parts.hostname or parts.path or parts.query or parts.fragment or parts.username:
        return DEFAULT_BASE_URL
    return f"https://{parts.netloc}"


def read_key() -> str | None:
    """KX 只读页面的访问码，放在 URL 路径里，只接受 URL 安全字符，防止拼出别的路径。"""
    value = (os.environ.get("KX_READ_KEY") or "").strip()
    return value if _READ_KEY.fullmatch(value) else None


def picks_token() -> str | None:
    value = (os.environ.get("KX_PICKS_TOKEN") or "").strip()
    return value if len(value) >= PICKS_TOKEN_MIN else None
