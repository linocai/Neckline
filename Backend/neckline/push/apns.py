"""APNs token-based 推送(ES256 JWT)—— plan 4B.5(🔴 高危区)。

**复用 LinoN `backend/app/push/apns.py`** 的经过设计的实现(token-based JWT / HTTP2 /
可注入 transport 免联网单测),改动点:
    · 读 `neckline.config.settings`(而非 LinoN 的 pydantic settings),字段前缀 `apns_*`。
    · `.p8` 是**账号级密钥**(Key ID Q963AP3VY8 / Team HX73DFL88G),直接复用给新 Bundle
      ID `top.linotsai.neckline`——`apns-topic` 换成新 Bundle ID 即可(§3.6)。
    · category 只保留当前信息类推送，不带动作按钮。

token-based JWT:.p8 私钥 + KeyID(kid)+ TeamID(iss);header alg=ES256;
payload {iss, iat};Authorization: bearer <jwt>;apns-topic = BundleID。
JWT 缓存 ≤ ~50min(Apple 要求 token 寿命 20–60min,过期重签)。

dev 网关:api.sandbox.push.apple.com(APNS_USE_SANDBOX=true);prod:api.push.apple.com。

可注入/可 mock:
  · send_push(...) 通过 transport 回调真发 HTTP/2(默认 _http2_post);测试注入假 transport,
    不依赖真 .p8、不真连 Apple。
  · JWT 签名单测用临时生成的 EC key(P-256),验证 header/claims 与 ES256 可被公钥验签。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from neckline.config import settings
from neckline.notify_kinds import CATEGORY_DIGEST, CATEGORY_IMPORTANT

logger = logging.getLogger(__name__)

# APNs 网关
GATEWAY_SANDBOX = "https://api.sandbox.push.apple.com"
GATEWAY_PROD = "https://api.push.apple.com"

# 锁屏动作分类(信息类,无动作按钮;客户端注册对应 UNNotificationCategory)。
#
# 当前只剩两个 category:盘后汇总与竞价核对。category 只决定「怎么响」,
# 「响不响」由事件自带的 `kind` + 按 kind 配的开关决定(按 category 配会连坐)。
# 三个字面量与全部 kind→level 归属的**唯一源是 `neckline.notify_kinds`**,本模块
# 只做本地别名(`push/` 是最底层的传输件,不该反向 import 上层;别名保证两处永远
# 是同一个串,`tests/test_notify_kinds.py` 有对拍断言)。
#
# JWT 刷新窗口:Apple 接受 20–60min,留余量 ~50min 重签。
_JWT_TTL_SEC = 50 * 60


@dataclass
class PushResult:
    ok: bool
    status: int            # HTTP 状态码(成功 200);本地未发=0
    reason: str            # apns 错误 reason 或本地原因
    apns_id: str = ""


# —— JWT 缓存 ————————————————————————————————————————————————————

_jwt_cache: Dict[str, Any] = {"token": None, "iat": 0, "kid": None}

# Readiness is deliberately separate from the JWT cache.  A broken or missing
# credential must not cause the two-second worker loop to repeatedly open and
# parse a private key.  Metadata changes invalidate a negative cache early;
# otherwise a short recheck period lets an operator restore a mounted secret
# without restarting the worker.
_READINESS_RECHECK_SECONDS = 60
_readiness_cache: Dict[str, Any] = {
    "signature": None, "metadata": None, "checked_at": 0.0, "value": None,
}


@dataclass(frozen=True)
class APNsReadiness:
    """Safe operational status for health endpoints and the notification worker.

    ``code`` is intentionally a small allow-list.  It never contains a file
    path, private-key material, provider response, or OS exception text.
    """

    ready: bool
    code: str


_READINESS_CODES = frozenset(("ready", "credentials_missing", "key_unreadable", "key_invalid"))


def _settings_signature() -> tuple[object, ...]:
    return (settings.apns_key_id, settings.apns_team_id, settings.apns_bundle_id,
            settings.apns_key_path, settings.apns_use_sandbox)


def _key_metadata(path_value: Optional[str]) -> tuple[object, ...]:
    if not path_value:
        return ("no_path",)
    try:
        stat = Path(path_value).stat()
    except OSError:
        return ("unreadable",)
    return ("file", stat.st_mtime_ns, stat.st_size)


def apns_readiness(*, now: Optional[float] = None, force: bool = False) -> APNsReadiness:
    """Check actual APNs credential usability without exposing secret details.

    This performs one ES256 signing attempt after a readable key changes (or a
    short negative-cache interval elapses).  It does not contact APNs.
    """
    checked_at = time.monotonic() if now is None else now
    signature = _settings_signature()
    metadata = _key_metadata(settings.apns_key_path)
    cached = _readiness_cache.get("value")
    if (not force and cached is not None and _readiness_cache.get("signature") == signature
            and _readiness_cache.get("metadata") == metadata
            and checked_at - float(_readiness_cache.get("checked_at", 0.0)) < _READINESS_RECHECK_SECONDS):
        return cached
    if not settings.has_apns_config:
        value = APNsReadiness(False, "credentials_missing")
    elif metadata[0] != "file":
        value = APNsReadiness(False, "key_unreadable")
    else:
        try:
            key_pem = _read_key(settings.apns_key_path)  # type: ignore[arg-type]
            # Signing is the required usable-key check.  Do not cache this token:
            # get_jwt owns the valid short-lived token cache.
            build_jwt(key_pem=key_pem, key_id=settings.apns_key_id,  # type: ignore[arg-type]
                      team_id=settings.apns_team_id, iat=int(time.time()))  # type: ignore[arg-type]
        except OSError:
            value = APNsReadiness(False, "key_unreadable")
        except Exception:  # invalid PEM, wrong key type, or signing library failure
            value = APNsReadiness(False, "key_invalid")
        else:
            value = APNsReadiness(True, "ready")
    _readiness_cache.update({"signature": signature, "metadata": metadata,
                             "checked_at": checked_at, "value": value})
    return value


def get_apns_readiness(*, force: bool = False) -> APNsReadiness:
    """Named release/operations readiness entry point; never discloses a secret."""
    return apns_readiness(force=force)


def _read_key(key_path: str) -> str:
    with open(key_path, "r", encoding="utf-8") as f:
        return f.read()


def build_jwt(*, key_pem: str, key_id: str, team_id: str, iat: Optional[int] = None) -> str:
    """构造 APNs token-based JWT(ES256)。header {alg:ES256, kid};claims {iss:TeamID, iat}。
    key_pem 为 PKCS#8 EC 私钥 PEM(.p8 内容);单测可传临时 EC key 的 PEM。"""
    import jwt  # PyJWT(局部 import:未装依赖时不拖垮整个 config 导入链)

    now = int(iat if iat is not None else time.time())
    return jwt.encode(
        {"iss": team_id, "iat": now},
        key_pem,
        algorithm="ES256",
        headers={"kid": key_id, "alg": "ES256"},
    )


def get_jwt(now: Optional[int] = None) -> Optional[str]:
    """取缓存 JWT(≤ ~50min 复用,过期重签)。不可用凭证只返回 None。"""
    if not apns_readiness().ready:
        return None
    now = int(now if now is not None else time.time())
    cached = _jwt_cache.get("token")
    if (
        cached
        and _jwt_cache.get("kid") == settings.apns_key_id
        and now - int(_jwt_cache.get("iat", 0)) < _JWT_TTL_SEC
    ):
        return cached
    try:
        token = build_jwt(
            key_pem=_read_key(settings.apns_key_path),  # type: ignore[arg-type]
            key_id=settings.apns_key_id,    # type: ignore[arg-type]
            team_id=settings.apns_team_id,  # type: ignore[arg-type]
            iat=now,
        )
    except Exception:
        # A replacement/removal race after readiness is represented by the same
        # safe result at the send boundary, never by an exception or a path log.
        return None
    _jwt_cache.update({"token": token, "iat": now, "kid": settings.apns_key_id})
    return token


def reset_jwt_cache() -> None:
    """清 JWT/readiness 缓存(测试/凭证热切换用)。"""
    _jwt_cache.update({"token": None, "iat": 0, "kid": None})
    _readiness_cache.update({"signature": None, "metadata": None, "checked_at": 0.0, "value": None})


def build_payload(
    title: str, body: str, *, category: str,
    thread_id: Optional[str] = None, custom: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """组装 APNs payload(aps + 自定义字段)。信息类推送:alert + sound + category。"""
    aps: Dict[str, Any] = {
        "alert": {"title": title, "body": body},
        "sound": "default",
        "category": category,
    }
    if thread_id:
        aps["thread-id"] = thread_id
    payload: Dict[str, Any] = {"aps": aps}
    if custom:
        payload.update(custom)
    return payload


# Transport 签名:(url, headers, body_bytes) -> PushResult。可注入/可 mock。
Transport = Callable[[str, Dict[str, str], bytes], "PushResult"]


def _http2_post(url: str, headers: Dict[str, str], body: bytes) -> PushResult:
    """默认真发:httpx HTTP/2 POST 到 APNs。仅在真连时被调(测试注入假 transport)。"""
    try:
        import httpx
    except ImportError:
        return PushResult(ok=False, status=0, reason="httpx 未安装")
    try:
        with httpx.Client(http2=True, timeout=10.0) as client:
            resp = client.post(url, headers=headers, content=body)
        apns_id = resp.headers.get("apns-id", "")
        if resp.status_code == 200:
            return PushResult(ok=True, status=200, reason="ok", apns_id=apns_id)
        reason = ""
        try:
            reason = resp.json().get("reason", "")
        except Exception:  # noqa: BLE001
            reason = resp.text[:200]
        return PushResult(ok=False, status=resp.status_code, reason=reason, apns_id=apns_id)
    except Exception as e:  # noqa: BLE001  网络/TLS/HTTP2 协商失败
        return PushResult(ok=False, status=0, reason=f"传输异常: {e}")


def _gateway() -> str:
    return GATEWAY_SANDBOX if settings.apns_use_sandbox else GATEWAY_PROD


def send_push(
    device_token: str, title: str, body: str, *,
    category: str = CATEGORY_DIGEST,
    thread_id: Optional[str] = None,
    custom: Optional[Dict[str, Any]] = None,
    collapse_id: Optional[str] = None,
    transport: Optional[Transport] = None,
    jwt_token: Optional[str] = None,
) -> PushResult:
    """发一条 APNs 推送到单个 device_token。凭证不全 / JWT 取不到 → ok=False,reason 可读,
    **不抛崩**。transport / jwt_token 可注入(测试免真连 Apple / 免真 .p8)。"""
    transport = transport or _http2_post
    readiness = apns_readiness()
    if jwt_token is None and not readiness.ready:
        return PushResult(ok=False, status=0, reason=f"apns_{readiness.code}")
    token = jwt_token if jwt_token is not None else get_jwt()
    if token is None:
        return PushResult(ok=False, status=0, reason="apns_key_unreadable")

    payload = build_payload(title, body, category=category, thread_id=thread_id, custom=custom)
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "authorization": f"bearer {token}",
        "apns-topic": settings.apns_bundle_id or "",
        "apns-push-type": "alert",
        "apns-priority": "10",
        "content-type": "application/json",
    }
    if collapse_id:
        encoded = collapse_id.encode("ascii", errors="strict")
        if len(encoded) > 64:
            raise ValueError("APNs collapse_id 不能超过 64 个 ASCII 字节")
        headers["apns-collapse-id"] = collapse_id
    url = f"{_gateway()}/3/device/{device_token}"
    return transport(url, headers, body_bytes)


__all__ = [
    "PushResult",
    "CATEGORY_IMPORTANT", "CATEGORY_DIGEST",
    "APNsReadiness", "apns_readiness", "get_apns_readiness", "build_jwt", "get_jwt", "reset_jwt_cache", "build_payload", "send_push",
    "GATEWAY_SANDBOX", "GATEWAY_PROD",
]
