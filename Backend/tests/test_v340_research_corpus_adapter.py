"""Portable regression for the explicit historical raw-reply validator."""
from __future__ import annotations

from hashlib import sha256
import json

from scripts.validate_v340_research_replies import (
    _canonical_local_state,
    _frozen_pool,
    _stage_history,
    validate_historical_replies,
)
from neckline.k10.research_context import public_packet


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def _fixture():
    packet = {"allowedEvidenceRefs": [{"documentId": "source-1", "revision": 1}]}
    claim = {"claimId": "claim-1", "text": "来源称项目在推进", "kind": "factual_assertion",
             "novelty": "new_fact", "speaker": "来源", "subject": "项目", "object": "阶段",
             "action": "推进", "stageOrCondition": "待核实", "timeText": "今日",
             "verificationStatus": "unverified", "decisionImpact": "改变结论",
             "sourceRef": {"documentId": "source-1", "revision": 1}, "location": "line:1"}
    valid = {"action": "plan_gaps", "questions": [{
        "questionId": "q-1", "claimIds": ["claim-1"], "companyCodes": ["300001.SZ"], "question": "是否有必要补证",
        "knownEvidence": [{"documentId": "source-1", "revision": 1}], "missingEvidence": ["独立确认"], "supportCondition": "确认",
        "refuteCondition": "否认", "decisionImpact": "改变结论", "state": "open",
        "resumeCondition": "看到公告",
    }]}
    invalid = {"action": "plan_gaps", "questions": "wrong-shape"}
    rows, matches, attempts, receipts, checkpoints = [{
        "snapshot_id": "snapshot-1", "revision": 1,
        "result_json": json.dumps({"action": "extract_claims", "claims": [claim]}),
    }], [], [], [], []
    for number, raw in enumerate((valid, invalid), start=1):
        action, snapshot_id, revision, attempt_id = "plan_gaps", "snapshot-1", number + 1, f"attempt-{number}"
        runtime_hash = _digest({"action": action, "packet": packet})
        wrapper_hash, wire_hash = f"{number:x}" * 64, f"{number + 2:x}" * 64
        rows.append({"snapshot_id": snapshot_id, "revision": revision,
                     "result_json": json.dumps({"conclusion": {"runtimeRequest": {
                         "action": action, "packet": packet, "inputSha256": runtime_hash}}})})
        payload = json.dumps({"rawResponses": [{"choices": [{"message": {"content": json.dumps(raw)}}]}]})
        attempts.append({"attempt_id": attempt_id, "state": "succeeded", "error_code": None,
                         "item_key": f"investigation_{action}:{snapshot_id}:{action}:{wrapper_hash}",
                         "input_sha256": wire_hash})
        receipts.append({"attempt_id": attempt_id, "request_sha256": wire_hash, "payload_json": payload})
        checkpoints.append({"input_sha256": wrapper_hash, "stage": "model:investigation_plan_gaps",
                            "status": "completed", "safe_error_code": None,
                            "network_attempt_count": 1, "repair_attempt_count": 0})
        matches.append({"attemptId": attempt_id, "action": action, "snapshotId": snapshot_id,
                        "requestStageRevision": revision, "runtimeRequestInputSha256": runtime_hash,
                        "wrapperInputSha256": wrapper_hash, "wireInputSha256": wire_hash,
                        "receiptPayloadSha256": sha256(payload.encode()).hexdigest()})
    corpus = {"tables": {"k10_research_stage_results": rows,
                           "k10_external_attempts": attempts,
                           "k10_model_response_receipts": receipts,
                           "k10_execution_item_checkpoints": checkpoints,
                           "k10_v2_strategy_snapshots": [{"universe_snapshot_id": "universe-1"}],
                           "k10_v2_universe_members": [{"snapshot_id": "universe-1", "company_code": "300001.SZ"}]}}
    index = {"formatVersion": "neckline-research-corpus-matches-1",
             "provenance": {"latestPacketUsed": False, "wireBytesReconstructed": False,
                            "newProtocolProviderValidation": False},
             "matches": matches, "unmatched": []}
    return corpus, index


def test_portable_raw_reply_validator_decodes_original_packet_and_reports_rejection():
    corpus, index = _fixture()
    # A later snapshot patch is never allowed to repair the earlier public
    # request.  The checker uses only rows with lower revisions, and the local
    # reconstruction must disappear before the original request hash is read.
    corpus["tables"]["k10_research_stage_results"].append({
        "snapshot_id": "snapshot-1", "revision": 4,
        "result_json": json.dumps({"action": "extract_claims", "claims": [{
            "claimId": "future-claim", "text": "later state", "kind": "factual_assertion", "novelty": "new_fact",
            "speaker": "later", "subject": "later", "object": "later", "action": "later",
            "stageOrCondition": "later", "timeText": "later", "verificationStatus": "unverified",
            "decisionImpact": "later", "sourceRef": {"documentId": "source-1", "revision": 1}, "location": "line:2",
        }]}),
    })
    local, _ = _canonical_local_state(history=_stage_history(corpus), snapshot_id="snapshot-1",
                                      before_revision=3, frozen_pool=_frozen_pool(corpus))
    assert [claim["claimId"] for claim in local["claims"]] == ["claim-1"]
    original_packet = index["matches"][1]["runtimeRequestInputSha256"]
    assert _digest({"action": "plan_gaps", "packet": public_packet({"allowedEvidenceRefs": [{"documentId": "source-1", "revision": 1}], "_localState": local})}) == original_packet
    result = validate_historical_replies(corpus, index)
    assert result["provenance"] == {
        "kind": "exact_legacy_runtime_packet_raw_reply_decode_validate_canonical_state_reconstructed",
        "adapter": "DeepSeekDiscoveryModel._parse_json_result_then_decode_stage_result_then_validate_stage_result",
        "latestPacketUsed": False, "wireBytesReconstructed": False,
        "newProtocolProviderValidation": False,
        "localValidationOnly": True, "networkAccessBlocked": True, "networkCallsAttempted": 0,
        "canonicalStateReconstructed": True,
        "canonicalStateSource": "same_snapshot_stage_results_strictly_before_requestStageRevision_plus_frozen_universe_membership",
        "canonicalStatePublicPacketHashVerified": True,
    }
    assert result["counts"] == {
        "exactMatchedAttempts": 2, "providerSucceeded": 2, "accepted": 1,
        "decoderRejected": 1, "matchedProviderFailures": 0, "unmatchedProviderFailures": 0,
        "canonicalStateReconstructedAttempts": 2, "canonicalStatePriorStageRows": 3,
    }
    assert result["acceptedByAction"] == [{"action": "plan_gaps", "count": 1}]
    assert result["decoderRejectedByActionAndKind"] == [{
        "action": "plan_gaps", "kind": "investigation_result_invalid", "count": 1,
    }]
    assert result["decoderRejectedOriginalCheckpoint"] == [{
        "action": "plan_gaps", "kind": "investigation_result_invalid", "checkpointStatus": "completed",
        "checkpointSafeErrorCode": None, "networkAttempts": 1, "repairAttempts": 0, "count": 1,
    }]
