"""Generate B69 Swift DTO fixtures from real CLI → worker → FastAPI paths.

This is deliberately a test-side exporter: every database, provider transport
and source is isolated and deterministic.  The four JSON files are unmodified
FastAPI response bodies, never hand-written client fixtures.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from neckline.api.k10 import create_router
from neckline.k10 import store
from tests import test_v310_pipeline_e2e as e2e


def _api(db_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(
        lambda: db_path,
        lambda: None,
        lambda: db_path.parent / "parquet",
        current_config_binding_provider=lambda: ("b39", 1, None),
        current_execution_config_binding_provider=lambda: ("b39-execution", 1, None),
    ))
    return TestClient(app)


def _write_json(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(f"B69 DTO evidence already exists: {path}")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_case(root: Path, *, provider_status: int | None) -> tuple[Path, str, str, object]:
    root.mkdir(parents=True, exist_ok=True)
    with pytest.MonkeyPatch.context() as monkeypatch:
        db_path, task_id, task, _calls, _gateway = e2e._run(
            root, monkeypatch, v2=True, provider_status=provider_status, cli_entry=True,
        )
        execution = store.task_execution_input(task_id=task_id, db_path=db_path)
        checkpoint = execution["checkpoint"]
        scan_id = checkpoint["scanId"]
        return db_path, task_id, scan_id, task


def export_dtos(output: Path, *, temp_root: Path) -> dict[str, str]:
    """Write the four required actual responses and their IDs, then clean DBs."""
    output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="dto-export-", dir=temp_root))
    try:
        success_db, completed_task_id, completed_scan_id, completed_task = _run_case(work / "completed", provider_status=None)
        assert getattr(completed_task, "status", None) == "completed"
        with _api(success_db) as client:
            completed_report = client.get("/api/v1/k10/v2/reports/latest", params={"window": "evening"})
            completed_scan = client.get(f"/api/v1/k10/scans/{completed_scan_id}")
        completed_report.raise_for_status(); completed_scan.raise_for_status()
        completed_report_json, completed_scan_json = completed_report.json(), completed_scan.json()
        completed = completed_report_json.get("report")
        assert completed_report_json.get("state") == "available"
        assert isinstance(completed, dict) and completed.get("status") == "completed"
        assert completed.get("availableAt") and isinstance(completed.get("eveningCards"), list) and completed["eveningCards"]

        failed_db, failed_task_id, failed_scan_id, failed_task = _run_case(work / "failed", provider_status=402)
        assert getattr(failed_task, "status", None) == "failed"
        with _api(failed_db) as client:
            failed_report = client.get("/api/v1/k10/v2/reports/latest", params={"window": "evening"})
            failed_scan = client.get(f"/api/v1/k10/scans/{failed_scan_id}")
        failed_report.raise_for_status(); failed_scan.raise_for_status()
        failed_report_json, failed_scan_json = failed_report.json(), failed_scan.json()
        failed = failed_report_json.get("report")
        assert failed_report_json.get("state") == "available"
        assert isinstance(failed, dict) and failed.get("status") == "failed"
        assert failed.get("availableAt") is None and failed.get("eveningCards") == []
        failed_summary = failed_scan_json.get("researchSummary")
        assert isinstance(failed_summary, dict) and failed_summary.get("executionFailed") is True

        _write_json(output / "b69-completed-report.json", completed_report_json)
        _write_json(output / "b69-completed-scan.json", completed_scan_json)
        _write_json(output / "b69-failed-report.json", failed_report_json)
        _write_json(output / "b69-failed-scan.json", failed_scan_json)
        ids = {"completedTaskId": completed_task_id, "completedScanId": completed_scan_id,
               "failedTaskId": failed_task_id, "failedScanId": failed_scan_id}
        _write_json(output / "b69-expected.json", ids)
        return ids
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, default=Path("/tmp/neckline-v330-b69/backend"))
    args = parser.parse_args(argv)
    args.temp_root.mkdir(parents=True, exist_ok=True)
    print(json.dumps(export_dtos(args.output, temp_root=args.temp_root), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
