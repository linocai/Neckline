"""Failure disposition replay is idempotent, without relaxing ledger guards."""
import sqlite3

import pytest

from neckline.k10 import store
from neckline.k10.research_store import mark_research_round_failed
from tests.test_v350_round_persistence import _setup, _packet


def test_failed_round_replay_preserves_one_revision_and_exact_disposition(tmp_path):
    db, snapshot = _setup(tmp_path)
    args = dict(snapshot_id=snapshot.snapshot_id, input_packet=_packet(),
                safe_error_code="investigation_reference_invalid",
                updated_at=snapshot.updated_at, db_path=db)
    failed = mark_research_round_failed(expected_revision=1, **args)
    assert failed.revision == 2 and failed.execution_status == "failed"
    for revision in (1, 2):
        assert mark_research_round_failed(expected_revision=revision, **args) == failed
    with pytest.raises(store.K10Conflict):
        mark_research_round_failed(expected_revision=2,
            **{**args, "safe_error_code": "investigation_execution_failed"})
    with pytest.raises(store.K10Conflict):
        mark_research_round_failed(expected_revision=2,
            **{**args, "input_packet": {**_packet(), "changed": True}})
    def expired_lease():
        raise store.K10Conflict("expired lease")
    with pytest.raises(store.K10Conflict, match="expired lease"):
        mark_research_round_failed(expected_revision=2, lease_guard=expired_lease, **args)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM k10_research_round_results").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM k10_research_snapshot_revisions").fetchone()[0] == 2
