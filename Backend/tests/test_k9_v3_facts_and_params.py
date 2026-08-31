"""V3 facts/parameters are real contracts, not fp-3 renames or permissive dicts."""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import polars as pl
import pytest

from neckline.db import init_schema
from neckline.facts import pack as fp3
from neckline.facts import store, v4
from neckline.facts import readiness
from neckline.k9 import v3_params, v3_run
from neckline.scorecard import packages
from tests.conftest import insert_namechange, insert_stock_basic, insert_sw_members, insert_trade_cal, write_daily_fixture


DAY = date(2026, 8, 28)


def _seed_v4(env) -> None:
    insert_trade_cal(env, [DAY])
    rows = []
    for code, close in (("600001.SH", 10.0), ("600002.SH", 11.0)):
        rows.append({"ts_code": code, "open": close, "high": close, "low": close, "close": close,
                     "pre_close": 10.0, "pct_chg": (close / 10 - 1) * 100, "vol": 100.0, "amount": 2_000_000.0})
    write_daily_fixture(env, "daily", DAY, rows)
    write_daily_fixture(env, "daily_basic", DAY, [{"ts_code": r["ts_code"], "turnover_rate": 2.0,
                        "turnover_rate_f": 2.0, "volume_ratio": 1.0, "circ_mv": 1.0,
                        "total_mv": 1.0, "free_share": 100_000.0} for r in rows])
    write_daily_fixture(env, "adj_factor", DAY, [{"ts_code": r["ts_code"], "adj_factor": 1.0} for r in rows])
    write_daily_fixture(env, "moneyflow_dc", DAY, [{"ts_code": r["ts_code"], "net_amount": 1.0,
                        "net_amount_rate": 1.0, "buy_elg_amount": 1.0, "buy_lg_amount": 1.0} for r in rows])
    write_daily_fixture(env, "limit_derived", DAY, pl.DataFrame(schema={"ts_code": pl.String, "trade_date": pl.Date}))
    write_daily_fixture(env, "suspend_d", DAY, pl.DataFrame(schema={"ts_code": pl.String, "trade_date": pl.Date, "suspend_type": pl.String}))
    insert_stock_basic(env, [{"ts_code": r["ts_code"], "market": "主板", "list_date": DAY, "list_status": "L"} for r in rows])
    insert_namechange(env, [{"ts_code": r["ts_code"], "name": r["ts_code"], "start_date": date(2020, 1, 1)} for r in rows])
    insert_sw_members(env, [{"ts_code": r["ts_code"], "l2_code": "801080.SI", "l2_name": "半导体", "in_date": date(2020, 1, 1)} for r in rows])
    import sqlite3
    from neckline.data.sw_industry import _snapshot_content_hash
    with sqlite3.connect(env.db_path) as conn:
        members = [(r["ts_code"], r["ts_code"], "801", "L1", "801080.SI", "半导体", "80108001.SI", "L3") for r in rows]
        conn.executemany("INSERT INTO sw_industry_member_snapshots(trade_date,ts_code,name,l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,source_fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)", [(DAY.strftime("%Y%m%d"), *row, "fixture") for row in members])
        conn.execute("INSERT INTO sw_industry_snapshot_manifests VALUES (?,?,?,?,?,?,?,?)", (DAY.strftime("%Y%m%d"), _snapshot_content_hash(DAY.strftime("%Y%m%d"), members), "fixture", "fixture", "fixture", "fixture", len(members), "fixture"))


def test_fp4_freezes_its_own_fields_and_explicit_free_float_unit(isolated_env):
    _seed_v4(isolated_env)
    built = v4.build(DAY, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert isinstance(built, fp3.CompletePack)
    assert built.pack_version == "fp-4"
    row = built.rows.filter(pl.col("ts_code") == "600001.SH").row(0, named=True)
    assert row["free_float_mv"] == 100_000 * 10_000 * 10.0
    assert row["free_float_mv_unit"] == "CNY"
    assert "listing_trade_days" not in built.rows.columns
    frozen = store.freeze_pack(built, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    again = store.load_pack(DAY, pack_version="fp-4", parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert frozen.content_fingerprint == again.content_fingerprint
    assert set(v4.PACK_COLUMNS) <= set(again.rows.columns)
    membership = next(item for item in again.sources if item["name"] == "sw_industry_member_snapshots")
    assert membership["metadata"]["sourceId"] == "fixture"
    assert again.market["fp4"]["swMembershipSource"]["contentSha256"] == membership["metadata"]["contentSha256"]
    assert readiness.preflight(DAY, pack_version="fp-4", parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path).ready
    import sqlite3
    with sqlite3.connect(isolated_env.db_path) as conn:
        conn.execute("UPDATE sw_industry_snapshot_manifests SET content_sha256='tampered' WHERE trade_date=?", (DAY.strftime("%Y%m%d"),))
    assert "内容哈希不可验证" in "；".join(readiness.preflight(DAY, pack_version="fp-4", parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path).gaps)


def test_fp4_does_not_require_lifetime_calendar_for_old_listings(isolated_env):
    _seed_v4(isolated_env)
    import sqlite3
    with sqlite3.connect(isolated_env.db_path) as conn:
        conn.execute("UPDATE stock_basic SET list_date='19901219'")
    built = v4.build(DAY, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert isinstance(built, fp3.CompletePack)
    assert set(built.rows["list_date"].to_list()) == {date(1990, 12, 19)}


def test_fp4_rejects_all_null_free_share_before_freeze(isolated_env):
    _seed_v4(isolated_env)
    write_daily_fixture(isolated_env, "daily_basic", DAY, [
        {"ts_code": code, "turnover_rate": 2.0, "turnover_rate_f": 2.0,
         "volume_ratio": 1.0, "circ_mv": 1.0, "total_mv": 1.0, "free_share": None}
        for code in ("600001.SH", "600002.SH")
    ])
    built = v4.build(DAY, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert isinstance(built, fp3.IncompletePack)
    assert any("free_float_mv(CNY) 有 2/2 行" in gap for gap in built.missing)


def test_fp4_correction_is_append_only_and_latest_reads_revision_two(isolated_env):
    _seed_v4(isolated_env)
    built = v4.build(DAY, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    original = store.freeze_pack(
        built, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    original_bytes = original.path.read_bytes()
    corrected_build = dataclasses.replace(
        built, rows=built.rows.with_columns((pl.col("amount") + 1.0).alias("amount")))
    corrected = store.freeze_correction(
        corrected_build, expected_superseded_pack_id=original.pack_id,
        correction_reason="unit population repair",
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    latest = store.load_pack(
        DAY, pack_version="fp-4", parquet_dir=isolated_env.parquet_dir,
        db_path=isolated_env.db_path)
    assert latest.pack_id == corrected.pack_id and latest.revision == 2
    assert latest.supersedes_pack_id == original.pack_id
    exact_original = store.load_pack_by_id(
        original.pack_id, parquet_dir=isolated_env.parquet_dir,
        db_path=isolated_env.db_path)
    assert exact_original.revision == 1 and exact_original.pack_id == original.pack_id
    assert original.path.read_bytes() == original_bytes
    assert corrected.path != original.path
    ranged = store.load_pack_range(
        DAY, DAY, as_of=DAY, columns=("trade_date", "ts_code", "amount"),
        pack_version="fp-4", parquet_dir=isolated_env.parquet_dir,
        db_path=isolated_env.db_path)
    assert ranged["amount"].to_list() == corrected_build.rows["amount"].to_list()


def test_k9_boundary_converts_daily_amount_from_kcny_to_cny():
    history = pl.DataFrame([{
        "trade_date": DAY, "ts_code": "600001.SH", "board": "MAIN", "is_st": False,
        "delist_risk": False, "list_date": date(2020, 1, 1), "close": 10.0,
        "valid_quote": True, "suspend_flag": "none", "sw_l2_code": "801080.SI",
        "amount": 200_000.0, "turnover_rate": 2.0, "free_float_mv": 20_000_000_000.0,
        "is_limit_up": False, "is_limit_down": False,
    }])
    cfg = {
        "newListingTradingDays": 1,
        "activity": {"windowDays": 1, "minimumValidDays": 1, "amountWeight": 0.6,
                     "participationWeight": 0.4, "excludeBottomPct": 0.0},
        "d0Liquidity": {"minimumAmountCny": 100_000_000.0,
                        "freeFloatMarketValueRatio": 0.005},
        "excludedL2Codes": [],
    }
    pool = v3_run._boundary(history, cfg)
    assert pool["ts_code"].to_list() == ["600001.SH"]


def test_d1_price_limit_is_derived_for_every_candidate_from_frozen_d0_close():
    hit = v3_run.V3Hit(
        "300710.SZ", "万隆光电", "801080.SI", "半导体", "p2", 1, 1.0,
        {"close": 31.21, "board": "GEM", "is_st": False,
         "limit_up_price": None, "limit_down_price": None}, {},
    )
    bound = v3_run.bind_d1_price_limits([hit], d1_trade_date=date(2026, 9, 1))[0]
    assert bound.baseline["limit_up_price"] == 37.45
    assert bound.baseline["limit_down_price"] == 24.97
    assert bound.baseline["price_limit_trade_date"] == "2026-09-01"


def test_failed_correction_never_saves_or_pushes_old_report(monkeypatch):
    from neckline.report import evening
    monkeypatch.setattr(
        evening, "_run_k9_lifecycle",
        lambda *_args, **_kwargs: (evening.STATUS_EMPTY, {"gaps": ["playbook missing"]}),
    )
    result = evening.run_evening_chain(
        DAY, report_date=DAY, segments=(evening.SEG_K9, evening.SEG_REPORT),
        correction_revision=2, save=True,
    )
    assert result.bundle is None
    assert result.status[evening.SEG_REPORT] == evening.STATUS_FAILED
    assert result.stats[evening.SEG_REPORT]["state"] == "not_saved"


def test_listing_age_uses_only_the_approved_number_of_frozen_days():
    days = [date(2026, 6, 1) + timedelta(days=i) for i in range(60)]
    history = pl.DataFrame({"trade_date": days})
    assert v3_run._listing_cutoff(history, 40) == days[-40]
    with pytest.raises(v3_run.PackUnavailable, match="上市历史证明不足"):
        v3_run._listing_cutoff(history, 61)


def test_fp4_rejects_a_renamed_fp3_pack(isolated_env):
    _seed_v4(isolated_env)
    fake = dataclasses.replace(fp3.build(DAY, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path), pack_version="fp-4")
    with pytest.raises(ValueError, match="不能用旧事实行伪造"):
        store.freeze_pack(fake, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)


def test_fp4_rejects_missing_historical_membership_dates(isolated_env):
    _seed_v4(isolated_env)
    import sqlite3
    with sqlite3.connect(isolated_env.db_path) as conn:
        conn.execute("DELETE FROM sw_industry_member_snapshots WHERE trade_date=?", (DAY.strftime("%Y%m%d"),))
    built = v4.build(DAY, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert isinstance(built, fp3.IncompletePack)
    assert any("没有可靠" in gap for gap in built.missing)


def test_readiness_reports_tampered_frozen_fp4_bytes(isolated_env):
    _seed_v4(isolated_env)
    frozen = store.freeze_pack(v4.build(DAY, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path),
                               parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    pl.read_parquet(frozen.path).with_columns((pl.col("close") + 0.01).alias("close")).write_parquet(frozen.path)
    ready = readiness.preflight(DAY, pack_version="fp-4", parquet_dir=isolated_env.parquet_dir,
                               db_path=isolated_env.db_path)
    assert not ready.ready and "冻结事实包不可用" in "；".join(ready.gaps)


def test_fp4_uses_only_dated_snapshot_not_current_sw_membership(isolated_env):
    _seed_v4(isolated_env)
    import sqlite3
    with sqlite3.connect(isolated_env.db_path) as conn:
        conn.execute("DELETE FROM sw_industry_member")
    built = v4.build(DAY, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert isinstance(built, fp3.CompletePack)
    assert not any(source.name == "sw_industry_member" for source in built.sources)
    assert set(built.rows["sw_l2_code"].drop_nulls().to_list()) == {"801080.SI"}
    store.freeze_pack(built, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert readiness.preflight(DAY, pack_version="fp-4", parquet_dir=isolated_env.parquet_dir,
                               db_path=isolated_env.db_path).ready
    with sqlite3.connect(isolated_env.db_path) as conn:
        conn.execute("DROP TABLE sw_industry_member")
    assert readiness.preflight(DAY, pack_version="fp-4", parquet_dir=isolated_env.parquet_dir,
                               db_path=isolated_env.db_path).ready


def _approved() -> dict:
    return {"schemaVersion":"k9-params-v3","packageVersion":"r1","strategyVersion":"K9-v3","factPackVersion":"fp-4","labelContractVersion":"d2-v2","status":"approved","parameterizedBy":"research","parameterizedAt":"2026-08-30","approvedBy":"owner","approvedAt":"2026-08-30","approvalNote":"approved","evidence":{},
      "boundary":{"newListingTradingDays":40,"activity":{"windowDays":60,"minimumValidDays":40,"amountWeight":0.6,"participationWeight":0.4,"excludeBottomPct":0.5},"d0Liquidity":{"minimumAmountCny":100000000,"freeFloatMarketValueRatio":0.005},"excludedL2Codes":[]},
      "channels":{"p2":{"enabled":True,"recall":{"windowDays":7,"volumeBaselineDays":20,"minCumulativeDropPct":0.1,"minDrawdownPct":0.12,"minIndustryUnderperformancePct":0.05,"minVolumeMultiple":1.0,"supportCloseLocationPct":0.65,"supportDailyReturnPct":0.0},"ranking":{"oversoldDepthWeight":0.15,"industryUnderperformanceWeight":0.2,"lowRecoveryWeight":0.35,"declineDecelerationWeight":0.2,"turnoverWeight":0.1}},"p3":{"enabled":False,"identity":{"windowDays":20,"eventWindowDays":5,"volumeBaselineDays":20,"minHotDays":6,"topPct":0.1,"largeMoveLimitWidthPct":0.5,"largeMoveAmplitudePct":0.7,"largeMoveVolumeMultiple":2.0,"hotness":{"amountWeight":0.6,"turnoverWeight":0.4}},"opportunity":{"requireDirectionResolved":True,"maxOverextendedPct":0.2,"minRelativeLeadership":0,"minCapitalRetention":0,"minStructureIntegrity":0},"ranking":{"directionWeight":0.2,"notOverextendedWeight":0.2,"relativeLeadershipWeight":0.2,"capitalRetentionWeight":0.2,"structureIntegrityWeight":0.2,"recentLimitDownRiskDeduction":0.1}},"p4":{"enabled":False,"benchmark":{"indexCode":"000001.SH"},"industry":{"windowDays":20,"minMembers":2,"minBreadthPct":0.5,"minOversoldRelativePct":0.1,"minRepairPct":0.01,"maxIndustries":3,"perIndustryStockCap":2},"stock":{"minRelativeStrength":0,"minCoreScore":0,"minLiquidityScore":0},"ranking":{"relativeStrengthWeight":0.4,"coreWeight":0.3,"liquidityWeight":0.3},"playbookBounds":{"minimumMemberCoverageMin":0,"minimumMemberCoverageMax":1,"medianReturnMin":-1,"medianReturnMax":1,"breadthMin":0,"breadthMax":1,"relativeBenchmarkReturnMin":-1,"relativeBenchmarkReturnMax":1,"relativeIndustryReturnMin":-1,"relativeIndustryReturnMax":1}}},"quotas":{"p2":5,"p3":0,"p4":0},"settlement":{"d1":{"enhancedReturnPct":0.015,"enhancedCloseLocationPct":0.6,"weakenedReturnPct":-0.015},"d2":{"opportunityReturnPct":0.03,"continuationReturnPct":0.01,"riskReturnPct":-0.04}}}


def test_v3_parameter_rejects_nan_unknown_wrong_unit_and_unclosed_weights():
    raw = _approved()
    assert v3_params.validate(raw) == ([], [])
    raw["channels"]["p2"]["ranking"]["turnoverWeight"] = float("nan")
    assert v3_params.validate(raw)[1]
    raw = _approved(); raw["boundary"]["activity"]["excludeBottomPct"] = 50
    assert any("excludeBottomPct" in x for x in v3_params.validate(raw)[1])
    raw = _approved(); raw["channels"]["p2"]["ranking"]["turnoverWeight"] = 0.2
    assert any("权重和" in x for x in v3_params.validate(raw)[1])
    raw = _approved(); raw["channels"]["p2"]["unknown"] = 1
    assert any("未知键" in x for x in v3_params.validate(raw)[1])


def test_d0_requires_frozen_playbook(tmp_path):
    db = tmp_path / "db.sqlite"; init_schema(db)
    params = v3_params.V3Params("r1", "sha", _approved())
    hit = v3_run.V3Hit("600001.SH", "a", "801080.SI", "半导体", "p2", 1, 1.0, {"close":10.0}, {"x":1})
    with pytest.raises(v3_run.PackageCreationError, match="冻结预案"):
        v3_run.create_package(batch_id="b", selection_date=DAY, signal_trade_date=DAY, d1_trade_date=DAY, d2_trade_date=DAY, params=params, pack_id="fp", hits=[hit], db_path=db)


def test_llm_typed_playbook_contract_freezes_nonempty_day1(tmp_path):
    db = tmp_path / "db.sqlite"; init_schema(db)
    params = v3_params.V3Params("r1", "sha", _approved())
    hit = v3_run.V3Hit("600001.SH", "a", "801080.SI", "半导体", "p2", 1, 1.0, {"close": 10.0, "limit_up_price": 11.0}, {"x": 1})
    from neckline.k9 import v3_playbook
    skeleton = v3_playbook.mechanical_skeleton([hit])
    playbooks = v3_playbook.validate_output({"candidates": [{"tsCode": "600001.SH", "invalidation": 9.0,
        "firstResistance": 10.5, "secondResistance": 10.8, "rationale": "冻结事实。",
        "openVerdict": {"rejectBelow": 9.2, "confirmRange": {"minimum": 9.5, "maximum": 10.2},
                        "overextendedAtOrAbove": 10.4, "unbuyableAtOrAbove": 11.0},
        "conditions": {"p2": {"holdAbove": 9.4}}}]}, skeleton, source="llm")
    v3_run.create_package(batch_id="full", selection_date=DAY, signal_trade_date=DAY, d1_trade_date=DAY, d2_trade_date=DAY, params=params, pack_id="fp", hits=[hit], playbooks=playbooks, db_path=db)
    item = packages.load_package("full", db_path=db)["candidates"][0]
    assert item["playbook"]["invalidation"] == 9.0
    assert item["playbook"]["firstResistance"] == 10.5


def test_playbook_prompt_distinguishes_frozen_input_from_required_output():
    from neckline.k9 import v3_playbook
    hit = v3_run.V3Hit(
        "600001.SH", "a", "801080.SI", "半导体", "p2", 1, 1.0,
        {"close": 10.0, "limit_up_price": 11.0}, {"x": 1})
    prompt = v3_playbook._prompt(v3_playbook.mechanical_skeleton([hit]))
    assert '"frozenCandidates"' in prompt
    assert '"requiredOutputShape"' not in prompt
    assert "不要回显 frozenCandidates" in prompt


def test_production_playbooks_generate_per_stock_but_return_one_atomic_mapping(monkeypatch):
    from neckline.k9 import v3_playbook
    hits = [
        v3_run.V3Hit(
            f"60000{i}.SH", str(i), "801080.SI", "半导体", "p2", i, 1.0,
            {"close": 10.0, "limit_up_price": 11.0}, {"x": 1})
        for i in range(1, 8)
    ]
    subset_sizes = []
    def fake_generate(subset, **_kwargs):
        subset_sizes.append(len(subset))
        code = next(iter(subset))
        return {code: {"tsCode": code}}, {"provider": "fake", "model": "fake", "output": {"candidates": [code]}}
    monkeypatch.setattr(v3_playbook, "_generate_subset", fake_generate)
    plans, provenance = v3_playbook.generate(hits)
    assert list(plans) == [hit.ts_code for hit in hits]
    assert subset_sizes == [1] * 7
    assert provenance["generationMode"] == "per_stock_atomic"
    assert provenance["stockCount"] == 7


def test_one_stock_playbook_retries_only_format_errors_with_precise_feedback():
    import json
    from neckline.k9 import v3_playbook
    from neckline.llm.base import LLMResult
    hit = v3_run.V3Hit(
        "600001.SH", "a", "801080.SI", "半导体", "p2", 1, 1.0,
        {"close": 10.0, "limit_up_price": 11.0}, {"x": 1})
    skeleton = v3_playbook.mechanical_skeleton([hit])
    valid = {"candidates": [{
        "tsCode": "600001.SH", "invalidation": 9.0,
        "firstResistance": 10.4, "secondResistance": 10.8,
        "openVerdict": {"rejectBelow": 9.2,
                        "confirmRange": {"minimum": 9.5, "maximum": 10.0},
                        "overextendedAtOrAbove": 10.5, "unbuyableAtOrAbove": 11.0},
        "conditions": {"p2": {"holdAbove": 9.4}}, "rationale": "完整预案。",
    }]}
    class FakeProvider:
        calls = 0
        feedback = ""
        def chat(self, messages, **_kwargs):
            self.calls += 1
            self.feedback = messages[-1].content or ""
            content = '{"candidates":[{"tsCode":"600001.SH"}]}' if self.calls == 1 else json.dumps(valid, ensure_ascii=False)
            return LLMResult(ok=True, content=content, provider="fake", model="fake")
    provider = FakeProvider()
    plans, meta = v3_playbook._generate_subset(skeleton, provider=provider)
    assert plans["600001.SH"]["invalidation"] == 9.0
    assert provider.calls == 2 and "缺少开盘判定" in provider.feedback
    assert meta["formatAttempts"] == 2


def test_successful_empty_selection_is_an_immutable_empty_package(tmp_path):
    db = tmp_path / "db.sqlite"; init_schema(db)
    params = v3_params.V3Params("r1", "sha", _approved())
    v3_run.create_package(batch_id="empty", selection_date=DAY, signal_trade_date=DAY, d1_trade_date=DAY, d2_trade_date=DAY, params=params, pack_id="fp", hits=[], db_path=db)
    item = packages.load_package("empty", db_path=db)
    assert item is not None and item["candidates"] == [] and item["state"] == "settled"
    assert item["coverage_state"] == "complete"


def test_explicit_d0_revision_advances_marker_without_overwriting_old_batch(tmp_path):
    db = tmp_path / "db.sqlite"; init_schema(db)
    params = v3_params.V3Params("r1", "sha", _approved())
    for batch_id, revision in (("old", 1), ("corrected", 2)):
        v3_run.create_package(
            batch_id=batch_id, selection_date=DAY, signal_trade_date=DAY,
            d1_trade_date=DAY, d2_trade_date=DAY, params=params,
            pack_id=f"fp-{revision}", hits=[], revision=revision, db_path=db)
    packages.record_selection_run(
        selection_date=DAY, signal_trade_date=DAY, state="empty",
        batch_id="old", reason="", db_path=db)
    packages.record_selection_run(
        selection_date=DAY, signal_trade_date=DAY, state="empty",
        batch_id="corrected", reason="", correction_revision=2, db_path=db)
    marker = packages.load_selection_run(DAY, db_path=db)
    assert marker["batch_id"] == "corrected"
    assert packages.load_package("old", db_path=db)["revision"] == 1
    assert packages.load_package("corrected", db_path=db)["revision"] == 2


def test_p2_p3_p4_each_rank_only_their_own_candidates(monkeypatch):
    """Golden miniature: no shared total-score, floor seat or P1 can leak back in."""
    rows = []
    for code, l2, amount in (("a", "i1", 100.0), ("b", "i1", 90.0), ("c", "i2", 80.0), ("d", "i2", 70.0)):
        rows.append({"trade_date": DAY, "ts_code": code, "name": code, "sw_l2_code": l2, "sw_l2_name": l2,
                     "close": 10.0, "pre_close": 10.0, "high": 10.0, "low": 10.0, "vol": amount, "amount": amount,
                     "turnover_rate": amount / 100, "ret_1d": 0.01, "rel_strength_1d": -0.01,
                     "net_amount_rate": 0.5, "is_limit_down": False, "limit_up_price": 11.0, "limit_down_price": 9.0, "sw_l2_median_ret": 0.0,
                     "sw_l2_member_count": 2})
    frame = pl.DataFrame(rows); pool = frame
    p2 = {"recall":{"windowDays":1,"volumeBaselineDays":1,"minCumulativeDropPct":0,"minDrawdownPct":0,"minIndustryUnderperformancePct":0,
                      "minVolumeMultiple":0,"supportCloseLocationPct":0,"supportDailyReturnPct":0},
          "ranking":{"oversoldDepthWeight":.2,"industryUnderperformanceWeight":.2,"lowRecoveryWeight":.2,"declineDecelerationWeight":.2,"turnoverWeight":.2}}
    p3 = {"identity":{"windowDays":1,"eventWindowDays":1,"volumeBaselineDays":1,"minHotDays":1,"topPct":1,"largeMoveLimitWidthPct":1,"largeMoveAmplitudePct":1,"largeMoveVolumeMultiple":0,"hotness":{"amountWeight":.6,"turnoverWeight":.4}},
          "opportunity":{"requireDirectionResolved":True,"maxOverextendedPct":1,"minRelativeLeadership":-1,"minCapitalRetention":0,"minStructureIntegrity":0},
          "ranking":{"directionWeight":.2,"notOverextendedWeight":.2,"relativeLeadershipWeight":.2,"capitalRetentionWeight":.2,"structureIntegrityWeight":.2,"recentLimitDownRiskDeduction":0}}
    p4 = {"benchmark":{"indexCode":"idx"},"industry":{"windowDays":1,"minMembers":2,"minBreadthPct":0,"minOversoldRelativePct":0,"minRepairPct":0,"maxIndustries":2,"perIndustryStockCap":1},
          "stock":{"minRelativeStrength":-1,"minCoreScore":0,"minLiquidityScore":0},"ranking":{"relativeStrengthWeight":.4,"coreWeight":.3,"liquidityWeight":.3},
          "playbookBounds":{"minimumMemberCoverageMin":0,"minimumMemberCoverageMax":1,"medianReturnMin":-1,"medianReturnMax":1,"breadthMin":0,"breadthMax":1,"relativeBenchmarkReturnMin":-1,"relativeBenchmarkReturnMax":1,"relativeIndustryReturnMin":-1,"relativeIndustryReturnMax":1}}
    # One isolated D0 has no prior valid volume baseline: both channels must
    # fail closed rather than use same-day/amount proxies.
    assert v3_run._p2(frame, pool, p2, 2, set()) == []
    assert v3_run._p3(frame, pool, p3, 2, set()) == []
    monkeypatch.setattr(v3_run, "_benchmark_series", lambda *_args, **_kwargs: pl.DataFrame({"trade_date":[DAY], "_benchmark_ret":[0.0]}))
    assert [h.channel for h in v3_run._p4(frame, pool, p4, 2, set(), parquet_dir=None, db_path=None)] == ["p4", "p4"]


def test_p2_and_p3_volume_evidence_only_use_each_days_prior_twenty_sessions():
    """No same-day amount proxy may satisfy either channel's volume condition."""
    start = date(2026, 7, 1)
    rows = []
    for offset in range(21):
        for code, amount, turnover in (("a", 100.0, 1.0), ("b", 200.0, 2.0)):
            rows.append({"trade_date": start + timedelta(days=offset), "ts_code": code, "name": code,
                         "sw_l2_code": "i", "sw_l2_name": "I", "close": 9.0 if offset < 20 else 10.0,
                         "pre_close": 9.8 if offset == 20 else 9.0,
                         "high": 10.0 if offset == 20 else 9.0,
                         "low": 10.0 if offset == 20 else 9.0,
                         "vol": 100.0 if offset < 20 or code == "b" else 200.0,
                         "amount": amount, "turnover_rate": turnover, "ret_1d": 0.02 if offset == 20 else -0.01,
                         "rel_strength_1d": -0.1, "sw_l2_median_ret": 0.0,
                         "net_amount_rate": 0.5, "is_limit_down": False,
                         "limit_up_price": 11.0, "limit_down_price": 8.0, "free_float_mv": 1.0})
    history = pl.DataFrame(rows)
    pool = history.filter(pl.col("trade_date") == history["trade_date"].max())
    p2 = {"recall": {"windowDays": 7, "volumeBaselineDays": 20, "minCumulativeDropPct": 0,
          "minDrawdownPct": 0, "minIndustryUnderperformancePct": 0, "minVolumeMultiple": 1.5,
          "supportCloseLocationPct": 0, "supportDailyReturnPct": 0},
          "ranking": {"oversoldDepthWeight": .2, "industryUnderperformanceWeight": .2,
          "lowRecoveryWeight": .2, "declineDecelerationWeight": .2, "turnoverWeight": .2}}
    p3 = {"identity": {"windowDays": 20, "eventWindowDays": 5, "volumeBaselineDays": 20,
          "minHotDays": 1, "topPct": 1, "largeMoveLimitWidthPct": .9, "largeMoveAmplitudePct": .9,
          "largeMoveVolumeMultiple": 1.5, "hotness": {"amountWeight": .6, "turnoverWeight": .4}},
          "opportunity": {"requireDirectionResolved": True, "maxOverextendedPct": 1,
          "minRelativeLeadership": -1, "minCapitalRetention": 0, "minStructureIntegrity": 0},
          "ranking": {"directionWeight": .2, "notOverextendedWeight": .2, "relativeLeadershipWeight": .2,
          "capitalRetentionWeight": .2, "structureIntegrityWeight": .2, "recentLimitDownRiskDeduction": 0}}
    assert [hit.ts_code for hit in v3_run._p2(history, pool, p2, 5, set())] == ["a"]
    assert [hit.ts_code for hit in v3_run._p3(history, pool, p3, 5, set())] == ["a"]


def test_p2_and_p4_compound_relative_returns_and_reject_incomplete_windows(monkeypatch):
    days = [date(2026, 8, 20), date(2026, 8, 21)]
    rows = []
    for day, ret, vol in zip(days, (-0.10, 0.10), (100.0, 200.0)):
        rows.append({"trade_date": day, "ts_code": "a", "name": "A", "sw_l2_code": "i", "sw_l2_name": "I",
                     "close": 10.0, "pre_close": 10.0, "high": 10.0, "low": 10.0, "vol": vol,
                     "amount": 100.0, "turnover_rate": 1.0, "ret_1d": ret, "sw_l2_median_ret": 0.0,
                     "rel_strength_1d": ret, "sw_l2_member_count": 2, "net_amount_rate": 0.5,
                     "is_limit_down": False, "limit_up_price": 11.0, "limit_down_price": 9.0,
                     "free_float_mv": 1.0})
    history = pl.DataFrame(rows); pool = history.filter(pl.col("trade_date") == days[-1])
    p2 = {"recall": {"windowDays": 2, "volumeBaselineDays": 1, "minCumulativeDropPct": 0,
          "minDrawdownPct": 0, "minIndustryUnderperformancePct": 0.005, "minVolumeMultiple": 1,
          "supportCloseLocationPct": 0, "supportDailyReturnPct": 0},
          "ranking": {"oversoldDepthWeight": .2, "industryUnderperformanceWeight": .2,
          "lowRecoveryWeight": .2, "declineDecelerationWeight": .2, "turnoverWeight": .2}}
    # (-10%) then (+10%) compounds to -1%, while an invalid daily sum is 0.
    assert [hit.ts_code for hit in v3_run._p2(history, pool, p2, 1, set())] == ["a"]
    p4 = {"benchmark": {"indexCode": "idx"},
          "industry": {"windowDays": 2, "minMembers": 2, "minBreadthPct": 0,
                       "minOversoldRelativePct": .005, "minRepairPct": .05, "maxIndustries": 1,
                       "perIndustryStockCap": 1},
          "stock": {"minRelativeStrength": -1, "minCoreScore": 0, "minLiquidityScore": 0},
          "ranking": {"relativeStrengthWeight": .4, "coreWeight": .3, "liquidityWeight": .3},
          "playbookBounds": {}}
    monkeypatch.setattr(v3_run, "_benchmark_series", lambda *_args, **_kwargs: pl.DataFrame({"trade_date": days, "_benchmark_ret": [0.0, 0.0]}))
    p4_history = history.with_columns(pl.col("ret_1d").alias("sw_l2_median_ret"))
    p4_pool = p4_history.filter(pl.col("trade_date") == days[-1])
    assert [hit.ts_code for hit in v3_run._p4(p4_history, p4_pool, p4, 1, set(), parquet_dir=None, db_path=None)] == ["a"]
    incomplete = history.with_columns(pl.when(pl.col("trade_date") == days[0]).then(None).otherwise(pl.col("sw_l2_median_ret")).alias("sw_l2_median_ret"))
    assert v3_run._p2(incomplete, pool, p2, 1, set()) == []
    incomplete_p4 = p4_history.with_columns(pl.when(pl.col("trade_date") == days[0]).then(None).otherwise(pl.col("sw_l2_median_ret")).alias("sw_l2_median_ret"))
    assert v3_run._p4(incomplete_p4, p4_pool, p4, 1, set(), parquet_dir=None, db_path=None) == []


def test_p3_large_event_is_relative_to_each_frozen_daily_limit_width_and_recent_window():
    start = date(2026, 7, 1)
    rows = []
    for offset in range(25):
        for code in ("normal", "twenty", "missing", "volume", "old"):
            close = high = 10.1
            volume = 100.0
            limit_up, limit_down = 11.0, 9.0
            if code == "normal" and offset == 24:
                close = high = 10.5  # 5% = 50% of its 10% daily limit width.
            elif code == "twenty" and offset == 24:
                close = high = 11.0; limit_up, limit_down = 12.0, 8.0  # 10% = 50% of 20cm.
            elif code == "missing":
                limit_up = limit_down = None
            elif code == "volume":
                limit_up = limit_down = None
                if offset == 24:
                    volume = 200.0  # only the independent prior-20-day volume event may qualify it.
            elif code == "old" and offset == 18:
                close = high = 10.5  # six sessions before D0: outside the five-day event window.
            rows.append({"trade_date": start + timedelta(days=offset), "ts_code": code, "name": code,
                         "sw_l2_code": "i", "sw_l2_name": "I", "close": close, "pre_close": 10.0,
                         "high": high, "low": 10.0, "vol": volume, "amount": 100.0,
                         "turnover_rate": 1.0, "ret_1d": 0.01, "rel_strength_1d": 0.0,
                         "net_amount_rate": 0.5, "is_limit_down": False,
                         "limit_up_price": limit_up, "limit_down_price": limit_down,
                         "free_float_mv": 1.0})
    history = pl.DataFrame(rows)
    pool = history.filter(pl.col("trade_date") == history["trade_date"].max())
    cfg = {"identity": {"windowDays": 20, "eventWindowDays": 5, "volumeBaselineDays": 20,
           "minHotDays": 1, "topPct": 1, "largeMoveLimitWidthPct": .5,
           "largeMoveAmplitudePct": .7, "largeMoveVolumeMultiple": 2,
           "hotness": {"amountWeight": .6, "turnoverWeight": .4}},
           "opportunity": {"requireDirectionResolved": True, "maxOverextendedPct": 1,
           "minRelativeLeadership": -1, "minCapitalRetention": 0, "minStructureIntegrity": 0},
           "ranking": {"directionWeight": .2, "notOverextendedWeight": .2,
           "relativeLeadershipWeight": .2, "capitalRetentionWeight": .2,
           "structureIntegrityWeight": .2, "recentLimitDownRiskDeduction": 0}}
    assert {hit.ts_code for hit in v3_run._p3(history, pool, cfg, 10, set())} == {"normal", "twenty", "volume"}


def test_locked_recent_codes_are_removed_before_channel_quota_is_cut():
    frame = pl.DataFrame({"ts_code":["old","next","third"],"name":["o","n","t"],"sw_l2_code":["i"]*3,
                          "sw_l2_name":["i"]*3,"trade_date":[DAY]*3,"close":[1.0]*3,"pre_close":[1.0]*3,
                          "limit_up_price":[1.1]*3,"limit_down_price":[0.9]*3,"free_float_mv":[1.0]*3,
                          "ret_1d":[0.0]*3,"sw_l2_median_ret":[0.0]*3,"rel_strength_1d":[0.0]*3,"value":[3.0,2.0,1.0]})
    hits = v3_run._rank(frame, "p2", pl.col("value"), 2, {"old"}, {"fixture": True})
    assert [x.ts_code for x in hits] == ["next", "third"]
