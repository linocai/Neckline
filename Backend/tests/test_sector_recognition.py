"""涨停取数、题材辨识度和回放快照。"""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from neckline.morning import replay as replay_mod
from neckline.morning.cli import main as cli_main
from neckline.morning.db import read, write
from neckline.morning.db import init_schema, replace_sector_concepts
from neckline.morning.replay import ReplayError, run_replay
from neckline.morning.schedule import CST
from neckline.morning.sector_line import run_sector_line
from neckline.morning.sector_prompt import (
    EVALUATION_CONTRACT, EVALUATION_SYSTEM, MAPPING_CONTRACT, MAPPING_SYSTEM, SELECTION_CONTRACT,
    SELECTION_SYSTEM,
)
from neckline.morning.sector_recognition import (
    closed_limit_days, collect_limit_days, ensure_limit_ups, install_limit_snapshot, score_recognition,
)
from tests.sector_v6_draft import (
    EVALUATION_SYSTEM_DRAFT, MAPPING_SYSTEM_DRAFT, SELECTION_SYSTEM_DRAFT,
)
from tests.test_sector_line import (
    DAY, Scripted, _answer, _at, _db, _event, _ids, _leader_codes, _msg, _ok, _pool, _rows, _rules, _window,
)

SECRET = "tushare-secret"


def _frame(fields, items, *, more=False):
    return {"code": 0, "data": {"fields": fields, "items": items, "has_more": more}}


def _codes(start: int, count: int) -> list[str]:
    return [f"{number:06d}" for number in range(start, start + count)]


def test_prompts_match_the_v6_draft_character_for_character():
    assert MAPPING_CONTRACT == "sector-mapping-v6" and MAPPING_SYSTEM == MAPPING_SYSTEM_DRAFT
    assert EVALUATION_CONTRACT == "sector-evaluation-v6" and EVALUATION_SYSTEM == EVALUATION_SYSTEM_DRAFT
    assert SELECTION_CONTRACT == "sector-selection-v2" and SELECTION_SYSTEM == SELECTION_SYSTEM_DRAFT


def test_recognition_scores_boundaries_and_sorts():
    full = set(_codes(1, 10))
    days = [date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 9)]
    limits = {
        "2026-11-05": {"000001", "000002", "000003"},
        "2026-11-06": {"000001", "000002", "000003", "000004"},
        "2026-11-09": {"000001", "000005", "000006", "000007"},
    }
    scored = score_recognition(
        _codes(1, 10), full, limits, days, min_others=2, min_share=0.3)
    assert [(item["code"], item["count"], item["last"]) for item in scored] == [
        ("000001", 2, "2026-11-09"),
        ("000005", 1, "2026-11-09"),
        ("000006", 1, "2026-11-09"),
        ("000007", 1, "2026-11-09"),
        ("000002", 1, "2026-11-06"),
        ("000003", 1, "2026-11-06"),
        ("000004", 1, "2026-11-06"),
    ]
    assert all(item["code"] != "000008" for item in scored)

    # 只有自己和另一只涨停。若把自己算进 others，会达到 minOthers=2；自己不算，所以不进名单。
    alone = score_recognition(
        ["000001"], full, {"2026-11-05": {"000001", "000002"}}, [date(2026, 11, 5)],
        min_others=2, min_share=0)
    assert alone == []

    single = score_recognition(
        ["000001"], {"000001"}, {"2026-11-05": {"000001"}}, [date(2026, 11, 5)],
        min_others=0, min_share=0)
    assert single == []

    wide = set(_codes(1, 101))
    exact = score_recognition(
        ["000001"], wide, {"2026-11-05": set(_codes(1, 3))}, [date(2026, 11, 5)],
        min_others=2, min_share=0.02)
    assert exact == [{"code": "000001", "count": 1, "last": "2026-11-05"}]
    short = score_recognition(
        ["000001"], wide, {"2026-11-05": {"000001", "000002"}}, [date(2026, 11, 5)],
        min_others=0, min_share=0.02)
    assert short == []


def test_ensure_limit_ups_pages_skips_empty_days_and_stops_on_errors(tmp_path):
    path = _db(tmp_path)
    first, second = date(2026, 10, 29), date(2026, 10, 30)
    calls: list[dict] = []

    def post(_url, payload, _timeout):
        calls.append(payload)
        offset = payload["params"]["offset"]
        trade = payload["params"]["trade_date"]
        assert payload["params"]["limit_type"] == "U"
        assert payload["fields"] == "trade_date,ts_code"
        if trade == "20261029":
            return _frame(["trade_date", "ts_code"], [])
        if offset == 0:
            return _frame(["trade_date", "ts_code"], [[trade, "300001.SZ"]], more=True)
        return _frame(["trade_date", "ts_code"], [[trade, "600000.SH"]])

    with write(path) as conn:
        result = ensure_limit_ups(
            conn, [first, second], post=post, environ={"TUSHARE_TOKEN": SECRET},
            sleep=lambda _seconds: None, interval_seconds=0)
    assert result == {"missing": ["2026-10-29"], "fetched": 1, "error": None}
    assert [item["params"]["offset"] for item in calls] == [0, 0, len(["300001.SZ"])]
    with read(path) as conn:
        stored = {row[0] for row in conn.execute("SELECT code FROM limit_ups WHERE trade_date='2026-10-30'")}
        days = {row[0] for row in conn.execute("SELECT trade_date FROM limit_up_days")}
    assert stored == {"300001", "600000"} and days == {"2026-10-30"}

    calls.clear()
    with write(path) as conn:
        again = ensure_limit_ups(
            conn, [second], post=post, environ={"TUSHARE_TOKEN": SECRET},
            sleep=lambda _seconds: None, interval_seconds=0)
    assert again["fetched"] == 0 and calls == []

    def mismatch(_url, payload, _timeout):
        calls.append(payload["params"]["trade_date"])
        return _frame(["trade_date", "ts_code"], [["19990101", "300001.SZ"]])

    calls.clear()
    with write(path) as conn:
        bad = ensure_limit_ups(
            conn, [date(2026, 10, 28), date(2026, 10, 27)], post=mismatch, environ={"TUSHARE_TOKEN": SECRET},
            sleep=lambda _seconds: None, interval_seconds=0)
    assert bad["fetched"] == 0 and bad["missing"] == ["2026-10-28", "2026-10-27"]
    assert "对不上" in bad["error"] and calls == ["20261028"]

    with write(path) as conn:
        stopped = ensure_limit_ups(
            conn, [date(2026, 10, 27)], post=mismatch, environ={"TUSHARE_TOKEN": SECRET},
            sleep=lambda _seconds: None, interval_seconds=0, before_call=lambda: True)
    assert stopped["error"] == "收口时刻前没取完" and stopped["fetched"] == 0

    def forbidden(*_args, **_kwargs):
        raise AssertionError("不该取")

    with write(path) as conn:
        missing_token = ensure_limit_ups(
            conn, [date(2026, 10, 27)], post=forbidden, environ={}, sleep=lambda _seconds: None,
            interval_seconds=0)
    assert missing_token["fetched"] == 0 and "TUSHARE_TOKEN" in missing_token["error"]


def _plant(path, *, limits=True) -> None:
    concepts = [{
        "tsCode": "885001.TI", "name": "人工智能", "memberCount": 3,
        "members": ["300001.SZ", "300002.SZ", "300003.SZ"],
    }]
    with write(path) as conn:
        replace_sector_concepts(conn, concepts, refreshed_at="2026-10-12T00:00:00+00:00", sha256="ab")
        if limits:
            from neckline.morning.sector_recognition import recognition_window
            for trade in recognition_window(DAY, 3):
                iso = trade.isoformat()
                conn.execute("INSERT INTO limit_ups(trade_date, code) VALUES (?, '999999')", (iso,))
                conn.execute(
                    "INSERT INTO limit_up_days(trade_date, count, fetched_at) VALUES (?, 1, ?)",
                    (iso, "2026-10-12T00:00:00+00:00"))


def _message():
    return _msg("a", "工信部印发行业补贴方案", "范围。", datetime(2026, 10, 13, 7, 10, tzinfo=CST))


def _config():
    config = _rules(_pool())
    config.rules["sectors"]["recognition"] = {"lookbackDays": 3, "hot": {"minOthers": 2, "minShare": 0.02}}
    return config


def test_replay_does_not_fetch_and_a_gap_names_the_missing_days(tmp_path):
    path = _db(tmp_path)
    _plant(path, limits=False)

    def post(*_args, **_kwargs):
        raise AssertionError("回放不该取涨停")

    outcome = run_sector_line(
        path, report_id="morning-2026-10-13", day=DAY, window=_window(), messages=[_message()],
        config=_config(), pool=_pool(), skip=[], client=Scripted(lambda *_args: (_ for _ in ()).throw(AssertionError("不该评"))),
        clock=lambda: _at(8, day=13), sector_live=False, limit_post=post, limit_sleep=lambda _seconds: None)
    assert outcome.gap == "板块线没跑：涨停数据没取全"
    assert outcome.stats["recognition"]["ran"] is False
    assert outcome.stats["recognition"]["note"] == "涨停数据没取全"
    assert outcome.stats["recognition"]["missing"]
    assert outcome.stats["recognition"]["fetched"] == 0
    assert outcome.content["entries"] == []


def test_morning_fill_stops_at_the_cutoff_and_a_complete_fill_evaluates(tmp_path):
    path = _db(tmp_path)
    _plant(path, limits=False)
    asked: list[str] = []

    def post(_url, payload, _timeout):
        asked.append(payload["params"]["trade_date"])
        trade = payload["params"]["trade_date"]
        return _frame(["trade_date", "ts_code"], [[trade, code] for code in ("300001.SZ", "300002.SZ", "300003.SZ")])

    ticks = {"n": 0}

    def clock():
        ticks["n"] += 1
        return _at(8, day=13) if ticks["n"] == 1 else _at(9, 6, day=13)

    stopped = run_sector_line(
        path, report_id="morning-2026-10-13", day=DAY, window=_window(), messages=[_message()],
        config=_config(), pool=_pool(), skip=[], client=Scripted(lambda *_args: (_ for _ in ()).throw(AssertionError("不该评"))),
        clock=clock, sector_live=True, limit_post=post, limit_environ={"TUSHARE_TOKEN": SECRET},
        limit_sleep=lambda _seconds: None)
    assert asked == []
    assert stopped.gap == "板块线没跑：涨停数据没取全"
    assert stopped.stats["recognition"]["note"] == "收口时刻前没取完"
    assert stopped.stats["recognition"]["fetched"] == 0

    def handler(sent, _options):
        if "板块分析员" in sent[0]["content"]:
            return _ok({"events": [_event({
                "title": "补贴", "ids": _ids(sent[1]["content"])[:1], "sector": "人工智能",
                "reason": "直接", "repeat": False,
            })], "dropped": []})
        return _ok(_answer(_leader_codes(sent[1]["content"])[:1] or ["300001"]))

    filled = run_sector_line(
        path, report_id="morning-2026-10-13", day=DAY, window=_window(), messages=[_message()],
        config=_config(), pool=_pool(), skip=[], client=Scripted(handler),
        clock=lambda: _at(8, day=13), sector_live=True, limit_post=post, limit_environ={"TUSHARE_TOKEN": SECRET},
        limit_sleep=lambda _seconds: None)
    assert filled.gap is None and len(filled.content["entries"]) == 1
    assert filled.stats["recognition"]["ran"] is True and filled.stats["recognition"]["fetched"] == 3
    assert filled.stats["recognition"]["days"][0] < filled.stats["recognition"]["days"][1]
    assert asked


def test_an_empty_recognition_list_skips_the_model(tmp_path):
    path = _db(tmp_path)
    _plant(path, limits=True)
    calls = {"eval": 0}

    def handler(sent, _options):
        if "板块分析员" not in sent[0]["content"]:
            calls["eval"] += 1
            raise AssertionError("名单是空的，不该评估")
        return _ok({"events": [_event({
            "title": "补贴", "ids": _ids(sent[1]["content"])[:1], "sector": "人工智能",
            "reason": "直接", "repeat": False,
        })], "dropped": []})

    outcome = run_sector_line(
        path, report_id="morning-2026-10-13", day=DAY, window=_window(), messages=[_message()],
        config=_config(), pool=_pool(), skip=[], client=Scripted(handler),
        clock=lambda: _at(8, day=13), sector_live=False)
    assert calls["eval"] == 0
    assert outcome.stats["recognition"]["emptyEvents"] == 1
    row = _rows(path)[0]
    assert row["state"] == "skipped" and row["reason"] == "板块里没有题材辨识度名单"
    assert row["verdict"] is None


def test_windows_follow_the_trading_calendar():
    sunday = datetime(2026, 11, 1, 12, tzinfo=CST)
    assert collect_limit_days(sunday, 1) == [date(2026, 10, 30)]
    tuesday_morning = datetime(2026, 11, 10, 6, tzinfo=CST)
    assert collect_limit_days(tuesday_morning, 1) == [date(2026, 11, 10)]
    assert closed_limit_days(tuesday_morning, "15:00", 1) == [date(2026, 11, 9)]
    after_close = datetime(2026, 11, 10, 15, tzinfo=CST)
    assert closed_limit_days(after_close, "15:00", 1) == [date(2026, 11, 10)]


def test_refresh_limit_ups_command_prints_the_result_and_hides_the_token(tmp_path, monkeypatch, capsys):
    path = _db(tmp_path)
    monkeypatch.setattr("neckline.morning.sector_recognition.LIMIT_INTERVAL_SECONDS", 0)

    def post(_url, payload, _timeout):
        assert payload["token"] == SECRET
        trade = payload["params"]["trade_date"]
        return _frame(["trade_date", "ts_code"], [[trade, "300001.SZ"]])

    monkeypatch.setattr("neckline.morning.stock_pool.http_post", post)
    monkeypatch.setenv("TUSHARE_TOKEN", SECRET)
    assert cli_main(["--db", str(path), "refresh-limit-ups", "--now", "2026-11-10T08:00:00+00:00"]) == 0
    out, err = capsys.readouterr()
    body = json.loads(out)
    assert body["missing"] == [] and body["fetched"] == 40
    assert SECRET not in out and SECRET not in err

    def broken(_url, payload, _timeout):
        return {"code": 1, "msg": f"token={SECRET}"}

    monkeypatch.setattr("neckline.morning.stock_pool.http_post", broken)
    assert cli_main(["--db", str(path), "refresh-limit-ups", "--now", "2026-11-11T08:00:00+00:00"]) == 1
    out, err = capsys.readouterr()
    assert SECRET not in out and SECRET not in err and "返回异常" in err


def test_replay_limit_snapshot_keeps_only_earlier_nonempty_trading_days(tmp_path, monkeypatch):
    snapshot = tmp_path / "snap.db"
    init_schema(snapshot)
    with write(snapshot) as conn:
        from neckline.morning.db import begin_report
        begin_report(conn, report_id="morning-2026-11-10", trade_date="2026-11-10",
                     window_start=datetime(2026, 11, 9, 7, tzinfo=CST),
                     window_end=datetime(2026, 11, 10, 0, tzinfo=CST),
                     config_version="screening-v7", config_sha256="0" * 64,
                     started_at=datetime(2026, 11, 9, 16, 2, tzinfo=CST))
    market = tmp_path / "market.json"
    market.write_text("{}", encoding="utf-8")
    limits = tmp_path / "limits.json"
    limits.write_text(json.dumps([
        {"trade_date": "20261110", "ts_code": "300002.SZ"},
        {"trade_date": "20261109", "ts_code": "300001.SZ"},
        {"trade_date": "20261108", "ts_code": "300003.SZ"},
        {"trade_date": "20261106", "ts_code": "600000.SH"},
        {"trade_date": "20261106", "ts_code": "not-a-code"},
    ]), encoding="utf-8")
    seen = {}

    def morning(*_args, **kwargs):
        seen["live"] = kwargs.get("sector_live")
        return {"state": "available", "entries": 0, "stats": {}}

    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")
    monkeypatch.setattr(replay_mod, "run_morning", morning)
    run_replay(snapshot=snapshot, trade_date="2026-11-10", work=tmp_path / "work", market_snapshot=market,
               receipts=tmp_path / "receipts", filings_cache=tmp_path / "filings", pool_seed_only=True,
               limit_snapshot=limits)
    assert seen["live"] is False
    with read(tmp_path / "work" / "2026-11-10.db") as conn:
        days = {row[0]: row[1] for row in conn.execute("SELECT trade_date, count FROM limit_up_days")}
        codes = {(row[0], row[1]) for row in conn.execute("SELECT trade_date, code FROM limit_ups")}
    assert days == {"2026-11-09": 1, "2026-11-06": 1}
    assert codes == {("2026-11-09", "300001"), ("2026-11-06", "600000")}

    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ReplayError, match="涨停快照不是列表"):
        run_replay(snapshot=snapshot, trade_date="2026-11-10", work=tmp_path / "again", market_snapshot=market,
                   receipts=tmp_path / "receipts", filings_cache=tmp_path / "filings", pool_seed_only=True,
                   limit_snapshot=bad)
    direct = tmp_path / "direct.db"
    init_schema(direct)
    with write(direct) as conn:
        with pytest.raises(ValueError, match="涨停快照不是列表"):
            install_limit_snapshot(conn, {"trade_date": "20261106"}, "2026-11-10")
