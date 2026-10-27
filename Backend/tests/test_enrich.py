"""公告 PDF 读数：种类识别、追加文本、缓存复用、续跑请求体不变、下载失败不中断。不联网。"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from neckline.filings.fetch import FetchResult
from neckline.filings.reader import PdfText
from neckline.morning import db
from neckline.screening.classify import classify
from neckline.screening.deepseek import DeepSeekClient, FileReceipts
from neckline.screening.enrich import (
    MemoryFilingReads, announcement_title, enrich_filings, filing_title_kind, purge_old_pdfs,
)
from neckline.screening.pool import Company, CompanyPool
from neckline.screening.prefilter import Message, Window

CST = timezone(timedelta(hours=8))
WHEN = datetime(2026, 10, 27, 8, 0, tzinfo=CST)
SETTINGS = {"maxBytes": 1000, "maxPages": 2, "concurrency": 2, "timeoutSeconds": 5, "cacheDays": 3}
CLASSIFY = {"profileChars": 20, "modelOptions": {"max_tokens": 8},
            "batchMaxCompanies": 4, "batchMaxChars": 8000, "concurrency": 1, "maxAttempts": 1,
            "excerpt": {"lead": 20, "window": 10, "limit": 200, "minChars": 20, "blockMaxChars": 4000},
            "history": {"maxItems": 2}}
WINDOW = Window(start=WHEN - timedelta(hours=17), end=WHEN, collected_by=WHEN)


def _message(i: int, title: str, *, source: str = "cninfo-announcement", url: str | None = "https://static.cninfo.com.cn/x/a.pdf",
             text: str = "只有标题") -> Message:
    return Message(document_id=f"doc_{i}", revision=1, source_key=source, source_label="巨潮公告", url=url,
                   title=title, text=text, published_at=WHEN, first_seen_at=WHEN,
                   companies={"300953.SZ": ["震裕科技"]})


def _pdf(text: str) -> PdfText:
    return PdfText(pages=[text], page_count=1, truncated=False, error=None)


def test_filing_titles_keep_only_periodic_forecast_and_express():
    assert announcement_title("光力科技：2026年第三季度报告") == "2026年第三季度报告"
    assert announcement_title("关于2026年第三季度报告披露的提示性公告") == "关于2026年第三季度报告披露的提示性公告"
    assert filing_title_kind("光力科技：2026年第三季度报告") == "periodic"
    assert filing_title_kind("通光线缆：2026年三季度报告") == "periodic"
    assert filing_title_kind("公司：2026年半年度业绩预告") == "forecast"
    assert filing_title_kind("公司：2026年度业绩快报") == "express"
    assert filing_title_kind("关于召开2025年年度报告说明会的公告") is None
    assert filing_title_kind("兆日科技：控股股东签股份转让补充协议") is None
    assert filing_title_kind("震裕科技：预计前三季度净利增长") is None


def test_enrich_appends_the_reading_and_reuses_the_store(tmp_path, monkeypatch):
    calls = []

    def fetcher(url: str) -> FetchResult:
        calls.append(url)
        return FetchResult(tmp_path / "cached.pdf", None, False)

    monkeypatch.setattr("neckline.screening.enrich.read_pdf", lambda path, max_pages: _pdf("净利润同比增长 12%"))
    store = MemoryFilingReads()
    first = _message(1, "震裕科技：2026年第三季度报告")
    stats = enrich_filings([first], store=store, fetcher=fetcher, settings=SETTINGS, clock=lambda: WHEN)
    assert stats.attempted == 1 and stats.ok == 1 and stats.from_store == 0 and stats.failed == {}
    assert first.text.startswith("只有标题\n【公告 PDF 读数】")
    again = _message(1, "震裕科技：2026年第三季度报告")
    reused = enrich_filings([again], store=store, fetcher=fetcher, settings=SETTINGS, clock=lambda: WHEN)
    assert calls == ["https://static.cninfo.com.cn/x/a.pdf"]
    assert reused.from_store == 1 and reused.ok == 1 and again.text == first.text


def test_enriched_classify_request_is_stable_across_a_resume(tmp_path, monkeypatch):
    monkeypatch.setattr("neckline.screening.enrich.read_pdf", lambda path, max_pages: _pdf("营业收入同比增长 8%"))
    store = MemoryFilingReads()
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        content = json.dumps({"companies": [{"company": "300953", "events": [{
            "ids": ["m0001"], "relation": "主体", "type": "业绩预增", "direction": "利好", "channel": "公司公告",
            "amountYuan": None, "firstDisclosed": None, "fresh": "新", "headline": "三季报", "reason": "净利增长"}],
            "mentions": []}]}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1}})

    def run(slot: str):
        message = _message(1, "震裕科技：2026年第三季度报告")
        enrich_filings([message], store=store, fetcher=lambda url: FetchResult(tmp_path / "a.pdf", None, False),
                       settings=SETTINGS, clock=lambda: WHEN)
        client = DeepSeekClient(endpoint="https://example.invalid/chat/completions", model="m", api_key="k",
                                receipts=FileReceipts(tmp_path / slot), timeout_seconds=5, max_attempts=1,
                                transport=httpx.MockTransport(handler))
        classify([message], [], CompanyPool([Company("300953.SZ", "震裕科技", ("震裕科技",))]), {}, client,
                 window=WINDOW, settings=CLASSIFY)
        return message.text

    first, second = run("a"), run("b")
    assert first == second and "【公告 PDF 读数】" in first
    assert len(seen) == 2
    assert hashlib.sha256(seen[0]).hexdigest() == hashlib.sha256(seen[1]).hexdigest()


def test_download_failure_is_a_gap_and_does_not_stop_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr("neckline.screening.enrich.read_pdf", lambda path, max_pages: _pdf("净利润同比增长 3%"))
    calls = []

    def fetcher(url: str) -> FetchResult:
        calls.append(url)
        if url.endswith("bad.pdf"):
            return FetchResult(None, "http_error", False)
        return FetchResult(tmp_path / "ok.pdf", None, False)

    bad = _message(1, "通光线缆：2026年三季度报告", url="https://static.cninfo.com.cn/x/bad.pdf")
    good = _message(2, "光力科技：2026年第三季度报告", url="https://static.cninfo.com.cn/x/ok.pdf")
    skipped = _message(3, "震裕科技：预计前三季度净利增长", source="jin10-flash", url=None, text="净利增长 58%")
    stats = enrich_filings([bad, good, skipped], store=MemoryFilingReads(), fetcher=fetcher, settings=SETTINGS,
                           clock=lambda: WHEN)
    assert stats.attempted == 2 and stats.ok == 1 and stats.failed == {"http_error": 1}
    assert calls.count("https://static.cninfo.com.cn/x/bad.pdf") == 2
    assert bad.text.endswith("【公告 PDF 读数】未能读出（http_error）")
    assert "【公告 PDF 读数】" in good.text and "未能读出（http_error）" not in good.text
    assert skipped.text == "净利增长 58%"


def test_db_store_survives_a_second_run(tmp_path, monkeypatch):
    from neckline.screening.enrich import DbFilingReads

    path = tmp_path / "v4.db"
    db.init_schema(path)
    monkeypatch.setattr("neckline.screening.enrich.read_pdf", lambda *_args, **_kwargs: _pdf("净利润同比下降 5%"))
    calls = []

    def fetcher(url: str) -> FetchResult:
        calls.append(url)
        return FetchResult(tmp_path / "a.pdf", None, False)

    message = _message(1, "通光线缆：2026年三季度报告")
    enrich_filings([message], store=DbFilingReads(path), fetcher=fetcher, settings=SETTINGS, clock=lambda: WHEN)
    replay = _message(1, "通光线缆：2026年三季度报告")
    stats = enrich_filings([replay], store=DbFilingReads(path), fetcher=fetcher, settings=SETTINGS, clock=lambda: WHEN)
    assert calls == ["https://static.cninfo.com.cn/x/a.pdf"] and stats.from_store == 1 and replay.text == message.text
    with db.read(path) as conn:
        row = conn.execute("SELECT state, kind FROM filing_reads").fetchone()
    assert tuple(row) == ("ok", "periodic")


def test_purge_old_pdfs_only_removes_stale_files_in_that_directory(tmp_path):
    old, fresh, note = tmp_path / "old.pdf", tmp_path / "fresh.pdf", tmp_path / "note.txt"
    nested = tmp_path / "nested"
    nested.mkdir()
    buried = nested / "old.pdf"
    for path in (old, fresh, buried):
        path.write_bytes(b"%PDF")
    note.write_text("keep")
    past = (WHEN - timedelta(days=10)).timestamp()
    for path in (old, buried):
        os.utime(path, (past, past))
    purge_old_pdfs(tmp_path, cache_days=3, now=WHEN)
    assert not old.exists()
    assert fresh.exists() and note.exists() and buried.exists()
