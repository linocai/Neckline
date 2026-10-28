"""只读网页兜底：/read/<访问码>。不用登录、不用 JavaScript，手机浏览器直接看；?format=json 给 Agent 读。

访问码是独立的 NECKLINE_READ_KEY（至少 24 位），不是 API 令牌；没配置或不对一律 404。
响应禁止索引、缓存和发送 Referer。读的是和推送、App 同一份晨报记录。
"""
from __future__ import annotations

import hmac
import logging
import os
from datetime import date
from html import escape
from pathlib import Path
from typing import Callable

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from neckline.market.overview import overview
from neckline.mcp.brief import BRIEF

from . import db
from .api import document_out, present_content, report_out, v4_db_path

_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}


class HideReadKey(logging.Filter):
    """访问码在路径里：uvicorn 的访问日志不记 /read/ 开头的请求（凭据不进日志），其他请求照常记。"""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        return not (isinstance(args, tuple) and len(args) >= 3 and str(args[2]).startswith("/read/"))


logging.getLogger("uvicorn.access").addFilter(HideReadKey())
_WEEKDAYS = "一二三四五六日"
_CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1f;--muted:#6e6e73;--line:#e3e1db;--card:#fff;--accent:#9a3412;--risk:#ff3b30;--view:#007aff}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#ecebe8;--muted:#9a9a9f;--line:#2c2c2e;--card:#1c1c1e;--accent:#fb923c;--risk:#ff453a;--view:#0a84ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}h1{font-size:22px;margin:4px 0}h2{font-size:17px;margin:24px 0 8px}
.meta{color:var(--muted);font-size:13px}.gap{margin:12px 0;padding:8px 12px;border-left:3px solid var(--accent);background:var(--card);font-size:14px}
ol{padding:0;list-style:none;margin:0}li{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
.co{font-weight:600}.tag{color:var(--muted);font-size:13px;margin-left:6px}.hl{margin:4px 0}.why{color:var(--muted);font-size:14px}
.lab{display:block;font-size:13px;color:var(--muted)}.q{font-size:14px;margin:6px 0}
.risk{color:var(--risk);margin:8px 0}
.views{color:var(--view);margin:8px 0;padding:8px 12px;border-left:3px solid var(--view)}
.views a{color:var(--view)}
.src{font-size:13px;margin-top:6px}a{color:var(--accent);text-decoration:none}details{margin:16px 0}summary{cursor:pointer;color:var(--muted)}
pre{white-space:pre-wrap;word-break:break-word;font:15px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
form{margin:12px 0}input,button{font:inherit;padding:10px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
button{background:var(--accent);color:white;border:0}table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:left;padding:4px 6px;border-bottom:1px solid var(--line);font-weight:400}
footer{margin:28px 0 8px;font-size:13px;color:var(--muted)}
"""

_QUESTION_LABELS = (
    ("value", "利好值多少"),
    ("priced", "市场已反映多少"),
    ("money", "资金为什么买或不买"),
    ("price", "价格划不划算"),
)


def _page(title: str, body: str) -> HTMLResponse:
    html = (f"<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<meta name=\"robots\" content=\"noindex,nofollow\"><meta name=\"referrer\" content=\"no-referrer\">"
            f"<title>{escape(title)}</title><style>{_CSS}</style></head><body><main>{body}</main></body></html>")
    return HTMLResponse(html, headers=_HEADERS)


def _day_label(trade_date: str) -> str:
    day = date.fromisoformat(trade_date)
    return f"{trade_date}（周{_WEEKDAYS[day.weekday()]}）"


def _source_line(base: str, source: dict) -> str:
    published = source.get("publishedAt") or ""
    shown = published[5:16].replace("T", " ") if len(published) >= 16 else published
    title = source.get("title") or source.get("sourceLabel") or "原文"
    document_id = source.get("documentId")
    if document_id:
        href = f"{base}/doc/{quote(str(document_id), safe='')}"
        link = f"<a href=\"{href}\">{escape(str(title))}</a>"
    elif source.get("url"):
        link = f"<a href=\"{escape(str(source['url']))}\">{escape(str(title))}</a>"
    else:
        link = escape(str(title))
    return f"<div>{escape(str(source.get('sourceLabel') or ''))} {escape(shown)} {link}</div>"


def _view_html(view: dict) -> str:
    by = escape(str(view.get("by") or ""))
    name = escape(str(view.get("name") or ""))
    text = escape(str(view.get("text") or ""))
    title = view.get("sourceTitle")
    url = view.get("sourceUrl")
    if url:
        source = f"（<a href=\"{escape(str(url))}\">{escape(str(title or '来源'))}</a>）"
    elif title:
        source = f"（{escape(str(title))}）"
    else:
        source = ""
    return f"<p><b>{by}</b> · {name}：{text}{source}</p>"


def _evaluated_entries(base: str, entries: list[dict]) -> str:
    items = []
    for entry in entries:
        disclosed = f"｜首次公开 {escape(str(entry['firstDisclosed']))}" if entry.get("firstDisclosed") else ""
        questions = entry.get("questions") if isinstance(entry.get("questions"), dict) else {}
        question_html = "".join(
            f"<div class=\"q\"><span class=\"lab\">{escape(label)}</span>{escape(str(questions.get(key) or ''))}</div>"
            for key, label in _QUESTION_LABELS)
        views = entry.get("views") if isinstance(entry.get("views"), list) else []
        view_html = ("<div class=\"views\"><div>各方观点</div>" + "".join(_view_html(view) for view in views if isinstance(view, dict))
                     + "</div>") if views else ""
        sources = "".join(_source_line(base, source) for source in entry.get("sources") or [] if isinstance(source, dict))
        items.append(
            f"<li><div><span class=\"co\">{escape(str(entry.get('companyName') or ''))} {escape(str(entry.get('companyCode') or ''))}</span>"
            f"<span class=\"tag\">{escape(str(entry.get('type') or ''))}{disclosed}</span></div>"
            f"<div class=\"hl\">{escape(str(entry.get('title') or ''))}</div>"
            f"<div class=\"why\">{escape(str(entry.get('conclusion') or ''))}</div>"
            f"{question_html}<p class=\"risk\">风险：{escape(str(entry.get('risk') or ''))}</p>"
            f"{view_html}<div class=\"src\">原文：{sources}</div></li>")
    return "<ol>" + "".join(items) + "</ol>"


def _legacy_entries(base: str, entries: list[dict]) -> str:
    items = []
    for entry in entries:
        sources = entry.get("sources") or []
        if sources:
            first = sources[0]
            more = f"，另有 {len(sources) - 1} 条" if len(sources) > 1 else ""
            published = first.get("publishedAt") or ""
            shown = published[5:16].replace("T", " ") if len(published) >= 16 else published
            link = (f"<a href=\"{base}/doc/{quote(str(first.get('documentId') or ''), safe='')}\">"
                    f"{escape(str(first.get('sourceLabel') or ''))} {escape(shown)}</a>")
            source_html = f"<div class=\"src\">来源：{link}{more}</div>"
        else:
            source_html = ""
        disclosed = f"｜首次公开 {escape(str(entry['firstDisclosed']))}" if entry.get("firstDisclosed") else ""
        rank = f"{entry['rank']}. " if entry.get("rank") else ""
        headline = entry.get("headline") or entry.get("title") or ""
        reason = entry.get("reason") or ""
        reason_html = f"<div class=\"why\">{escape(str(reason))}</div>" if reason else ""
        items.append(f"<li><div><span class=\"co\">{rank}{escape(str(entry.get('companyName') or ''))} "
                     f"{escape(str(entry.get('companyCode') or ''))}</span>"
                     f"<span class=\"tag\">{escape(str(entry.get('type') or ''))}{disclosed}</span></div>"
                     f"<div class=\"hl\">{escape(str(headline))}</div>{reason_html}{source_html}</li>")
    return "<ol>" + "".join(items) + "</ol>"


def _entries(base: str, entries: list[dict]) -> str:
    if entries and ("title" in entries[0] or "questions" in entries[0]):
        return _evaluated_entries(base, entries)
    return _legacy_entries(base, entries)


def _empty_copy(content: dict) -> str:
    if "evaluated" not in content:
        return "<p>今天没有符合条件的利好。</p>"
    count = content.get("evaluated")
    shown = count if isinstance(count, int) and not isinstance(count, bool) else 0
    return f"<p>今天没有值得推的</p><p>评估了 {shown} 家公司的隔夜消息，没有值得推的</p>"


def _intro(base: str) -> str:
    action = escape(base + "/stock", quote=True)
    return ("<h2>简要提示词</h2>"
            f"<pre>{escape(BRIEF)}</pre>"
            "<h2>使用说明</h2>"
            "<p>单票页在本页地址后面加 /stock/代码或简称，例如 /stock/300002 或 /stock/神州泰岳。"
            "也可以在下面直接查。</p>"
            f"<form method=\"get\" action=\"{action}\"><label for=\"q\">代码或简称</label> "
            "<input id=\"q\" name=\"q\" maxlength=\"80\" required> "
            "<button type=\"submit\">查看</button></form>")


def _cell(value, places: int = 2) -> str:
    if value is None or isinstance(value, bool):
        return "—"
    if isinstance(value, float):
        return escape(f"{value:.{places}f}")
    return escape(str(value))


def _render_stock(base: str, data: dict) -> HTMLResponse:
    back = f"<p><a href=\"{escape(base, quote=True)}\">返回晨报</a></p>"
    if data.get("inPool") is False:
        return _page("不在 K10 股票池", f"<h1>不在 K10 股票池</h1><p>{escape(data['message'])}</p>{back}")
    if "candidates" in data:
        items = "".join(
            f"<li><a href=\"{escape(base, quote=True)}/stock/{escape(item['code'], quote=True)}\">"
            f"{escape(item['name'])} {escape(item['code'])}</a></li>"
            for item in data["candidates"])
        return _page("请指定代码", f"<h1>请指定代码</h1><p>{escape(data['message'])}</p><ul>{items}</ul>{back}")
    daily = data.get("daily") or {}
    intra = data.get("intraday") or {}
    quote = intra.get("quote") or {}
    series = intra.get("series") or {}
    morning = data.get("morningReport")
    valuation = daily.get("valuation") or {}
    flow = daily.get("moneyflow") or {}
    window = daily.get("window20") or {}
    averages = daily.get("averages") or {}
    parts = [f"<h1>{escape(data['name'])} {escape(data['code'])}</h1>",
             f"<p class=\"meta\">生成 {escape(str(data.get('generatedAt') or ''))}</p>"]
    if data.get("gaps"):
        parts.append("<div class=\"gap\">" + "<br>".join(escape(item) for item in data["gaps"]) + "</div>")
    if morning:
        title = morning.get("title") or ""
        conclusion = morning.get("conclusion") or ""
        risk = morning.get("risk") or ""
        risk_html = f"<p class=\"risk\">风险：{escape(str(risk))}</p>" if risk else ""
        parts.append("<h2>晨报</h2><p>"
                     f"{escape(str(morning.get('tradeDate') or ''))} {escape(str(title))}</p>"
                     f"<p class=\"why\">{escape(str(conclusion))}</p>{risk_html}")
    else:
        parts.append("<h2>晨报</h2><p>最新可用晨报的正式名单里没有它。</p>")
    parts.append("<h2>收盘后</h2>")
    if not daily:
        parts.append("<p>没有收盘数据。</p>")
    else:
        ma = []
        for width in (5, 10, 20):
            item = averages.get(f"ma{width}")
            ma.append(f"{width}日 —" if not item else f"{width}日 {_cell(item.get('value'), 4)}（{escape(item.get('position') or '')}）")
        parts.append(
            f"<p class=\"meta\">交易日 {escape(str(daily.get('tradeDate') or ''))}｜来源 TuShare｜"
            f"{escape(daily.get('adjustment') or '不复权')}</p>"
            f"<p>收盘 {_cell(daily.get('close'), 4)}｜涨跌幅 {_cell(daily.get('changePercent'))}%"
            f"｜成交额 {_cell(daily.get('turnoverYi'), 4)} 亿元｜换手率 {_cell(daily.get('turnoverRate'))}%</p>"
            f"<p>近20日：累计涨跌幅 {_cell(window.get('changePercent'))}%｜日均成交额 {_cell(window.get('averageTurnoverYi'), 4)} 亿元"
            f"｜日均振幅 {_cell(window.get('averageAmplitudePercent'))}%"
            f"{'' if not window.get('note') else '｜' + escape(window['note'])}</p>"
            f"<p>收盘价相对均线：{'｜'.join(ma)}"
            f"{'' if not daily.get('averageNote') else '｜' + escape(daily['averageNote'])}</p>"
            f"<p class=\"meta\">市值所属交易日 {escape(str(valuation.get('tradeDate') or '—'))}"
            f"{'' if not valuation.get('note') else '｜' + escape(valuation['note'])}</p>"
            f"<p>总市值 {_cell(valuation.get('totalMvYi'), 4)} 亿元｜流通市值 {_cell(valuation.get('circMvYi'), 4)} 亿元"
            f"｜市盈率TTM {_cell(valuation.get('peTtm'))}｜市净率 {_cell(valuation.get('pb'))}</p>"
            f"<p>资金净流入 {_cell(flow.get('netInflowYi'), 4)} 亿元"
            f"（{escape(flow.get('basis') or 'TuShare moneyflow 净流入额')}，交易日 {escape(str(flow.get('tradeDate') or '—'))}）"
            f"{'' if not flow.get('note') else '｜' + escape(flow['note'])}</p>")
    parts.append("<h2>盘中</h2>")
    if not intra:
        parts.append("<p>没有盘中数据。</p>")
    else:
        parts.append(f"<p class=\"meta\">分时交易日 {escape(str(intra.get('sessionDate') or ''))}</p>")
        if quote:
            parts.append(
                f"<p>最新 {_cell(quote.get('latestPrice'), 4)}｜昨收 {_cell(quote.get('previousClose'), 4)}"
                f"｜涨跌幅 {_cell(quote.get('changePercent'))}%｜成交额 {_cell(quote.get('turnoverYi'), 4)} 亿元"
                f"｜换手率 {_cell(quote.get('turnoverRate'))}%｜数据时间 {escape(str(quote.get('asOf') or '—'))}"
                f"{'' if not quote.get('note') else '｜' + escape(quote['note'])}</p>")
        if series:
            rows = "".join("<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>"
                           for row in series.get("rows") or [])
            head = "".join(f"<th>{escape(str(name))}</th>" for name in series.get("columns") or [])
            parts.append(f"<p class=\"meta\">{escape(series.get('note') or '')}｜数据时间 {escape(str(series.get('asOf') or '—'))}</p>"
                         f"<table><tr>{head}</tr>{rows}</table>")
    parts.append(back)
    return _page(f"{data['name']} {data['code']}", "".join(parts))


def render_report(base: str, report: dict) -> HTMLResponse:
    title = f"晨报 {_day_label(report['trade_date'])}"
    intro = _intro(base)
    if report["status"] == "failed":
        body = intro + (f"<h1>{escape(title)}</h1><p class=\"gap\">晨报出问题了：{escape(report['failure_reason'] or '原因未记录')}</p>")
    elif report["status"] != "available":
        body = intro + f"<h1>{escape(title)}</h1><p class=\"meta\">正在出报，稍后刷新。</p>"
    else:
        c = present_content(report["content"])
        body = intro + (f"<h1>{escape(title)}</h1><div class=\"meta\">窗口 {escape(c['window']['start'][5:16].replace('T', ' '))} → "
                f"{escape(c['window']['end'][5:16].replace('T', ' '))}｜生成 {escape(c['generatedAt'][11:16])}｜"
                f"规则 {escape(c['configVersion'])}</div>")
        if c.get("gapSummary"):
            body += f"<div class=\"gap\">缺口：{escape(c['gapSummary'])}</div>"
        body += _entries(base, c["entries"]) if c["entries"] else _empty_copy(c)
    body += (f"<footer><a href=\"{base}/history\">历史</a>　·　<a href=\"{base}/day/{escape(report['trade_date'])}?format=json\">JSON</a>"
             f"</footer>")
    return _page(title, body)


def create_router(*, db_path_provider: Callable[[], Path | None] = v4_db_path,
                  read_key_provider: Callable[[], str | None] = lambda: os.environ.get("NECKLINE_READ_KEY")) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    def guard(key: str) -> Path:
        expected = (read_key_provider() or "").strip()
        if len(expected) < 24 or not hmac.compare_digest(key.encode(), expected.encode()):
            raise HTTPException(status_code=404)
        path = db_path_provider()
        if path is None:
            raise HTTPException(status_code=404)
        return path

    def _report(path: Path, trade_date: str | None) -> dict:
        try:
            with db.read(path) as conn:
                report = db.get_report(conn, trade_date) if trade_date else db.latest_report(conn)
        except db.NotReady:
            report = None
        if report is None:
            raise HTTPException(status_code=404)
        return report

    @router.get("/read/{key}")
    def latest(key: str, format: str | None = None):
        path = guard(key)
        try:
            report = _report(path, None)
        except HTTPException:
            if format == "json":
                return JSONResponse({"status": "no_report", "brief": BRIEF}, headers=_HEADERS)
            return _page("晨报", _intro(f"/read/{key}") + "<h1>晨报</h1><p>还没有晨报。每个交易日早上 08:00 截止后出报，出来就会显示在这里。</p>")
        if format == "json":
            payload = report_out(report)
            payload["brief"] = BRIEF
            return JSONResponse(payload, headers=_HEADERS)
        return render_report(f"/read/{key}", report)

    @router.get("/read/{key}/day/{trade_date}")
    def by_date(key: str, trade_date: str, format: str | None = None):
        path = guard(key)
        report = _report(path, trade_date)
        if format == "json":
            return JSONResponse(report_out(report), headers=_HEADERS)
        return render_report(f"/read/{key}", report)

    @router.get("/read/{key}/history")
    def history(key: str):
        path = guard(key)
        try:
            with db.read(path) as conn:
                reports = db.list_reports(conn, 60)
        except db.NotReady:
            reports = []
        rows = "".join(
            f"<li><a href=\"/read/{key}/day/{escape(r['trade_date'])}\">{escape(_day_label(r['trade_date']))}</a>"
            f"<span class=\"tag\">{'出问题' if r['status'] == 'failed' else '出报中' if r['status'] == 'running' else str(len(r['content']['entries'])) + ' 家'}</span></li>"
            for r in reports)
        return _page("晨报历史", f"<h1>晨报历史</h1><ol>{rows or '<li>还没有晨报</li>'}</ol>"
                                 f"<footer><a href=\"/read/{key}\">最新一期</a></footer>")

    @router.get("/read/{key}/doc/{document_id}")
    def document(key: str, document_id: str, format: str | None = None):
        path = guard(key)
        try:
            with db.read(path) as conn:
                doc = db.get_document(conn, document_id)
        except db.NotReady:
            doc = None
        if doc is None:
            raise HTTPException(status_code=404)
        out = document_out(doc)
        if format == "json":
            return JSONResponse(out, headers=_HEADERS)
        original = (f"<p class=\"src\"><a href=\"{escape(out['url'])}\" rel=\"noreferrer noopener\">原始链接</a></p>"
                    if out["url"] else "")
        body = (f"<h1>{escape(out['title'] or out['sourceLabel'])}</h1><div class=\"meta\">{escape(out['sourceLabel'])}｜"
                f"{escape((out['publishedAt'] or '')[:16].replace('T', ' '))}</div>{original}<pre>{escape(out['text'])}</pre>"
                f"<footer><a href=\"/read/{key}\">返回晨报</a></footer>")
        return _page(out["title"] or "原文", body)

    @router.get("/read/{key}/stock")
    def stock_query(key: str, q: str | None = None, format: str | None = None):
        guard(key)
        text = (q or "").strip()
        if not text:
            return _page("查股票", _intro(f"/read/{key}") + "<p>输入代码或简称。</p>")
        target = f"/read/{key}/stock/{quote(text, safe='')}"
        if format:
            target += "?format=" + quote(format, safe="")
        return RedirectResponse(target, status_code=303, headers=_HEADERS)

    @router.get("/read/{key}/stock/{symbol}")
    async def stock_page(key: str, symbol: str, format: str | None = None):
        guard(key)
        data = await overview(symbol)
        if format == "json":
            payload = dict(data)
            payload["brief"] = BRIEF
            return JSONResponse(payload, headers=_HEADERS)
        return _render_stock(f"/read/{key}", data)

    return router
