from __future__ import annotations

import json
from pathlib import Path

import pytest

from neckline.scan.seeds import DriverSeed
from neckline.selection.deep_queue import DirectionPipelineConfig, DirectionPipelineConfigError, build_deep_queue
from neckline.selection.direction_brief import build_briefs
from neckline.selection.direction_inventory import build_inventory
from neckline.selection.direction_merge import merge_directions
from neckline.selection.direction_triage import build_triage_payload, disposition_map, parse_triage_response


BACKEND_ROOT = Path(__file__).resolve().parent.parent
BALANCED_CONFIG = BACKEND_ROOT / "config" / "direction-pipeline.v2.4.2-balanced.json"


def _config(**changes):
    value = {
        "version": "v1", "deep_initial_limit": 20, "triage_batch_size": 8, "triage_concurrency": 1,
        "deep_reason_batch_size": 1, "fill_batch_size": 3, "sufficient_candidate_count": 7,
        "normal_before_reserve": True, "coverage_industry_min": 2, "coverage_seed_kind_min": 2,
        "coverage_potential_czy_min": 1, "selection_token_budget": 10_000,
        "selection_wall_seconds": 600, "max_total_deep": 30, "max_fill_rounds": 3,
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


def test_all_seeds_visible_and_only_exact_duplicates_removed():
    seeds = _seeds(23)
    inventory = build_inventory(seeds + [seeds[0]])
    assert len(inventory.directions) == 23
    assert inventory.duplicate_count == 1
    assert [x.ordinal for x in inventory.directions] == list(range(23))
    merged = merge_directions(build_briefs(inventory.directions))
    assert len(merged.directions) == 23
    assert {x.merge_status for x in merged.directions} == {"merge_policy_unconfigured"}


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
        "version": "v2.4.2-balanced-r1",
        "deep_initial_limit": 20,
        "triage_batch_size": 8,
        "triage_concurrency": 1,
        "deep_reason_batch_size": 2,
        "fill_batch_size": 4,
        "sufficient_candidate_count": 7,
        "normal_before_reserve": True,
        "coverage_industry_min": 6,
        "coverage_seed_kind_min": 4,
        "coverage_potential_czy_min": 2,
        "selection_token_budget": 350_000,
        "selection_wall_seconds": 1_500,
        "max_total_deep": 32,
        "max_fill_rounds": 3,
        "cross_seed_merge_policy": "identity_only",
    }
    assert config.max_total_deep == config.deep_initial_limit + config.fill_batch_size * config.max_fill_rounds
    unit = (BACKEND_ROOT / "deploy" / "neckline-basket.service").read_text(encoding="utf-8")
    assert (
        "--direction-pipeline-config "
        "/opt/neckline/config/direction-pipeline.v2.4.2-balanced.json"
    ) in unit


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
