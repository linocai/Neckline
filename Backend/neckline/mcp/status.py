"""MCP 是否已经在本次进程里挂上。health 只读这个布尔，不读口令。"""

_serving = False


def set_mcp_serving(value: bool) -> None:
    global _serving
    _serving = bool(value)


def mcp_serving() -> bool:
    return _serving
