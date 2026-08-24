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
    """取缓存 JWT(≤ ~50min 复用,过期重签)。APNs 配置不全 / 读 .p8 失败 → None(不抛)。"""
    if not settings.has_apns_config:
        logger.warning("APNs 配置不全(KeyID/TeamID/BundleID/.p8 路径),跳过 JWT 构造")
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
        key_pem = _read_key(settings.apns_key_path)  # type: ignore[arg-type]
    except OSError as e:
        logger.error("读取 .p8 失败(%s): %s", settings.apns_key_path, e)
        return None
    token = build_jwt(
        key_pem=key_pem,
        key_id=settings.apns_key_id,    # type: ignore[arg-type]
        team_id=settings.apns_team_id,  # type: ignore[arg-type]
        iat=now,
    )
    _jwt_cache.update({"token": token, "iat": now, "kid": settings.apns_key_id})
    return token


def reset_jwt_cache() -> None:
    """清 JWT 缓存(测试/凭证热切换用)。"""
    _jwt_cache.update({"token": None, "iat": 0, "kid": None})


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
    transport: Optional[Transport] = None,
    jwt_token: Optional[str] = None,
) -> PushResult:
    """发一条 APNs 推送到单个 device_token。凭证不全 / JWT 取不到 → ok=False,reason 可读,
    **不抛崩**。transport / jwt_token 可注入(测试免真连 Apple / 免真 .p8)。"""
    transport = transport or _http2_post
    token = jwt_token if jwt_token is not None else get_jwt()
    if token is None:
        return PushResult(ok=False, status=0, reason="APNs JWT 不可用(凭证缺失/读取失败)")

    payload = build_payload(title, body, category=category, thread_id=thread_id, custom=custom)
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "authorization": f"bearer {token}",
        "apns-topic": settings.apns_bundle_id or "",
        "apns-push-type": "alert",
        "apns-priority": "10",
        "content-type": "application/json",
    }
    url = f"{_gateway()}/3/device/{device_token}"
    return transport(url, headers, body_bytes)


__all__ = [
    "PushResult",
    "CATEGORY_IMPORTANT", "CATEGORY_DIGEST",
    "build_jwt", "get_jwt", "reset_jwt_cache", "build_payload", "send_push",
    "GATEWAY_SANDBOX", "GATEWAY_PROD",
]
