"""B82 handoff fixtures for populated client acceptance.

The three retained databases are deliberately made through the same CLI,
worker, production handler and FastAPI router used by the release.  They are
offline fixtures only: the model/search transports deny sockets and all
responses are deterministic.  They give the native clients stable examples of
the three states they must render, without pretending that a synthetic run is
a production report.

Set ``NK_B82_APP_API_DIR`` to retain the fixtures under the release temporary
directory.  A second invocation verifies the retained files without replacing
them.  See the generated ``handoff-manifest.json`` for the matching FastAPI
commands and the intentionally non-production bearer token.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
from typing import Any, Mapping

import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import main as cli_main
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.windows import SHANGHAI
from neckline.k10.worker import run_once
from . import v340_acceptance_fixture as base
from .test_v350_cli_api import DirectRoundTransport, explicit_bindings


_OUTPUT_ENV = "NK_B82_APP_API_DIR"
_REFRESH_ENV = "NK_B82_APP_API_REFRESH"
_MANIFEST_NAME = "handoff-manifest.json"
_FIXTURE_TOKEN = "b82-handoff-api-token"
_GENERATOR = "tests/test_b82_api_handoff.py::test_b82_cli_worker_fastapi_handoff"
_CURRENT_EVENING_TRADING_DAY = date(2026, 9, 21)
_CURRENT_EVENING_HANDLER_AT = datetime(2026, 9, 21, 22, 0, tzinfo=SHANGHAI)
_CURRENT_MORNING_TRADING_DAY = date(2026, 9, 22)


def _handoff_root(tmp_path: Path) -> Path:
    value = os.environ.get(_OUTPUT_ENV)
    return Path(value).expanduser().resolve() if value else tmp_path / "app-api"


def _task_outbox_count(database: Path, task_id: str) -> int:
    with sqlite3.connect(database) as connection:
        return int(connection.execute(
            "SELECT COUNT(*) FROM k10_task_notifications WHERE task_id=?", (task_id,),
        ).fetchone()[0])


def _task_cli_stdout(database: Path, task_id: str) -> str:
    """Read back the durable task identity after the CLI printed it."""
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT task_id FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()
    assert row == (task_id,)
    return f"{task_id}\n"


def _api_projection(database: Path, *, window: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read the production FastAPI router exactly as a client does."""
    with base.actual_api(database, **explicit_bindings(database)) as client:
        latest_response = client.get(f"/api/v1/k10/v2/reports/latest?window={window}")
        latest_response.raise_for_status()
        latest = latest_response.json()
        assert latest["schemaVersion"] == 9
        report = latest["report"]
        assert isinstance(report, dict)
        report_id = report["reportId"]
        assert "parentReportId" in report
        exact_response = client.get(f"/api/v1/k10/v2/reports/{report_id}")
        exact_response.raise_for_status()
        assert exact_response.json()["report"] == report
        materials_response = client.get(f"/api/v1/k10/v2/reports/{report_id}/materials")
        materials_response.raise_for_status()
        materials = materials_response.json()
        assert materials["schemaVersion"] == 9 and materials["reportId"] == report_id
        for item in materials["items"]:
            assert isinstance(item["eventId"], str) and item["eventId"]
    return latest, materials


def _formal_card_count(report: Mapping[str, Any]) -> int:
    return sum(len(report[name]) for name in ("eveningCards", "updatedCards", "addedCards"))


def _gap_reasons(report: Mapping[str, Any]) -> list[str]:
    delivery = report.get("delivery")
    if not isinstance(delivery, Mapping):
        return []
    return [str(gap["reasonCode"]) for gap in delivery.get("gaps", [])
            if isinstance(gap, Mapping) and isinstance(gap.get("reasonCode"), str)]


def _record(
    *, label: str, database: Path, task_id: str, task_status: str, cli_stdout: str,
    business_times: Mapping[str, str], latest: Mapping[str, Any], materials: Mapping[str, Any],
) -> dict[str, Any]:
    report = latest["report"]
    assert isinstance(report, Mapping)
    assert cli_stdout.strip() == task_id
    assert _task_cli_stdout(database, task_id) == cli_stdout
    cards = _formal_card_count(report)
    event_ids = [item["eventId"] for item in materials["items"]]
    assert len(event_ids) == len(set(event_ids))
    return {
        "state": label,
        "generator": _GENERATOR,
        "database": database.name,
        "taskId": task_id,
        "taskStatus": task_status,
        "cliStdout": cli_stdout,
        "window": report["windowKind"],
        "businessTimes": dict(business_times),
        "reportId": report["reportId"],
        "parentReportId": report["parentReportId"],
        "cutoffAt": report["cutoffAt"],
        "deliveryDeadlineAt": report.get("deliveryDeadlineAt"),
        "formalCardCount": cards,
        "materialsCount": len(materials["items"]),
        "eventCount": len(event_ids),
        "gapReasons": _gap_reasons(report),
        "outboxCount": _task_outbox_count(database, task_id),
        "bindings": explicit_bindings(database),
    }


def _capture_evening_flow(root: Path, monkeypatch: pytest.MonkeyPatch, *, name: str,
                          refusal_event: int | None = None,
                          refusal_operation: str | None = None,
                          expect_handler_failure: bool = False,
                          trading_day: date = base.DAY,
                          fixture_now: datetime = base.NOW,
                          fixture_run_at: datetime = base.RUN_AT) -> tuple[base.FlowResult, str]:
    """Run the existing full-scale producer, while retaining its real CLI stdout."""
    captured: list[str] = []
    with monkeypatch.context() as patch:
        original_cli = base.cli_main

        def capture_cli(arguments: list[str]) -> int:
            result = original_cli(arguments)
            stream = sys.stdout
            assert isinstance(stream, StringIO)
            captured.append(stream.getvalue())
            return result

        patch.setattr(base, "TITLE_COUNT", 130)
        patch.setattr(base, "DeterministicTransport", DirectRoundTransport)
        patch.setattr(base, "cli_main", capture_cli)
        flow = base.run_full_scale_flow(
            root, patch, name=name, selected_event_count=3, refusal_event=refusal_event,
            refusal_operation=refusal_operation, expect_handler_failure=expect_handler_failure,
            trading_day=trading_day, fixture_now=fixture_now, fixture_run_at=fixture_run_at,
        )
    assert captured == [f"{flow.task_id}\n"]
    return flow, captured[0]


def _capture_morning_partial(
    root: Path, monkeypatch: pytest.MonkeyPatch, *, day: date = _CURRENT_MORNING_TRADING_DAY,
) -> tuple[Path, str, str, dict[str, str]]:
    """Reuse the B78 real reserve path, including its frozen finalization boundary."""
    cutoff = datetime.combine(day, time(8, 30), tzinfo=SHANGHAI)
    deadline = cutoff.replace(hour=9, minute=20)
    database = root / "partial-morning.sqlite"
    config_id, config_revision, execution_id, execution_revision = base.seed_database(
        database, trading_day=day - timedelta(days=1), fixture_now=cutoff - timedelta(hours=1),
    )
    configuration = store.read_run_config(config_id=config_id, revision=config_revision, db_path=database)["payload"]
    # The serial profile is frozen into the isolated strategy before the real
    # CLI producer creates its task binding.  No report or task state is
    # written by this fixture.
    execution_payload = dict(store.read_execution_config(
        config_id=execution_id, revision=execution_revision, db_path=database,
    )["payload"])
    execution_payload["discovery"] = {**execution_payload["discovery"], "deepReadConcurrency": 1}
    execution_revision = store.append_execution_config(
        config_id=execution_id, payload=execution_payload,
        created_at=(cutoff - timedelta(minutes=2)).isoformat(), db_path=database,
    )
    execution = store.read_execution_config(config_id=execution_id, revision=execution_revision, db_path=database)
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT content_json FROM k10_v2_strategy_snapshots WHERE snapshot_id=?", ("k10-v2-20260909",),
        ).fetchone()
        assert row is not None
        content = json.loads(row[0])
        content.update({"executionConfigId": execution_id, "executionConfigRevision": execution_revision,
                        "executionSha256": execution["contentSha256"]})
        connection.execute(
            "UPDATE k10_v2_strategy_snapshots SET execution_config_id=?,execution_config_revision=?,content_json=? "
            "WHERE snapshot_id=?",
            (execution_id, execution_revision,
             json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")), "k10-v2-20260909"),
        )
    envelope = pipeline._morning_finalization_reserve(configuration=configuration, execution_profile=execution)
    finalization_at = deadline - envelope
    research_closeout_at = finalization_at - envelope
    business_clock = [research_closeout_at - timedelta(seconds=1)]

    class CloseAfterFirstRoundTransport(DirectRoundTransport):
        advanced = False

        def respond(self, request):
            payload = self._packet(request)
            title_items = payload.get("items")
            if ("inputCount" not in payload and isinstance(title_items, list) and title_items
                    and all(isinstance(row, dict) and isinstance(row.get("title"), str) for row in title_items)):
                self._record("titleBatch")
                return self._ok({"items": [
                    {"i": index, "status": "candidate", "matterKey": f"matter-{self._title_number(row)}",
                     "stageKey": "new", "reason": "标题含新事件",
                     "companyCodes": [self.company_codes[self._title_number(row)]] if self._title_number(row) < 2 else []}
                    for index, row in enumerate(title_items)
                ]})
            response = super().respond(request)
            if payload.get("action") == "research_round" and not self.advanced:
                self.advanced = True
                business_clock[0] = finalization_at
            return response

    output = StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(base, "TITLE_COUNT", 3)
        patch.setattr(base, "DeterministicTransport", CloseAfterFirstRoundTransport)
        transport, _ = base.install_offline_transports(
            patch, refusal_event=None, selected_event_count=2, fixture_run_at=business_clock[0],
        )
        patch.setattr(pipeline, "_now", lambda: business_clock[0])
        patch.setattr(socket.socket, "connect", base._deny_network)
        patch.setattr(socket.socket, "connect_ex", base._deny_network)
        provider = MeteredProvider(
            ledger_db=database, ledger_task="discovery", api_key="fixture", model="deepseek-v4-pro",
            name="fixture", api_url="https://fixture.invalid/v1/chat/completions", read_timeout=1,
            use_streaming=False,
        )
        patch.setattr(pipeline, "resolve_deepseek_v4_pro",
                      lambda **_kwargs: ProviderResolution("configured", provider, "fixture", None))
        with redirect_stdout(output):
            assert cli_main([
                "enqueue", "--db", str(database), "--kind", "morning", "--trading-day", day.isoformat(),
                "--config-id", config_id, "--config-revision", str(config_revision),
                "--execution-config-id", execution_id, "--execution-config-revision", str(execution_revision),
            ]) == 0
        task_id = output.getvalue().strip()

        def handler(context):
            return pipeline.production_scan_handler(
                replace(context, clock=lambda: business_clock[0]), tushare_token="fixture-token",
                parquet_dir=root / "partial-morning-parquet", now=lambda: business_clock[0],
            )

        task = run_once(
            db_path=database, task_id=task_id, worker_id="b82-api-handoff", lease_for=timedelta(minutes=5),
            handlers={"morning_scan": handler}, clock=lambda: datetime.now(SHANGHAI), require_b76_contract=True,
        )
        assert task is not None and task.status == "completed"
        assert sum(call == "research:research_round" for call, _ in transport.calls) == 1
        assert sum(call == "prioritize" for call, _ in transport.calls) == 1
    with sqlite3.connect(database) as connection:
        event = connection.execute(
            "SELECT status,safe_error_code FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND item_kind='event' ORDER BY item_key", (task_id,),
        ).fetchall()
        assert sorted(event) == [("completed", None), ("failed", "morning_closeout_reserve")]
        assert connection.execute("SELECT COUNT(*) FROM k10_external_attempts WHERE state IN ('started','unknown')").fetchone()[0] == 0
    return database, task_id, output.getvalue(), {
        "businessStartAt": (research_closeout_at - timedelta(seconds=1)).isoformat(),
        "researchCloseoutAt": research_closeout_at.isoformat(),
        "finalizationAt": finalization_at.isoformat(),
        "deliveryDeadlineAt": deadline.isoformat(),
    }


def _start_command(root: Path, record: Mapping[str, Any]) -> str:
    bindings = record["bindings"]
    assert isinstance(bindings, Mapping)
    config_id = str(bindings["config_id"])
    config_revision = int(bindings["config_revision"])
    execution_id = str(bindings["execution_id"])
    execution_revision = int(bindings["execution_revision"])
    database = root / str(record["database"])
    parquet = root / f"{record['state']}-parquet"
    return (
        "cd Backend && env PYTHON_DOTENV_DISABLED=1 "
        f"DB_PATH='{database}' PARQUET_DIR='{parquet}' API_TOKEN='{_FIXTURE_TOKEN}' "
        f"K10_CONFIG_ID='{config_id}' K10_CONFIG_REVISION='{config_revision}' "
        f"K10_EXECUTION_CONFIG_ID='{execution_id}' K10_EXECUTION_CONFIG_REVISION='{execution_revision}' "
        ".venv/bin/python -m uvicorn neckline.api.app:app --host 127.0.0.1 --port 8751"
    )


def _clean_closed_fixture_sidecars(database: Path) -> None:
    """Checkpoint only the test-owned database, then leave no redundant WAL files."""
    with sqlite3.connect(database) as connection:
        busy, _log, _checkpointed = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    assert busy == 0
    for suffix in ("-wal", "-shm"):
        sidecar = database.with_name(database.name + suffix)
        if sidecar.exists():
            sidecar.unlink()


def _clean_fixture_diagnostics(root: Path) -> None:
    """The rejected synthetic reply is neither a handoff input nor evidence to retain."""
    directory = root / "provider-diagnostics"
    if not directory.exists():
        return
    for path in sorted(directory.rglob("*"), reverse=True):
        if path.is_file():
            assert path.suffix == ".json"
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    directory.rmdir()


def _refresh_owned_handoff_artifacts(root: Path, manifest: Path) -> None:
    """Replace only a manifest-confirmed, closed B82 fixture set on request."""
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload.get("generator") == _GENERATOR
    expected = {"complete-evening.sqlite", "partial-morning.sqlite", "materials-evening.sqlite", _MANIFEST_NAME}
    sqlite_sidecars = {name + suffix for name in expected if name.endswith(".sqlite") for suffix in ("-wal", "-shm")}
    parquet_directories = {"complete-evening-parquet", "partial-morning-parquet", "materials-evening-parquet"}
    for path in root.iterdir():
        if path.name in expected or path.name in sqlite_sidecars or path.name == "provider-diagnostics" or path.name in parquet_directories:
            continue
        raise AssertionError(f"refuse to refresh unknown handoff artifact: {path.name}")
    for database in (root / name for name in expected if name.endswith(".sqlite")):
        for path in (database, database.with_name(database.name + "-wal"), database.with_name(database.name + "-shm")):
            if path.exists():
                path.unlink()
    for directory in (root / "provider-diagnostics", *(root / name for name in parquet_directories)):
        if directory.exists():
            shutil.rmtree(directory)
    manifest.unlink()


def _write_manifest(root: Path, records: list[dict[str, Any]]) -> None:
    manifest = {
        "contract": "B82 offline CLI -> worker -> production handler -> SQLite -> FastAPI handoff",
        "network": "denied; deterministic model/search transports only",
        "generator": _GENERATOR,
        "bearerToken": _FIXTURE_TOKEN,
        "retention": "Preserve until both native populated-state acceptance runs have consumed these B82 fixtures; no process is left running.",
        "states": [{**record, "startCommand": _start_command(root, record)} for record in records],
    }
    (root / _MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _verify_retained(root: Path) -> list[dict[str, Any]]:
    manifest_path = root / _MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["generator"] == _GENERATOR
    assert manifest["bearerToken"] == _FIXTURE_TOKEN
    records = manifest["states"]
    assert [record["state"] for record in records] == ["complete-evening", "partial-morning", "materials-evening"]
    for record in records:
        database = root / record["database"]
        assert database.is_file()
        latest, materials = _api_projection(database, window=record["window"])
        observed = _record(
            label=record["state"], database=database, task_id=record["taskId"], task_status=record["taskStatus"],
            cli_stdout=record["cliStdout"], business_times=record["businessTimes"], latest=latest, materials=materials,
        )
        for key in ("reportId", "parentReportId", "cutoffAt", "deliveryDeadlineAt", "formalCardCount",
                    "materialsCount", "eventCount", "gapReasons", "outboxCount", "bindings"):
            assert observed[key] == record[key]
        assert record["startCommand"] == _start_command(root, record)
    return records


def test_b82_cli_worker_fastapi_handoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create complete/partial/materials-only artifacts through real producer boundaries."""
    root = _handoff_root(tmp_path)
    manifest = root / _MANIFEST_NAME
    if manifest.exists():
        if os.environ.get(_REFRESH_ENV) != "1":
            _verify_retained(root)
            for record in json.loads(manifest.read_text(encoding="utf-8"))["states"]:
                _clean_closed_fixture_sidecars(root / record["database"])
            _clean_fixture_diagnostics(root)
            return
        _refresh_owned_handoff_artifacts(root, manifest)
    root.mkdir(parents=True, exist_ok=True)
    assert not list(root.glob("*.sqlite")), "refuse to replace an unmanifested handoff database"

    complete_flow, complete_stdout = _capture_evening_flow(
        root, monkeypatch, name="complete-evening", trading_day=_CURRENT_EVENING_TRADING_DAY,
        fixture_now=_CURRENT_EVENING_HANDLER_AT - timedelta(hours=2), fixture_run_at=_CURRENT_EVENING_HANDLER_AT,
    )
    complete_latest, complete_materials = _api_projection(complete_flow.db_path, window="evening")
    complete_report = complete_latest["report"]
    assert complete_report["status"] == "completed" and complete_report["availableAt"]
    assert complete_report["deliveryDeadlineAt"] is None and _formal_card_count(complete_report) > 0
    for card in complete_report["eveningCards"]:
        assert card["d1TradeDate"] == "2026-09-22" and card["d2TradeDate"] == "2026-09-23"
        assert card["canSelect"] is True
    complete = _record(
        label="complete-evening", database=complete_flow.db_path, task_id=complete_flow.task_id,
        task_status=complete_flow.task_status, cli_stdout=complete_stdout,
        business_times={"handlerAt": _CURRENT_EVENING_HANDLER_AT.isoformat()}, latest=complete_latest,
        materials=complete_materials,
    )

    partial_database, partial_task_id, partial_stdout, partial_times = _capture_morning_partial(root, monkeypatch)
    partial_latest, partial_materials = _api_projection(partial_database, window="morning")
    partial_report = partial_latest["report"]
    assert partial_report["status"] == "partial" and partial_report["availableAt"]
    assert partial_report["parentReportId"] is None
    assert datetime.fromisoformat(partial_report["deliveryDeadlineAt"]) == datetime.fromisoformat(
        partial_times["deliveryDeadlineAt"],
    )
    assert [card["companyCode"] for card in partial_report["addedCards"]] == ["300002.SZ"]
    for card in partial_report["addedCards"]:
        assert card["d1TradeDate"] == "2026-09-22" and card["d2TradeDate"] == "2026-09-23"
        assert card["canSelect"] is True
    assert _gap_reasons(partial_report) == ["morning_closeout_reserve"]
    partial = _record(
        label="partial-morning", database=partial_database, task_id=partial_task_id, task_status="completed",
        cli_stdout=partial_stdout, business_times=partial_times, latest=partial_latest, materials=partial_materials,
    )

    materials_flow, materials_stdout = _capture_evening_flow(
        root, monkeypatch, name="materials-evening", refusal_operation="prioritize", expect_handler_failure=True,
    )
    materials_latest, materials_payload = _api_projection(materials_flow.db_path, window="evening")
    materials_report = materials_latest["report"]
    assert materials_flow.task_status == "failed"
    assert materials_report["status"] == "failed" and materials_report["availableAt"] is None
    assert _formal_card_count(materials_report) == 0 and materials_payload["items"]
    assert materials_report["materials"]["state"] == "available"
    materials = _record(
        label="materials-evening", database=materials_flow.db_path, task_id=materials_flow.task_id,
        task_status=materials_flow.task_status, cli_stdout=materials_stdout,
        business_times={"handlerAt": base.RUN_AT.isoformat()}, latest=materials_latest, materials=materials_payload,
    )

    records = [complete, partial, materials]
    for record in records:
        _clean_closed_fixture_sidecars(root / record["database"])
    _clean_fixture_diagnostics(root)
    _write_manifest(root, records)
    _verify_retained(root)
