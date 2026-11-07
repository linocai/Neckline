"""板块清单、日线和成分股材料。"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

import pytest

from neckline.morning import cli, db, stock_pool
from neckline.morning.replay import main as replay_main
from neckline.morning.replay import run_replay
from neckline.morning.report import load_config
from neckline.morning.sectors import (
    FETCH_CUTOFF, MEMBER_INTERVAL_SECONDS, board_material, capture_sector_payload, concept_list,
    concepts_content_sha256, concepts_refresh_due, fill_market_daily, member_facts, refresh_concepts,
    trading_days_ending,
)
from neckline.morning.stock_pool import store_rows
from neckline.evaluation.snapshot import main as snapshot_main

SECRET = "sector-token-do-not-print"
AS_OF = date(2026, 11, 4)
NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
POOL3 = ["300001.SZ", "300002.SZ", "300003.SZ"]
REGION_NAMES = [
    "上海国企改革", "上海自贸区", "中韩自贸区", "京津冀一体化", "共同富裕示范区", "天津自贸区", "广东自贸区",
    "新疆振兴", "横琴新区", "海南自贸区", "海峡两岸", "深圳国企改革", "福建自贸区", "粤港澳大湾区", "自由贸易港",
    "西部大开发", "长三角一体化", "雄安新区", "黑龙江自贸区",
]


@pytest.fixture
def path(tmp_path):
    target = tmp_path / "v4.db"
    db.init_schema(target)
    return target


def _rules():
    return load_config().rules["sectors"]["concepts"]


def _frame(fields, rows, *, more=False):
    return {"code": 0, "data": {"fields": fields, "items": rows, "has_more": more}}


def _codes(count: int) -> list[str]:
    return [f"30{i:04d}.SZ" for i in range(count)]


def _kept(name: str, members: list[str], pool: list[str]) -> list[str]:
    result = concept_list(
        [{"tsCode": "885001.TI", "name": name}],
        [{"tsCode": "885001.TI", "code": code} for code in members],
        pool, _rules())
    return [item["name"] for item in result["concepts"]]


def _concept_post(concepts, members, *, fail_after=None):
    seen = []

    def post(_url, payload, _timeout):
        assert payload["token"] == SECRET
        api = payload["api_name"]
        seen.append(api)
        if fail_after is not None and len(seen) >= fail_after:
            raise RuntimeError(f"down {SECRET}")
        if api == "ths_index":
            return _frame(["ts_code", "name", "count"],
                          [[row["ts_code"], row["name"], row["count"]] for row in concepts])
        if api == "ths_member":
            ts_code = payload["params"]["ts_code"]
            return _frame(["ts_code", "con_code", "con_name"],
                          [[ts_code, code, "名"] for code in members[ts_code]])
        raise AssertionError(api)

    return post, seen


def _store_pool(path, codes):
    rows = [{"code": code, "name": "甲", "fullname": "", "listDate": "2020-01-01", "aliases": [],
             "summary": None, "summarySource": "test"} for code in codes]
    with db.write(path) as conn:
        store_rows(conn, rows, now=NOW, source="test")


def _bar(code, day, *, pct, amount=1.5, limit_up=False, close=11, pre_close=10):
    return {"ts_code": code, "trade_date": day.strftime("%Y%m%d"), "open": 10, "high": close, "low": 9,
            "close": close, "pre_close": pre_close, "pct_chg": pct, "amount": amount, "limitUp": limit_up}


@pytest.mark.parametrize("name,kept", [
    ("沪深300成份股", False), ("沪深300", True),
    ("上证50样本股", False), ("上证样本", True),
    ("深股通", False), ("深股通概念", True),
    ("同花顺漂亮100", False), ("漂亮同花顺", True),
    ("中国AI 50", False), ("中国AI 500", True),
    ("高股息精选", False), ("高股息", True),
    ("融资融券", False), ("融资融券标的", True),
    ("证金持股", False), ("证金持股计划", True),
    ("国家大基金持股", False), ("国家大基金", True),
    ("举牌", False), ("举牌概念", True),
    ("中字头股票", False), ("中字头", True),
    ("一季报预增", False), ("预增热", True),
    ("次新股", False), ("新股", True),
    ("ST板块", False), ("摘帽", False), ("ST摘帽", True),
    ("专精特新", False), ("专精特新概念", True),
    ("股权转让(并购重组)", False), ("股权转让", True),
    *[(name, False) for name in REGION_NAMES],
    ("上海", True), ("人工智能", True),
])
def test_each_exclude_rule_and_its_near_miss(name, kept):
    assert bool(_kept(name, POOL3, POOL3)) is kept


def test_member_bounds_use_the_current_pool_and_ignore_st():
    wide = _codes(150)
    assert _kept("人工智能", wide, wide) == ["人工智能"]
    assert _kept("人工智能", _codes(151), _codes(151)) == []
    assert _kept("人工智能", POOL3[:2], POOL3[:2]) == []
    assert _kept("人工智能", POOL3, POOL3) == ["人工智能"]
    # 三只里有一只不在当期池子（ST 已离开），按池内两只剔除；四只里池内三只则保留。
    assert _kept("人工智能", POOL3, POOL3[:2]) == []
    assert _kept("人工智能", [*POOL3, "300009.SZ"], POOL3) == ["人工智能"]
    listed = concept_list(
        [{"tsCode": "885001.TI", "name": "人工智能"}],
        [{"tsCode": "885001.TI", "code": code} for code in ("300003", "300001.SZ", "300002")],
        ["300001", "300003.SZ", "300002.SZ"], _rules())
    assert listed["concepts"][0]["members"] == ["300001.SZ", "300002.SZ", "300003.SZ"]
    assert listed["concepts"][0]["count"] == 3
    ordered = concept_list(
        [{"tsCode": "885002.TI", "name": "乙"}, {"tsCode": "885001.TI", "name": "甲"}],
        [{"tsCode": "885001.TI", "code": code} for code in POOL3]
        + [{"tsCode": "885002.TI", "code": code} for code in POOL3],
        POOL3, _rules())
    assert [item["name"] for item in ordered["concepts"]] == sorted(["甲", "乙"])


def test_sector_config_is_required_and_has_no_defaults(tmp_path):
    rules = load_config().rules
    concepts = rules["sectors"]["concepts"]
    assert concepts["excludeNames"] == REGION_NAMES
    assert concepts["excludePatterns"] == [
        "成份股|样本股|股通$|^同花顺|^中国AI 50$|^高股息精选$",
        "^融资融券$|^证金持股$|^国家大基金持股$|^举牌$|^中字头股票$",
        "预增$", "次新", "^ST板块$|^摘帽$",
        r"^专精特新$|^股权转让\(并购重组\)$",
    ]
    assert concepts["maxMembers"] == 150 and concepts["minMembers"] == 3 and concepts["refreshDays"] == 7
    assert rules["sectors"]["market"] == {
        "lookbackDays": 40, "boardWindows": [1, 5, 20], "limitWindow": 5}
    assert MEMBER_INTERVAL_SECONDS == 0.35

    def rejected(mutate, message):
        copied = json.loads(json.dumps(rules))
        mutate(copied)
        target = tmp_path / "rules.json"
        target.write_text(json.dumps(copied), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            load_config(path=target)

    rejected(lambda item: item.pop("sectors"), "sectors")
    rejected(lambda item: item["sectors"].pop("concepts"), "sectors.concepts")
    rejected(lambda item: item["sectors"].pop("market"), "sectors.market")
    rejected(lambda item: item["sectors"]["concepts"].pop("excludeNames"), "excludeNames")
    rejected(lambda item: item["sectors"]["concepts"].pop("excludePatterns"), "excludePatterns")
    rejected(lambda item: item["sectors"]["concepts"].pop("refreshDays"), "refreshDays")
    rejected(lambda item: item["sectors"]["concepts"].__setitem__("maxMembers", True), "maxMembers")
    rejected(lambda item: item["sectors"]["concepts"].__setitem__("maxMembers", 2), "maxMembers")
    rejected(lambda item: item["sectors"]["concepts"]["excludePatterns"].__setitem__(0, "["), "excludePatterns")
    rejected(lambda item: item["sectors"]["market"].pop("lookbackDays"), "lookbackDays")
    rejected(lambda item: item["sectors"]["market"].pop("boardWindows"), "boardWindows")
    rejected(lambda item: item["sectors"]["market"].__setitem__("boardWindows", []), "boardWindows")
    rejected(lambda item: item["sectors"]["market"].__setitem__("boardWindows", [1, True]), "boardWindows")


def test_concept_refresh_replaces_everything_and_a_failure_keeps_the_previous(path, caplog):
    first, _seen = _concept_post(
        [{"ts_code": "885001.TI", "name": "人工智能", "count": 10},
         {"ts_code": "885002.TI", "name": "机器人", "count": 4}],
        {"885001.TI": ["300003.SZ", "300001.SZ", "600000.SH"], "885002.TI": ["301001.SZ"]})
    sleeps = []
    with db.write(path) as conn:
        result = refresh_concepts(conn, rules=_rules(), prefixes=("300", "301", "302"), now=NOW, post=first,
                                  environ={"TUSHARE_TOKEN": SECRET}, sleep=sleeps.append, interval_seconds=0.2)
    assert result.replaced and result.error is None and sleeps == [0.2, 0.2]
    with db.read(path) as conn:
        stored = db.read_sector_concepts(conn)
    assert stored["count"] == 2 and stored["error"] is None
    assert stored["sha256"] == concepts_content_sha256(stored["concepts"]) == result.sha256
    by_name = {item["name"]: item for item in stored["concepts"]}
    assert by_name["人工智能"]["memberCount"] == 10
    assert by_name["人工智能"]["members"] == ["300001.SZ", "300003.SZ"]
    assert by_name["机器人"]["members"] == ["301001.SZ"]

    failed, _seen = _concept_post(
        [{"ts_code": "885009.TI", "name": "全新", "count": 3}], {"885009.TI": ["300001.SZ"]}, fail_after=2)
    caplog.set_level(logging.ERROR)
    with db.write(path) as conn:
        again = refresh_concepts(conn, rules=_rules(), prefixes=("300", "301", "302"), now=NOW, post=failed,
                                 environ={"TUSHARE_TOKEN": SECRET}, sleep=lambda _seconds: None, interval_seconds=0)
    assert again.replaced is False and again.sha256 == result.sha256
    assert SECRET not in again.error and SECRET not in caplog.text
    with db.read(path) as conn:
        kept = db.read_sector_concepts(conn)
    assert [item["tsCode"] for item in kept["concepts"]] == ["885001.TI", "885002.TI"]
    assert kept["error"] and SECRET not in kept["error"]
    assert kept["refreshedAt"] == stored["refreshedAt"]


def test_a_failed_replace_rolls_back_the_whole_list(path, monkeypatch):
    post, _seen = _concept_post(
        [{"ts_code": "885001.TI", "name": "人工智能", "count": 3}], {"885001.TI": POOL3})
    with db.write(path) as conn:
        refresh_concepts(conn, rules=_rules(), prefixes=("300", "301", "302"), now=NOW, post=post,
                         environ={"TUSHARE_TOKEN": SECRET}, sleep=lambda _seconds: None, interval_seconds=0)

    def boom(conn, concepts, **_kwargs):
        conn.execute("DELETE FROM sector_concepts")
        raise RuntimeError("boom")

    monkeypatch.setattr("neckline.morning.sectors.db.replace_sector_concepts", boom)
    with pytest.raises(RuntimeError, match="boom"):
        with db.write(path) as conn:
            refresh_concepts(conn, rules=_rules(), prefixes=("300", "301", "302"), now=NOW, post=post,
                             environ={"TUSHARE_TOKEN": SECRET}, sleep=lambda _seconds: None, interval_seconds=0)
    with db.read(path) as conn:
        stored = db.read_sector_concepts(conn)
    assert [item["name"] for item in stored["concepts"]] == ["人工智能"]
    assert stored["error"] is None


def test_refresh_is_due_only_after_seven_days_and_collect_uses_that(path, monkeypatch):
    meta = {"refreshedAt": "2026-11-01T12:00:00+00:00"}
    assert concepts_refresh_due(None, today=date(2026, 11, 8), refresh_days=7) is True
    assert concepts_refresh_due({"refreshedAt": None}, today=date(2026, 11, 1), refresh_days=7) is True
    assert concepts_refresh_due(meta, today=date(2026, 11, 7), refresh_days=7) is False
    assert concepts_refresh_due(meta, today=date(2026, 11, 8), refresh_days=7) is True

    monkeypatch.setattr(cli, "MEMBER_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(cli, "run_collection", lambda *_args, **_kwargs: {"status": "completed"})
    post, seen = _concept_post(
        [{"ts_code": "885001.TI", "name": "人工智能", "count": 3}], {"885001.TI": ["300001.SZ"]})
    monkeypatch.setattr(stock_pool, "http_post", post)
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET)
    assert cli.main(["--db", str(path), "collect", "--now", "2026-11-01T12:00:00+00:00"]) == 0
    assert seen == ["ths_index", "ths_member"]
    seen.clear()
    assert cli.main(["--db", str(path), "collect", "--now", "2026-11-07T12:00:00+00:00"]) == 0
    assert seen == []
    assert cli.main(["--db", str(path), "refresh-concepts", "--now", "2026-11-02T12:00:00+00:00"]) == 0
    assert seen == ["ths_index", "ths_member"]
    with db.read(path) as conn:
        assert db.read_sector_concepts(conn)["count"] == 1


def test_collect_keeps_the_list_when_refresh_fails_and_the_command_hides_the_token(path, monkeypatch, capsys, caplog):
    monkeypatch.setattr(cli, "MEMBER_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(cli, "run_collection", lambda *_args, **_kwargs: {"status": "completed"})
    good, _seen = _concept_post(
        [{"ts_code": "885001.TI", "name": "人工智能", "count": 3}], {"885001.TI": ["300001.SZ"]})
    monkeypatch.setattr(stock_pool, "http_post", good)
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET)
    assert cli.main(["--db", str(path), "collect", "--now", "2026-11-01T12:00:00+00:00"]) == 0

    def broken(_url, payload, _timeout):
        return {"code": 1, "msg": f"token={SECRET}"}

    monkeypatch.setattr(stock_pool, "http_post", broken)
    caplog.set_level(logging.ERROR)
    assert cli.main(["--db", str(path), "collect", "--now", "2026-11-08T12:00:00+00:00"]) == 0
    assert "板块清单没能刷新" in caplog.text and SECRET not in caplog.text
    with db.read(path) as conn:
        stored = db.read_sector_concepts(conn)
    assert stored["concepts"][0]["name"] == "人工智能" and stored["error"]
    assert SECRET not in stored["error"]

    capsys.readouterr()
    assert cli.main(["--db", str(path), "refresh-concepts", "--now", "2026-11-09T12:00:00+00:00"]) == 1
    out, err = capsys.readouterr()
    assert SECRET not in out and SECRET not in err and "返回异常" in err
    with db.read(path) as conn:
        still = db.read_sector_concepts(conn)
    assert still["concepts"][0]["name"] == "人工智能"


def test_daily_fill_only_missing_days_and_drops_rows_outside_the_window(path):
    window = trading_days_ending(AS_OF, 3)
    _store_pool(path, ["300001.SZ"])
    with db.write(path) as conn:
        db.write_market_day(conn, window[0].isoformat(), [{
            "code": "300001.SZ", "open": 1, "high": 1, "low": 1, "close": 1, "preClose": 1,
            "pctChg": 0, "amount": 10, "limitUp": False}])
        db.write_market_day(conn, window[0].isoformat(), [
            {"code": "300001.SZ", "open": 1, "high": 1, "low": 1, "close": 1, "preClose": 1,
             "pctChg": 0, "amount": 10, "limitUp": False},
            {"code": "301001.SZ", "open": 1, "high": 1, "low": 1, "close": 1, "preClose": 1,
             "pctChg": 0, "amount": 10, "limitUp": False}])
        db.write_market_day(conn, "2026-09-01", [{
            "code": "300001.SZ", "open": 1, "high": 1, "low": 1, "close": 1, "preClose": 1,
             "pctChg": 0, "amount": 10, "limitUp": False}])
        db.write_market_day(conn, "2026-11-05", [{
            "code": "300001.SZ", "open": 1, "high": 1, "low": 1, "close": 1, "preClose": 1,
            "pctChg": 0, "amount": 10, "limitUp": False}])
    asked = []

    def post(_url, payload, _timeout):
        api = payload["api_name"]
        stamp = payload["params"]["trade_date"]
        asked.append((api, stamp))
        assert stamp != window[0].strftime("%Y%m%d")
        rows = [
            ["300001.SZ", stamp, 10, 11, 9, 11, 10, 1, 1.5],
            ["301001.SZ", stamp, 10, 11, 9, 11, 10, 2, 2],
            ["302001.SZ", stamp, 10, 11, 9, 11, 10, 3, 3],
            ["600000.SH", stamp, 10, 11, 9, 11, 10, 4, 4],
        ]
        if api == "daily":
            return _frame(["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "amount"],
                          rows)
        if api == "limit_list_d":
            assert payload["params"]["limit_type"] == "U"
            return _frame(["ts_code", "trade_date"], [["300001.SZ", stamp], ["600000.SH", stamp]])
        raise AssertionError(api)

    with db.write(path) as conn:
        result = fill_market_daily(conn, as_of=AS_OF, lookback_days=3, prefixes=("300", "301", "302"), post=post,
                                   environ={"TUSHARE_TOKEN": SECRET}, sleep=lambda _seconds: None, interval_seconds=0)
    assert result.ok and result.filled == tuple(day.isoformat() for day in window[1:])
    assert result.deleted == 2
    assert [api for api, _stamp in asked] == ["daily", "limit_list_d", "daily", "limit_list_d"]
    with db.read(path) as conn:
        rows = db.read_market_daily(conn)
    by_day = {}
    for row in rows:
        by_day.setdefault(row["tradeDate"], {})[row["code"]] = row
    assert set(by_day) == {day.isoformat() for day in window}
    kept = by_day[window[1].isoformat()]
    assert by_day[window[0].isoformat()]["300001.SZ"]["amount"] == 10
    assert set(kept) == {"300001.SZ", "301001.SZ"}
    assert kept["300001.SZ"]["amount"] == 1500 and kept["300001.SZ"]["limitUp"] is True
    assert kept["301001.SZ"]["limitUp"] is False and kept["301001.SZ"]["pctChg"] == 2


def test_daily_fill_failure_is_a_marker_and_keeps_existing_rows(path, caplog):
    window = trading_days_ending(AS_OF, 2)
    _store_pool(path, ["300001.SZ"])
    with db.write(path) as conn:
        db.write_market_day(conn, window[0].isoformat(), [{
            "code": "300001.SZ", "open": 1, "high": 1, "low": 1, "close": 1, "preClose": 1,
            "pctChg": 0, "amount": 10, "limitUp": False}])
        db.write_market_day(conn, "2026-09-01", [{
            "code": "300001.SZ", "open": 1, "high": 1, "low": 1, "close": 1, "preClose": 1,
            "pctChg": 0, "amount": 10, "limitUp": False}])

    def post(_url, payload, _timeout):
        if payload["api_name"] == "daily":
            return _frame(["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "amount"],
                          [["300001.SZ", "20261103", 1, 1, 1, 1, 1, 1, 1]])
        raise AssertionError(payload["api_name"])

    caplog.set_level(logging.ERROR)
    with db.write(path) as conn:
        mismatch = fill_market_daily(conn, as_of=AS_OF, lookback_days=2, prefixes=("300", "301", "302"), post=post,
                                     environ={"TUSHARE_TOKEN": SECRET}, sleep=lambda _seconds: None,
                                     interval_seconds=0)
    assert mismatch.ok is False and "交易日对不上" in mismatch.error and SECRET not in mismatch.error
    assert "日线没能补齐" in caplog.text
    with db.read(path) as conn:
        assert db.market_dates(conn) == ["2026-09-01", window[0].isoformat()]

    def explode(_url, payload, _timeout):
        raise RuntimeError(f"down {SECRET}")

    with db.write(path) as conn:
        failed = fill_market_daily(conn, as_of=AS_OF, lookback_days=2, prefixes=("300", "301", "302"), post=explode,
                                   environ={"TUSHARE_TOKEN": SECRET}, sleep=lambda _seconds: None, interval_seconds=0)
    assert failed.error and SECRET not in failed.error and failed.filled == ()
    with db.read(path) as conn:
        assert "2026-09-01" in db.market_dates(conn)
        assert AS_OF.isoformat() not in db.market_dates(conn)
    with db.write(path) as conn:
        uncovered = fill_market_daily(conn, as_of=date(2027, 3, 2), lookback_days=1, prefixes=("300",),
                                     post=explode, environ={"TUSHARE_TOKEN": SECRET}, sleep=lambda _seconds: None,
                                     interval_seconds=0)
    assert uncovered.error and "2027" in uncovered.error


def _material_row(code, day, pct, *, close=10, pre_close=10, amount=1000, limit_up=False):
    return {"code": code, "tradeDate": day.isoformat(), "close": close, "preClose": pre_close, "pctChg": pct,
            "amount": amount, "limitUp": limit_up, "open": pre_close, "high": close, "low": pre_close}


def test_board_material_compounds_returns_and_stops_at_as_of():
    days = trading_days_ending(AS_OF, 2)
    later = date(2026, 11, 5)
    rows = [
        _material_row("300001.SZ", days[0], 10, close=11, pre_close=10),
        _material_row("300001.SZ", days[1], 10, close=5.5, pre_close=5, limit_up=True),
        _material_row("300001.SZ", later, 100, close=80, limit_up=True),
        _material_row("300002.SZ", days[0], 1),
        _material_row("300003.SZ", days[0], 0),
        _material_row("300003.SZ", days[1], 0),
    ]
    # 除权日用收盘价相除会得到 5.5/11-1 = -50%，连乘是 21%。
    wrong = (5.5 / 11 - 1) * 100
    result = board_material(["300001.SZ", "300002.SZ"], rows, AS_OF, [1, 2], 2,
                            ["300001.SZ", "300002.SZ", "300003.SZ"])
    assert wrong == pytest.approx(-50)
    by_days = {block["days"]: block for block in result["windows"]}
    assert by_days[2]["median"] == pytest.approx(21)
    assert by_days[2]["median"] != pytest.approx(wrong)
    assert by_days[2]["counted"] == 1 and by_days[2]["missing"] == 1 and by_days[2]["up"] == 1
    assert by_days[2]["baselineCounted"] == 2 and by_days[2]["baselineMissing"] == 1
    assert by_days[2]["baselineMedian"] == pytest.approx(10.5)
    assert by_days[1]["median"] == pytest.approx(10)
    assert result["limitUps"] == 1 and result["limitMissing"] == 1
    assert result["baselineLimitUps"] == 1 and result["baselineLimitMissing"] == 1
    assert result["memberCount"] == 2 and result["asOf"] == "2026-11-04"
    with pytest.raises(ValueError, match="交易日"):
        board_material(POOL3, [], date(2026, 11, 7), [1], 1, POOL3)


def test_member_facts_keep_each_window_and_ignore_later_bars():
    days = trading_days_ending(AS_OF, 20)
    pcts = [0] * 20
    pcts[0] = 50
    pcts[-1] = 10
    limits = [False] * 20
    limits[1] = limits[2] = True
    rows = [_material_row("300001.SZ", day, pct, close=10, amount=1000, limit_up=limit_up)
            for day, pct, limit_up in zip(days, pcts, limits)]
    rows.append(_material_row("300001.SZ", date(2026, 11, 5), 50, close=1, amount=9, limit_up=True))
    result = member_facts("300001.SZ", rows, AS_OF, amount_window=20, limit_window=20, change_window=5)
    assert result["averageAmountYuan"] == 1000
    assert result["limitUps"] == 2
    assert result["changePercent"] == pytest.approx(10)
    gap = [row for row in rows if row["tradeDate"] != days[0].isoformat()]
    missing = member_facts("300001.SZ", gap, AS_OF, amount_window=20, limit_window=20, change_window=5)
    assert missing["averageAmountYuan"] is None and missing["limitUps"] is None
    assert missing["changePercent"] == pytest.approx(10)


def test_sector_snapshot_keeps_chinext_bars_and_replay_loads_them(tmp_path, monkeypatch):
    seen = []

    def post(_url, payload, _timeout):
        assert payload["token"] == SECRET
        api = payload["api_name"]
        seen.append((api, payload["params"].get("offset")))
        if api == "ths_index":
            return _frame(["ts_code", "name", "count"], [["885001.TI", "人工智能", 3]])
        if api == "ths_member":
            return _frame(["ts_code", "con_code", "con_name"],
                          [["885001.TI", "300001.SZ", "甲"], ["885001.TI", "600000.SH", "乙"]])
        if api == "daily":
            return _frame(
                ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "amount"],
                [["300001.SZ", "20261104", 10, 11, 9, 11, 10, 1, 1.5],
                 ["600000.SH", "20261104", 10, 11, 9, 11, 10, 1, 9]])
        if api == "limit_list_d":
            return _frame(["ts_code", "trade_date"], [["300001.SZ", "20261104"], ["600000.SH", "20261104"]])
        if api == "daily_basic":
            return _frame(["ts_code", "trade_date", "circ_mv", "total_mv"],
                          [["300001.SZ", "20261104", 100000, 200000], ["600000.SH", "20261104", 1, 1]])
        if api == "stock_basic":
            return _frame(["ts_code", "name", "fullname", "list_date", "list_status"],
                          [["300001.SZ", "甲", "甲公司", "20200101", "L"]])
        if api == "stock_company":
            return _frame(["ts_code", "main_business"], [["300001.SZ", "做设备"]])
        if api == "namechange":
            if not payload["params"].get("offset"):
                return _frame(["ts_code", "name", "start_date", "end_date"],
                              [["300001.SZ", "甲旧", "20200101", "20210101"]], more=True)
            return _frame(["ts_code", "name", "start_date", "end_date"],
                          [["300001.SZ", "甲", "20210102", "20261104"]])
        raise AssertionError(api)

    progress = []
    payload = capture_sector_payload(start=AS_OF, end=AS_OF, prefixes=("300", "301", "302"), post=post,
                                     environ={"TUSHARE_TOKEN": SECRET}, sleep=lambda _seconds: None,
                                     interval_seconds=0, progress=progress.append)
    assert [api for api, _offset in seen].count("namechange") == 2
    assert SECRET not in json.dumps(payload, ensure_ascii=False) and SECRET not in "".join(progress)
    assert [row["ts_code"] for row in payload["daily"]] == ["300001.SZ"]
    assert [row["ts_code"] for row in payload["limit_list_d"]] == ["300001.SZ"]
    assert [row["ts_code"] for row in payload["daily_basic"]] == ["300001.SZ"]
    assert len(payload["ths_member"]) == 2 and len(payload["namechange"]) == 2
    assert payload["stock_basic"] and payload["stock_company"]
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    assert snapshot_main(["--sector", "--start", "2026-11-04", "--end", "2026-11-04",
                          "--out", str(tmp_path / "nope.json")]) == 2

    sector = tmp_path / "sector.json"
    sector.write_text(json.dumps(payload), encoding="utf-8")
    snapshot = tmp_path / "snap.db"
    db.init_schema(snapshot)
    with db.write(snapshot) as conn:
        db.begin_report(conn, report_id="morning-2026-11-04", trade_date="2026-11-04",
                        window_start=datetime(2026, 11, 3, 7, tzinfo=timezone.utc),
                        window_end=datetime(2026, 11, 4, 0, tzinfo=timezone.utc),
                        config_version="screening-v6", config_sha256="0" * 64,
                        started_at=datetime(2026, 11, 3, 16, 2, tzinfo=timezone.utc))
    market = tmp_path / "market.json"
    market.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")
    monkeypatch.setattr("neckline.morning.replay.run_morning",
                        lambda *_args, **_kwargs: {"state": "available", "entries": 0, "stats": {}})
    run_replay(snapshot=snapshot, trade_date="2026-11-04", work=tmp_path / "work", market_snapshot=market,
               receipts=tmp_path / "receipts", filings_cache=tmp_path / "filings", pool_seed_only=True,
               sector_snapshot=sector)
    with db.read(tmp_path / "work" / "2026-11-04.db") as conn:
        concepts = db.read_sector_concepts(conn)
        bars = db.read_market_daily(conn)
    assert concepts["concepts"][0]["members"] == ["300001.SZ"]
    assert bars[0]["amount"] == 1500 and bars[0]["limitUp"] is True and bars[0]["pctChg"] == 1

    seen_args = {}

    def fake(**kwargs):
        seen_args["sector"] = kwargs["sector_snapshot"]
        return {"state": "available", "entries": 0}

    monkeypatch.setattr("neckline.morning.replay.run_replay", fake)
    assert replay_main(["--snapshot", "a.db", "--trade-date", "2026-11-04", "--work", str(tmp_path),
                        "--market-snapshot", "m.json", "--receipts", "r", "--filings-cache", "f",
                        "--pool-seed-only", "--sector-snapshot", "sector.json"]) == 0
    assert seen_args["sector"].name == "sector.json"


def test_daily_fill_stops_before_the_next_call_when_the_budget_is_gone(path):
    _store_pool(path, ["300001.SZ"])
    asked = []
    budget = {"left": 1}

    def before_call():
        budget["left"] -= 1
        return budget["left"] < 0

    def post(_url, payload, _timeout):
        api = payload["api_name"]
        asked.append(api)
        stamp = payload["params"]["trade_date"]
        if api == "daily":
            return _frame(
                ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "amount"],
                [["300001.SZ", stamp, 10, 11, 9, 11, 10, 1, 1.5]])
        if api == "limit_list_d":
            return _frame(["ts_code", "trade_date"], [["300001.SZ", stamp]])
        raise AssertionError(api)

    with db.write(path) as conn:
        result = fill_market_daily(
            conn, as_of=AS_OF, lookback_days=2, prefixes=("300", "301", "302"), post=post,
            environ={"TUSHARE_TOKEN": SECRET}, sleep=lambda _seconds: None, interval_seconds=0,
            before_call=before_call)
    assert asked == ["daily"]
    assert result.error == FETCH_CUTOFF and result.filled == ()


def test_concept_refresh_stops_when_the_budget_is_gone(path):
    first, _seen = _concept_post(
        [{"ts_code": "885001.TI", "name": "人工智能", "count": 3}], {"885001.TI": ["300001.SZ"]})
    with db.write(path) as conn:
        done = refresh_concepts(conn, rules=_rules(), prefixes=("300", "301", "302"), now=NOW, post=first,
                                environ={"TUSHARE_TOKEN": SECRET}, sleep=lambda _seconds: None, interval_seconds=0)
    assert done.replaced and done.error is None
    budget = {"left": 1}
    asked = []

    def before_call():
        budget["left"] -= 1
        return budget["left"] < 0

    def post(_url, payload, _timeout):
        asked.append(payload["api_name"])
        return first(_url, payload, _timeout)

    with db.write(path) as conn:
        stopped = refresh_concepts(
            conn, rules=_rules(), prefixes=("300", "301", "302"), now=NOW, post=post,
            environ={"TUSHARE_TOKEN": SECRET}, sleep=lambda _seconds: None, interval_seconds=0,
            before_call=before_call)
    assert asked == ["ths_index"]
    assert stopped.replaced is False and stopped.error
    with db.read(path) as conn:
        stored = db.read_sector_concepts(conn)
    assert stored["concepts"][0]["name"] == "人工智能" and stored["error"]
    assert stored["sha256"] == done.sha256
