"""A real morning producer owns its reviews and preserves the evening report."""
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import date, datetime, timedelta
from io import StringIO
import json
import os
from pathlib import Path
import socket
import sqlite3

import httpx

from neckline.k10 import morning_runtime, pipeline, store
from neckline.k10.cli import main as cli_main
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.windows import SHANGHAI
from neckline.k10.worker import run_once
from . import v340_acceptance_fixture as base
from .test_v350_cli_api import DirectRoundTransport, explicit_bindings
from .test_v340_morning_end_to_end import MorningChildRefusalTransport


def test_morning_review_failure_stays_in_parent_and_keeps_evening_readable(tmp_path, monkeypatch):
    root = Path(os.environ.get("NECKLINE_B78_MORNING_OUTPUT", str(tmp_path)))
    root.mkdir(parents=True, exist_ok=True)
    assert not (root / "morning.sqlite").exists(), "refuse to overwrite acceptance database"
    morning_at = datetime(2026, 9, 16, 8, 35, tzinfo=SHANGHAI)
    monkeypatch.setattr(base, "TITLE_COUNT", 130)
    monkeypatch.setattr(base, "DeterministicTransport", DirectRoundTransport)
    monkeypatch.setattr(socket.socket, "connect", base._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", base._deny_network)
    evening = base.run_full_scale_flow(root, monkeypatch, name="morning", selected_event_count=1,
        trading_day=date(2026, 9, 15), fixture_now=datetime(2026, 9, 15, 12, tzinfo=SHANGHAI),
        fixture_run_at=datetime(2026, 9, 15, 22, tzinfo=SHANGHAI))
    assert evening.task_status == "completed"
    db = evening.db_path
    binding = explicit_bindings(db)
    with base.actual_api(db, **binding) as client:
        evening_before = client.get("/api/v1/k10/v2/reports/latest?window=evening").json()["report"]
    assert evening_before["eveningCards"]
    transport, _ = base.install_offline_transports(monkeypatch, refusal_event=None,
        selected_event_count=1, fixture_run_at=morning_at)
    refusals = MorningChildRefusalTransport(transport)

    def offline_client(**kwargs):
        if kwargs.get("transport") is not None:
            return base._REAL_HTTPX_CLIENT(**kwargs)
        return base._REAL_HTTPX_CLIENT(**{**kwargs, "transport": httpx.MockTransport(refusals.respond)})

    monkeypatch.setattr(httpx, "Client", offline_client)
    provider = MeteredProvider(ledger_db=db, ledger_task="morning", api_key="fixture",
        model="deepseek-v4-pro", name="fixture", api_url="https://fixture.invalid/v1/chat/completions",
        read_timeout=1, use_streaming=False)
    provider.max_attempts = 1
    resolution = lambda **kwargs: ProviderResolution("configured", provider, "fixture", None)
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", resolution)
    monkeypatch.setattr(morning_runtime, "resolve_deepseek_v4_pro", resolution)
    stdout = StringIO()
    with redirect_stdout(stdout):
        assert cli_main(["enqueue", "--db", str(db), "--kind", "morning", "--trading-day", "2026-09-16",
            "--config-id", binding["config_id"], "--config-revision", str(binding["config_revision"]),
            "--execution-config-id", binding["execution_id"],
            "--execution-config-revision", str(binding["execution_revision"])]) == 0
    task_id = stdout.getvalue().strip()
    frozen = store.get_task(task_id=task_id, db_path=db)
    frozen_input = store.task_execution_input(task_id=task_id, db_path=db)
    assert datetime.fromisoformat(frozen_input["inputCutoffAt"]).astimezone(SHANGHAI).strftime("%H:%M") == "08:30"
    assert datetime.fromisoformat(frozen.payload["deliveryDeadlineAt"]).astimezone(SHANGHAI).strftime("%H:%M") == "09:20"

    def morning_handler(context):
        # Provider admission uses the explicit business clock. The captured
        # require_lease closure still checks the real worker's live lease.
        return pipeline.production_scan_handler(replace(context, clock=lambda: morning_at), tushare_token="fixture-token",
            parquet_dir=root / "morning-parquet", now=lambda: morning_at)

    task = run_once(db_path=db, task_id=task_id, worker_id="b78-morning-acceptance",
        lease_for=timedelta(minutes=5), handlers={"morning_scan": morning_handler},
        clock=lambda: datetime.now(SHANGHAI), require_b76_contract=True)
    assert task and task.status == "completed", {"task": task, "calls": transport.calls}
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_tasks WHERE kind='morning_review'").fetchone()[0] == 0
        items = conn.execute("SELECT work_item_id,status FROM k10_morning_review_work_items").fetchall()
        assert items and all(row[1] == "failed" for row in items)
        assert conn.execute("SELECT COUNT(*) FROM k10_external_attempts WHERE state IN ('started','unknown')").fetchone()[0] == 0
    assert refusals.child_requests == len(items)
    with base.actual_api(db, **binding) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=morning")
        response.raise_for_status()
        payload = response.json()
        preserved = client.get("/api/v1/k10/v2/reports/latest?window=evening").json()["report"]
    assert payload["schemaVersion"] == 9 and payload["state"] == "available"
    report = payload["report"]
    assert report["windowKind"] == "morning" and report["status"] == "partial"
    assert report["availableAt"] and report["coverageGaps"] and report["incompleteReviews"]
    assert preserved["reportId"] == evening_before["reportId"]
    assert preserved["eveningCards"] == evening_before["eveningCards"]
    if os.environ.get("NECKLINE_B78_MORNING_OUTPUT"):
        (root / "morning-report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
