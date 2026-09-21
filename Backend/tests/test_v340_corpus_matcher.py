"""Historical reply association must not expand the original evidence packet."""
from copy import deepcopy
from hashlib import sha256
import json

from scripts.match_v340_research_corpus import match_corpus


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _corpus():
    snapshot = {"snapshotId": "research-1", "taskId": "task-1", "eventId": "event-1", "eventRevision": 1,
                "newsCutoffAt": "2026-09-15T21:00:00+08:00", "createdAt": "2026-09-15T21:01:00+08:00",
                "contextSha256": "c" * 64, "promptContractRevision": "original-contract",
                "modelParametersSha256": "m" * 64, "researchStatus": "continue_research"}
    original_packet = {"allowedEvidenceRefs": [{"documentId": "original-only", "revision": 1}]}
    future_packet = {"allowedEvidenceRefs": [{"documentId": "future-not-visible", "revision": 9}]}
    item = {"snapshot": snapshot, "action": "plan_gaps", "evidencePacket": original_packet}
    identity = {"semanticVersion": "k10-model-checkpoint-v1", "operation": "investigation_plan_gaps",
                "cutoffAt": "2026-09-15T21:00:00+08:00", "executionBinding": {
                    "configId": "frozen-config", "revision": 1, "contentSha256": "f" * 64},
                "modelOptions": {"temperature": 0}, "runtimeProvider": {
                    "endpoint": "https://provider.invalid/chat/completions", "model": "frozen-model", "name": "frozen"}}
    wrapper_hash = _hash({**identity, "input": item})
    wire_hash = "w" * 64  # Explicitly distinct from the wrapper/runtime packet hashes.
    payload = '{"content":"original paid reply","ok":true}'
    request_rows = [{"snapshot_id": "research-1", "revision": revision,
                     "result_json": json.dumps({"conclusion": {"runtimeRequest": {
                         "action": "plan_gaps", "packet": packet,
                         "inputSha256": _hash({"action": "plan_gaps", "packet": packet})}}})}
                    for revision, packet in ((2, original_packet), (20, future_packet))]
    latest = {**snapshot, "researchStatus": "comparison_complete", "revision": 30,
              "executionStatus": "ok", "updatedAt": "later", "verificationCutoffAt": "later"}
    corpus = {"taskId": "task-1", "tables": {
        "k10_tasks": [{"task_id": "task-1", "status": "failed", "input_cutoff_at": identity["cutoffAt"],
                       "checkpoint_json": json.dumps({"providerBinding": identity["runtimeProvider"]})}],
        "k10_task_execution_bindings": [{"task_id": "task-1", "execution_config_id": "frozen-config",
                                         "execution_config_revision": 1, "execution_content_sha256": "f" * 64}],
        "k10_execution_config_revisions": [{"config_id": "frozen-config", "revision": 1,
                                            "content_sha256": "f" * 64, "payload_json": json.dumps({
                                                "discovery": {"modelOptions": {"investigation": {"temperature": 0}}}})}],
        "k10_research_snapshot_revisions": [{"task_id": "task-1", "snapshot_id": "research-1",
                                             "snapshot_json": json.dumps(latest)}],
        "k10_external_attempts": [{"task_id": "task-1", "attempt_id": "attempt-1", "stage": "investigation",
                                   "state": "succeeded", "input_sha256": wire_hash,
                                   "item_key": f"investigation_plan_gaps:research-1:plan_gaps:{wrapper_hash}"}],
        "k10_model_response_receipts": [{"task_id": "task-1", "attempt_id": "attempt-1",
                                         "request_sha256": wire_hash, "payload_json": payload,
                                         "payload_sha256": sha256(payload.encode()).hexdigest()}],
        "k10_research_stage_results": request_rows, "k10_execution_item_checkpoints": [],
    }}
    return corpus, identity, item


def test_matches_original_packet_and_snapshot_state_not_latest_evidence():
    corpus, _, _ = _corpus()
    before = deepcopy(corpus)
    result = match_corpus(corpus, corpus_sha256="a" * 64)
    assert corpus == before
    assert result["summary"]["matchedAttempts"] == 1
    match = result["matches"][0]
    assert match["requestStageRevision"] == 2
    assert match["stableSnapshotResearchStatus"] == "continue_research"
    assert match["wrapperInputSha256"] != match["wireInputSha256"]
    assert result["provenance"]["latestPacketUsed"] is False
    assert result["provenance"]["wireBytesReconstructed"] is False


def test_changed_packet_cannot_become_historical_visible_evidence():
    corpus, _, _ = _corpus()
    row = corpus["tables"]["k10_research_stage_results"][0]
    result = json.loads(row["result_json"])
    result["conclusion"]["runtimeRequest"]["packet"]["allowedEvidenceRefs"].append(
        {"documentId": "secret-future", "revision": 1})
    row["result_json"] = json.dumps(result)
    result = match_corpus(corpus, corpus_sha256="a" * 64)
    assert result["summary"]["matchedAttempts"] == 0
    assert result["summary"]["invalidRuntimeRequests"] == 1


def test_receipt_requires_exact_wire_binding_and_unchanged_paid_payload():
    for field in ("request_sha256", "payload_sha256"):
        corpus, _, _ = _corpus()
        corpus["tables"]["k10_model_response_receipts"][0][field] = "bad"
        result = match_corpus(corpus, corpus_sha256="a" * 64)
        assert result["matches"] == []
        assert result["summary"]["unmatchedAttempts"] == 1


def test_changed_frozen_provider_cannot_match_same_packet():
    corpus, _, _ = _corpus()
    task = corpus["tables"]["k10_tasks"][0]
    checkpoint = json.loads(task["checkpoint_json"])
    checkpoint["providerBinding"]["model"] = "replacement-model"
    task["checkpoint_json"] = json.dumps(checkpoint)
    assert match_corpus(corpus, corpus_sha256="a" * 64)["matches"] == []


def test_retained_semantic_recovery_is_associated_without_authorizing_execution():
    corpus, identity, item = _corpus()
    prior = _hash({**identity, "input": item})
    correction = ("已保存上次付费回复，但该回复未满足本阶段契约。请根据当前明确列出的字段、类型、枚举和可见引用修正输出；"
                  "不要重复上次无效结构，不要新增事实、公司或超出当前问题的调查。")
    derived = {**item, "authorizedSemanticRecoveryOf": prior,
               "authorizedRecoveryFeedback": {"errorCode": "investigation_contract_invalid", "requiredCorrection": correction}}
    derived_hash = _hash({**identity, "input": derived})
    corpus["tables"]["k10_execution_item_checkpoints"].append({"task_id": "task-1",
        "stage": "model:investigation_plan_gaps", "input_sha256": prior,
        "safe_error_code": "investigation_contract_invalid"})
    corpus["tables"]["k10_external_attempts"][0]["item_key"] = (
        f"investigation_plan_gaps:research-1:plan_gaps:{derived_hash}")
    result = match_corpus(corpus, corpus_sha256="a" * 64)
    assert result["matches"][0]["recoveryMetadata"]["authorizedSemanticRecoveryOf"] == prior
    assert result["summary"]["matchedAttempts"] == 1
