"""Review regressions for resident worker and exact Jin10 page traversal."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import httpx
import pytest

from neckline.db import init_schema
from neckline.k10 import store
from neckline.k10 import cli, collection_runtime
from neckline.k10.collection_gateway import _digest, call_question_tool
from neckline.k10.jin10_mcp import Jin10Client, Jin10Error
from neckline.k10.jin10_normalize import persist_question_tool_result
from neckline.k10.discovery import DiscoveryDocument
from neckline.k10.schema import initialize_schema, read_connection
from neckline.k10.verification import Jin10QuestionGateway
from neckline.k10.worker import TaskContext, TaskResult, run_worker


NOW = datetime(2026, 9, 26, 8, tzinfo=timezone.utc)


def _database(tmp_path: Path) -> Path:
    database = tmp_path / "fresh.sqlite"
    init_schema(database)
    initialize_schema(database)
    store.set_run_control(state="closed", reason_code="user_paused",
                          changed_at=NOW.isoformat(), changed_by="fixture", db_path=database)
    store.set_collection_control(state="closed", reason_code="user_paused",
                                 changed_at=NOW.isoformat(), changed_by="fixture", db_path=database)
    return database


def _task(database: Path) -> str:
    store.set_collection_control(state="open", reason_code="operator_open",
                                 changed_at=NOW.isoformat(), changed_by="fixture", db_path=database)
    task = store.enqueue_task(task_id="b93-collection-fixture", kind="collect_news",
                              idempotency_key="b93-collection-fixture", input_version="fixture",
                              input_cutoff_at=NOW.isoformat(), payload={}, budget={"maxAttempts": 1},
                              created_at=NOW.isoformat(), db_path=database)
    return task.task_id


def _running_task(database: Path) -> str:
    task_id = _task(database)
    assert store.claim_task_by_id(task_id=task_id, worker_id="b93-fixture",
        now=NOW, lease_for=timedelta(minutes=5), db_path=database) is not None
    return task_id


def test_worker_remains_resident_while_both_controls_closed_then_consumes_open_collection(tmp_path):
    database = _database(tmp_path)
    task_id = _task(database)
    store.set_collection_control(state="closed", reason_code="user_paused",
        changed_at=NOW.isoformat(), changed_by="fixture", db_path=database)
    stop, entered = Event(), Event()

    def handler(context):
        entered.set()
        stop.set()
        return TaskResult("completed", "fixture_done", context.checkpoint)

    worker = Thread(target=run_worker, kwargs={"db_path": database, "worker_id": "b93-fixture",
        "lease_for": timedelta(minutes=5), "idle_seconds": 0.01,
        "handlers": {"collect_news": handler}, "stop": stop}, daemon=True)
    worker.start()
    try:
        assert worker.is_alive() and not entered.wait(0.05)
        assert store.get_task(task_id=task_id, db_path=database).status == "queued"
        store.set_collection_control(state="open", reason_code="operator_open",
            changed_at=NOW.isoformat(), changed_by="fixture", db_path=database)
        assert entered.wait(3)
    finally:
        stop.set()
        worker.join(3)
    assert not worker.is_alive()
    assert store.get_task(task_id=task_id, db_path=database).status == "completed"


def test_cli_starts_resident_worker_even_when_both_controls_are_closed(tmp_path, monkeypatch):
    database = _database(tmp_path)
    entered = []
    monkeypatch.setattr(cli, "production_handlers", lambda **_: {})
    monkeypatch.setattr(cli, "create_collection_handler", lambda **_: lambda _: None)
    monkeypatch.setattr(cli, "create_notification_maintenance", lambda **_: lambda: None)
    monkeypatch.setattr(cli.signal, "signal", lambda *_: None)
    monkeypatch.setattr(cli, "run_worker", lambda **kwargs: entered.append(kwargs["worker_id"]))
    assert cli.main(["worker", "--db", str(database), "--parquet-dir", str(tmp_path),
        "--worker-id", "b93-closed-start", "--tushare-token-env", "B93_UNUSED_TOKEN"]) == 0
    assert entered == ["b93-closed-start"]


def test_cli_fresh_initializer_creates_only_an_absent_database(tmp_path):
    database = tmp_path / "new-only.sqlite"
    output = StringIO()
    with redirect_stdout(output):
        assert cli.main(["initialize-fresh", "--db", str(database)]) == 0
    assert json.loads(output.getvalue())["status"] == "empty_initialized"
    assert database.exists()
    with pytest.raises(FileExistsError):
        cli.main(["initialize-fresh", "--db", str(database)])


class _PagedQuestionClient:
    endpoint = "https://mcp.jin10.com/mcp"
    protocol_version = "2025-11-25"
    calls: list[dict[str, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def pagination_argument(self, name: str) -> str | None:
        return "offset" if name == "search_news" else None

    def call_tool_raw(self, name: str, arguments):
        assert name == "search_news"
        self.calls.append(dict(arguments))
        page = 2 if "offset" in arguments else 1
        return {"structuredContent": {"status": 200, "data": {
            "items": [{"id": f"article-{page}", "url": f"https://xnews.jin10.com/details/article-{page}",
                       "time": "2026-09-26T18:00:00+08:00", "title": f"第{page}页关键进展"}],
            "has_more": page == 1, "next_offset": "page-2" if page == 1 else None}}}


def test_question_bound_news_search_reads_next_page_with_distinct_paid_receipts(tmp_path):
    database = _database(tmp_path)
    task_id = _running_task(database)
    _PagedQuestionClient.calls = []
    gateway = Jin10QuestionGateway(db_path=database, task_id=task_id,
        client_factory=lambda: _PagedQuestionClient(), clock=lambda: NOW)
    request = dict(event=SimpleNamespace(canonical_key="important-event"), retrieved_at=NOW,
        cutoff_at=datetime(2026, 9, 26, 23, tzinfo=timezone.utc),
        question=SimpleNamespace(question="公司是否参与该订单", question_id="participation"),
        query_path=SimpleNamespace(target_source="jin10-news", intent="verify_company_link",
                                   query="测试公司"))
    first = gateway.fetch(**request)
    second = gateway.fetch(**request)
    assert [document.metadata["providerId"] for document in first.eligible_documents] == [
        "article-1", "article-2"]
    assert first.coverage["state"] == "completed"
    assert first.coverage["resultCount"] == 2
    assert second.coverage["documentRefs"] == first.coverage["documentRefs"]
    assert _PagedQuestionClient.calls == [
        {"keyword": "测试公司"}, {"keyword": "测试公司", "offset": "page-2"}]
    checkpoint = store.task_execution_input(task_id=task_id, db_path=database)["checkpoint"]
    assert len(checkpoint["toolReceipts"]) == 2


def test_unknown_second_news_page_retains_first_page_and_blocks_repost(tmp_path):
    database = _database(tmp_path)
    task_id = _running_task(database)
    class UnknownSecondPage(_PagedQuestionClient):
        calls = []
        def call_tool_raw(self, name, arguments):
            if "offset" in arguments:
                self.calls.append(dict(arguments))
                raise Jin10Error("transport_unknown", unknown=True)
            return super().call_tool_raw(name, arguments)

    gateway = Jin10QuestionGateway(db_path=database, task_id=task_id,
        client_factory=lambda: UnknownSecondPage(), clock=lambda: NOW)
    request = dict(event=SimpleNamespace(canonical_key="important-event"), retrieved_at=NOW,
        cutoff_at=datetime(2026, 9, 26, 23, tzinfo=timezone.utc),
        question=SimpleNamespace(question="公司是否参与该订单", question_id="participation"),
        query_path=SimpleNamespace(target_source="jin10-news", intent="verify_company_link",
                                   query="测试公司"))
    first = gateway.fetch(**request)
    repeated = gateway.fetch(**request)
    assert first.state == repeated.state == "pending"
    assert [doc.metadata["providerId"] for doc in repeated.eligible_documents] == ["article-1"]
    assert repeated.coverage["state"] == "pending"
    assert UnknownSecondPage.calls == [
        {"keyword": "测试公司"}, {"keyword": "测试公司", "offset": "page-2"}]


def test_question_page_does_not_discover_metadata_after_control_closes(tmp_path):
    database = _database(tmp_path)
    task_id = _running_task(database)
    class ClosingPage(_PagedQuestionClient):
        calls = []
        def call_tool_raw(self, name, arguments):
            reply = super().call_tool_raw(name, arguments)
            store.set_collection_control(state="closed", reason_code="user_paused",
                changed_at=NOW.isoformat(), changed_by="fixture", db_path=database)
            return reply
        def pagination_argument(self, name):
            raise AssertionError("paused question must not make a new MCP metadata request")

    gateway = Jin10QuestionGateway(db_path=database, task_id=task_id,
        client_factory=lambda: ClosingPage(), clock=lambda: NOW)
    bundle = gateway.fetch(event=SimpleNamespace(canonical_key="important-event"),
        retrieved_at=NOW, cutoff_at=datetime(2026, 9, 26, 23, tzinfo=timezone.utc),
        question=SimpleNamespace(question="公司是否参与该订单", question_id="participation"),
        query_path=SimpleNamespace(target_source="jin10-news", intent="verify_company_link",
                                   query="测试公司"))
    assert bundle.coverage["state"] == "partial"
    assert bundle.coverage["reason"] == "collection_paused"
    assert len(bundle.documents) == 1
    assert ClosingPage.calls == [{"keyword": "测试公司"}]
    assert len(store.task_execution_input(task_id=task_id, db_path=database)["checkpoint"]["toolReceipts"]) == 1


@pytest.mark.parametrize("closure", ["throttle", "morning_closeout"])
def test_question_page_preserves_paid_first_page_when_new_search_admission_stops(tmp_path, closure):
    from neckline.k10.discovery import ProviderThrottleYield
    from neckline.k10.pipeline import PipelineError

    database = _database(tmp_path)
    task_id = _running_task(database)
    count = 0
    def admission():
        nonlocal count
        count += 1
        if count == 2:
            if closure == "throttle":
                raise ProviderThrottleYield(3)
            raise PipelineError("晨报排序预留", code="morning_closeout_reserve")

    _PagedQuestionClient.calls = []
    gateway = Jin10QuestionGateway(db_path=database, task_id=task_id,
        client_factory=lambda: _PagedQuestionClient(), clock=lambda: NOW,
        new_external_admission_guard=admission)
    bundle = gateway.fetch(event=SimpleNamespace(canonical_key="important-event"),
        retrieved_at=NOW, cutoff_at=datetime(2026, 9, 26, 23, tzinfo=timezone.utc),
        question=SimpleNamespace(question="公司是否参与该订单", question_id="participation"),
        query_path=SimpleNamespace(target_source="jin10-news", intent="verify_company_link",
                                   query="测试公司"))
    assert bundle.coverage["state"] == "partial"
    assert bundle.coverage["reason"] == (
        "research_slice_closed" if closure == "throttle" else "morning_closeout_reserve")
    assert [doc.metadata["providerId"] for doc in bundle.eligible_documents] == ["article-1"]
    assert _PagedQuestionClient.calls == [{"keyword": "测试公司"}]
    assert len(store.task_execution_input(task_id=task_id, db_path=database)["checkpoint"]["toolReceipts"]) == 1


def test_flash_search_at_150_keeps_truncation_without_attempting_page_two(tmp_path):
    database = _database(tmp_path)
    task_id = _running_task(database)
    class FlashLimitClient(_PagedQuestionClient):
        calls = []
        def call_tool_raw(self, name, arguments):
            assert name == "search_flash"
            self.calls.append(dict(arguments))
            return {"structuredContent": {"status": 200, "data": {
                "items": [{"id": f"flash-{index}",
                           "url": f"https://flash.jin10.com/detail/flash-{index}",
                           "time": "2026-09-26T15:00:00+08:00",
                           "content": f"快讯{index}原文"} for index in range(150)],
                "has_more": False, "next_offset": None}}}

    gateway = Jin10QuestionGateway(db_path=database, task_id=task_id,
        client_factory=lambda: FlashLimitClient(), clock=lambda: NOW)
    bundle = gateway.fetch(event=SimpleNamespace(canonical_key="important-event"),
        retrieved_at=NOW, cutoff_at=datetime(2026, 9, 26, 23, tzinfo=timezone.utc),
        question=SimpleNamespace(question="有无补充快讯", question_id="flash-check"),
        query_path=SimpleNamespace(target_source="jin10-flash", intent="verify_company_link",
                                   query="测试公司"))
    assert bundle.coverage["state"] == "partial"
    assert bundle.coverage["truncated"] is True
    assert len(bundle.documents) == 150
    assert FlashLimitClient.calls == [{"keyword": "测试公司"}]


def test_reused_paid_list_reply_recovers_schema_without_reposting_tool(tmp_path):
    database = _database(tmp_path)
    task_id = _running_task(database)
    tool_calls: list[dict[str, object]] = []

    def wire(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {}}
        elif method == "tools/list":
            result = {"tools": [{"name": "list_news", "inputSchema": {"type": "object",
                "properties": {"offset": {"type": "string"}}, "required": []}}]}
        else:
            assert method == "tools/call"
            tool_calls.append(body)
            result = {"structuredContent": {"status": 200, "data": {
                "items": [], "has_more": True, "next_offset": "second-page"}}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    transport = httpx.MockTransport(wire)
    def client():
        return Jin10Client(token="isolated", timeout_seconds=1, transport=transport)

    kwargs = dict(task_id=task_id, question="采集当前目录", target="news-slot",
                  purpose="scheduled_collection", tool_name="list_news", arguments={},
                  db_path=database, clock=lambda: NOW)
    with client() as first:
        reply = call_question_tool(**kwargs, client=first)
        assert first.pagination_argument("list_news") == "offset"
    # A process died after the paid reply reached the ledger but before page
    # checkpoint. The new client has no in-memory schemas; it must only redo
    # the read-only MCP handshake, then traverse the next page with offset.
    with client() as restarted:
        assert call_question_tool(**kwargs, client=restarted) == reply
        assert restarted.pagination_argument("list_news") == "offset"
    assert len(tool_calls) == 1
    assert len(store.task_execution_input(task_id=task_id, db_path=database)["checkpoint"]["toolReceipts"]) == 1


def test_missing_credential_still_reuses_exact_paid_reply_but_cannot_handshake(tmp_path):
    database = _database(tmp_path)
    task_id = _running_task(database)
    methods = []
    def wire(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        methods.append(body["method"])
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {}}
        elif body["method"] == "tools/list":
            result = {"tools": [{"name": "list_news", "inputSchema": {"type": "object",
                "properties": {"offset": {"type": "string"}}, "required": []}}]}
        else:
            result = {"structuredContent": {"status": 200, "data": {
                "items": [], "has_more": True, "next_offset": "later"}}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    transport = httpx.MockTransport(wire)
    kwargs = dict(task_id=task_id, question="已付费目录", target="slot",
        purpose="scheduled_collection", tool_name="list_news", arguments={},
        db_path=database, clock=lambda: NOW)
    with Jin10Client(token="isolated", timeout_seconds=1, transport=transport) as first:
        paid = call_question_tool(**kwargs, client=first)
    after_paid = list(methods)
    with Jin10Client(token=None, timeout_seconds=1, transport=transport) as restarted:
        assert call_question_tool(**kwargs, client=restarted) == paid
        with pytest.raises(Jin10Error, match="credential_missing"):
            restarted.pagination_argument("list_news")
    assert methods == after_paid


def test_question_gateway_uses_paid_flash_reply_without_token_or_new_network(tmp_path):
    database = _database(tmp_path)
    task_id = _running_task(database)
    methods = []
    def wire(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        methods.append(body["method"])
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {}}
        elif body["method"] == "tools/list":
            result = {"tools": [{"name": "search_flash", "inputSchema": {"type": "object",
                "properties": {"keyword": {"type": "string"}}, "required": ["keyword"]}}]}
        else:
            result = {"structuredContent": {"status": 200, "data": {
                "items": [{"id": "paid-flash", "url": "https://flash.jin10.com/detail/paid-flash",
                           "time": "2026-09-26T15:00:00+08:00", "content": "已付费快讯原文"}],
                "has_more": False, "next_cursor": None}}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    transport = httpx.MockTransport(wire)
    def factory(token):
        return lambda: Jin10Client(token=token, timeout_seconds=1, transport=transport)
    request = dict(event=SimpleNamespace(canonical_key="important-event"), retrieved_at=NOW,
        cutoff_at=datetime(2026, 9, 26, 23, tzinfo=timezone.utc),
        question=SimpleNamespace(question="快讯是否改变判断", question_id="flash-check"),
        query_path=SimpleNamespace(target_source="jin10-flash", intent="verify_new_fact",
                                   query="测试公司"))
    paid_gateway = Jin10QuestionGateway(db_path=database, task_id=task_id,
        client_factory=factory("isolated"), clock=lambda: NOW)
    assert len(paid_gateway.fetch(**request).eligible_documents) == 1
    after_paid = list(methods)
    replay_gateway = Jin10QuestionGateway(db_path=database, task_id=task_id,
        client_factory=factory(None), clock=lambda: NOW)
    replay = replay_gateway.fetch(**request)
    assert [doc.metadata["providerId"] for doc in replay.eligible_documents] == ["paid-flash"]
    assert methods == after_paid


def test_same_article_read_for_two_company_questions_reuses_exact_paid_reply(tmp_path):
    database = _database(tmp_path)
    task_id = _running_task(database)
    directory = persist_question_tool_result(
        task_id=task_id, tool_name="list_news",
        structured={"data": {"items": [{
            "id": "shared-article", "url": "https://xnews.jin10.com/details/shared-article",
            "time": "2026-09-26T15:00:00+08:00", "title": "两家公司相关的公告",
        }], "has_more": False}}, obtained_at=NOW,
        question="采集文章目录", target="news-slot", db_path=database,
    )
    parent = directory["documentRefs"][0]
    parent_row = store.load_document_versions(refs=[parent], db_path=database)[0]
    document = DiscoveryDocument(
        parent_row["documentId"], parent_row["revision"],
        parent_row["publishedAt"], parent_row["fetchedAt"],
        parent_row["originalText"], parent_row["excerpt"],
        {**parent_row["metadata"], "sourceKey": parent_row["sourceKey"]},
    )
    methods: list[str] = []

    def wire(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        methods.append(method)
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {}}
        elif method == "tools/list":
            result = {"tools": [{"name": "get_news", "inputSchema": {"type": "object",
                "properties": {"id": {"type": "string"}}, "required": ["id"]}}]}
        else:
            assert method == "tools/call"
            assert body["params"]["arguments"] == {"id": "shared-article"}
            result = {"structuredContent": {"status": 200, "data": {
                "id": "shared-article", "url": "https://xnews.jin10.com/details/shared-article",
                "time": "2026-09-26T15:00:00+08:00", "title": "两家公司相关的公告",
                "content": "公告原文同时涉及甲公司和乙公司。",
            }}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    transport = httpx.MockTransport(wire)
    gateway = Jin10QuestionGateway(
        db_path=database, task_id=task_id,
        client_factory=lambda: Jin10Client(token="isolated", timeout_seconds=1, transport=transport),
        clock=lambda: NOW,
    )
    for company in ("甲公司", "乙公司"):
        bundle = gateway.fetch_fulltext(
            event=SimpleNamespace(canonical_key=company), document=document,
            question=SimpleNamespace(question=f"{company}与公告有何关系", question_id=company),
            request=SimpleNamespace(reason_excerpt_insufficient="需要原文核验"),
            cutoff_at=datetime(2026, 9, 26, 23, tzinfo=timezone.utc),
        )
        assert bundle.state == "available"
        assert bundle.eligible_documents[0].original_text == "公告原文同时涉及甲公司和乙公司。"
    assert methods.count("tools/call") == 1
    assert len(store.task_execution_input(task_id=task_id, db_path=database)["checkpoint"]["toolReceipts"]) == 1


def test_missing_token_without_paid_reply_does_not_spend_attempt_budget(tmp_path):
    database = _database(tmp_path)
    task_id = _running_task(database)
    methods = []
    def wire(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        methods.append(body["method"])
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {}}
        elif body["method"] == "tools/list":
            result = {"tools": [{"name": "list_news", "inputSchema": {"type": "object",
                "properties": {}, "required": []}}]}
        else:
            result = {"structuredContent": {"status": 200, "data": {
                "items": [], "has_more": False}}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    transport = httpx.MockTransport(wire)
    kwargs = dict(task_id=task_id, question="首次列表", target="slot", purpose="scheduled_collection",
        tool_name="list_news", arguments={}, db_path=database, clock=lambda: NOW)
    with Jin10Client(token=None, timeout_seconds=1, transport=transport) as no_key:
        with pytest.raises(Jin10Error, match="credential_missing"):
            call_question_tool(**kwargs, client=no_key)
    assert methods == []
    with read_connection(database) as conn:
        assert conn.execute("SELECT count(*) FROM k10_external_attempts WHERE task_id=?",
                            (task_id,)).fetchone()[0] == 0
    with Jin10Client(token="isolated", timeout_seconds=1, transport=transport) as configured:
        assert call_question_tool(**kwargs, client=configured)["status"] == 200
    assert methods.count("tools/call") == 1


@pytest.mark.parametrize("prior_state", ["started", "unknown"])
def test_missing_token_preserves_same_wire_pending_outcome(tmp_path, prior_state):
    database = _database(tmp_path)
    task_id = _running_task(database)
    kwargs = dict(task_id=task_id, question="待结算文章搜索", target="company-1",
        purpose="verify_company_link", tool_name="search_news",
        arguments={"keyword": "测试公司"}, db_path=database, clock=lambda: NOW)
    with Jin10Client(token=None, timeout_seconds=1,
                     transport=httpx.MockTransport(lambda _: pytest.fail("不得发出网络请求"))) as client:
        input_hash = _digest({"service": "jin10", "endpoint": client.endpoint,
            "protocolVersion": client.protocol_version, "taskId": task_id,
            "tool": kwargs["tool_name"], "arguments": kwargs["arguments"]})
        attempt_key = "jin10:" + input_hash
        begun = store.begin_tool_attempt(task_id=task_id, stage="jin10:search_news",
            item_key="company-1", attempt_key=attempt_key, input_sha256=input_hash,
            started_at=NOW.isoformat(), db_path=database)
        assert begun["state"] == "started"
        if prior_state == "unknown":
            store.settle_tool_attempt(task_id=task_id, attempt_id=begun["attemptId"],
                attempt_key=attempt_key, input_sha256=input_hash, result=None,
                outcome="unknown", safe_error_code="transport_unknown",
                settled_at=NOW.isoformat(), db_path=database)
        with pytest.raises(Jin10Error) as caught:
            call_question_tool(**kwargs, client=client)
    assert caught.value.code == "outcome_unknown" and caught.value.unknown
    with read_connection(database) as conn:
        attempts = conn.execute(
            "SELECT state FROM k10_external_attempts WHERE task_id=?", (task_id,),
        ).fetchall()
    assert [row[0] for row in attempts] == [prior_state]


def test_collector_restart_between_receipt_and_page_checkpoint_reuses_paid_reply(tmp_path, monkeypatch):
    database = _database(tmp_path)
    task_id = _running_task(database)
    task = store.get_task(task_id=task_id, db_path=database)
    assert task is not None
    context = TaskContext(task=task, budget={"maxAttempts": 1}, checkpoint={},
        input_version="fixture", input_cutoff_at=NOW.isoformat(), db_path=database,
        lease_lost=Event(), clock=lambda: NOW)
    tool_arguments = []

    def wire(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {}}
        elif method == "tools/list":
            result = {"tools": [{"name": "list_news", "inputSchema": {"type": "object",
                "properties": {"offset": {"type": "string"}}, "required": []}}]}
        else:
            assert method == "tools/call"
            arguments = body["params"]["arguments"]
            tool_arguments.append(arguments)
            page = 2 if arguments else 1
            result = {"structuredContent": {"status": 200, "data": {
                "items": [{"id": f"list-{page}", "url": f"https://xnews.jin10.com/details/list-{page}",
                           "time": "2026-09-26T15:00:00+08:00", "title": f"目录{page}"}],
                "has_more": page == 1, "next_offset": "page-2" if page == 1 else None}}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    def client_factory(**kwargs):
        return Jin10Client(transport=httpx.MockTransport(wire), **kwargs)

    config = {"bootstrapStartAt": "2026-09-26T07:00:00+00:00",
              "maxPages": 4, "timeoutSeconds": 1, "lookbackSeconds": 3600}
    mcp = {"endpoint": "https://mcp.jin10.com/mcp", "protocolVersion": "2025-11-25"}
    original_save = collection_runtime._save
    crashed = False

    def crash_before_checkpoint(context, source_key, state, **kwargs):
        nonlocal crashed
        if not crashed and state["pagesFetched"] == 1:
            crashed = True
            raise RuntimeError("fixture_crash_after_paid_reply")
        return original_save(context, source_key, state, **kwargs)

    monkeypatch.setattr(collection_runtime, "_save", crash_before_checkpoint)
    with pytest.raises(RuntimeError, match="fixture_crash_after_paid_reply"):
        collection_runtime._jin10_source(context, source_key="jin10-news", config=config,
            mcp=mcp, slot=NOW, token="isolated", client_factory=client_factory)
    assert len(tool_arguments) == 1
    assert len(store.task_execution_input(task_id=task_id, db_path=database)["checkpoint"]["toolReceipts"]) == 1
    monkeypatch.setattr(collection_runtime, "_save", original_save)
    state = collection_runtime._jin10_source(context, source_key="jin10-news", config=config,
        mcp=mcp, slot=NOW, token="isolated", client_factory=client_factory)
    assert state["pagesFetched"] == 2
    assert len(state["documentRefs"]) == 2
    assert tool_arguments == [{}, {"offset": "page-2"}]


@pytest.mark.parametrize("has_more", [False, True])
def test_collector_without_token_restores_paid_list_page_before_document_checkpoint(tmp_path, has_more):
    database = _database(tmp_path)
    task_id = _running_task(database)
    task = store.get_task(task_id=task_id, db_path=database)
    assert task is not None
    context = TaskContext(task=task, budget={"maxAttempts": 1}, checkpoint={},
        input_version="fixture", input_cutoff_at=NOW.isoformat(), db_path=database,
        lease_lost=Event(), clock=lambda: NOW)
    methods = []
    def wire(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        methods.append(body["method"])
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {}}
        elif body["method"] == "tools/list":
            result = {"tools": [{"name": "list_news", "inputSchema": {"type": "object",
                "properties": {"offset": {"type": "string"}}, "required": []}}]}
        else:
            next_page = "offset" in body["params"]["arguments"]
            article_id = "paid-page-2" if next_page else "paid-before-crash"
            result = {"structuredContent": {"status": 200, "data": {
                "items": [{"id": article_id,
                           "url": "https://xnews.jin10.com/details/" + article_id,
                           "time": "2026-09-26T15:00:00+08:00", "title": "已付费目录原件"}],
                "has_more": has_more and not next_page,
                "next_offset": "page-2" if has_more and not next_page else None}}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    transport = httpx.MockTransport(wire)
    with Jin10Client(token="isolated", timeout_seconds=1, transport=transport) as paid_client:
        call_question_tool(task_id=task_id, question="槽位目录", target="slot",
            purpose="scheduled_collection", tool_name="list_news", arguments={},
            db_path=database, client=paid_client, clock=lambda: NOW)
    after_paid = list(methods)
    assert not store.load_document_versions(refs=[], db_path=database)
    config = {"bootstrapStartAt": "2026-09-26T07:00:00+00:00",
              "maxPages": 4, "timeoutSeconds": 1, "lookbackSeconds": 3600}
    mcp = {"endpoint": "https://mcp.jin10.com/mcp", "protocolVersion": "2025-11-25"}
    state = collection_runtime._jin10_source(context, source_key="jin10-news", config=config,
        mcp=mcp, slot=NOW, token=None,
        client_factory=lambda **kwargs: Jin10Client(transport=transport, **kwargs))
    assert state["state"] == ("partial" if has_more else "completed")
    assert state["errorCode"] == ("credential_missing" if has_more else None)
    assert len(state["documentRefs"]) == 1
    assert methods == after_paid
    rows = store.load_document_versions(refs=state["documentRefs"], db_path=database)
    assert rows[0]["metadata"]["providerId"] == "paid-before-crash"
    if has_more:
        resumed_checkpoint = store.task_execution_input(task_id=task_id, db_path=database)["checkpoint"]
        resumed = collection_runtime._jin10_source(replace(context, checkpoint=resumed_checkpoint),
            source_key="jin10-news", config=config, mcp=mcp, slot=NOW, token="isolated",
            client_factory=lambda **kwargs: Jin10Client(transport=transport, **kwargs))
        assert resumed["state"] == "completed"
        assert len(resumed["documentRefs"]) == 2
        assert {row["metadata"]["providerId"] for row in store.load_document_versions(
            refs=resumed["documentRefs"], db_path=database)} == {"paid-before-crash", "paid-page-2"}
        assert methods.count("tools/call") == 2


def test_collection_page_receipt_settles_but_pause_blocks_metadata_lookup(tmp_path):
    database = _database(tmp_path)
    task_id = _running_task(database)
    task = store.get_task(task_id=task_id, db_path=database)
    assert task is not None
    context = TaskContext(task=task, budget={"maxAttempts": 1}, checkpoint={},
        input_version="fixture", input_cutoff_at=NOW.isoformat(), db_path=database,
        lease_lost=Event(), clock=lambda: NOW)
    class ClosingCollectionClient(_PagedQuestionClient):
        calls = []
        def call_tool_raw(self, name, arguments):
            assert name == "list_news"
            self.calls.append(dict(arguments))
            store.set_collection_control(state="closed", reason_code="user_paused",
                changed_at=NOW.isoformat(), changed_by="fixture", db_path=database)
            return {"structuredContent": {"status": 200, "data": {
                "items": [{"id": "paused-page", "url": "https://xnews.jin10.com/details/paused-page",
                           "time": "2026-09-26T15:00:00+08:00", "title": "已结算目录"}],
                "has_more": True, "next_offset": "page-2"}}}
        def pagination_argument(self, name):
            raise AssertionError("paused collection must not make a new MCP metadata request")

    config = {"bootstrapStartAt": "2026-09-26T07:00:00+00:00",
              "maxPages": 4, "timeoutSeconds": 1, "lookbackSeconds": 3600}
    mcp = {"endpoint": "https://mcp.jin10.com/mcp", "protocolVersion": "2025-11-25"}
    state = collection_runtime._jin10_source(context, source_key="jin10-news", config=config,
        mcp=mcp, slot=NOW, token="isolated", client_factory=lambda **_: ClosingCollectionClient())
    assert state["state"] == "partial" and state["errorCode"] == "collection_paused"
    assert len(state["documentRefs"]) == 1
    assert ClosingCollectionClient.calls == [{}]
    assert len(store.task_execution_input(task_id=task_id, db_path=database)["checkpoint"]["toolReceipts"]) == 1


def test_schema_restore_checks_admission_before_each_mcp_request():
    allowed = True
    methods = []
    def guard():
        if not allowed:
            raise Jin10Error("closed_before_send", pre_send=True)
    def wire(request: httpx.Request) -> httpx.Response:
        nonlocal allowed
        body = json.loads(request.content)
        methods.append(body["method"])
        assert body["method"] == "initialize"
        allowed = False
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
            "result": {"protocolVersion": "2025-11-25", "capabilities": {}}})
    with Jin10Client(token="isolated", timeout_seconds=1,
                     transport=httpx.MockTransport(wire)) as client:
        with pytest.raises(Jin10Error, match="closed_before_send"):
            client.pagination_argument("list_news", admission_guard=guard)
    assert methods == ["initialize"]


def test_control_closing_during_initial_mcp_handshake_never_sends_paid_call(tmp_path):
    database = _database(tmp_path)
    task_id = _running_task(database)
    methods = []
    def wire(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        methods.append(body["method"])
        assert body["method"] == "initialize"
        store.set_collection_control(state="closed", reason_code="user_paused",
            changed_at=NOW.isoformat(), changed_by="fixture", db_path=database)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
            "result": {"protocolVersion": "2025-11-25", "capabilities": {}}})

    with Jin10Client(token="isolated", timeout_seconds=1,
                     transport=httpx.MockTransport(wire)) as client:
        with pytest.raises(Jin10Error, match="closed_before_send"):
            call_question_tool(task_id=task_id, question="某家公司与订单关系",
                target="event-1", purpose="verify_company_link", tool_name="search_news",
                arguments={"keyword": "测试公司"}, db_path=database, client=client, clock=lambda: NOW)
    assert methods == ["initialize"]
    with read_connection(database) as conn:
        attempts = conn.execute("SELECT state,error_code FROM k10_external_attempts WHERE task_id=?",
            (task_id,)).fetchall()
    assert len(attempts) == 1 and attempts[0][0] == "failed"
