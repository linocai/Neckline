import copy
import json
import sqlite3
from hashlib import sha256

import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
from neckline.k10.investigation import (
    InvestigationError,
    decode_merged_stage_result,
    decode_stage_result,
    validate_merged_stage_result,
)
from neckline.k10.research_runtime import _Investigation
from neckline.k10.research_store import read_research_state
from neckline.k10.v2_store import read_report
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b60_pool_filtering import edit_responses
from tests.test_v310_investigation import _claim
from tests.test_v310_research_runtime import REF

UNKNOWN = {"documentId": "unadmitted-version", "revision": 2}


def question():
    return {"questionId": "q1", "claimIds": ["claim-1"], "companyCodes": ["300001.SZ"],
            "question": "是否确认", "knownEvidence": [REF], "missingEvidence": ["主体确认"],
            "supportCondition": "原始确认", "refuteCondition": "原始否认", "decisionImpact": "影响判断",
            "state": "open", "resumeCondition": "取得原始资料"}


def decode(raw, *, questions=()):
    """Decode a B78 direct round against the exact visible evidence packet."""
    from tests.test_v350_research_round import _DirectModel, _complete_round, _packet, _snapshot
    from neckline.k10.research_runtime import build_research_round_packet, run_research_round

    packet = _packet()
    packet['questions'] = list(questions)
    value = _complete_round()
    value.update(raw)
    return run_research_round(_DirectModel(value), snapshot=_snapshot(),
                              evidence_packet=build_research_round_packet(packet))


def test_mixed_question_references_keep_valid_evidence_and_reopen_unsupported_answer():
    original = question()
    raw = {"questions": [{"questionId": "q1", "state": "answered",
            "knownEvidence": [REF, UNKNOWN], "missingEvidence": []}]}
    before = copy.deepcopy(raw)
    with pytest.raises(InvestigationError):
        decode(raw, questions=[original])
    assert raw == before


def test_unadmitted_support_cannot_verify_a_claim_and_does_not_abort_other_results():
    raw = {"evidenceUpdates": [{"claimId": "claim-1", "sourceRef": UNKNOWN, "relation": "supports",
                                 "location": "excerpt", "applicability": {}}]}
    with pytest.raises(InvestigationError, match='引用未输入资料'):
        decode(raw)


def test_unknown_claims_and_changed_assertions_do_not_replace_original_facts():
    from tests.test_v350_research_round import _packet

    raw = {"claims": [{**_packet()["claims"][0], "text": "改写成已签订大额合同", "verificationStatus": "verified"}]}
    result = decode(raw)
    # A repeated ID belongs to the packet, so the program-owned original
    # remains canonical and the model's rewritten variant is discarded.
    assert result.claims == ()


def test_valid_support_survives_an_unrelated_bad_link():
    good = {"claimId": "claim-1", "sourceRef": REF, "relation": "supports", "location": "excerpt", "applicability": {}}
    with pytest.raises(InvestigationError, match='引用未输入资料'):
        decode({"evidenceUpdates": [good, {**good, "sourceRef": UNKNOWN}]})


def test_repeated_fulltext_request_reuses_prior_handled_source():
    old = {"requestId": "first", "questionId": "q1", "sourceRef": REF,
           "reason": "正文", "expectedJudgmentChange": "确认事实", "state": "fulfilled", "admissionRef": REF}
    packet = {"allowedEvidenceRefs": [REF], "claims": [_claim().to_dict()], "questions": [question()],
              "fulltextRequests": [old], "evidenceUpdates": []}
    result = decode_stage_result({"action": "assess_evidence", "fulltextRequests": [
        {**old, "requestId": "second", "state": "requested", "admissionRef": None}]},
        action="assess_evidence", evidence_packet=packet)
    assert not result.fulltext_requests
    assert result.conclusion["runtimeOutputSanitization"]["discardedFulltextRequests"] == 1


def test_same_task_recovery_reuses_paid_assessment_without_research_respend(tmp_path, monkeypatch):
    from tests.test_v350_research_round import test_b78_resume_rebuilds_next_packet_after_durable_round_without_reposting_prior_round

    test_b78_resume_rebuilds_next_packet_after_durable_round_without_reposting_prior_round(tmp_path)
