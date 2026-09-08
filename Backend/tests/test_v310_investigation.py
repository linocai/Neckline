from __future__ import annotations

import json
import sqlite3

import httpx
from datetime import datetime, timezone
from pathlib import Path
import pytest

from neckline.k10 import store
from neckline.k10.investigation import (
    InvestigationError, advance_research, decode_stage_result, query_path_signature,
)
from neckline.k10.discovery import (CandidateComparison, CompanyMappingDraft, DiscoveryDocument, EvidenceRef, EventComparison,
                                    EventDraft, InvestigationOutcome, Verification, _merge_same_event_sources, run_discovery)
from neckline.k10.pipeline import DeepSeekDiscoveryModel, PipelineError, _CheckpointedDiscoveryModel
from neckline.k10.metering import MeteredProvider, bind_provider_execution_spending, provider_spend_context
from neckline.llm.base import ChatMessage
from neckline.k10.investigation_prompts import request_spec
from neckline.k10.research_contracts import Claim, EvidenceDisclosure, QueryPath, ResearchSnapshot, ResearchStageResult
from neckline.k10.schema import initialize_schema
from neckline.k10.universe import CompanyMetadata
from neckline.llm.base import LLMResult
from tests.k10_v306_fixture import append_approved_execution_profile


def _snapshot(*, execution_status: str = "ok") -> ResearchSnapshot:
    return ResearchSnapshot(
        "snapshot-1", "task-1", "event-1", 1, "2026-09-08T13:00:00+00:00",
        "2026-09-08T13:05:00+00:00", "a" * 64, "k10-investigation-v1", "b" * 64,
        "continue_research", execution_status, 1, "2026-09-08T13:05:00+00:00", "2026-09-08T13:05:00+00:00",
    )


def _claim() -> Claim:
    return Claim("claim-1", "供应商称项目进入送样", "rumor", "new_fact", "供应商", "项目", "产品", "送样", "需确认", "今日",
                 "unverified", "会影响阶段判断", {"documentId": "source-1", "revision": 1}, "paragraph:2")


class _Model:
    def __init__(self, result: ResearchStageResult) -> None:
        self.result = result
        self.calls = 0

    def advance_research(self, **_kwargs) -> ResearchStageResult:
        self.calls += 1
        return self.result


def test_extract_claims_is_the_only_stage_given_frozen_body_and_keeps_rumor_unverified():
    model = _Model(ResearchStageResult("extract_claims", claims=(_claim(),)))
    step = advance_research(model=model, snapshot=_snapshot(), action="extract_claims", evidence_packet={
        "allowedEvidenceRefs": [{"documentId": "source-1", "revision": 1}],
        "originalText": "untrusted frozen body",
    })
    assert step.result.claims[0].kind == "rumor"
    assert step.result.claims[0].verification_status == "unverified"
    with pytest.raises(InvestigationError, match="不得重复传入冻结正文"):
        advance_research(model=_Model(ResearchStageResult("plan_gaps")), snapshot=_snapshot(), action="plan_gaps",
                         evidence_packet={"allowedEvidenceRefs": [], "originalText": "must not recur"})


def test_query_paths_need_open_question_and_a_new_evidence_path():
    path = QueryPath("path-1", "question-1", "项目 送样 公告", "确认项目是否送样", "项目主体",
                     "检查项目主体而不是转载", "确认阶段信息", "确认阶段会改变比较", "planned")
    packet = {"allowedEvidenceRefs": [], "openQuestionIds": ["question-1"],
              "attemptedPathSignatures": [query_path_signature(path)]}
    with pytest.raises(InvestigationError, match="没有新增证据路径"):
        advance_research(model=_Model(ResearchStageResult("plan_queries", query_paths=(path,))), snapshot=_snapshot(),
                         action="plan_queries", evidence_packet=packet)


def test_fulltext_requires_existing_admission_and_is_not_an_implicit_retry():
    model = _Model(ResearchStageResult("assess_evidence"))
    with pytest.raises(InvestigationError, match="全文未获准入"):
        advance_research(model=model, snapshot=_snapshot(), action="assess_evidence", evidence_packet={
            "allowedEvidenceRefs": [{"documentId": "search-1", "revision": 1}],
            "admittedFulltextRefs": [],
            "fullTextDocuments": [{"documentId": "search-1", "revision": 1, "text": "body"}],
        })
    assert model.calls == 0


def test_unverified_rumor_assessment_is_valid_when_disclosure_is_complete():
    disclosure = EvidenceDisclosure("unverified", True, "unknown", None, ("原始发布者未知",), "若项目主体确认则重新比较")
    assessment = {
        "companyCode": "300001.SZ", "role": "primary", "rank": 1, "summary": "条件化题材推断",
        "priorityReason": "业务关系待核", "gap": "原始来源未知", "rankChangeConditions": "主体否认则撤回",
        "twoDayReason": "新传闻可能引发关注", "evidenceDisclosure": disclosure.to_dict(),
    }
    step = advance_research(model=_Model(ResearchStageResult("compare_companies", company_assessments=(assessment,))),
                            snapshot=_snapshot(), action="compare_companies", evidence_packet={
                                "allowedEvidenceRefs": [], "companyCodes": ["300001.SZ"],
                            })
    assert step.result.company_assessments[0]["evidenceDisclosure"]["verificationStatus"] == "unverified"


def test_failed_execution_cannot_be_represented_as_completed_research():
    with pytest.raises(InvestigationError, match="非正常执行状态"):
        advance_research(model=_Model(ResearchStageResult("close_research", conclusion={"researchStatus": "ready_for_comparison"})),
                         snapshot=_snapshot(execution_status="failed"), action="close_research",
                         evidence_packet={"allowedEvidenceRefs": []})


def test_safe_stage_error_stops_research_before_any_completion_can_be_recorded():
    with pytest.raises(InvestigationError, match="执行失败"):
        advance_research(model=_Model(ResearchStageResult("plan_gaps", safe_error_code="investigation_provider_failed")),
                         snapshot=_snapshot(), action="plan_gaps", evidence_packet={"allowedEvidenceRefs": []})


def test_decoder_rejects_model_output_that_cannot_support_rumor_disclosure():
    with pytest.raises(InvestigationError, match="typed contract"):
        decode_stage_result({"action": "extract_claims", "claims": [{
            "claimId": "claim-1", "text": "传闻", "kind": "not-a-kind", "novelty": "new_fact", "speaker": None, "subject": None,
            "object": None, "action": None, "stageOrCondition": None, "timeText": None,
            "verificationStatus": "unverified", "decisionImpact": "影响判断",
            "sourceRef": {"documentId": "source-1", "revision": 1}, "location": "p:1",
        }]}, action="extract_claims")


def test_checkpointed_investigation_reuses_same_typed_input_without_second_model_call(tmp_path):
    path = tmp_path / "investigation.sqlite"
    initialize_schema(path)
    now = "2026-09-08T13:05:00+00:00"
    store.enqueue_task(task_id="task-1", kind="evening_scan", idempotency_key="research", input_version="fixture",
                       input_cutoff_at=now, payload={}, budget={}, created_at=now, db_path=path)
    result = ResearchStageResult("close_research", conclusion={"researchStatus": "pending_verification"})
    base = _Model(result)
    binding = {"configId": "fixture", "revision": 1, "contentSha256": "c" * 64, "payload": {"discovery": {
        "networkMaxAttempts": 1, "jsonRepairMaxAttempts": 0,
        "modelOptions": {"investigation": {"maxTokens": 128, "thinking": {"type": "disabled"}}},
    }}}
    # The operation ledger only requires an immutable task binding.  This test
    # isolates checkpoint reuse from execution-profile validation, which has its
    # own config regression suite.
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO k10_execution_config_revisions VALUES(?,?,?,?,?)",
                     ("fixture", 1, json.dumps({"fixture": True}), "c" * 64, now))
        conn.execute("INSERT INTO k10_task_execution_bindings VALUES(?,?,?,?,?,?)",
                     ("task-1", "fixture", 1, "c" * 64, "scheduled", now))
    wrapped = _CheckpointedDiscoveryModel(base=base, task_id="task-1", execution_profile=binding,
                                          cutoff_at=__import__("datetime").datetime.fromisoformat(now), db_path=path,
                                          leaseguard=None)
    packet = {"allowedEvidenceRefs": []}
    first = wrapped.advance_research(snapshot=_snapshot(), action="close_research", evidence_packet=packet)
    second = wrapped.advance_research(snapshot=_snapshot(), action="close_research", evidence_packet=packet)
    assert first.conclusion == second.conclusion == {"researchStatus": "pending_verification"}
    assert base.calls == 1


@pytest.mark.parametrize(("action", "key"), [
    ("extract_claims", "claims"), ("plan_gaps", "questions"), ("plan_queries", "queryPaths"),
    ("assess_evidence", "fulltextRequests"), ("close_research", "conclusion"),
    ("compare_companies", "companyAssessments"),
])
def test_prompt_contract_is_action_specific_and_exposes_typed_required_shapes(action, key):
    instruction, payload = request_spec(snapshot=_snapshot(), action=action, evidence_packet={"allowedEvidenceRefs": []})
    assert payload["outputContract"]["action"] == action
    assert key in payload["outputContract"]
    assert "只输出 JSON" in instruction
    assert "不可信证据" in instruction


def test_comparison_prompt_explicitly_allows_disclosed_unverified_rumor_publication():
    instruction, payload = request_spec(snapshot=_snapshot(), action="compare_companies", evidence_packet={"allowedEvidenceRefs": []})
    assert "primary、alternative 或 tied 并正常发布" in instruction
    disclosure = payload["outputContract"]["companyAssessments"][0]["evidenceDisclosure"]
    assert set(disclosure) == {"verificationStatus", "isRumor", "originStatus", "originEvidenceRef", "unverifiedReasons", "conditionalAnalysis"}


def test_completed_research_allows_a_fully_disclosed_unverified_rumor_candidate():
    cutoff = datetime(2026, 9, 8, 13, tzinfo=timezone.utc)
    document = DiscoveryDocument("source-1", 1, cutoff.isoformat(), cutoff.isoformat(), "body", None, {"title": "消息"})
    event = EventDraft("event", "initial", "reported", "消息", "rumor", {"researchClaims": []}, (document.evidence_ref,))
    disclosure = EvidenceDisclosure("unverified", True, "unknown", None, ("源头未知",), "若主体否认则撤回").to_dict()
    mapping = CompanyMappingDraft("300001.SZ", "initial", (document.evidence_ref,), {"relationship": "待核"}, "源头未知")
    comparison = EventComparison("完整比较", {"300001.SZ": CandidateComparison("条件化结论", {
        "role": "primary", "priorityReason": "业务关系仍待核", "gap": "源头未知", "rankChangeConditions": "主体否认则撤回",
        "twoDayReason": "新传闻可能引发关注", "evidenceDisclosure": disclosure}, (document.evidence_ref,), 1)}, (document.evidence_ref,))
    class Model:
        def understand(self, **_): return (event,)
        def classify_opportunity(self, **_): return {"kind": "initial", "reason": "条件化传闻", "newFacts": "出现新传闻",
                                                      "twoDayReason": "新传闻可能引发关注"}
        def prioritize(self, **_): return (("event", "300001.SZ"),)
    class Metadata:
        def lookup(self, **_): return CompanyMetadata("300001.SZ", "chinext", False, "801080.SI", cutoff)
    configuration = json.loads((Path(__file__).parents[1] / "neckline/config/k10-v1.4.json").read_text())
    configuration["sourceAdapters"] = [{"key": "fixture", "lateArrivalReplaySeconds": 1}]
    run = run_discovery(documents=(document,), configuration=configuration, model=Model(),
        verify=lambda _: pytest.fail("B39 research must replace old verification"), metadata=Metadata(), cutoff_at=cutoff,
        investigate=lambda _: InvestigationOutcome(Verification("needs_review", "已完成研究", (document.evidence_ref,),
            {"state": "available", "researchSnapshotId": "research-1"}), (mapping,), comparison, "research-1"))
    assert run.issues == (), run.issues
    assert [candidate.mapping.company_code for candidate in run.candidates] == ["300001.SZ"]


def _understand_event(*, canonical_key: str, claim_id: str, document_id: str = "source-1") -> dict:
    return {
        "canonicalKey": canonical_key, "stageKey": "reported", "eventState": "reported",
        "headline": canonical_key, "eventKind": "rumor", "facts": {},
        "sourceRefs": [{"documentId": document_id, "revision": 1}],
        "claims": [{
            "claimId": claim_id, "text": "供应商称项目进入送样", "kind": "rumor", "novelty": "new_fact",
            "speaker": "供应商", "subject": "项目", "object": "产品", "action": "送样",
            "stageOrCondition": "待确认", "timeText": "今日", "verificationStatus": "unverified",
            "decisionImpact": "影响阶段判断", "sourceRef": {"documentId": document_id, "revision": 1},
            "location": "paragraph:2",
        }],
    }


def test_b39_understand_requires_an_explicit_claims_array():
    raw = {"needsFullText": False, "events": [{
        "canonicalKey": "event", "stageKey": "reported", "eventState": "reported", "headline": "消息",
        "eventKind": "rumor", "facts": {}, "sourceRefs": [{"documentId": "source-1", "revision": 1}],
    }]}
    with pytest.raises(PipelineError) as raised:
        DeepSeekDiscoveryModel._decode_understand(raw, require_claims=True)
    assert raised.value.code == "investigation_claims_missing"


def test_one_selected_body_can_yield_multiple_typed_events_in_one_model_call():
    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, *_args, **_kwargs):
            self.calls += 1
            return LLMResult(ok=True, content=json.dumps({"needsFullText": False, "events": [
                _understand_event(canonical_key="event-a", claim_id="claim-a"),
                _understand_event(canonical_key="event-b", claim_id="claim-b"),
            ]}), provider="fixture", model="deepseek-v4-pro", prompt_tokens=3,
                completion_tokens=4, total_tokens=7, usage_unavailable=False)

    provider = Provider()
    model = DeepSeekDiscoveryModel(provider)
    # The production B39 predicate is intentionally present: this is not a
    # legacy model response permitted to omit claims.
    model._execution_policy = {"titleTriagePolicy": {}, "investigationPromptContractRevision": "k10-investigation-v1", "modelOptions": {
        "understand": {"maxTokens": 128, "thinking": {"type": "disabled"}},
        "investigation": {"maxTokens": 128, "thinking": {"type": "disabled"}},
    }}
    document = DiscoveryDocument("source-1", 1, "2026-09-08T13:00:00+00:00", "2026-09-08T13:01:00+00:00",
                                 "一篇完整正文", None, {"title": "完整报道"})
    events = model.understand(document=document)
    assert provider.calls == 1
    assert [event.canonical_key for event in events] == ["event-a", "event-b"]
    assert [event.facts["researchClaims"][0]["claimId"] for event in events] == ["claim-a", "claim-b"]


def test_b39_event_evidence_card_does_not_smuggle_source_body_through_metadata():
    model = DeepSeekDiscoveryModel(provider=None)  # no request is issued in this unit test
    model._execution_policy = {"titleTriagePolicy": {}, "investigationPromptContractRevision": "k10-investigation-v1", "modelOptions": {
        "investigation": {"maxTokens": 128, "thinking": {"type": "disabled"}},
    }}
    document = DiscoveryDocument("source-1", 1, None, "2026-09-08T13:01:00+00:00", "raw body must not recur",
                                 "locating excerpt", {"title": "消息", "url": "https://example.invalid/a",
                                                        "rawContent": "raw body must not recur", "nested": {"body": "hidden"}})
    event = EventDraft("event", "reported", "reported", "消息", "rumor", {}, (document.evidence_ref,))
    model.register_documents(documents=(document,))
    cards, _available, _independent = model._event_evidence(event)
    assert cards == [{"documentId": "source-1", "revision": 1, "publishedAt": None,
                      "fetchedAt": "2026-09-08T13:01:00+00:00", "metadata": {"title": "消息", "url": "https://example.invalid/a"},
                      "excerpt": "locating excerpt"}]


def test_b39_research_context_uses_local_history_without_an_unbound_gateway_search():
    cutoff = datetime(2026, 9, 8, 13, tzinfo=timezone.utc)
    event = EventDraft("event", "reported", "reported", "消息", "rumor", {"researchClaims": []},
                       (EvidenceRef("source-1", 1),))
    mapping = CompanyMappingDraft("300001.SZ", "送样", (EvidenceRef("source-1", 1),), {}, "待核")
    seen: dict[str, object] = {}

    def legacy_loader(**_kwargs):
        raise AssertionError("B39 comparison must not make an unbound historical search")

    def local_loader(**kwargs):
        seen.update(kwargs)
        return {"historicalCases": [], "historicalCoverage": {
            "state": "unavailable", "requestedOutcomes": ["success", "flat", "failure"],
            "presentOutcomes": [], "missingOutcomes": ["success", "flat", "failure"],
            "reason": "historical_evidence_requires_investigation_path", "sourceRefs": [],
        }}

    model = DeepSeekDiscoveryModel(
        provider=None, market_context_loader=lambda code: {"companyCode": code, "asOf": cutoff.isoformat()},
        historical_context_loader=legacy_loader, historical_local_context_loader=local_loader,
    )
    model._execution_policy = {"titleTriagePolicy": {}, "investigationPromptContractRevision": "k10-investigation-v1", "modelOptions": {
        "investigation": {"maxTokens": 128, "thinking": {"type": "disabled"}},
    }}
    model.set_scan_cutoff(cutoff)
    packet = model.research_comparison_context(event=event, mappings=(mapping,))
    assert packet["marketContext"] == {"300001.SZ": {"companyCode": "300001.SZ", "asOf": cutoff.isoformat()}}
    assert packet["historicalCases"] == []
    assert packet["historicalCoverage"]["reason"] == "historical_evidence_requires_investigation_path"
    assert seen["event"] == event and seen["mappings"] == (mapping,) and seen["as_of"] == cutoff


def test_merging_supporting_sources_keeps_the_one_body_typed_claims_without_duplicate_ids():
    first = EventDraft("same-event", "reported", "reported", "一", "rumor", {
        "researchClaims": [_understand_event(canonical_key="same-event", claim_id="claim")["claims"][0]],
    }, (EvidenceRef("source-1", 1),))
    second_claim = _understand_event(canonical_key="same-event", claim_id="claim", document_id="source-2")["claims"][0]
    second = EventDraft("same-event", "reported", "reported", "二", "rumor", {"researchClaims": [second_claim]},
                        (EvidenceRef("source-2", 1),))
    merged = _merge_same_event_sources((first, second))
    claims = merged[0].facts["researchClaims"]
    assert len(claims) == 2
    assert {claim["sourceRef"]["documentId"] for claim in claims} == {"source-1", "source-2"}
    assert len({claim["claimId"] for claim in claims}) == 2


def test_assess_prompt_preserves_claim_identity_and_requires_evidence_for_verified_claims():
    instruction, payload = request_spec(snapshot=_snapshot(), action="assess_evidence", evidence_packet={
        "allowedEvidenceRefs": [{"documentId": "source-1", "revision": 1}],
    })
    assert "既有 claimId" in instruction
    assert "新事实必须新建 claimId" in instruction
    assert "evidenceUpdates.relation=supports" in instruction
    shape = payload["outputContract"]["claims"][0]
    assert shape["text"] == "exactly the existing claim text"
    assert shape["sourceRef"] == {"documentId": "input", "revision": 1}


class _FailingResearchModel:
    def __init__(self, code: str) -> None:
        self.code, self.calls = code, 0

    def advance_research(self, **_kwargs) -> ResearchStageResult:
        self.calls += 1
        raise PipelineError("fixture failure", code=self.code)


def _checkpointed_research_wrapper(tmp_path, base, *, allow_failed_research_resume=False, network_max_attempts=1):
    path = tmp_path / "investigation-recovery.sqlite"
    if not path.exists():
        initialize_schema(path)
        now = "2026-09-08T13:05:00+00:00"
        store.enqueue_task(task_id="task-1", kind="evening_scan", idempotency_key="research", input_version="fixture",
                           input_cutoff_at=now, payload={}, budget={}, created_at=now, db_path=path)
        with sqlite3.connect(path) as conn:
            conn.execute("INSERT INTO k10_execution_config_revisions VALUES(?,?,?,?,?)",
                         ("fixture", 1, json.dumps({"fixture": True}), "c" * 64, now))
            conn.execute("INSERT INTO k10_task_execution_bindings VALUES(?,?,?,?,?,?)",
                         ("task-1", "fixture", 1, "c" * 64, "scheduled", now))
    binding = {"configId": "fixture", "revision": 1, "contentSha256": "c" * 64, "payload": {"discovery": {
        "networkMaxAttempts": network_max_attempts, "jsonRepairMaxAttempts": 0,
        "modelOptions": {"investigation": {"maxTokens": 128, "thinking": {"type": "disabled"}}},
    }}}
    return path, _CheckpointedDiscoveryModel(
        base=base, task_id="task-1", execution_profile=binding,
        cutoff_at=__import__("datetime").datetime.fromisoformat("2026-09-08T13:05:00+00:00"), db_path=path,
        leaseguard=None, allow_failed_research_resume=allow_failed_research_resume,
    )


def test_authorized_recovery_retries_known_semantic_rejection_without_losing_stable_checkpoint_identity(tmp_path):
    failing = _FailingResearchModel("investigation_result_invalid")
    path, first = _checkpointed_research_wrapper(tmp_path, failing)
    packet = {"allowedEvidenceRefs": []}
    with pytest.raises(PipelineError, match="模型阶段未完成") as rejected:
        first.advance_research(snapshot=_snapshot(), action="close_research", evidence_packet=packet)
    assert rejected.value.code == "investigation_result_invalid"
    # A CAS revision change by itself is not semantic input. The explicit
    # recovery grants exactly one distinct group for the known semantic error.
    original = _snapshot()
    resumed = ResearchSnapshot(original.snapshot_id, original.task_id, original.event_id,
        original.event_revision, original.news_cutoff_at, "2026-09-08T14:00:00+00:00",
        original.context_sha256, original.prompt_contract_revision,
        original.model_parameters_sha256, original.research_status, "ok", 9,
        original.created_at, "2026-09-08T14:00:00+00:00")
    success = _Model(ResearchStageResult("close_research", conclusion={"researchStatus": "pending_verification"}))
    _, second = _checkpointed_research_wrapper(tmp_path, success, allow_failed_research_resume=True)
    assert second.advance_research(snapshot=resumed, action="close_research", evidence_packet=packet).conclusion == {
        "researchStatus": "pending_verification"}
    assert failing.calls == success.calls == 1
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT status,safe_error_code FROM k10_execution_item_checkpoints "
                            "WHERE task_id='task-1' AND stage='model:investigation_close_research'").fetchall()
    assert {row[0] for row in rows} == {"failed", "completed"}


def test_unknown_research_provider_attempt_never_enters_model_again_on_recovery(tmp_path):
    failing = _FailingResearchModel("provider_request_outcome_unknown")
    _, first = _checkpointed_research_wrapper(tmp_path, failing)
    packet = {"allowedEvidenceRefs": []}
    with pytest.raises(PipelineError, match="模型阶段未完成") as rejected:
        first.advance_research(snapshot=_snapshot(), action="close_research", evidence_packet=packet)
    assert rejected.value.code == "provider_request_outcome_unknown"
    valid = _Model(ResearchStageResult("close_research", conclusion={"researchStatus": "pending_verification"}))
    _, resumed = _checkpointed_research_wrapper(tmp_path, valid, allow_failed_research_resume=True)
    changed = ResearchSnapshot("snapshot-1", "task-1", "event-1", 1, "2026-09-08T13:00:00+00:00",
        "2026-09-08T14:00:00+00:00", "a" * 64, "k10-investigation-v1", "b" * 64,
        "continue_research", "ok", 10, "2026-09-08T13:05:00+00:00", "2026-09-08T14:00:00+00:00")
    with pytest.raises(PipelineError, match="结果未知") as raised:
        resumed.advance_research(snapshot=changed, action="close_research", evidence_packet=packet)
    assert raised.value.code == "provider_request_outcome_unknown"
    assert valid.calls == 0


def test_unknown_actual_provider_attempt_blocks_repeat_http_request(tmp_path, monkeypatch):
    path = tmp_path / "provider-unknown.sqlite"
    initialize_schema(path)
    now = "2026-09-08T13:05:00+00:00"
    store.set_run_control(state="open", reason_code="fixture", changed_at=now, changed_by="test", db_path=path)
    store.enqueue_task(task_id="task-provider", kind="evening_scan", idempotency_key="provider-unknown",
                       input_version="fixture", input_cutoff_at=now, payload={}, budget={}, created_at=now, db_path=path)
    config_id, revision = append_approved_execution_profile(db_path=path, created_at=now, config_id="provider-profile")
    store.bind_task_execution(task_id="task-provider", execution_config_id=config_id, execution_config_revision=revision,
                              binding_kind="scheduled", bound_at=now, db_path=path)
    profile = store.task_execution_profile(task_id="task-provider", db_path=path)
    calls = []
    transport = httpx.MockTransport(lambda request: (calls.append(request), httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }))[1])
    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(**{**kwargs, "transport": transport}))
    provider = MeteredProvider(ledger_db=path, ledger_task="fixture", api_key="fixture", model="deepseek-v4-pro",
                               name="fixture", api_url="https://example.invalid/chat", read_timeout=1, use_streaming=False)
    bind_provider_execution_spending(provider=provider, task_id="task-provider", execution_profile=profile)
    # Simulate a process loss after the upstream response: the durable intent is
    # still started, so a resumed operation must never open the same socket.
    monkeypatch.setattr(provider, "_settle_attempt", lambda **_kwargs: None)
    messages = [ChatMessage("user", "bounded fixture")]
    options = {"maxTokens": 128, "thinking": {"type": "disabled"}}
    with provider_spend_context(provider=provider, task_id="task-provider", stage="investigation",
                                item_key="investigation_close:snapshot:stable", attempt=1):
        assert provider.chat(messages, enable_search=False, response_format={"type": "json_object"}, model_options=options).ok
    with provider_spend_context(provider=provider, task_id="task-provider", stage="investigation",
                                item_key="investigation_close:snapshot:stable", attempt=1):
        blocked = provider.chat(messages, enable_search=False, response_format={"type": "json_object"}, model_options=options)
    assert not blocked.ok and blocked.error_code == "provider_request_outcome_unknown"
    assert len(calls) == 1
    assert store.external_attempt_summary(task_id="task-provider", db_path=path)["started"] == 1


def _investigation_checkpoint_identity(wrapper, snapshot, action, packet, *, recovery_of=None):
    _, request = request_spec(snapshot=snapshot, action=action, evidence_packet=packet)
    item = {"snapshot": request["snapshot"], "action": action, "evidencePacket": dict(packet)}
    operation = f"investigation_{action}"
    digest = wrapper._digest(operation=operation, stage="investigation", item=item)
    if recovery_of is not None:
        item["authorizedSemanticRecoveryOf"] = recovery_of
        digest = wrapper._digest(operation=operation, stage="investigation", item=item)
    item_key = f"{snapshot.snapshot_id}:{action}"
    ledger = "model:" + operation + ":" + __import__("hashlib").sha256(
        f"{operation}\x1f{item_key}\x1f{digest}".encode("utf-8")
    ).hexdigest()
    return operation, item_key, digest, ledger


def _write_investigation_checkpoint(path, *, operation, item_key, digest, ledger, status, code, network_attempts):
    store.record_execution_checkpoint(
        task_id="task-1", item_kind="event", item_key=ledger, stage=f"model:{operation}", input_sha256=digest,
        status=status, attempt_count=network_attempts, network_attempt_count=network_attempts,
        repair_attempt_count=0, elapsed_ms=0, input_tokens=None, output_tokens=None, result=None,
        safe_error_code=code, safe_error_ref=item_key if code else None,
        updated_at="2026-09-08T13:10:00+00:00", db_path=path,
    )


def test_running_research_checkpoint_blocks_recovery_even_with_remaining_network_attempts(tmp_path):
    valid = _Model(ResearchStageResult("close_research", conclusion={"researchStatus": "pending_verification"}))
    path, wrapper = _checkpointed_research_wrapper(tmp_path, valid, network_max_attempts=2)
    packet, snapshot = {"allowedEvidenceRefs": []}, _snapshot()
    operation, item_key, digest, ledger = _investigation_checkpoint_identity(wrapper, snapshot, "close_research", packet)
    _write_investigation_checkpoint(path, operation=operation, item_key=item_key, digest=digest, ledger=ledger,
                                    status="running", code=None, network_attempts=1)
    with pytest.raises(PipelineError, match="结果未知") as raised:
        wrapper.advance_research(snapshot=snapshot, action="close_research", evidence_packet=packet)
    assert raised.value.code == "model_request_outcome_unknown"
    assert valid.calls == 0


def test_authorized_semantic_recovery_does_not_bypass_unknown_derived_checkpoint(tmp_path):
    failing = _FailingResearchModel("investigation_result_invalid")
    path, first = _checkpointed_research_wrapper(tmp_path, failing)
    packet, snapshot = {"allowedEvidenceRefs": []}, _snapshot()
    with pytest.raises(PipelineError):
        first.advance_research(snapshot=snapshot, action="close_research", evidence_packet=packet)
    operation, item_key, base_digest, _base_ledger = _investigation_checkpoint_identity(first, snapshot, "close_research", packet)
    _operation, _item_key, derived_digest, derived_ledger = _investigation_checkpoint_identity(
        first, snapshot, "close_research", packet, recovery_of=base_digest)
    _write_investigation_checkpoint(path, operation=operation, item_key=item_key, digest=derived_digest,
                                    ledger=derived_ledger, status="failed",
                                    code="provider_request_outcome_unknown", network_attempts=1)
    valid = _Model(ResearchStageResult("close_research", conclusion={"researchStatus": "pending_verification"}))
    _, resumed = _checkpointed_research_wrapper(tmp_path, valid, allow_failed_research_resume=True)
    with pytest.raises(PipelineError, match="结果未知") as raised:
        resumed.advance_research(snapshot=snapshot, action="close_research", evidence_packet=packet)
    assert raised.value.code == "provider_request_outcome_unknown"
    assert valid.calls == 0


def test_semantically_rejected_typed_checkpoint_is_not_reused_and_authorized_recovery_retries_once(tmp_path):
    packet, snapshot = {"allowedEvidenceRefs": []}, _snapshot()
    cached = _Model(ResearchStageResult("close_research", conclusion={"researchStatus": "pending_verification"}))
    path, first = _checkpointed_research_wrapper(tmp_path, cached)
    assert first.advance_research(snapshot=snapshot, action="close_research", evidence_packet=packet).conclusion == {
        "researchStatus": "pending_verification"}
    # This mirrors the later investigation relation/company validation: the
    # model output is typed JSON but its claimed source/company relation is not
    # valid for the current evidence packet.
    assert first.reject_research_result(snapshot=snapshot, action="close_research", evidence_packet=packet,
                                        safe_error_code="investigation_reference_invalid")
    operation, _item_key, _digest, ledger = _investigation_checkpoint_identity(first, snapshot, "close_research", packet)
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT status,result_json,safe_error_code FROM k10_execution_item_checkpoints "
                           "WHERE task_id='task-1' AND item_key=? AND stage=?", (ledger, f"model:{operation}")).fetchone()
    assert row is not None and row[0] == "failed" and row[1] is not None and row[2] == "investigation_reference_invalid"
    retried = _Model(ResearchStageResult("close_research", conclusion={"researchStatus": "pending_verification"}))
    _, recovery = _checkpointed_research_wrapper(tmp_path, retried, allow_failed_research_resume=True)
    assert recovery.advance_research(snapshot=snapshot, action="close_research", evidence_packet=packet).conclusion == {
        "researchStatus": "pending_verification"}
    assert cached.calls == retried.calls == 1
