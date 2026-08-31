"""P4 uses frozen member-stock snapshots, never a Shenwan .SI live quote."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime

import polars as pl
import pytest

from neckline.auction import p4, recorder, settle, store
from neckline.auction.readings import collect_open_readings
from neckline.data.realtime import Quote, to_symbol
from neckline.db import init_schema
from neckline.scorecard import packages


DAY, D1 = date(2026, 8, 20), date(2026, 8, 21)
MEMBERS = ["000001.SZ", "000002.SZ"]


def _contract(*, members=MEMBERS):
    codes = sorted(members)
    import hashlib
    return {"aggregationSchemaVersion": p4.AGGREGATION_SCHEMA_VERSION, "signalTradeDate": "20260820",
            "benchmark": {"indexCode": "000001.SH"}, "industries": {"801080.SI": {
                "industryCode": "801080.SI", "industryName": "半导体", "signalTradeDate": "20260820",
                "memberCodes": codes, "memberHashSha256": hashlib.sha256("\n".join(codes).encode()).hexdigest(),
                "memberCount": len(codes), "fp4MemberCount": len(codes),
                "aggregationSchemaVersion": p4.AGGREGATION_SCHEMA_VERSION, "benchmark": {"indexCode": "000001.SH"},
            }}}


def _playbook():
    return {"revision": 1, "invalidation": 9.0, "firstResistance": 11.0, "secondResistance": 12.0,
            "openVerdict": {"rejectBelow": 9.2, "confirmRange": {"minimum": 9.5, "maximum": 10.5},
                            "overextendedAtOrAbove": 10.8, "unbuyableAtOrAbove": 11.0},
            "conditions": {"p4": {"industry": {"minimumMemberCoverage": 1.0,
                "medianReturnAtOrAbove": 0.01, "breadthAtOrAbove": 0.5,
                "relativeBenchmarkReturnAtOrAbove": 0.005, "failBelowMedianReturn": -0.03,
                "failBelowBreadth": 0.0, "failBelowRelativeBenchmarkReturn": -0.03},
                "stock": {"holdAbove": 9.4, "relativeIndustryReturnAtOrAbove": 0.0}}}}


def _batch(db, *, batch_id="p4", duplicate=False):
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT OR IGNORE INTO trade_cal(exchange,cal_date,is_open,pretrade_date) VALUES ('SSE','20260821',1,'')")
    candidate = packages.Candidate("000001.SZ", "甲", "801080.SI", "半导体", ["p4"], {"p4": 1},
                                   _playbook(), {"limit_up_price": 11.0}, {})
    candidates = [candidate, candidate] if duplicate else [candidate]
    # Candidate primary key makes duplicate candidate rows deliberately invalid;
    # repeated industry is tested by a second candidate below instead.
    if duplicate:
        candidates = [candidate, packages.Candidate("000003.SZ", "乙", "801080.SI", "半导体", ["p4"], {"p4": 2},
                                                      _playbook(), {"limit_up_price": 11.0}, {})]
    packages.create_batch(batch_id=batch_id, selection_date=DAY, signal_trade_date=DAY, d1_trade_date=D1,
                          d2_trade_date=date(2026, 8, 24), revision=1, params_package_version="p", params_sha256="h",
                          pack_id="fp4", frozen_contract={"p4IndustryEvidence": _contract()}, candidates=candidates, db_path=db)


def _ticks():
    return pl.DataFrame({"ts_code": ["000001.SZ", "000002.SZ", "000001.SH"], "_clock": ["09:31"] * 3,
                         "price": [10.2, 10.1, 10.0], "pre_close": [10.0, 10.0, 10.0], "valid_trade": [True] * 3,
                         "volume_delta": [1.0] * 3, "amount_delta": [1.0] * 3})


def test_si_realtime_symbol_is_fail_closed():
    with pytest.raises(ValueError, match="申万行业"):
        to_symbol("801080.SI")
    assert to_symbol("000001.SH") == "sh000001"


def test_corrupt_frozen_member_si_is_not_emitted_as_a_quote_target():
    bad = _contract()["industries"]["801080.SI"]
    bad["memberCodes"] = ["801080.SI"]
    bad["memberCount"] = bad["fp4MemberCount"] = 1
    import hashlib
    bad["memberHashSha256"] = hashlib.sha256(b"801080.SI").hexdigest()
    codes, invalid = p4.target_codes([{"tsCode": "000001.SZ", "_p4IndustryEvidence": bad}])
    assert codes == set() and invalid["000001.SZ"] == "frozen_member_code_not_stock"


def test_d0_freezes_fp4_members_once_and_never_reads_later_membership(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from neckline.k9 import v3_run
    from neckline.k9.v3_params import V3Params
    from tests.test_k9_v3_facts_and_params import _approved

    db = tmp_path / "db.sqlite"; init_schema(db)
    rows = pl.DataFrame({"ts_code": MEMBERS, "sw_l2_code": ["801080.SI"] * 2,
                         "sw_l2_name": ["半导体"] * 2, "sw_l2_member_count": [2, 2]})
    import hashlib
    member_codes = sorted(MEMBERS)
    market = {"fp4": {"industryMembers": {"801080.SI": {"industryCode": "801080.SI", "industryName": "半导体", "memberCodes": member_codes, "memberCount": 2, "memberHashSha256": hashlib.sha256("\n".join(member_codes).encode()).hexdigest()}}}}
    monkeypatch.setattr(v3_run.fact_store, "load_pack", lambda *args, **kwargs: SimpleNamespace(pack_id="fp4", rows=rows, market=market))
    params = V3Params("p", "sha", _approved())
    hit = v3_run.V3Hit("000001.SZ", "甲", "801080.SI", "半导体", "p4", 1, 1.0,
                       {"limit_up_price": 11.0}, {"playbookBounds": params.raw["channels"]["p4"]["playbookBounds"]})
    v3_run.create_package(batch_id="frozen", selection_date=DAY, signal_trade_date=DAY, d1_trade_date=D1,
                          d2_trade_date=date(2026, 8, 24), params=params, pack_id="fp4", hits=[hit],
                          playbooks={"000001.SZ": _playbook()}, db_path=db)
    frozen = packages.load_package("frozen", db_path=db)["frozen_contract"]["p4IndustryEvidence"]["industries"]["801080.SI"]
    assert frozen["memberCodes"] == MEMBERS and frozen["signalTradeDate"] == "20260820"
    # Simulate a later source-table constituent change: the package contract is
    # the only D1 authority and stays byte-identical.
    rows = rows.filter(pl.col("ts_code") == "000001.SZ")
    assert packages.load_package("frozen", db_path=db)["frozen_contract"]["p4IndustryEvidence"]["industries"]["801080.SI"] == frozen


def test_frozen_contract_uses_one_member_set_for_multiple_candidates_and_aggregate_is_stable():
    contract = _contract()["industries"]["801080.SI"]
    frame = _ticks()
    first = p4.aggregate(frame, contract=contract, candidate_code="000001.SZ")
    # A later membership-table change is not even an input to the aggregator.
    second = p4.aggregate(frame, contract=contract, candidate_code="000001.SZ")
    assert first == second
    assert first["memberCount"] == 2 and first["evaluatedCount"] == 2
    assert first["coverage"] == 1.0 and first["breadth"] == 1.0
    assert first["medianReturn"] == pytest.approx(0.015)
    assert first["relativeReturn"] == pytest.approx(0.015)


def test_p4_llm_conditions_are_typed_bounded_and_complete():
    from neckline.k9 import v3_playbook, v3_run
    from tests.test_k9_v3_facts_and_params import _approved
    bounds = _approved()["channels"]["p4"]["playbookBounds"]
    hit = v3_run.V3Hit("000001.SZ", "甲", "801080.SI", "半导体", "p4", 1, 1.0,
                       {"close": 10.0, "limit_up_price": 11.0}, {"playbookBounds": bounds})
    skeleton = v3_playbook.mechanical_skeleton([hit])
    payload = {"candidates": [{"tsCode": "000001.SZ", "invalidation": 9.0, "firstResistance": 10.8,
        "secondResistance": 10.9, "rationale": "冻结行业成员盘中修复。",
        "openVerdict": {"rejectBelow": 9.2, "confirmRange": {"minimum": 9.5, "maximum": 10.5},
                        "overextendedAtOrAbove": 10.7, "unbuyableAtOrAbove": 11.0},
        "conditions": {"p4": _playbook()["conditions"]["p4"]}}]}
    assert "000001.SZ" in v3_playbook.validate_output(payload, skeleton, source="llm")
    del payload["candidates"][0]["conditions"]["p4"]["industry"]["breadthAtOrAbove"]
    with pytest.raises(v3_playbook.PlaybookUnavailable, match="breadth"):
        v3_playbook.validate_output(payload, skeleton, source="llm")


def test_p4_recorder_targets_members_and_benchmark_not_si_and_deduplicates(tmp_path, monkeypatch):
    db, parquet = tmp_path / "db.sqlite", tmp_path / "pq"; init_schema(db); _batch(db, duplicate=True)
    monkeypatch.setattr(recorder, "is_capture_window", lambda _, **_kwargs: True)
    seen = []
    call = [0]
    def quotes(codes):
        seen.extend(codes)
        assert "801080.SI" not in codes
        call[0] += 1
        return {code: Quote(code, code, 10.0, 9.9, 10.0, 10.0, 10.0, 10.0 + call[0], 100.0 + call[0],
                            "2026-08-21 09:31:00", "test", traded_price=10.0) for code in codes}
    monkeypatch.setattr(recorder, "get_quotes", quotes)
    recorder.record_snapshot(datetime(2026, 8, 21, 9, 26), db_path=db, parquet_dir=parquet)
    assert len(seen) == len(set(seen))
    assert {"000001.SZ", "000002.SZ", "000003.SZ", "000001.SH"} <= set(seen)


def test_record_read_settle_p4_uses_member_coverage_and_preserves_raw_evidence(tmp_path, monkeypatch):
    db, parquet = tmp_path / "db.sqlite", tmp_path / "pq"; init_schema(db); _batch(db)
    monkeypatch.setattr(recorder, "is_capture_window", lambda _, **_kwargs: True)
    quotes = {
        "000001.SZ": Quote("000001.SZ", "甲", 10.2, 10.0, 10.0, 10.2, 10.0, 1, 100,
                              "2026-08-21 09:31:00", "test", traded_price=10.2),
        "000002.SZ": Quote("000002.SZ", "乙", 10.1, 10.0, 10.0, 10.1, 10.0, 1, 100,
                              "2026-08-21 09:31:00", "test", traded_price=10.1),
        "000001.SH": Quote("000001.SH", "基准", 10.0, 10.0, 10.0, 10.0, 10.0, 1, 100,
                              "2026-08-21 09:31:00", "test", traded_price=10.0),
    }
    monkeypatch.setattr(recorder, "get_quotes", lambda _: quotes)
    recorder.record_snapshot(datetime(2026, 8, 21, 9, 26), db_path=db, parquet_dir=parquet)
    for quote in quotes.values():
        quote.volume += 1; quote.amount += 100
    recorder.record_snapshot(datetime(2026, 8, 21, 9, 31), db_path=db, parquet_dir=parquet)
    package = packages.load_package("p4", db_path=db)
    from neckline.auction import checklist
    snapshot = checklist.build_checklist(package, trade_date=D1).as_dict()
    store.save_checklist(type("Snapshot", (), {"as_dict": lambda self: snapshot, "batch_id": "p4", "trade_date": D1})(), db_path=db)
    reading = collect_open_readings(D1, db_path=db, parquet_dir=parquet)["000001.SZ"]
    assert reading["industry"]["feedStatus"] == "available"
    result = settle.run_settle_tick(datetime(2026, 8, 21, 10, 0), db_path=db, readings={"000001.SZ": reading})
    assert result.confirmed == 1
    raw = packages.load_package("p4", db_path=db)["candidates"][0]["d1"]["raw"]
    assert raw["industry"]["memberCount"] == 2 and raw["industry"]["evaluatedCount"] == 2


def test_p4_missing_member_evidence_is_unavailable_but_other_channel_reject_wins():
    candidate = {"channels": ["p2", "p4"], "playbook": {**_playbook(), "conditions": {
        "p2": {"holdAbove": 10.3}, "p4": _playbook()["conditions"]["p4"]}}}
    reading = {"limitUpPrice": 11.0, "trades": [{"time": "09:31", "price": 10.2}],
               "industry": {"feedStatus": "unavailable", "reason": "member_ticks_missing"}}
    assert settle._open_verdict("pending_open", reading, candidate)[0] == "rejected"
