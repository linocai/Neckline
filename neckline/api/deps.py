"""鉴权依赖(plan 4A.1,🔴 高危区)。照搬 LinoN `backend/app/api/deps.py` 的经过
生产验证的姿势(Bearer + `hmac.compare_digest` 恒定时间比对 + startup fail-fast)。

单用户共享密钥:比对 `Authorization: Bearer <API_TOKEN>` 与 `.env` 的 `API_TOKEN`,
用 `hmac.compare_digest`(恒定时间,防时序侧信道)。除 `/health` 外所有端点必过。

启动 fail-fast:`require_api_token_ready()` 校验 `API_TOKEN` 存在且 `len>=16`,在 app
startup 调用——配置缺失则拒绝起服务(免裸奔)。
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from neckline.config import settings


def require_api_token_ready() -> None:
    """启动 fail-fast:`API_TOKEN` 必须存在且 `len>=16`,否则抛 RuntimeError(拒绝起服务)。"""
    tok = settings.api_token
    if not tok or len(tok.strip()) < 16:
        raise RuntimeError(
            "API_TOKEN 缺失或过短(需 len>=16)。请在 .env 配置 API_TOKEN 后再起服务。"
        )


def require_token(authorization: str = Header(default="")) -> None:
    """Bearer token 鉴权依赖。缺/错 token → 401。

    比对用 `hmac.compare_digest`(恒定时间)。服务端未配 `API_TOKEN` → 也判 401
    (不泄漏配置状态,且 startup 已 fail-fast,正常不会走到)。
    """
    expected = (settings.api_token or "").strip()
    prefix = "Bearer "
    presented = ""
    if authorization.startswith(prefix):
        presented = authorization[len(prefix):].strip()

    if not expected or not presented or not hmac.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未授权:缺失或错误的 Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


__all__ = ["require_api_token_ready", "require_token"]
