"""Paid Tavily responses survive derivation failure without another request."""
from datetime import timedelta
import json
import sqlite3

import httpx
import pytest

from neckline.db import init_schema as init_shared_schema
from neckline.k10 import store, verification
from neckline.k10.delivery import runtime_contract
from neckline.k10.discovery import ProviderThrottleYield
from neckline.k10.schema import initialize_schema
from neckline.k10.verification import TavilyEvidenceGateway
from neckline.search.tavily import TavilySearchClient
from tests.test_k10_verification import _bound_task, _event, _fetch_bound, NOW, COMPLETED_AT
from tests.test_v310_tavily import _frozen


def setup_gateway(tmp_path):
    path = tmp_path / "paid-tavily.sqlite"
    init_shared_schema(path)
    initialize_schema(path)
    store.set_run_control(state="open", reason_code="fixture", changed_at=NOW.isoformat(),
                          changed_by="test", db_path=path)
    task_id = _bound_task(path)
    requests = []
    search_body = {
        "results": [{"url": "https://example.invalid/current-announcement", "title": "项目公告",
                     "content": "仅取得项目入围资格，未签订订单。", "published_date": "2026-09-06",
                     "provider_extension": {"keep": "exact paid source"}}],
        "usage": {"credits": 2}, "request_id": "search-paid-once",
    }
    extract_body = {"results": [{"url": search_body["results"][0]["url"],
                                "raw_content": "公告全文：仅取得入围资格，未形成签约订单。"}],
                    "usage": {"credits": 1}, "request_id": "extract-paid-once"}

    def respond(request):
        requests.append(request.url.path)
        return httpx.Response(200, json=search_body if request.url.path == "/search" else extract_body)

    client = TavilySearchClient("isolated-fixture", transport=httpx.MockTransport(respond))
    gateway = TavilyEvidenceGateway(db_path=path, task_id=task_id, client=client,
                                   clock=lambda: COMPLETED_AT, network_max_attempts=2)
    return path, task_id, gateway, requests, search_body, extract_body


def restarted(path, task_id, monkeypatch):
    # Replaying paid material must not require a currently configured key.
    monkeypatch.setattr(verification, "get_tavily_api_key", lambda **_: None)
    return TavilyEvidenceGateway(db_path=path, task_id=task_id, client=None,
        clock=lambda: COMPLETED_AT + timedelta(days=1), network_max_attempts=2)


def rows(path, table):
    assert table in {"k10_external_attempts", "llm_usage_events", "k10_tavily_response_receipts"}
    with sqlite3.connect(path) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


@pytest.mark.parametrize("interrupt_at", ["document", "checkpoint"])
def test_search_receipt_replays_without_post_or_duplicate_usage(tmp_path, monkeypatch, interrupt_at):
    path, task, gateway, calls, raw, _ = setup_gateway(tmp_path)
    if interrupt_at == "document":
        method = "append_document_version"
    else:
        method = "record_execution_checkpoint"
    original = getattr(store, method)

    def interrupted(**kwargs):
        if method != "record_execution_checkpoint" or kwargs.get("stage") == "tavily_evidence":
            raise sqlite3.OperationalError("interrupted after paid search response")
        return original(**kwargs)

    monkeypatch.setattr(store, method, interrupted)
    with pytest.raises(sqlite3.OperationalError, match="after paid search"):
        _fetch_bound(gateway, event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    before = {table: rows(path, table) for table in
              ("k10_external_attempts", "llm_usage_events", "k10_tavily_response_receipts")}
    assert len(before["llm_usage_events"]) == 1
    assert len(before["k10_tavily_response_receipts"]) == 1
    with sqlite3.connect(path) as conn:
        saved = json.loads(conn.execute("SELECT payload_json FROM k10_tavily_response_receipts").fetchone()[0])
        assert saved["response"]["raw_response"] == raw
        assert conn.execute("SELECT state,search_requests,search_credits FROM k10_external_attempts").fetchone() == ("succeeded", 1, 2)
    monkeypatch.setattr(store, method, original)
    recovered = _fetch_bound(restarted(path, task, monkeypatch), event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert recovered.state == "available" and len(recovered.eligible_documents) == 1
    assert calls == ["/search"]
    assert recovered.documents[0].fetched_at == COMPLETED_AT.isoformat(timespec="seconds")
    assert recovered.documents[0].excerpt == raw["results"][0]["content"]
    for table, values in before.items():
        assert rows(path, table) == values
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT status,network_attempt_count FROM k10_execution_item_checkpoints").fetchone() == ("completed", 1)
        assert conn.execute("SELECT COUNT(*) FROM k10_source_document_versions").fetchone()[0] == 1


def test_extract_receipt_replays_original_body_and_time(tmp_path, monkeypatch):
    path, task, gateway, calls, _, raw = setup_gateway(tmp_path)
    _frozen(path, task, 1)
    document = _fetch_bound(gateway, event=_event(), retrieved_at=NOW, cutoff_at=NOW).documents[0]
    question = {"questionId": "q-stage", "question": "是入围还是已签约？"}
    request = {"questionId": "q-stage", "sourceRef": {"documentId": document.document_id, "revision": document.revision},
               "reasonExcerptInsufficient": "核实原公告附带条件", "expectedJudgmentChange": "区分入围与签约"}
    original = store.append_document_version

    def interrupted(**kwargs):
        if kwargs.get("fetch_version") == "tavily-extract-fulltext-v1":
            raise sqlite3.OperationalError("interrupted after paid extract response")
        return original(**kwargs)

    monkeypatch.setattr(store, "append_document_version", interrupted)
    with pytest.raises(sqlite3.OperationalError, match="after paid extract"):
        gateway.fetch_fulltext(event=_event(), document=document, question=question, request=request, cutoff_at=NOW)
    before = {table: rows(path, table) for table in
              ("k10_external_attempts", "llm_usage_events", "k10_tavily_response_receipts")}
    assert len(before["llm_usage_events"]) == 2
    monkeypatch.setattr(store, "append_document_version", original)
    result = restarted(path, task, monkeypatch).fetch_fulltext(
        event=_event(), document=document, question=question, request=request, cutoff_at=NOW)
    assert result.state == "available" and calls == ["/search", "/extract"]
    assert result.documents[0].original_text == raw["results"][0]["raw_content"]
    assert result.documents[0].fetched_at == COMPLETED_AT.isoformat(timespec="seconds")
    for table, values in before.items():
        assert rows(path, table) == values
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT SUM(network_attempt_count) FROM k10_execution_item_checkpoints").fetchone()[0] == 2


def test_settlement_rolls_back_receipt_and_usage_together(tmp_path):
    path, _, gateway, calls, _, _ = setup_gateway(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER interrupt_usage BEFORE INSERT ON llm_usage_events "
                     "BEGIN SELECT RAISE(ABORT, 'usage write interrupted'); END")
    with pytest.raises(sqlite3.IntegrityError, match="usage write interrupted"):
        _fetch_bound(gateway, event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert calls == ["/search"]
    assert rows(path, "k10_tavily_response_receipts") == []
    assert rows(path, "llm_usage_events") == []
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT state,search_credits FROM k10_external_attempts").fetchone() == ("started", None)
    # With no durable response the unknown paid call cannot be silently sent again.
    result = _fetch_bound(gateway, event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert result.state == "pending" and calls == ["/search"]


@pytest.mark.parametrize("corruption", ["payload", "ledger_credits", "ledger_state"])
def test_corrupted_receipt_never_reissues_a_paid_request(tmp_path, monkeypatch, corruption):
    path, task, gateway, calls, _, _ = setup_gateway(tmp_path)
    original = store.append_document_version
    monkeypatch.setattr(store, "append_document_version", lambda **_: (_ for _ in ()).throw(RuntimeError("stop derivation")))
    with pytest.raises(RuntimeError, match="stop derivation"):
        _fetch_bound(gateway, event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    monkeypatch.setattr(store, "append_document_version", original)
    with sqlite3.connect(path) as conn:
        if corruption == "payload":
            conn.execute("UPDATE k10_tavily_response_receipts SET payload_json='{}'")
        elif corruption == "ledger_credits":
            conn.execute("UPDATE k10_external_attempts SET search_credits=99")
        else:
            conn.execute("UPDATE k10_external_attempts SET state='failed',error_code='insufficient_balance'")
    with pytest.raises(store.K10Conflict, match="Tavily"):
        _fetch_bound(restarted(path, task, monkeypatch), event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert calls == ["/search"]


@pytest.mark.parametrize("window,delay,should_wait", [("evening", 21601, True), ("morning", 60, True),
                                                     ("morning", 1080, False), ("historical", 21601, False)])
def test_throttle_uses_frozen_morning_deadline_not_old_evening_lifetime(tmp_path, window, delay, should_wait):
    path = tmp_path / "throttle.sqlite"
    initialize_schema(path)
    payload = {} if window == "historical" else {
        "runtimeContract": runtime_contract(), "windowKind": window,
        "deliveryDeadlineAt": "2026-09-07T09:20:00+08:00" if window == "morning" else None,
    }
    task = _bound_task(path, payload=payload)
    calls = []

    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(429, json={}, headers={"Retry-After": str(delay)})

    client = TavilySearchClient("isolated-fixture", transport=httpx.MockTransport(respond))
    gateway = TavilyEvidenceGateway(db_path=path, task_id=task, client=client,
                                   clock=lambda: COMPLETED_AT, network_max_attempts=2)
    if should_wait:
        with pytest.raises(ProviderThrottleYield):
            _fetch_bound(gateway, event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    else:
        result = _fetch_bound(gateway, event=_event(), retrieved_at=NOW, cutoff_at=NOW)
        assert result.coverage["reason"] == "network_attempts_exhausted"
    assert calls == ["/search"]
