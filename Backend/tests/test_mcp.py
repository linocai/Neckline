"""只读 MCP、OAuth 和只读网页单票页。假行情，不联网。"""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest
from starlette.testclient import TestClient

from neckline.api import deps
from neckline.api.app import app
from neckline.market.mairui import Mairui
from neckline.market.pool_query import _load_pool
from neckline.market.services import Services, use
from neckline.market.tushare_daily import TuShareDaily
from neckline.market.util import SHANGHAI
from neckline.mcp.auth import password_digest
from neckline.mcp.brief import BRIEF
from neckline.morning import db
from neckline.morning.web import HideReadKey
from neckline.news.model import SourceDocument

BASE = "https://nk.linotsai.top"
RESOURCE = BASE + "/mcp"
CHATGPT = "https://chatgpt.com/connector_platform_oauth_redirect"
CLAUDE_AI = "https://claude.ai/api/mcp/auth_callback"
CLAUDE_COM = "https://claude.com/api/mcp/auth_callback"
PASSWORD = "test-only-password"
SALT = "ab" * 16
TOKEN = "test-tushare-token"
LICENCE = "test-licence"
API_TOKEN = "a" * 16
READ_KEY = "c" * 24
VERIFIER = "v" * 64
NOW = datetime(2026, 10, 16, 10, 6, tzinfo=SHANGHAI)
TOOLS = {"read_brief", "get_morning_report", "get_document", "check_pool", "get_stock_overview"}


def _frame(fields, rows):
    return {"code": 0, "data": {"fields": fields, "items": rows, "has_more": False}}


def _handler(request: httpx.Request) -> httpx.Response:
    if "tushare" in request.url.host:
        body = json.loads(request.content)
        if body["api_name"] == "daily":
            fields = ["ts_code", "trade_date", "close", "pre_close", "pct_chg", "high", "low", "amount"]
            return httpx.Response(200, json=_frame(fields, [["300002.SZ", "20260120", 11, 10, 10, 12, 10, 200000]]))
        if body["api_name"] == "daily_basic":
            fields = ["ts_code", "trade_date", "turnover_rate", "pe_ttm", "pb", "total_mv", "circ_mv"]
            return httpx.Response(200, json=_frame(fields, [["300002.SZ", "20260120", 3.5, 20.5, 2.5, 500000, 250000]]))
        fields = ["ts_code", "trade_date", "net_mf_amount"]
        return httpx.Response(200, json=_frame(fields, [["300002.SZ", "20260120", 3000]]))
    assert LICENCE in request.url.path
    if "/real/" in request.url.path:
        return httpx.Response(200, json={"t": "2026-10-16 10:05:00", "p": 10.5, "yc": 10, "pc": 5, "cje": 150000000, "tr": 2.5})
    return httpx.Response(200, json=[{"t": "2026-10-16 10:00:00", "o": 10, "h": 11, "l": 9, "c": 10.5, "a": 10000}])


def _seed(path):
    db.init_schema(path)
    fetched = datetime(2026, 10, 15, 8, 1, tzinfo=timezone.utc)
    document = SourceDocument(
        source_key="jin10-flash", external_id="ext-mcp", url="https://example.test/news",
        title="订单标题", original_text="公告原文写了金额", excerpt=None,
        published_at=datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc), published_precision="exact",
        fetched_at=fetched,
    )
    doc_id = db.document_id(document.source_key, document.external_id)
    content = {
        "reportId": "morning-2026-10-16", "tradeDate": "2026-10-16", "kind": "morning",
        "window": {"start": "2026-10-15T15:00+08:00", "end": "2026-10-16T08:00+08:00"},
        "generatedAt": "2026-10-16T08:02+08:00", "configVersion": "screening-v1", "gapSummary": None,
        "entries": [{"rank": 1, "companyCode": "300002", "companyName": "神州泰岳", "type": "订单",
                     "importance": "高", "headline": "订单落地", "reason": "公告写了金额",
                     "sources": [{"documentId": doc_id, "sourceLabel": "金十快讯",
                                  "publishedAt": "2026-10-15T16:00+08:00", "title": "订单标题",
                                  "url": "https://example.test/news"}],
                     "others": []}],
        "review": {"types": [], "relation": [], "fresh": [], "old": []},
    }
    with db.write(path) as conn:
        db.store_documents(conn, [document])
        conn.execute(
            "INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("morning-2026-10-16", "2026-10-16", "2026-10-15T07:00:00+00:00", "2026-10-16T00:00:00+00:00",
             "available", "screening-v1", "0" * 64, "2026-10-16T00:00:00+00:00", "2026-10-16T00:02:00+00:00",
             None, None, None, "{}", json.dumps(content, ensure_ascii=False)))
    return doc_id


@pytest.fixture
def browser(tmp_path, monkeypatch):
    path = tmp_path / "v4.db"
    doc_id = _seed(path)
    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    services = Services(
        pool=_load_pool(),
        tushare=TuShareDaily(TOKEN, client=client, now=lambda: NOW),
        mairui=Mairui(LICENCE, client=client, now=lambda: NOW),
        now=lambda: NOW,
    )
    use(services)
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    monkeypatch.setenv("NECKLINE_READ_KEY", READ_KEY)
    monkeypatch.setenv("NECKLINE_PUBLIC_BASE", BASE)
    monkeypatch.setenv("NECKLINE_MCP_PASSWORD_SALT", SALT)
    monkeypatch.setenv("NECKLINE_MCP_PASSWORD_HASH", password_digest(PASSWORD, SALT))
    monkeypatch.setenv("NECKLINE_MCP_AUTH_DB", str(tmp_path / "mcp-auth.db"))
    monkeypatch.setenv("TUSHARE_TOKEN", TOKEN)
    monkeypatch.setenv("MAIRUI_LICENCE", LICENCE)
    monkeypatch.setattr(deps, "settings", dataclasses.replace(deps.settings, api_token=API_TOKEN))
    with TestClient(app, base_url=BASE, follow_redirects=False) as test_client:
        yield test_client, doc_id
    asyncio.run(services.aclose())


def _register(test_client, redirect=CHATGPT):
    return test_client.post("/register", json={
        "client_name": "Neckline test client", "redirect_uris": [redirect],
        "token_endpoint_auth_method": "client_secret_post",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "scope": "neckline:read",
    })


def _start(test_client, client, redirect=CHATGPT):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
    response = test_client.get("/authorize", params={
        "response_type": "code", "client_id": client["client_id"], "redirect_uri": redirect,
        "scope": "neckline:read", "state": "roundtrip-state", "code_challenge": challenge,
        "code_challenge_method": "S256", "resource": RESOURCE,
    })
    assert response.status_code in (302, 303, 307), response.text
    form = test_client.get(response.headers["location"])
    assert form.status_code == 200, form.text
    assert "连接 Neckline" in form.text and "ChatGPT" not in form.text
    # 浏览器在 no-referrer 下会把表单 POST 的 Origin 报成 null，登录页必须用 same-origin。
    assert form.headers["referrer-policy"] == "same-origin"
    assert response.headers["referrer-policy"] == "no-referrer"
    # 登录成功后的 303 及客户端回调后续的跳转都不能被 form-action 拦下：登录页不设 form-action，其他路由仍只有 'self'。
    assert "form-action" not in form.headers["content-security-policy"]
    assert "default-src 'none'" in form.headers["content-security-policy"]
    assert "form-action 'self';" in response.headers["content-security-policy"]
    return dict(re.findall(r'name="(request|csrf)" value="([^"]+)"', form.text))


def _login(test_client, client, redirect=CHATGPT):
    fields = _start(test_client, client, redirect)
    response = test_client.post("/login", data={**fields, "password": PASSWORD}, headers={"Origin": BASE})
    assert response.status_code == 303, response.text
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["state"] == ["roundtrip-state"]
    assert urlsplit(response.headers["location"]).netloc in {"chatgpt.com", "claude.ai", "claude.com"}
    return query["code"][0]


def _exchange(test_client, client, code, **overrides):
    payload = {"grant_type": "authorization_code", "client_id": client["client_id"],
               "client_secret": client["client_secret"], "redirect_uri": CHATGPT, "code": code,
               "code_verifier": VERIFIER, "resource": RESOURCE}
    payload.update(overrides)
    return test_client.post("/token", data=payload)


def _rpc(test_client, headers, method, params=None, identifier=1):
    body = {"jsonrpc": "2.0", "id": identifier, "method": method}
    if params is not None:
        body["params"] = params
    return test_client.post("/mcp", headers=headers, json=body)


def _payload(response):
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result.get("isError") is not True, result
    if "structuredContent" in result:
        return result["structuredContent"]
    return json.loads(result["content"][0]["text"])


def test_mcp_is_unavailable_until_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "settings", dataclasses.replace(deps.settings, api_token=API_TOKEN))
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(tmp_path / "missing-not-created.db"))
    with TestClient(app) as test_client:
        for path in ("/mcp", "/login", "/register", "/authorize", "/token",
                     "/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource/mcp"):
            response = test_client.post(path) if path in {"/mcp", "/register", "/token"} else test_client.get(path)
            assert response.status_code == 503, (path, response.status_code, response.text)
            assert response.json()["error"] == "mcp_unavailable"
        health = test_client.get("/api/v4/health").json()
        assert health["mcp"] is False and health["tushare"] is False and health["mairui"] is False
        assert health["releaseSet"]


@pytest.mark.parametrize("url", [
    "https://evil.example/redirect",
    "https://chatgpt.com.evil.example/connector_platform_oauth_redirect",
    "https://chatgpt.com/connector_platform_oauth_redirect?next=evil",
    "http://chatgpt.com/connector_platform_oauth_redirect",
    "https://claude.ai.evil.example/api/mcp/auth_callback",
    "https://claude.ai/api/mcp/auth_callback?next=evil",
])
def test_registration_rejects_callbacks_outside_the_allowlist(browser, url):
    test_client, _doc = browser
    assert _register(test_client, url).status_code == 400


@pytest.mark.parametrize("url", [
    CHATGPT,
    "https://chatgpt.com/connector/oauth/abc_DEF-1",
    CLAUDE_AI,
    CLAUDE_COM,
])
def test_chatgpt_and_claude_callbacks_can_register(browser, url):
    test_client, _doc = browser
    response = _register(test_client, url)
    assert response.status_code == 201, response.text
    assert PASSWORD not in response.text and SALT not in response.text


def test_wrong_password_is_rejected(browser):
    test_client, _doc = browser
    client = _register(test_client).json()
    fields = _start(test_client, client)
    assert test_client.post("/login", data={**fields, "password": "wrong-password"}, headers={"Origin": BASE}).status_code == 403
    assert test_client.post("/login", data={**fields, "password": PASSWORD}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert test_client.post("/login", data={**fields, "csrf": "wrong", "password": PASSWORD}, headers={"Origin": BASE}).status_code == 403


def test_oauth_pkce_and_mcp_tools_round_trip(browser, caplog):
    caplog.set_level(logging.DEBUG)
    test_client, doc_id = browser
    denied = test_client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert denied.status_code == 401
    assert 'resource_metadata="https://nk.linotsai.top/.well-known/oauth-protected-resource/mcp"' in denied.headers["www-authenticate"]
    metadata = test_client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert metadata["resource"] == RESOURCE
    assert metadata["authorization_servers"] == [BASE]
    issuer = test_client.get("/.well-known/oauth-authorization-server").json()
    assert issuer["issuer"] == BASE
    assert issuer["token_endpoint"] == BASE + "/token"
    assert issuer["code_challenge_methods_supported"] == ["S256"]
    health = test_client.get("/api/v4/health").json()
    assert health["mcp"] is True and health["tushare"] is True and health["mairui"] is True
    assert TOKEN not in json.dumps(health) and LICENCE not in json.dumps(health)

    client = _register(test_client).json()
    code = _login(test_client, client)
    assert _exchange(test_client, client, code, code_verifier="x" * 64).status_code == 400
    assert _exchange(test_client, client, code, resource="https://evil.example/mcp").status_code == 400
    traded = _exchange(test_client, client, code)
    assert traded.status_code == 200, traded.text
    tokens = traded.json()
    assert _exchange(test_client, client, code).status_code == 400
    headers = {"Authorization": "Bearer " + tokens["access_token"],
               "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    assert test_client.post("/mcp", headers={"Authorization": f"Bearer {API_TOKEN}", "Accept": "application/json"},
                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401
    assert test_client.get("/api/v4/reports/latest", headers=headers).status_code == 401

    init = _rpc(test_client, headers, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                                    "clientInfo": {"name": "test", "version": "1"}})
    assert init.status_code == 200, init.text
    assert init.json()["result"]["instructions"] == BRIEF
    listing = _rpc(test_client, headers, "tools/list", identifier=2)
    tools = listing.json()["result"]["tools"]
    assert {item["name"] for item in tools} == TOOLS
    for item in tools:
        hints = item["annotations"]
        assert hints.get("readOnlyHint", hints.get("read_only_hint")) is True
        assert hints.get("destructiveHint", hints.get("destructive_hint")) is False
    titles = {item["name"]: item.get("title") for item in tools}
    assert titles == {"read_brief": "读取简要提示词", "get_morning_report": "读取晨报", "get_document": "读取原文",
                      "check_pool": "查询股票池", "get_stock_overview": "读取单票技术面概览"}

    assert _payload(_rpc(test_client, headers, "tools/call", {"name": "read_brief", "arguments": {}}, 3))["brief"] == BRIEF
    report = _payload(_rpc(test_client, headers, "tools/call", {"name": "get_morning_report", "arguments": {}}, 4))
    assert report["tradeDate"] == "2026-10-16"
    assert report["content"]["entries"][0]["headline"] == "订单落地"
    document = _payload(_rpc(test_client, headers, "tools/call",
                             {"name": "get_document", "arguments": {"document_id": doc_id}}, 5))
    assert document["text"] == "公告原文写了金额" and document["url"] == "https://example.test/news"
    assert _payload(_rpc(test_client, headers, "tools/call", {"name": "check_pool", "arguments": {"query": "000001"}}, 6)) == {
        "inPool": False, "message": "不在 K10 股票池"}
    overview = _payload(_rpc(test_client, headers, "tools/call",
                             {"name": "get_stock_overview", "arguments": {"stock": "神州泰岳"}}, 7))
    assert overview["code"] == "300002.SZ" and overview["morningReport"]["reason"] == "公告写了金额"
    assert overview["daily"]["close"] == 11 and overview["daily"]["moneyflow"]["basis"] == "TuShare moneyflow 净流入额"
    assert overview["intraday"]["quote"]["turnoverYi"] == 1.5
    assert overview["intraday"]["series"]["note"] == "由一分钟数据聚合"

    page = test_client.get(f"/read/{READ_KEY}")
    assert page.status_code == 200 and "简要提示词" in page.text and "<form" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["x-robots-tag"] == "noindex, nofollow"
    assert page.headers["referrer-policy"] == "no-referrer"
    latest = test_client.get(f"/read/{READ_KEY}?format=json").json()
    assert latest["brief"] == BRIEF and latest["content"]["entries"][0]["headline"] == "订单落地"
    assert READ_KEY not in json.dumps(latest, ensure_ascii=False)
    missing = test_client.get(f"/read/{READ_KEY}/stock/平安银行")
    assert missing.status_code == 200 and "不在 K10 股票池" in missing.text
    stock = test_client.get(f"/read/{READ_KEY}/stock/神州泰岳")
    assert stock.status_code == 200 and "TuShare moneyflow 净流入额" in stock.text and "10:05" in stock.text
    stock_json = test_client.get(f"/read/{READ_KEY}/stock/300002?format=json").json()
    assert stock_json["brief"] == BRIEF and stock_json["daily"]["turnoverYi"] == 2
    jumped = test_client.get(f"/read/{READ_KEY}/stock", params={"q": "神州泰岳"})
    assert jumped.status_code == 303 and unquote(jumped.headers["location"]).endswith("/stock/神州泰岳")
    assert test_client.get(f"/read/{'d' * 24}/stock/300002").status_code == 404
    assert test_client.get("/read/short/stock/300002").status_code == 404

    blob = "\n".join(item.text for item in (denied, init, listing, page, missing, stock, traded)) + caplog.text
    for secret in (TOKEN, LICENCE, PASSWORD, SALT, password_digest(PASSWORD, SALT)):
        assert secret not in blob
    raw = _auth_db().read_bytes()
    assert tokens["access_token"].encode() not in raw
    assert tokens["refresh_token"].encode() not in raw
    assert PASSWORD.encode() not in raw and LICENCE.encode() not in raw and TOKEN.encode() not in raw
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:1", "GET", f"/read/{READ_KEY}/stock/300002", "1.1", 200), None)
    assert not HideReadKey().filter(record)


def _auth_db():
    import os
    from pathlib import Path
    return Path(os.environ["NECKLINE_MCP_AUTH_DB"])


def test_token_requests_may_omit_resource_but_not_send_a_wrong_one(browser):
    """授权码和刷新令牌签发时已绑定本服务：客户端换令牌、续期不带 resource 也要能用，带错的仍拒绝。"""
    test_client, _doc = browser
    client = _register(test_client).json()
    payload = {"grant_type": "authorization_code", "client_id": client["client_id"],
               "client_secret": client["client_secret"], "redirect_uri": CHATGPT,
               "code": _login(test_client, client), "code_verifier": VERIFIER}
    traded = test_client.post("/token", data=payload)
    assert traded.status_code == 200, traded.text
    refresh = {"grant_type": "refresh_token", "client_id": client["client_id"],
               "client_secret": client["client_secret"], "refresh_token": traded.json()["refresh_token"]}
    assert test_client.post("/token", data={**refresh, "resource": "https://evil.example/mcp"}).status_code == 400
    renewed = test_client.post("/token", data=refresh)
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["access_token"] != traded.json()["access_token"]


def test_request_body_and_login_are_limited(browser):
    test_client, _doc = browser
    assert test_client.post("/mcp", content=b"x" * 65537).status_code == 413
    for _ in range(10):
        reused = test_client.post("/login", data={})
        assert reused.status_code == 400 and "已经用过或已过期" in reused.text
    assert test_client.post("/login", data={}).status_code == 429
