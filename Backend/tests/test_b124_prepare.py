"""B124：二级数据准备。行情和 PDF 都用注入的假数据，不联网。"""
from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest

from neckline.evaluation.market import (
    BASIC_FIELDS, FINA_FIELDS, INCOME_FIELDS, RawMarket, SnapshotSource, TuShareSource,
    clip_raw, company_snapshot, price_background,
)
from neckline.evaluation.prepare import _metric, _qoq, build_package
from neckline.evaluation.snapshot import capture, main, read_codes
from neckline.filings.fetch import FetchResult
from neckline.market.tushare_daily import API_URL, DAILY_FIELDS
from neckline.market.util import SHANGHAI
from neckline.morning.report import load_config
from neckline.morning.schedule import CST
from neckline.screening.classify import Event
from neckline.screening.enrich import FilingRecord, MemoryFilingReads
from neckline.screening.pool import Company, CompanyPool
from neckline.screening.prefilter import Message, Window

DAY = date(2026, 10, 22)
TOKEN = "test-tushare-token"
NOW = datetime(2026, 10, 27, 8, 0, tzinfo=SHANGHAI)
WINDOW = Window(start=datetime(2026, 10, 21, 15, 0, tzinfo=CST), end=datetime(2026, 10, 22, 8, 0, tzinfo=CST),
                collected_by=datetime(2026, 10, 22, 8, 2, tzinfo=CST))
CLOCK = lambda: datetime(2026, 10, 22, 8, 0, tzinfo=CST)
LIMITS = {
    "pdfMaxPages": 40, "originalChars": 12000, "openingChars": 3000, "headingsMax": 60,
    "mediaChars": 2000, "maxMedia": 5, "pdfMaxBytes": 30000000,
}


def _limits(**overrides: int) -> dict:
    merged = dict(LIMITS)
    merged.update(overrides)
    return merged


def _pool() -> SimpleNamespace:
    pool = CompanyPool([Company("300001.SZ", "甲公司", ("甲公司",))])
    return SimpleNamespace(pool=pool, profiles={"300001.SZ": "做测试产品"})


def _message(i: int, hour: int, *, source: str = "jin10-flash", title: str = "", text: str = "",
             url: str | None = None) -> Message:
    ts = datetime(2026, 10, 21, hour, 0, tzinfo=CST)
    label = "巨潮公告" if source == "cninfo-announcement" else "金十快讯"
    return Message(document_id=f"doc_{i}", revision=1, source_key=source, source_label=label, url=url,
                   title=title, text=text, published_at=ts, first_seen_at=ts)


def _event(ids: list[str], **fields: object) -> Event:
    base: dict = dict(relation="主体", type="订单中标", direction="利好", first_disclosed=None, fresh="新",
                      headline="签了订单", reason="金额大", channel="公司公告", amount_yuan=None)
    base.update(fields)
    return Event(company="300001.SZ", ids=ids, **base)


def _candidate(primary: Event, messages: list[Message], others: list[Event] | None = None) -> SimpleNamespace:
    return SimpleNamespace(company="300001.SZ", primary=primary, others=list(others or []), messages=list(messages),
                           by_id=None)


def _raw(**kwargs: object) -> RawMarket:
    return RawMarket(code="300001", fetched_at="2026-10-22T08:00:00+08:00", **kwargs)  # type: ignore[arg-type]


def _bar(day: int, close: float, pre: float, pct: float, amount: float = 1000) -> dict:
    return {"ts_code": "300001.SZ", "trade_date": str(day), "close": close, "pre_close": pre,
            "pct_chg": pct, "high": close, "low": close, "amount": amount}


def _income(end: str, ann: str, revenue: float, net: float, flag: str = "1") -> dict:
    return {"ts_code": "300001.SZ", "end_date": end, "ann_date": ann, "update_flag": flag,
            "revenue": revenue, "n_income_attr_p": net}


def _fina(end: str, ann: str, deducted: float, flag: str = "1") -> dict:
    return {"ts_code": "300001.SZ", "end_date": end, "ann_date": ann, "update_flag": flag, "profit_dedt": deducted}


class _Market:
    def __init__(self, raw: RawMarket) -> None:
        self.raw_market = raw

    def raw(self, code: str) -> RawMarket:
        return self.raw_market


def _build(candidate: object, *, raw: RawMarket | None = None, settings: dict | None = None,
           store: object | None = None, fetcher=None) -> dict:
    return build_package(
        candidate, trade_date=DAY, window=WINDOW, pool=_pool(), market=_Market(raw or _raw()),
        filings_store=store if store is not None else MemoryFilingReads(),
        fetcher=fetcher or (lambda _url: FetchResult(None, "timeout", False)),
        settings=settings or _limits(), clock=CLOCK)


def _row(**fields: object) -> dict:
    base = {"current": None, "yoy": None, "prior": None, "ytd": None, "ytd_yoy": None}
    base.update(fields)
    return base


def _store(message: Message, kind: str, figures: dict) -> MemoryFilingReads:
    store = MemoryFilingReads()
    store.put(FilingRecord(
        document_id=message.document_id, revision=message.revision, url=message.url or "", kind=kind,
        state="ok", error=None, figures_json=json.dumps(figures, ensure_ascii=False, sort_keys=True),
        summary="cached", read_at="2026-10-22T00:00:00+00:00"))
    return store


def test_clip_drops_same_day_filing_and_bar() -> None:
    raw = _raw(
        income=[
            _income("20260930", "20261022", 999, 999),
            _income("20260630", "20260820", 10, 4),
        ],
        daily=[_bar(20261021, 10, 9, 1), _bar(20261022, 999, 10, 50)],
        daily_basic=[
            {"trade_date": "20261021", "total_mv": 12.5, "pe_ttm": 18.5},
            {"trade_date": "20261022", "total_mv": 99, "pe_ttm": 99},
        ],
    )
    clipped = clip_raw(raw, DAY)
    assert [row["ann_date"] for row in clipped.income] == ["20260820"]
    assert [row["trade_date"] for row in clipped.daily] == ["20261021"]
    assert len(raw.income) == 2 and len(raw.daily) == 2
    snap = company_snapshot(raw, DAY)
    assert snap["revenue"] == 10.0 and snap["netProfit"] == 4.0
    assert snap["marketDate"] == "2026-10-21" and snap["totalMv"] == 125000.0 and snap["peTtm"] == 18.5
    price = price_background(raw, DAY)
    assert price["tradeDate"] == "2026-10-21" and price["changePercent"] == 1.0


def test_same_period_prefers_latest_announcement_then_update_flag() -> None:
    raw = _raw(income=[
        _income("20260630", "20260820", 2, 2, "1"),
        _income("20260630", "20260820", 1, 1, "0"),
        _income("20260630", "20260828", 5, 5, "0"),
        _income("20260331", "20261021", 9, 9, "1"),
        _income("20260930", "20261022", 100, 100, "1"),
    ])
    snap = company_snapshot(raw, DAY)
    assert snap["reportPeriod"] == "2026-06-30" and snap["annDate"] == "2026-08-28"
    assert snap["revenue"] == 5.0 and snap["netProfit"] == 5.0
    tied = _raw(income=[_income("20260630", "20260820", 1, 1, "0"), _income("20260630", "20260820", 2, 2, "1")])
    chosen = company_snapshot(tied, DAY)
    assert chosen["revenue"] == 2.0 and chosen["annDate"] == "2026-08-20"


def test_company_snapshot_converts_market_cap_and_lists_missing_fields() -> None:
    raw = _raw(
        income=[_income("20260630", "20260820", 100.25, 40)],
        fina_indicator=[_fina("20260630", "20260820", 30.5)],
        daily=[_bar(20261021, 10, 10, 0)],
        daily_basic=[{"trade_date": "20261021", "total_mv": 12.5, "pe_ttm": 18.5}],
    )
    snap = company_snapshot(raw, DAY)
    assert snap["revenue"] == 100.25 and snap["netProfit"] == 40.0 and snap["deducted"] == 30.5
    assert snap["totalMv"] == 125000.0 and snap["peTtm"] == 18.5
    assert snap["missing"] == []
    empty = company_snapshot(_raw(income=[_income("20260630", "20260820", 100.25, 40)]), DAY)
    assert empty["deducted"] is None and empty["totalMv"] is None
    assert empty["missing"] == ["扣非净利", "行情日", "总市值", "市盈率"]


def test_price_background_twenty_day_change_and_moving_average_position() -> None:
    flat = [_bar(20261002 + index, 10, 10, 0) for index in range(20)]
    flat.append(_bar(20261022, 999, 10, 50))
    price = price_background(_raw(daily=flat), DAY)
    assert price["tradeDate"] == "2026-10-21"
    assert price["changePercent"] == 0.0
    assert price["previous20"]["changePercent"] == 0.0
    assert price["previous20"]["averageAmountYuan"] == 1_000_000.0
    assert price["previous20"]["missingDays"] == 0 and price["previous20"]["note"] is None
    assert price["ma5"] == "持平" and price["ma10"] == "持平" and price["ma20"] == "持平"

    above = [_bar(20261002 + index, 10, 10, 0) for index in range(19)]
    above.append(_bar(20261021, 12, 10, 20))
    up = price_background(_raw(daily=above), DAY)
    assert up["previous20"]["changePercent"] == 20.0 and up["changePercent"] == 20.0
    assert up["ma5"] == "上方" and up["ma20"] == "上方"

    below = [_bar(20261002 + index, 10, 10, 0) for index in range(19)]
    below.append(_bar(20261021, 8, 10, -20))
    down = price_background(_raw(daily=below), DAY)
    assert down["previous20"]["changePercent"] == -20.0 and down["ma5"] == "下方"

    short = price_background(_raw(daily=flat[:3]), DAY)
    assert short["previous20"]["changePercent"] is None and short["previous20"]["missingDays"] == 17
    assert short["previous20"]["note"] is not None and "只有3天" in short["previous20"]["note"]
    assert short["ma5"] is None


def test_quarter_on_quarter_subtracts_cumulative_and_q1_does_not() -> None:
    filing = _message(1, 20, source="cninfo-announcement", title="甲公司：2026年第三季度报告",
                      url="http://static.cninfo.com.cn/finalpage/2026-10-22/1.PDF")
    news = _message(2, 21, title="三季报解读", text="媒体称三季报大增")
    figures = {
        "period": "q3", "missing": [], "non_recurring_current": 30, "non_recurring_ytd": 40,
        "revenue": _row(current=100, yoy=10, ytd=300, ytd_yoy=12),
        "net_profit": _row(current=120, yoy=20, ytd=400, ytd_yoy=15),
        "deducted": _row(current=90, yoy=18, ytd=360, ytd_yoy=14),
    }
    raw = _raw(
        income=[
            _income("20260630", "20261022", 999999, 999999),
            _income("20260630", "20260820", 80, 250),
            _income("20260331", "20260425", 30, 100),
            _income("20250930", "20251025", 1, 80),
        ],
        fina_indicator=[
            _fina("20260630", "20261022", 999999),
            _fina("20260630", "20260820", 130),
            _fina("20260331", "20260425", 70),
        ],
        daily=[_bar(20261021, 10, 10, 0)],
        daily_basic=[{"trade_date": "20261021", "total_mv": 12.5, "pe_ttm": 18.5}],
    )
    candidate = _candidate(_event([f"{filing.document_id}@1", f"{news.document_id}@1"], type="业绩预增"), [news, filing])
    package = _build(candidate, raw=raw, store=_store(filing, "periodic", figures))
    again = _build(candidate, raw=raw, store=_store(filing, "periodic", figures))
    assert json.dumps(package, ensure_ascii=False) == json.dumps(again, ensure_ascii=False)
    earn = package["earnings"]
    assert earn["kind"] == "periodic" and earn["period"] == "q3" and earn["message"] == "m1"
    assert earn["quarter"]["netProfit"] == {"value": 120.0, "low": None, "high": None, "source": "pdf",
                                            "missing": None, "note": "本报告期"}
    assert earn["priorQuarter"]["netProfit"]["value"] == 150.0
    assert earn["priorQuarter"]["netProfit"]["source"] == "计算"
    assert earn["priorQuarter"]["deducted"]["value"] == 60.0
    assert earn["quarterOnQuarter"]["netProfit"]["value"] == -20.0
    assert earn["quarterOnQuarter"]["netProfit"]["source"] == "计算"
    assert earn["netMinusDeducted"]["value"] == 30.0 and earn["netMinusDeducted"]["source"] == "计算"
    assert earn["nonRecurring"]["current"]["value"] == 30.0
    assert earn["nonRecurring"]["current"]["ratio"] == 25.0
    assert earn["nonRecurring"]["current"]["source"] == "pdf"
    assert earn["nonRecurring"]["ytd"]["value"] == 40.0 and earn["nonRecurring"]["ytd"]["ratio"] == 10.0
    assert earn["base"]["netProfit"]["value"] == 80.0 and earn["base"]["netProfit"]["source"] == "tushare"
    assert earn["yoy"]["netProfit"]["value"] == 20.0 and earn["yoy"]["netProfit"]["source"] == "pdf"
    assert package["financials"]["revenue"] == 80.0 and package["financials"]["totalMv"] == 125000.0
    assert any("公告下载失败（m1）：timeout" == item for item in package["gaps"])

    q1 = _message(3, 9, source="cninfo-announcement", title="甲公司：2026年第一季度报告",
                  url="http://static.cninfo.com.cn/finalpage/2026-04-25/2.PDF")
    q1_figures = {
        "period": "q1", "missing": [], "non_recurring_current": None, "non_recurring_ytd": None,
        "revenue": _row(current=40, yoy=-5, prior=50),
        "net_profit": _row(current=80, yoy=-10, prior=50),
        "deducted": _row(current=70, yoy=-8, prior=40),
    }
    # 同期 TuShare 累计故意放一个大数：一季度单季必须直接用本报告期，不能再减。
    q1_raw = _raw(income=[_income("20260331", "20260420", 999, 999)])
    q1_candidate = _candidate(_event([f"{q1.document_id}@1"], type="业绩预增"), [q1])
    q1_package = _build(q1_candidate, raw=q1_raw, store=_store(q1, "periodic", q1_figures))
    quarter = q1_package["earnings"]["quarter"]["netProfit"]
    assert quarter["value"] == 80.0 and quarter["source"] == "pdf" and quarter["note"] == "一季度无需相减"
    assert q1_package["earnings"]["quarterOnQuarter"]["netProfit"]["missing"] == "缺上一期"
    assert q1_package["earnings"]["base"]["netProfit"]["value"] == 50.0
    assert q1_package["earnings"]["base"]["netProfit"]["source"] == "pdf"


def test_quarter_on_quarter_keeps_direction_when_prior_quarter_lost() -> None:
    loss = _metric(-100, source="计算")
    narrower = _qoq(_metric(-50, source="计算"), loss)
    wider = _qoq(_metric(-150, source="计算"), loss)
    into_loss = _qoq(_metric(-50, source="pdf"), _metric(100, source="pdf"))
    assert narrower["value"] == 50.0 and wider["value"] == -50.0 and into_loss["value"] == -150.0
    assert narrower["note"] == "本季对上一季；上季亏损，按差额除以上季绝对值"
    assert "上季亏损" in wider["note"]
    assert "上季亏损" not in (into_loss["note"] or "")
    zero = _qoq(_metric(50, source="pdf"), _metric(0, source="计算"))
    assert zero["value"] is None and zero["missing"] == "上一季为零"
    ranged = _qoq(_metric(low=-80, high=-40, source="计算"), loss)
    assert ranged["low"] == 20.0 and ranged["high"] == 60.0
    assert ranged["note"] == "本季对上一季；上季亏损，按差额除以上季绝对值"

    filing = _message(1, 20, source="cninfo-announcement", title="甲公司：2026年前三季度业绩预告",
                      url="http://static.cninfo.com.cn/finalpage/2026-10-14/3.PDF")
    figures = {
        "period": "2026年1月1日至2026年9月30日", "missing": [],
        "net_profit": {"state": "盈利", "current": {"low": 0, "high": 50}, "prior": -80,
                       "yoy": {"low": 10, "high": 20}},
        "deducted": {"state": "盈利", "current": {"low": 0, "high": 40}, "prior": -70,
                     "yoy": {"low": 5, "high": 8}},
    }
    raw = _raw(income=[
        _income("20260331", "20260425", 10, 50),
        _income("20260630", "20260820", 20, -50),
    ])
    candidate = _candidate(_event([f"{filing.document_id}@1"], type="业绩预增"), [filing])
    package = _build(candidate, raw=raw, store=_store(filing, "forecast", figures))
    quarter = package["earnings"]["quarterOnQuarter"]["netProfit"]
    assert package["earnings"]["priorQuarter"]["netProfit"]["value"] == -100.0
    assert quarter["low"] == 150.0 and quarter["high"] == 200.0 and quarter["value"] is None
    assert quarter["note"] == "本季对上一季；上季亏损，按差额除以上季绝对值"


def test_forecast_quarter_subtracts_half_year_cumulative_range() -> None:
    filing = _message(1, 20, source="cninfo-announcement", title="甲公司：2026年前三季度业绩预告",
                      url="http://static.cninfo.com.cn/finalpage/2026-10-14/3.PDF")
    figures = {
        "period": "2026年1月1日至2026年9月30日", "missing": [],
        "net_profit": {"state": "盈利", "current": {"low": 300, "high": 400}, "prior": 200,
                       "yoy": {"low": 10, "high": 20}},
        "deducted": {"state": "盈利", "current": {"low": 280, "high": 360}, "prior": 180,
                     "yoy": {"low": 5, "high": 8}},
    }
    raw = _raw(income=[
        _income("20260630", "20261022", 1, 999999),
        _income("20260630", "20260820", 80, 250),
        _income("20250930", "20251025", 1, 1),
    ])
    candidate = _candidate(_event([f"{filing.document_id}@1"], type="业绩预增"), [filing])
    package = _build(candidate, raw=raw, store=_store(filing, "forecast", figures))
    quarter = package["earnings"]["quarter"]["netProfit"]
    assert quarter["low"] == 50.0 and quarter["high"] == 150.0 and quarter["value"] is None
    assert quarter["source"] == "计算" and quarter["note"] == "累计区间减去半年报累计"
    assert package["earnings"]["base"]["netProfit"]["value"] == 200.0
    assert package["earnings"]["base"]["netProfit"]["source"] == "pdf"
    assert package["earnings"]["yoy"]["revenue"]["missing"] == "预告没有营业收入"

    missing = _build(candidate, raw=_raw(income=[_income("20260331", "20260425", 30, 100)]),
                     store=_store(filing, "forecast", figures))
    assert missing["earnings"]["quarter"]["netProfit"]["missing"] == "缺上一期"


def test_long_text_uses_headings_opening_and_attaches_media() -> None:
    body = "一、标题甲\n二、标题乙\n三、标题丙\n" + "正文" * 30
    first = _message(1, 10, title="长新闻", text=body)
    second = _message(2, 11, title="媒体一", text="媒体报道一二三四五")
    third = _message(3, 12, title="媒体二", text="第二条媒体报道")
    ids = [f"{item.document_id}@1" for item in (first, second, third)]
    package = _build(_candidate(_event(ids), [third, first, second]),
                     settings=_limits(originalChars=20, openingChars=8, headingsMax=2, mediaChars=5, maxMedia=1))
    document = package["original"]["documents"][0]
    assert package["original"]["kind"] == "news" and document["id"] == "m1"
    assert document["truncated"] is True and document["text"] is None
    assert document["chars"] == len(body) and document["pages"] is None
    assert document["headings"] == ["一、标题甲", "二、标题乙"]
    assert document["opening"] == body[:8]
    assert package["original"]["media"] == [{
        "message": "m2", "source": "金十快讯", "title": "媒体一", "publishedAt": "2026-10-21T11:00:00+08:00",
        "text": "媒体报道一", "chars": len("媒体报道一二三四五"), "truncated": True,
    }]
    assert "媒体报道超过 1 条，只附了前 1 条" in package["gaps"]
    assert package["limits"]["originalChars"] == 20
    assert package["search"] == {"enabled": False, "results": []}


def test_pdf_failure_still_builds_package_and_records_gaps(tmp_path: Path) -> None:
    filing = _message(1, 18, source="cninfo-announcement", title="关于购买资产的公告",
                      url="http://static.cninfo.com.cn/finalpage/2026-10-22/9.PDF", text="只有标题")
    candidate = _candidate(_event([f"{filing.document_id}@1"], headline="买资产"), [filing])
    failed = _build(candidate)
    assert "earnings" not in failed
    assert failed["company"]["name"] == "甲公司" and failed["events"]["primary"]["headline"] == "买资产"
    assert failed["original"]["documents"][0]["error"] == "timeout"
    assert "公告下载失败（m1）：timeout" in failed["gaps"]
    assert failed["messages"][0]["cninfo"] is True and failed["messages"][0]["ref"] == "doc_1@1"

    garbage = tmp_path / "bad.pdf"
    garbage.write_bytes(b"this is not a pdf")
    unreadable = _build(candidate, fetcher=lambda _url: FetchResult(garbage, None, False))
    assert unreadable["original"]["documents"][0]["error"] == "pdf_unreadable"
    assert any(item.startswith("公告读取失败（a1）") for item in unreadable["gaps"])
    assert unreadable["events"]["primary"]["messages"] == ["m1"]


def test_build_package_is_byte_stable_and_keeps_other_events_out_of_original() -> None:
    primary = _message(1, 16, title="订单", text="签订了大额订单")
    other = _message(2, 18, title="卖资产", text="出售一条产线")
    candidate = _candidate(
        _event([f"{primary.document_id}@1"]),
        [other, primary],
        [_event([f"{other.document_id}@1"], type="出售资产", headline="卖资产")],
    )
    left = _build(candidate)
    right = _build(candidate)
    assert left == right
    assert json.dumps(left, ensure_ascii=False) == json.dumps(right, ensure_ascii=False)
    assert left["messages"][0]["id"] == "m1" and left["messages"][1]["id"] == "m2"
    assert left["events"]["primary"]["messages"] == ["m1"]
    assert left["events"]["others"][0]["messages"] == ["m2"]
    assert left["original"]["documents"][0]["text"] == "签订了大额订单"
    assert left["original"]["documents"][0]["truncated"] is False
    assert left["original"]["media"] == []
    assert left["sizes"]["total"] > 0 and "earnings" not in left


def test_snapshot_source_missing_code_and_replay_still_clips(tmp_path: Path) -> None:
    payload = {"300001": _raw(income=[_income("20260930", "20261022", 999, 999)]).as_dict()}
    path = tmp_path / "market.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    source = SnapshotSource(path)
    missing = source.raw("300009")
    assert missing.income == [] and missing.errors["income"] == "not_in_snapshot"
    assert missing.errors["daily_basic"] == "not_in_snapshot"
    present = source.raw("300001.SZ")
    assert present.income[0]["revenue"] == 999
    assert company_snapshot(present, DAY)["revenue"] is None
    news = _message(1, 16, title="订单", text="签订了大额订单")
    candidate = SimpleNamespace(company="300009", primary=_event([f"{news.document_id}@1"]), others=[],
                                messages=[news], by_id=None)
    package = build_package(candidate, trade_date=DAY, window=WINDOW, pool=_pool(), market=source,
                            filings_store=MemoryFilingReads(), fetcher=lambda _url: FetchResult(None, "timeout", False),
                            settings=_limits(), clock=CLOCK)
    assert "行情快照没有这只股票" in package["gaps"]
    assert package["company"]["code"] == "300009" and package["search"]["enabled"] is False


def test_tushare_source_requests_only_needed_fields_and_hides_token() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).rstrip("/") == API_URL
        body = json.loads(request.content)
        seen.append(body)
        assert set(body) == {"api_name", "token", "params", "fields"}
        name = body["api_name"]
        if name == "income":
            raise httpx.ConnectError(f"down {TOKEN}")
        if name == "daily_basic":
            return httpx.Response(200, json={"code": 40203, "msg": f"频率 {TOKEN}"})
        fields = body["fields"].split(",")
        return httpx.Response(200, json={"code": 0, "data": {"fields": fields, "items": [], "has_more": False}})

    source = TuShareSource(TOKEN, transport=httpx.MockTransport(handler), now=lambda: NOW)
    try:
        raw = source.raw("300001")
    finally:
        source.close()
    by_name = {body["api_name"]: body for body in seen}
    assert set(by_name) == {"income", "fina_indicator", "daily", "daily_basic"}
    assert by_name["income"]["params"] == {"ts_code": "300001.SZ", "start_date": "20241027"}
    assert by_name["fina_indicator"]["params"] == {"ts_code": "300001.SZ", "start_date": "20241027"}
    assert by_name["daily"]["params"] == {"ts_code": "300001.SZ", "start_date": "20260818", "end_date": "20261027"}
    assert set(by_name["daily_basic"]["params"]) == {"ts_code", "start_date", "end_date"}
    assert by_name["income"]["fields"] == INCOME_FIELDS
    assert by_name["fina_indicator"]["fields"] == FINA_FIELDS
    assert by_name["daily"]["fields"] == DAILY_FIELDS
    assert by_name["daily_basic"]["fields"] == BASIC_FIELDS
    assert "pb" not in BASIC_FIELDS and "end_date" not in by_name["income"]["params"]
    assert raw.errors["income"] == "transport_error"
    assert raw.errors["daily_basic"] == "TuShare 行情频率超限，这次没有重试。"
    assert raw.errors["fina_indicator"] is None and raw.errors["daily"] is None
    dumped = json.dumps(raw.as_dict(), ensure_ascii=False)
    assert TOKEN not in dumped

    calls = {"n": 0}

    def blocked(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"code": 0, "msg": TOKEN})

    empty = TuShareSource("", transport=httpx.MockTransport(blocked), now=lambda: NOW)
    try:
        denied = empty.raw("300001")
    finally:
        empty.close()
    assert calls["n"] == 0
    assert denied.errors["income"] == "credential_missing"
    assert TOKEN not in json.dumps(denied.as_dict(), ensure_ascii=False)


def test_snapshot_capture_skips_existing_codes_and_prints_counts_only(tmp_path: Path, monkeypatch, capsys) -> None:
    existing = tmp_path / "old.json"
    existing.write_text(json.dumps({"300001": _raw().as_dict()}), encoding="utf-8")
    called: list[str] = []

    class Fake:
        def raw(self, code: str) -> RawMarket:
            called.append(code)
            return RawMarket(code=code, fetched_at="2026-10-27T08:00:00+08:00")

    progress: list[str] = []
    out = tmp_path / "out.json"
    capture(["300001", "300002", "300002"], out, source=Fake(), merge=existing, concurrency=1,
            progress=progress.append)
    assert called == ["300002"] and progress == ["1/1"]
    assert TOKEN not in "".join(progress)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert set(saved) == {"300001", "300002"}
    codes = tmp_path / "codes.txt"
    codes.write_text("300001\n\n300002\n", encoding="utf-8")
    assert read_codes(codes) == ["300001", "300002"]
    bad = tmp_path / "bad.txt"
    bad.write_text("300001\nABCDEF\n", encoding="utf-8")
    with pytest.raises(ValueError, match="六位"):
        read_codes(bad)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    assert main(["--codes", str(codes), "--out", str(tmp_path / "cli.json")]) == 2
    assert capsys.readouterr().out.strip() == "未配置 TUSHARE_TOKEN"


def test_evaluation_config_requires_positive_integers(tmp_path: Path) -> None:
    config = load_config()
    evaluation = config.rules["evaluation"]
    assert {key: evaluation["search"][key] for key in ("endpoint", "count", "summaryChars", "timeoutSeconds", "maxAttempts")} == {
        "endpoint": "https://api.deepseek.com/v1/tools/search", "count": 10, "summaryChars": 1000,
        "timeoutSeconds": 30, "maxAttempts": 2}
    assert evaluation["note"] == "长度参数待 B7 用真实公告实测后定稿"
    assert evaluation["package"]["pdfMaxPages"] == 40
    assert evaluation["package"]["originalChars"] == 12000
    assert evaluation["package"]["openingChars"] == 3000
    assert evaluation["package"]["headingsMax"] == 60
    assert evaluation["package"]["mediaChars"] == 2000
    assert evaluation["package"]["maxMedia"] == 5
    assert evaluation["package"]["pdfMaxBytes"] == 30000000
    assert evaluation["market"] == {"concurrency": 4, "timeoutSeconds": 20}

    def rejected(mutate, message: str) -> None:
        rules = json.loads(json.dumps(config.rules))
        mutate(rules)
        path = tmp_path / "rules.json"
        path.write_text(json.dumps(rules), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            load_config(path=path)

    rejected(lambda rules: rules.pop("evaluation"), "evaluation")
    rejected(lambda rules: rules["evaluation"].pop("search"), "evaluation.search")
    rejected(lambda rules: rules["evaluation"].__setitem__("search", {}), "evaluation.search")
    rejected(lambda rules: rules["evaluation"]["package"].__setitem__("originalChars", 0), "originalChars")
    rejected(lambda rules: rules["evaluation"]["package"].pop("pdfMaxBytes"), "pdfMaxBytes")
    rejected(lambda rules: rules["evaluation"]["market"].__setitem__("timeoutSeconds", 0), "timeoutSeconds")
    rejected(lambda rules: rules["evaluation"]["market"].__setitem__("concurrency", True), "concurrency")


def test_selected_entry_with_classification_ids_carries_original_and_media() -> None:
    """一级给的事件编号是 m0001 这类分类编号；select 产出的条目要带上自己的编号表，原文与媒体报道才对得上。"""
    from neckline.screening.classify import EVENT_TYPES, CompanyResult
    from neckline.screening.select import select

    flash = _message(1, 18, text="甲公司签下 2 亿元订单，媒体称是公司历史最大单。")
    notice = _message(2, 19, source="cninfo-announcement", title="甲公司：关于签订重大合同的公告",
                      text="甲公司（300001）公告：关于签订重大合同的公告", url="http://static.cninfo.com.cn/x/1.PDF")
    unrelated = _message(3, 20, text="乙公司的消息，不属于本条。")
    by_id = {"m0001": flash, "m0002": notice, "m0003": unrelated}
    result = CompanyResult(company="300001.SZ", message_ids=["m0001", "m0002"],
                           events=[_event(["m0001", "m0002"], amount_yuan=200_000_000)], mentions=[])
    rules = {"relation": {"include": ["主体"]}, "freshness": {"include": ["新"]}, "direction": {"include": ["利好"]},
             "channels": {"exclude": [], "requireAmount": []}, "minimumAmountYuan": {},
             "kinds": {"include": ["订单中标"], "judge": [],
                       "exclude": [kind for kind in EVENT_TYPES if kind != "订单中标"]}}
    (entry,) = select(by_id, {"300001.SZ": result}, rules).candidates
    assert set(entry.by_id) == {"m0001", "m0002"}

    package = _build(entry)
    assert "主事件没有对上消息" not in package["gaps"]
    assert [m["ref"] for m in package["messages"]] == ["doc_1@1", "doc_2@1"]
    assert package["original"]["documents"] and package["original"]["media"]
    assert "乙公司" not in json.dumps(package, ensure_ascii=False)


def test_single_quarter_forecast_is_used_as_the_quarter_without_subtracting() -> None:
    """预告期间是 7–9 月时，区间本身就是第三季度，不能再减半年累计；上年同期也不拿 TuShare 的累计数顶替。"""
    from neckline.evaluation.prepare import _base, _current_quarter

    line = {"state": "盈利", "current": {"low": 30_000_000.0, "high": 40_000_000.0}, "prior": None,
            "yoy": {"low": None, "high": None}}
    empty = {"state": None, "current": {"low": None, "high": None}, "prior": None, "yoy": {"low": None, "high": None}}
    figures = {"period": "2026年7月1日至2026年9月30日", "net_profit": line, "deducted": empty}
    income = [_income("20260630", "20260820", 500, 60_000_000), _income("20250930", "20251025", 600, 90_000_000)]
    quarter = _current_quarter("forecast", "q3", figures, income, [], 2026)
    assert (quarter["netProfit"]["low"], quarter["netProfit"]["high"]) == (30_000_000.0, 40_000_000.0)
    assert quarter["netProfit"]["source"] == "pdf"
    assert _base("forecast", "q3", 2026, figures, income)["netProfit"]["value"] is None

    cumulative = dict(figures, period="2026年1月1日至2026年9月30日")
    subtracted = _current_quarter("forecast", "q3", cumulative, income, [], 2026)
    assert (subtracted["netProfit"]["low"], subtracted["netProfit"]["high"]) == (-30_000_000.0, -20_000_000.0)

    odd = dict(figures, period="2026年4月1日至2026年9月30日")
    assert _current_quarter("forecast", "q3", odd, income, [], 2026)["netProfit"]["missing"]


def test_odd_first_quarter_forecast_period_is_left_missing() -> None:
    from neckline.evaluation.prepare import _current_quarter

    line = {"state": "盈利", "current": {"low": 1.0, "high": 2.0}, "prior": None, "yoy": {"low": None, "high": None}}
    empty = {"state": None, "current": {"low": None, "high": None}, "prior": None, "yoy": {"low": None, "high": None}}
    odd = {"period": "2026年2月1日至2026年3月31日", "net_profit": line, "deducted": empty}
    assert _current_quarter("forecast", "q1", odd, [], [], 2026)["netProfit"]["missing"]
    normal = dict(odd, period="2026年1月1日至2026年3月31日")
    assert _current_quarter("forecast", "q1", normal, [], [], 2026)["netProfit"]["low"] == 1.0
