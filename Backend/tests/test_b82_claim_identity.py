"""B82 body facts receive deterministic program-owned identities."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import sqlite3

import httpx
import pytest

from neckline.k10 import metering, store
from neckline.k10.discovery import DiscoveryDocument, freeze_event_drafts
from neckline.k10.metering import MeteredProvider
from neckline.k10.pipeline import (DeepSeekDiscoveryModel, PipelineError, _CheckpointedDiscoveryModel,
                                   _document_checkpoint_key, _research_id)
from neckline.k10.research_contracts import ResearchContractError, ResearchSnapshot
from neckline.k10.research_runtime import _Investigation, _hash, normalize_body_claims
from neckline.k10.schema import initialize_schema
from neckline.llm.base import LLMResult
from tests.k10_v306_fixture import append_approved_execution_profile, execution_payload


REF_A = {"documentId": "body-a", "revision": 3}
REF_B = {"documentId": "body-b", "revision": 1}


def _append_execution_profile(*, db_path, created_at: str, config_id: str,
                              investigation_contract: str) -> tuple[str, int]:
    """Bind the actual versioned request contract used by a recovery test."""
    policy_id = f"{config_id}-policy"
    policy, payload = execution_payload(policy_id=policy_id)
    payload["discovery"]["investigationPromptContractRevision"] = investigation_contract
    store.append_title_triage_policy(
        policy_id=policy_id, content=policy, approval_state="approved",
        created_at=created_at, approved_at=created_at, db_path=db_path,
    )
    revision = store.append_execution_config(
        config_id=config_id, payload=payload, created_at=created_at, db_path=db_path,
    )
    return config_id, revision


def _claim(*, text: str = "公司披露项目已完成送样", source_ref: dict[str, object] | None = REF_A,
           claim_id: str = "model-made-id", verification_status: str = "verified",
           location: str = "paragraph:2") -> dict[str, object]:
    value: dict[str, object] = {
        "claimId": claim_id,
        "text": text,
        "kind": "factual_assertion",
        "novelty": "new_fact",
        "speaker": "公司",
        "subject": "项目",
        "object": "样品",
        "action": "送样",
        "stageOrCondition": None,
        "timeText": "本次公告",
        "verificationStatus": verification_status,
        "decisionImpact": "需要核实送样是否对应实际订单",
        "location": location,
    }
    if source_ref is not None:
        value["sourceRef"] = source_ref
    return value


def test_b82_body_claim_identity_is_program_owned_stable_deduplicated_and_unverified():
    positive = _claim()
    duplicate = _claim(claim_id="different-model-id", verification_status="contradicted")
    denial = _claim(text="公司否认项目已经完成送样", claim_id="same-model-id", verification_status="verified")
    raw = [positive, duplicate, denial]
    before = copy.deepcopy(raw)

    claims = normalize_body_claims(raw_claims=raw, allowed_source_refs=[REF_A], fallback_source_ref=REF_A)

    assert raw == before
    assert len(claims) == 2
    assert all(claim.claim_id.startswith("claim_") for claim in claims)
    assert all(claim.verification_status == "unverified" for claim in claims)
    assert claims[0].claim_id != claims[1].claim_id
    # A new provider-owned ID or status cannot perturb the durable fact key.
    again = normalize_body_claims(raw_claims=[duplicate, denial], allowed_source_refs=[REF_A], fallback_source_ref=REF_A)
    assert [claim.claim_id for claim in again] == [claim.claim_id for claim in claims]


def test_b82_body_claim_identity_keeps_explicit_multi_source_facts_separate_and_never_mislinks():
    # The B-body fact explicitly names B while A is the only optional fallback.
    # Its source must stay B; choosing the first event source would silently
    # make it look like a fact in the wrong article revision.
    from_b = _claim(source_ref=REF_B)
    multi = normalize_body_claims(raw_claims=[from_b], allowed_source_refs=[REF_A, REF_B])
    b_only = normalize_body_claims(raw_claims=[from_b], allowed_source_refs=[REF_B], fallback_source_ref=REF_B)
    a_only = normalize_body_claims(raw_claims=[_claim(source_ref=REF_A)], allowed_source_refs=[REF_A], fallback_source_ref=REF_A)

    assert multi[0].source_ref == REF_B
    assert multi[0].claim_id == b_only[0].claim_id
    assert multi[0].claim_id != a_only[0].claim_id

    with pytest.raises(ResearchContractError, match="不能省略 sourceRef"):
        normalize_body_claims(raw_claims=[_claim(source_ref=None)], allowed_source_refs=[REF_A, REF_B])
    with pytest.raises(ResearchContractError, match="不属于当前正文"):
        normalize_body_claims(
            raw_claims=[_claim(source_ref={"documentId": "untrusted", "revision": 1})],
            allowed_source_refs=[REF_A, REF_B],
        )


@pytest.mark.parametrize("recovery_state", ["failed", "running"])
def test_b82_exact_b81_understand_receipt_preserves_existing_snapshot_claim_identity(
        tmp_path, monkeypatch, recovery_state):
    """A B81 body receipt remains a local recovery, not a B82 remap/rebill.

    This enters through the production checkpointed model adapter: the first
    metered B81-shaped response is frozen, its semantic checkpoint is marked
    authorized for recovery, and the new adapter must replay the exact stored
    receipt.  The persisted snapshot's context hash is then checked by the
    real investigation constructor, which is where an accidental B82 claim-ID
    normalization previously failed.
    """
    db_path = tmp_path / "b81-understand-replay.sqlite"
    initialize_schema(db_path)
    now = "2026-09-20T04:10:00+00:00"
    store.set_run_control(state="open", reason_code="offline_test", changed_at=now,
                          changed_by="test_b82", db_path=db_path)
    store.enqueue_task(task_id="understand-replay", kind="evening_scan", idempotency_key="understand-replay",
                       input_version="fixture", input_cutoff_at=now, payload={}, budget={},
                       created_at=now, db_path=db_path)
    config_id, revision = append_approved_execution_profile(
        db_path=db_path, created_at=now, config_id="b81-understand")
    store.bind_task_execution(task_id="understand-replay", execution_config_id=config_id,
                              execution_config_revision=revision, binding_kind="scheduled",
                              bound_at=now, db_path=db_path)
    profile = store.task_execution_profile(task_id="understand-replay", db_path=db_path)
    discovery = profile["payload"]["discovery"]
    monkeypatch.setattr(store, "admit_article", lambda **_kwargs: {"state": "admitted"})

    calls: list[str] = []
    raw = {"needsFullText": False, "events": [{
        "canonicalKey": "event-old-claim", "stageKey": "reported", "eventState": "reported",
        "headline": "旧回执命题", "eventKind": "rumor", "facts": {"fixture": True},
        "sourceRefs": [{"documentId": "source-old", "revision": 1}],
        "claims": [{
            "claimId": "provider-b81-claim", "text": "公司称项目正在送样", "kind": "rumor",
            "novelty": "new_fact", "speaker": "公司", "subject": "项目", "object": "样品",
            "action": "送样", "stageOrCondition": "待确认", "timeText": "今日",
            "verificationStatus": "unverified", "decisionImpact": "影响阶段核验",
            "sourceRef": {"documentId": "source-old", "revision": 1}, "location": "paragraph:1",
        }],
    }]}

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": json.dumps(raw)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 13, "completion_tokens": 8, "total_tokens": 21},
        })

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(**{
        **kwargs, "transport": httpx.MockTransport(respond),
    }))
    monkeypatch.setitem(metering._MODEL_CAPABILITIES, ("https://example.invalid/chat", "deepseek-flash"), {
        "contextTokens": 1_000_000, "maxOutputTokens": 384_000, "counter": "offline-b82-claim",
    })
    provider = MeteredProvider(ledger_db=db_path, ledger_task="understand-replay", api_key="fixture",
                               model="deepseek-flash", name="fixture", api_url="https://example.invalid/chat",
                               read_timeout=1, use_streaming=False)
    document = DiscoveryDocument("source-old", 1, now, now, "公司称项目正在送样。", None, {"title": "旧正文"})

    def adapter(*, allow_failed_resume: bool) -> _CheckpointedDiscoveryModel:
        base = DeepSeekDiscoveryModel(provider)
        base.set_execution_policy(discovery)
        return _CheckpointedDiscoveryModel(
            base=base, task_id="understand-replay", execution_profile=profile,
            cutoff_at=datetime(2026, 9, 20, 4, 10, tzinfo=timezone.utc), db_path=db_path,
            leaseguard=None, allow_failed_research_resume=allow_failed_resume,
        )

    original = adapter(allow_failed_resume=True).understand(document=document)
    assert calls == ["/chat"]
    assert original[0].facts["researchClaims"][0]["claimId"] == "provider-b81-claim"
    # The legacy snapshot verifier deliberately accepts no naked raw receipt.
    # Model recovery above is the subject under test; this is the independent
    # frozen discovery prerequisite produced by a normal title-gated run.
    # It proves the same task, document, frozen selection and decoded source
    # event that originally admitted the snapshot.
    selected_refs = [{"documentId": "source-old", "revision": 1}]
    title_policy = discovery["titleTriagePolicy"]
    store.freeze_title_triage_manifest(
        task_id="understand-replay", input_manifest_sha256=store._hash(selected_refs),
        window_kind="evening", policy_id=title_policy["policyId"],
        policy_revision=title_policy["revision"],
        policy_content_sha256=title_policy["contentSha256"], input_count=1,
        input_refs=selected_refs, batch_count=1, title_status="frozen",
        created_at=now, db_path=db_path,
    )
    store.record_title_triage_item(
        task_id="understand-replay", document_id="source-old", revision=1,
        batch_index=0, disposition="candidate", matter_key="old-claim",
        merged_ref=None, selection_rank=1, audit_reason="frozen source proof",
        created_at=now, db_path=db_path,
    )
    store.freeze_title_selection_manifest(
        task_id="understand-replay", selection_manifest_sha256=store._hash(selected_refs),
        selected_refs=selected_refs, created_at=now, db_path=db_path,
    )
    document_key, document_digest = _document_checkpoint_key(document)
    store.record_execution_checkpoint(
        task_id="understand-replay", item_kind="document", item_key=document_key,
        stage="understand", input_sha256=document_digest, status="completed",
        attempt_count=1, network_attempt_count=0, repair_attempt_count=0,
        elapsed_ms=0, input_tokens=None, output_tokens=None,
        result={"events": freeze_event_drafts(original), "extraction": {},
                "filterState": "completed", "fullTextUsed": False},
        safe_error_code=None, safe_error_ref=None, updated_at=now, db_path=db_path,
    )
    with sqlite3.connect(db_path) as conn:
        ledger, digest = conn.execute(
            "SELECT item_key,input_sha256 FROM k10_execution_item_checkpoints "
            "WHERE task_id='understand-replay' AND stage='model:understand'"
        ).fetchone()
        if recovery_state == "failed":
            conn.execute(
                "UPDATE k10_execution_item_checkpoints SET status='failed',safe_error_code='pipeline_invalid',safe_error_ref=? "
                "WHERE task_id='understand-replay' AND item_key=? AND stage='model:understand'", (ledger, ledger),
            )
            checkpoint = json.loads(conn.execute(
                "SELECT checkpoint_json FROM k10_tasks WHERE task_id='understand-replay'"
            ).fetchone()[0])
            checkpoint["recoveryAuthorized"] = {"failedModelInputSha256": [digest]}
            conn.execute("UPDATE k10_tasks SET checkpoint_json=? WHERE task_id='understand-replay'",
                         (json.dumps(checkpoint, sort_keys=True, separators=(",", ":")),))
        else:
            conn.execute(
                "UPDATE k10_execution_item_checkpoints SET status='running',result_json=NULL,safe_error_code=NULL,safe_error_ref=NULL "
                "WHERE task_id='understand-replay' AND item_key=? AND stage='model:understand'", (ledger,),
            )

    event = original[0]
    event_id = store.append_event_revision(event_id="event_old_claim", stable_key=event.canonical_key,
        headline=event.headline, event_kind=event.event_kind, facts=dict(event.facts),
        source_refs=[{"documentId": "source-old", "revision": 1}], supersedes_revision=None,
        created_at=now, db_path=db_path)
    cutoff = datetime(2026, 9, 20, 4, 10, tzinfo=timezone.utc)
    cutoff_text = cutoff.isoformat(timespec="microseconds")
    context = {"canonicalKey": event.canonical_key, "stageKey": event.stage_key, "eventState": event.event_state,
               "headline": event.headline, "eventKind": event.event_kind,
               "sourceRefs": [{"documentId": "source-old", "revision": 1}], "facts": dict(event.facts),
               "newsCutoffAt": cutoff_text, "cutoffInclusive": False}
    snapshot = ResearchSnapshot(_research_id(task_id="understand-replay", event=event), "understand-replay",
        event_id.event_id, event_id.revision, cutoff_text, now, _hash(context),
        "k10-investigation-v1", "b" * 64, "continue_research", "ok", 1, now, now)
    from neckline.k10.research_store import create_research_snapshot
    create_research_snapshot(snapshot=snapshot, db_path=db_path)

    # A local exact receipt replay must not reach this transport again.
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: pytest.fail("B81 receipt replay opened a new provider request"))
    recovered = adapter(allow_failed_resume=recovery_state == "failed").understand(document=document)
    assert calls == ["/chat"]
    assert recovered[0].facts["researchClaims"][0]["claimId"] == "provider-b81-claim"
    # The production research constructor still enforces the context hash;
    # success proves the old snapshot, claim link and frozen receipt agree.
    from neckline.k10.delivery import RESEARCH_CONTRACT
    runtime = _Investigation(model=object(), verifier=object(), task_id="understand-replay", event=recovered[0],
        documents={document.evidence_ref: document}, execution_profile=profile, cutoff_at=cutoff, db_path=db_path,
        created_at=cutoff, leaseguard=None, cutoff_inclusive=False, snapshot_created=None,
        clock=lambda: cutoff, allow_failed_resume=True, runtime_contract={"research": RESEARCH_CONTRACT})
    assert runtime.snapshot.context_sha256 == snapshot.context_sha256


@pytest.mark.parametrize("checkpoint_state", ["running", "missing"])
@pytest.mark.parametrize("paid_repair", [False, True, "both_invalid", "tampered_feedback"])
def test_b82_running_understand_receipt_restarts_with_program_claim_identity_and_no_post(
        tmp_path, monkeypatch, checkpoint_state, paid_repair):
    """A B82 raw receipt is decoded as B82 after a crash before checkpoint commit.

    This uses the metered adapter with a real persisted running checkpoint or
    a lost checkpoint after receipt persistence. A receipt recovery must not
    open another socket, and it must not opt into the B81 provider-claim
    identity branch merely because it is receipt-only.
    """
    db_path = tmp_path / "b82-understand-running.sqlite"
    initialize_schema(db_path)
    now = "2026-09-22T04:10:00+00:00"
    task_id = "b82-understand-running"
    store.set_run_control(state="open", reason_code="offline_test", changed_at=now,
                          changed_by="test_b82", db_path=db_path)
    store.enqueue_task(task_id=task_id, kind="evening_scan", idempotency_key=task_id,
                       input_version="fixture", input_cutoff_at=now, payload={}, budget={},
                       created_at=now, db_path=db_path)
    config_id, revision = _append_execution_profile(
        db_path=db_path, created_at=now, config_id="b82-understand-v2",
        investigation_contract="k10-investigation-v2",
    )
    store.bind_task_execution(task_id=task_id, execution_config_id=config_id,
                              execution_config_revision=revision, binding_kind="scheduled",
                              bound_at=now, db_path=db_path)
    profile = store.task_execution_profile(task_id=task_id, db_path=db_path)
    discovery = profile["payload"]["discovery"]
    monkeypatch.setattr(store, "admit_article", lambda **_kwargs: {"state": "admitted"})

    calls: list[str] = []
    source_ref = {"documentId": "source-b82", "revision": 1}
    raw = {"needsFullText": False, "events": [{
        "canonicalKey": "event-b82-claim", "stageKey": "reported", "eventState": "reported",
        "headline": "B82 回执", "eventKind": "disclosure", "facts": {"fixture": True},
        "sourceRefs": [source_ref],
        "claims": [{
            "claimId": "provider-b82-claim", "text": "公司披露样品仍在客户验证中", "kind": "factual_assertion",
            "novelty": "new_fact", "speaker": "公司", "subject": "项目", "object": "样品",
            "action": "验证", "stageOrCondition": "客户测试", "timeText": "今日",
            "verificationStatus": "unverified", "decisionImpact": "影响订单兑现判断",
            "sourceRef": source_ref, "location": "paragraph:1",
        }],
    }]}

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        reply = copy.deepcopy(raw)
        if paid_repair and len(calls) == 1:
            del reply["events"][0]["claims"][0]["novelty"]
        if paid_repair == "both_invalid" and len(calls) == 2:
            del reply["events"][0]["claims"]
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": json.dumps(reply)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 13, "completion_tokens": 8, "total_tokens": 21},
        })

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(**{
        **kwargs, "transport": httpx.MockTransport(respond),
    }))
    monkeypatch.setitem(metering._MODEL_CAPABILITIES, ("https://example.invalid/chat", "deepseek-flash"), {
        "contextTokens": 1_000_000, "maxOutputTokens": 384_000, "counter": "offline-b82-claim-v2",
    })
    provider = MeteredProvider(ledger_db=db_path, ledger_task=task_id, api_key="fixture",
                               model="deepseek-flash", name="fixture", api_url="https://example.invalid/chat",
                               read_timeout=1, use_streaming=False)
    document = DiscoveryDocument("source-b82", 1, now, now, "公司披露样品仍在客户验证中。", None, {"title": "B82 正文"})

    def adapter() -> _CheckpointedDiscoveryModel:
        base = DeepSeekDiscoveryModel(provider)
        base.set_execution_policy(discovery)
        return _CheckpointedDiscoveryModel(
            base=base, task_id=task_id, execution_profile=profile,
            cutoff_at=datetime(2026, 9, 22, 4, 10, tzinfo=timezone.utc), db_path=db_path,
            leaseguard=None, allow_failed_research_resume=False,
        )

    # Structural reads must reconstruct the complete original instruction too,
    # including the locator request contract appended by the material renderer.
    from hashlib import sha256
    from neckline.k10.research_material import source_material_for_understand
    check = adapter()
    structural = source_material_for_understand(document, max_characters=0)
    operation, payload = check._base._material_request(document, structural)
    prompt_hash = sha256(json.dumps({"operation": operation, "payload": payload,
        "modelOptions": check._base._model_options("understand")}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    digest = check._digest(operation="understand", stage="understand", item={
        "sourceContentSha256": structural["sourceContentSha256"], "requestSha256": prompt_hash,
        "textMode": structural["textMode"], "material": structural})
    proof = check._body_receipt_replay_proof(document=document, material=structural, original_digest=digest)
    assert proof is not None and proof["request"]["operation"] == operation
    assert proof["request"]["payload"] == payload

    # Exercise the receipt boundary, before the optional fact cache is written.
    monkeypatch.setattr(store, "store_fact_cache", lambda **_kwargs: None)
    if paid_repair == "both_invalid":
        with pytest.raises(PipelineError):
            adapter().understand(document=document)
        original_claim_id = None
    else:
        original = adapter().understand(document=document)
        original_claim_id = original[0].facts["researchClaims"][0]["claimId"]
    assert calls == ["/chat"] * (2 if paid_repair else 1)
    if original_claim_id is not None:
        assert original_claim_id.startswith("claim_")
    assert original_claim_id != "provider-b82-claim"
    with sqlite3.connect(db_path) as conn:
        (ledger,) = conn.execute(
            "SELECT item_key FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND stage='model:understand'", (task_id,),
        ).fetchone()
        if checkpoint_state == "running":
            conn.execute(
                "UPDATE k10_execution_item_checkpoints "
                "SET status='running',result_json=NULL,safe_error_code=NULL,safe_error_ref=NULL "
                "WHERE task_id=? AND item_key=? AND stage='model:understand'", (task_id, ledger),
            )
        else:
            conn.execute(
                "DELETE FROM k10_execution_item_checkpoints "
                "WHERE task_id=? AND item_key=? AND stage='model:understand'", (task_id, ledger),
            )

    if paid_repair == "tampered_feedback":
        with sqlite3.connect(db_path) as conn:
            for attempt, encoded in conn.execute("SELECT attempt_id,payload_json FROM k10_model_response_receipts"):
                receipt = json.loads(encoded)
                feedback = receipt["replayMetadata"]["repairFeedback"]
                if feedback is not None:
                    feedback["requiredCorrection"] = "This was not the paid wire."
                    changed = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    conn.execute("UPDATE k10_model_response_receipts SET payload_json=?,payload_sha256=? WHERE attempt_id=?",
                                 (changed, sha256(changed.encode()).hexdigest(), attempt))

    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: pytest.fail("B82 receipt replay opened a new provider request"))
    if paid_repair in {"both_invalid", "tampered_feedback"}:
        with pytest.raises(PipelineError) as error:
            adapter().understand(document=document)
        assert error.value.code == ("provider_response_receipt_unverifiable"
                                    if paid_repair == "tampered_feedback" else "understand_json_contract_invalid")
    else:
        recovered = adapter().understand(document=document)
        recovered_claim_id = recovered[0].facts["researchClaims"][0]["claimId"]
        assert recovered_claim_id == original_claim_id
        assert recovered_claim_id.startswith("claim_")
    assert calls == ["/chat"] * (2 if paid_repair else 1)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM k10_external_attempts WHERE task_id=? AND stage IN ('fullText','lightweight')",
            (task_id,),
        ).fetchone() == (2 if paid_repair else 1,)
