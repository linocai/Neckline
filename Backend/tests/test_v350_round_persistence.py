"""Direct-round state must remain atomic and preserve the accepted evidence."""
from dataclasses import replace
import sqlite3

import pytest

from neckline.k10 import store
from neckline.k10.delivery import runtime_contract
from neckline.k10.research_contracts import ResearchRoundResult
from neckline.k10.research_store import append_research_round, create_research_snapshot, load_research_round_state
from neckline.k10.schema import initialize_schema
from .test_v350_research_round import _snapshot, _packet, _complete_round


def _setup(tmp_path):
    db = tmp_path / "round.sqlite"
    initialize_schema(db)
    snapshot = _snapshot()
    store.set_run_control(state="open", reason_code="fixture", changed_at=snapshot.created_at,
                          changed_by="round-storage-test", db_path=db)
    task = store.enqueue_task(task_id=snapshot.task_id, kind="evening_scan", idempotency_key="round-storage",
        input_version="round-input", input_cutoff_at=snapshot.news_cutoff_at,
        payload={"runtimeContract": runtime_contract()}, budget={"maxAttempts": 1},
        created_at=snapshot.created_at, db_path=db)
    snapshot = replace(snapshot, task_id=task.task_id)
    store.append_event_revision(event_id=snapshot.event_id, stable_key="round-storage-event",
        headline="来源陈述", event_kind="industry_change", facts={}, source_refs=[],
        supersedes_revision=None, created_at=snapshot.created_at, db_path=db)
    create_research_snapshot(snapshot=snapshot, db_path=db)
    return db, snapshot


def _append(db, snapshot, result=None):
    return append_research_round(snapshot_id=snapshot.snapshot_id, expected_revision=snapshot.revision,
        input_packet=_packet(), result=ResearchRoundResult.from_dict(result or _complete_round()),
        research_status="ready_for_comparison", updated_at=snapshot.updated_at, db_path=db)


def test_round_and_snapshot_are_one_transaction(tmp_path):
    db, snapshot = _setup(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TRIGGER interrupt_round BEFORE INSERT ON k10_research_round_results "
                     "BEGIN SELECT RAISE(ABORT, 'fixture interruption'); END")
    with pytest.raises(sqlite3.DatabaseError, match="fixture interruption"):
        _append(db, snapshot)
    state = load_research_round_state(snapshot_id=snapshot.snapshot_id, db_path=db)
    assert state["snapshot"].revision == 1 and state["rounds"] == []
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TRIGGER interrupt_round")
    assert _append(db, snapshot).revision == 2
    assert _append(db, snapshot).revision == 2
    state = load_research_round_state(snapshot_id=snapshot.snapshot_id, db_path=db)
    assert len(state["rounds"]) == 1


def test_same_packet_cannot_silently_accept_different_result(tmp_path):
    db, snapshot = _setup(tmp_path)
    _append(db, snapshot)
    changed = _complete_round()
    changed["conclusion"]["stopReason"] = "a different accepted conclusion"
    with pytest.raises(store.K10Conflict):
        _append(db, snapshot, changed)
    state = load_research_round_state(snapshot_id=snapshot.snapshot_id, db_path=db)
    assert state["rounds"][0]["result"]["conclusion"]["stopReason"] == _complete_round()["conclusion"]["stopReason"]


def test_corrupt_round_packet_is_not_reused_as_model_evidence(tmp_path):
    db, snapshot = _setup(tmp_path)
    _append(db, snapshot)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE k10_research_round_results SET input_packet_json='{}'")
    with pytest.raises(store.K10Conflict):
        load_research_round_state(snapshot_id=snapshot.snapshot_id, db_path=db)
