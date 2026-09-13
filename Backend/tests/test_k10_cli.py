from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3

import pytest

from neckline.k10 import store
from neckline.k10.cli import configure, enqueue_scan, main
from neckline.k10.notifications import initialize_notifications_schema
from neckline.k10.pipeline import execute_scan
from neckline.k10.schema import initialize_schema
from neckline.k10.worker import run_once
from neckline.k10.windows import SHANGHAI
from tests.test_k10_pipeline import (_Adapter, _FixtureVerificationGateway, _Metadata, _VerifiedModel,
                                    _configuration, _watermark)
from tests.k10_v306_fixture import append_approved_execution_profile


DAY = date(2026, 9, 7)
NOW = datetime(2026, 9, 7, 20, tzinfo=SHANGHAI)


def _db(path: Path) -> int:
    initialize_schema(path)
    store.set_run_control(state="open", reason_code="fixture_cli", changed_at=NOW.isoformat(), changed_by="test", db_path=path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES('SSE', ?, 1)", [("20260907",), ("20260908",), ("20260909",), ("20260910",)])
    return store.append_run_config(config_id="fixture", payload=_configuration(), created_at=NOW.isoformat(), db_path=path)


def _execution(path: Path) -> int:
    config_id, revision = append_approved_execution_profile(
        db_path=path, created_at=NOW.isoformat(), config_id="fixture-execution",
    )
    assert config_id == "fixture-execution"
    return revision


def test_configure_appends_validated_immutable_pack(tmp_path):
    path = tmp_path / "configure.sqlite"
    initialize_schema(path)
    pack = tmp_path / "k10.json"
    payload = {
        "configVersion": "k10-v1.4", "universe": "chinext", "excludeBaijiu": True,
        "hardExclusions": {"approved": True, "board": "chinext", "priceLimit": "none", "st": "exclude", "swL2Exclusions": ["801125.SI"]},
        "sourceAdapters": [{"key": "fixture", "lateArrivalReplaySeconds": 86400}],
        "modelRoutes": {"discovery": "deepseek-v4-pro", "analysis": "deepseek-v4-pro", "morning": "deepseek-v4-pro"},
        "taskPolicies": {"discovery": {"maxAttempts": 1, "modelMaxAttempts": 1, "timeoutSeconds": 30, "costLimit": 0, "maxSourceRequests": 1, "maxVerificationRequests": 1}},
    }
    pack.write_text(json.dumps(payload), encoding="utf-8")
    assert configure(db_path=path, config_id="K10-v1.4", file_path=pack, now=NOW) == ("K10-v1.4", 1)
    assert configure(db_path=path, config_id="K10-v1.4", file_path=pack, now=NOW) == ("K10-v1.4", 1)


def test_enqueue_is_idempotent_and_requires_official_calendar(tmp_path):
    path = tmp_path / "cli.sqlite"
    revision = _db(path)
    execution_revision = _execution(path)
    execution = {"execution_config_id": "fixture-execution", "execution_config_revision": execution_revision}
    first = enqueue_scan(db_path=path, kind="evening", trading_day=DAY, config_id="fixture", config_revision=revision, now=NOW, **execution)
    again = enqueue_scan(db_path=path, kind="evening", trading_day=DAY, config_id="fixture", config_revision=revision, now=NOW, **execution)
    task = store.get_task(task_id=first, db_path=path)
    assert first == again
    assert task.kind == "evening_scan"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT input_cutoff_at FROM k10_tasks WHERE task_id=?", (first,)).fetchone()[0] == "2026-09-07T21:00:00+08:00"

    with pytest.raises(RuntimeError, match="交易日历"):
        enqueue_scan(db_path=path, kind="morning", trading_day=date(2026, 9, 11), config_id="fixture", config_revision=revision, now=NOW, **execution)

    bootstrap = enqueue_scan(db_path=path, kind="evening", trading_day=DAY, config_id="fixture", config_revision=revision,
                             now=NOW, bootstrap_cutoff="2026-09-01T21:00:00+08:00", **execution)
    assert store.get_task(task_id=bootstrap, db_path=path).payload["sourceBootstrapCutoff"] == "2026-09-01T21:00:00+08:00"


def test_first_monday_evening_bootstrap_is_explicit_and_preserves_weekend_coverage(tmp_path):
    path = tmp_path / "first-monday.sqlite"
    revision = _db(path)
    execution_revision = _execution(path)
    task_id = enqueue_scan(
        db_path=path, kind="evening", trading_day=DAY, config_id="fixture", config_revision=revision,
        now=NOW, bootstrap_cutoff="2026-09-04T21:00:00+08:00",
        execution_config_id="fixture-execution", execution_config_revision=execution_revision,
    )
    task = store.get_task(task_id=task_id, db_path=path)
    assert task is not None
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT input_cutoff_at FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()[0] == "2026-09-07T21:00:00+08:00"
    assert task.payload["sourceBootstrapCutoff"] == "2026-09-04T21:00:00+08:00"


def test_cli_enqueue_closed_calendar_is_a_successful_noop(tmp_path, capsys):
    path = tmp_path / "cli-closed.sqlite"
    revision = _db(path)
    execution_revision = _execution(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO trade_cal VALUES('SSE', '20260912', 0)")

    assert main(["enqueue", "--db", str(path), "--kind", "evening", "--trading-day", "2026-09-11",
                 "--config-id", "fixture", "--config-revision", str(revision),
                 "--execution-config-id", "fixture-execution", "--execution-config-revision", str(execution_revision)]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "not_trading_day", "tradingDay": "2026-09-11", "calendarDay": "2026-09-12"}
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_tasks").fetchone()[0] == 0


def test_cli_enqueue_missing_calendar_still_fails(tmp_path):
    path = tmp_path / "cli-missing-calendar.sqlite"
    revision = _db(path)
    execution_revision = _execution(path)

    with pytest.raises(RuntimeError, match="交易日历缺覆盖"):
        main(["enqueue", "--db", str(path), "--kind", "evening", "--trading-day", "2026-09-11",
              "--config-id", "fixture", "--config-revision", str(revision),
              "--execution-config-id", "fixture-execution", "--execution-config-revision", str(execution_revision)])


def test_cli_worker_once_with_empty_queue_constructs_production_handlers_without_network(tmp_path, monkeypatch):
    path = tmp_path / "empty-worker.sqlite"
    initialize_schema(path)
    initialize_notifications_schema(path)
    monkeypatch.setenv("TUSHARE_TOKEN", "unused-for-empty-queue")

    assert main([
        "worker", "--db", str(path), "--parquet-dir", str(tmp_path / "parquet"),
        "--worker-id", "empty-worker", "--tushare-token-env", "TUSHARE_TOKEN", "--once",
    ]) == 0
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_tasks").fetchone() == (0,)


def test_cli_enqueue_then_worker_handler_reaches_source_event_and_candidate(tmp_path, capsys):
    path = tmp_path / "cli-worker.sqlite"
    revision = _db(path)
    execution_revision = _execution(path)
    _watermark(path)
    assert main(["enqueue", "--db", str(path), "--kind", "evening", "--trading-day", "2026-09-07",
                 "--config-id", "fixture", "--config-revision", str(revision),
                 "--execution-config-id", "fixture-execution", "--execution-config-revision", str(execution_revision)]) == 0
    task_id = capsys.readouterr().out.strip()
    execution_binding = store.task_execution_profile(task_id=task_id, db_path=path)
    assert execution_binding is not None
    assert execution_binding["configId"] == "fixture-execution"
    assert execution_binding["revision"] == execution_revision
    assert execution_binding["bindingKind"] == "scheduled"
    adapter = _Adapter()

    def evening(context):
        config = store.read_run_config(config_id="fixture", revision=revision, db_path=path)
        return execute_scan(kind="evening", cutoff_at=datetime.fromisoformat(context.input_cutoff_at),
                            configuration=config["payload"], db_path=path, adapter=adapter, model=_VerifiedModel(),
                            metadata=_Metadata(), created_at=NOW + timedelta(minutes=6), config_id="fixture",
                            config_revision=revision, publication_clock=lambda: NOW + timedelta(minutes=6),
                            verification_gateway=_FixtureVerificationGateway())

    task = run_once(db_path=path, worker_id="fixture-worker", lease_for=timedelta(minutes=5),
                    handlers={"evening_scan": evening}, clock=lambda: NOW + timedelta(minutes=1))
    assert task.task_id == task_id
    assert task.status == "completed"
    with sqlite3.connect(path) as conn:
        checkpoint = json.loads(conn.execute("SELECT checkpoint_json FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()[0])
    assert len(store.list_candidates(scan_id=checkpoint["scanId"], state="offered", db_path=path)) == 1
    assert adapter.request is not None
