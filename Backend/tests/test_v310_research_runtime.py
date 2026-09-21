"""B78 direct-round provenance and pending-disposition regressions."""
from dataclasses import replace

import pytest

from neckline.k10.discovery import DiscoveryDocument
from neckline.k10.investigation import InvestigationError
from neckline.k10.research_context import read_context
from neckline.k10.research_contracts import FullTextRequest, Question, ResearchRoundResult
from neckline.k10.research_runtime import (
    _Investigation, build_research_round_packet, validate_research_round_result,
)
from tests.test_v310_investigation import _claim


REF = {"documentId": "source-1", "revision": 1}


def _conclusion(*, mappings=None, status="pending_verification"):
    return {
        "researchStatus": status,
        "companyMappings": [] if mappings is None else mappings,
        "stopReason": "资料尚不足以正式比较",
        "resumeCondition": "出现直接公告或反证资料",
    }


def _question() -> Question:
    return Question(
        "q-1", (_claim().claim_id,), ("000001.SZ",), "送样是否已经转为订单",
        (REF,), ("订单或反证资料",), "披露订单", "明确未签约",
        "会改变公司关联强度", "open", "出现直接资料后复核",
    )


def test_direct_round_new_verified_claim_requires_visible_applicable_support():
    """A fresh claim cannot prove itself just by claiming `verified`."""
    verified = replace(_claim(), claim_id="new-verified-claim", verification_status="verified")
    packet = build_research_round_packet({
        "allowedEvidenceRefs": [REF], "claims": [_claim().to_dict()],
        "evidenceCards": [{**REF, "excerpt": "供应商称项目进入送样"}],
    })
    unsupported = ResearchRoundResult(claims=(verified,), conclusion=_conclusion())

    with pytest.raises(InvestigationError) as caught:
        validate_research_round_result(result=unsupported, evidence_packet=packet)

    assert caught.value.code == "investigation_support_missing"
    supported = ResearchRoundResult(
        claims=(verified,),
        evidence_updates=({"claimId": verified.claim_id, "sourceRef": REF, "relation": "supports",
                           "location": "paragraph:2", "applicability": {"scope": "项目送样"}},),
        conclusion=_conclusion(),
    )
    # One correctly scoped visible source is sufficient; B78 does not invent
    # a second-source quota or force another paid search.
    validate_research_round_result(result=supported, evidence_packet=packet)


def test_direct_round_cannot_repeat_processed_fulltext_for_same_question():
    question = _question()
    fulfilled = FullTextRequest("read-1", question.question_id, REF, "缺阶段条件", "资格与订单区别",
                                "fulfilled", REF)
    packet = build_research_round_packet({
        "allowedEvidenceRefs": [REF], "claims": [_claim().to_dict()],
        "questions": [question.to_dict()], "fulltextRequests": [fulfilled.to_dict()],
        "evidenceCards": [{**REF, "excerpt": "供应商称项目进入送样"}],
    })
    repeated = ResearchRoundResult(
        fulltext_requests=(replace(fulfilled, request_id="read-2", state="requested", admission_ref=None),),
        conclusion=_conclusion(status="continue_research"),
    )

    with pytest.raises(InvestigationError) as caught:
        validate_research_round_result(result=repeated, evidence_packet=packet)

    assert caught.value.code == "investigation_fulltext_duplicate"


def test_direct_round_fulltext_card_stays_body_free_until_locator_read():
    """An admitted source is summarized once; only its bounded read exposes text."""
    document = DiscoveryDocument("source-1", 1, None, "2026-09-08T13:00:00+00:00",
                                 "the admitted article", "the admitted article", {})
    packet = build_research_round_packet({
        "allowedEvidenceRefs": [REF], "claims": [_claim().to_dict()],
        "fullTextDocuments": [{**REF, "excerpt": document.excerpt,
                                "locators": [{"locator": "paragraph:1"}]}],
    })

    card = packet["fullTextDocuments"][0]
    assert "originalText" not in card and "text" not in card
    assert card["locators"] == [{"locator": "paragraph:1"}]
    read = read_context(
        {"kind": "source", "sourceRef": REF, "location": "paragraph:1",
         "purpose": "Read the admitted wording"},
        state={"claims": [_claim().to_dict()], "questions": []},
        documents={document.evidence_ref: document}, binding=None,
        eligible_refs={(document.document_id, document.revision)}, visible_packet=packet,
    )
    assert read["value"]["text"] == "the admitted article"


def test_exhausted_direct_followup_keeps_mapping_as_pending_without_assessment():
    """A visible mapping survives as a disclosed gap, never as a ranked card."""
    mappings = [{"companyCode": "000001.SZ", "relationEvidence": [REF], "affectedStage": "送样",
                 "inference": {}, "uncertainty": "待核"}]
    in_progress = ResearchRoundResult(
        conclusion=_conclusion(mappings=mappings, status="continue_research"),
        company_assessments=(), comparison=None,
    )

    pending = _Investigation._b78_terminal_pending_result(in_progress, "没有形成新的可见资料")

    assert pending.conclusion["companyMappings"] == mappings
    assert pending.conclusion["researchStatus"] == "pending_verification"
    assert pending.conclusion["resumeCondition"]
    assert pending.company_assessments == () and pending.comparison is None
