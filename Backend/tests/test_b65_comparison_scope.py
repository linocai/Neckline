import copy
import json
import sqlite3
from hashlib import sha256

import pytest

from neckline.k10 import research_runtime, store
from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
from neckline.k10.investigation import InvestigationError, decode_stage_result, validate_stage_result
from neckline.k10.research_contracts import EvidenceDisclosure
from neckline.k10.research_store import read_research_state
from neckline.k10.v2_store import read_report
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b60_pool_filtering import edit_responses


def assessment(code, *, role="primary", rank=1):
    return {"companyCode": code, "role": role, "rank": rank, "summary": "条件化推断",
            "priorityReason": "事件标的", "gap": "原始资料待核", "rankChangeConditions": "主体否认则撤回",
            "twoDayReason": "新增信息可能引起关注", "evidenceDisclosure":
            EvidenceDisclosure("unverified", True, "unknown", None, ("来源待核",), "原始资料确认后复评").to_dict()}


def compare(rows, codes):
    raw = {"action": "compare_companies", "companyAssessments": rows,
           "conclusion": {"summary": "共同事实待核", "evidenceRefs": []}}
    before = copy.deepcopy(raw)
    packet = {"allowedEvidenceRefs": [], "companyCodes": codes, "publicationAllowed": True}
    result = decode_stage_result(raw, action="compare_companies", evidence_packet=packet)
    validate_stage_result(action="compare_companies", result=result, evidence_packet=packet)
    assert raw == before
    return result


def test_actual_comparison_topology_discards_five_extra_excluded_companies():
    primary = assessment("300357.SZ")
    extra = [assessment(code, role="excluded", rank=None) for code in
             ("300169.SZ", "300556.SZ", "300569.SZ", "300929.SZ", "301042.SZ")]
    result = compare([primary, *extra], ["300357.SZ"])
    assert list(result.company_assessments) == [primary]
    assert result.conclusion["runtimeOutputSanitization"]["discardedCompanyAssessments"] == 5


def test_duplicate_comparison_keeps_original_judgment():
    primary = assessment("300357.SZ")
    result = compare([primary, assessment("300357.SZ", role="excluded", rank=None)], ["300357.SZ"])
    assert list(result.company_assessments) == [primary]


def test_missing_input_company_is_never_filled_with_an_invented_assessment():
    with pytest.raises(InvestigationError, match="遗漏输入公司"):
        compare([assessment("300357.SZ")], ["300357.SZ", "300169.SZ"])


def test_paid_comparison_recovery_keeps_real_task_and_does_not_rebill(tmp_path, monkeypatch):
    """The B78 direct receipt is rebuilt locally before the next round.

    Its one complete response contains mappings and comparison together, so a
    restart must not recreate the former standalone comparison POST.
    """
    from tests.test_v350_research_round import test_b78_resume_rebuilds_next_packet_after_durable_round_without_reposting_prior_round

    test_b78_resume_rebuilds_next_packet_after_durable_round_without_reposting_prior_round(tmp_path)
