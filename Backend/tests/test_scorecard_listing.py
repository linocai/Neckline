"""K9 清单 D0→D+4 五指标结算。"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from neckline.db import connection, init_schema
from neckline.scorecard import listing


def _seed_contract(db, d0: date) -> None:
    day = d0.strftime("%Y%m%d")
    now = "2026-08-22T00:00:00Z"
    init_schema(db)
    with connection(db) as conn:
        for rank, (code, l2) in enumerate(
                (("AAA", "L1"), ("BBB", "L1"), ("CCC", "L1"), ("DDD", "L1")), 1):
            conn.execute(
                "INSERT INTO k9_listing_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (day, code, "run", "K9", code, l2, "行业一", '["p1"]', "p1",
                 "strict", "floor", rank, 1.0, 1.0, 1.0, 0.0, now))
        for code, verdict in (("AAA", "confirmed"), ("BBB", "rejected"),
                              ("CCC", "observed")):
            conn.execute(
                "INSERT INTO k9_d1_verdicts "
                "(trade_date,ts_code,strategy,d0_date,pattern,playbook_version,verdict,"
                "decided_stage,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ((d0 + timedelta(days=1)).strftime("%Y%m%d"), code, "K9", day, "p1", 1,
                 verdict, "open30", now))
            conn.execute(
                "INSERT INTO k9_playbooks VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (day, code, 1, "llm", "p1", 11.0, 12.0, 9.0, "[]", "test", now, now))


def test_d0_to_d4_followup_and_five_metrics(tmp_path, monkeypatch):
    db = tmp_path / "score.db"
    d0 = date(2026, 8, 10)
    sessions = [d0 + timedelta(days=i) for i in range(1, 5)]
    _seed_contract(db, d0)
    monkeypatch.setattr(listing, "trading_days_between", lambda start, end: sessions)

    rows = {
        d0: [
            {"ts_code": "AAA", "close": 10.0, "high": 10.0, "sw_l2_code": "L1"},
            {"ts_code": "BBB", "close": 10.0, "high": 10.0, "sw_l2_code": "L1"},
            {"ts_code": "PEER", "close": 10.0, "high": 10.0, "sw_l2_code": "L1"},
        ],
        sessions[0]: [
            {"ts_code": "AAA", "close": 10.5, "high": 11.2, "sw_l2_code": "L1"},
            {"ts_code": "BBB", "close": 10.1, "high": 11.1, "sw_l2_code": "L1"},
            {"ts_code": "PEER", "close": 10.1, "high": 10.2, "sw_l2_code": "L1"},
        ],
        sessions[1]: [
            {"ts_code": "AAA", "close": 10.6, "high": 10.8, "sw_l2_code": "L1"},
            {"ts_code": "BBB", "close": 10.2, "high": 10.4, "sw_l2_code": "L1"},
            {"ts_code": "PEER", "close": 10.2, "high": 10.3, "sw_l2_code": "L1"},
        ],
        sessions[2]: [
            {"ts_code": "AAA", "close": 10.8, "high": 10.9, "sw_l2_code": "L1"},
            {"ts_code": "BBB", "close": 10.3, "high": 10.5, "sw_l2_code": "L1"},
            {"ts_code": "PEER", "close": 10.3, "high": 10.4, "sw_l2_code": "L1"},
        ],
        sessions[3]: [
            {"ts_code": "AAA", "close": 11.0, "high": 11.0, "sw_l2_code": "L1"},
            {"ts_code": "BBB", "close": 10.4, "high": 10.6, "sw_l2_code": "L1"},
            {"ts_code": "PEER", "close": 10.2, "high": 10.3, "sw_l2_code": "L1"},
        ],
    }
    monkeypatch.setattr(
        listing.fact_store, "load_pack",
        lambda day, **kwargs: SimpleNamespace(rows=pl.DataFrame(rows[day])))

    assert listing.refresh_day(d0, as_of=sessions[-1], db_path=db) is True
    score = listing.load_scorecard(window=20, db_path=db)
    assert score["listingCount"] == 4
    assert score["establishmentNumerator"] == 1
    assert score["establishmentDenominator"] == 2
    assert score["establishmentRate"] == 0.5
    # K9 §八：CCC=观察、DDD=尚未生成终值，两者都不进入成立率分母。
    assert score["realizationRate"] == 1.0
    assert score["falseKillRate"] == 1.0
    assert score["industryScore"] == pytest.approx(0.04)
    assert score["pickScore"] == pytest.approx(0.03)
    assert "combined" not in score and "total" not in score
    aaa = next(row for row in score["rows"] if row["tsCode"] == "AAA")
    assert aaa["stockCloseReturn"] == pytest.approx(0.10)
    assert aaa["stockMaxReturn"] == pytest.approx(0.12)  # 辅助最高收益独立保存


def test_not_enough_sessions_does_not_write(tmp_path, monkeypatch):
    db = tmp_path / "early.db"
    d0 = date(2026, 8, 20)
    _seed_contract(db, d0)
    monkeypatch.setattr(listing, "trading_days_between",
                        lambda start, end: [d0 + timedelta(days=1)])
    assert listing.refresh_day(d0, as_of=d0 + timedelta(days=1), db_path=db) is False
    assert listing.load_scorecard(window=20, db_path=db)["listingCount"] == 0
