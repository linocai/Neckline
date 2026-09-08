"""Actual model/worker boundaries: evidence validation and explicit runtime repair."""
import json
import sqlite3
from datetime import timedelta
import pytest
from neckline.k10 import pipeline, store
from neckline.k10.cli import main, recover_scan, frozen_scan_input_sha256
from neckline.k10.worker import run_once, _execution_deadline
from tests.test_v310_pipeline_e2e import _run, _http_transport, RUN_AT
from tests.test_v310_investigation import _claim
from neckline.k10.investigation import decode_stage_result, InvestigationError


def test_compact_update_copies_only_known_input_identity_and_persists_complete_result():
    original = _claim().to_dict()
    raw = {"action":"assess_evidence", "claims":[{"claimId":original["claimId"], "verificationStatus":"partially_supported"}]}
    result = decode_stage_result(raw,action="assess_evidence",evidence_packet={"claims":[original]})
    expected = {**original,"verificationStatus":"partially_supported"}
    assert result.claims[0].to_dict() == expected
    assert decode_stage_result(result.to_dict(), action="assess_evidence").claims[0].to_dict() == expected
    assert original == _claim().to_dict()
    with pytest.raises(InvestigationError):
        decode_stage_result(raw,action="assess_evidence",evidence_packet={"claims":[]})


def test_evidence_location_is_repaired_before_completed_model_checkpoint(tmp_path, monkeypatch):
    db, task_id, task, calls, gateway = _run(tmp_path, monkeypatch, evidence_location="repair")
    assert task.status == "completed"
    assert calls.count("research:assess_evidence") == 3
    assert len(gateway.search_paths) == 2
    assert calls.count("understand") == 1
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT status,result_json FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:investigation_assess_evidence'", (task_id,)).fetchall()
    assert all(state == "completed" and json.loads(raw)["evidenceUpdates"][0]["location"] == "excerpt" for state, raw in rows)


def test_cli_runtime_repair_keeps_frozen_work_and_revalidates_poisoned_legacy_cache(tmp_path, monkeypatch):
    db, task_id, task, _, _ = _run(tmp_path, monkeypatch, malformed_action="close_research")
    assert task.status == "failed"
    before = store.task_execution_input(task_id=task_id, db_path=db)
    scan = before["checkpoint"]["scanId"]
    with sqlite3.connect(db) as conn:
        key, raw = conn.execute("SELECT item_key,result_json FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:investigation_assess_evidence' LIMIT 1", (task_id,)).fetchone()
        value = json.loads(raw)
        value["evidenceUpdates"] = [{"claimId":"c", "sourceRef":{"documentId":"d","revision":1}, "relation":"irrelevant", "location":"", "applicability":{}}]
        poisoned = json.dumps(value)
        conn.execute("UPDATE k10_execution_item_checkpoints SET result_json=? WHERE task_id=? AND item_key=?", (poisoned, task_id, key))
    assert main(["recover-scan","--db",str(db),"--scan-id",scan,"--execution-config-id","b39-execution",
        "--execution-config-revision","1","--confirm-frozen-input-sha256",frozen_scan_input_sha256(scan_id=scan,db_path=db),
        "--research-max-tokens","32768","--completion-deadline-seconds","21600"]) == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT status,result_json FROM k10_execution_item_checkpoints WHERE task_id=? AND item_key=?", (task_id,key)).fetchone() == ("failed",poisoned)
    calls = _http_transport(monkeypatch, initial_query_round=1)
    original = pipeline.DeepSeekDiscoveryModel._request_json
    observed = []
    def capture(self, **kwargs):
        if kwargs["payload"].get("action"):
            observed.append(kwargs["model_options"]["maxTokens"])
        return original(self, **kwargs)
    monkeypatch.setattr(pipeline.DeepSeekDiscoveryModel,"_request_json",capture)
    now = RUN_AT + timedelta(hours=3)
    done = run_once(db_path=db,worker_id="repair",lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token="fixture-token",parquet_dir=tmp_path/"parquet"),clock=lambda:now)
    assert done.status == "completed"
    assert observed and set(observed) == {32768}
    assert not {"titleBatch","titleGlobal","titleReview","understand","research:plan_gaps"} & set(calls)
    after = store.task_execution_input(task_id=task_id, db_path=db)
    assert after["checkpoint"]["executionStartedAt"] == before["checkpoint"]["executionStartedAt"]
    assert after["checkpoint"]["runtimeRepair"]["completionDeadlineSeconds"] == 21600
    assert len(store.list_candidates(scan_id=scan,state="offered",db_path=db)) == 1


def test_runtime_deadline_does_not_accept_another_binding_or_shorten_policy():
    profile = {"contentSha256":"bound", "payload":{"discovery":{"completionDeadlineSeconds":7200}}}
    for repair in ({"originalExecutionContentSha256":"other","completionDeadlineSeconds":21600},
                   {"originalExecutionContentSha256":"bound","completionDeadlineSeconds":1}):
        with pytest.raises(ValueError):
            _execution_deadline(profile=profile,started_at=RUN_AT,runtime_repair=repair)
    assert _execution_deadline(profile=profile,started_at=RUN_AT) == RUN_AT+timedelta(hours=2)
