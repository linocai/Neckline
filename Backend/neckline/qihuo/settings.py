"""Qihuo 只读 JSON 环境配置。缺失或格式不合法一律视作未配置，不影响主服务启动。"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "https://linotsai.top"
_READ_KEY = re.compile(r"[A-Za-z0-9_-]{24,128}")
_HOST = re.compile(r"[A-Za-z0-9.-]+")


def validated_base_url() -> str | None:
    """校验 QIHUO_BASE_URL：未设置时用默认域名；显式设置但畸形时返回 None（绝不静默回退）。"""
    raw = os.environ.get("QIHUO_BASE_URL")
    if raw is None or not raw.strip():
        return DEFAULT_BASE_URL
    cleaned = raw.strip().rstrip("/")
    if any(ch.isspace() or ord(ch) < 32 for ch in cleaned):
        return None
    try:
        parts = urlsplit(cleaned)
        port = parts.port  # 触发非法端口（非数字或越界）ValueError
    except ValueError:
        return None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or not _HOST.fullmatch(parts.hostname)
        or parts.path
        or parts.query
        or parts.fragment
        or parts.username is not None
        or parts.password is not None
    ):
        return None
    if port is not None and not (1 <= port <= 65535):
        return None
    host = parts.hostname
    return f"https://{host}:{port}" if port is not None else f"https://{host}"


def base_url() -> str:
    """兼容调用：返回已校验域名，非法时回退默认域名。"""
    return validated_base_url() or DEFAULT_BASE_URL


def read_key() -> str | None:
    """Qihuo 独立只读访问码（放路径段，只接受 URL 安全字符）。"""
    value = (os.environ.get("QIHUO_READ_KEY") or "").strip()
    return value if _READ_KEY.fullmatch(value) else None


def is_configured() -> bool:
    """Qihuo 域名与访问码同时合法才视为已配置（health 与拉取统一口径）。"""
    return validated_base_url() is not None and read_key() is not None


def read_url() -> str | None:
    """组装完整只读 JSON 地址；未配置或地址畸形返回 None。"""
    base = validated_base_url()
    key = read_key()
    if base is None or key is None:
        return None
    return f"{base}/qihuo/read/{key}"
