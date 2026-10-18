"""股票池、TuShare 收盘数据和麦蕊盘中数据。全部走假 transport，不联网。"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

import httpx
import pytest

from neckline.market.calendar import intraday_session_date
from neckline.market.mairui import Mairui
from neckline.market.overview import overview
from neckline.market.pool_query import _load_pool, lookup
from neckline.market.services import Services
from neckline.market.tushare_daily import BASIS, TuShareDaily, cache_phase
from neckline.market.util import SHANGHAI, silence_http_logs
from neckline.morning import db
from neckline.screening.pool import Company, CompanyPool

TOKEN = "test-tushare-token"
LICENCE = "test-licence"
NOW = datetime(2026, 10, 16, 10, 0, tzinfo=SHANGHAI)


def _at(day: str, hm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:00+08:00")


def _pool(*rows: tuple[str, str, tuple[str, ...]]) -> CompanyPool:
    return CompanyPool([Company(code, name, aliases) for code, name, aliases in rows])


def _frame(fields: list[str], rows: list[list]) -> dict:
    return {"code": 0, "data": {"fields": fields, "items": rows, "has_more": False}}


def _daily_rows(days: list[tuple]) -> list[list]:
    return [["300002.SZ", day, close, pre, pct, high, low, amount] for day, close, pre, high, low, amount, pct in days]


def _service(handler, now=NOW, token=TOKEN, licence=LICENCE, pool=None) -> tuple[Services, list]:
    seen: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    services = Services(
        pool=pool or _load_pool(),
        tushare=TuShareDaily(token, client=client, now=lambda: now),
        mairui=Mairui(licence, client=client, now=lambda: now),
        now=lambda: now,
    )
    return services, seen


def _run(coro):
    return asyncio.run(coro)


def test_pool_lookup_accepts_code_name_and_alias_and_stops_at_five():
    assert len(_load_pool()) == 1089
    for query in ("300002", "300002.SZ", "sz300002", " 神州泰岳 ", "北京神州泰岳软件股份有限公司"):
        assert lookup(query) == {"inPool": True, "code": "300002.SZ", "name": "神州泰岳"}
    for query in ("000001", "600000.SH", "300002.SH", "SH300002"):
        assert lookup(query) == {"inPool": False, "message": "不在 K10 股票池"}
    for query in ("平安银行", "", "不存在的公司"):
        missed = lookup(query)
        assert missed["inPool"] is False and missed["message"].startswith("不在 K10 股票池（按名称没找到")
    shared = _pool(
        ("300001.SZ", "甲公司", ("重名",)),
        ("300002.SZ", "乙公司", ("重名",)),
    )
    found = lookup("重名", shared)
    assert found["inPool"] is True and [item["code"] for item in found["candidates"]] == ["300001.SZ", "300002.SZ"]
    assert "truncated" not in found
    many = _pool(*((f"30000{index}.SZ", f"公司{index}", ("很多",)) for index in range(1, 7)))
    crowded = lookup("很多", many)
    assert len(crowded["candidates"]) == 5 and crowded["truncated"] is True
    assert not hasattr(lookup, "all") and not any(name.startswith(("list", "all")) for name in dir(_load_pool.__module__) if False)
    import neckline.market.pool_query as pool_query
    public = [name for name in dir(pool_query) if not name.startswith("_")]
    assert "lookup" in public and "PoolIndex" in public
    assert not any(name.lower().startswith(("list", "all", "dump", "export")) for name in public)


def test_session_date_follows_the_trading_clock():
    assert intraday_session_date(_at("2026-10-16", "10:00")) == datetime(2026, 10, 16).date()
    assert intraday_session_date(_at("2026-10-16", "09:30")) == datetime(2026, 10, 16).date()
    assert intraday_session_date(_at("2026-10-16", "15:01")) == datetime(2026, 10, 16).date()
    assert intraday_session_date(_at("2026-10-16", "09:00")) == datetime(2026, 10, 15).date()
    assert intraday_session_date(_at("2026-10-18", "10:00")) == datetime(2026, 10, 16).date()
    assert intraday_session_date(_at("2026-10-01", "10:00")) == datetime(2026, 9, 30).date()
    assert intraday_session_date(_at("2026-10-10", "10:00")) == datetime(2026, 10, 9).date()
    assert cache_phase(_at("2026-10-16", "14:59")).endswith("#session")
    assert cache_phase(_at("2026-10-16", "15:00")).endswith("#close")
    assert cache_phase(_at("2026-10-18", "16:00")).endswith("#session")


def _twenty() -> list[tuple]:
    return [(f"202601{day:02d}", 11, 10, 12, 10, 200000, 10) for day in range(1, 21)]


def _basic(day="20260120", mv=500000, circ=250000):
    return ["300002.SZ", day, 3.5, 20.5, 2.5, mv, circ]


def _flow(day="20260120", amount=3000):
    return ["300002.SZ", day, amount]


def _tushare_app(daily, basic, flow, *, flow_code=0, basic_code=0):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["token"] == TOKEN
        assert request.url.host == "api.tushare.pro"
        name = body["api_name"]
        if name == "daily":
            fields = ["ts_code", "trade_date", "close", "pre_close", "pct_chg", "high", "low", "amount"]
            return httpx.Response(200, json=_frame(fields, daily))
        if name == "daily_basic":
            fields = ["ts_code", "trade_date", "turnover_rate", "pe_ttm", "pb", "total_mv", "circ_mv"]
            if basic_code:
                return httpx.Response(200, json={"code": basic_code, "msg": TOKEN, "data": None})
            return httpx.Response(200, json=_frame(fields, basic))
        fields = ["ts_code", "trade_date", "net_mf_amount"]
        if flow_code:
            return httpx.Response(200, json={"code": flow_code, "msg": TOKEN, "data": None})
        return httpx.Response(200, json=_frame(fields, flow))

    return handler


def test_tushare_units_window_averages_and_cache():
    services, seen = _service(_tushare_app(_daily_rows(_twenty()), [_basic()], [_flow()]))

    async def once():
        first = await services.tushare.load("300002.SZ")
        calls = len(seen)
        second = await services.tushare.load("300002.SZ")
        return first, second, calls

    (payload, gaps), again, calls = _run(once())
    assert calls == 3 and len(seen) == 3 and again == (payload, gaps)
    assert {json.loads(item.content)["api_name"] for item in seen} == {"daily", "daily_basic", "moneyflow"}
    assert payload["tradeDate"] == "2026-01-20"
    assert payload["close"] == 11
    assert payload["changePercent"] == 10
    assert payload["turnoverYi"] == 2
    assert payload["turnoverRate"] == 3.5
    window = payload["window20"]
    assert window["changePercent"] == 10
    assert window["averageTurnoverYi"] == 2
    assert window["averageAmplitudePercent"] == 20
    assert window["missingDays"] == 0 and window["note"] is None
    assert payload["averages"]["ma5"] == {"value": 11, "position": "持平"}
    assert payload["averages"]["ma20"]["position"] == "持平"
    assert payload["valuation"]["totalMvYi"] == 50
    assert payload["valuation"]["circMvYi"] == 25
    assert payload["valuation"]["peTtm"] == 20.5 and payload["valuation"]["pb"] == 2.5
    assert payload["valuation"]["tradeDate"] == "2026-01-20" and payload["valuation"]["note"] is None
    assert payload["moneyflow"] == {"tradeDate": "2026-01-20", "netInflowYi": 0.3, "basis": BASIS, "note": None}
    assert BASIS == "TuShare moneyflow 净流入额"
    assert "主力" not in json.dumps(payload, ensure_ascii=False)
    assert not gaps


def test_short_history_nulls_the_twenty_day_block_and_long_averages():
    days = [(f"2026010{day}", 10, 10, 11, 9, 100000, 0) for day in range(1, 8)]
    days.append(("20260108", 12, 10, 13, 9, 100000, 20))
    services, _seen = _service(_tushare_app(_daily_rows(days), [_basic("20260108")], [_flow("20260108")]))
    payload, _gaps = _run(services.tushare.load("300002.SZ"))
    assert payload["window20"]["changePercent"] is None
    assert payload["window20"]["averageTurnoverYi"] is None
    assert payload["window20"]["averageAmplitudePercent"] is None
    assert payload["window20"]["missingDays"] == 12
    assert "缺12天" in payload["window20"]["note"]
    assert payload["averages"]["ma5"] == {"value": 10.4, "position": "上方"}
    assert payload["averages"]["ma10"] is None and payload["averages"]["ma20"] is None
    assert "10日" in payload["averageNote"] and "20日" in payload["averageNote"]


def test_mismatched_dates_are_labeled_and_not_mixed():
    basic = [_basic("20260119"), _basic("20260120", mv=1, circ=1)]
    basic[1][1] = "20260121"
    services, _seen = _service(_tushare_app(_daily_rows(_twenty()), basic, [_flow("20260119", 999999)]))
    payload, gaps = _run(services.tushare.load("300002.SZ"))
    assert payload["close"] == 11 and payload["turnoverRate"] is None
    assert payload["valuation"]["totalMvYi"] is None and payload["valuation"]["peTtm"] is None
    assert payload["valuation"]["tradeDate"] == "2026-01-21"
    assert "不一致，未混用" in payload["valuation"]["note"]
    assert payload["moneyflow"]["netInflowYi"] is None
    assert payload["moneyflow"]["tradeDate"] == "2026-01-19"
    assert "不一致，未混用" in payload["moneyflow"]["note"]
    assert gaps == []


def test_a_failed_endpoint_drops_only_that_block(caplog):
    caplog.set_level(logging.DEBUG)
    silence_http_logs()
    services, seen = _service(_tushare_app(_daily_rows(_twenty()), [_basic()], [_flow()], flow_code=40203))

    async def load_twice():
        first = await services.tushare.load("300002.SZ")
        count = len(seen)
        second = await services.tushare.load("300002.SZ")
        return first, second, count

    (payload, gaps), again, count = _run(load_twice())
    assert count == 3 and len(seen) == 3 and again[0]["close"] == payload["close"]
    assert payload["close"] == 11 and payload["moneyflow"] is None
    assert payload["valuation"]["peTtm"] == 20.5
    assert gaps == ["TuShare 行情频率超限，这次没有重试。"]
    assert TOKEN not in json.dumps(payload, ensure_ascii=False) and TOKEN not in "".join(gaps) and TOKEN not in caplog.text


def test_tushare_outage_does_not_echo_the_token_or_url(caplog):
    caplog.set_level(logging.DEBUG)
    silence_http_logs()

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["api_name"] == "daily":
            raise httpx.ConnectError("https://api.tushare.pro " + TOKEN)
        return httpx.Response(200, json=_frame(["ts_code", "trade_date", "net_mf_amount"], []))

    services, seen = _service(handler)
    with pytest.raises(Exception) as raised:
        _run(services.tushare.load("300002.SZ"))
    assert "暂时连不上" in str(raised.value)
    assert TOKEN not in str(raised.value) and "tushare.pro" not in str(raised.value) and TOKEN not in caplog.text
    assert len(seen) == 3


def _minute(clock: str, amount=10000, day="2026-10-16") -> dict:
    return {"t": f"{day} {clock}:00", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 1, "a": amount, "pc": 1, "sf": 0}


def _quote(day="2026-10-16 10:05:00") -> dict:
    return {"t": day, "p": 10.5, "yc": 10, "o": 10, "h": 11, "l": 9, "pc": 5, "cje": 150000000, "tr": 2.5, "v": 1}


def _mairui(quote, bars, *, pro_body=None, bar_body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert LICENCE in request.url.path
        if pro_body is not None and "/pro/" in request.url.path:
            return httpx.Response(200, text=pro_body)
        if "/real/" in request.url.path:
            return httpx.Response(200, json=quote)
        if bar_body is not None:
            return httpx.Response(200, text=bar_body)
        return httpx.Response(200, json=bars)

    return handler


def test_mairui_quote_and_minute_aggregation(caplog):
    caplog.set_level(logging.DEBUG)
    silence_http_logs()
    bars = [_minute(f"09:{minute:02d}") for minute in range(31, 36)]
    bars.extend([_minute("09:31"), _minute("09:20"), _minute("09:31", day="2026-10-15"), {"t": "bad"}])
    services, seen = _service(_mairui(_quote(), bars))

    async def load():
        first = await services.mairui.block("300002.SZ", NOW.date())
        count = len(seen)
        second = await services.mairui.block("300002.SZ", NOW.date())
        return first, count, len(seen)

    block, count, after = _run(load())
    assert count == 2 and after == 2
    assert block["quote"]["latestPrice"] == 10.5
    assert block["quote"]["turnoverYi"] == 1.5
    assert block["quote"]["turnoverRate"] == 2.5
    assert block["quote"]["note"] is None
    assert block["series"]["note"] == "由一分钟数据聚合"
    assert block["series"]["rows"] == [["09:35", 10, 11, 9, 10.5, 5]]
    assert block["series"]["rejectedRows"] == 4
    assert "test-licence" not in json.dumps(block, ensure_ascii=False)
    assert LICENCE not in caplog.text and "mairuiapi.com" not in caplog.text
    stale, _seen = _service(_mairui(_quote("2026-10-15 14:00:00"), [_minute("10:00", day="2026-10-15")]), now=NOW)
    stale_block = _run(stale.mairui.block("300002.SZ", datetime(2026, 10, 15).date()))
    assert stale_block["quote"]["note"] == "不是实时行情"


@pytest.mark.parametrize("code,text", [
    (101, "麦蕊当日调用额度已用完。"),
    (102, "麦蕊证书无效，请检查服务器配置。"),
    (103, "麦蕊限制了当前请求来源或频率，请稍后再试。"),
])
def test_mairui_errors_are_chinese_and_hide_the_licence(code, text, caplog):
    caplog.set_level(logging.DEBUG)
    silence_http_logs()
    body = f"{code}: {LICENCE} https://api.mairuiapi.com/secret"

    def handler(request: httpx.Request) -> httpx.Response:
        assert LICENCE in request.url.path
        return httpx.Response(200, text=body)

    services, _seen = _service(handler)
    block = _run(services.mairui.block("300002.SZ", NOW.date()))
    blob = json.dumps(block, ensure_ascii=False) + caplog.text
    assert text in blob
    assert LICENCE not in blob and "mairuiapi" not in blob and "http" not in blob
    assert block["quote"] is None and block["series"] is None


def test_pro_108_falls_back_to_five_minutes():
    bars = [_minute("09:35"), _minute("09:40")]
    services, seen = _service(_mairui(_quote(), bars, pro_body=f"108: {LICENCE}"))
    block = _run(services.mairui.block("300002.SZ", NOW.date()))
    assert any("/pro/" in item.url.path for item in seen)
    assert any("/history/" in item.url.path and "/pro/" not in item.url.path for item in seen)
    assert block["series"]["note"] == "麦蕊五分钟分时"
    assert block["series"]["rows"][0][0] == "09:35"
    assert LICENCE not in json.dumps(block, ensure_ascii=False)
    with pytest.raises(Exception, match="只接受创业板"):
        _run(services.mairui.block("600000.SZ", NOW.date()))


def test_overview_uses_the_morning_entry_and_isolates_failures(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    silence_http_logs()
    path = tmp_path / "v4.db"
    db.init_schema(path)
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    content = {"entries": [{"rank": 2, "companyCode": "300002", "companyName": "神州泰岳",
                            "headline": "订单落地", "reason": "公告写了金额",
                            "sources": [{"documentId": "doc_test"}]}], "review": {}}
    with db.write(path) as conn:
        conn.execute(
            "INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("morning-2026-10-15", "2026-10-15", "2026-10-14T07:00:00+00:00", "2026-10-15T00:00:00+00:00",
             "available", "screening-v1", "0" * 64, "2026-10-15T00:00:00+00:00", "2026-10-15T00:02:00+00:00",
             None, None, None, "{}", json.dumps(content)))
        conn.execute(
            "INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("morning-2026-10-16", "2026-10-16", "2026-10-15T07:00:00+00:00", "2026-10-16T00:00:00+00:00",
             "failed", "screening-v1", "0" * 64, "2026-10-16T00:00:00+00:00", None,
             "2026-10-16T00:03:00+00:00", "没出成", None, "{}", None))
    def mixed(request: httpx.Request) -> httpx.Response:
        if "tushare" in request.url.host:
            return _tushare_app(_daily_rows(_twenty()), [_basic()], [_flow()])(request)
        if "/real/" in request.url.path:
            return httpx.Response(200, json=_quote())
        return httpx.Response(200, text=f"101: {LICENCE}")

    services, seen = _service(mixed)
    result = _run(overview("神州泰岳", services=services))
    assert result["code"] == "300002.SZ" and result["name"] == "神州泰岳"
    assert result["morningReport"]["headline"] == "订单落地"
    assert result["morningReport"]["tradeDate"] == "2026-10-15"
    assert result["morningReport"]["documentIds"] == ["doc_test"]
    assert result["daily"]["close"] == 11
    assert result["intraday"]["quote"]["turnoverYi"] == 1.5
    assert result["intraday"]["series"] is None
    assert any("额度已用完" in item for item in result["gaps"])
    assert TOKEN not in json.dumps(result, ensure_ascii=False) and LICENCE not in json.dumps(result, ensure_ascii=False)
    assert TOKEN not in caplog.text and LICENCE not in caplog.text

    def daily_down(request: httpx.Request) -> httpx.Response:
        if "tushare" in request.url.host and json.loads(request.content)["api_name"] == "daily":
            return httpx.Response(200, json={"code": 1, "msg": TOKEN})
        if "tushare" in request.url.host:
            return httpx.Response(200, json=_frame(["ts_code", "trade_date", "net_mf_amount"], []))
        return _mairui(_quote(), [_minute("10:00")])(request)

    broken, _calls = _service(daily_down)
    failed = _run(overview("300002", services=broken))
    assert failed["daily"] is None and failed["intraday"]["series"]["note"] == "由一分钟数据聚合"
    assert any("没有返回可用的收盘数据" in item for item in failed["gaps"])
    assert TOKEN not in json.dumps(failed, ensure_ascii=False)


def test_outside_the_pool_makes_no_external_call():
    def forbid(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    services, seen = _service(forbid)
    outside = _run(overview("平安银行", services=services))
    ambiguous = _run(overview("重名", services=Services(
        pool=_pool(("300001.SZ", "甲", ("重名",)), ("300002.SZ", "乙", ("重名",))),
        tushare=services.tushare, mairui=services.mairui, now=services.now)))
    assert outside["inPool"] is False and outside["message"].startswith("不在 K10 股票池")
    assert ambiguous["candidates"] and seen == []
    session = intraday_session_date(_at("2026-10-18", "10:00"))
    recorded = {}

    def remember(request: httpx.Request) -> httpx.Response:
        if "/history/" in request.url.path or "/pro/" in request.url.path:
            recorded["st"] = request.url.params["st"]
        if "tushare" in request.url.host:
            return _tushare_app(_daily_rows(_twenty()), [_basic()], [_flow()])(request)
        if "/real/" in request.url.path:
            return httpx.Response(200, json=_quote("2026-10-16 15:00:00"))
        return httpx.Response(200, json=[_minute("10:00")])

    weekend, _seen = _service(remember, now=_at("2026-10-18", "10:00"))
    viewed = _run(overview("300002.SZ", services=weekend))
    assert session.isoformat() == "2026-10-16"
    assert viewed["intraday"]["sessionDate"] == "2026-10-16"
    assert recorded["st"].startswith("20261016")
    assert viewed["intraday"]["quote"]["note"] == "不是实时行情"
