"""只读网页兜底：/read/<访问码>。不用登录、不用 JavaScript，手机浏览器直接看；?format=json 给 Agent 读。

访问码是独立的 NECKLINE_READ_KEY（至少 24 位），不是 API 令牌；没配置或不对一律 404。
响应禁止索引、缓存和发送 Referer。读的是和推送、App 同一份晨报记录。
"""
from __future__ import annotations

import hmac
import os
from datetime import date
from html import escape
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from . import db
from .api import document_out, report_out, v4_db_path

_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}
_WEEKDAYS = "一二三四五六日"
_CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1f;--muted:#6e6e73;--line:#e3e1db;--card:#fff;--accent:#9a3412}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#ecebe8;--muted:#9a9a9f;--line:#2c2c2e;--card:#1c1c1e;--accent:#fb923c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}h1{font-size:22px;margin:4px 0}h2{font-size:17px;margin:24px 0 8px}
.meta{color:var(--muted);font-size:13px}.gap{margin:12px 0;padding:8px 12px;border-left:3px solid var(--accent);background:var(--card);font-size:14px}
ol{padding:0;list-style:none;margin:0}li{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:10px 0}
.co{font-weight:600}.tag{color:var(--muted);font-size:13px;margin-left:6px}.hl{margin:4px 0}.why{color:var(--muted);font-size:14px}
.src{font-size:13px;margin-top:6px}a{color:var(--accent);text-decoration:none}details{margin:16px 0}summary{cursor:pointer;color:var(--muted)}
pre{white-space:pre-wrap;word-break:break-word;font:inherit}footer{margin:28px 0 8px;font-size:13px;color:var(--muted)}
"""


def _page(title: str, body: str) -> HTMLResponse:
    html = (f"<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<meta name=\"robots\" content=\"noindex,nofollow\"><meta name=\"referrer\" content=\"no-referrer\">"
            f"<title>{escape(title)}</title><style>{_CSS}</style></head><body><main>{body}</main></body></html>")
    return HTMLResponse(html, headers=_HEADERS)


def _day_label(trade_date: str) -> str:
    day = date.fromisoformat(trade_date)
    return f"{trade_date}（周{_WEEKDAYS[day.weekday()]}）"


def _entries(base: str, entries: list[dict]) -> str:
    items = []
    for e in entries:
        first = e["sources"][0]
        more = f"，另有 {len(e['sources']) - 1} 条" if len(e["sources"]) > 1 else ""
        link = f"<a href=\"{base}/doc/{escape(first['documentId'])}\">{escape(first['sourceLabel'])} {escape(first['publishedAt'][5:16].replace('T', ' '))}</a>"
        disclosed = f"｜首次公开 {escape(e['firstDisclosed'])}" if e.get("firstDisclosed") else ""
        others = "".join(f"<div class=\"why\">同公司另有：{escape(o['headline'])}</div>" for o in e.get("others", []))
        items.append(f"<li><div><span class=\"co\">{e['rank']}. {escape(e['companyName'])} {escape(e['companyCode'])}</span>"
                     f"<span class=\"tag\">{escape(e['type'])}｜{escape(e['importance'])}{disclosed}</span></div>"
                     f"<div class=\"hl\">{escape(e['headline'])}</div><div class=\"why\">{escape(e['reason'])}</div>"
                     f"{others}<div class=\"src\">来源：{link}{more}</div></li>")
    return "<ol>" + "".join(items) + "</ol>"


def render_report(base: str, report: dict) -> HTMLResponse:
    title = f"晨报 {_day_label(report['trade_date'])}"
    if report["status"] == "failed":
        body = (f"<h1>{escape(title)}</h1><p class=\"gap\">晨报出问题了：{escape(report['failure_reason'] or '原因未记录')}</p>")
    elif report["status"] != "available":
        body = f"<h1>{escape(title)}</h1><p class=\"meta\">正在出报，稍后刷新。</p>"
    else:
        c = report["content"]
        body = (f"<h1>{escape(title)}</h1><div class=\"meta\">窗口 {escape(c['window']['start'][5:16].replace('T', ' '))} → "
                f"{escape(c['window']['end'][5:16].replace('T', ' '))}｜生成 {escape(c['generatedAt'][11:16])}｜"
                f"规则 {escape(c['configVersion'])}</div>")
        if c.get("gapSummary"):
            body += f"<div class=\"gap\">缺口：{escape(c['gapSummary'])}</div>"
        body += _entries(base, c["entries"]) if c["entries"] else "<p>今天没有符合条件的利好。</p>"
        for key, label in (("types", "待定：类型不在收录清单"), ("relation", "待定：受益方"),
                           ("fresh", "待定：看不出是否窗口内首次公开"), ("old", "判为旧事的利好")):
            entries = c["review"].get(key) or []
            if entries:
                body += f"<details><summary>{label}（{len(entries)}）</summary>{_entries(base, entries)}</details>"
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
                return JSONResponse({"status": "no_report"}, headers=_HEADERS)
            return _page("晨报", "<h1>晨报</h1><p>还没有晨报。每个交易日早上 08:00 截止后出报，出来就会显示在这里。</p>")
        if format == "json":
            return JSONResponse(report_out(report), headers=_HEADERS)
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

    return router
