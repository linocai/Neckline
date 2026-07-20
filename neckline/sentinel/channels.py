"""推送通道抽象(plan §3.5/§3.6「推送通道先做抽象+两个实现」)。

    · `ConsoleChannel`(默认):打日志,永远可用,零配置——本地跑哨兵脚本时的
      兜底可见性,即使没有配置任何外部推送,用户盯着终端也能看到事件。
    · `BarkChannel`(iOS 推送):`BARK_URL` 从 `.env` 读(如
      `https://api.day.app/<你的device key>`),一条 POST 即推。**缺省不崩**——
      未配置 `BARK_URL` 时 `send()` 直接返回 `False`(优雅跳过,不抛异常),
      引擎据此只当"这个通道没送达",不影响其它通道/主循环。
    · `MacNotifyChannel`(可选,`osascript`):本地 macOS 通知,不依赖网络。
      未在非 macOS / 无 `osascript` 环境自动生效(`shutil.which` 探测)。

**载体拍板未定案**(§3.5,阶段4前用户拍板 A/B/C 三选项;推荐 B = Bark+轻量Web),
Bark 实现成本极低,阶段3先备着,不代表载体已经拍板。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import Any, List, Optional

from neckline.config import Settings
from neckline.config import settings as _default_settings

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 3.0
_READ_TIMEOUT = 5.0
_MAX_ATTEMPTS = 2

LEVEL_INFO = "info"
LEVEL_WARN = "warn"
LEVEL_CRITICAL = "critical"


class PushChannel(ABC):
    name: str = "base"

    @abstractmethod
    def send(self, title: str, body: str, *, level: str = LEVEL_INFO, transport: Optional[Any] = None) -> bool:
        """推送一条消息。返回是否送达成功——**永不抛异常**,失败原因记日志,让
        调用方(engine.py)能继续跑其它通道/下一个事件,不因单一通道故障拖垮
        整个哨兵主循环。"""
        raise NotImplementedError


class ConsoleChannel(PushChannel):
    """默认通道:打日志。永远"成功"(日志本身写不进去是另一个量级的系统故障,
    不在本通道的降级职责范围内)。"""

    name = "console"

    def send(self, title: str, body: str, *, level: str = LEVEL_INFO, transport: Optional[Any] = None) -> bool:
        logger.info("[哨兵推送][%s] %s\n%s", level.upper(), title, body)
        return True


class BarkChannel(PushChannel):
    """Bark(iOS 推送 App)。POST JSON 到 `bark_url`(而非 GET 路径拼接)——Bark
    官方 API 两种都支持,POST+JSON 对中文/标点更稳健,不必操心 URL 转义。"""

    name = "bark"

    def __init__(self, bark_url: Optional[str] = None) -> None:
        self.bark_url = (bark_url or "").strip().rstrip("/") or None

    def send(self, title: str, body: str, *, level: str = LEVEL_INFO, transport: Optional[Any] = None) -> bool:
        if not self.bark_url:
            return False  # 未配置 BARK_URL,优雅跳过,不算异常
        try:
            import httpx
        except ImportError:  # pragma: no cover
            logger.warning("httpx 未安装,Bark 推送跳过")
            return False

        payload = {"title": title, "body": body, "group": "neckline"}
        if level == LEVEL_CRITICAL:
            payload["level"] = "critical"
            payload["sound"] = "alarm"

        timeout = httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)
        last_exc: Optional[BaseException] = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                client_kwargs = {"timeout": timeout}
                if transport is not None:
                    client_kwargs["transport"] = transport
                with httpx.Client(**client_kwargs) as client:
                    resp = client.post(self.bark_url, json=payload)
                if resp.status_code == 200:
                    return True
                logger.warning("Bark 推送非200(%s,尝试%d/%d)", resp.status_code, attempt, _MAX_ATTEMPTS)
            except Exception as e:  # noqa: BLE001
                last_exc = e
                logger.warning("Bark 推送异常(尝试%d/%d):%s", attempt, _MAX_ATTEMPTS, e)
        if last_exc is not None:
            logger.warning("Bark 推送全部尝试失败:%s", last_exc)
        return False


def _escape_applescript(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


class MacNotifyChannel(PushChannel):
    """可选:macOS 本地通知(`osascript`)。非 macOS 或找不到 `osascript` 时
    `send()` 直接返回 False(不崩,静默降级)。"""

    name = "mac"

    def send(self, title: str, body: str, *, level: str = LEVEL_INFO, transport: Optional[Any] = None) -> bool:
        if shutil.which("osascript") is None:
            return False
        script = (
            f'display notification "{_escape_applescript(body)}" '
            f'with title "{_escape_applescript(title)}"'
        )
        try:
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=5)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("macOS 本地通知失败:%s", e)
            return False


def default_channels(settings_obj: Optional[Settings] = None) -> List[PushChannel]:
    """`ConsoleChannel` 恒在;`BarkChannel` 仅当 `.env` 配了 `BARK_URL` 才加入。
    `MacNotifyChannel` 不在默认集合里(§3.6"可选加"),由 `scripts/sentinel.py`
    的 `--mac-notify` 显式开启。"""
    s = settings_obj or _default_settings
    channels: List[PushChannel] = [ConsoleChannel()]
    if s.bark_url:
        channels.append(BarkChannel(s.bark_url))
    return channels


def push_all(channels: List[PushChannel], title: str, body: str, *, level: str = LEVEL_INFO) -> List[str]:
    """向全部通道推送,返回成功送达的通道名列表(供 engine.py 记日志/判断"是否
    至少有一条通道送达")。单个通道异常已在各自 `send()` 内部兜住,这里额外加
    一层 try/except 只是双保险(纪律:推送失败绝不能反过来打断哨兵主循环)。"""
    delivered: List[str] = []
    for ch in channels:
        try:
            if ch.send(title, body, level=level):
                delivered.append(ch.name)
        except Exception as e:  # noqa: BLE001
            logger.warning("推送通道 %s 异常(已忽略,不影响其它通道):%s", ch.name, e)
    return delivered


__all__ = [
    "PushChannel",
    "ConsoleChannel",
    "BarkChannel",
    "MacNotifyChannel",
    "default_channels",
    "push_all",
    "LEVEL_INFO",
    "LEVEL_WARN",
    "LEVEL_CRITICAL",
]
