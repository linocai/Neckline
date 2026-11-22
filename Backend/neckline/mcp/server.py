"""只读 MCP。工具涵盖 K10 晨报/股票池/单票概览，以及 Q 期货规则、启用池总览与单品种档案。"""
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

import neckline
from neckline.market.overview import overview
from neckline.market.pool_query import assess
from neckline.mcp.auth import SCOPE, OwnerAuth
from neckline.mcp.brief import BRIEF, MCP_INSTRUCTIONS
from neckline.mcp.settings import McpSettings, load_mcp_settings
from neckline.morning import db
from neckline.morning.api import document_out, report_out, v4_db_path
from neckline.qihuo import services as qihuo_services
from neckline.qihuo.board import QihuoUnavailable, load_bundle as load_qihuo_bundle
from neckline.qihuo.project import project_mcp_overview, project_mcp_product, project_mcp_rules

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


async def _load_qihuo() -> tuple[dict[str, Any] | None, bool, str | None, dict[str, Any] | None]:
    path = v4_db_path()
    if path is None:
        return None, False, None, {"status": "unavailable", "message": "数据缓存库还没配置。"}
    try:
        with db.read(path):
            pass
    except db.NotReady:
        return None, False, None, {"status": "unavailable", "message": "数据缓存库还没准备好。"}
    try:
        raw, _fetched_at, stale, stale_reason = await load_qihuo_bundle(qihuo_services.current(), db_path=path)
    except QihuoUnavailable as error:
        return None, False, None, {"status": "unavailable", "reason": error.reason, "message": error.message}
    return raw, stale, stale_reason, None


def _build(settings: McpSettings, limiter) -> McpBundle:
    auth = OwnerAuth(settings)
    server = MCPServer(
        name="neckline",
        title="Neckline",
        version=neckline.__version__,
        instructions=MCP_INSTRUCTIONS,
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
        """读取最新或指定交易日的晨报，和推送、App 是同一份。每期有个股（最多 10 只）和板块热点（最多 3 个事件）。个股每条有结论、四个问题（利好值多少、市场已反映多少、资金为什么买或不买、价格划不划算）、风险和各方观点。板块热点每条带事件、对应板块、结论、风险、各方观点和 1–3 只承接股（各有为什么和价格；按题材辨识度从名单前面选，不因找不到直接合作方就判不推）。板块线只判断这条消息会不会让资金今天来买这几只票、明天还在，不判断高低位。判「不推」的不在名单里。trade_date 形如 2026-10-16，不填则读最新一期。"""
        return _report(trade_date)

    @server.tool(name="get_document", title="读取原文", annotations=_READ)
    async def get_document(document_id: str) -> dict[str, Any]:
        """按文档 id 读取晨报原文全文和原链接。可传 doc_<32位十六进制> 取最新版本，或 doc_<32位十六进制>@<正整数> 取指定版本。"""
        return _document(document_id)

    @server.tool(name="check_pool", title="查询股票池", annotations=_READ)
    async def check_pool(query: str) -> dict[str, Any]:
        """查询一只股票是否在 K10 股票池。K10 股票池是全部在市创业板（不含 ST）。可以是六位代码、带 .SZ 的代码、简称或别名。不返回整份名单。"""
        return assess(query)

    @server.tool(name="get_stock_overview", title="读取单票技术面概览", annotations=_READ)
    async def get_stock_overview(stock: str) -> dict[str, Any]:
        """池内单票的晨报条目、TuShare 收盘数据和麦蕊盘中数据。池外只说明不在 K10 股票池。"""
        try:
            return await overview(stock)
        except Exception:
            _LOG.warning("overview failed")
            return {"status": "unavailable", "message": "技术面概览没有取到。"}

    @server.tool(name="read_qihuo_rules", title="读取 Q1 期货规则与提示词", annotations=_READ)
    async def read_qihuo_rules() -> dict[str, Any]:
        """读取国内期货（Q1）规则与对话提示词（同步自 Q1.md 第 7 章）。讨论国内期货时建议先读，作为讨论框架；涉及盘面或实时现货/资讯时，请结合你自身的网络搜索能力自由补充。只读。"""
        raw, stale, stale_reason, err = await _load_qihuo()
        if err is not None or raw is None:
            return err or {"status": "unavailable", "message": "Q 期货数据暂不可用。"}
        return project_mcp_rules(raw, stale=stale, stale_reason=stale_reason)

    @server.tool(name="get_qihuo_overview", title="读取 Q 期货启用池总览", annotations=_READ)
    async def get_qihuo_overview() -> dict[str, Any]:
        """读取当前启用的 5 个化工期货品种（烧碱 SH、塑料 L、甲醇 MA、PP pp、乙二醇 eg）总览快照，含最新收盘/结算、仓单及日变、主力前 20 名会员净持仓、参考现货/基差（乙二醇暂留空）、近 15 日利多/利空消息数及采集状态，与 App 看板同源。本工具提供快速客观基线，请结合自身搜索能力补充最新实时资讯。只读。"""
        raw, stale, stale_reason, err = await _load_qihuo()
        if err is not None or raw is None:
            return err or {"status": "unavailable", "message": "Q 期货数据暂不可用。"}
        return project_mcp_overview(raw, stale=stale, stale_reason=stale_reason)

    @server.tool(name="get_qihuo_product", title="读取单个启用品种完整档案与近15日记录", annotations=_READ)
    async def get_qihuo_product(product: str) -> dict[str, Any]:
        """按品种代码、slug 或中文名（如 SH、sh、烧碱、L、塑料、MA、甲醇、pp、PP、eg、乙二醇）读取单个启用品种的长期基本信息档案与近 15 天消息、数据、持仓明细（与 App 看板同源）。池外或已停用品种会明确提醒不在当前 5 品种启用池内。看板中未覆盖的实时现货/突发新闻可自由使用你的搜索能力补充。只读。"""
        raw, stale, stale_reason, err = await _load_qihuo()
        if err is not None or raw is None:
            return err or {"status": "unavailable", "message": "Q 期货数据暂不可用。"}
        return project_mcp_product(raw, product, stale=stale, stale_reason=stale_reason)

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
            allowed_origins=[
                settings.origin, "https://chatgpt.com", "https://claude.ai",
                "https://claude.com", "https://grok.com", "https://gemini.google.com",
            ],
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
