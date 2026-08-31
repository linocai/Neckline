from datetime import date, datetime
import sqlite3
from types import SimpleNamespace

import polars as pl
import pytest

from neckline.auction import checklist, recorder, settle, store
from neckline.auction.readings import collect_open_readings
from neckline.data.realtime import Quote
from neckline.db import init_schema
from neckline.k9 import v3_playbook
from neckline.report import pipeline
from neckline.scorecard import packages


DAY = date(2026, 8, 20)
D1 = date(2026, 8, 21)


def _skeleton(code="000001.SZ"):
    return {code: {"tsCode": code, "name": "甲", "channels": ["p2"], "channelRanks": {"p2": 1},
                   "baseline": {"close": 10.0, "limit_up_price": 11.0}, "conditions": {}, "mergeRule": {}}}


def _model_output(code="000001.SZ"):
    return {"candidates": [{"tsCode": code, "invalidation": 9.0, "firstResistance": 10.7,
                              "secondResistance": 10.9, "rationale": "冻结事实下的预案。",
                              "openVerdict": {"rejectBelow": 9.2, "confirmRange": {"minimum": 9.5, "maximum": 10.3},
                                              "overextendedAtOrAbove": 10.6, "unbuyableAtOrAbove": 11.0},
                              "conditions": {"p2": {"holdAbove": 9.4}}}]}


def _batch(db):
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT OR IGNORE INTO trade_cal(exchange,cal_date,is_open,pretrade_date) VALUES ('SSE','20260821',1,'')")
    plan = v3_playbook.validate_output(_model_output(), _skeleton(), source="llm")["000001.SZ"]
    packages.create_batch(batch_id="b", selection_date=DAY, signal_trade_date=DAY, d1_trade_date=D1,
                          d2_trade_date=date(2026, 8, 24), revision=1, params_package_version="p",
                          params_sha256="sha", pack_id="fp", frozen_contract={"parameters": {"settlement": {}}},
                          candidates=[packages.Candidate("000001.SZ", "甲", None, None, ["p2"], {"p2": 1}, plan, {}, {})], db_path=db)


def test_llm_revision_user_append_and_d1_freeze_are_append_only(tmp_path):
    db = tmp_path / "db.sqlite"; init_schema(db); _batch(db)
    plan = v3_playbook.validate_output(_model_output(), _skeleton(), source="user")["000001.SZ"]
    revision = packages.append_user_playbook_revision(batch_id="b", ts_code="000001.SZ", playbook=plan,
                                                       provenance={"source": "user"}, db_path=db,
                                                       now=datetime(2026, 8, 21, 9, 25, 59))
    assert revision == 2
    frozen_revision, frozen = packages.freeze_playbook_revision(batch_id="b", ts_code="000001.SZ", db_path=db)
    assert frozen_revision == 2 and frozen["source"] == "user"
    snap = checklist.build_checklist(packages.load_package("b", db_path=db), trade_date=D1).as_dict()
    store.save_checklist(SimpleNamespace(as_dict=lambda: snap, batch_id="b", trade_date=D1), db_path=db)
    with pytest.raises(packages.PackageConflict):
        packages.append_user_playbook_revision(batch_id="b", ts_code="000001.SZ", playbook=plan,
                                               provenance={"source": "user"}, db_path=db)


def test_iso_capture_is_read_as_shanghai_trade_time_and_settles(tmp_path, monkeypatch):
    db, parquet = tmp_path / "db.sqlite", tmp_path / "pq"; init_schema(db); _batch(db)
    monkeypatch.setattr(recorder, "is_capture_window", lambda _, **_kwargs: True)
    quote = Quote("000001.SZ", "甲", 10.0, 9.8, 9.9, 10.0, 9.9, 100.0, 1000.0,
                  "2026-08-21 09:31:00", "test", traded_price=10.0)
    monkeypatch.setattr(recorder, "get_quotes", lambda _: {"000001.SZ": quote})
    recorder.record_snapshot(datetime(2026, 8, 21, 9, 26), db_path=db, parquet_dir=parquet)
    quote.volume += 1; quote.amount += 100
    recorder.record_snapshot(datetime(2026, 8, 21, 9, 31), db_path=db, parquet_dir=parquet)
    reading = collect_open_readings(D1, db_path=db, parquet_dir=parquet)["000001.SZ"]
    assert reading["feedStatus"] == "available" and reading["trades"][0]["time"] == "09:31:00"
    package = packages.load_package("b", db_path=db)
    snap = checklist.build_checklist(package, trade_date=D1).as_dict()
    store.save_checklist(SimpleNamespace(as_dict=lambda: snap, batch_id="b", trade_date=D1), db_path=db)
    result = settle.run_settle_tick(datetime(2026, 8, 21, 10, 0), db_path=db, parquet_dir=parquet,
                                    readings={"000001.SZ": reading})
    assert result.confirmed == 1


def test_report_refuses_trade_date_that_does_not_match_immutable_package(tmp_path):
    db = tmp_path / "db.sqlite"; init_schema(db); _batch(db)
    packages.record_selection_run(selection_date=DAY, signal_trade_date=DAY, state="has_list", batch_id="b", db_path=db)
    report = pipeline.build_report(date(2026, 8, 19), report_date=DAY, db_path=db)
    assert report.state.value == "not_run"
    assert "不可变成绩包不一致" in "；".join(report.gaps)
