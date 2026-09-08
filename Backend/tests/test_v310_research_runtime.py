"""Resource and provenance invariants at the real investigation coordinator."""
from dataclasses import replace

import pytest

from neckline.k10.discovery import DiscoveryDocument, EvidenceRef
from neckline.k10.investigation import InvestigationError
from neckline.k10.research_contracts import FullTextRequest, ResearchStageResult
from neckline.k10.research_runtime import _Investigation
from tests.test_v310_investigation import _claim


REF = {"documentId": "source-1", "revision": 1}


def _runtime():
    runtime = object.__new__(_Investigation)
    runtime.state = {"claims": [_claim().to_dict()], "questions": [], "paths": [],
                     "fulltextRequests": [], "stageResults": [], "evidenceUpdates": []}
    return runtime


def test_verified_claim_requires_applicable_support_not_just_a_status_change():
    runtime = _runtime()
    verified = replace(_claim(), verification_status="verified")
    packet = {"allowedEvidenceRefs": [REF]}
    with pytest.raises(InvestigationError) as caught:
        runtime._validate_result("assess_evidence", ResearchStageResult("assess_evidence", claims=(verified,)), packet)
    assert caught.value.code == "investigation_support_missing"
    # One correctly scoped original source is enough; no extra Tavily vote or
    # second source is required merely to satisfy an arbitrary source count.
    runtime._validate_result("assess_evidence", ResearchStageResult("assess_evidence", claims=(verified,),
        evidence_updates=({"claimId": verified.claim_id, "sourceRef": REF, "relation": "supports",
                           "location": "paragraph:2", "applicability": {"scope": "项目送样"}},)), packet)


def test_new_request_id_cannot_repeat_a_processed_fulltext_for_the_same_question():
    runtime = _runtime()
    runtime.state["questions"] = [{"questionId": "q-1"}]
    old = FullTextRequest("read-1", "q-1", REF, "缺阶段条件", "资格与订单区别", "fulfilled", REF)
    runtime.state["fulltextRequests"] = [old.to_dict()]
    with pytest.raises(InvestigationError) as caught:
        runtime._validate_result("assess_evidence", ResearchStageResult("assess_evidence", fulltext_requests=(
            replace(old, request_id="read-2", state="requested", admission_ref=None),)),
            {"allowedEvidenceRefs": [REF]})
    assert caught.value.code == "investigation_fulltext_duplicate"


def _tool_stage(revision):
    return {"revision": revision, "action": "assess_evidence", "result": {
        "conclusion": {"runtimeEvidence": {"eligibleDocumentRefs": [REF], "documentRefs": [REF],
            "coverage": {"operation": "extract", "admissionState": "fulfilled"}}}}}


def test_shared_fulltext_is_sent_to_the_model_once_and_only_after_admission():
    runtime = _runtime()
    runtime.documents = {EvidenceRef("source-1", 1): DiscoveryDocument("source-1", 1, None,
        "2026-09-08T13:00:00+00:00", "the admitted article", None, {})}
    runtime.state["stageResults"] = [_tool_stage(2)]
    calls = []
    def call(action, packet):
        calls.append((action, packet))
        runtime.state["stageResults"].append({"revision": 3, "action": "assess_evidence", "result": {}})
    runtime._call = call
    assert runtime._assess_due()
    assert calls[0][1]["fullTextDocuments"][0]["text"] == "the admitted article"
    assert calls[0][1]["admittedFulltextRefs"] == [REF]
    runtime.state["stageResults"].append(_tool_stage(4))
    assert runtime._assess_due()
    assert calls[1][1]["fullTextDocuments"] == []


def test_close_requesting_an_extra_article_must_assess_it_before_accepting_ready():
    runtime = _runtime()
    calls = []
    def close(action):
        calls.append(action)
        return ResearchStageResult(action, conclusion={"researchStatus": "ready_for_comparison"})
    def fulltexts():
        calls.append("admit_extract_assess")
        return len(calls) == 2
    runtime._call, runtime._fulltexts = close, fulltexts
    runtime._close()
    assert calls == ["close_research", "admit_extract_assess", "close_research", "admit_extract_assess"]


def test_exhausted_paths_keep_known_companies_for_pending_assessments():
    runtime = _runtime()
    mappings = [{"companyCode": "300001.SZ", "relationEvidence": [REF]}]
    runtime.state["stageResults"] = [{"result": {"conclusion": {"companyMappings": mappings}}}]
    runtime._record = lambda *args, **kwargs: None
    result = runtime._pending("已经没有可改变判断的新路径")
    assert result["companyMappings"] == mappings
    assert result["researchStatus"] == "pending_verification"
    assert result["resumeCondition"]
