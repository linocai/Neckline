"""Second independent-review regressions for the K9-v3 release candidate."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from neckline.auction import recorder, settle
from neckline.data.market_data import day_file_path, get_market_slice
from neckline.data.realtime import Quote
from neckline.db import init_schema
from neckline.k9 import v3_params
from neckline.k9 import v3_run
from neckline.report import evening, pipeline
from neckline.scorecard import packages, lifecycle
from tests.test_k9_v3_facts_and_params import _approved


DAY = date(2026, 8, 20)


def _candidate(*, code: str = "000001.SZ") -> packages.Candidate:
    return packages.Candidate(
        code, "示例", None, None, ["p2"], {"p2": 1},
        {"revision": 1, "invalidation": 9.0, "firstResistance": 11.0, "secondResistance": 12.0,
         "openVerdict": {"rejectBelow": 9.0, "confirmRange": {"minimum": 9.5, "maximum": 10.5},
                         "unbuyableAtOrAbove": 11.0}},
        {"close": 10.0, "limit_up_price": 11.0}, {},
    )


def _batch(db: Path, *, batch_id: str = "b", candidates=None, selection: date = DAY) -> None:
    packages.create_batch(
        batch_id=batch_id, selection_date=selection, signal_trade_date=DAY,
        d1_trade_date=date(2026, 8, 21), d2_trade_date=date(2026, 8, 24), revision=1,
        params_package_version="r1", params_sha256="sha", pack_id="fp4",
        frozen_contract={"parameters": _approved()},
        candidates=[_candidate()] if candidates is None else candidates, db_path=db,
    )


def test_pre_release_d1_check_migrates_and_accepts_unavailable(tmp_path):
    db = tmp_path / "pre-release.db"; init_schema(db); _batch(db)
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("ALTER TABLE k9_selection_d1 RENAME TO old_d1")
        conn.execute("""CREATE TABLE k9_selection_d1 (
          batch_id TEXT NOT NULL, ts_code TEXT NOT NULL,
          checklist_verdict TEXT NOT NULL CHECK(checklist_verdict IN ('rejected','unbuyable','pending_open')),
          open_verdict TEXT CHECK(open_verdict IN ('confirmed','rejected','observed','unbuyable')),
          reference_price REAL, close_state TEXT, raw_json TEXT NOT NULL, captured_at TEXT NOT NULL,
          PRIMARY KEY(batch_id,ts_code))""")
        conn.execute("DROP TABLE old_d1")
        conn.execute("PRAGMA foreign_keys=ON")
    init_schema(db)
    packages.append_d1(batch_id="b", ts_code="000001.SZ", checklist_verdict="pending_open",
                       open_verdict="unavailable", reference_price=None, raw={"reason": "feed"}, db_path=db)
    assert packages.load_package("b", db_path=db)["candidates"][0]["d1"]["openVerdict"] == "unavailable"


def test_open_verdict_is_fully_driven_by_frozen_playbook_and_priority():
    candidate = {"playbook": _candidate().playbook}
    assert settle._open_verdict("pending_open", {"feedStatus": "unavailable"}, candidate)[0] == "unavailable"
    assert settle._open_verdict("pending_open", {"limitUpPrice": 11, "trades": [{"time": "09:31", "price": 11}]}, candidate)[0] == "unbuyable"
    assert settle._open_verdict("pending_open", {"limitUpPrice": 11, "trades": [{"time": "09:31", "price": 8.9}]}, candidate) == ("rejected", None, 8.9)
    assert settle._open_verdict("pending_open", {"limitUpPrice": 11, "trades": [{"time": "10:00", "price": 10.0}]}, candidate) == ("confirmed", 10.0, 10.0)
    assert settle._open_verdict("pending_open", {"limitUpPrice": 11, "trades": [{"time": "10:00", "price": 10.7}]}, candidate) == ("observed", 10.7, 10.7)
    assert settle._open_verdict("rejected", {"limitUpPrice": 11, "trades": [{"time": "10:00", "price": 10.0}]}, candidate) == ("rejected", None, 10.0)


def test_successful_empty_package_is_settled_without_d1_task_or_pushable_active_queue(tmp_path):
    db = tmp_path / "empty.db"; init_schema(db)
    _batch(db, candidates=[])
    package = packages.load_package("b", db_path=db)
    assert package["state"] == "settled" and package["coverage_state"] == "complete"
    assert packages.list_packages(state="active", db_path=db) == []
    assert [row["batch_id"] for row in packages.list_packages(state="settled", db_path=db)] == ["b"]


def test_successful_lifecycle_identity_never_downgrades_or_reenters_d0(tmp_path, monkeypatch):
    db = tmp_path / "lifecycle-ok.db"; init_schema(db)
    identity = f"nightly:{DAY:%Y%m%d}:{DAY:%Y%m%d}"
    attempt = lifecycle.begin_attempt(selection_date=DAY, signal_trade_date=DAY, run_identity=identity, db_path=db)
    good = tuple(lifecycle.StageOutcome(stage, True) for stage in ("d2", "d1", "d0"))
    lifecycle.record_attempt(attempt_id=attempt, outcomes=good, db_path=db)
    lifecycle.record_attempt(attempt_id=attempt, outcomes=(lifecycle.StageOutcome("d2", False, "later fault"),), db_path=db)
    frozen = lifecycle.latest_attempt(selection_date=DAY, signal_trade_date=DAY, db_path=db)
    assert frozen["status"] == "ok" and frozen["stages"]["d2"]["status"] == "ok"
    monkeypatch.setattr(evening, "_create_d0", lambda **_kwargs: pytest.fail("successful identity must short-circuit"))
    status, detail = evening._run_k9_lifecycle(DAY, report_date=DAY, k9_params_path=None, db_path=db, parquet_dir=None)
    assert status == evening.STATUS_OK and detail["reason"] == "already_succeeded"


def test_later_failed_identity_cannot_shadow_an_earlier_success(tmp_path):
    db = tmp_path / "lifecycle-success-wins.db"; init_schema(db)
    successful = lifecycle.begin_attempt(
        selection_date=DAY, signal_trade_date=DAY, run_identity="successful", db_path=db,
    )
    lifecycle.record_attempt(
        attempt_id=successful,
        outcomes=tuple(lifecycle.StageOutcome(stage, True) for stage in ("d2", "d1", "d0")),
        db_path=db,
    )
    failed = lifecycle.begin_attempt(
        selection_date=DAY, signal_trade_date=DAY, run_identity="later-failed", db_path=db,
    )
    lifecycle.record_attempt(
        attempt_id=failed,
        outcomes=(lifecycle.StageOutcome("d0", False, "later fault"),), db_path=db,
    )
    authoritative = lifecycle.latest_attempt(selection_date=DAY, signal_trade_date=DAY, db_path=db)
    assert authoritative["attemptId"] == successful and authoritative["status"] == "ok"


def test_lifecycle_aggregates_durable_stage_success_across_recovery_attempts(tmp_path):
    db = tmp_path / "lifecycle-recovery.db"; init_schema(db)
    attempt = lifecycle.begin_attempt(selection_date=DAY, signal_trade_date=DAY,
                                      run_identity="recovery", db_path=db)
    lifecycle.record_attempt(attempt_id=attempt, outcomes=(
        lifecycle.StageOutcome("d2", True), lifecycle.StageOutcome("d1", False, "first feed fault"),
        lifecycle.StageOutcome("d0", True),
    ), db_path=db)
    lifecycle.record_attempt(attempt_id=attempt, outcomes=(
        lifecycle.StageOutcome("d2", False, "late retry fault"), lifecycle.StageOutcome("d1", True),
    ), db_path=db)
    state = lifecycle.latest_attempt(selection_date=DAY, signal_trade_date=DAY, db_path=db)
    assert state["status"] == "ok"
    assert {key: value["status"] for key, value in state["stages"].items()} == {"d2": "ok", "d1": "ok", "d0": "ok"}


def test_lifecycle_begin_is_atomic_under_two_connection_race(tmp_path):
    db = tmp_path / "lifecycle-race.db"; init_schema(db)
    def begin() -> str:
        return lifecycle.begin_attempt(selection_date=DAY, signal_trade_date=DAY,
                                       run_identity="same-run", db_path=db)
    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(lambda _value: begin(), range(2)))
    assert ids[0] == ids[1]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k9_lifecycle_attempts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM k9_lifecycle_stages WHERE attempt_id=?", (ids[0],)).fetchone()[0] == 3


def test_minimum_history_includes_global_listing_proof_but_ignores_disabled_channels(monkeypatch):
    raw = _approved()
    raw["boundary"]["activity"]["windowDays"] = 10
    raw["channels"]["p2"]["recall"]["volumeBaselineDays"] = 20
    raw["channels"]["p3"]["identity"]["windowDays"] = 90
    params = v3_params.V3Params("r1", "sha", raw)
    requested: list[int] = []
    monkeypatch.setattr(v3_run, "_require_facts", lambda *_args, **kwargs: (requested.append(kwargs["minimum_days"]) or (object(), pl.DataFrame())))
    monkeypatch.setattr(v3_run, "_boundary", lambda *_args: pl.DataFrame())
    monkeypatch.setattr(v3_run.packages, "recent_locked_codes", lambda **_kwargs: set())
    monkeypatch.setattr(v3_run, "_p2", lambda *_args, **_kwargs: [])
    v3_run.compute(DAY, selection_date=DAY, params=params)
    assert requested == [40]
    raw["channels"]["p3"]["enabled"] = True
    monkeypatch.setattr(v3_run, "_p3", lambda *_args, **_kwargs: [])
    v3_run.compute(DAY, selection_date=DAY, params=params)
    assert requested == [40, 90]


def test_report_only_calls_empty_when_marker_and_empty_package_agree(tmp_path):
    db = tmp_path / "report.db"; init_schema(db)
    _batch(db, candidates=[])
    packages.record_selection_run(selection_date=DAY, signal_trade_date=DAY, state="empty", batch_id="b", reason="", db_path=db)
    attempt = lifecycle.begin_attempt(selection_date=DAY, signal_trade_date=DAY, run_identity="test-empty", db_path=db)
    lifecycle.record_attempt(attempt_id=attempt, outcomes=(
        lifecycle.StageOutcome("d2", True), lifecycle.StageOutcome("d1", False, "temporary"), lifecycle.StageOutcome("d0", True),
    ), db_path=db)
    lifecycle.record_attempt(attempt_id=attempt, outcomes=(
        lifecycle.StageOutcome("d2", False, "late retry"), lifecycle.StageOutcome("d1", True),
    ), db_path=db)
    assert lifecycle.latest_attempt(selection_date=DAY, signal_trade_date=DAY, db_path=db)["status"] == "ok"
    report = pipeline.build_report(DAY, report_date=DAY, db_path=db)
    assert report.state.value == "empty" and report.listing_size == 0
    missing = pipeline.build_report(date(2026, 8, 21), report_date=date(2026, 8, 21), db_path=db)
    assert missing.state.value == "not_run" and missing.listing_size is None


def test_params_reject_channel_maxima_and_p4_industry_cap_contract():
    raw = _approved()
    raw["quotas"]["p2"] = 6
    assert any("quotas.p2 不能超过 5" in x for x in v3_params.validate(raw)[1])
    raw = _approved(); raw["quotas"]["p3"] = 9; raw["channels"]["p3"]["enabled"] = True
    assert any("quotas.p3 不能超过 8" in x for x in v3_params.validate(raw)[1])
    raw = _approved(); raw["channels"]["p4"]["enabled"] = True; raw["quotas"]["p4"] = 7
    assert any("maxIndustries" in x for x in v3_params.validate(raw)[1])


def test_weekend_lock_uses_public_selection_date_not_signal_trade_date(tmp_path):
    db = tmp_path / "lock.db"; init_schema(db)
    _batch(db, batch_id="fri", selection=DAY)
    assert packages.recent_locked_codes(before_selection_date=date(2026, 8, 23), db_path=db) == {"000001.SZ"}


def test_recorder_freezes_real_provider_snapshot_and_audits_missing_source(tmp_path, monkeypatch):
    db, parquet = tmp_path / "ticks.db", tmp_path / "parquet"; init_schema(db); _batch(db)
    now = datetime(2026, 8, 21, 10, 0, 0)
    monkeypatch.setattr(recorder, "is_capture_window", lambda value, **_kwargs: True)
    quote = Quote("000001.SZ", "示例", 10.0, 9.8, 9.9, 10.1, 9.7, 100.0, 1000.0,
                  "2026-08-21 10:00:00", "sina", traded_price=10.0)
    monkeypatch.setattr(recorder, "get_quotes", lambda codes: {"000001.SZ": quote})
    first = recorder.record_snapshot(now.replace(hour=9, minute=26), db_path=db, parquet_dir=parquet)
    quote.volume += 1; quote.amount += 10
    second = recorder.record_snapshot(now, db_path=db, parquet_dir=parquet)
    frame = get_market_slice(DAY + (date(2026, 8, 21) - DAY), table="intraday_ticks", parquet_dir=parquet)
    assert first.captured == 0 and second.captured == 1 and len(frame.filter(pl.col("ts_code") == "000001.SZ")) == 2
    with sqlite3.connect(db) as conn:
        statuses = dict(conn.execute("SELECT ts_code,status FROM k9_intraday_capture_audit"))
    # The package-wide benchmark is a D1/D2 settlement input for every channel,
    # even when P4 recall is disabled.
    assert statuses == {"000001.SH": "unavailable", "000001.SZ": "captured"}


def test_recorder_first_snapshot_does_not_scan_incompatible_historical_partitions(tmp_path, monkeypatch):
    db, parquet = tmp_path / "ticks.db", tmp_path / "parquet"; init_schema(db); _batch(db)
    # Reproduce production history: old files in the same year disagree on a
    # legacy column. They are irrelevant to the target day's first baseline.
    first_old = day_file_path("intraday_ticks", date(2026, 8, 19), parquet)
    second_old = day_file_path("intraday_ticks", date(2026, 8, 20), parquet)
    first_old.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"trade_date": [date(2026, 8, 19)], "legacy": [1]}).write_parquet(first_old)
    pl.DataFrame({"trade_date": [date(2026, 8, 20)], "legacy": ["old"]}).write_parquet(second_old)

    monkeypatch.setattr(recorder, "is_capture_window", lambda value, **_kwargs: True)
    quote = Quote("000001.SZ", "示例", 10.0, 9.8, 9.9, 10.1, 9.7, 100.0, 1000.0,
                  "2026-08-21 09:26:00", "sina", traded_price=10.0)
    monkeypatch.setattr(recorder, "get_quotes", lambda codes: {"000001.SZ": quote})

    result = recorder.record_snapshot(datetime(2026, 8, 21, 9, 26), db_path=db, parquet_dir=parquet)

    assert result.ran and day_file_path("intraday_ticks", date(2026, 8, 21), parquet).exists()
    with sqlite3.connect(db) as conn:
        reasons = {row[0] for row in conn.execute(
            "SELECT reason FROM k9_intraday_capture_audit WHERE trade_date='20260821'"
        )}
    assert "existing_partition_unreadable" not in reasons


def test_p2_only_package_records_the_frozen_benchmark_for_d2(tmp_path):
    db = tmp_path / "p2-benchmark.db"; init_schema(db); _batch(db)
    assert recorder._due_codes(date(2026, 8, 21), db_path=db) == {"000001.SZ", "000001.SH"}


def test_production_recorder_is_single_process_service_entry_not_a_mock_or_extra_unit():
    root = Path(__file__).resolve().parents[1]
    app = (root / "neckline" / "api" / "app.py").read_text(encoding="utf-8")
    unit = (root / "deploy" / "neckline.service").read_text(encoding="utf-8")
    assert "_intraday_capture_loop" in app and "record_snapshot" in app
    assert "--workers" not in unit and "auction-recorder.service" not in "\n".join(p.name for p in (root / "deploy").iterdir())


def test_selection_copy_reads_v3_baseline_and_top_level_frozen_playbook():
    from neckline.api.app import _selection_copy_text

    text = _selection_copy_text({
        "headline": "今天有这些 · 1 只", "reportDate": "20260820", "tradeDate": "20260820",
        "stocks": [{"tsCode": "000001.SZ", "name": "示例", "baseline": {"close": 10.0},
                    "playbook": {"revision": 2, "invalidation": 9.0,
                                 "firstResistance": 11.0, "secondResistance": 12.0}}],
    })
    assert "收盘价（截至行情日）：10.00" in text
    assert "失效价 9.0；第一压力位 11.0；第二压力位 12.0" in text
    assert "预案第 2 版" in text and "资料暂未生成" not in text
