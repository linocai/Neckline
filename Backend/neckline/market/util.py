"""行情共用的小函数。价格和金额在各自模块里换算，这里只做解析。"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def silence_http_logs() -> None:
    """httpx 会把完整 URL 打进 INFO，麦蕊证书在路径里，不能记。"""
    for name in ("httpx", "httpcore", "httpx2", "httpcore2"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def num(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def shanghai_now() -> datetime:
    return datetime.now(SHANGHAI)


class MarketError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)
