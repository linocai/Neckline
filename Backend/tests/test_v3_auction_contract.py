from __future__ import annotations

import sqlite3
from datetime import date, datetime

import pytest
import polars as pl

from neckline.auction import checklist, pipeline
from neckline.auction import eod, settle
from neckline.auction.readings import collect_d1_eod_readings
from neckline.auction import store
from neckline.data.market_data import write_table_day
from neckline.db import init_schema
from neckline.scorecard import packages


def test_v3_checklist_has_the_three_required_segments():
    package = {"batch_id": "b", "selection_date": "20260820", "signal_trade_date": "20260820",
               "candidates": [
                   {"tsCode": "a", "name": "A", "channels": ["p2"], "channelRanks": {"p2": 1}, "playbook": {}},
                   {"tsCode": "b", "name": "B", "channels": ["p3"], "channelRanks": {"p3": 1}, "playbook": {}},
               ]}
    out = checklist.build_checklist(package, trade_date=date(2026, 8, 21),
                                    readings={"a": {"auctionPrice": 1, "limitUpPrice": 1}}).as_dict()
    assert [x["verdict"] for x in out["segments"]] == ["rejected", "unbuyable", "pending_open"]
    assert out["strategyVersion"] == "K9-v3"


def test_v3_checklist_is_bound_to_a_batch_and_frozen(tmp_path):
    # The table requires a real package foreign key; lifecycle tests cover DB write.
    # This test locks the public read surface and avoids a date-addressed fallback.
    assert store.load_checklist("unknown", db_path=tmp_path / "empty.db") is None


def _batch(db, batch_id="b", candidates=None, revision=1):
    # Scheduler paths must consume this isolated database's official calendar,
    # never the workspace cache.
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT OR IGNORE INTO trade_cal(exchange,cal_date,is_open,pretrade_date) VALUES ('SSE','20260821',1,'')")
    candidate = packages.Candidate(
        "000001.SZ", "示例", None, None, ["p2"], {"p2": 1},
        {"revision": 1, "invalidation": 9.0, "firstResistance": 11.0}, {}, {},
    )
    packages.create_batch(
        batch_id=batch_id, selection_date=date(2026, 8, 20), signal_trade_date=date(2026, 8, 20),
        d1_trade_date=date(2026, 8, 21), d2_trade_date=date(2026, 8, 24), revision=revision,
        params_package_version="p", params_sha256="h", pack_id="fp",
        frozen_contract={"parameters": {"channels": {"p4": {"benchmark": {"indexCode": "000001.SH"}}}, "settlement": {
            "d1": {"enhancedReturnPct": 0.01, "enhancedCloseLocationPct": 0.6, "weakenedReturnPct": -0.01},
            "d2": {"opportunityReturnPct": 0.03, "continuationReturnPct": 0.01, "riskReturnPct": -0.04},
        }}}, candidates=[candidate] if candidates is None else candidates, db_path=db,
    )


def test_1000_requires_a_last_sub_limit_trade_and_keeps_unbuyable_separate():
    assert settle._valid_reference({"limitUpPrice": 11, "trades": [
        {"time": "09:31", "price": 10.2}, {"time": "10:00", "price": 10.4},
    ]}) == 10.4
    assert settle._valid_reference({"limitUpPrice": 11, "trades": [{"time": "09:31", "price": 11}]}) is None


def test_rejected_candidate_keeps_a_nontradable_diagnostic_baseline_for_d2():
    candidate = {"playbook": {"openVerdict": {
        "rejectBelow": 9.0, "confirmRange": {"minimum": 9.5, "maximum": 10.5},
        "unbuyableAtOrAbove": 11.0,
    }}}
    verdict, tradeable_reference, diagnostic_reference = settle._open_verdict(
        "rejected", {"limitUpPrice": 11, "trades": [{"time": "09:31", "price": 10.0}]}, candidate,
    )
    assert (verdict, tradeable_reference, diagnostic_reference) == ("rejected", None, 10.0)


def test_d1_close_and_d2_are_separate_appends_with_independent_risk(tmp_path):
    db = tmp_path / "test.db"; init_schema(db); _batch(db)
    packages.append_d1(batch_id="b", ts_code="000001.SZ", checklist_verdict="pending_open",
                       open_verdict="confirmed", reference_price=10.0, raw={}, db_path=db)
    assert eod.settle_d1_close_for_due(
        trade_date=date(2026, 8, 21),
        readings={"000001.SZ": {"close": 10.3, "postOpenHigh": 10.4, "postOpenLow": 10.0, "benchmarkReferencePrice": 100.0}}, db_path=db,
    ) == 1
    assert eod.settle_d2_for_due(
        trade_date=date(2026, 8, 24),
        readings={"000001.SZ": {"postOpenHigh": 10.5, "postOpenLow": 9.5, "close": 10.2, "benchmarkClosePrice": 101.0}}, db_path=db,
    ) == 1
    item = packages.load_package("b", db_path=db)["candidates"][0]
    assert item["d1"]["openVerdict"] == "confirmed"
    assert item["d1"]["closeState"] == "enhanced"
    assert item["d2"]["selectionResult"] == "success_realized"
    assert item["d2"]["riskTag"] == "risk"
    assert item["d2"]["raw"]["d1ReferencePrice"] == 10.0
    assert item["d1"]["closeRaw"]["postOpenLow"] == 10.0
    assert item["d2"]["raw"]["maxDrawdown"] == pytest.approx(-0.05)
    assert item["d2"]["raw"]["benchmarkReturn"] == pytest.approx(0.01)
    assert item["d2"]["raw"]["relativeBenchmark"] == pytest.approx(0.01)


def test_d1_eod_reads_frozen_benchmark_with_keyword_only_production_call_and_can_append_close(tmp_path):
    db, parquet = tmp_path / "db.sqlite", tmp_path / "pq"; init_schema(db); _batch(db)
    packages.append_d1(batch_id="b", ts_code="000001.SZ", checklist_verdict="pending_open",
                       open_verdict="confirmed", reference_price=10.0, raw={}, db_path=db)
    day = date(2026, 8, 21)
    write_table_day("intraday_ticks", day, pl.DataFrame({
        "ts_code": ["000001.SZ", "000001.SH"], "trade_date": [day, day],
        "event_time": ["10:01:00", "10:00:00"], "timestamp": ["2026-08-21T10:01:00+08:00", "2026-08-21T10:00:00+08:00"],
        "price": [10.2, 100.0], "valid_trade": [True, True], "volume_delta": [1.0, 1.0], "amount_delta": [1.0, 1.0],
    }), parquet)
    readings = collect_d1_eod_readings(day, db_path=db, parquet_dir=parquet)
    assert readings["000001.SZ"]["benchmarkReferencePrice"] == 100.0
    assert eod.settle_d1_close_for_due(trade_date=day, readings=readings, db_path=db) == 1
    assert packages.load_package("b", db_path=db)["candidates"][0]["d1"]["closeRaw"]["d1Close"] == 10.2


def test_missing_d2_is_permanently_unavailable_and_sets_coverage(tmp_path):
    db = tmp_path / "test.db"; init_schema(db); _batch(db)
    packages.append_d1(batch_id="b", ts_code="000001.SZ", checklist_verdict="unbuyable",
                       open_verdict="unbuyable", reference_price=None, raw={}, db_path=db)
    eod.settle_d2_for_due(trade_date=date(2026, 8, 24), readings={}, db_path=db)
    package = packages.load_package("b", db_path=db)
    assert package["coverage_state"] == "unavailable"
    assert package["candidates"][0]["d2"]["selectionResult"] == "unavailable"


def test_d1_close_and_d2_retries_only_append_missing_candidates(tmp_path):
    db = tmp_path / "resume.db"; init_schema(db)
    second = packages.Candidate("000002.SZ", "第二", None, None, ["p2"], {"p2": 2},
                                {"revision": 1, "invalidation": 9.0, "firstResistance": 11.0}, {}, {})
    _batch(db, candidates=[packages.Candidate("000001.SZ", "示例", None, None, ["p2"], {"p2": 1},
                                              {"revision": 1, "invalidation": 9.0, "firstResistance": 11.0}, {}, {}), second])
    for code in ("000001.SZ", "000002.SZ"):
        packages.append_d1(batch_id="b", ts_code=code, checklist_verdict="pending_open",
                           open_verdict="confirmed", reference_price=10.0, raw={}, db_path=db)
    packages.append_d1_close(batch_id="b", ts_code="000001.SZ", close_state="held",
                             raw={"d1Close": 10.0, "postOpenLow": 9.9}, db_path=db)
    readings = {"000001.SZ": {"close": 99.0, "postOpenHigh": 99.0, "postOpenLow": 99.0},
                "000002.SZ": {"close": 10.2, "postOpenHigh": 10.3, "postOpenLow": 9.8}}
    assert eod.settle_d1_close_for_due(trade_date=date(2026, 8, 21), readings=readings, db_path=db) == 1
    package = packages.load_package("b", db_path=db)
    assert {x["tsCode"]: x["d1"]["closeRaw"]["d1Close"] for x in package["candidates"]} == {"000001.SZ": 10.0, "000002.SZ": 10.2}
    packages.append_d2(batch_id="b", ts_code="000001.SZ", selection_result="unavailable",
                       playbook_result="unavailable", risk_tag=None, raw={"frozen": True}, db_path=db)
    d2 = {"000001.SZ": {"close": 99.0}, "000002.SZ": {"close": 10.2, "postOpenHigh": 10.4,
          "postOpenLow": 9.7, "benchmarkClosePrice": 101.0}}
    assert eod.settle_d2_for_due(trade_date=date(2026, 8, 24), readings=d2, db_path=db) == 1
    package = packages.load_package("b", db_path=db)
    assert next(x for x in package["candidates"] if x["tsCode"] == "000001.SZ")["d2"]["raw"] == {"frozen": True}


def test_1000_retry_records_completion_after_final_d1_append_crash_window(tmp_path):
    db = tmp_path / "event.db"; init_schema(db); _batch(db)
    packages.freeze_checklist_atomic(packages.load_package("b", db_path=db), trade_date=date(2026, 8, 21), readings={},
                                     now=datetime(2026, 8, 21, 9, 26), db_path=db)
    packages.append_d1(batch_id="b", ts_code="000001.SZ", checklist_verdict="pending_open",
                       open_verdict="confirmed", reference_price=10.0, raw={"frozen": True}, db_path=db)
    replay = settle.run_settle_tick(datetime(2026, 8, 21, 10, 0), db_path=db,
                                    readings={"000001.SZ": {"limitUpPrice": 11, "trades": [{"time": "10:00", "price": 99}]}})
    assert replay.ran and replay.settled == 0
    assert settle.run_settle_tick(datetime(2026, 8, 21, 10, 1), db_path=db).skipped_reason == "already_ran"


def test_checklist_retry_after_commit_before_push_restores_real_counts_for_all_batches(tmp_path):
    db = tmp_path / "checklist-event.db"; init_schema(db); _batch(db); _batch(db, batch_id="b2", revision=2)
    clock = datetime(2026, 8, 21, 9, 26)
    packages.freeze_checklist_atomic(packages.load_package("b", db_path=db), trade_date=clock.date(),
                                     readings={}, now=clock, db_path=db)
    replay = pipeline.run_checklist_tick(datetime(2026, 8, 21, 9, 27), db_path=db)
    assert replay.ran and replay.batch_count == 2
    assert replay.counts == {"rejected": 0, "unbuyable": 0, "pendingOpen": 2}


def test_checklist_notification_failure_retries_real_stored_counts_then_marks_delivery(tmp_path, monkeypatch):
    import neckline.api.app as app_mod
    from neckline.api.notify import NotifyOutcome
    from neckline.dedup import already_pushed
    from neckline.auction import readings

    db = tmp_path / "checklist-notify.db"; init_schema(db); _batch(db)
    monkeypatch.setattr(app_mod, "_DB_PATH_OVERRIDE", db)
    monkeypatch.setattr(readings, "collect_auction_readings", lambda *_args, **_kwargs: {})
    outcomes = [NotifyOutcome(failed=1), NotifyOutcome(sent=1, delivery_complete=True)]
    captured: list[dict] = []
    def push(counts, **_kwargs):
        captured.append(dict(counts))
        return outcomes.pop(0)
    monkeypatch.setattr(app_mod.notify, "push_checklist_summary", push)
    now = datetime(2026, 8, 21, 9, 26)
    app_mod._morning_checklist_tick(now)
    assert not already_pushed(now.date(), pipeline.AUCTION_SCOPE, "", pipeline.EVENT_CHECKLIST, db_path=db)
    app_mod._morning_checklist_tick(now.replace(second=30))
    assert already_pushed(now.date(), pipeline.AUCTION_SCOPE, "", pipeline.EVENT_CHECKLIST, db_path=db)
    assert captured == [{"rejected": 0, "unbuyable": 0, "pendingOpen": 1}] * 2


def test_checklist_partial_fanout_records_each_success_before_retry(tmp_path, monkeypatch):
    import neckline.api.app as app_mod
    from neckline.api.notify import NotifyOutcome
    from neckline.dedup import already_pushed
    from neckline.auction import readings

    db = tmp_path / "checklist-partial.db"; init_schema(db); _batch(db)
    monkeypatch.setattr(app_mod, "_DB_PATH_OVERRIDE", db)
    monkeypatch.setattr(readings, "collect_auction_readings", lambda *_args, **_kwargs: {})
    from neckline.dedup import device_delivery_key, delivered_device_keys
    accepted = device_delivery_key("accepted")
    captured_skips: list[set[str]] = []
    outcomes = [
        NotifyOutcome(sent=1, failed=1, delivered_device_keys=(accepted,)),
        NotifyOutcome(sent=1, delivery_complete=True),
    ]
    def push(*_args, **kwargs):
        captured_skips.append(set(kwargs["skip_device_keys"]))
        return outcomes.pop(0)
    monkeypatch.setattr(app_mod.notify, "push_checklist_summary", push)
    now = datetime(2026, 8, 21, 9, 26)
    app_mod._morning_checklist_tick(now)
    assert not already_pushed(now.date(), pipeline.AUCTION_SCOPE, "", pipeline.EVENT_CHECKLIST, db_path=db)
    assert delivered_device_keys(now.date(), pipeline.AUCTION_SCOPE, "", pipeline.EVENT_CHECKLIST, db_path=db) == {accepted}
    app_mod._morning_checklist_tick(now.replace(second=30))
    assert already_pushed(now.date(), pipeline.AUCTION_SCOPE, "", pipeline.EVENT_CHECKLIST, db_path=db)
    assert captured_skips == [set(), {accepted}]


def test_d2_rejection_diagnosis_is_not_a_tradeable_k9_result(tmp_path):
    db = tmp_path / "test.db"; init_schema(db); _batch(db)
    packages.append_d1(batch_id="b", ts_code="000001.SZ", checklist_verdict="rejected",
                       open_verdict="rejected", reference_price=None,
                       raw={"diagnosticReferencePrice": 10.0}, db_path=db)
    assert eod.settle_d2_for_due(
        trade_date=date(2026, 8, 24),
        readings={"000001.SZ": {"postOpenHigh": 10.1, "postOpenLow": 9.7, "close": 9.9, "benchmarkClosePrice": 100.0}}, db_path=db,
    ) == 1
    item = packages.load_package("b", db_path=db)["candidates"][0]
    assert item["d2"]["selectionResult"] == "unavailable"
    assert item["d2"]["raw"]["tradable"] is False
