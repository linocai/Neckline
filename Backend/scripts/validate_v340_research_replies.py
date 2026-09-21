"""Revalidate exact historical K10 research replies without a provider request.

The caller must provide the task-export corpus and matcher index explicitly.  This
script selects each response's immutable public ``runtimeRequest.packet`` by
``snapshotId`` plus ``requestStageRevision`` and verifies its digest.  It
rebuilds only the normalizer's hidden claim/question state from the same
snapshot's earlier persisted typed patches, plus frozen universe membership;
the public packet digest must remain unchanged.  It never substitutes current
state, opens a production database, reconstructs HTTP bytes, or treats old B75
replies as B76 compound-model validation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
from hashlib import sha256
import json
from pathlib import Path
import socket
import sys
from typing import Any, Mapping
from functools import wraps
from unittest.mock import patch

if __package__ in {None, ""}:
    # Permit the documented ``python scripts/...`` invocation without using a
    # source checkout as an installed package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neckline.k10.investigation import InvestigationError, decode_stage_result, validate_stage_result
from neckline.k10.pipeline import DeepSeekDiscoveryModel, PipelineError
from neckline.k10.research_context import public_packet
from neckline.llm.base import LLMResult


class CorpusValidationError(ValueError):
    """The private corpus/index cannot safely support local replay."""


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise CorpusValidationError("corpus_or_index_root_invalid")
    return value


def _runtime_requests(corpus: Mapping[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    tables = corpus.get("tables")
    if not isinstance(tables, Mapping) or not isinstance(tables.get("k10_research_stage_results"), list):
        raise CorpusValidationError("research_stage_results_missing")
    requests: dict[tuple[str, int], dict[str, Any]] = {}
    for row in tables["k10_research_stage_results"]:
        if not isinstance(row, Mapping):
            raise CorpusValidationError("research_stage_row_invalid")
        try:
            result = json.loads(row["result_json"])
            request = (result.get("conclusion") or {}).get("runtimeRequest")
            pointer = (str(row["snapshot_id"]), int(row["revision"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CorpusValidationError("research_stage_request_invalid") from exc
        if isinstance(request, Mapping):
            if pointer in requests:
                raise CorpusValidationError("duplicate_runtime_request_pointer")
            requests[pointer] = dict(request)
    return requests


def _stage_history(corpus: Mapping[str, Any]) -> dict[str, list[tuple[int, Mapping[str, Any]]]]:
    """Return only immutable, persisted patches ordered within each snapshot.

    A historical runtime packet is deliberately public and therefore omits the
    system-only state that the normalizer uses to retain immutable question and
    claim fields.  Its earlier durable stage results are the sole admissible
    source for that state.  This helper never reads a final snapshot, a current
    database, or a later revision.
    """
    tables = corpus.get("tables")
    rows = tables.get("k10_research_stage_results") if isinstance(tables, Mapping) else None
    if not isinstance(rows, list):
        raise CorpusValidationError("research_stage_results_missing")
    history: dict[str, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("snapshot_id"), str):
            raise CorpusValidationError("research_stage_row_invalid")
        try:
            revision = int(row["revision"])
            result = json.loads(row["result_json"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CorpusValidationError("research_stage_result_invalid") from exc
        if revision < 1 or not isinstance(result, Mapping):
            raise CorpusValidationError("research_stage_result_invalid")
        history[row["snapshot_id"]].append((revision, result))
    for rows_for_snapshot in history.values():
        rows_for_snapshot.sort(key=lambda row: row[0])
        if len({revision for revision, _ in rows_for_snapshot}) != len(rows_for_snapshot):
            raise CorpusValidationError("research_stage_revision_duplicate")
    return history


def _frozen_pool(corpus: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Recover membership only from the frozen strategy universe in the corpus."""
    tables = corpus.get("tables")
    strategy_rows = tables.get("k10_v2_strategy_snapshots") if isinstance(tables, Mapping) else None
    members = tables.get("k10_v2_universe_members") if isinstance(tables, Mapping) else None
    if not isinstance(strategy_rows, list) or len(strategy_rows) != 1 or not isinstance(members, list):
        raise CorpusValidationError("frozen_universe_missing")
    universe_id = strategy_rows[0].get("universe_snapshot_id") if isinstance(strategy_rows[0], Mapping) else None
    if not isinstance(universe_id, str) or not universe_id:
        raise CorpusValidationError("frozen_universe_identity_missing")
    codes: set[str] = set()
    for row in members:
        if not isinstance(row, Mapping) or row.get("snapshot_id") != universe_id:
            continue
        code = row.get("company_code")
        if not isinstance(code, str) or not code:
            raise CorpusValidationError("frozen_universe_member_invalid")
        codes.add(code)
    if not codes:
        raise CorpusValidationError("frozen_universe_empty")
    return tuple({"companyCode": code} for code in sorted(codes))


def _canonical_local_state(*, history: Mapping[str, list[tuple[int, Mapping[str, Any]]]],
                           snapshot_id: str, before_revision: int,
                           frozen_pool: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], int]:
    """Rebuild claims/questions known *before* an exact historical request.

    Stored stage results are typed patches.  They append or replace an item by
    its stable ID; later rows are intentionally excluded.  The return value is
    injected solely below ``_localState`` and is stripped before the recorded
    public-packet digest is checked.
    """
    claims: dict[str, dict[str, Any]] = {}
    questions: dict[str, dict[str, Any]] = {}
    consumed = 0
    for revision, result in history.get(snapshot_id, ()):
        if revision >= before_revision:
            break
        consumed += 1
        for field, key, target in (("claims", "claimId", claims), ("questions", "questionId", questions)):
            values = result.get(field, [])
            if values is None:
                continue
            if not isinstance(values, list):
                raise CorpusValidationError("research_stage_patch_collection_invalid")
            for value in values:
                if not isinstance(value, Mapping) or not isinstance(value.get(key), str) or not value[key]:
                    raise CorpusValidationError("research_stage_patch_identity_invalid")
                target[value[key]] = dict(value)
    return {
        "claims": [claims[key] for key in sorted(claims)],
        "questions": [questions[key] for key in sorted(questions)],
        "fixedPool": [dict(row) for row in frozen_pool],
    }, consumed


def _table_by_id(corpus: Mapping[str, Any], table: str, key: str) -> dict[str, Mapping[str, Any]]:
    tables = corpus.get("tables")
    rows = tables.get(table) if isinstance(tables, Mapping) else None
    if not isinstance(rows, list):
        raise CorpusValidationError(f"{table}_missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get(key), str) or not row[key]:
            raise CorpusValidationError(f"{table}_row_invalid")
        if row[key] in result:
            raise CorpusValidationError(f"{table}_duplicate_key")
        result[row[key]] = row
    return result


def _raw_content(receipt: Mapping[str, Any]) -> str:
    try:
        payload = json.loads(receipt["payload_json"])
        raw_responses = payload["rawResponses"]
        content = raw_responses[-1]["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CorpusValidationError("receipt_raw_response_missing") from exc
    if not isinstance(content, str):
        raise CorpusValidationError("receipt_content_not_text")
    return content


def _error_kind(exc: Exception) -> str:
    if isinstance(exc, (InvestigationError, PipelineError)):
        return exc.code
    return type(exc).__name__.lower()


def _offline_validation(fn):
    """Make an accidental provider call a corpus-validation failure.

    Importing the normal production adapter is deliberate: the historical reply
    must traverse its real sole-output parser.  Imports are not evidence of a
    network call, though, so the actual decode/validate period replaces both
    stdlib socket entry points and records attempts explicitly.
    """
    @wraps(fn)
    def wrapped(*args, **kwargs):
        attempts = 0

        def deny_network(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            raise CorpusValidationError("network_call_blocked")

        with patch("socket.socket", deny_network), patch("socket.create_connection", deny_network):
            result = fn(*args, **kwargs)
        if not isinstance(result, dict) or not isinstance(result.get("provenance"), Mapping):
            raise CorpusValidationError("historical_reply_validation_result_invalid")
        result["provenance"] = {**result["provenance"],
            "localValidationOnly": True,
            "networkAccessBlocked": True,
            "networkCallsAttempted": attempts,
        }
        return result
    return wrapped


@_offline_validation
def validate_historical_replies(corpus: Mapping[str, Any], index: Mapping[str, Any]) -> dict[str, Any]:
    """Validate every *exactly matched* legacy provider reply once.

    Decoder rejections are historical findings, not an instruction to retry or
    mutate the receipt.  Structural corpus/index faults raise instead of being
    counted as a model-reply outcome.
    """
    if index.get("formatVersion") != "neckline-research-corpus-matches-1":
        raise CorpusValidationError("match_index_format_invalid")
    provenance = index.get("provenance")
    if not isinstance(provenance, Mapping) or provenance.get("latestPacketUsed") is not False:
        raise CorpusValidationError("match_index_may_use_latest_packet")
    if provenance.get("wireBytesReconstructed") is not False or provenance.get("newProtocolProviderValidation") is not False:
        raise CorpusValidationError("match_index_provenance_invalid")
    matches = index.get("matches")
    if not isinstance(matches, list) or any(not isinstance(row, Mapping) for row in matches):
        raise CorpusValidationError("match_index_matches_invalid")

    requests = _runtime_requests(corpus)
    history = _stage_history(corpus)
    frozen_pool = _frozen_pool(corpus)
    attempts = _table_by_id(corpus, "k10_external_attempts", "attempt_id")
    receipts = _table_by_id(corpus, "k10_model_response_receipts", "attempt_id")
    tables = corpus["tables"]
    checkpoint_rows = tables.get("k10_execution_item_checkpoints")
    if not isinstance(checkpoint_rows, list) or any(not isinstance(row, Mapping) for row in checkpoint_rows):
        raise CorpusValidationError("execution_item_checkpoints_missing")
    checkpoints_by_input: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in checkpoint_rows:
        fingerprint = row.get("input_sha256")
        if isinstance(fingerprint, str):
            checkpoints_by_input[fingerprint].append(row)
    matched_ids: set[str] = set()
    accepted = Counter()
    rejected = Counter()
    rejected_checkpoint = Counter()
    provider_failures = Counter()
    canonical_state_reconstructed = 0
    canonical_state_stage_rows = 0

    for match in matches:
        try:
            attempt_id = match["attemptId"]
            action = match["action"]
            pointer = (str(match["snapshotId"]), int(match["requestStageRevision"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise CorpusValidationError("match_index_row_invalid") from exc
        if not isinstance(attempt_id, str) or attempt_id in matched_ids:
            raise CorpusValidationError("match_index_attempt_duplicate")
        matched_ids.add(attempt_id)
        request = requests.get(pointer)
        attempt = attempts.get(attempt_id)
        receipt = receipts.get(attempt_id)
        if request is None or attempt is None or receipt is None:
            raise CorpusValidationError("exact_match_reference_missing")
        if request.get("action") != action or not isinstance(request.get("packet"), Mapping):
            raise CorpusValidationError("exact_match_runtime_request_invalid")
        if _digest({"action": action, "packet": request["packet"]}) != match.get("runtimeRequestInputSha256"):
            raise CorpusValidationError("exact_match_runtime_request_digest_invalid")
        if attempt.get("item_key", "").rsplit(":", 1)[-1] != match.get("wrapperInputSha256"):
            raise CorpusValidationError("exact_match_wrapper_digest_invalid")
        if attempt.get("input_sha256") != match.get("wireInputSha256") or receipt.get("request_sha256") != match.get("wireInputSha256"):
            raise CorpusValidationError("exact_match_wire_digest_invalid")
        payload = receipt.get("payload_json")
        if not isinstance(payload, str) or sha256(payload.encode("utf-8")).hexdigest() != match.get("receiptPayloadSha256"):
            raise CorpusValidationError("exact_match_receipt_digest_invalid")
        checkpoints = [row for row in checkpoints_by_input[match["wrapperInputSha256"]]
                       if row.get("stage") == f"model:investigation_{action}"]
        if len(checkpoints) != 1:
            raise CorpusValidationError("exact_match_checkpoint_association_invalid")
        checkpoint = checkpoints[0]

        # ``runtimeRequest.packet`` is intentionally public. Rebuild only the
        # normalizer's system state from this snapshot's prior durable patches,
        # then prove the exact public packet remains unchanged.
        local_state, prior_stage_count = _canonical_local_state(
            history=history, snapshot_id=pointer[0], before_revision=pointer[1], frozen_pool=frozen_pool,
        )
        replay_packet = {**request["packet"], "_localState": local_state}
        if _digest({"action": action, "packet": public_packet(replay_packet)}) != match.get("runtimeRequestInputSha256"):
            raise CorpusValidationError("canonical_state_changed_public_packet")
        canonical_state_reconstructed += 1
        canonical_state_stage_rows += prior_stage_count

        state = attempt.get("state")
        if state != "succeeded":
            code = attempt.get("error_code") if isinstance(attempt.get("error_code"), str) else "unknown_provider_failure"
            provider_failures[(action, code)] += 1
            continue
        try:
            # This is the same sole-output adapter used by the legacy
            # DeepSeek model before the shared typed decoder/validator.  It
            # deliberately does not invent a repair response or retry.
            raw = DeepSeekDiscoveryModel._parse_json_result(LLMResult(ok=True, content=_raw_content(receipt)))
            normalized = decode_stage_result(raw, action=action, evidence_packet=replay_packet)
            validate_stage_result(action=action, result=normalized, evidence_packet=replay_packet)
        except (InvestigationError, PipelineError) as exc:
            kind = _error_kind(exc)
            rejected[(action, kind)] += 1
            rejected_checkpoint[(action, kind, checkpoint.get("status"), checkpoint.get("safe_error_code"),
                                 checkpoint.get("network_attempt_count"), checkpoint.get("repair_attempt_count"))] += 1
        else:
            accepted[action] += 1

    unmatched = index.get("unmatched")
    if not isinstance(unmatched, list) or any(not isinstance(row, Mapping) for row in unmatched):
        raise CorpusValidationError("match_index_unmatched_invalid")
    unmatched_failures = Counter()
    for row in unmatched:
        attempt_id = row.get("attemptId")
        attempt = attempts.get(attempt_id) if isinstance(attempt_id, str) else None
        if attempt is None or attempt.get("state") == "succeeded":
            raise CorpusValidationError("unmatched_attempt_invalid")
        code = attempt.get("error_code") if isinstance(attempt.get("error_code"), str) else "unknown_provider_failure"
        unmatched_failures[(row.get("action"), code)] += 1

    def rows(counter: Counter[tuple[str, str]] | Counter[str]) -> list[dict[str, Any]]:
        if counter and all(isinstance(key, tuple) for key in counter):
            return [{"action": action, "kind": kind, "count": count}
                    for (action, kind), count in sorted(counter.items())]
        return [{"action": action, "count": count} for action, count in sorted(counter.items())]

    checkpoint_rows_summary = [{
        "action": action, "kind": kind, "checkpointStatus": status,
        "checkpointSafeErrorCode": safe_error_code, "networkAttempts": network_attempts,
        "repairAttempts": repair_attempts, "count": count,
    } for (action, kind, status, safe_error_code, network_attempts, repair_attempts), count
      in sorted(rejected_checkpoint.items())]

    succeeded = sum(accepted.values()) + sum(rejected.values())
    return {
        "formatVersion": "neckline-v340-historical-reply-validation-1",
        "provenance": {
            "kind": "exact_legacy_runtime_packet_raw_reply_decode_validate_canonical_state_reconstructed",
            "adapter": "DeepSeekDiscoveryModel._parse_json_result_then_decode_stage_result_then_validate_stage_result",
            "latestPacketUsed": False,
            "wireBytesReconstructed": False,
            "newProtocolProviderValidation": False,
            "canonicalStateReconstructed": True,
            "canonicalStateSource": "same_snapshot_stage_results_strictly_before_requestStageRevision_plus_frozen_universe_membership",
            "canonicalStatePublicPacketHashVerified": True,
        },
        "counts": {
            "exactMatchedAttempts": len(matches),
            "providerSucceeded": succeeded,
            "accepted": sum(accepted.values()),
            "decoderRejected": sum(rejected.values()),
            "matchedProviderFailures": sum(provider_failures.values()),
            "unmatchedProviderFailures": sum(unmatched_failures.values()),
            "canonicalStateReconstructedAttempts": canonical_state_reconstructed,
            "canonicalStatePriorStageRows": canonical_state_stage_rows,
        },
        "acceptedByAction": rows(accepted),
        "decoderRejectedByActionAndKind": rows(rejected),
        "decoderRejectedOriginalCheckpoint": checkpoint_rows_summary,
        "matchedProviderFailuresByActionAndKind": rows(provider_failures),
        "unmatchedProviderFailuresByActionAndKind": rows(unmatched_failures),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path, help="private task corpus (.json or .json.gz)")
    parser.add_argument("--matches", required=True, type=Path, help="matcher JSON generated from the same corpus")
    args = parser.parse_args()
    try:
        result = validate_historical_replies(_load(args.corpus), _load(args.matches))
        # The summary is safe to retain as release evidence.  It identifies the
        # immutable private inputs without copying any raw prompt or reply.
        result["inputEvidence"] = {
            "corpusSha256": sha256(args.corpus.read_bytes()).hexdigest(),
            "matchIndexSha256": sha256(args.matches.read_bytes()).hexdigest(),
            "validatorScriptSha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "command": "python scripts/validate_v340_research_replies.py --corpus <private-corpus> --matches <private-match-index>",
        }
    except (OSError, ValueError, json.JSONDecodeError, CorpusValidationError) as exc:
        print(json.dumps({"error": "historical_reply_validation_failed", "kind":
                          exc.args[0] if exc.args else type(exc).__name__}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
