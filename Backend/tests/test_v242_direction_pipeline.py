from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from neckline.scan.seeds import DriverSeed
from neckline.selection.deep_queue import DirectionPipelineConfig, DirectionPipelineConfigError, build_deep_queue
from neckline.selection.deep_reason import parse_deep_reason
from neckline.selection.deep_research import request_for
from neckline.selection.direction_brief import build_briefs
from neckline.selection.direction_inventory import build_inventory
from neckline.selection.direction_merge import merge_directions
from neckline.selection.direction_triage import build_triage_payload, disposition_map, parse_triage_response


BACKEND_ROOT = Path(__file__).resolve().parent.parent
BALANCED_CONFIG = BACKEND_ROOT / "config" / "direction-pipeline.v2.4.2-balanced.json"


def _config(**changes):
    value = {
        "version": "v1", "mechanical_shortlist_limit": 30,
        "deep_initial_limit": 20, "triage_batch_size": 8, "triage_concurrency": 1,
        "deep_reason_batch_size": 1, "fill_batch_size": 3, "sufficient_candidate_count": 7,
        "normal_before_reserve": True, "coverage_industry_min": 2, "coverage_seed_kind_min": 2,
        "coverage_potential_czy_min": 1, "selection_token_budget": 10_000,
        "max_total_deep": 30, "max_fill_rounds": 3,
        "cross_seed_merge_policy": "identity_only",
    }
    value.update(changes)
    return value


def _seeds(n=24):
    return [DriverSeed(
        seed_key=f"seed-{i}", seed_kind="hot_industry" if i % 2 else "limit_cluster",
        label=f"direction-{i}", member_codes=(f"000{i:03d}.SZ",),
        evidence={"industry": "A" if i % 3 else "B", "potential_czy": ["C"] if i % 4 == 0 else []},
    ) for i in range(n)]


def _check():
    return {
        "verdict": "ok", "support": ["公开事实"], "counter_evidence": [],
        "missing": [], "reason": "已有公开证据",
    }


def _deep_candidate(brief):
    code = brief.member_codes[0]
    return {
        "directionId": brief.direction_id, "decision": "candidate",
        "decisionReason": "驱动与成员均有公开证据", "name": brief.label,
        "driver": "公开事件驱动", "driver_kind": "event", "why_now": "当日出现新证据",
        "common_trait": "都直接受同一事件驱动", "persistence": "事件仍在验证窗口",
        "strengthen_and_invalidate": "增量公告强化，事件撤回证伪", "evidence_conflicts": "",
        "engine_code": "Z", "market_check": _check(), "sector_check": _check(),
        # Deliberately hostile model-authored identity: the parser must discard
        # it and bind the mechanical seed key below.
        "seed_keys": ["模型擅自写的错误种子"],
        "members": [{
            "ts_code": code, "role": "core", "reason": "方向容量代表",
            "primary_claim": "yes", "primary_claim_reason": "驱动、代表性与协同一致",
            "position_check": _check(), "core_check": _check(),
        }],
        "narrative": "完整研究材料",
        "card_material": {
            "upside_path": "驱动验证后沿均线逐步抬升",
            "entries": [{"ts_code": code, "low": 9.8, "high": 10.2,
                         "max_chase": 10.5, "exit_low": 11.2, "exit_high": 12.0,
                         "why": "回踩当日实体中枢观察"}],
            "verification": "多数成员守住支撑并继续放量",
            "invalidation": "多数成员跌破机械失效条件",
            "risks": ["事件兑现速度不及预期"], "tier_note": None,
        },
    }


def test_all_seeds_visible_and_only_exact_duplicates_removed():
    seeds = _seeds(23)
    inventory = build_inventory(seeds + [seeds[0]])
    assert len(inventory.directions) == 23
    assert inventory.duplicate_count == 1
    assert [x.ordinal for x in inventory.directions] == list(range(23))
    merged = merge_directions(build_briefs(inventory.directions))
    assert len(merged.directions) == 23
    assert {x.merge_status for x in merged.directions} == {"merge_policy_unconfigured"}


def test_hot_industry_label_is_preserved_as_mechanical_coverage_category():
    seed = DriverSeed(
        seed_key="industry-1", seed_kind="hot_industry", label="医药商业",
        member_codes=("600001.SH",), evidence={"industry_rank": 1},
    )
    brief = build_briefs(build_inventory([seed]).directions)[0]
    assert brief.industry == "医药商业"


def test_tavily_query_never_emits_the_rejected_one_character_shape():
    request = request_for(
        direction_id="copper", label="铜", brief={"industry": None},
    )
    assert request.query == "铜 A股 最新产业动态"


def test_v242_pipeline_resolves_bare_concept_code_before_tavily_query():
    from neckline.selection import aggregate as ag

    seed = DriverSeed(
        seed_key="cluster-1", seed_kind="limit_cluster", label="885756.TI",
        member_codes=("003031.SZ",), evidence={"anchor_concept": "885756.TI"},
    )
    ctx = ag.MechContext(
        trade_date=date(2026, 8, 14), index_names={"885756.TI": "芯片概念"},
    )
    resolved = ag._pipeline_seeds_with_readable_labels((seed,), ctx)[0]
    assert resolved.label == "芯片概念"
    assert resolved.seed_key == seed.seed_key
    assert resolved.member_codes == seed.member_codes
    brief = build_briefs(build_inventory((resolved,)).directions)[0]
    assert request_for(
        direction_id=brief.direction_id, label=brief.label, brief=brief.public_dict(),
    ).query == "芯片概念 A股 最新产业动态"


def test_config_has_no_hidden_defaults_and_confirmed_initial_limit():
    raw = _config()
    raw.pop("fill_batch_size")
    with pytest.raises(DirectionPipelineConfigError, match="fill_batch_size"):
        DirectionPipelineConfig.from_mapping(raw)
    with pytest.raises(DirectionPipelineConfigError, match="deep_initial_limit"):
        DirectionPipelineConfig.from_mapping(_config(deep_initial_limit=19))


def test_approved_balanced_production_config_is_exact_and_wired_to_basket_unit():
    raw = json.loads(BALANCED_CONFIG.read_text(encoding="utf-8"))
    config = DirectionPipelineConfig.from_mapping(raw)
    assert raw == {
        "version": "v2.4.2-balanced-r3",
        "mechanical_shortlist_limit": 48,
        "deep_initial_limit": 20,
        "triage_batch_size": 8,
        "triage_concurrency": 1,
        "deep_reason_batch_size": 2,
        "fill_batch_size": 5,
        "sufficient_candidate_count": 7,
        "normal_before_reserve": True,
        "coverage_industry_min": 6,
        "coverage_seed_kind_min": 4,
        "coverage_potential_czy_min": 2,
        "selection_token_budget": 350_000,
        "max_total_deep": 30,
        "max_fill_rounds": 2,
        "cross_seed_merge_policy": "identity_only",
    }
    assert config.max_total_deep == config.deep_initial_limit + config.fill_batch_size * config.max_fill_rounds
    unit = (BACKEND_ROOT / "deploy" / "neckline-basket.service").read_text(encoding="utf-8")
    assert (
        "--direction-pipeline-config "
        "/opt/neckline/config/direction-pipeline.v2.4.2-balanced.json"
    ) in unit


def test_retired_wall_field_cannot_restore_a_time_cutoff():
    raw = _config(selection_wall_seconds=1)
    config = DirectionPipelineConfig.from_mapping(raw)
    assert not hasattr(config, "selection_wall_seconds")


def test_triage_has_no_search_and_missing_is_clamped_to_reserve():
    briefs = list(build_briefs(build_inventory(_seeds(2)).directions))
    missing = briefs[0]
    object.__setattr__(missing, "evidence", {"data_missing": True})
    payload = build_triage_payload(briefs)
    assert payload["enable_search"] is False
    batch = parse_triage_response([
        {"directionId": briefs[0].direction_id, "disposition": "deep", "reason": "x"},
    ], briefs)
    assert disposition_map(batch)[briefs[0].direction_id] == "reserve"
    assert disposition_map(batch)[briefs[1].direction_id] == "reserve"


def test_malformed_triage_is_retryable_reserve_and_queue_is_covered_then_limited():
    briefs = build_briefs(build_inventory(_seeds(24)).directions)
    batch = parse_triage_response("not-json", briefs)
    assert batch.malformed is True
    assert {x.disposition for x in batch.decisions} == {"reserve"}
    dispositions = {brief.direction_id: "deep" for brief in briefs}
    queue = build_deep_queue(briefs, dispositions, DirectionPipelineConfig.from_mapping(_config()))
    assert len(queue.entries) == 20
    assert len(queue.remaining_ids) == 4
    assert any(entry.coverage_reason.startswith("coverage:industry") for entry in queue.entries)


def test_deep_contract_binds_mechanical_seed_and_survives_the_real_whitelist_gate():
    from neckline.selection import aggregate as ag

    seed = _seeds(1)[0]
    brief = build_briefs(build_inventory([seed]).directions)[0]
    parsed = parse_deep_reason(
        _deep_candidate(brief), direction_id=brief.direction_id,
        seed_key=brief.seed_key, allowed_member_codes=brief.member_codes,
    )
    proposal = parsed.to_legacy_proposal()
    assert proposal["seed_keys"] == [seed.seed_key]
    assert proposal["directionId"] == brief.direction_id
    candidate, rejected = ag._gate_proposal(
        proposal, trade_date_s="20260814", seeds_by_key={seed.seed_key: seed},
        presented_by_seed={seed.seed_key: seed.member_codes},
        evidence_by_seed={seed.seed_key: ag.DriverEvidence(
            seed_key=seed.seed_key, status=ag.EVIDENCE_OK,
            items=(ag.EvidenceItem("公开事件", "交易所", "2026-08-14"),),
        )},
        ctx=ag.MechContext(trade_date=date(2026, 8, 14)),
        pack_version="test-pack", charter_version="test-charter", used_keys=set(),
    )
    assert rejected is None
    assert candidate is not None and candidate.seed_keys == (seed.seed_key,)


def test_tavily_hit_without_publication_date_is_evidence_not_zero_hits():
    from neckline.selection import aggregate as ag

    items = ag._pipeline_evidence_items({"evidence": [{
        "claim": "交易所披露了扩产进展", "source": "example.test", "date": "",
        "url": "https://example.test/notice",
    }]})
    assert len(items) == 1
    assert items[0].date == ag.EVIDENCE_DATE_UNDISCLOSED

    seed = _seeds(1)[0]
    brief = build_briefs(build_inventory([seed]).directions)[0]
    proposal = parse_deep_reason(
        _deep_candidate(brief), direction_id=brief.direction_id,
        seed_key=brief.seed_key, allowed_member_codes=brief.member_codes,
    ).to_legacy_proposal()
    candidate, rejected = ag._gate_proposal(
        proposal, trade_date_s="20260814", seeds_by_key={seed.seed_key: seed},
        presented_by_seed={seed.seed_key: seed.member_codes},
        evidence_by_seed={seed.seed_key: ag.DriverEvidence(
            seed_key=seed.seed_key, status=ag.EVIDENCE_OK, items=items,
        )},
        ctx=ag.MechContext(trade_date=date(2026, 8, 14)),
        pack_version="test-pack", charter_version="test-charter", used_keys=set(),
    )
    assert rejected is None
    assert candidate is not None


def test_deep_contract_keeps_valid_no_candidate_separate_from_system_failure():
    seed = _seeds(1)[0]
    brief = build_briefs(build_inventory([seed]).directions)[0]
    parsed = parse_deep_reason(
        {"directionId": brief.direction_id, "decision": "not_candidate",
         "decisionReason": "公开证据不足以支持共同驱动"},
        direction_id=brief.direction_id, seed_key=brief.seed_key,
        allowed_member_codes=brief.member_codes,
    )
    assert parsed.decision == "not_candidate" and not parsed.is_candidate
    with pytest.raises(ValueError, match="only candidate"):
        parsed.to_legacy_proposal()


@pytest.mark.parametrize("mutate,match", [
    (lambda raw: raw.update({"members": []}), "1 to 3"),
    (lambda raw: raw["members"][0].update({"ts_code": "999999.SZ"}), "whitelist"),
    (lambda raw: raw["members"][0].pop("core_check"), "core_check"),
    (lambda raw: raw.pop("card_material"), "card_material"),
    (lambda raw: raw.update({"card_material": {}}), "card_material"),
])
def test_deep_contract_rejects_shapes_that_previously_became_zero_basket_gate_rejects(mutate, match):
    seed = _seeds(1)[0]
    brief = build_briefs(build_inventory([seed]).directions)[0]
    raw = _deep_candidate(brief)
    mutate(raw)
    with pytest.raises(ValueError, match=match):
        parse_deep_reason(
            raw, direction_id=brief.direction_id, seed_key=brief.seed_key,
            allowed_member_codes=brief.member_codes,
        )
