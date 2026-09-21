"""B78 company identity and relation-evidence validation."""
from __future__ import annotations

import pytest

from neckline.k10.research_contracts import ResearchContractError, ResearchRoundResult, canonical_company_code


@pytest.mark.parametrize("code,expected", [
    ("002361", "002361.SZ"), ("300001", "300001.SZ"), ("600001", "600001.SH"),
    ("688001", "688001.SH"), ("300001.SH", "300001.SH"),
])
def test_equity_code_spelling_never_changes_an_explicit_exchange(code, expected):
    assert canonical_company_code(code) == expected


@pytest.mark.parametrize("code", ["NVDA", "30001", "3012345", "800001", None])
def test_unknown_or_ambiguous_code_is_not_guessed(code):
    with pytest.raises(ResearchContractError):
        canonical_company_code(code)


def test_direct_round_rejects_company_relation_without_visible_evidence():
    raw = {
        "action": "research_round", "claims": [], "questions": [], "queryPaths": [],
        "evidenceUpdates": [], "fulltextRequests": [],
        "conclusion": {"researchStatus": "ready_for_comparison", "stopReason": "资料足够",
                       "resumeCondition": None, "companyMappings": [{
                           "companyCode": "300001", "affectedStage": "送样", "inference": {},
                           "uncertainty": "待核", "relationEvidence": [],
                       }]},
        "comparison": {"summary": "关系待核", "evidenceRefs": []}, "companyAssessments": [],
    }

    with pytest.raises(ResearchContractError) as caught:
        ResearchRoundResult.from_dict(raw)

    assert caught.value.field_name == "companyMappings[].relationEvidence"
