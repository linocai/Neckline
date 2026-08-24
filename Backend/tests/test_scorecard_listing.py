"""K9-v2 清单 D0→D2 五指标结算。"""

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from neckline.db import connection, init_schema
from neckline.scorecard import listing


def _seed_contract(db, d0: date) -> None:
    day, now = d0.strftime("%Y%m%d"), "2026-08-24T00:00:00Z"
    init_schema(db)
    scoring = json.dumps({"touchThresholdU": 0.05, "riskLineL": 0.05,
                          "d1Reference": "last_valid_trade_at_10_00",
                          "matchedBaseline": "industryMedian"})
    with connection(db) as conn:
        conn.execute(
            "INSERT INTO k9_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("run", day, "K9", "K9-v2", "d2-v1", "fixture", "pack", "fp-3",
             "strict", 3, 3, 2, 0, 0, 0, "{}", "{}", "[]", "[]", "explain", scoring, now))
        for rank, (code, pattern) in enumerate((("AAA", "p1"), ("BBB", "p2")), 1):
            conn.execute(
                "INSERT INTO k9_listing_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (day, code, "run", "K9", "K9-v2", code, "L1", "行业一",
                 json.dumps([pattern]), pattern, "strict", "floor", rank, 1.0,
                 1.0, 1.0, 0.0, "{}", "[]", now))
        for code, pattern in (("AAA", "p1"), ("BBB", "p2"), ("PEER", "p1")):
            conn.execute(
                "INSERT INTO k9_channel_hits "
                "(run_id,trade_date,strategy_version,ts_code,pattern,tier,seated,"
                "strength_json,evidence_json,risks_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("run", day, "K9-v2", code, pattern, "strict", int(code != "PEER"),
                 "{}", "{}", "[]", now))
        for code, verdict, reference in (("AAA", "confirmed", 10.1), ("BBB", "rejected", 9.9)):
            conn.execute(
                "INSERT INTO k9_d1_verdicts "
                "(trade_date,ts_code,strategy,d0_date,pattern,playbook_version,verdict,"
                "decided_stage,open30_readings_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                ((d0 + timedelta(days=1)).strftime("%Y%m%d"), code, "K9", day, "p1", 1,
                 verdict, "open30", json.dumps({"last_valid_trade_at_10_00": reference}), now))


def test_d0_to_d2_followup_and_five_metrics(tmp_path, monkeypatch):
    db, d0 = tmp_path / "score.db", date(2026, 8, 10)
    d1, d2 = d0 + timedelta(days=1), d0 + timedelta(days=2)
    _seed_contract(db, d0)
    monkeypatch.setattr(listing, "trading_days_between", lambda start, end: [d1, d2])
    bars = {
        d0: [
            {"ts_code": "AAA", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "is_limit_up": False, "sw_l2_code": "L1"},
            {"ts_code": "BBB", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "is_limit_up": False, "sw_l2_code": "L1"},
            {"ts_code": "PEER", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "is_limit_up": False, "sw_l2_code": "L1"},
        ],
        d1: [
            {"ts_code": "AAA", "open": 10.1, "high": 10.7, "low": 9.8, "close": 10.3,
             "is_limit_up": False, "sw_l2_code": "L1"},
            {"ts_code": "BBB", "open": 9.9, "high": 10.2, "low": 9.4, "close": 9.7,
             "is_limit_up": False, "sw_l2_code": "L1"},
            {"ts_code": "PEER", "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1,
             "is_limit_up": False, "sw_l2_code": "L1"},
        ],
        d2: [
            {"ts_code": "AAA", "open": 10.3, "high": 10.5, "low": 9.7, "close": 10.4,
             "is_limit_up": False, "sw_l2_code": "L1"},
            {"ts_code": "BBB", "open": 9.7, "high": 10.1, "low": 9.5, "close": 9.8,
             "is_limit_up": False, "sw_l2_code": "L1"},
            {"ts_code": "PEER", "open": 10.1, "high": 10.3, "low": 10.0, "close": 10.1,
             "is_limit_up": False, "sw_l2_code": "L1"},
        ],
    }
    monkeypatch.setattr(
        listing.fact_store, "load_pack",
        lambda day, **kwargs: SimpleNamespace(rows=pl.DataFrame(bars[day])))

    assert listing.open_day(d0, db_path=db) == 2
    with connection(db) as conn:
        pending = conn.execute(
            "SELECT COUNT(*) FROM k9_predictions WHERE path_state='pending'"
        ).fetchone()[0]
        provenance = conn.execute(
            "SELECT DISTINCT strategy_version,label_contract_version,"
            "params_package_version,pack_id,pack_version FROM k9_predictions"
        ).fetchall()
    assert pending == 2
    assert provenance == [("K9-v2", "d2-v1", "fixture", "pack", "fp-3")]
    assert listing.active_queue_count(d0, db_path=db) == 2
    assert listing.refresh_day(d0, as_of=d2, db_path=db)
    score = listing.load_scorecard(window=20, db_path=db)
    assert score["strategyVersion"] == "K9-v2"
    assert score["activeQueueLimit"] == 60
    assert score["settledDays"] == 1 and score["listingCount"] == 2
    assert score["overall"]["touchRate"] == 0.5
    assert score["overall"]["d2CloseWinRate"] == 0.5
    assert score["overall"]["averageIndustryExcess"] == pytest.approx(0.0)
    assert score["overall"]["averageMaxDrawdown"] == pytest.approx(-0.045)
    assert score["overall"]["finalListingLift"]["vsStrictRecall"] == pytest.approx(0.0)
    assert score["overall"]["finalListingLift"]["vsMatchedBaseline"] == pytest.approx(0.0)
    assert score["byPattern"]["p1"]["touchRate"] == 1.0
    assert score["byPattern"]["p2"]["touchRate"] == 0.0
    assert score["d1Aux"]["confirmationRate"]["touchRate"] == 0.5
    assert score["d1Aux"]["confirmed"]["touchRate"] == 1.0
    assert score["d1Aux"]["rejected"]["touchRate"] == 0.0
    assert all(row["d2Date"] == d2.strftime("%Y%m%d") for row in score["rows"])


def test_not_enough_sessions_does_not_write(tmp_path, monkeypatch):
    db, d0 = tmp_path / "early.db", date(2026, 8, 20)
    _seed_contract(db, d0)
    monkeypatch.setattr(
        listing, "trading_days_between", lambda start, end: [d0 + timedelta(days=1)])
    assert not listing.refresh_day(d0, as_of=d0 + timedelta(days=1), db_path=db)
    assert listing.load_scorecard(window=20, db_path=db)["listingCount"] == 0
