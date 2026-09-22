"""B82 R3: legacy discovery issues must never guess a private research unit.

The producer writes a V3 draft on the first worker pass, then a later worker
thaws that exact draft.  This keeps the regression at the CLI/worker boundary:
the test never repairs a binding or report row after the fact.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import sqlite3
from typing import Any

import pytest

from neckline.k10 import pipeline, store
from neckline.k10.discovery import DiscoveryIssue, DiscoverySliceYield
from neckline.k10.worker import run_once
from tests import test_b82_cross_slice as cross
from tests import v340_acceptance_fixture as base
from tests.test_v350_cli_api import explicit_bindings


def _legacy_v3_issue_round(
    tmp_path, monkeypatch: pytest.MonkeyPatch, *, issue: DiscoveryIssue, frozen_version: int = 3,
) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any], Any]:
    """Run one real task through its persisted frozen discovery draft."""
    assert frozen_version in {3, 4}
    database = tmp_path / f"legacy-{issue.code}.sqlite"
    day = base.DAY
    business_clock = [cross.evening_cutoff(day) + timedelta(hours=1)]
    config_id, config_revision, execution_id, execution_revision = base.seed_database(
        database, trading_day=day, fixture_now=business_clock[0],
    )
    execution_revision, _ = cross._cross_serial_execution_binding(
        database=database,
        execution_id=execution_id,
        execution_revision=execution_revision,
        configuration_id=config_id,
        configuration_revision=config_revision,
        created_at=business_clock[0],
    )
    cross._install_unit_fixture(monkeypatch, database=database, business_clock=business_clock)

    original_run = pipeline.run_discovery

    def with_legacy_issue(*args, **kwargs):
        run = original_run(*args, **kwargs)
        assert len(run.events) == 2
        return replace(run, issues=(*run.issues, issue))

    monkeypatch.setattr(pipeline, "run_discovery", with_legacy_issue)
    original_freeze = pipeline.freeze_discovery_run

    def freeze_as_v3(run):
        frozen = original_freeze(run)
        legacy = [row for row in frozen["issues"] if row["code"] == issue.code]
        assert legacy == [{"stage": issue.stage, "code": issue.code,
                           **({"canonicalKey": issue.canonical_key} if issue.canonical_key else {}),
                           **({"executionUnitId": issue.execution_unit_id}
                              if issue.execution_unit_id else {})}]
        # Version 3 is the old durable protocol with no private execution
        # unit.  Version 4 keeps the explicit ID so finalization can reject a
        # foreign one rather than silently falling back to canonicalKey.
        return {**frozen, "version": frozen_version}

    monkeypatch.setattr(pipeline, "freeze_discovery_run", freeze_as_v3)
    original_checkpoint = store.update_running_scan_coverage
    controls = {"yielded": False}

    def checkpoint_then_yield(*, scan_id: str, coverage, db_path):
        original_checkpoint(scan_id=scan_id, coverage=coverage, db_path=db_path)
        if not controls["yielded"] and isinstance(coverage.get("discoveryDraft"), dict):
            controls["yielded"] = True
            raise DiscoverySliceYield()

    monkeypatch.setattr(store, "update_running_scan_coverage", checkpoint_then_yield)
    task_id = cross._enqueue_evening(
        database=database,
        day=day,
        config_id=config_id,
        config_revision=config_revision,
        execution_id=execution_id,
        execution_revision=execution_revision,
    )
    handler = cross._handler(parquet_dir=tmp_path / "parquet", business_clock=business_clock)
    paused = cross._run_worker(
        database=database, task_id=task_id, worker_id=f"b82-{issue.code}-pause", handler=handler,
    )
    assert paused.status == "queued" and controls["yielded"]
    scan_id, scan_status, frozen_coverage = cross._scan_for_task(database=database, task_id=task_id)
    assert scan_status == "running"
    frozen = frozen_coverage["discoveryDraft"]
    assert frozen["version"] == frozen_version
    if frozen_version == 3:
        assert all("executionUnitId" not in row for row in frozen["issues"])
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT not_before_at FROM k10_task_retry_schedules WHERE task_id=?", (task_id,),
        ).fetchone()
    assert row is not None
    due = datetime.fromisoformat(row[0])
    completed = run_once(
        db_path=database,
        task_id=task_id,
        worker_id=f"b82-{issue.code}-resume",
        lease_for=timedelta(minutes=5),
        handlers={"evening_scan": handler},
        clock=lambda: due + timedelta(seconds=1),
        require_b76_contract=True,
    )
    assert completed is not None and completed.status == "completed"

    with base.actual_api(database, **explicit_bindings(database)) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=evening")
        response.raise_for_status()
        report = response.json()["report"]
        materials_response = client.get(f"/api/v1/k10/v2/reports/{report['reportId']}/materials")
        materials_response.raise_for_status()
    return report, materials_response.json(), scan_id, frozen, database


def test_b82_legacy_ambiguous_event_issue_blocks_sibling_candidates(tmp_path, monkeypatch):
    """A V3 public-event issue cannot be assigned to announcement or denial."""
    report, materials, scan_id, frozen, database = _legacy_v3_issue_round(
        tmp_path,
        monkeypatch,
        issue=DiscoveryIssue(
            "compare", "legacy_event_stage_unbound", canonical_key=cross._EVENT_KEY,
        ),
    )

    assert frozen["issues"] == [{
        "stage": "compare", "code": "legacy_event_stage_unbound", "canonicalKey": cross._EVENT_KEY,
    }]
    assert report["status"] == "partial" and report["availableAt"]
    delivery = report["delivery"]
    assert delivery["outcome"] == "partial"
    assert delivery["rankingScope"] == "none"
    assert delivery["counts"] == {
        **delivery["counts"],
        "eventInput": 2,
        "eventProcessed": 0,
        "eventFailed": 0,
        "eventUnprocessed": 2,
        "comparableCompanies": 2,
        "publishedCompanies": 0,
    }
    gaps = [gap for gap in delivery["gaps"] if gap["reasonCode"] == "legacy_event_stage_unbound"]
    assert len(gaps) == 1
    assert gaps[0]["unitKind"] == "event"
    assert gaps[0]["companyScopeKnown"] is False
    assert gaps[0]["eventIds"] == [pipeline._event_id(cross._EVENT_KEY)]
    assert report["eveningCards"] == []
    # Safe source material survives even when formal ranking cannot be honest.
    assert len(materials["items"]) == 2
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM k10_publication_samples WHERE batch_id=?", ("publication_" + scan_id,),
        ).fetchone() == (0,)


def test_b82_legacy_title_issue_keeps_independent_complete_candidates(tmp_path, monkeypatch):
    """A title-only V3 gap is disclosed without erasing separately complete events."""
    report, materials, _scan_id, frozen, _database = _legacy_v3_issue_round(
        tmp_path,
        monkeypatch,
        issue=DiscoveryIssue("title_triage", "legacy_title_only_unbound"),
    )

    assert frozen["issues"] == [{"stage": "title_triage", "code": "legacy_title_only_unbound"}]
    assert report["status"] == "partial" and report["availableAt"]
    delivery = report["delivery"]
    assert delivery["outcome"] == "partial"
    assert delivery["rankingScope"] == "completed_subset"
    assert delivery["counts"] == {
        **delivery["counts"],
        "eventInput": 2,
        "eventProcessed": 2,
        "eventFailed": 0,
        "eventUnprocessed": 0,
        "comparableCompanies": 2,
        "publishedCompanies": 2,
    }
    gaps = [gap for gap in delivery["gaps"] if gap["reasonCode"] == "legacy_title_only_unbound"]
    assert len(gaps) == 1
    assert gaps[0]["unitKind"] == "discovery"
    assert gaps[0]["companyScopeKnown"] is False
    assert len(report["eveningCards"]) == 2
    assert len(materials["items"]) == 2


def test_b82_foreign_v4_execution_unit_never_falls_back_to_canonical_key(tmp_path, monkeypatch):
    """A nonempty V4 ID outside this run is corrupt, even for a matching event key."""
    foreign_unit_id = "foreign_unit_b82_issue"
    report, materials, scan_id, frozen, database = _legacy_v3_issue_round(
        tmp_path,
        monkeypatch,
        issue=DiscoveryIssue(
            "compare",
            "foreign_execution_unit",
            canonical_key=cross._EVENT_KEY,
            execution_unit_id=foreign_unit_id,
        ),
        frozen_version=4,
    )

    assert frozen["issues"] == [{
        "stage": "compare",
        "code": "foreign_execution_unit",
        "canonicalKey": cross._EVENT_KEY,
        "executionUnitId": foreign_unit_id,
    }]
    delivery = report["delivery"]
    assert report["status"] == "partial" and report["availableAt"]
    assert delivery["outcome"] == "partial" and delivery["rankingScope"] == "none"
    assert delivery["counts"] == {
        **delivery["counts"],
        "eventInput": 2,
        "eventProcessed": 0,
        "eventFailed": 0,
        "eventUnprocessed": 2,
        "comparableCompanies": 2,
        "publishedCompanies": 0,
    }
    gaps = [gap for gap in delivery["gaps"] if gap["reasonCode"] == "foreign_execution_unit"]
    assert len(gaps) == 1
    assert gaps[0]["unitKind"] == "event"
    assert gaps[0]["companyScopeKnown"] is False
    assert gaps[0]["unitId"] == f"foreign_{foreign_unit_id}"
    assert report["eveningCards"] == []
    assert len(materials["items"]) == 2
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM k10_publication_samples WHERE batch_id=?", ("publication_" + scan_id,),
        ).fetchone() == (0,)
