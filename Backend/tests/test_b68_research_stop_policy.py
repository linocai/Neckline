import json

from neckline.k10.research_store import list_research_assessments
from neckline.k10.v2_store import read_report
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for


def test_real_worker_receives_current_stop_rule_before_comparison_and_publishes_unverified(tmp_path, monkeypatch):
    from tests.test_v350_research_round import (
        _DirectModel, _complete_round, _packet, _snapshot,
    )
    from neckline.k10.research_runtime import build_research_round_packet, research_round_request_spec, run_research_round

    packet = build_research_round_packet(_packet())
    instruction, request = research_round_request_spec(snapshot=_snapshot(), evidence_packet=packet)
    result = run_research_round(_DirectModel(_complete_round()), snapshot=_snapshot(), evidence_packet=packet)
    assert request['action'] == 'research_round'
    assert '完整的事件研究轮次' in instruction
    assert result.company_assessments[0]['evidenceDisclosure']['verificationStatus'] == 'unverified'
    assert result.company_assessments[0]['gap']
