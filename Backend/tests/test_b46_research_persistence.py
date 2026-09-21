"""B78 direct-result persistence at the real worker boundary."""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import pytest

from neckline.k10 import store
from neckline.k10.research_store import load_research_round_state
from neckline.k10.worker import _execution_deadline
from tests.test_v310_pipeline_e2e import _run, RUN_AT


def test_direct_round_receipt_is_persisted_once_without_legacy_stage_projection(tmp_path, monkeypatch):
    db, task_id, task, calls, gateway = _run(tmp_path, monkeypatch)

    assert task.status == "completed"
    assert calls.count("research:research_round") == 1
    assert gateway.search_paths == []
    scan_id = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    snapshot_id = store.get_scan(scan_id=scan_id, db_path=db)["coverage"]["researchSnapshotIds"][0]
    state = load_research_round_state(snapshot_id=snapshot_id, db_path=db)
    assert state is not None and len(state["rounds"]) == 1
    assert state["rounds"][0]["result"]["action"] == "research_round"
    with sqlite3.connect(db) as conn:
        checkpoints = conn.execute(
            "SELECT status,result_json FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND stage='model:investigation_research_round'",
            (task_id,),
        ).fetchall()
        legacy_rows = conn.execute(
            "SELECT COUNT(*) FROM k10_research_stage_results WHERE snapshot_id=?", (snapshot_id,),
        ).fetchone()[0]
    assert checkpoints and all(status == "completed" for status, _ in checkpoints)
    assert all(json.loads(raw)["action"] == "research_round" for _, raw in checkpoints)
    assert legacy_rows == 0


def test_runtime_deadline_does_not_accept_another_binding_or_shorten_policy():
    profile = {"contentSha256": "bound", "payload": {"discovery": {"completionDeadlineSeconds": 7200}}}
    for repair in ({"originalExecutionContentSha256": "other", "completionDeadlineSeconds": 21600},
                   {"originalExecutionContentSha256": "bound", "completionDeadlineSeconds": 1}):
        with pytest.raises(ValueError):
            _execution_deadline(profile=profile, started_at=RUN_AT, runtime_repair=repair)
    assert _execution_deadline(profile=profile, started_at=RUN_AT) == RUN_AT + timedelta(hours=2)
