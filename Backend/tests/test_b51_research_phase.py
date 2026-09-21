"""B78 finalizes direct comparison in the same durable research receipt."""
from __future__ import annotations

from neckline.k10 import store
from neckline.k10.research_store import load_research_round_state
from tests.test_v310_pipeline_e2e import _run


def test_direct_round_persists_comparison_without_a_second_research_phase(tmp_path, monkeypatch):
    db, task_id, task, calls, gateway = _run(tmp_path, monkeypatch)

    assert task.status == "completed"
    assert calls.count("research:research_round") == 1
    assert gateway.search_paths == []
    assert not {"research:plan_gaps", "research:plan_queries", "research:assess_evidence",
                "research:close_research", "research:compare_companies"} & set(calls)
    scan = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    snapshot_id = store.get_scan(scan_id=scan, db_path=db)["coverage"]["researchSnapshotIds"][0]
    state = load_research_round_state(snapshot_id=snapshot_id, db_path=db)
    result = state["rounds"][0]["result"]
    assert {row["role"] for row in result["companyAssessments"]} == {"primary", "pending", "excluded"}
    assert result["comparison"]["summary"]
