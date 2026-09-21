"""Match private historical research replies to their exact recorded input packets.

This is a local, read-only corpus operation. It neither opens a database nor
imports production clients. The model wire SHA and the orchestration SHA are
different identities; this tool proves their association through the durable
attempt item key, not by pretending those hashes should be equal.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


STATUSES = ("ready_for_comparison", "continue_research", "pending_verification",
            "abandon_recommendation", "background_only", "comparison_complete")
VOLATILE_SNAPSHOT_KEYS = {"revision", "executionStatus", "updatedAt", "verificationCutoffAt"}
RECOVERY_CORRECTION = ("已保存上次付费回复，但该回复未满足本阶段契约。请根据当前明确列出的字段、类型、枚举和可见引用修正输出；"
                       "不要重复上次无效结构，不要新增事实、公司或超出当前问题的调查。")


def digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def match_corpus(corpus: dict[str, Any], *, corpus_sha256: str) -> dict[str, Any]:
    tables = corpus["tables"]
    tasks = tables["k10_tasks"]
    if len(tasks) != 1 or tasks[0]["task_id"] != corpus["taskId"]:
        raise ValueError("Corpus must contain exactly its declared task")
    task = tasks[0]
    task_id = task["task_id"]
    if task["status"] not in {"failed", "completed", "cancelled", "not_configured"}:
        raise ValueError("Historical replay requires a terminal task corpus")
    binding = next(row for row in tables["k10_task_execution_bindings"] if row["task_id"] == task_id)
    config = next(row for row in tables["k10_execution_config_revisions"]
                  if row["config_id"] == binding["execution_config_id"]
                  and row["revision"] == binding["execution_config_revision"]
                  and row["content_sha256"] == binding["execution_content_sha256"])
    config_payload = json.loads(config["payload_json"])
    checkpoint = json.loads(task["checkpoint_json"])
    identity = {
        "semanticVersion": "k10-model-checkpoint-v1",
        "cutoffAt": datetime.fromisoformat(task["input_cutoff_at"]).isoformat(timespec="seconds"),
        "executionBinding": {"configId": binding["execution_config_id"],
                             "revision": binding["execution_config_revision"],
                             "contentSha256": binding["execution_content_sha256"]},
        "modelOptions": config_payload["discovery"]["modelOptions"]["investigation"],
        **({"runtimeProvider": checkpoint["providerBinding"]} if "providerBinding" in checkpoint else {}),
    }
    snapshots = {row["snapshot_id"]: json.loads(row["snapshot_json"])
                 for row in tables["k10_research_snapshot_revisions"] if row["task_id"] == task_id}
    attempts = {row["attempt_id"]: row for row in tables["k10_external_attempts"]
                if row["task_id"] == task_id and row["stage"] == "investigation"}
    receipts = {row["attempt_id"]: row for row in tables["k10_model_response_receipts"]
                if row["task_id"] == task_id}
    wanted: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts.values():
        wanted[attempt["item_key"].rsplit(":", 1)[-1]].append(attempt)
    checkpoints = {row["input_sha256"]: row for row in tables.get("k10_execution_item_checkpoints", [])
                   if row["task_id"] == task_id and row["stage"].startswith("model:investigation_")}
    matches: dict[str, dict[str, Any]] = {}
    invalid_requests = []
    runtime_requests = 0
    for row in tables["k10_research_stage_results"]:
        result = json.loads(row["result_json"])
        request = (result.get("conclusion") or {}).get("runtimeRequest")
        if not request:
            continue
        runtime_requests += 1
        pointer = {"snapshotId": row["snapshot_id"], "requestStageRevision": row["revision"]}
        action, packet = request["action"], request["packet"]
        if digest({"action": action, "packet": packet}) != request["inputSha256"]:
            invalid_requests.append({**pointer, "reason": "runtime_request_hash_mismatch"})
            continue
        if row["snapshot_id"] not in snapshots:
            invalid_requests.append({**pointer, "reason": "snapshot_identity_missing"})
            continue
        # Only immutable snapshot fields come from the final persisted revision.
        # The historical researchStatus is accepted only if its whole original
        # input hashes to the durable attempt identity; it is never guessed.
        stable = {key: value for key, value in snapshots[row["snapshot_id"]].items()
                  if key not in VOLATILE_SNAPSHOT_KEYS}
        operation = "investigation_" + action
        for status in STATUSES:
            snapshot = {**stable, "researchStatus": status}
            item = {"snapshot": snapshot, "action": action, "evidencePacket": packet}
            seen: set[str] = set()
            while True:
                wrapper_hash = digest({**identity, "operation": operation, "input": item})
                if wrapper_hash in seen:
                    break
                seen.add(wrapper_hash)
                expected_key = f"{operation}:{row['snapshot_id']}:{action}:{wrapper_hash}"
                for attempt in wanted.get(wrapper_hash, []):
                    if attempt["item_key"] != expected_key:
                        continue
                    receipt = receipts.get(attempt["attempt_id"])
                    if receipt is None or receipt["request_sha256"] != attempt["input_sha256"]:
                        continue
                    if sha256(receipt["payload_json"].encode()).hexdigest() != receipt["payload_sha256"]:
                        continue
                    candidate = {**pointer, "attemptId": attempt["attempt_id"], "action": action,
                                 "runtimeRequestInputSha256": request["inputSha256"],
                                 "wrapperInputSha256": wrapper_hash,
                                 "wireInputSha256": attempt["input_sha256"],
                                 "receiptPayloadSha256": receipt["payload_sha256"],
                                 "stableSnapshotResearchStatus": status,
                                 "recoveryMetadata": {key: value for key, value in item.items()
                                                      if key not in {"snapshot", "action", "evidencePacket"}}}
                    prior = matches.get(attempt["attempt_id"])
                    if prior is not None and any(prior[key] != candidate[key] for key in candidate
                                                 if key != "requestStageRevision"):
                        raise ValueError("Ambiguous historical input association")
                    if prior is None or row["revision"] < prior["requestStageRevision"]:
                        matches[attempt["attempt_id"]] = candidate
                old = checkpoints.get(wrapper_hash)
                code = old.get("safe_error_code") if old is not None else None
                if not isinstance(code, str) or code.startswith("provider_") or "network" in code or code == "content_policy_refused":
                    break
                # Historical recovery metadata is reproducible only from a
                # retained failure. Matching it is evidence, never permission
                # to recover or resend the real task.
                item = {**item, "authorizedSemanticRecoveryOf": wrapper_hash,
                        "authorizedRecoveryFeedback": {"errorCode": code,
                                                       "requiredCorrection": RECOVERY_CORRECTION}}
    unmatched = [{"attemptId": attempt_id, "state": row["state"],
                  "action": row["item_key"].split(":", 1)[0],
                  "reason": "no_exact_recorded_input_association"}
                 for attempt_id, row in attempts.items() if attempt_id not in matches]
    return {
        "formatVersion": "neckline-research-corpus-matches-1", "sourceTask": task_id,
        "corpusSha256": corpus_sha256,
        "provenance": {"kind": "exact_historical_wrapper_input_and_receipt_binding",
                       "referenceCode": "v3.3.0-b75", "latestPacketUsed": False,
                       "wireBytesReconstructed": False, "newProtocolProviderValidation": False,
                       "networkClientsImported": False,
                       "note": "Use requestStageRevision to select the original runtimeRequest packet; never substitute the latest packet."},
        "summary": {"runtimeRequests": runtime_requests, "researchAttempts": len(attempts),
                    "matchedAttempts": len(matches), "unmatchedAttempts": len(unmatched),
                    "invalidRuntimeRequests": len(invalid_requests),
                    "matchedByAction": dict(Counter(row["action"] for row in matches.values()))},
        "matches": sorted(matches.values(), key=lambda row: row["attemptId"]),
        "unmatched": unmatched, "invalidRequests": invalid_requests,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    args = parser.parse_args()
    data = args.corpus.read_bytes()
    payload = gzip.decompress(data) if args.corpus.suffix == ".gz" else data
    result = match_corpus(json.loads(payload), corpus_sha256=sha256(data).hexdigest())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
