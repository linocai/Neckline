from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
import sqlite3

import pytest

from neckline.k10 import morning_runtime, pipeline, store
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from tests import v340_acceptance_fixture as base
from tests.test_v350_cli_api import DirectRoundTransport, explicit_bindings, generate_acceptance


def test_same_task_resume_keeps_frozen_review_catalogue(tmp_path, monkeypatch):
    evening = generate_acceptance(tmp_path / "producer", monkeypatch, scenario="complete")
    database = evening.database
    bindings = explicit_bindings(database)
    morning_time = datetime(2026, 9, 9, 8, 35, tzinfo=base.SHANGHAI)

    def resolver(**_kwargs):
        provider = MeteredProvider(
            ledger_db=database,
            ledger_task="discovery",
            api_key="fixture",
            model="deepseek-v4-pro",
            name="fixture",
            api_url="https://fixture.invalid/v1/chat/completions",
            read_timeout=1,
            use_streaming=False,
        )
        provider.max_attempts = 1
        return ProviderResolution("configured", provider, "fixture", None)

    monkeypatch.setattr(DirectRoundTransport, "loopback_morning_discovery_zero", True)
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", resolver)
    monkeypatch.setattr(morning_runtime, "resolve_deepseek_v4_pro", resolver)
    stdout = StringIO()
    with base.redirect_stdout(stdout):
        assert base.cli_main([
            "enqueue", "--db", str(database), "--kind", "morning", "--trading-day", "2026-09-09",
            "--config-id", bindings["config_id"], "--config-revision", str(bindings["config_revision"]),
            "--execution-config-id", bindings["execution_id"],
            "--execution-config-revision", str(bindings["execution_revision"]),
        ]) == 0
    task_id = stdout.getvalue().strip()

    original_review = morning_runtime.morning_review_handler

    def interrupt_review(*_args, **_kwargs):
        raise store.K10Conflict("reviewer injected lease loss after work-item reservation")

    monkeypatch.setattr(morning_runtime, "morning_review_handler", interrupt_review)

    def handler_at(instant):
        def handler(context):
            return pipeline.production_scan_handler(
                context,
                tushare_token="fixture-token",
                parquet_dir=tmp_path / "parquet",
                now=lambda: instant,
            )
        return handler

    with pytest.raises(store.K10Conflict):
        run_once(
            db_path=database,
            task_id=task_id,
            worker_id="b91-review-repro-first",
            lease_for=timedelta(minutes=5),
            handlers={"morning_scan": handler_at(morning_time)},
            clock=lambda: morning_time,
            require_b76_contract=True,
        )

    with sqlite3.connect(database) as connection:
        first_items = connection.execute(
            "SELECT work_item_id,input_sha256,status FROM k10_morning_review_work_items ORDER BY work_item_id"
        ).fetchall()
        first_docs = connection.execute("SELECT COUNT(*) FROM k10_source_document_versions").fetchone()[0]
        task_after_first = connection.execute(
            "SELECT status,stage,checkpoint_json FROM k10_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
    assert first_items and all(row[2] == "running" for row in first_items)
    assert task_after_first[0] == "running"

    monkeypatch.setattr(morning_runtime, "morning_review_handler", original_review)
    later = morning_time + timedelta(minutes=6)
    resumed = run_once(
        db_path=database,
        task_id=task_id,
        worker_id="b91-review-repro-second",
        lease_for=timedelta(minutes=5),
        handlers={"morning_scan": handler_at(later)},
        clock=lambda: later,
        require_b76_contract=True,
    )
    assert resumed is not None

    with sqlite3.connect(database) as connection:
        second_items = connection.execute(
            "SELECT work_item_id,input_sha256,status FROM k10_morning_review_work_items ORDER BY work_item_id"
        ).fetchall()
        second_docs = connection.execute("SELECT COUNT(*) FROM k10_source_document_versions").fetchone()[0]
        final_task = connection.execute(
            "SELECT status,stage,error_text,checkpoint_json FROM k10_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        reports = connection.execute(
            "SELECT report_id,status,available_at,error_json FROM k10_v2_report_runs WHERE window_kind='morning' ORDER BY created_at"
        ).fetchall()

    assert second_docs >= first_docs
    assert [(row[0], row[1]) for row in second_items] == [(row[0], row[1]) for row in first_items]
    assert final_task[0] == "completed" and final_task[1] in {"report_complete", "report_partial"}
    assert all(row[2] == "completed" for row in second_items)
