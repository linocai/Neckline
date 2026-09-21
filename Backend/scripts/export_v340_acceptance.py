#!/usr/bin/env python3
"""Export B76 acceptance responses from the actual offline CLI/worker fixture.

This is a local release-evidence producer.  It never reads production state,
opens a network socket, or falls back to a hand-written API payload.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from _pytest.monkeypatch import MonkeyPatch


RELEASE_ROOT = Path("/Users/linotsai/Lino/releases/Neckline/v3.4.0-b76-20260916")
EVIDENCE_API = RELEASE_ROOT / "evidence" / "api"
TEMPORARY = RELEASE_ROOT / "temporary" / "acceptance" / "loopback"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("complete", "partial", "failed", "empty", "complete-zero", "partial-zero"), required=True)
    parser.add_argument("--database-name", help="preserved isolated database basename; defaults to the scenario")
    args = parser.parse_args()

    # This script lives under Backend/scripts while its isolated real-entry
    # fixture deliberately lives under Backend/tests.
    backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend))
    sys.path.insert(0, str(backend / "tests"))
    from v340_acceptance_fixture import active_bindings, actual_api, run_full_scale_flow, seed_database

    EVIDENCE_API.mkdir(parents=True, exist_ok=True, mode=0o700)
    TEMPORARY.mkdir(parents=True, exist_ok=True, mode=0o700)
    database_name = args.database_name or args.scenario
    if not database_name or Path(database_name).name != database_name or not database_name.replace("-", "").replace("_", "").isalnum():
        raise SystemExit("database name must contain only letters, numbers, '_' or '-'")
    db_path = TEMPORARY / f"{database_name}.sqlite"
    if db_path.exists():
        raise SystemExit(f"preserved acceptance DB already exists: {db_path}")

    monkeypatch = MonkeyPatch()
    try:
        options = {
            "complete": {},
            "partial": {"refusal_event": 1},
            "failed": {"refusal_operation": "prioritize", "expect_handler_failure": True},
            "complete-zero": {"selected_event_count": 0},
            "partial-zero": {"selected_event_count": 1, "refusal_event": 0, "all_events_same_company": True},
        }
        if args.scenario == "empty":
            seed_database(db_path)
            flow = None
        else:
            flow = run_full_scale_flow(TEMPORARY, monkeypatch, name=database_name, **options[args.scenario])
            expected = "failed" if args.scenario == "failed" else "completed"
            if flow.task_status != expected:
                raise RuntimeError(f"{args.scenario} acceptance task did not reach {expected}: {flow.task_status}")
        config_id, config_revision, execution_id, execution_revision = active_bindings(db_path)
        with actual_api(db_path, config_id=config_id, config_revision=config_revision,
                        execution_id=execution_id, execution_revision=execution_revision) as client:
            report = client.get("/api/v1/k10/v2/reports/latest?window=evening")
            readiness = client.get("/api/v1/k10/operations/readiness")
        if report.status_code != 200 or readiness.status_code != 200:
            raise RuntimeError("actual FastAPI acceptance response was not available")
        readiness_payload = readiness.json()
        run_control = readiness_payload.get("runControl") if isinstance(readiness_payload, dict) else None
        if not isinstance(run_control, dict) or any(
            not isinstance(run_control.get(key), str) or not run_control[key]
            for key in ("state", "reasonCode", "changedAt", "executionState")
        ):
            raise RuntimeError("actual operations readiness lacks its B76 run-control projection")
        report_name = {
            "complete": "b76-complete-report.json",
            "partial": "b76-partial-report.json",
            "failed": "b76-failed-report.json",
            "empty": "b76-empty-report.json",
            "complete-zero": "b76-complete-zero-cards-report.json",
            "partial-zero": "b76-partial-zero-cards-report.json",
        }[args.scenario]
        _write_json(EVIDENCE_API / report_name, report.json())
        if args.scenario == "complete":
            _write_json(EVIDENCE_API / "b76-operations-readiness.json", readiness_payload)
        _write_json(RELEASE_ROOT / "evidence" / "acceptance" / f"{args.scenario}-provenance.json", {
            "synthetic": True,
            "scenario": args.scenario,
            "taskId": None if flow is None else flow.task_id,
            "scanId": None if flow is None else flow.scan_id,
            "database": str(db_path),
            "modelTransport": "deterministic OpenAI-compatible MockTransport",
            "searchTransport": "real TavilySearchClient with deterministic httpx.MockTransport",
            "network": "socket denied",
            "titleCount": None if flow is None else 1602,
            "eventCount": None if flow is None else (0 if args.scenario == "complete-zero" else 1 if args.scenario == "partial-zero" else 84),
            "searchCredits": None if flow is None else len(flow.gateway_calls),
        })
    finally:
        monkeypatch.undo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
