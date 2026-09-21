"""B78 direct-result envelope repair and question disclosure regressions."""
from __future__ import annotations

import pytest

from neckline.k10.investigation import InvestigationError
from neckline.k10.research_contracts import Question, ResearchContractError, ResearchRoundResult
from tests.test_v310_pipeline_e2e import _run


def test_real_worker_repairs_one_invalid_direct_round_envelope(tmp_path, monkeypatch):
    from tests.test_b60_pool_filtering import edit_responses
    replies = 0
    def corrupt_first_round(value):
        nonlocal replies
        if value.get("action") == "research_round":
            replies += 1
            if replies == 1:
                value["claims"] = "wrong-shape"
    edit_responses(monkeypatch, corrupt_first_round)
    _, _, task, calls, gateway = _run(tmp_path, monkeypatch)

    assert task.status == "completed"
    assert calls.count("research:research_round") == 2
    assert replies == 2
    assert gateway.search_paths == []
    assert calls.count("understand") == 1


@pytest.mark.parametrize("payload", [
    {"action": "plan_queries", "questions": []},
    {"action": "research_round", "claims": "wrong-shape"},
])
def test_direct_round_envelope_never_projects_or_invents_legacy_fields(payload):
    with pytest.raises((InvestigationError, ResearchContractError)):
        ResearchRoundResult.from_dict(payload)


@pytest.mark.parametrize("state", ["open", "answered"])
def test_resolved_question_can_clear_gap_but_open_question_cannot(state):
    payload = {"questionId": "q", "claimIds": ["c"], "companyCodes": [], "question": "original question",
               "knownEvidence": [], "missingEvidence": [], "supportCondition": "confirmation", "refuteCondition": "denial",
               "decisionImpact": "changes assessment", "state": state, "resumeCondition": None}
    if state == "open":
        with pytest.raises(ResearchContractError):
            Question.from_dict(payload)
    else:
        assert Question.from_dict(payload).missing_evidence == ()
