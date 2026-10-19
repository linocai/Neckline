"""把 /mcp 和 OAuth 路径转给已配置的 MCP；没配好时返回 503，不返回 500。"""
from __future__ import annotations

from collections import OrderedDict, deque
import time
from urllib.parse import parse_qs
import re

from starlette.responses import JSONResponse

MAX_BODY = 65536
_VERIFIER = re.compile(r"[A-Za-z0-9._~-]{43,128}")
_SAFE = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
MCP_ROUTES = [
    ("/mcp", ["GET", "POST", "DELETE", "OPTIONS"]),
    ("/authorize", ["GET", "POST"]),
    ("/token", ["POST", "OPTIONS"]),
    ("/register", ["POST", "OPTIONS"]),
    ("/revoke", ["POST", "OPTIONS"]),
    ("/login", ["GET", "POST"]),
    ("/.well-known/oauth-authorization-server", ["GET", "OPTIONS"]),
    ("/.well-known/oauth-protected-resource/mcp", ["GET", "OPTIONS"]),
]


def _json(payload: dict, status: int, extra: dict | None = None) -> JSONResponse:
    headers = dict(_SAFE)
    if extra:
        headers.update(extra)
    return JSONResponse(payload, status_code=status, headers=headers)


class LoginLimiter:
    """登录和动态注册按来源 IP 限速。限额用完直接拒绝，不进口令校验。"""

    def __init__(self) -> None:
        self.requests: OrderedDict = OrderedDict()

    def reject(self, scope):
        path, method = scope.get("path"), scope.get("method")
        if path == "/login" and method == "POST":
            kind, period, limit = "login", 300, 10
        elif path == "/register" and method == "POST":
            kind, period, limit = "registration", 60, 10
        else:
            return None
        ip = (scope.get("client") or ("unknown", 0))[0]
        key = (ip, kind)
        queue = self.requests.setdefault(key, deque())
        now = time.monotonic()
        while queue and now - queue[0] > period:
            queue.popleft()
        if len(queue) >= limit:
            return _json({"error": "rate_limited"}, 429, {"Retry-After": str(period)})
        queue.append(now)
        self.requests.move_to_end(key)
        while len(self.requests) > 2000:
            self.requests.popitem(last=False)
        return None


def _token_guard(body: bytes, resource: str):
    form = parse_qs(body.decode("utf-8", errors="replace"))
    # 带了 resource 就必须是本服务；没带也放行：授权码和刷新令牌在签发时已绑定本服务，
    # 换令牌时 OwnerAuth 还会再核对一次。避免客户端续期时不带 resource 导致连接过一小时就断。
    if "resource" in form and form["resource"] != [resource]:
        return _json({"error": "invalid_target"}, 400)
    verifier = form.get("code_verifier", [""])[0]
    if form.get("grant_type") == ["authorization_code"] and not _VERIFIER.fullmatch(verifier):
        return _json({"error": "invalid_grant"}, 400)
    return None


async def _read_body(receive):
    body = b""
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None, "disconnect"
        body += message.get("body", b"")
        if len(body) > MAX_BODY:
            return None, "too_large"
        if not message.get("more_body"):
            return body, None


def _replay(body: bytes, receive):
    sent = False

    async def replay():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


def _secure_send(send, path: str | None = None):
    # 登录页用 same-origin：no-referrer 会让浏览器把表单 POST 的 Origin 报成 null，
    # 登录的 Origin 校验就永远过不了（10/19 Claude 实测 403）。same-origin 对外站仍不带来源。
    policy = b"same-origin" if path == "/login" else b"no-referrer"
    # Chrome 把 form-action 也用在表单提交后的重定向上：登录成功后的 303 跳回客户端回调，
    # 只写 'self' 会被拦下（10/19 Claude 实测：登录 303 后停在原页，再点一次就 403）。
    # 登录页只多放行 auth.allowed_redirect 认可的三个回调域名。
    targets = b" https://chatgpt.com https://claude.ai https://claude.com" if path == "/login" else b""
    csp = (b"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'" + targets
           + b"; frame-ancestors 'none'; base-uri 'none'")

    async def wrapped(message):
        if message["type"] == "http.response.start":
            message = dict(message)
            headers = list(message.get("headers", []))
            headers.extend([
                (b"cache-control", b"no-store"),
                (b"referrer-policy", policy),
                (b"x-content-type-options", b"nosniff"),
                (b"content-security-policy", csp),
            ])
            message["headers"] = headers
        await send(message)

    return wrapped


class McpGateway:
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await _json({"error": "mcp_unavailable", "message": "MCP 未就绪"}, 503)(scope, receive, send)
            return
        body, error = await _read_body(receive)
        if error == "disconnect":
            return
        if error == "too_large":
            await _json({"error": "request_too_large"}, 413)(scope, receive, send)
            return
        state = getattr(scope.get("app"), "state", None)
        bundle = getattr(state, "mcp_bundle", None)
        if bundle is None:
            await _json({"error": "mcp_unavailable", "message": "MCP 未就绪"}, 503)(scope, receive, send)
            return
        limited = bundle.limiter.reject(scope)
        if limited is not None:
            await limited(scope, receive, send)
            return
        if scope.get("path") == "/token" and scope.get("method") == "POST":
            rejected = _token_guard(body or b"", bundle.resource)
            if rejected is not None:
                await rejected(scope, receive, send)
                return
        await bundle.asgi(scope, _replay(body or b"", receive), _secure_send(send, scope.get("path")))


gateway = McpGateway()
