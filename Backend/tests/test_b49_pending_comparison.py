"""B78 direct comparison outcomes distinguish publishable uncertainty from pending work."""
from __future__ import annotations

from neckline.k10 import store
from tests.test_v310_pipeline_e2e import _run


def test_labeled_unverified_direct_comparison_can_publish_without_another_model_stage(tmp_path, monkeypatch):
    db, task_id, task, calls, gateway = _run(tmp_path, monkeypatch, pending_ranking="legacy_wrong")

    assert task.status == "completed"
    assert calls.count("research:research_round") == 1
    assert gateway.search_paths == []
    scan = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    candidates = store.list_candidates(scan_id=scan, state="offered", db_path=db)
    assert len(candidates) == 1
    disclosure = candidates[0]["comparison"]["differences"]["evidenceDisclosure"]
    assert disclosure["verificationStatus"] == "unverified"
    assert disclosure["unverifiedReasons"] == ["独立来源未确认"]


def test_pending_direct_comparison_does_not_invent_a_rank_or_final_sort(tmp_path, monkeypatch):
    db, task_id, task, calls, gateway = _run(tmp_path, monkeypatch, pending_ranking="pending")

    assert task.status == "completed"
    assert calls.count("research:research_round") == 1
    assert "prioritize" not in calls
    assert gateway.search_paths == []
    scan = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    assert store.list_candidates(scan_id=scan, state="offered", db_path=db) == []
