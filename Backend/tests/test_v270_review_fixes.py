"""Regression coverage for the V2.7 review fixes; these are production contracts."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import polars as pl

from neckline.auction import settle
from neckline.auction.readings import collect_open_readings
from neckline.data.market_data import write_table_day
from neckline.db import init_schema
from neckline.k9 import v3_run
from neckline.report import pipeline
from neckline.report import evening as evening_report
from neckline.scorecard import lifecycle
from neckline.scorecard import packages
from scripts import evening as evening_script
from scripts import backfill as backfill_script
from scripts import bootstrap_fact_packs


def test_missing_tick_feed_is_never_mislabelled_as_observed():
    assert settle._open_verdict("pending_open", {"feedStatus": "unavailable"}) == ("unavailable", None, None)
    assert settle._open_verdict("pending_open", {"limitUpPrice": 11}) == ("unavailable", None, None)


def test_open_reference_is_loaded_from_local_ticks_not_a_daily_bar(tmp_path):
    db = tmp_path / "db.sqlite"; parquet = tmp_path / "parquet"; init_schema(db)
    candidate = packages.Candidate("000001.SZ", "A", None, None, ["p2"], {"p2": 1}, {}, {"limit_up_price": 11.0}, {})
    packages.create_batch(batch_id="b", selection_date=date(2026, 8, 20), signal_trade_date=date(2026, 8, 20),
                          d1_trade_date=date(2026, 8, 21), d2_trade_date=date(2026, 8, 24), revision=1,
                          params_package_version="p", params_sha256="h", pack_id="fp", frozen_contract={}, candidates=[candidate], db_path=db)
    write_table_day("intraday_ticks", date(2026, 8, 21), pl.DataFrame({"ts_code": ["000001.SZ", "000001.SZ"], "trade_date": [date(2026, 8, 21)] * 2, "timestamp": ["2026-08-21 09:31:00+08:00", "2026-08-21 10:00:00+08:00"], "price": [10.1, 10.3], "valid_trade": [True, True], "volume_delta": [1, 1], "amount_delta": [1, 1]}), parquet)
    readings = collect_open_readings(date(2026, 8, 21), db_path=db, parquet_dir=parquet)
    assert [tick["price"] for tick in readings["000001.SZ"]["trades"]] == [10.1, 10.3]
    assert settle._valid_reference(readings["000001.SZ"]) == 10.3


def test_p2_low_recovery_rewards_higher_close_location_when_quota_cuts_candidates():
    day = date(2026, 8, 28)
    frame = pl.DataFrame([
        *[{"trade_date": date(2026, 8, 27), "ts_code": code, "name": name, "sw_l2_code": "i", "sw_l2_name": "I", "close": 8.0, "high": 10.0, "low": 0.0, "vol": 100.0, "amount": 100.0, "turnover_rate": 1.0, "rel_strength_1d": 0.0, "sw_l2_median_ret": 0.0, "ret_1d": 0.0}
          for code, name in (("recovered", "A"), ("weak", "B"))],
        {"trade_date": day, "ts_code": "recovered", "name": "A", "sw_l2_code": "i", "sw_l2_name": "I", "close": 9.0, "high": 10.0, "low": 0.0, "vol": 100.0, "amount": 100.0, "turnover_rate": 1.0, "rel_strength_1d": 0.0, "sw_l2_median_ret": 0.0, "ret_1d": 0.0},
        {"trade_date": day, "ts_code": "weak", "name": "B", "sw_l2_code": "i", "sw_l2_name": "I", "close": 7.0, "high": 10.0, "low": 0.0, "vol": 100.0, "amount": 100.0, "turnover_rate": 1.0, "rel_strength_1d": 0.0, "sw_l2_median_ret": 0.0, "ret_1d": 0.0},
    ])
    cfg = {"recall": {"windowDays": 1, "volumeBaselineDays": 1, "minCumulativeDropPct": 0, "minDrawdownPct": 0,
                          "minIndustryUnderperformancePct": 0, "minVolumeMultiple": 0,
                          "supportCloseLocationPct": 0, "supportDailyReturnPct": 0},
           "ranking": {"oversoldDepthWeight": 0, "industryUnderperformanceWeight": 0,
                       "lowRecoveryWeight": 1, "declineDecelerationWeight": 0, "turnoverWeight": 0}}
    hits = v3_run._p2(frame, frame.filter(pl.col("trade_date") == day), cfg, 1, set())
    assert [hit.ts_code for hit in hits] == ["recovered"]


def test_weekend_public_selection_date_is_the_package_date(tmp_path):
    from neckline.db import init_schema
    db = tmp_path / "test.db"; init_schema(db)
    packages.create_batch(batch_id="sun", selection_date=date(2026, 8, 30), signal_trade_date=date(2026, 8, 28),
                          d1_trade_date=date(2026, 8, 31), d2_trade_date=date(2026, 9, 1), revision=1,
                          params_package_version="p", params_sha256="h", pack_id="fp", frozen_contract={}, candidates=[], db_path=db)
    # Facts/params deliberately fail, but the read side must still retain the dual date identity.
    assert packages.load_package("sun", db_path=db)["selection_date"] == "20260830"
    assert packages.load_package("sun", db_path=db)["signal_trade_date"] == "20260828"


def test_not_run_report_never_calls_apns(monkeypatch):
    called = []
    from neckline.api import notify
    monkeypatch.setattr(notify, "push_report_ready", lambda *a, **k: called.append((a, k)))
    bundle = SimpleNamespace(state=SimpleNamespace(value="not_run"), report_date=date(2026, 8, 30), trade_date=date(2026, 8, 28))
    evening_script._notify(bundle)
    assert called == []


def test_requested_fp4_backfill_incomplete_days_are_failures_not_successful_noops(monkeypatch):
    days = [date(2026, 8, 20)]
    monkeypatch.setattr(bootstrap_fact_packs, "trading_days_between", lambda *_, **_kwargs: days)
    monkeypatch.setattr(bootstrap_fact_packs.fact_pack, "build", lambda _, **_kwargs: __import__("neckline.facts.pack", fromlist=["IncompletePack"]).IncompletePack(days[0], "fp-4", ("missing",)))
    assert bootstrap_fact_packs.run(days[0], days[0]) == 1
    from neckline.facts import v4
    monkeypatch.setattr(v4, "build", lambda _, **_kwargs: __import__("neckline.facts.pack", fromlist=["IncompletePack"]).IncompletePack(days[0], "fp-4", ("missing",)))
    stats = backfill_script.freeze_fp4(days)
    assert stats["incomplete"] == stats["missing"] == 1 and stats["missingDates"] == ["20260820"]


def test_unexpected_intraday_evidence_exception_is_durably_failed_before_report_can_run(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"; init_schema(db)
    day = date(2026, 8, 20)
    from neckline.auction import readings
    monkeypatch.setattr(readings, "collect_d1_eod_readings", lambda *args, **kwargs: (_ for _ in ()).throw(TypeError("bad provider shape")))
    monkeypatch.setattr(readings, "collect_d2_eod_readings", lambda *args, **kwargs: {})
    status, _detail = evening_report._run_k9_lifecycle(day, report_date=day, k9_params_path=None, db_path=db, parquet_dir=tmp_path / "pq")
    attempt = lifecycle.latest_attempt(selection_date=day, signal_trade_date=day, db_path=db)
    assert status == evening_report.STATUS_FAILED
    assert attempt and attempt["status"] == "failed" and attempt["stages"]["d1"]["status"] == "failed"
