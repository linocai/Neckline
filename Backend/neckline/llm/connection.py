"""Local validation for explicitly configured Chat Completions connections."""
from urllib.parse import urlsplit, urlunsplit


def chat_endpoint(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(c.isspace() for c in value.strip()):
        raise ValueError("API 地址不能为空或包含空格")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("API 地址格式无效") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API 地址须为 HTTPS，不能含密钥、查询参数或用户名密码")
    path = parsed.path.rstrip("/")
    if path.endswith(("/responses", "/messages")):
        raise ValueError("此处需要 Chat Completions 地址")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return urlunsplit(("https", host + (f":{port}" if port is not None else ""), path, "", ""))


def model_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200 or any(c.isspace() or ord(c) < 32 for c in value.strip()):
        raise ValueError("模型名称不能为空、包含空格或超过 200 字符")
    return value.strip()


def connection_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80 or any(c in value for c in "/?#%") or any(ord(c) < 32 for c in value):
        raise ValueError("连接名称须为 1–80 字符，不能包含 / ? # %")
    return value.strip()
