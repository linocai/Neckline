"""MCP 公开地址和连接口令。缺了或格式不对就当 MCP 未就绪，不让 API 起不来。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_HEX = re.compile(r"[0-9a-f]+")


@dataclass(frozen=True)
class McpSettings:
    public_base: str
    password_salt: str
    password_hash: str
    auth_db: Path

    @property
    def resource(self) -> str:
        return self.public_base + "/mcp"

    @property
    def origin(self) -> str:
        parts = urlsplit(self.public_base)
        return f"{parts.scheme}://{parts.netloc}"


def _base(value: str) -> str | None:
    raw = value.strip().rstrip("/")
    parts = urlsplit(raw)
    if parts.scheme != "https" or parts.username or parts.password or parts.query or parts.fragment:
        return None
    if parts.path or not parts.hostname or " " in parts.netloc:
        return None
    return f"https://{parts.netloc}"


def _hex_bytes(value: str, exact: int | None = None, minimum: int | None = None) -> str | None:
    text = value.strip().lower()
    if not _HEX.fullmatch(text) or len(text) % 2:
        return None
    size = len(text) // 2
    if exact is not None and size != exact:
        return None
    if minimum is not None and size < minimum:
        return None
    return text


def load_mcp_settings() -> McpSettings | None:
    public_base = _base(os.environ.get("NECKLINE_PUBLIC_BASE") or "")
    salt = _hex_bytes(os.environ.get("NECKLINE_MCP_PASSWORD_SALT") or "", minimum=16)
    digest = _hex_bytes(os.environ.get("NECKLINE_MCP_PASSWORD_HASH") or "", exact=32)
    raw_db = (os.environ.get("NECKLINE_MCP_AUTH_DB") or "").strip()
    path = Path(raw_db) if raw_db else None
    if not public_base or not salt or not digest or path is None or not path.is_absolute():
        return None
    return McpSettings(public_base, salt, digest, path)
