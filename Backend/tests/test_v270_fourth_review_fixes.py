"""Fourth-review regressions: seconds, deltas, immutable D1 and lifecycle gates."""
from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl
import pytest

from neckline.auction.readings import collect_open_readings
from neckline.auction import pipeline as auction_pipeline, settle
from neckline.data.market_data import write_table_day
from neckline.db import init_schema
from neckline.report import pipeline
from neckline.scorecard import lifecycle, packages


DAY, D1 = date(2026, 8, 20), date(2026, 8, 21)


def _batch(db):
    candidate = packages.Candidate("000001.SZ", "甲", None, None, ["p2"], {"p2": 1},
        {"revision": 1, "invalidation": 9, "firstResistance": 11,
         "openVerdict": {"rejectBelow": 9, "confirmRange": {"minimum": 9.5, "maximum": 10.5}, "unbuyableAtOrAbove": 11}},
        {"limit_up_price": 11}, {})
    packages.create_batch(batch_id="b", selection_date=DAY, signal_trade_date=DAY,
        d1_trade_date=D1, d2_trade_date=date(2026, 8, 24), revision=1,
        params_package_version="p", params_sha256="h", pack_id="fp", frozen_contract={}, candidates=[candidate], db_path=db)


def test_open_ticks_are_second_exact_and_require_counter_increment(tmp_path):
    db, parquet = tmp_path / "db.sqlite", tmp_path / "pq"; init_schema(db); _batch(db)
    write_table_day("intraday_ticks", D1, pl.DataFrame({
        "ts_code": ["000001.SZ"] * 4, "timestamp": ["2026-08-21T09:30:00+08:00", "2026-08-21T10:00:00+08:00", "2026-08-21T10:00:00.001+08:00", "2026-08-21T10:00:30+08:00"],
        "trade_date": [D1] * 4,
        "price": [10.0, 10.1, 10.2, 10.3], "valid_trade": [True] * 4,
        "volume_delta": [1, 1, 1, 0], "amount_delta": [0, 0, 0, 0],
    }), parquet)
    readings = collect_open_readings(D1, db_path=db, parquet_dir=parquet)
    assert [item["time"] for item in readings["000001.SZ"]["trades"]] == ["09:30:00", "10:00:00"]


def test_direct_settlement_adapter_keeps_the_same_second_exact_open_boundary():
    reading = {"limitUpPrice": 11, "trades": [
        {"time": "10:00:00", "price": 10.0},
        {"time": "10:00:00.001", "price": 10.1},
        {"time": "10:00:30", "price": 10.2},
    ]}
    assert settle._valid_reference(reading) == 10.0


def test_d1_freeze_is_atomic_and_hard_at_0926(tmp_path):
    db = tmp_path / "db.sqlite"; init_schema(db); _batch(db)
    package = packages.load_package("b", db_path=db)
    with pytest.raises(packages.PackageConflict):
        packages.freeze_checklist_atomic(package, trade_date=D1, readings={}, now=datetime(2026, 8, 21, 9, 25, 59), db_path=db)
    snapshot = packages.freeze_checklist_atomic(package, trade_date=D1, readings={}, now=datetime(2026, 8, 21, 9, 26), db_path=db)
    assert snapshot.as_dict()["batchId"] == "b"
    with pytest.raises(packages.PackageConflict):
        packages.append_user_playbook_revision(batch_id="b", ts_code="000001.SZ", playbook={}, provenance={}, now=datetime(2026, 8, 21, 9, 26), db_path=db)


def test_playbook_cutoff_is_absolute_cn_time_even_when_no_checklist_was_written(tmp_path):
    db = tmp_path / "db.sqlite"; init_schema(db); _batch(db)
    with pytest.raises(packages.PackageConflict):
        packages.append_user_playbook_revision(batch_id="b", ts_code="000001.SZ", playbook={}, provenance={},
                                               now=datetime(2026, 8, 21, 1, 26, tzinfo=timezone.utc), db_path=db)
    with pytest.raises(packages.PackageConflict):
        packages.append_user_playbook_revision(batch_id="b", ts_code="000001.SZ", playbook={}, provenance={},
                                               now=datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc), db_path=db)


def test_morning_windows_convert_non_china_host_clock_before_date_and_time_checks(monkeypatch):
    monkeypatch.setattr(auction_pipeline, "is_trading_day", lambda day: day == D1)
    monkeypatch.setattr(settle, "is_trading_day", lambda day: day == D1)
    # 01:26/02:00 UTC are Shanghai 09:26/10:00, independent of host timezone.
    assert auction_pipeline.is_auction_window(datetime(2026, 8, 21, 1, 26, tzinfo=timezone.utc))
    assert settle.is_settle_window(datetime(2026, 8, 21, 2, 0, tzinfo=timezone.utc))
    assert not settle.is_settle_window(datetime(2026, 8, 21, 2, 5, tzinfo=timezone.utc))


def test_report_requires_durable_all_stage_success(tmp_path):
    db = tmp_path / "db.sqlite"; init_schema(db); _batch(db)
    packages.record_selection_run(selection_date=DAY, signal_trade_date=DAY, state="has_list", batch_id="b", db_path=db)
    attempt = lifecycle.begin_attempt(selection_date=DAY, signal_trade_date=DAY, run_identity="fourth-review", db_path=db)
    lifecycle.record_attempt(attempt_id=attempt, outcomes=(lifecycle.StageOutcome("d2", True), lifecycle.StageOutcome("d1", False, "ticks missing"), lifecycle.StageOutcome("d0", True)), db_path=db)
    report = pipeline.build_report(DAY, report_date=DAY, params_path=tmp_path / "params.json", db_path=db)
    assert report.state.value == "not_run"
    assert "上游生命周期未成功" in "；".join(report.gaps)
