"""只读 MCP。工具只有简要提示词、晨报、原文、股票池查询、单票概览。"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from neckline.market.overview import overview
from neckline.market.pool_query import lookup
from neckline.mcp.auth import SCOPE, OwnerAuth
from neckline.mcp.brief import BRIEF
from neckline.mcp.settings import McpSettings, load_mcp_settings
from neckline.morning import db
from neckline.morning.api import document_out, report_out, v4_db_path

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
_LOG = logging.getLogger("neckline.mcp")


@dataclass
class McpBundle:
    server: MCPServer
    asgi: Any
    resource: str
    limiter: Any


def _report(trade_date: str | None) -> dict:
    path = v4_db_path()
    if path is None:
        return {"status": "unavailable", "message": "晨报库还没配置。"}
    selected = trade_date.strip() if trade_date else ""
    if selected and not _DATE.fullmatch(selected):
        return {"status": "invalid_date", "message": "日期格式应为 YYYY-MM-DD。"}
    try:
        with db.read(path) as conn:
            report = db.get_report(conn, selected) if selected else db.latest_report(conn)
    except db.NotReady:
        return {"status": "unavailable", "message": "晨报库还没准备好。"}
    if report is None:
        return {"status": "no_report", "message": "这一天没有晨报。" if selected else "还没有晨报。"}
    return report_out(report)


def _document(document_id: str) -> dict:
    path = v4_db_path()
    if path is None:
        return {"status": "unavailable", "message": "晨报库还没配置。"}
    try:
        with db.read(path) as conn:
            document = db.get_document(conn, document_id)
    except db.NotReady:
        return {"status": "unavailable", "message": "晨报库还没准备好。"}
    if document is None:
        return {"status": "no_document", "message": "没有这篇原文。"}
    return document_out(document)


def _build(settings: McpSettings, limiter) -> McpBundle:
    auth = OwnerAuth(settings)
    server = MCPServer(
        name="neckline",
        title="Neckline",
        version="4.0.0",
        instructions=BRIEF,
        auth_server_provider=auth,
        auth=AuthSettings(
            issuer_url=settings.public_base,
            resource_server_url=settings.resource,
            validate_token_resource=True,
            required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
            revocation_options=RevocationOptions(enabled=True),
        ),
        log_level="WARNING",
    )

    @server.tool(name="read_brief", title="读取简要提示词", annotations=_READ)
    async def read_brief() -> dict[str, Any]:
        """返回 K10 讨论用的简要提示词。对话开始时先读。只读。"""
        return {"brief": BRIEF}

    @server.tool(name="get_morning_report", title="读取晨报", annotations=_READ)
    async def get_morning_report(trade_date: str | None = None) -> dict[str, Any]:
        """读取最新或指定交易日的晨报，和推送、App 是同一份。trade_date 形如 2026-10-16，不填则读最新一期。"""
        return _report(trade_date)

    @server.tool(name="get_document", title="读取原文", annotations=_READ)
    async def get_document(document_id: str) -> dict[str, Any]:
        """按文档 id 读取晨报原文全文和原链接。可传 doc_<32位十六进制> 取最新版本，或 doc_<32位十六进制>@<正整数> 取指定版本。"""
        return _document(document_id)

    @server.tool(name="check_pool", title="查询股票池", annotations=_READ)
    async def check_pool(query: str) -> dict[str, Any]:
        """查询一只股票是否在 K10 股票池。可以是六位代码、带 .SZ 的代码、简称或别名。不返回整份名单。"""
        return lookup(query)

    @server.tool(name="get_stock_overview", title="读取单票技术面概览", annotations=_READ)
    async def get_stock_overview(stock: str) -> dict[str, Any]:
        """池内单票的晨报条目、TuShare 收盘数据和麦蕊盘中数据。池外只说明不在 K10 股票池。"""
        try:
            return await overview(stock)
        except Exception:
            _LOG.warning("overview failed")
            return {"status": "unavailable", "message": "技术面概览没有取到。"}

    @server.custom_route("/login", methods=["GET", "POST"], include_in_schema=False)
    async def login(request):
        return await auth.login(request)

    host = urlsplit(settings.public_base).netloc
    asgi = server.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        max_request_body_size=65536,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[host, "127.0.0.1:*", "testserver"],
            allowed_origins=[settings.origin, "https://chatgpt.com", "https://claude.ai", "https://claude.com", "https://grok.com"],
        ),
    )
    return McpBundle(server=server, asgi=asgi, resource=settings.resource, limiter=limiter)


def build_mcp_bundle() -> McpBundle | None:
    from neckline.mcp.gateway import LoginLimiter

    settings = load_mcp_settings()
    if settings is None:
        return None
    try:
        return _build(settings, LoginLimiter())
    except OSError:
        _LOG.warning("MCP 鉴权库打不开")
        return None
