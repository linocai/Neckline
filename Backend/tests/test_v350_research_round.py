"""B78 direct research round: no legacy stage projection or replay POST."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import sqlite3

import pytest

from neckline.k10.investigation import InvestigationError
from neckline.k10.research_context import canonical_body_identity, question_dependency
from neckline.k10.research_contracts import (
    RESEARCH_ROUND_ACTION, RESEARCH_ROUND_CONTRACT, Claim, FullTextRequest, QueryPath,
    Question, ResearchRoundResult, ResearchSnapshot,
)
from neckline.k10.research_runtime import (
    _Investigation, _b78_filter_pool_result, _b78_visible_shared_claims,
    build_research_round_packet, normalize_research_round_result, research_round_request_spec, run_research_round,
)
from neckline.k10.research_store import append_research_round, create_research_snapshot, load_research_round_state
from neckline.k10.schema import initialize_schema
from neckline.k10 import store
from neckline.k10.discovery import DiscoveryDocument, EventDraft, EvidenceRef
from neckline.k10.delivery import runtime_contract
from neckline.k10.verification import VerificationEvidenceBundle


REF = {"documentId": "doc-1", "revision": 1}


def _snapshot() -> ResearchSnapshot:
    return ResearchSnapshot(
        "round-snapshot", "task-round", "event-round", 1,
        "2026-09-20T00:30:00+00:00", "2026-09-20T01:20:00+00:00",
        "a" * 64, RESEARCH_ROUND_CONTRACT, "b" * 64,
        "continue_research", "ok", 1,
        "2026-09-20T00:30:00+00:00", "2026-09-20T00:30:00+00:00",
    )


def _packet() -> dict:
    return {
        "allowedEvidenceRefs": [REF],
        "claims": [{
            "claimId": "claim-1", "text": "公司披露项目处于送样阶段", "kind": "factual_assertion",
            "novelty": "new_stage", "speaker": "公司", "subject": "项目", "object": "客户",
            "action": "送样", "stageOrCondition": "尚未签约", "timeText": "今日",
            "verificationStatus": "unverified", "decisionImpact": "影响关联判断",
            "sourceRef": REF, "location": "excerpt",
        }],
        "questions": [], "evidenceCards": [{**REF, "excerpt": "公司披露项目处于送样阶段"}],
        "companyScope": {"fixedPool": [], "companyProfiles": [], "candidateCompanyCodes": []},
    }


def _complete_round(ref: dict = REF) -> dict:
    return {
        "action": RESEARCH_ROUND_ACTION,
        "claims": [], "questions": [], "queryPaths": [], "evidenceUpdates": [], "fulltextRequests": [],
        "conclusion": {
            "researchStatus": "ready_for_comparison", "stopReason": "现有资料足以比较并已披露未核实环节",
            "resumeCondition": None,
            "companyMappings": [{
                "companyCode": "000001.SZ", "affectedStage": "送样",
                "relationEvidence": [ref], "inference": {"relation": "供应链关联"}, "uncertainty": "尚未确认订单",
            }],
        },
        "comparison": {"summary": "事件仍处于送样阶段", "evidenceRefs": [ref]},
        "companyAssessments": [{
            "companyCode": "000001.SZ", "role": "primary", "rank": 1,
            "summary": "关联明确但订单待核", "priorityReason": "直接关联", "gap": "订单未确认",
            "rankChangeConditions": "订单披露", "twoDayReason": "以送样进展为观察事实",
            "evidenceDisclosure": {
                "verificationStatus": "unverified", "isRumor": False, "originStatus": "identified",
                "originEvidenceRef": ref, "unverifiedReasons": ["尚未见订单确认"], "conditionalAnalysis": None,
            },
        }],
    }


class _DirectModel:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def advance_research_round(self, *, snapshot, evidence_packet):
        self.calls.append((snapshot.snapshot_id, evidence_packet))
        return self.reply


def test_b78_direct_round_uses_one_direct_model_method_and_never_legacy_actions():
    packet = build_research_round_packet(_packet())
    model = _DirectModel(_complete_round())

    result = run_research_round(model, snapshot=_snapshot(), evidence_packet=packet)

    assert isinstance(result, ResearchRoundResult)
    assert result.action == RESEARCH_ROUND_ACTION
    assert len(model.calls) == 1
    assert "plan_gaps" not in result.to_dict()
    assert "close_research" not in result.to_dict()
    instruction, request = research_round_request_spec(snapshot=_snapshot(), evidence_packet=packet)
    assert "完整的事件研究轮次" in instruction
    assert request["action"] == RESEARCH_ROUND_ACTION
    assert request["outputContract"]["action"] == RESEARCH_ROUND_ACTION


def test_b78_round_receipt_replay_validates_locally_without_second_provider_call():
    packet = build_research_round_packet(_packet())
    model = _DirectModel(_complete_round())

    result = run_research_round(model, snapshot=_snapshot(), evidence_packet=packet,
                                restored_receipt=_complete_round())

    assert result.conclusion["researchStatus"] == "ready_for_comparison"
    assert model.calls == []


def test_b78_visible_shared_claim_is_question_eligible_but_never_inherits_peer_verification():
    shared = {**_packet()["claims"][0], "claimId": "shared-source-fact", "text": "同源资料仍称送样",
              "verificationStatus": "verified", "provenance": {"snapshotId": "peer-round", "snapshotRevision": 1}}
    packet = build_research_round_packet({**_packet(), "reusableSourceEvidence": {"claims": [shared]}})
    visible = _b78_visible_shared_claims(packet)
    question = Question(
        "shared-question", ("shared-source-fact",), ("000001.SZ",), "送样是否已转订单",
        (REF,), ("订单或反证资料",), "有订单披露", "明确未签约", "会改变公司关联强度",
        "open", "出现直接资料后复核",
    )
    model = _DirectModel({
        "action": RESEARCH_ROUND_ACTION, "claims": [], "questions": [question.to_dict()],
        "queryPaths": [], "evidenceUpdates": [], "fulltextRequests": [],
        "conclusion": {"researchStatus": "continue_research", "stopReason": "还需订单资料",
                       "resumeCondition": "获得订单公告", "companyMappings": []},
        "comparison": None, "companyAssessments": [],
    })

    result = run_research_round(model, snapshot=_snapshot(), evidence_packet=packet)

    assert visible[0].claim_id == "shared-source-fact"
    assert visible[0].verification_status == "unverified"
    assert visible[0].source_ref == REF and visible[0].location == "excerpt"
    assert result.questions[0].claim_ids == ("shared-source-fact",)


def test_b78_pool_filter_keeps_in_pool_mapping_and_drops_only_its_outside_pair():
    packet = build_research_round_packet({
        **_packet(),
        "companyScope": {"fixedPool": [{"companyCode": "000001.SZ"}], "companyProfiles": [],
                         "candidateCompanyCodes": ["000001.SZ"]},
    })
    raw = _complete_round()
    outsider_mapping = {**raw["conclusion"]["companyMappings"][0], "companyCode": "300487.SZ"}
    outsider_assessment = {**raw["companyAssessments"][0], "companyCode": "300487.SZ"}
    raw["conclusion"]["companyMappings"].append(outsider_mapping)
    raw["companyAssessments"].append(outsider_assessment)
    result = ResearchRoundResult.from_dict(raw)

    filtered = _b78_filter_pool_result(result=result, evidence_packet=packet)

    assert [row["companyCode"] for row in result.conclusion["companyMappings"]] == ["000001.SZ", "300487.SZ"]
    assert [row["companyCode"] for row in filtered.conclusion["companyMappings"]] == ["000001.SZ"]
    assert [row["companyCode"] for row in filtered.company_assessments] == ["000001.SZ"]


def test_b78_duplicate_packet_claim_cannot_replace_immutable_source_fact():
    packet = build_research_round_packet(_packet())
    altered = {**_packet()["claims"][0], "text": "模型篡改的送样事实", "verificationStatus": "verified"}
    independent = {**_packet()["claims"][0], "claimId": "claim-independent", "text": "独立的新来源事实"}
    raw = _complete_round()
    raw["claims"] = [altered, independent]
    result = ResearchRoundResult.from_dict(raw)

    reconciled = normalize_research_round_result(result=result, evidence_packet=packet)

    assert [claim.claim_id for claim in reconciled.claims] == ["claim-independent"]
    assert _b78_visible_shared_claims(packet) == ()


def test_b82_normalization_keeps_valid_mapping_when_model_explains_an_unmapped_exclusion():
    """An old excluded explanation is not a second comparison coverage set."""
    packet = build_research_round_packet(_packet())
    raw = _complete_round()
    raw["conclusion"]["runtimeOutputSanitization"] = {
        "discardedNonMappingExcludedAssessments": "untrusted-model-value",
    }
    raw["companyAssessments"].append({
        **raw["companyAssessments"][0], "companyCode": "300002.SZ", "role": "excluded", "rank": None,
        "summary": "召回资料没有本事件关联", "priorityReason": "没有关系证据", "gap": "无",
        "rankChangeConditions": "出现关联公告", "twoDayReason": "不属于本轮比较",
    })

    result = run_research_round(_DirectModel(raw), snapshot=_snapshot(), evidence_packet=packet)

    assert [row["companyCode"] for row in result.company_assessments] == ["000001.SZ"]
    assert result.conclusion["runtimeOutputSanitization"] == {
        "discardedNonMappingExcludedAssessments": 1,
    }


@pytest.mark.parametrize("role", ["primary", "alternative", "tied", "pending"])
def test_b82_nonmapping_business_assessment_remains_a_coverage_error(role):
    packet = build_research_round_packet(_packet())
    raw = _complete_round()
    raw["companyAssessments"].append({
        **raw["companyAssessments"][0], "companyCode": "300002.SZ", "role": role,
        "rank": 2 if role in {"primary", "alternative", "tied"} else None,
    })

    with pytest.raises(InvestigationError) as caught:
        run_research_round(_DirectModel(raw), snapshot=_snapshot(), evidence_packet=packet)

    assert caught.value.code == "investigation_company_coverage_invalid"


def test_b82_mapping_without_its_assessment_remains_a_coverage_error():
    packet = build_research_round_packet(_packet())
    raw = _complete_round()
    raw["companyAssessments"] = []

    with pytest.raises(InvestigationError) as caught:
        run_research_round(_DirectModel(raw), snapshot=_snapshot(), evidence_packet=packet)

    assert caught.value.code == "investigation_company_coverage_invalid"


def test_b82_company_mappings_are_a_strict_nonrepeating_comparison_set():
    packet = build_research_round_packet(_packet())
    raw = _complete_round()
    raw["conclusion"]["companyMappings"].append(dict(raw["conclusion"]["companyMappings"][0]))

    with pytest.raises(InvestigationError) as caught:
        run_research_round(_DirectModel(raw), snapshot=_snapshot(), evidence_packet=packet)

    # The typed decoder rejects the duplicate before normalization, which is
    # the earliest and therefore canonical strict-set boundary.
    assert caught.value.code == "investigation_result_invalid"


def test_b82_mapping_with_an_unknown_relation_source_remains_a_reference_error():
    packet = build_research_round_packet(_packet())
    raw = _complete_round()
    raw["conclusion"]["companyMappings"][0]["relationEvidence"] = [
        {"documentId": "unknown-source", "revision": 1},
    ]

    with pytest.raises(InvestigationError) as caught:
        run_research_round(_DirectModel(raw), snapshot=_snapshot(), evidence_packet=packet)

    assert caught.value.code == "investigation_reference_invalid"


def test_b82_nonmapping_exclusion_with_unknown_origin_is_not_silently_discarded():
    """Cleanup may remove old explanations, never an unshown source claim."""
    packet = build_research_round_packet(_packet())
    raw = _complete_round()
    raw["companyAssessments"].append({
        **raw["companyAssessments"][0], "companyCode": "300002.SZ", "role": "excluded", "rank": None,
        "evidenceDisclosure": {
            **raw["companyAssessments"][0]["evidenceDisclosure"],
            "originEvidenceRef": {"documentId": "unknown-source", "revision": 1},
        },
    })

    with pytest.raises(InvestigationError) as caught:
        run_research_round(_DirectModel(raw), snapshot=_snapshot(), evidence_packet=packet)

    assert caught.value.code == "investigation_reference_invalid"


def test_b82_production_adapter_rejects_in_pool_nonmapping_recommendation():
    """The live adapter must not let a fixed pool hide an unmapped ranking."""
    from neckline.k10.pipeline import DeepSeekDiscoveryModel

    packet = build_research_round_packet({
        **_packet(),
        "companyScope": {
            "fixedPool": [{"companyCode": "000001.SZ"}, {"companyCode": "300002.SZ"}],
            "companyProfiles": [], "candidateCompanyCodes": ["000001.SZ", "300002.SZ"],
        },
    })
    raw = _complete_round()
    raw["companyAssessments"].append({
        **raw["companyAssessments"][0], "companyCode": "300002.SZ", "role": "primary", "rank": 2,
    })
    adapter = object.__new__(DeepSeekDiscoveryModel)
    adapter._json = lambda **_kwargs: raw
    adapter._model_options = lambda _stage: {}

    with pytest.raises(InvestigationError) as caught:
        adapter.advance_research_round(snapshot=_snapshot(), evidence_packet=packet)

    assert caught.value.code == "investigation_company_coverage_invalid"


def test_b82_duplicate_packet_claim_is_normalized_before_validation_and_receipt_replay():
    """A stale paid reply gets the same canonical local result without a POST."""
    packet = build_research_round_packet(_packet())
    raw = _complete_round()
    raw["claims"] = [{
        **_packet()["claims"][0], "text": "模型篡改的送样事实", "verificationStatus": "verified",
        "sourceRef": {"documentId": "unknown-source", "revision": 1}, "location": "paragraph:99",
    }]
    model = _DirectModel(raw)

    live = run_research_round(model, snapshot=_snapshot(), evidence_packet=packet)
    replay = run_research_round(model, snapshot=_snapshot(), evidence_packet=packet,
                                restored_receipt=raw)

    assert not live.claims and not replay.claims
    assert len(model.calls) == 1


def test_b82_production_research_adapter_normalizes_duplicate_claim_before_validation():
    """Exercise the actual production adapter order, without a provider POST."""
    from neckline.k10.pipeline import DeepSeekDiscoveryModel

    packet = build_research_round_packet(_packet())
    raw = _complete_round()
    raw["claims"] = [{
        **_packet()["claims"][0], "text": "模型篡改的送样事实", "verificationStatus": "verified",
        "sourceRef": {"documentId": "unknown-source", "revision": 1}, "location": "paragraph:99",
    }]
    adapter = object.__new__(DeepSeekDiscoveryModel)
    adapter._json = lambda **_kwargs: raw
    adapter._model_options = lambda _stage: {}

    result = adapter.advance_research_round(snapshot=_snapshot(), evidence_packet=packet)

    assert not result.claims


def test_b82_new_verified_claim_without_support_is_not_normalized_into_acceptance():
    packet = build_research_round_packet(_packet())
    raw = _complete_round()
    raw["claims"] = [{
        **_packet()["claims"][0], "claimId": "new-unsupported-verified", "text": "新增但无支持命题",
        "verificationStatus": "verified",
    }]

    with pytest.raises(InvestigationError) as caught:
        run_research_round(_DirectModel(raw), snapshot=_snapshot(), evidence_packet=packet)

    assert caught.value.code == "investigation_support_missing"


def test_b78_round_rejects_old_compound_action_instead_of_projecting_it():
    packet = build_research_round_packet(_packet())
    model = _DirectModel({"action": "assess_and_decide", "claims": []})

    try:
        run_research_round(model, snapshot=_snapshot(), evidence_packet=packet)
    except InvestigationError as error:
        assert error.code == "investigation_result_invalid"
    else:
        raise AssertionError("B78 must reject, not project, a legacy compound result")


def test_b78_round_allows_only_context_read_when_a_real_gap_remains():
    packet = build_research_round_packet(_packet())
    model = _DirectModel({
        "action": RESEARCH_ROUND_ACTION,
        "contextRequests": [{"kind": "source", "purpose": "核对送样是否已转订单",
                             "sourceRef": REF, "location": "excerpt"}],
    })

    result = run_research_round(model, snapshot=_snapshot(), evidence_packet=packet)

    assert len(result.context_requests) == 1
    assert result.conclusion is None


def test_b78_body_identity_ignores_document_url_and_metadata_versions(tmp_path):
    """A real stored duplicate body cannot manufacture a new research route."""
    path = tmp_path / "canonical-body.sqlite"
    initialize_schema(path)
    common = {
        "published_at": "2026-09-20T00:00:00+00:00", "published_precision": "exact",
        "fetched_at": "2026-09-20T00:10:00+00:00", "original_text": "同一段正文\r\n保留原意",
        "excerpt": "同一段正文", "fetch_version": "fixture", "created_at": "2026-09-20T00:10:00+00:00",
        "db_path": path,
    }
    first = store.append_document_version(
        document_id="same-body-a", source_key="fixture-a", external_id="a",
        canonical_url="https://example.test/a?tracking=one", content_sha256="a" * 64,
        metadata={"url": "https://example.test/a?tracking=one", "fetchedBy": "one"}, **common,
    )
    second = store.append_document_version(
        document_id="same-body-b", source_key="fixture-b", external_id="b",
        canonical_url="https://example.test/b?tracking=two", content_sha256="b" * 64,
        metadata={"url": "https://example.test/b?tracking=two", "fetchedBy": "two"}, **common,
    )
    rows = store.load_document_versions(refs=[
        {"documentId": first.document_id, "revision": first.revision},
        {"documentId": second.document_id, "revision": second.revision},
    ], db_path=path)
    assert {row["contentSha256"] for row in rows} == {"a" * 64, "b" * 64}
    identities = {(row["documentId"], row["revision"]): canonical_body_identity(row) for row in rows}
    assert len(set(identities.values())) == 1
    first_dependency = question_dependency({
        "claimIds": ["claim-1"], "companyCodes": ["000001.SZ"], "question": "订单是否确认",
        "supportCondition": "披露订单", "refuteCondition": "明确未签约",
        "knownEvidence": [{"documentId": first.document_id, "revision": first.revision}],
    }, content_sha256_by_ref=identities)
    second_dependency = question_dependency({
        "claimIds": ["claim-1"], "companyCodes": ["000001.SZ"], "question": "订单是否确认",
        "supportCondition": "披露订单", "refuteCondition": "明确未签约",
        "knownEvidence": [{"documentId": second.document_id, "revision": second.revision}],
    }, content_sha256_by_ref=identities)
    assert first_dependency["knownEvidence"] == second_dependency["knownEvidence"]


def _document(document_id: str, *, text: str, excerpt: str) -> DiscoveryDocument:
    return DiscoveryDocument(
        document_id, 1, "2026-09-20T00:00:00+00:00", "2026-09-20T00:10:00+00:00",
        text, excerpt, {"contentVersionAtCutoff": "confirmed"},
    )


def _question(*, refs: list[dict] | None = None) -> Question:
    return Question(
        "q-order", ("claim-1",), ("000001.SZ",), "送样是否已经转为订单",
        tuple(refs or [REF]), ("订单或反证资料",), "有订单披露", "明确未签约",
        "会改变公司关联强度", "open", "出现直接资料后复核",
    )


def _query() -> QueryPath:
    return QueryPath(
        "path-order", "q-order", "公司 送样 订单 公告", "寻找订单反证", "公司公告",
        "当前资料没有订单状态", "直接公告", "会改变关联强度", "planned",
        purpose_kind="counterevidence", target_refs=({"kind": "claim", "claimId": "claim-1"},),
    )


class _RoundModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.context_calls = []

    def advance_research_round(self, *, snapshot, evidence_packet):
        self.calls.append(evidence_packet)
        return self.replies.pop(0)

    def research_comparison_context(self, *, event, mappings):
        self.context_calls.append(tuple(item.company_code for item in mappings))
        return {
            "marketContext": {item.company_code: {"state": "available", "asOf": "2026-09-20"}
                              for item in mappings},
            "historicalCases": [],
            "historicalCoverage": {
                "state": "unavailable", "requestedOutcomes": ["success", "flat", "failure"],
                "presentOutcomes": [], "missingOutcomes": ["success", "flat", "failure"],
                "reason": "no_local_cases", "sourceRefs": [],
            },
        }


class _RoundVerifier:
    def __init__(self, *, search_document: DiscoveryDocument | None = None,
                 fulltext_document: DiscoveryDocument | None = None):
        self.search_document, self.fulltext_document = search_document, fulltext_document
        self.search_paths, self.search_query_paths, self.fulltext_requests = [], [], []

    def fetch(self, **kwargs):
        self.search_paths.append(kwargs["query_path"].path_id)
        self.search_query_paths.append(kwargs["query_path"])
        document = self.search_document
        assert document is not None
        return VerificationEvidenceBundle("available", (document,), (document,), {
            "state": "available", "requestState": "completed", "operation": "search",
            "reason": "fixture_search_hit",
        })

    def fetch_fulltext(self, **kwargs):
        self.fulltext_requests.append(kwargs["request"].request_id)
        document = self.fulltext_document
        assert document is not None
        return VerificationEvidenceBundle("available", (document,), (document,), {
            "state": "available", "requestState": "completed", "operation": "extract",
            "admissionRef": kwargs["request"].source_ref, "reason": "fixture_extract",
        })


def _runtime(tmp_path, *, model, verifier, extra_documents=(), allowed_extra=(),
             new_external_admission_guard=None):
    original = _document("doc-1", text="原始事件正文\n公司披露项目处于送样阶段。", excerpt="公司披露项目处于送样阶段")
    event = EventDraft("event-round", "stage-round", "new", "送样进展", "company_event", {
        "researchClaims": [_packet()["claims"][0]],
    }, (original.evidence_ref,))
    runtime = object.__new__(_Investigation)
    runtime.model, runtime.verifier, runtime.event, runtime.db_path = model, verifier, event, tmp_path / "round.sqlite"
    runtime.identity, runtime.guard = "round-snapshot", None
    runtime.clock = lambda: datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)
    runtime.cutoff, runtime.cutoff_inclusive = datetime(2026, 9, 20, 0, 30, tzinfo=timezone.utc), False
    runtime.allow_failed_resume = False
    runtime.new_external_admission_guard = new_external_admission_guard
    runtime.context = {
        "canonicalKey": event.canonical_key, "stageKey": event.stage_key, "eventState": event.event_state,
        "headline": event.headline, "eventKind": event.event_kind,
    }
    runtime.state = {"snapshot": _snapshot(), "questions": []}
    runtime.documents = {original.evidence_ref: original,
                         **{document.evidence_ref: document for document in extra_documents}}
    runtime.allowed = {original.evidence_ref, *allowed_extra}
    runtime._company_scope = lambda: {
        "candidateCompanyCodes": ["000001.SZ"],
        "companyProfiles": [{"identity": {"ts_code": "000001.SZ"}, "name": "测试公司",
                             "fieldManifest": [], "business": "测试业务"}],
    }
    return runtime


def test_non_b78_research_construction_is_read_only_and_writes_nothing(tmp_path):
    original = _document("legacy-source", text="冻结旧资料", excerpt="冻结旧资料")
    event = EventDraft("legacy-event", "legacy-stage", "new", "旧任务", "company_event", {},
                       (original.evidence_ref,))
    database = tmp_path / "legacy-must-not-run.sqlite"

    with pytest.raises(InvestigationError, match="仅可读取") as error:
        _Investigation(
            model=object(), verifier=object(), task_id="legacy-task", event=event,
            documents={original.evidence_ref: original}, execution_profile={},
            cutoff_at=datetime(2026, 9, 20, tzinfo=timezone.utc), db_path=database,
            created_at=datetime(2026, 9, 20, tzinfo=timezone.utc), leaseguard=None,
            cutoff_inclusive=False, snapshot_created=None,
            clock=lambda: datetime(2026, 9, 20, tzinfo=timezone.utc),
            runtime_contract={"research": "k10-research-3.4.0-b76"},
        )

    assert error.value.code == "research_legacy_read_only"
    assert not database.exists()


def test_b82_new_research_admission_runs_only_before_creating_a_snapshot(tmp_path):
    database = tmp_path / "research-admission.sqlite"
    initialize_schema(database)
    original = _document("admission-source", text="冻结来源正文", excerpt="冻结来源摘要")
    event = EventDraft("admission-event", "stage-round", "new", "调查准入", "company_event", {
        "researchClaims": [_packet()["claims"][0]],
    }, (original.evidence_ref,))
    profile = {"payload": {"discovery": {
        "investigationPromptContractRevision": RESEARCH_ROUND_CONTRACT,
        "model": "offline-fixture", "modelOptions": {"investigation": {}},
    }}}
    moment = datetime(2026, 9, 20, tzinfo=timezone.utc)
    store.set_run_control(state="open", reason_code="fixture", changed_at=moment,
                          changed_by="research-admission-test", db_path=database)
    store.enqueue_task(task_id="admission-task", kind="evening_scan", idempotency_key="research-admission",
        input_version="frozen-input", input_cutoff_at=moment, payload={"runtimeContract": runtime_contract()},
        budget={"maxAttempts": 1}, created_at=moment, db_path=database)
    calls = []

    first = _Investigation(
        model=_DirectModel(_complete_round()), verifier=_RoundVerifier(), task_id="admission-task", event=event,
        documents={original.evidence_ref: original}, execution_profile=profile, cutoff_at=moment,
        db_path=database, created_at=moment, leaseguard=None, cutoff_inclusive=False,
        snapshot_created=None, clock=lambda: moment, runtime_contract=runtime_contract(),
        new_research_admission_guard=lambda: calls.append("new"),
    )

    def should_not_run():
        raise AssertionError("a restored snapshot must not consume new-research admission")

    restored = _Investigation(
        model=_DirectModel(_complete_round()), verifier=_RoundVerifier(), task_id="admission-task", event=event,
        documents={original.evidence_ref: original}, execution_profile=profile, cutoff_at=moment,
        db_path=database, created_at=moment, leaseguard=None, cutoff_inclusive=False,
        snapshot_created=None, clock=lambda: moment, runtime_contract=runtime_contract(),
        new_research_admission_guard=should_not_run,
    )

    assert calls == ["new"]
    assert first.snapshot.snapshot_id == restored.snapshot.snapshot_id
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM k10_research_snapshot_revisions").fetchone()[0] == 1


def test_b82_new_research_admission_rejects_before_any_snapshot_write(tmp_path):
    database = tmp_path / "research-admission-rejected.sqlite"
    initialize_schema(database)
    original = _document("rejected-source", text="冻结来源正文", excerpt="冻结来源摘要")
    event = EventDraft("rejected-event", "stage-round", "new", "调查准入", "company_event", {
        "researchClaims": [_packet()["claims"][0]],
    }, (original.evidence_ref,))
    profile = {"payload": {"discovery": {
        "investigationPromptContractRevision": RESEARCH_ROUND_CONTRACT,
        "model": "offline-fixture", "modelOptions": {"investigation": {}},
    }}}
    moment = datetime(2026, 9, 20, tzinfo=timezone.utc)
    store.set_run_control(state="open", reason_code="fixture", changed_at=moment,
                          changed_by="research-admission-test", db_path=database)
    store.enqueue_task(task_id="rejected-task", kind="evening_scan", idempotency_key="research-admission-rejected",
        input_version="frozen-input", input_cutoff_at=moment, payload={"runtimeContract": runtime_contract()},
        budget={"maxAttempts": 1}, created_at=moment, db_path=database)

    def reject():
        raise InvestigationError("晨报保留最终排序时间，停止新的事件研究", code="morning_closeout_reserve")

    with pytest.raises(InvestigationError) as caught:
        _Investigation(
            model=_DirectModel(_complete_round()), verifier=_RoundVerifier(), task_id="rejected-task", event=event,
            documents={original.evidence_ref: original}, execution_profile=profile, cutoff_at=moment,
            db_path=database, created_at=moment, leaseguard=None, cutoff_inclusive=False,
            snapshot_created=None, clock=lambda: moment, runtime_contract=runtime_contract(),
            new_research_admission_guard=reject,
        )

    assert caught.value.code == "morning_closeout_reserve"
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM k10_events").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM k10_research_snapshot_revisions").fetchone()[0] == 0


def test_b82_search_admission_runs_only_at_a_new_provider_search_boundary(tmp_path):
    searched = _document("search-admission", text="独立公告", excerpt="独立公告")
    first = {
        "action": RESEARCH_ROUND_ACTION, "claims": [], "questions": [_question().to_dict()],
        "queryPaths": [_query().to_dict()], "evidenceUpdates": [], "fulltextRequests": [],
        "conclusion": {"researchStatus": "continue_research", "companyMappings": [],
                       "stopReason": "仍需查询", "resumeCondition": "取得公告"},
        "comparison": None, "companyAssessments": [],
    }
    calls = []
    runtime = _runtime(
        tmp_path, model=_RoundModel([first, _complete_round({"documentId": searched.document_id, "revision": 1})]),
        verifier=_RoundVerifier(search_document=searched),
        new_external_admission_guard=lambda: calls.append("search"),
    )

    runtime._run_b78()

    assert calls == ["search"]


def test_b82_original_source_fulltext_reuse_does_not_consume_new_external_admission(tmp_path):
    first = {
        "action": RESEARCH_ROUND_ACTION, "claims": [], "questions": [_question().to_dict()],
        "queryPaths": [], "evidenceUpdates": [], "fulltextRequests": [FullTextRequest(
            "original-reuse", "q-order", REF, "需要原文限定条件", "影响关联", "requested",
        ).to_dict()],
        "conclusion": {"researchStatus": "continue_research", "companyMappings": [],
                       "stopReason": "需要本地原文", "resumeCondition": "读原文"},
        "comparison": None, "companyAssessments": [],
    }
    calls = []
    runtime = _runtime(tmp_path, model=_RoundModel([first, _complete_round()]), verifier=_RoundVerifier(),
                       new_external_admission_guard=lambda: calls.append("unexpected"))

    runtime._run_b78()

    assert calls == []


def test_b78_search_material_is_visible_before_a_second_direct_round(tmp_path):
    searched = _document("search-confirmation", text="独立公告\n公司明确尚未签约。", excerpt="公司明确尚未签约。")
    searched_ref = {"documentId": searched.document_id, "revision": searched.revision}
    first = {
        "action": RESEARCH_ROUND_ACTION,
        "claims": [], "questions": [_question().to_dict()], "queryPaths": [_query().to_dict()],
        "evidenceUpdates": [], "fulltextRequests": [],
        "conclusion": {"researchStatus": "continue_research", "stopReason": "需要订单反证",
                       "resumeCondition": "取得独立公告", "companyMappings": []},
        "comparison": None, "companyAssessments": [],
    }
    model = _RoundModel([first, _complete_round(searched_ref)])
    verifier = _RoundVerifier(search_document=searched)
    outcome = _runtime(tmp_path, model=model, verifier=verifier)._run_b78()

    assert len(verifier.search_paths) == 1
    assert verifier.search_query_paths[0].query == _query().query
    scoped = verifier.search_query_paths[0].question_scope
    assert scoped and scoped["questionId"] == "q-order" and scoped["scopeSha256"]
    assert len(model.calls) == 2
    assert model.context_calls[0] == ("000001.SZ",)
    assert model.calls[0]["marketContext"]["000001.SZ"]["state"] == "available"
    assert model.calls[0]["historicalCoverage"]["reason"] == "no_local_cases"
    assert searched_ref in model.calls[1]["allowedEvidenceRefs"]
    assert any(row["documentId"] == searched.document_id and row.get("excerpt") == searched.excerpt
               for row in model.calls[1]["evidenceCards"])
    assert outcome.mappings[0].relation_evidence == (searched.evidence_ref,)


def test_b78_keeps_distinct_necessary_queries_for_one_question(tmp_path):
    """An issuer notice and industry report are separate necessary probes."""
    searched = _document("search-two-routes", text="独立公告\n公司明确尚未签约。", excerpt="公司明确尚未签约。")
    issuer_notice = _query()
    industry_report = replace(
        issuer_notice, path_id="path-industry-report", query="项目 行业媒体 订单进展",
        intent="寻找行业媒体的订单反证", target_source="行业媒体",
    )
    first = {
        "action": RESEARCH_ROUND_ACTION,
        "claims": [], "questions": [_question().to_dict()],
        "queryPaths": [issuer_notice.to_dict(), industry_report.to_dict()],
        "evidenceUpdates": [], "fulltextRequests": [],
        "conclusion": {"researchStatus": "continue_research", "stopReason": "需要订单反证",
                       "resumeCondition": "取得必要来源", "companyMappings": []},
        "comparison": None, "companyAssessments": [],
    }
    verifier = _RoundVerifier(search_document=searched)
    outcome = _runtime(
        tmp_path, model=_RoundModel([first, _complete_round({"documentId": searched.document_id, "revision": 1})]),
        verifier=verifier,
    )._run_b78()

    assert len(set(verifier.search_paths)) == 2
    assert [path.query for path in verifier.search_query_paths] == [issuer_notice.query, industry_report.query]
    assert outcome.mappings[0].relation_evidence == (searched.evidence_ref,)


def test_b78_prunes_redundant_cross_question_path_before_one_valid_search(tmp_path):
    searched = _document("search-pruned", text="独立公告\n公司明确尚未签约。", excerpt="公司明确尚未签约。")
    second_question = Question(
        "q-other", ("claim-1",), ("000002.SZ",), "另一家公司是否有关联",
        (REF,), ("公司关联资料",), "披露关联", "明确无关联", "会改变关联强度", "open", "出现公司资料后复核",
    )
    invalid = replace(_query(), path_id="cross-question", query="另一家公司送样公告",
                      target_refs=({"kind": "company", "companyCode": "000002.SZ"},))
    first = {
        "action": RESEARCH_ROUND_ACTION, "claims": [],
        "questions": [_question().to_dict(), second_question.to_dict()],
        "queryPaths": [_query().to_dict(), invalid.to_dict()], "evidenceUpdates": [], "fulltextRequests": [],
        "conclusion": {"researchStatus": "continue_research", "stopReason": "需要订单反证",
                       "resumeCondition": "取得独立公告", "companyMappings": []},
        "comparison": None, "companyAssessments": [],
    }
    verifier = _RoundVerifier(search_document=searched)
    outcome = _runtime(tmp_path, model=_RoundModel([first, _complete_round({"documentId": searched.document_id, "revision": 1})]),
                       verifier=verifier)._run_b78()

    assert len(verifier.search_paths) == 1
    assert verifier.search_query_paths[0].query == _query().query
    assert outcome.mappings[0].company_code == "000001.SZ"


def test_b78_rejects_unknown_query_target_before_any_search_post(tmp_path):
    searched = _document("search-poison", text="独立公告", excerpt="独立公告")
    poison = replace(_query(), path_id="unknown-claim", query="未知命题公告",
                     target_refs=({"kind": "claim", "claimId": "invented-claim"},))
    first = {
        "action": RESEARCH_ROUND_ACTION, "claims": [], "questions": [_question().to_dict()],
        "queryPaths": [_query().to_dict(), poison.to_dict()], "evidenceUpdates": [], "fulltextRequests": [],
        "conclusion": {"researchStatus": "continue_research", "stopReason": "需要订单反证",
                       "resumeCondition": "取得独立公告", "companyMappings": []},
        "comparison": None, "companyAssessments": [],
    }
    verifier = _RoundVerifier(search_document=searched)

    with pytest.raises(InvestigationError) as error:
        _runtime(tmp_path, model=_RoundModel([first]), verifier=verifier)._run_b78()

    assert error.value.code == "investigation_path_scope_invalid"
    assert verifier.search_paths == []


def test_b78_existing_source_read_is_visible_before_a_second_direct_round(tmp_path):
    first = {
        "action": RESEARCH_ROUND_ACTION,
        "contextRequests": [{
            "kind": "source", "purpose": "核对送样是否已转订单", "sourceRef": REF,
            "location": "excerpt",
        }],
    }
    model = _RoundModel([first, _complete_round()])
    outcome = _runtime(tmp_path, model=model, verifier=_RoundVerifier())._run_b78()

    assert len(model.calls) == 2
    assert "excerpt" not in model.calls[0]["evidenceCards"][0]
    assert any(row.get("request", {}).get("sourceRef") == REF
               and "送样阶段" in row.get("value", {}).get("text", "")
               for row in model.calls[1]["contextResults"])
    assert outcome.mappings[0].company_code == "000001.SZ"


def test_b78_fulltext_material_uses_safe_local_read_before_second_direct_round(tmp_path):
    lead = _document("search-lead", text="公告全文\n关键条件：公司尚未签署订单，送样仍待客户确认。", excerpt="公司尚未签署订单")
    lead_ref = {"documentId": lead.document_id, "revision": lead.revision}
    first = {
        "action": RESEARCH_ROUND_ACTION,
        "claims": [], "questions": [_question(refs=[REF, lead_ref]).to_dict()], "queryPaths": [],
        "evidenceUpdates": [], "fulltextRequests": [FullTextRequest(
            "full-order", "q-order", lead_ref, "摘录不足以判断限定条件", "会改变订单判断", "requested",
        ).to_dict()],
        "conclusion": {"researchStatus": "continue_research", "stopReason": "需要完整公告限定条件",
                       "resumeCondition": "读取原始公告", "companyMappings": []},
        "comparison": None, "companyAssessments": [],
    }
    model = _RoundModel([first, _complete_round(lead_ref)])
    verifier = _RoundVerifier(fulltext_document=lead)
    outcome = _runtime(tmp_path, model=model, verifier=verifier,
                       extra_documents=(lead,), allowed_extra=(lead.evidence_ref,))._run_b78()

    assert len(verifier.fulltext_requests) == 1
    assert len(model.calls) == 2
    assert any(row.get("request", {}).get("sourceRef") == lead_ref
               and "关键条件" in row.get("value", {}).get("text", "")
               for row in model.calls[1]["contextResults"])
    assert any(row["documentId"] == lead.document_id for row in model.calls[1]["fullTextDocuments"])
    assert outcome.mappings[0].relation_evidence == (lead.evidence_ref,)


def _durable_runtime(tmp_path, *, model, verifier):
    """Build the direct-round snapshot boundary without an old stage record."""
    database = tmp_path / "durable-round.sqlite"
    initialize_schema(database)
    snapshot = _snapshot()
    store.set_run_control(state="open", reason_code="fixture", changed_at=snapshot.created_at,
                          changed_by="round-runtime-test", db_path=database)
    task = store.enqueue_task(task_id=snapshot.task_id, kind="evening_scan", idempotency_key="round-runtime",
        input_version="round-input", input_cutoff_at=snapshot.news_cutoff_at,
        payload={"runtimeContract": runtime_contract()}, budget={"maxAttempts": 1},
        created_at=snapshot.created_at, db_path=database)
    snapshot = replace(snapshot, task_id=task.task_id)
    store.append_event_revision(event_id=snapshot.event_id, stable_key="round-runtime-event",
        headline="来源陈述", event_kind="industry_change", facts={}, source_refs=[],
        supersedes_revision=None, created_at=snapshot.created_at, db_path=database)
    create_research_snapshot(snapshot=snapshot, db_path=database)
    runtime = _runtime(tmp_path, model=model, verifier=verifier)
    runtime.db_path, runtime.identity = database, snapshot.snapshot_id
    runtime.state = {"snapshot": snapshot, "questions": []}
    runtime.b78_research = True
    return runtime, database


def test_b78_resume_rebuilds_next_packet_after_durable_round_without_reposting_prior_round(tmp_path):
    first = {
        "action": RESEARCH_ROUND_ACTION,
        "contextRequests": [{
            "kind": "source", "purpose": "核对送样是否已转订单", "sourceRef": REF,
            "location": "excerpt",
        }],
    }
    model = _RoundModel([first, _complete_round()])
    runtime, database = _durable_runtime(tmp_path, model=model, verifier=_RoundVerifier())
    original_packet = runtime._b78_packet
    packet_calls = 0

    def interrupt_after_first_append(**kwargs):
        nonlocal packet_calls
        packet_calls += 1
        if packet_calls == 2:
            raise RuntimeError("fixture interruption after direct-round append")
        return original_packet(**kwargs)

    runtime._b78_packet = interrupt_after_first_append
    with pytest.raises(RuntimeError, match="after direct-round append"):
        runtime._run_b78()
    state = load_research_round_state(snapshot_id="round-snapshot", db_path=database)
    assert state["snapshot"].revision == 2 and len(state["rounds"]) == 1
    assert len(model.calls) == 1

    resumed, _ = _durable_runtime(tmp_path, model=model, verifier=_RoundVerifier())
    outcome = resumed._run_b78()
    state = load_research_round_state(snapshot_id="round-snapshot", db_path=database)

    assert len(model.calls) == 2  # the first receipt was rebuilt locally, not reposted
    assert any(row.get("request", {}).get("sourceRef") == REF
               for row in model.calls[1]["contextResults"])
    assert outcome.mappings[0].company_code == "000001.SZ"
    assert state["snapshot"].research_status == "ready_for_comparison"
    assert len(state["rounds"]) == 2
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_research_stage_results").fetchone()[0] == 0


@pytest.mark.parametrize('frozen_before_peer', [False, True])
def test_b78_committed_peer_round_refreshes_same_task_shared_source_facts(tmp_path, frozen_before_peer):
    """A later same-task event sees accepted peer facts, never peer mappings or raw bodies."""
    database = tmp_path / "shared-direct-round.sqlite"
    initialize_schema(database)
    snapshot = _snapshot()
    store.set_run_control(state="open", reason_code="fixture", changed_at=snapshot.created_at,
                          changed_by="round-runtime-test", db_path=database)
    task = store.enqueue_task(task_id=snapshot.task_id, kind="evening_scan", idempotency_key="shared-direct-round",
        input_version="round-input", input_cutoff_at=snapshot.news_cutoff_at,
        payload={"runtimeContract": runtime_contract()}, budget={"maxAttempts": 1},
        created_at=snapshot.created_at, db_path=database)
    source = store.append_document_version(
        document_id="shared-source", source_key="fixture", external_id="shared-source",
        canonical_url="https://example.test/shared", content_sha256="f" * 64,
        published_at="2026-09-20T00:00:00+00:00", published_precision="exact",
        fetched_at="2026-09-20T00:10:00+00:00", original_text="同源公告正文，不进入共享事实包。",
        excerpt="供应商确认仍在送样", fetch_version="fixture", metadata={"ignored": "metadata"},
        created_at=snapshot.created_at, db_path=database,
    )
    source_ref = {"documentId": source.document_id, "revision": source.revision}
    peer_event = store.append_event_revision(
        event_id="peer-event", stable_key="peer-event", headline="同源事件 A", event_kind="industry_change",
        facts={}, source_refs=[source_ref], supersedes_revision=None, created_at=snapshot.created_at, db_path=database,
    )
    receiving_event = store.append_event_revision(
        event_id="receiving-event", stable_key="receiving-event", headline="同源事件 B", event_kind="industry_change",
        facts={}, source_refs=[source_ref], supersedes_revision=None, created_at=snapshot.created_at, db_path=database,
    )
    peer_snapshot = replace(snapshot, snapshot_id="peer-round", task_id=task.task_id,
                            event_id=peer_event.event_id, event_revision=peer_event.revision)
    receiving_snapshot = replace(snapshot, snapshot_id="receiving-round", task_id=task.task_id,
                                 event_id=receiving_event.event_id, event_revision=receiving_event.revision)
    create_research_snapshot(snapshot=peer_snapshot, db_path=database)
    create_research_snapshot(snapshot=receiving_snapshot, db_path=database)
    from datetime import datetime, timedelta
    from neckline.k10.research_store import freeze_research_input_boundary
    original_time = datetime.fromisoformat(receiving_snapshot.updated_at)
    if frozen_before_peer:
        freeze_research_input_boundary(snapshot=receiving_snapshot, as_of=receiving_snapshot.updated_at,
                                       db_path=database)
    shared_claim = {**_packet()["claims"][0], "claimId": "peer-source-fact", "text": "供应商确认仍在送样",
                    "sourceRef": source_ref, "location": "excerpt"}
    peer_result = _complete_round(source_ref)
    peer_result["claims"] = [shared_claim]
    append_research_round(snapshot_id=peer_snapshot.snapshot_id, expected_revision=1,
        input_packet={**_packet(), "claims": [shared_claim], "allowedEvidenceRefs": [source_ref],
                      "evidenceCards": [{**source_ref, "excerpt": "供应商确认仍在送样"}]},
        result=ResearchRoundResult.from_dict(peer_result), research_status="ready_for_comparison",
        # A peer can commit later in the same clock second. The durable row
        # watermark, not a wall-clock comparison alone, freezes visibility.
        updated_at=original_time.isoformat(), db_path=database)

    document = _document("shared-source", text="同源公告正文，不进入共享事实包。", excerpt="供应商确认仍在送样")
    current_claim = Claim.from_dict({**_packet()["claims"][0], "claimId": "receiving-claim",
                                     "sourceRef": source_ref, "location": "excerpt"})
    runtime = object.__new__(_Investigation)
    runtime.model, runtime.verifier = _RoundModel([]), _RoundVerifier()
    runtime.task_id, runtime.identity, runtime.db_path = task.task_id, receiving_snapshot.snapshot_id, database
    runtime.event = EventDraft("receiving-event", "stage-round", "new", "同源事件 B", "company_event",
                               {"researchClaims": [current_claim.to_dict()]}, (document.evidence_ref,))
    runtime.state = {"snapshot": receiving_snapshot, "questions": []}
    runtime.documents, runtime.allowed = {document.evidence_ref: document}, {document.evidence_ref}
    runtime.context = {"canonicalKey": runtime.event.canonical_key, "stageKey": runtime.event.stage_key,
                       "eventState": runtime.event.event_state, "headline": runtime.event.headline,
                       "eventKind": runtime.event.event_kind}
    runtime._company_scope = lambda: {}
    if frozen_before_peer:
        runtime.b78_research = True
        runtime.guard = None
        runtime.clock = lambda: original_time + timedelta(seconds=2)

    packet = runtime._b78_packet(claims=(current_claim,))
    shared = packet["reusableSourceEvidence"]

    if frozen_before_peer:
        assert shared['claims'] == []
        runtime.clock = lambda: original_time + timedelta(seconds=20)
        assert runtime._b78_packet(claims=(current_claim,)) == packet
        return

    assert [row["text"] for row in shared["claims"]] == ["供应商确认仍在送样"]
    assert shared["claims"][0]["provenance"]["snapshotId"] == peer_snapshot.snapshot_id
    assert shared["claims"][0]["sourceTiming"]["fetchedAt"] == "2026-09-20T00:10:00+00:00"
    assert shared["companyRelations"] == []
    assert not ({"originalText", "metadata", "rawResponse"} & set(shared["claims"][0]))
