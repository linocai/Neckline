"""Direct-round review regressions through the real offline worker."""
import json
import sqlite3

import httpx
import pytest

from neckline.k10 import store
from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
from neckline.k10.worker import run_once
from neckline.k10.v2_store import read_report
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for


def _payload_of(request):
    text = json.loads(request.content)["messages"][-1]["content"]
    return json.loads(text.split("<untrusted-k10-evidence>\n", 1)[1].split("\n</untrusted-k10-evidence>", 1)[0])


def _reply(value):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(value)},
        "finish_reason": "stop"}], "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6}})


def _direct_query_round(payload, *, paths=(("path-1", "项目 送样 公告", "公司公告"),)):
    packet = payload["evidencePacket"]
    ref = packet["allowedEvidenceRefs"][0]
    claim_id = packet["claims"][0]["claimId"]
    return {
        "action": "research_round",
        "questions": [{
            "questionId": "q-1", "claimIds": [claim_id], "companyCodes": ["300001.SZ"],
            "question": "送样是否获公司确认", "knownEvidence": [ref], "missingEvidence": ["公司确认"],
            "supportCondition": "公司确认", "refuteCondition": "公司否认", "decisionImpact": "影响主推",
            "state": "open", "resumeCondition": "出现公司公告",
        }],
        "queryPaths": [{
            "pathId": path_id, "questionId": "q-1", "query": query, "intent": "确认送样",
            "targetSource": source, "newPathReason": "首批必要来源", "expectedInformationGain": "确认主体",
            "expectedJudgmentChange": "改变比较", "purposeKind": "event_fact",
            "targetRefs": [{"kind": "claim", "claimId": claim_id}], "state": "planned", "resultSummary": None,
        } for path_id, query, source in paths],
        "conclusion": {
            "researchStatus": "continue_research", "companyMappings": [], "materialGaps": ["公司确认"],
            "stopReason": "需要必要公开资料", "resumeCondition": "取得公司公告",
        },
        "companyAssessments": [],
    }


def _resume_failed_same_task(*, db, task_id, tmp_path):
    """Use the production recovery entry, preserving the CLI task binding."""
    execution = store.task_execution_input(task_id=task_id, db_path=db)
    scan = store.get_scan(scan_id=execution["checkpoint"]["scanId"], db_path=db)
    recovered = recover_scan(
        db_path=db, scan_id=scan["scanId"], execution_config_id="b39-execution", execution_config_revision=1,
        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan["scanId"], db_path=db), now=e2e.RUN_AT,
    )
    assert recovered == task_id
    task = run_once(
        db_path=db, worker_id="v321-direct-resume", lease_for=e2e.timedelta(minutes=5),
        handlers=e2e.pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path / "parquet"),
        clock=lambda: e2e.RUN_AT,
    )
    assert task is not None
    return task


def _pause_after_committed_round(monkeypatch):
    """Interrupt after receipt/checkpoint durability, before the direct result is consumed."""
    original, tripped = store.record_execution_checkpoint, {"value": False}

    def interrupt(**kwargs):
        result = original(**kwargs)
        if (kwargs.get("stage") == "model:investigation_research_round"
                and kwargs.get("status") == "completed" and not tripped["value"]):
            tripped["value"] = True
            raise sqlite3.OperationalError("fixture interruption after committed direct round")
        return result

    monkeypatch.setattr(store, "record_execution_checkpoint", interrupt)
    return original, tripped


@pytest.mark.parametrize("kind,pause", [("question", False), ("question", True), ("claim", False), ("claim", True)])
def test_current_question_or_claim_read_cannot_create_a_new_paid_direct_round(tmp_path, monkeypatch, kind, pause):
    """These values are already in a B78 packet; a reread cannot advance research."""
    transport, rounds = httpx.MockTransport, []
    original_checkpoint = None
    if pause:
        original_checkpoint, tripped = _pause_after_committed_round(monkeypatch)

    def wrap(handler):
        def respond(request):
            payload = _payload_of(request)
            if payload.get("action") == "research_round":
                rounds.append(payload)
                request_row = ({"kind": "question", "id": "q-1", "questionId": "q-1", "purpose": "读取当前缺口"}
                               if kind == "question" else
                               {"kind": "claim", "id": "article-claim-1", "purpose": "读取当前判断依据"})
                return _reply({"action": "research_round", "contextRequests": [request_row]})
            return handler(request)
        return transport(respond)

    monkeypatch.setattr(httpx, "MockTransport", wrap)
    db, task_id, task, _calls, _gateway = e2e._run(tmp_path, monkeypatch, v2=True, pending_ranking="legacy_wrong")
    if pause:
        assert tripped["value"] and task.status == "failed"
        monkeypatch.setattr(store, "record_execution_checkpoint", original_checkpoint)
        task = _resume_failed_same_task(db=db, task_id=task_id, tmp_path=tmp_path)
    assert task.status == "completed"
    assert len(rounds) == 1
    report = read_report(db_path=db)
    assert report["eveningCards"] == []


@pytest.mark.parametrize("pause", [False, True])
def test_empty_search_recovery_finishes_without_a_second_direct_round(tmp_path, monkeypatch, pause):
    """An interrupted empty lookup resumes its durable reply and ends as pending evidence."""
    transport, rounds = httpx.MockTransport, []
    original_checkpoint = None
    if pause:
        original_checkpoint, tripped = _pause_after_committed_round(monkeypatch)

    def wrap(handler):
        def respond(request):
            payload = _payload_of(request)
            if payload.get("action") == "research_round":
                rounds.append(payload)
                return _reply(_direct_query_round(payload))
            return handler(request)
        return transport(respond)

    monkeypatch.setattr(httpx, "MockTransport", wrap)
    db, task_id, task, _calls, gateway = e2e._run(tmp_path, monkeypatch, v2=True, pending_ranking="legacy_wrong")
    if pause:
        assert tripped["value"] and task.status == "failed"
        monkeypatch.setattr(store, "record_execution_checkpoint", original_checkpoint)
        task = _resume_failed_same_task(db=db, task_id=task_id, tmp_path=tmp_path)
    assert task.status == "completed"
    e2e.assert_search_routes(gateway, [('项目 送样 公告','公司公告','q-1')])
    assert len(rounds) == 1
    assert read_report(db_path=db)["eveningCards"] == []


@pytest.mark.parametrize("pause", [False, True])
def test_admitted_distinct_source_batch_completes_once_each_after_recovery(tmp_path, monkeypatch, pause):
    """Different necessary sources survive dedupe and retain their durable batch on resume."""
    transport, rounds = httpx.MockTransport, []
    original_checkpoint = None
    if pause:
        original_checkpoint, tripped = _pause_after_committed_round(monkeypatch)

    def wrap(handler):
        def respond(request):
            payload = _payload_of(request)
            if payload.get("action") == "research_round":
                rounds.append(payload)
                return _reply(_direct_query_round(payload, paths=(
                    ("announcement", "项目 送样 公告", "公司公告"),
                    ("industry", "项目 送样 行业核实", "行业媒体"),
                )))
            return handler(request)
        return transport(respond)

    monkeypatch.setattr(httpx, "MockTransport", wrap)
    db, task_id, task, _calls, gateway = e2e._run(tmp_path, monkeypatch, v2=True, pending_ranking="legacy_wrong")
    if pause:
        assert tripped["value"] and task.status == "failed"
        monkeypatch.setattr(store, "record_execution_checkpoint", original_checkpoint)
        task = _resume_failed_same_task(db=db, task_id=task_id, tmp_path=tmp_path)
    assert task.status == "completed"
    e2e.assert_search_routes(gateway, [('项目 送样 公告','公司公告','q-1'), ('项目 送样 行业核实','行业媒体','q-1')])
    assert len(rounds) == 1


def test_empty_search_finishes_direct_round_without_a_synthetic_closure(tmp_path, monkeypatch):
    """An empty necessary search is terminal evidence, never a paid close-stage replay."""
    transport = httpx.MockTransport
    rounds = []

    def wrap(handler):
        def respond(request):
            payload = _payload_of(request)
            if payload.get("action") == "research_round":
                rounds.append(payload)
                if len(rounds) == 1:
                    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
                        _direct_query_round(payload))}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6}})
            return handler(request)
        return transport(respond)

    monkeypatch.setattr(httpx, "MockTransport", wrap)
    db, task_id, task, calls, gateway = e2e._run(tmp_path, monkeypatch, v2=True, pending_ranking="legacy_wrong")
    assert task.status == "completed"
    e2e.assert_search_routes(gateway, [('项目 送样 公告','公司公告','q-1')])
    assert len(rounds) == 1
    assert calls.count("research:research_round") == 0  # The crafted direct reply is the one paid request.
    assert read_report(db_path=db)["eveningCards"] == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_research_round_results").fetchone() == (1,)
        assert conn.execute("SELECT COUNT(*) FROM k10_research_stage_results").fetchone() == (0,)
        attempts = conn.execute(
            "SELECT state FROM k10_external_attempts WHERE task_id=? AND stage='investigation'", (task_id,)
        ).fetchall()
    assert attempts and all(state[0] == "succeeded" for state in attempts)


@pytest.mark.parametrize("correction", ["reject", "visible", "read"])
def test_direct_round_rejects_hidden_comparison_source_before_publication(tmp_path, monkeypatch, correction):
    """A comparison may only use packet-visible or explicitly read source facts."""
    transport = httpx.MockTransport
    rounds = []
    hidden = {"documentId": "hidden-source", "revision": 1}

    def wrap(handler):
        def respond(request):
            payload = _payload_of(request)
            if payload.get("action") != "research_round":
                return handler(request)
            rounds.append(payload)
            response = handler(request)
            body = response.json()
            value = json.loads(body["choices"][0]["message"]["content"])
            if len(rounds) == 1:
                assert hidden not in payload["evidencePacket"]["allowedEvidenceRefs"]
                value["comparison"]["evidenceRefs"] = [hidden]
                body["choices"][0]["message"]["content"] = json.dumps(value)
                return httpx.Response(response.status_code, json=body)
            assert "上次输出未通过校验" in json.loads(request.content)["messages"][-1]["content"]
            if correction == "reject":
                return _reply({
                    "action": "research_round",
                    "conclusion": {"researchStatus": "background_only", "companyMappings": [],
                                   "materialGaps": ["比较来源不可见"], "stopReason": "无法验证来源",
                                   "resumeCondition": "公开资料出现"},
                    "companyAssessments": [],
                })
            if correction == "read" and len(rounds) == 2:
                source_ref = payload["evidencePacket"]["allowedEvidenceRefs"][0]
                return _reply({"action": "research_round", "contextRequests": [{
                    "kind": "source", "sourceRef": source_ref, "location": "excerpt",
                    "purpose": "核对可见原文",
                }]})
            if correction == "read":
                assert payload["evidencePacket"]["contextResults"]
            return response
        return transport(respond)

    monkeypatch.setattr(httpx, "MockTransport", wrap)
    db, _task_id, task, _calls, _gateway = e2e._run(
        tmp_path, monkeypatch, v2=True, pending_ranking="legacy_wrong",
    )
    assert task.status == "completed"
    assert len(rounds) == 2
    report = read_report(db_path=db)
    assert len(report["eveningCards"]) == (0 if correction in {"reject", "read"} else 1)
    with sqlite3.connect(db) as conn:
        persisted = [json.loads(row[0]) for row in conn.execute("SELECT result_json FROM k10_research_round_results")]
    assert len(persisted) == 1
    if correction == "reject":
        assert persisted[0]["conclusion"]["researchStatus"] == "background_only"
    elif correction == "read":
        # Requesting a source with no newly visible text cannot turn that
        # source into evidence; the direct runner terminates safely.
        assert persisted[0]["conclusion"]["researchStatus"] == "pending_verification"
    else:
        assert hidden not in persisted[0]["comparison"]["evidenceRefs"]
    with client_for(db) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=evening")
    assert response.status_code == 200
    assert len(response.json()["report"]["eveningCards"]) == (0 if correction in {"reject", "read"} else 1)
