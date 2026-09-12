import copy
from datetime import timedelta

from neckline.k10 import pipeline, store
from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
from neckline.k10.investigation import InvestigationError, decode_stage_result
from neckline.k10.research_runtime import _Investigation
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b60_pool_filtering import edit_responses
from tests.test_v310_investigation import _claim
from tests.test_v310_research_runtime import _runtime, REF

UNKNOWN = {"documentId": "unadmitted-version", "revision": 2}


def question():
    return {"questionId": "q1", "claimIds": ["claim-1"], "companyCodes": ["300001.SZ"],
            "question": "是否确认", "knownEvidence": [REF], "missingEvidence": ["主体确认"],
            "supportCondition": "原始确认", "refuteCondition": "原始否认", "decisionImpact": "影响判断",
            "state": "open", "resumeCondition": "取得原始资料"}


def decode(raw, *, questions=()):
    runtime = _runtime()
    runtime.state["questions"] = list(questions)
    packet = {"allowedEvidenceRefs": [REF], "claims": runtime.state["claims"],
              "questions": list(questions), "evidenceUpdates": [], "fulltextRequests": []}
    result = decode_stage_result(raw, action="assess_evidence", evidence_packet=packet)
    runtime._validate_result("assess_evidence", result, packet)
    return result


def test_mixed_question_references_keep_valid_evidence_and_reopen_unsupported_answer():
    original = question()
    raw = {"action": "assess_evidence", "questions": [{"questionId": "q1", "state": "answered",
            "knownEvidence": [REF, UNKNOWN], "missingEvidence": []}]}
    before = copy.deepcopy(raw)
    result = decode(raw, questions=[original])
    assert raw == before
    assert list(result.questions[0].known_evidence) == [REF]
    assert result.questions[0].state == "open" and result.questions[0].missing_evidence
    assert result.conclusion["runtimeOutputSanitization"]["discardedReferences"] == 1


def test_unadmitted_support_cannot_verify_a_claim_and_does_not_abort_other_results():
    raw = {"action": "assess_evidence", "claims": [{"claimId": "claim-1", "verificationStatus": "verified"}],
           "evidenceUpdates": [{"claimId": "claim-1", "sourceRef": UNKNOWN, "relation": "supports",
                                "location": "excerpt", "applicability": {}}]}
    result = decode(raw)
    assert not result.evidence_updates
    assert result.claims[0].verification_status == "unverified"
    assert result.claims[0].source_ref == REF


def test_unknown_claims_and_changed_assertions_do_not_replace_original_facts():
    raw = {"action": "assess_evidence", "claims": [
        {**_claim().to_dict(), "text": "改写成已签订大额合同", "verificationStatus": "verified"},
        {**_claim().to_dict(), "claimId": "outside", "sourceRef": UNKNOWN}],
        "evidenceUpdates": [{"claimId": "outside", "sourceRef": REF, "relation": "supports",
                             "location": "excerpt", "applicability": {}}]}
    result = decode(raw)
    assert not result.claims and not result.evidence_updates
    assert result.conclusion["runtimeOutputSanitization"]["discardedClaims"] == 2


def test_valid_support_survives_an_unrelated_bad_link():
    good = {"claimId": "claim-1", "sourceRef": REF, "relation": "supports", "location": "excerpt", "applicability": {}}
    result = decode({"action": "assess_evidence", "claims": [{"claimId": "claim-1", "verificationStatus": "verified"}],
                     "evidenceUpdates": [good, {**good, "sourceRef": UNKNOWN}]})
    assert list(result.evidence_updates) == [good] and result.claims[0].verification_status == "verified"


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
    original = _Investigation._validate_result
    def old_boundary(self, action, result, packet):
        if action == "assess_evidence" and result.evidence_updates:
            raise InvestigationError("证据关联越出当前命题或来源", code="investigation_reference_invalid")
        return original(self, action, result, packet)
    def bad_reference(value):
        if value.get("action") == "assess_evidence":
            value["evidenceUpdates"] = [{"claimId": "article-claim-1", "sourceRef": UNKNOWN,
                                          "relation": "supports", "location": "excerpt", "applicability": {}}]
    # The regression needs an actual returned source; an empty search no
    # longer creates a paid assessment in the v2 context protocol.
    def fetch(self, **kwargs):
        from neckline.k10.discovery import DiscoveryDocument
        from neckline.k10.verification import VerificationEvidenceBundle
        self.search_paths.append(kwargs['query_path'].path_id)
        ref = kwargs['event'].source_refs[0]
        db = tmp_path/'b39-e2e.sqlite'
        row = store.load_document_versions(refs=[{'documentId':ref.document_id,'revision':ref.revision}],db_path=db)[0]
        identifier = 'b64-evidence-' + str(len(self.search_paths))
        text = '独立核验材料 ' + str(len(self.search_paths)) + '，仍未确认项目订单'
        store.append_document_version(document_id=identifier,source_key='fixture',external_id=identifier,canonical_url=None,
            content_sha256=__import__('hashlib').sha256(text.encode()).hexdigest(),published_at=row['publishedAt'],published_precision='exact',
            fetched_at=row['fetchedAt'],original_text=None,excerpt=text,fetch_version='fixture',metadata={},created_at=e2e.RUN_AT.isoformat(),db_path=db)
        doc=DiscoveryDocument(identifier,1,row['publishedAt'],row['fetchedAt'],None,text,{})
        return VerificationEvidenceBundle('available',(doc,),(doc,),{'state':'available','requestState':'completed'})
    monkeypatch.setattr(e2e._Gateway,'fetch',fetch)
    edit_responses(monkeypatch, bad_reference)
    # Reproduce the actual historical decoder/boundary rather than changing
    # producer state by hand. Later recovery must use the real returned task.
    monkeypatch.setattr(_Investigation, "_validate_result", old_boundary)
    monkeypatch.setattr(pipeline, "decode_stage_result", lambda value, **kw: __import__(
        "neckline.k10.investigation", fromlist=["decode_stage_result"]).decode_stage_result(value, action=kw["action"]))
    db, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    assert task.status == "failed" and calls.count("research:assess_evidence") == 2
    monkeypatch.setattr(_Investigation, "_validate_result", original)
    monkeypatch.setattr(pipeline, "decode_stage_result", decode_stage_result)
    scan = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    assert recover_scan(db_path=db, scan_id=scan, execution_config_id="b39-execution", execution_config_revision=1,
                        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan, db_path=db), now=e2e.RUN_AT) == task_id
    resumed = e2e._http_transport(monkeypatch, v2=True, initial_query_round=1)
    done = run_once(db_path=db, task_id=task_id, worker_id="b64", lease_for=timedelta(minutes=5),
                    handlers=pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path/"parquet"),
                    clock=lambda: e2e.RUN_AT)
    assert done.status == "completed" and "titleBatch" not in resumed and "understand" not in resumed
    # The saved failed assessment is consumed locally; only the genuinely new
    # next search batch needs one subsequent assessment.
    assert resumed.count("research:assess_evidence") == 1
    report = read_report(db_path=db)
    assert report["eveningCards"] and not report["incompleteReviews"]
