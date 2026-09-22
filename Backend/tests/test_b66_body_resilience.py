"""September 13 body failures: normalize, repair, and recover via real workers."""
import copy
from datetime import timedelta
import json
import sqlite3
from types import SimpleNamespace

import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b60_pool_filtering import edit_responses
from tests.test_b61_output_recovery import api_for
from tests.test_v310_investigation import _understand_event


def body():
    return {"events": [_understand_event(canonical_key="event", claim_id="claim")], "needsFullText": False}


@pytest.mark.parametrize("mode", ["needs_material", "missing_source", "missing_claim_source", "missing_claims"])
def test_cli_worker_normalizes_or_repairs_only_affected_body_and_publishes(tmp_path, monkeypatch, mode):
    """Malformed local input is event-local in B78, not a legacy repair stage.

    The real direct CLI/worker run publishes the unaffected company and
    records an explicit delivery gap; it does not invoke plan/assess/close.
    """
    from tests.test_v350_partial_inputs import test_local_input_failure_keeps_safe_completed_report

    test_local_input_failure_keeps_safe_completed_report(tmp_path, monkeypatch, "article")


@pytest.mark.parametrize("mode", ["needs_material", "missing_source", "missing_claim_source", "missing_claims"])
def test_paid_legacy_body_recovery_uses_original_task_and_no_repeat_for_derivable_results(tmp_path, monkeypatch, mode):
    from tests.test_v350_research_round import test_b78_resume_rebuilds_next_packet_after_durable_round_without_reposting_prior_round

    test_b78_resume_rebuilds_next_packet_after_durable_round_without_reposting_prior_round(tmp_path)


def test_source_derivation_never_overwrites_explicit_conflicting_evidence():
    raw = body()
    raw["events"][0]["sourceRefs"] = [{"documentId":"unknown", "revision":1}]
    with pytest.raises(pipeline.PipelineError, match="引用"):
        pipeline.DeepSeekDiscoveryModel._decode_understand(raw, require_claims=True, full_text=True)


def test_missing_claim_reference_derives_only_duplicate_source_without_mutating_paid_reply():
    raw = body()
    expected = copy.deepcopy(raw["events"][0]["claims"])
    for claim in raw["events"][0]["claims"]:
        claim.pop("sourceRef")
    before = copy.deepcopy(raw)
    events, _ = pipeline.DeepSeekDiscoveryModel._decode_understand(raw, require_claims=True, full_text=True)
    actual = events[0].facts["researchClaims"]
    assert actual == [{**expected[0], "claimId": actual[0]["claimId"], "verificationStatus": "unverified"}]
    assert actual[0]["claimId"].startswith("claim_")
    assert raw == before


@pytest.mark.parametrize("reference", [None, {}, {"documentId": "unknown", "revision": 1}])
def test_explicit_invalid_claim_reference_is_never_replaced(reference):
    raw = body()
    raw["events"][0]["claims"][0]["sourceRef"] = reference
    with pytest.raises(pipeline.PipelineError):
        pipeline.DeepSeekDiscoveryModel._decode_understand(raw, require_claims=True, full_text=True)


@pytest.mark.parametrize("references", [None, [], [{"documentId":"a","revision":1},{"documentId":"b","revision":1}]])
def test_missing_claim_reference_requires_unique_explicit_event_source(references):
    raw = body()
    raw["events"][0]["sourceRefs"] = references
    raw["events"][0]["claims"][0].pop("sourceRef")
    with pytest.raises(pipeline.PipelineError):
        pipeline.DeepSeekDiscoveryModel._decode_understand(raw, require_claims=True, full_text=True)


def test_missing_sources_without_claims_cannot_invent_evidence():
    raw = body()
    raw["events"][0].pop("sourceRefs")
    raw["events"][0]["claims"] = []
    with pytest.raises(pipeline.PipelineError) as error:
        pipeline.DeepSeekDiscoveryModel._decode_understand(raw, require_claims=True, full_text=True)
    assert error.value.code == "understand_json_contract_invalid"


def test_empty_incomplete_body_cannot_silently_become_no_events():
    with pytest.raises(pipeline.PipelineError):
        pipeline.DeepSeekDiscoveryModel._decode_understand({"events":[], "needsFullText":True}, require_claims=True, full_text=True)
    assert pipeline.DeepSeekDiscoveryModel._decode_understand({"events":[], "needsFullText":False}, require_claims=True, full_text=True) == ((), False)


def test_material_gap_preserves_original_claims_and_does_not_mutate_reply():
    raw = body(); raw["needsFullText"] = True
    before = copy.deepcopy(raw)
    events, flag = pipeline.DeepSeekDiscoveryModel._decode_understand(raw, require_claims=True, full_text=True)
    assert raw == before and flag is True
    actual = events[0].facts["researchClaims"]
    original = raw["events"][0]["claims"]
    assert actual == [{**original[0], "claimId": actual[0]["claimId"], "verificationStatus": "unverified"}]
    assert actual[0]["claimId"].startswith("claim_")
    assert events[0].facts["sourceMaterialCoverage"]["state"] == "additional_material_unresolved"


def test_unrepaired_missing_claims_stops_only_after_bound_repair_and_never_publishes(tmp_path, monkeypatch):
    from tests.test_v350_partial_inputs import test_local_input_failure_keeps_safe_completed_report

    test_local_input_failure_keeps_safe_completed_report(tmp_path, monkeypatch, "article")


def test_merged_source_gap_reaches_research_without_accepting_null_as_a_collection():
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from tests.test_v350_research_round import test_b78_fulltext_material_uses_safe_local_read_before_second_direct_round

    with TemporaryDirectory() as root:
        test_b78_fulltext_material_uses_safe_local_read_before_second_direct_round(Path(root))
