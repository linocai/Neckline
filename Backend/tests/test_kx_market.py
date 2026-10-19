"""KX 看板的行情：麦蕊批量取数与 60 秒缓存、TuShare 日线统计每天一次，以及各派生字段。"""
from __future__ import annotations

import dataclasses
import logging
from datetime import date

import httpx
import pytest

from neckline.kx import store
from neckline.kx.days import board_clock, trading_days_ending
from neckline.kx.board import build_board
from neckline.kx.quotes import BATCH_SIZE, Quote
from neckline.morning import db
from tests.kx_support import LICENCE, TUSHARE_TOKEN, at, build, make_mairui, make_stats, quote_values, run


def _board(harness):
    return run(build_board(harness.services, harness.path))


def _rows(board):
    return {row["code"]: row for row in board["rows"]}


def test_quotes_are_fetched_in_groups_of_twenty_and_cached_for_sixty_seconds(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=45)
    first = _board(harness)
    assert BATCH_SIZE == 20
    assert [len(group) for group in harness.mairui.calls] == [20, 20, 5]
    assert sorted(code for group in harness.mairui.calls for code in group) == sorted(_rows(first))
    harness.clock.advance(30)
    _board(harness)
    harness.clock.advance(29)
    _board(harness)
    assert len(harness.mairui.calls) == 3  # 60 秒内重复请求看板不重复调用麦蕊
    harness.clock.advance(2)
    again = _board(harness)
    assert len(harness.mairui.calls) == 6
    assert again["session"]["quoteTime"] == "2026-10-20T10:29:58+08:00"


def test_one_failed_group_only_marks_its_own_rows_and_is_retried_after_ten_seconds(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=45)
    harness.mairui.fail_groups = {"000000"}  # 第一组整组失败
    board = _board(harness)
    rows = _rows(board)
    first_group = {code for code in harness.mairui.calls[0]}
    assert len(first_group) == 20
    for code, row in rows.items():
        if code in first_group:
            assert row["quoteStatus"] == "missing" and row["price"] is None and row["note"] == "行情未取得"
            assert row["change5dPercent"] is None and row["averages"] == {"ma5": None, "ma10": None, "ma20": None}
        else:
            assert row["quoteStatus"] == "ok" and row["price"] is not None
    assert board["gaps"] == ["20 只行情未取得：麦蕊限制了当前请求来源或频率，请稍后再试。"]
    assert LICENCE not in str(board)
    assert len(harness.mairui.calls) == 3
    harness.clock.advance(9)
    _board(harness)
    assert len(harness.mairui.calls) == 3  # 失败组 10 秒内不重试，其余组仍在 60 秒缓存里
    harness.clock.advance(2)
    harness.mairui.fail_groups = set()
    recovered = _board(harness)
    assert len(harness.mairui.calls) == 4 and harness.mairui.calls[-1] == harness.mairui.calls[0]  # 只重试失败的那一组
    assert all(row["quoteStatus"] == "ok" for row in recovered["rows"]) and recovered["gaps"] == []


def test_a_missing_or_stale_quote_is_reported_not_estimated(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=25)
    harness.mairui.drop_codes = {"000001"}
    harness.mairui.stale_codes = {"000002": "2026-10-16"}  # 停牌：报价还停在上周五
    rows = _rows(_board(harness))
    assert rows["000001"]["quoteStatus"] == "missing" and rows["000001"]["note"] == "没有取得这只的行情"
    stale = rows["000002"]
    assert stale["quoteStatus"] == "stale" and stale["note"] == "报价日期是 2026-10-16，不是 2026-10-20"
    assert stale["price"] == quote_values(2)["p"] and stale["changePercent"] is not None
    assert stale["averages"] == {"ma5": None, "ma10": None, "ma20": None} and stale["totalMvYi"] is None
    assert stale["volumeRatio"] is None and stale["change5dPercent"] is None  # 基准日对不上，不硬算
    assert stale["quoteTime"] == "2026-10-16T10:29:58+08:00"


def test_quote_fields_and_derived_values(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=25)
    rows = _rows(_board(harness))
    for number, (code, name, industry) in enumerate(harness.kx.members):
        raw, row = quote_values(number), rows[code]
        assert (row["name"], row["industry"], row["quoteStatus"], row["note"]) == (name, industry, "ok", None)
        assert row["price"] == raw["p"] and row["previousClose"] == raw["yc"] and row["open"] == raw["o"]
        assert row["high"] == raw["h"] and row["low"] == raw["l"]
        assert row["changePercent"] == raw["pc"] and row["changeAmount"] == raw["ud"]
        assert row["amplitudePercent"] == raw["zf"] and row["turnoverRatePercent"] == raw["tr"]
        assert row["turnoverYi"] == pytest.approx(raw["cje"] / 1e8, abs=1e-4)
        assert row["volumeLots"] == raw["v"]
        assert row["openChangePercent"] == pytest.approx((raw["o"] / raw["yc"] - 1) * 100, abs=0.006)
        assert row["rangePosition"] == pytest.approx((raw["p"] - raw["l"]) / (raw["h"] - raw["l"]), abs=1e-4)
        assert row["pullbackFromHighPercent"] == pytest.approx((raw["p"] / raw["h"] - 1) * 100, abs=0.006)
        assert row["pullbackFromHighPercent"] <= 0 and 0 <= row["rangePosition"] <= 1
        assert row["quoteTime"] == "2026-10-20T10:29:58+08:00"


def test_a_flat_day_has_no_range_position_and_a_bad_range_is_called_out(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=3)
    original = harness.mairui.handler

    def tampered(request):
        rows = original(request).json()
        rows[0].update(h=rows[0]["p"], l=rows[0]["p"], o=rows[0]["p"])  # 一字板：最高 = 最低
        rows[1].update(h=rows[1]["l"] - 1)                              # 最高价低于最低价
        return httpx.Response(200, json=rows)

    harness.services.mairui = make_mairui(tampered, harness.clock)
    rows = _rows(_board(harness))
    assert rows["000000"]["rangePosition"] is None and rows["000000"]["pullbackFromHighPercent"] == 0.0
    assert rows["000001"]["rangePosition"] is None and rows["000001"]["pullbackFromHighPercent"] is None
    assert rows["000001"]["note"] == "价格区间不一致，没有计算区间位置"


def test_only_the_needed_quote_fields_are_kept():
    assert {field.name for field in dataclasses.fields(Quote)} == {
        "price", "previous_close", "open", "high", "low", "change_percent", "change_amount", "amplitude_percent",
        "turnover_rate", "amount_yuan", "volume_lots", "at"}


# ------------------------------------------------------------------ TuShare 日线统计

def test_daily_statistics_are_fetched_once_a_day_and_cached_in_the_database(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=45)
    _board(harness)
    assert harness.tushare.count("daily") == 1 and harness.tushare.count("daily_basic") == 1
    daily = next(item for item in harness.tushare.calls if item["api_name"] == "daily")["params"]
    codes = daily["ts_code"].split(",")
    assert len(codes) == 45 and codes[0] == "000000.SZ" and codes[5] == "600005.SH"
    assert daily["start_date"] == "20260914"  # 到基准日为止的 20 个交易日里最早的一天
    assert daily["end_date"] == "20261019"  # 行情所在交易日 10/20 的前一个交易日
    basic = next(item for item in harness.tushare.calls if item["api_name"] == "daily_basic")["params"]
    assert basic == {"trade_date": "20261019"}
    for _ in range(5):  # 反复请求、跨过 60 秒缓存，TuShare 仍然只取一次
        harness.clock.advance(70)
        _board(harness)
    assert harness.tushare.count("daily") == 1 and harness.tushare.count("daily_basic") == 1
    # 重启（换一个进程内服务）也不重取：缓存在库里
    def forbidden(request):
        raise AssertionError("不该再取 TuShare")

    harness.services.stats = make_stats(forbidden, harness.clock)
    _board(harness)
    with db.read(harness.path) as conn:
        assert conn.execute("SELECT count(*) FROM kx_daily_stats").fetchone()[0] == 2
    # 下一个完整交易日才会再取一次
    harness.clock.set(at("2026-10-21"))
    harness.mairui.day = "2026-10-21"
    harness.services.stats = make_stats(harness.tushare.handler, harness.clock)
    _board(harness)
    assert harness.tushare.count("daily") == 2 and harness.tushare.count("daily_basic") == 2
    assert harness.tushare.calls[-1]["params"]["trade_date"] == "20261020"


def test_daily_statistics_derivations(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=25)
    board = _board(harness)
    assert board["stats"]["asOf"] == "2026-10-19"
    rows = _rows(board)
    for number, (code, _name, _industry) in enumerate(harness.kx.members):
        raw, row = quote_values(number), rows[code]
        yc, price = raw["yc"], raw["p"]
        # 假 TuShare：最后一个完整交易日的收盘价 = 昨收，往前每个交易日低 0.05
        assert row["change5dPercent"] == pytest.approx((price / (yc - 0.05 * 4) - 1) * 100, abs=0.006)
        assert row["change20dPercent"] == pytest.approx((price / (yc - 0.05 * 19) - 1) * 100, abs=0.006)
        for name, back in (("ma5", 2.0), ("ma10", 4.5), ("ma20", 9.5)):
            average = yc - 0.05 * back
            assert row["averages"][name]["value"] == pytest.approx(average, abs=1e-4)
            expected = "above" if price > round(average, 4) else "below" if price < round(average, 4) else "flat"
            assert row["averages"][name]["position"] == expected
        average_amount = (number + 1) * 1e7
        assert row["avgTurnover20Yi"] == pytest.approx(average_amount / 1e8, abs=1e-4)
        assert row["volumeRatio"] == pytest.approx(raw["cje"] / average_amount, abs=0.006)
        # 总市值 = 前一交易日市值 × 实时价 ÷ 昨收（万元 → 亿元）
        assert row["totalMvYi"] == pytest.approx((100000 + number * 1000) * 1e4 * price / yc / 1e8, abs=0.006)
        assert row["circMvYi"] == pytest.approx((60000 + number * 500) * 1e4 * price / yc / 1e8, abs=0.006)
    assert rows["000003"]["averages"]["ma5"]["position"] == "above"  # 现价 = 昨收 > 5 日均线
    assert any(row["averages"]["ma5"]["position"] == "below" for row in rows.values())


def test_missing_daily_bars_leave_blanks_instead_of_estimates(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=25)
    first_day_of_window = trading_days_ending(date(2026, 10, 19), 20)[0].strftime("%Y%m%d")
    assert first_day_of_window == "20260914"
    harness.tushare.skip_bar = {"600010.SH": "20261015", "600011.SH": first_day_of_window}
    rows = _rows(_board(harness))
    cut = rows["600010"]  # 缺 10/15：落在 5 日、10 日、20 日窗口里
    assert cut["averages"] == {"ma5": None, "ma10": None, "ma20": None} and cut["avgTurnover20Yi"] is None
    assert cut["volumeRatio"] is None and cut["change20dPercent"] is not None
    assert cut["change5dPercent"] is not None  # 5 个交易日前的那一天（10/13）在
    assert cut["note"] == "日线数据不全，部分统计没有计算"
    assert cut["totalMvYi"] is not None  # 市值是另一个接口，不受影响
    first_day = rows["600011"]  # 只缺 20 个交易日里最早的一天
    assert first_day["change20dPercent"] is None and first_day["averages"]["ma20"] is None
    assert first_day["averages"]["ma10"] is not None and first_day["averages"]["ma5"] is not None
    assert rows["000000"]["note"] is None


def test_a_tushare_failure_is_reported_and_retried_later_not_on_every_request(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    harness = build(tmp_path, monkeypatch, count=25)
    harness.tushare.fail = True
    board = _board(harness)
    assert harness.tushare.count("daily") == 1 and harness.tushare.count("daily_basic") == 1
    assert board["gaps"] == ["TuShare 行情频率超限，这次没有重试。"] and board["stats"]["asOf"] is None
    row = board["rows"][0]
    assert row["quoteStatus"] == "ok" and row["price"] is not None  # 实时部分不受影响
    assert row["change5dPercent"] is None and row["totalMvYi"] is None and row["volumeRatio"] is None
    assert row["averages"] == {"ma5": None, "ma10": None, "ma20": None}
    harness.clock.advance(200)
    _board(harness)
    assert harness.tushare.count("daily") == 1  # 5 分钟内不重试
    harness.clock.advance(120)
    harness.tushare.fail = False
    recovered = _board(harness)
    assert harness.tushare.count("daily") == 2 and recovered["gaps"] == [] and recovered["stats"]["asOf"] == "2026-10-19"
    assert recovered["rows"][0]["change5dPercent"] is not None
    assert TUSHARE_TOKEN not in caplog.text and TUSHARE_TOKEN not in str(board)


def test_session_and_statistics_base_follow_the_trading_calendar(tmp_path, monkeypatch):
    # 周六：行情是周五收盘，统计基准日是周四
    harness = build(tmp_path, monkeypatch, count=5, now=at("2026-10-17", "11:00"), day="2026-10-16")
    board = _board(harness)
    assert board["session"] == {"tradeDate": "2026-10-16", "inSession": False, "refreshSeconds": None,
                                "quoteTime": "2026-10-16T10:29:58+08:00"}
    assert board["stats"]["asOf"] == "2026-10-15"
    assert board["picks"]["referenceDate"] == "2026-10-19"  # 非交易日：高亮参考日是下一个交易日
    assert all(row["quoteStatus"] == "ok" for row in board["rows"])
    # 周一 09:20：行情日还是上周五（09:30 才算当天）；盘中刷新窗口已经开始
    (tmp_path / "early").mkdir()
    early = build(tmp_path / "early", monkeypatch, count=5, now=at("2026-10-19", "09:20"), day="2026-10-16")
    board = _board(early)
    assert board["session"]["tradeDate"] == "2026-10-16" and board["session"]["inSession"] is True
    assert board["session"]["refreshSeconds"] == 60 and board["stats"]["asOf"] == "2026-10-15"


@pytest.mark.parametrize("moment,in_session", [
    ("09:14", False), ("09:15", True), ("11:45", True), ("13:00", True), ("15:04", True), ("15:05", False), ("20:00", False)])
def test_in_session_window(moment, in_session):
    assert board_clock(at("2026-10-20", moment)).in_session is in_session
    assert board_clock(at("2026-10-24", moment)).in_session is False  # 周六


def test_the_board_works_on_a_database_with_no_picks_and_no_credentials_for_tushare(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=5)
    harness.services.stats = make_stats(harness.tushare.handler, harness.clock, token=None)
    board = _board(harness)
    assert board["picks"] == {"referenceDate": "2026-10-20", "lastPickAt": None}
    assert board["gaps"] == ["未配置 TuShare，没有日线统计。"]
    assert all(row["highlight"] == "normal" and row["lastPickedAt"] is None for row in board["rows"])
    with db.read(harness.path) as conn:
        assert store.latest_picks(conn) == ({}, None)
