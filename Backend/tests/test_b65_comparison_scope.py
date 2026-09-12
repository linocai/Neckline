import copy
from datetime import timedelta

import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
from neckline.k10.investigation import InvestigationError, decode_stage_result, validate_stage_result
from neckline.k10.research_contracts import EvidenceDisclosure
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
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
    def add_duplicate(value):
        if value.get("action") == "compare_companies":
            value["companyAssessments"].append(copy.deepcopy(value["companyAssessments"][0]))
    edit_responses(monkeypatch, add_duplicate)
    # Reproduce the prior boundary through the actual producer and worker.
    # Omitting the packet disables only the new request-scoped normalization.
    monkeypatch.setattr(pipeline, "decode_stage_result", lambda value, **kw:
        decode_stage_result(value, action=kw["action"], **({} if kw["action"] == "compare_companies" else {"evidence_packet":kw.get("evidence_packet")})))
    db, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    assert task.status == "failed" and calls.count("research:compare_companies") == 2
    monkeypatch.setattr(pipeline, "decode_stage_result", decode_stage_result)
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    assert recover_scan(db_path=db, scan_id=scan_id, execution_config_id="b39-execution", execution_config_revision=1,
        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan_id, db_path=db), now=e2e.RUN_AT) == task_id
    resumed = e2e._http_transport(monkeypatch, v2=True)
    done = run_once(db_path=db, task_id=task_id, worker_id="b65", lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path/"parquet"),
        clock=lambda: e2e.RUN_AT)
    assert done.status == "completed"
    assert "research:compare_companies" not in resumed and "titleBatch" not in resumed and "understand" not in resumed
    report = read_report(db_path=db)
    assert report["eveningCards"] and not report["incompleteReviews"]
