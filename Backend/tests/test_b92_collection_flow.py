"""B92 collector producer/worker integration with deterministic provider replies."""
from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from neckline.db import init_schema
from neckline.k10 import store
from neckline.k10.cli import main
from neckline.k10.collection_runtime import collection_config_for_report, create_collection_handler
from neckline.k10.collection_gateway import call_question_tool
from neckline.k10.jin10_mcp import Jin10Error
from neckline.k10.jin10_normalize import persist_question_tool_result
from neckline.k10 import schema
from neckline.k10.schema import SqliteWriteBusy, initialize_schema
from neckline.k10.worker import TaskResult, run_once
from neckline.api.collection import create_router
from neckline.api.k10 import create_router as create_k10_router


BASE_CONFIG = Path(__file__).resolve().parents[1] / "neckline/config/k10-collection-v1.json"


def _cli(*args: str) -> str:
    output = StringIO()
    with redirect_stdout(output):
        assert main(list(args)) == 0
    return output.getvalue().strip()


def _configured(tmp_path: Path) -> tuple[Path, str, int]:
    database = tmp_path / "collection.sqlite"
    init_schema(database)
    initialize_schema(database)
    payload = json.loads(BASE_CONFIG.read_text())
    for source in payload["sources"]:
        source["bootstrapStartAt"] = "2026-09-25T00:00:00+08:00"
    config_file = tmp_path / "collection.json"
    config_file.write_text(json.dumps(payload))
    binding = json.loads(_cli("configure-collection", "--db", str(database),
                              "--config-id", "flow-fixture", "--file", str(config_file)))
    _cli("collection-control", "--db", str(database), "--state", "open",
         "--config-id", binding["configId"], "--config-revision", str(binding["revision"]))
    store.set_run_control(state="closed", reason_code="user_paused",
                          changed_at="2026-09-25T00:00:00+08:00", changed_by="flow-fixture",
                          db_path=database)
    return database, binding["configId"], binding["revision"]


class FixtureJin10:
    endpoint = "https://mcp.jin10.com/mcp"
    protocol_version = "2025-11-25"
    published_at = "2026-09-25T00:00:00+08:00"
    calls: list[tuple[str, dict]] = []

    def __init__(self, **kwargs):
        assert kwargs["token"] == "fixture-token"

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def call_tool_raw(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        stamp = self.published_at
        item = {"id": name + ":" + stamp, "url": "https://jin10.com/" + name,
                "time": stamp, "title": None if name == "list_flash" else "测试文章",
                "content": "测试快讯正文" if name == "list_flash" else None,
                "intro": None if name == "list_flash" else "测试目录摘要"}
        return {"isError": False, "structuredContent": {"status": 200,
                "data": {"items": [item], "has_more": False, "next_cursor": None}}}

    def pagination_argument(self, name):
        return "cursor"


def _tushare(payload):
    stamp = payload["params"]["start_date"]
    return {"code": 0, "data": {"fields": ["pub_time", "src", "title", "content"],
            "items": [[stamp, "fixture", "测试通讯", "测试通讯全文"]]}}


def _run_slot(database: Path, config_id: str, revision: int, slot: str, published_at: str):
    FixtureJin10.published_at = published_at
    task_id = _cli("enqueue-collection", "--db", str(database), "--slot", slot,
                   "--config-id", config_id, "--config-revision", str(revision))
    clock = lambda: datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    result = run_once(db_path=database, worker_id="collection-fixture", lease_for=timedelta(minutes=5),
                      handlers={"collect_news": create_collection_handler(
                          tushare_token="fixture-token", jin10_token="fixture-token",
                          client_factory=FixtureJin10, tushare_request=_tushare)},
                      clock=clock, task_id=task_id, require_b76_contract=True)
    assert result is not None and result.task_id == task_id
    return task_id


def test_real_cli_worker_two_slots_preserve_historical_run_coverage(tmp_path):
    database, config_id, revision = _configured(tmp_path)
    assert store.run_control_status(db_path=database)["state"] == "closed"
    FixtureJin10.calls = []
    first = _run_slot(database, config_id, revision,
        "2026-09-25T08:00:00+08:00", "2026-09-25T00:00:00+08:00")
    first_checkpoint = store.task_execution_input(task_id=first, db_path=database)["checkpoint"]
    assert all(source["coverageThrough"] == "2026-09-25T08:00:00+08:00"
               for source in first_checkpoint["sources"].values())
    second = _run_slot(database, config_id, revision,
        "2026-09-25T20:00:00+08:00", "2026-09-25T07:00:00+08:00")
    assert first != second and len(FixtureJin10.calls) == 4
    assert store.run_control_status(db_path=database)["state"] == "closed"
    assert store.get_task(task_id=first, db_path=database).status == "completed"
    assert store.get_task(task_id=second, db_path=database).status == "completed"
    app = FastAPI()
    app.include_router(create_router(db_path_provider=lambda: database,
        require_token_dependency=lambda: None,
        current_collection_binding_provider=lambda: (config_id, revision, None)))
    with TestClient(app) as client:
        response = client.get("/api/v1/k10/collection/status")
    assert response.status_code == 200
    data = response.json()
    assert datetime.fromisoformat(data["sources"][0]["coverageThrough"]) == datetime.fromisoformat(
        "2026-09-25T20:00:00+08:00")
    assert [run["taskId"] for run in data["latestRuns"]] == [second, first]
    for run, expected in zip(data["latestRuns"], ("20:00:00", "08:00:00")):
        assert all(source["coverageThrough"].endswith(expected + "+08:00")
                   for source in run["sourceOutcomes"])
    assert all(item["sourceKey"] in {"tushare-major-news", "jin10-flash", "jin10-news"}
               for item in data["sources"])


def test_closed_report_and_unknown_kind_do_not_block_collection_queue(tmp_path):
    database, config_id, revision = _configured(tmp_path)
    store.enqueue_task(task_id="unknown-b92-kind", kind="unrecognized_work",
        idempotency_key="unknown-b92-kind", input_version="fixture",
        input_cutoff_at="2026-09-25T08:00:00+08:00", payload={}, budget={"maxAttempts": 1},
        created_at="2026-09-25T07:00:00+08:00", db_path=database)
    FixtureJin10.calls = []
    collection = _run_slot(database, config_id, revision,
        "2026-09-25T08:00:00+08:00", "2026-09-25T00:00:00+08:00")
    assert store.get_task(task_id=collection, db_path=database).status == "completed"
    assert store.get_task(task_id="unknown-b92-kind", db_path=database).status == "queued"
    assert run_once(db_path=database, worker_id="unknown-check", lease_for=timedelta(minutes=5),
        handlers={"unrecognized_work": lambda _context: (_ for _ in ()).throw(AssertionError())},
        clock=lambda: datetime(2026, 9, 26, 12, tzinfo=timezone.utc)) is None


def test_closed_before_send_can_retry_same_wire_after_reopen(tmp_path):
    database, config_id, revision = _configured(tmp_path)
    task_id = _cli("enqueue-collection", "--db", str(database),
        "--slot", "2026-09-25T08:00:00+08:00", "--config-id", config_id,
        "--config-revision", str(revision))
    FixtureJin10.calls = []
    client = FixtureJin10(token="fixture-token")
    clock = lambda: datetime(2026, 9, 26, 12, tzinfo=timezone.utc)

    def close_before_send():
        store.set_collection_control(state="closed", reason_code="user_paused",
            changed_at=clock().isoformat(), changed_by="fixture", db_path=database)
        raise RuntimeError("closed")

    def handler(context):
        request = dict(task_id=context.task.task_id, question="同一文章是什么", target="wire-target",
            purpose="first", tool_name="list_news", arguments={}, db_path=database,
            client=client, clock=clock, leaseguard=context.require_lease)
        try:
            call_question_tool(**request, new_external_admission_guard=close_before_send)
            raise AssertionError("关闭后不应发送")
        except Jin10Error as exc:
            assert exc.code == "collection_paused"
        assert FixtureJin10.calls == []
        store.set_collection_control(state="open", reason_code="operator_open",
            changed_at=clock().isoformat(), changed_by="fixture", db_path=database)
        first = call_question_tool(**request)
        second = call_question_tool(**{**request, "question": "另一问题共用同一 wire",
                                       "purpose": "second"})
        assert first == second
        return TaskResult("completed", "fixture_done", context.checkpoint)

    result = run_once(db_path=database, task_id=task_id, worker_id="retry-fixture",
        lease_for=timedelta(minutes=5), handlers={"collect_news": handler},
        clock=clock, require_b76_contract=True)
    assert result is not None and result.status == "completed"
    assert len(FixtureJin10.calls) == 1
    checkpoint = store.task_execution_input(task_id=task_id, db_path=database)["checkpoint"]
    assert len(checkpoint["toolReceipts"]) == 1
    assert sorted(len(value) for value in checkpoint["toolQuestionLinks"].values()) == [2]


def test_unknown_sent_tool_outcome_cannot_be_rebilled(tmp_path):
    database, config_id, revision = _configured(tmp_path)
    task_id = _cli("enqueue-collection", "--db", str(database),
        "--slot", "2026-09-25T08:00:00+08:00", "--config-id", config_id,
        "--config-revision", str(revision))
    clock = lambda: datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    class UnknownClient(FixtureJin10):
        count = 0
        def call_tool_raw(self, name, arguments):
            self.count += 1
            raise Jin10Error("transport_unknown", unknown=True)
    client = UnknownClient(token="fixture-token")
    def handler(context):
        request = dict(task_id=context.task.task_id, question="原件在哪里", target="wire-target",
            purpose="fixture", tool_name="list_news", arguments={}, db_path=database,
            client=client, clock=clock, leaseguard=context.require_lease)
        for _ in range(2):
            try:
                call_question_tool(**request)
            except Jin10Error as exc:
                assert exc.unknown
        return TaskResult("failed", "pending_outcome", context.checkpoint, "付费结果未知")
    result = run_once(db_path=database, task_id=task_id, worker_id="unknown-fixture",
        lease_for=timedelta(minutes=5), handlers={"collect_news": handler},
        clock=clock, require_b76_contract=True)
    assert result is not None and result.status == "failed" and client.count == 1
    with store.read_connection(database) as conn:
        states = [row[0] for row in conn.execute(
            "SELECT state FROM k10_external_attempts WHERE task_id=?", (task_id,))]
    assert states == ["unknown"]


def test_received_jin10_reply_recovers_one_local_sqlite_settlement_without_rebilling(tmp_path, monkeypatch):
    database, config_id, revision = _configured(tmp_path)
    task_id = _cli("enqueue-collection", "--db", str(database),
        "--slot", "2026-09-25T08:00:00+08:00", "--config-id", config_id,
        "--config-revision", str(revision))
    monkeypatch.setattr(schema, "_WRITE_BEGIN_ATTEMPTS", 1)
    monkeypatch.setattr(schema, "_WRITE_BEGIN_CONNECTION_TIMEOUT_SECONDS", 0.01)
    original_settle = store.settle_tool_attempt
    settlements = 0
    actual_busy = False

    def busy_first_settlement(**kwargs):
        nonlocal settlements, actual_busy
        settlements += 1
        if settlements != 1:
            return original_settle(**kwargs)
        blocker = sqlite3.connect(database)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            return original_settle(**kwargs)
        except SqliteWriteBusy:
            actual_busy = True
            raise
        finally:
            blocker.rollback()
            blocker.close()

    monkeypatch.setattr(store, "settle_tool_attempt", busy_first_settlement)
    FixtureJin10.calls = []
    client = FixtureJin10(token="fixture-token")
    clock = lambda: datetime(2026, 9, 26, 12, tzinfo=timezone.utc)

    def handler(context):
        request = dict(task_id=context.task.task_id, question="文章的正文是什么", target="paid-wire",
            purpose="fixture", tool_name="list_news", arguments={}, db_path=database,
            client=client, clock=clock, leaseguard=context.require_lease)
        first = call_question_tool(**request)
        second = call_question_tool(**{**request, "question": "另一问题共用已付费原回复"})
        assert first == second and first["status"] == 200
        return TaskResult("completed", "fixture_done", context.checkpoint)

    task = run_once(db_path=database, task_id=task_id, worker_id="local-settlement",
        lease_for=timedelta(minutes=5), handlers={"collect_news": handler},
        clock=clock, require_b76_contract=True)
    assert task is not None and task.status == "completed"
    assert actual_busy and settlements == 2 and len(FixtureJin10.calls) == 1
    checkpoint = store.task_execution_input(task_id=task_id, db_path=database)["checkpoint"]
    assert len(checkpoint["toolReceipts"]) == 1
    assert sorted(len(links) for links in checkpoint["toolQuestionLinks"].values()) == [2]
    with store.read_connection(database) as conn:
        assert conn.execute("SELECT state,COUNT(*) FROM k10_external_attempts WHERE task_id=? GROUP BY state",
                            (task_id,)).fetchall() == [("succeeded", 1)]


def test_persistent_sqlite_busy_after_jin10_reply_keeps_one_started_attempt(tmp_path, monkeypatch):
    database, config_id, revision = _configured(tmp_path)
    task_id = _cli("enqueue-collection", "--db", str(database),
        "--slot", "2026-09-25T08:00:00+08:00", "--config-id", config_id,
        "--config-revision", str(revision))
    monkeypatch.setattr(schema, "_WRITE_BEGIN_ATTEMPTS", 1)
    monkeypatch.setattr(schema, "_WRITE_BEGIN_CONNECTION_TIMEOUT_SECONDS", 0.01)
    original_settle = store.settle_tool_attempt
    settlements = 0
    blocker = None

    def still_busy(**kwargs):
        nonlocal settlements, blocker
        settlements += 1
        if blocker is None:
            blocker = sqlite3.connect(database)
            blocker.execute("BEGIN IMMEDIATE")
        return original_settle(**kwargs)

    monkeypatch.setattr(store, "settle_tool_attempt", still_busy)
    FixtureJin10.calls = []
    client = FixtureJin10(token="fixture-token")
    clock = lambda: datetime(2026, 9, 26, 12, tzinfo=timezone.utc)

    def handler(context):
        request = dict(task_id=context.task.task_id, question="文章的正文是什么", target="paid-wire",
            purpose="fixture", tool_name="list_news", arguments={}, db_path=database,
            client=client, clock=clock, leaseguard=context.require_lease)
        try:
            with pytest.raises(SqliteWriteBusy):
                call_question_tool(**request)
        finally:
            assert blocker is not None
            blocker.rollback()
            blocker.close()
        with pytest.raises(Jin10Error) as pending:
            call_question_tool(**request)
        assert pending.value.unknown
        return TaskResult("failed", "pending_outcome", context.checkpoint, "回执落盘仍待人工核对")

    task = run_once(db_path=database, task_id=task_id, worker_id="busy-settlement",
        lease_for=timedelta(minutes=5), handlers={"collect_news": handler},
        clock=clock, require_b76_contract=True)
    assert task is not None and task.status == "failed"
    assert settlements == 2 and len(FixtureJin10.calls) == 1
    with store.read_connection(database) as conn:
        assert conn.execute("SELECT state,COUNT(*) FROM k10_external_attempts WHERE task_id=? GROUP BY state",
                            (task_id,)).fetchall() == [("started", 1)]


def test_uncertain_commit_rechecks_exact_jin10_receipt_without_second_settlement(tmp_path, monkeypatch):
    database, config_id, revision = _configured(tmp_path)
    task_id = _cli("enqueue-collection", "--db", str(database),
        "--slot", "2026-09-25T08:00:00+08:00", "--config-id", config_id,
        "--config-revision", str(revision))
    original_settle = store.settle_tool_attempt
    settlements = 0

    def committed_but_uncertain(**kwargs):
        nonlocal settlements
        settlements += 1
        original_settle(**kwargs)
        raise SqliteWriteBusy("fixture commit result was unknown to caller")

    monkeypatch.setattr(store, "settle_tool_attempt", committed_but_uncertain)
    FixtureJin10.calls = []
    client = FixtureJin10(token="fixture-token")
    clock = lambda: datetime(2026, 9, 26, 12, tzinfo=timezone.utc)

    def handler(context):
        request = dict(task_id=context.task.task_id, question="文章的正文是什么", target="paid-wire",
            purpose="fixture", tool_name="list_news", arguments={}, db_path=database,
            client=client, clock=clock, leaseguard=context.require_lease)
        first = call_question_tool(**request)
        second = call_question_tool(**{**request, "question": "已提交后的另一问题"})
        assert first == second and first["status"] == 200
        return TaskResult("completed", "fixture_done", context.checkpoint)

    task = run_once(db_path=database, task_id=task_id, worker_id="uncertain-commit",
        lease_for=timedelta(minutes=5), handlers={"collect_news": handler},
        clock=clock, require_b76_contract=True)
    assert task is not None and task.status == "completed"
    assert settlements == 1 and len(FixtureJin10.calls) == 1
    checkpoint = store.task_execution_input(task_id=task_id, db_path=database)["checkpoint"]
    assert len(checkpoint["toolReceipts"]) == 1
    with store.read_connection(database) as conn:
        assert conn.execute("SELECT state,COUNT(*) FROM k10_external_attempts WHERE task_id=? GROUP BY state",
                            (task_id,)).fetchall() == [("succeeded", 1)]


@pytest.mark.parametrize("first_error,expected_code,wait_seconds,pre_send,unknown", [
    ("rate_limited", "rate_limited", 31, False, False),
    ("protocol_version_mismatch", "pre_send_error", 0, True, False),
    ("sse_response_unknown", "pre_send_error", 0, True, True),
    ("session_expired", "session_expired", 0, False, False),
])
def test_known_refusal_and_handshake_failure_have_bounded_same_task_retry(
    tmp_path, first_error, expected_code, wait_seconds, pre_send, unknown,
):
    database, config_id, revision = _configured(tmp_path)
    task_id = _cli("enqueue-collection", "--db", str(database),
        "--slot", "2026-09-25T08:00:00+08:00", "--config-id", config_id,
        "--config-revision", str(revision))
    instant = [datetime(2026, 9, 26, 12, tzinfo=timezone.utc)]
    clock = lambda: instant[0]
    class RefusalClient(FixtureJin10):
        count = 0
        def call_tool_raw(self, name, arguments):
            self.count += 1
            if self.count == 1:
                raise Jin10Error(first_error, pre_send=pre_send, unknown=unknown)
            return super().call_tool_raw(name, arguments)
    client = RefusalClient(token="fixture-token")

    def handler(context):
        request = dict(task_id=context.task.task_id, question="这条文章是什么", target="same-wire",
            purpose="fixture", tool_name="list_news", arguments={}, db_path=database,
            client=client, clock=clock, leaseguard=context.require_lease)
        try:
            call_question_tool(**request)
            raise AssertionError("首个明确失败应上抛")
        except Jin10Error as exc:
            assert exc.code == first_error
        if wait_seconds:
            try:
                call_question_tool(**request)
                raise AssertionError("429 退避前不应发送")
            except Jin10Error as exc:
                assert exc.code == "retry_later"
            instant[0] += timedelta(seconds=wait_seconds)
        assert call_question_tool(**request)["status"] == 200
        return TaskResult("completed", "fixture_done", context.checkpoint)

    FixtureJin10.calls = []
    result = run_once(db_path=database, task_id=task_id, worker_id="known-retry-fixture",
        lease_for=timedelta(minutes=5), handlers={"collect_news": handler},
        clock=clock, require_b76_contract=True)
    assert result is not None and result.status == "completed"
    assert client.count == 2 and len(FixtureJin10.calls) == 1
    with store.read_connection(database) as conn:
        attempts = list(conn.execute(
            "SELECT state,error_code FROM k10_external_attempts WHERE task_id=? ORDER BY started_at",
            (task_id,)))
    assert sorted(tuple(row) for row in attempts) == sorted([
        ("failed", expected_code), ("succeeded", None)])


def test_freeze_mixed_collection_revisions_keeps_exact_question_binding(tmp_path, monkeypatch):
    # Keep producer creation and the explicit worker/freeze clocks in order;
    # wall-clock execution after September 27 must not hide both input tasks.
    class ProducerClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 26, 12, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr("neckline.k10.cli.datetime", ProducerClock)
    database, config_id, first_revision = _configured(tmp_path)
    _run_slot(database, config_id, first_revision,
        "2026-09-25T08:00:00+08:00", "2026-09-25T00:00:00+08:00")
    config_file = tmp_path / "collection-v2.json"
    config_payload = json.loads((tmp_path / "collection.json").read_text())
    config_payload["sources"][0]["maxPages"] = 64
    config_file.write_text(json.dumps(config_payload))
    second_revision = json.loads(_cli("configure-collection", "--db", str(database),
        "--config-id", config_id, "--file", str(config_file)))["revision"]
    assert second_revision != first_revision
    second_task = _cli("enqueue-collection", "--db", str(database),
        "--slot", "2026-09-25T20:00:00+08:00", "--config-id", config_id,
        "--config-revision", str(second_revision))
    def close_after_paid_reply(payload):
        result = _tushare(payload)
        store.set_collection_control(state="closed", reason_code="user_paused",
            changed_at="2026-09-26T12:00:00+00:00", changed_by="fixture", db_path=database)
        return result
    run_once(db_path=database, task_id=second_task, worker_id="partial-collection",
        lease_for=timedelta(minutes=5),
        handlers={"collect_news": create_collection_handler(
            tushare_token="fixture-token", jin10_token="fixture-token",
            client_factory=FixtureJin10, tushare_request=close_after_paid_reply)},
        clock=lambda: datetime(2026, 9, 26, 12, tzinfo=timezone.utc), require_b76_contract=True)
    second_checkpoint = store.task_execution_input(task_id=second_task, db_path=database)["checkpoint"]
    assert list(second_checkpoint["sources"]) == ["tushare-major-news"]
    scan_id = "scan-b92-mixed-config"
    store.create_scan(scan_id=scan_id, window_kind="evening",
        cutoff_at="2026-09-25T21:00:00+08:00", config_id=None, config_revision=None,
        status="running", coverage={}, created_at="2026-09-26T13:00:00+00:00",
        completed_at=None, db_path=database)
    freeze_args = dict(db_path=database, scan_id=scan_id, window="evening",
        source_keys=("tushare-major-news", "jin10-flash", "jin10-news"),
        frozen_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
        bootstrap_at="2026-09-25T00:00:00+08:00")
    frozen = store.freeze_collected_input(**freeze_args)
    assert len(frozen["collectionTaskIds"]) == 2
    binding = frozen["questionToolConfigBinding"]
    assert binding["taskId"] == second_task and binding["revision"] == second_revision
    config = collection_config_for_report(db_path=database,
        collection_task_ids=frozen["collectionTaskIds"], binding=binding)
    assert config is not None and config["revision"] == second_revision
    config_payload["sources"][0]["maxPages"] = 32
    config_file.write_text(json.dumps(config_payload))
    third_revision = json.loads(_cli("configure-collection", "--db", str(database),
        "--config-id", config_id, "--file", str(config_file)))["revision"]
    assert third_revision > second_revision
    assert store.freeze_collected_input(**freeze_args) == frozen
    assert collection_config_for_report(db_path=database,
        collection_task_ids=frozen["collectionTaskIds"], binding=binding)["revision"] == second_revision


@pytest.mark.parametrize("page_parameter", ["cursor", "offset"])
def test_official_documented_jin10_aliases_keep_raw_flash_and_schema_page_parameter(
    tmp_path, page_parameter,
):
    """Fixture follows the public Jin10 demo, not a live provider probe."""
    database, config_id, revision = _configured(tmp_path)
    class OfficialShapeClient(FixtureJin10):
        calls = []
        def pagination_argument(self, name):
            return page_parameter if name == "list_flash" else "cursor"
        def call_tool_raw(self, name, arguments):
            self.calls.append((name, dict(arguments)))
            if name == "list_flash":
                first = not arguments
                item_id = "20260328134122893800" if first else "20260328134122893801"
                return {"structuredContent": {"status": 200, "data": {
                    "items": [{"id": item_id, "title": "", "content": "原始快讯完整内容",
                               "time": "2026-09-25T00:00:00+08:00",
                               "url": "https://flash.jin10.com/detail/" + item_id}],
                    "next_offset": "offset-one" if first else None,
                    "has_more": first}}}
            return {"structuredContent": {"status": 200, "data": {
                "items": [{"id": "214768", "title": "市场主线", "introduction": "官方简介正文",
                           "time": "2026-09-25T00:00:00+08:00",
                           "url": "https://xnews.jin10.com/details/214768"}],
                "next_offset": None, "has_more": False}}}
    OfficialShapeClient.calls = []
    task_id = _cli("enqueue-collection", "--db", str(database),
        "--slot", "2026-09-25T08:00:00+08:00", "--config-id", config_id,
        "--config-revision", str(revision))
    result = run_once(db_path=database, task_id=task_id, worker_id="official-shape",
        lease_for=timedelta(minutes=5), handlers={"collect_news": create_collection_handler(
            tushare_token="fixture-token", jin10_token="fixture-token",
            client_factory=OfficialShapeClient, tushare_request=_tushare)},
        clock=lambda: datetime(2026, 9, 26, 12, tzinfo=timezone.utc), require_b76_contract=True)
    assert result is not None and result.status == "completed"
    assert ("list_flash", {page_parameter: "offset-one"}) in OfficialShapeClient.calls
    checkpoint = store.task_execution_input(task_id=task_id, db_path=database)["checkpoint"]
    flash_ref = checkpoint["sources"]["jin10-flash"]["documentRefs"][0]
    news_ref = checkpoint["sources"]["jin10-news"]["documentRefs"][0]
    app = FastAPI()
    app.include_router(create_k10_router(db_path_provider=lambda: database,
        require_token_dependency=lambda: None, parquet_dir_provider=lambda: tmp_path))
    with TestClient(app) as client:
        flash = client.get(f"/api/v1/k10/documents/{flash_ref['documentId']}?revision=1").json()
        news = client.get(f"/api/v1/k10/documents/{news_ref['documentId']}?revision=1").json()
    assert flash["originalTitle"] is None and flash["title"] is None
    assert flash["body"] == "原始快讯完整内容" and flash["sourceKind"] == "flash"
    assert flash["contentKind"] == "original" and flash["eventTime"] is None
    assert news["excerpt"] == "官方简介正文" and news["contentKind"] == "excerpt"
    with pytest.raises(Jin10Error, match="page_cursor_conflict"):
        persist_question_tool_result(task_id=task_id, tool_name="list_news",
            structured={"data": {"items": [], "next_offset": "a", "next_cursor": "b",
                                 "has_more": True}}, obtained_at=datetime.now(timezone.utc),
            question="哪条", target="fixture", db_path=database)
    rejected = persist_question_tool_result(task_id=task_id, tool_name="list_news",
        structured={"data": {"items": [{"id": "x", "url": "https://xnews.jin10.com/details/x",
            "time": "2026-09-25T00:00:00+08:00", "title": "标题",
            "intro": "甲", "introduction": "乙"}], "has_more": False}},
        obtained_at=datetime.now(timezone.utc), question="哪条", target="fixture", db_path=database)
    assert rejected["documentRefs"] == []
    assert rejected["coverage"]["state"] == "partial"
    assert rejected["coverage"]["rejectedItems"][0]["reasonCode"] == "item_introduction_conflict"


def test_freeze_only_consumes_terminal_refs_and_blocks_old_unknown_dependency(tmp_path):
    """Helper boundary fixture: publication/unknown rows model durable prior outcomes."""
    database, config_id, revision = _configured(tmp_path)
    _run_slot(database, config_id, revision,
        "2026-09-25T08:00:00+08:00", "2026-09-25T00:00:00+08:00")
    source_keys = ("tushare-major-news", "jin10-flash", "jin10-news")
    store.create_scan(scan_id="prior-formal", window_kind="evening",
        cutoff_at="2026-09-25T21:00:00+08:00", config_id=None, config_revision=None,
        status="running", coverage={}, created_at="2026-09-26T12:30:00+00:00",
        completed_at=None, db_path=database)
    prior = store.freeze_collected_input(db_path=database, scan_id="prior-formal",
        window="evening", source_keys=source_keys,
        frozen_at=datetime(2026, 9, 26, 13, tzinfo=timezone.utc),
        bootstrap_at="2026-09-25T00:00:00+08:00")
    refs = prior["inputDocumentRefs"]
    assert len(refs) == 3
    terminal, blocked, untouched = refs
    prior_coverage = {"collectedInput": prior,
        "collectedInputConsumption": {"terminalRefs": [{"documentId": terminal["documentId"],
                                                          "revision": terminal["revision"]}]}}
    store.finalize_scan(scan_id="prior-formal", status="partial", coverage=prior_coverage,
        completed_at="2026-09-26T13:05:00+00:00", db_path=database)
    store.create_scan(scan_id="unpublished-unknown", window_kind="evening",
        cutoff_at="2026-09-26T20:00:00+08:00", config_id=None, config_revision=None,
        status="failed", coverage={"collectedInput": {"inputDocumentRefs": [blocked]}},
        created_at="2026-09-26T13:10:00+00:00", completed_at="2026-09-26T13:11:00+00:00",
        db_path=database)
    store.enqueue_task(task_id="prior-paid-task", kind="unrecognized_work",
        idempotency_key="prior-paid-task", input_version="fixture",
        input_cutoff_at="2026-09-26T20:00:00+08:00", payload={}, budget={"maxAttempts": 1},
        created_at="2026-09-26T13:10:00+00:00", db_path=database)
    # These rows represent earlier committed report outcomes, not a simulated
    # producer acceptance. FK checks are disabled only for this isolated helper
    # test's historical strategy/execution identities.
    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("INSERT INTO k10_v2_report_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("prior-formal-report", "prior-formal", "fixture-strategy", "evening", None,
             "2026-09-25T21:00:00+08:00", None, "2026-09-26T13:05:00+00:00",
             "partial", None, "2026-09-26T13:05:00+00:00"))
        conn.execute("INSERT INTO k10_scan_execution_bindings VALUES(?,?,?,?,?,?,?)",
            ("unpublished-unknown", "prior-paid-task", "fixture-execution", 1,
             "fixture-hash", "scheduled", "2026-09-26T13:10:00+00:00"))
        conn.execute("INSERT INTO k10_external_attempts(attempt_id,task_id,stage,item_key,attempt_key,input_sha256,state,started_at) "
                     "VALUES(?,?,?,?,?,?,'unknown',?)",
            ("old-unknown", "prior-paid-task", "jin10:get_news", "old-ref", "old-wire", "old-hash",
             "2026-09-26T13:10:00+00:00"))
    store.create_scan(scan_id="next-formal", window_kind="evening",
        cutoff_at="2026-09-26T21:00:00+08:00", config_id=None, config_revision=None,
        status="running", coverage={}, created_at="2026-09-26T13:30:00+00:00",
        completed_at=None, db_path=database)
    freeze_args = dict(db_path=database, scan_id="next-formal", window="evening",
        source_keys=source_keys, frozen_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
        bootstrap_at="2026-09-25T00:00:00+08:00")
    next_input = store.freeze_collected_input(**freeze_args)
    assert [(item["documentId"], item["revision"]) for item in next_input["inputDocumentRefs"]] == [
        (untouched["documentId"], untouched["revision"])]
    assert next_input["blockedDocumentRefs"] == [{"documentId": blocked["documentId"],
        "revision": blocked["revision"], "reasonCode": "prior_unknown_external_attempt"}]
    source = next(item for item in next_input["sourceOutcomes"]
                  if item["sourceKey"] == blocked["sourceKey"])
    assert any(gap["reasonCode"] == "prior_unknown_external_attempt" for gap in source["gaps"])
    assert store.freeze_collected_input(**freeze_args) == next_input


@pytest.mark.parametrize('cursor_field', ['next_cursor', 'next_offset', 'cursor'])
def test_b94_empty_terminal_cursor_recovers_same_task_without_rebilling(tmp_path, monkeypatch, cursor_field):
    from neckline.k10 import jin10_normalize
    database, config_id, revision = _configured(tmp_path)
    class LastPageClient(FixtureJin10):
        calls = []
        def call_tool_raw(self, name, arguments):
            self.calls.append((name, dict(arguments)))
            last = bool(arguments)
            item = {'id': name + str(last), 'url': 'https://jin10.com/' + name + str(last),
                    'time': '2026-09-25T01:00:00+08:00' if last else '2026-09-25T07:00:00+08:00',
                    'title': '目录' if name == 'list_news' else None,
                    'content': '完整快讯' if name == 'list_flash' else None}
            return {'structuredContent': {'status': 200, 'data': {
                'items': [item], 'has_more': not last, cursor_field: '' if last else 'last-page'}}}
    original = jin10_normalize._page
    def before_fix(tool, structured):
        if structured['data'].get(cursor_field) == '':
            raise Jin10Error('page_cursor_invalid')
        return original(tool, structured)
    monkeypatch.setattr(jin10_normalize, '_page', before_fix)
    task_id = _cli('enqueue-collection', '--db', str(database), '--slot', '2026-09-25T08:00:00+08:00',
                   '--config-id', config_id, '--config-revision', str(revision))
    clock = lambda: datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    handler = create_collection_handler(tushare_token='fixture-token', jin10_token='fixture-token',
        client_factory=LastPageClient, tushare_request=_tushare)
    kwargs = dict(db_path=database, task_id=task_id, worker_id='b94-terminal',
        lease_for=timedelta(minutes=5), handlers={'collect_news': handler}, clock=clock,
        require_b76_contract=True)
    first = run_once(**kwargs)
    assert first.status == 'failed'
    calls = list(LastPageClient.calls)
    assert len(calls) == 4
    monkeypatch.setattr(jin10_normalize, '_page', original)
    store.retry_task(task_id=task_id, expected_attempt_count=first.attempt_count,
                     retried_at=clock().isoformat(), db_path=database)
    recovered = run_once(**kwargs)
    # Visible history stops after the requested start: a repaired parser is
    # not proof of complete coverage and must not advance the watermark.
    assert recovered.status == 'failed' and LastPageClient.calls == calls
    checkpoint = store.task_execution_input(task_id=task_id, db_path=database)['checkpoint']
    for source in ('jin10-flash', 'jin10-news'):
        state = checkpoint['sources'][source]
        assert len(state['documentRefs']) == 2 and state['pagesFetched'] == 2
        assert state['errorCode'] == 'history_unavailable'
        assert state['coverageThrough'] is None and state['gaps']
        assert 'page_cursor_invalid' not in state['limitations']
        assert store.latest_source_watermark(source_key=source, db_path=database) is None
    with schema.read_connection(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_external_attempts WHERE state IN ('started','unknown')").fetchone()[0] == 0
    app = FastAPI()
    app.include_router(create_router(db_path_provider=lambda: database,
        require_token_dependency=lambda: None, current_collection_binding_provider=lambda: (config_id, revision, None)))
    app.include_router(create_k10_router(db_path_provider=lambda: database,
        require_token_dependency=lambda: None, parquet_dir_provider=lambda: tmp_path))
    with TestClient(app) as client:
        status = client.get('/api/v1/k10/collection/status').json()
        assert all(s['state'] == 'partial' for s in status['sources'] if s['sourceKey'].startswith('jin10'))
        ref = checkpoint['sources']['jin10-flash']['documentRefs'][-1]
        response = client.get(f"/api/v1/k10/documents/{ref['documentId']}?revision={ref['revision']}")
        assert response.status_code == 200 and response.json()['body'] == '完整快讯'


@pytest.mark.parametrize('cursor,more', [('', True), (42, False), (False, False), ([], False)])
def test_b94_terminal_cursor_fix_keeps_invalid_pagination_rejected(cursor, more):
    from neckline.k10.jin10_normalize import _page
    with pytest.raises(Jin10Error, match='page_cursor_invalid'):
        _page('list_flash', {'data': {'items': [], 'next_cursor': cursor, 'has_more': more}})
