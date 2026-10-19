"""单用户连接口令。动态注册只放行 ChatGPT、Claude 和 Grok 的回调。"""
from __future__ import annotations

import hashlib
import hmac
import logging
from html import escape
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError, RefreshToken, RegistrationError, TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from neckline.mcp.settings import McpSettings
from neckline.mcp.state import Store

SCOPE = "neckline:read"
_LOG = logging.getLogger("neckline.mcp")
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")
_CONNECTOR = re.compile(r"/connector/oauth/[A-Za-z0-9_-]+")


def password_digest(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32).hex()


def _same(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left.encode(), right.encode())


# Grok 连接器的回调：xAI 没有公开文档，按多家 MCP 服务实测报告只放行这两个精确地址
# （现用的带结尾斜杠；旧路径一并放行）。grok.com 的其他路径、子域名、端口仍拒绝。
_GROK_CALLBACKS = {"/connectors-oauth-exchange-code/", "/connectors/oauth/callback"}


def allowed_redirect(value) -> bool:
    parts = urlsplit(str(value))
    if parts.scheme != "https" or parts.query or parts.fragment or parts.username:
        return False
    if parts.netloc == "chatgpt.com" and (parts.path == "/connector_platform_oauth_redirect" or _CONNECTOR.fullmatch(parts.path)):
        return True
    if parts.netloc == "grok.com" and parts.path in _GROK_CALLBACKS:
        return True
    return parts.netloc in {"claude.ai", "claude.com"} and parts.path == "/api/mcp/auth_callback"


class OwnerAuth:
    def __init__(self, settings: McpSettings) -> None:
        self.settings = settings
        self.store = Store(settings.auth_db)

    async def get_client(self, client_id):
        data = self.store.get("client", client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info):
        if not client_info.redirect_uris or not all(allowed_redirect(item) for item in client_info.redirect_uris):
            # 回调地址不是秘密：记下被拒的地址，新客户端接入时不用猜它的回调。
            _LOG.warning("registration rejected, redirect_uris=%s", [str(item)[:200] for item in client_info.redirect_uris or []])
            raise RegistrationError("invalid_redirect_uri", "Only ChatGPT, Claude and Grok OAuth callback URLs are allowed.")
        if set(client_info.grant_types) - {"authorization_code", "refresh_token"}:
            raise RegistrationError("invalid_client_metadata", "Unsupported grant type.")
        if self.store.count("client") >= 100:
            raise RegistrationError("invalid_client_metadata", "Registration limit reached.")
        self.store.put("client", client_info.client_id, client_info.model_dump(mode="json"))

    async def authorize(self, client, params: AuthorizationParams):
        if params.resource != self.settings.resource:
            raise AuthorizeError("invalid_target", "The requested resource does not match this service.")
        if params.scopes != [SCOPE] or not allowed_redirect(params.redirect_uri):
            raise AuthorizeError("invalid_scope", "Only neckline:read may be authorized.")
        if not _CHALLENGE.fullmatch(params.code_challenge):
            raise AuthorizeError("invalid_request", "An S256 PKCE challenge is required.")
        if self.store.count("pending") >= 100:
            raise AuthorizeError("temporarily_unavailable", "Too many pending authorizations.")
        pending = secrets.token_urlsafe(32)
        self.store.put("pending", pending, {
            "client_id": client.client_id,
            "params": params.model_dump(mode="json"),
            "csrf": secrets.token_urlsafe(32),
        }, time.time() + 600)
        return self.settings.public_base + "/login?" + urlencode({"request": pending})

    async def login(self, request: Request):
        if request.method == "GET":
            pending = request.query_params.get("request", "")
            item = self.store.get("pending", pending)
            if not item:
                return HTMLResponse("连接请求已过期，请回到对话客户端重新连接。", status_code=400)
            html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>连接 Neckline</title>
<style>body{{font:17px system-ui;max-width:420px;margin:12vh auto;padding:24px;color:#182438;background:#f6f8fc}}main{{background:white;padding:28px;border-radius:16px}}input,button{{box-sizing:border-box;width:100%;padding:14px;margin-top:14px;font:inherit;border:1px solid #ccd3df;border-radius:8px}}button{{background:#9a3412;color:white;border:0}}p{{line-height:1.65;color:#526071}}</style>
<main><h1>连接 Neckline</h1><p>允许这个对话客户端读取晨报和 K10 股票数据。这里只提供数据，不执行交易。</p>
<form method="post" action="/login"><input type="hidden" name="request" value="{escape(pending, quote=True)}"><input type="hidden" name="csrf" value="{escape(item['csrf'], quote=True)}">
<label for="password">输入你的 Neckline 连接口令</label><input id="password" type="password" name="password" required maxlength="128" autocomplete="current-password"><button type="submit">授权连接</button></form>
<p>这里使用部署时生成的连接口令，不用填写行情证书。</p></main></html>'''
            response = HTMLResponse(html)
            response.set_cookie("__Secure-nl-login", item["csrf"], max_age=600, secure=True, httponly=True, samesite="lax", path="/login")
            return response
        form = await request.form()
        pending = str(form.get("request", ""))
        item = self.store.get("pending", pending)
        csrf = str(form.get("csrf", ""))
        if not item:
            # 已用过（授权成功后再点一次）或已过期：如实说明，不和校验失败混在一起。
            return HTMLResponse("这次连接请求已经用过或已过期。授权成功的话回到对话客户端即可；否则请在客户端重新连接。",
                                status_code=400)
        if (request.headers.get("origin") != self.settings.origin or not _same(csrf, item["csrf"])
                or not _same(request.cookies.get("__Secure-nl-login", ""), item["csrf"])):
            return HTMLResponse("连接校验失败，请回到对话客户端重试。", status_code=403)
        password = str(form.get("password", ""))
        digest = password_digest(password, self.settings.password_salt) if 1 <= len(password) <= 128 else ""
        if not digest or not _same(digest, self.settings.password_hash):
            return HTMLResponse("连接口令不正确，请返回重试。", status_code=403)
        item = self.store.get("pending", pending, consume=True)
        if not item:
            return HTMLResponse("连接请求已被使用。", status_code=400)
        params = AuthorizationParams.model_validate(item["params"])
        code = secrets.token_urlsafe(32)
        record = AuthorizationCode(
            code="", client_id=item["client_id"], scopes=params.scopes or [SCOPE], expires_at=time.time() + 120,
            code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly, resource=params.resource,
            subject="owner",
        )
        self.store.put("code", code, record.model_dump(mode="json"), record.expires_at)
        query = {"code": code}
        if params.state is not None:
            query["state"] = params.state
        response = RedirectResponse(str(params.redirect_uri) + "?" + urlencode(query), status_code=303)
        response.delete_cookie("__Secure-nl-login", path="/login", secure=True, httponly=True, samesite="lax")
        return response

    async def load_authorization_code(self, client, authorization_code):
        item = self.store.get("code", authorization_code)
        if not item or item["client_id"] != client.client_id:
            return None
        return AuthorizationCode.model_validate({**item, "code": authorization_code})

    def issue(self, client_id, scopes, resource, family=None):
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        family = family or secrets.token_urlsafe(24)
        now = int(time.time())
        common = {"client_id": client_id, "scopes": scopes, "resource": resource, "subject": "owner", "family": family, "token": ""}
        self.store.put("access", access, {**common, "expires_at": now + 3600, "claims": {"iss": self.settings.public_base}}, now + 3600)
        self.store.put("refresh", refresh, {**common, "expires_at": now + 30 * 86400}, now + 30 * 86400)
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=3600, refresh_token=refresh, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, authorization_code):
        item = self.store.get("code", authorization_code.code, consume=True)
        if not item or item["client_id"] != client.client_id or item["resource"] != self.settings.resource:
            raise TokenError("invalid_grant", "Authorization code is invalid or has already been used.")
        return self.issue(client.client_id, item["scopes"], item["resource"])

    async def load_refresh_token(self, client, refresh_token):
        item = self.store.get("refresh", refresh_token)
        if not item or item["client_id"] != client.client_id:
            return None
        return RefreshToken.model_validate({**item, "token": refresh_token})

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        item = self.store.get("refresh", refresh_token.token, consume=True)
        if not item or item["client_id"] != client.client_id or item["resource"] != self.settings.resource or not set(scopes).issubset(item["scopes"]):
            raise TokenError("invalid_grant", "Refresh token is invalid or has already been used.")
        self.store.revoke_family(item["family"])
        return self.issue(client.client_id, scopes, item["resource"], item["family"])

    async def load_access_token(self, token):
        item = self.store.get("access", token)
        if not item or item["resource"] != self.settings.resource:
            return None
        return AccessToken.model_validate({**item, "token": token})

    async def revoke_token(self, token):
        kind = "refresh" if isinstance(token, RefreshToken) else "access"
        item = self.store.get(kind, token.token)
        if item:
            self.store.revoke_family(item["family"])

    async def exchange_identity_assertion(self, client, params):
        raise TokenError("unsupported_grant_type", "Unsupported grant type.")
