"""B78 Schema-9 forward migration preserves paid-receipt recovery facts."""
from pathlib import Path
import sqlite3

import httpx
import pytest

from neckline.k10 import schema
from neckline.k10.metering import provider_spend_context
from neckline.llm.base import ChatMessage
from tests.test_v330_b69 import _receipt_provider


_V10_TABLES = (
    "k10_v2_report_delivery_metadata",
    "k10_v2_report_materials",
    "k10_tavily_response_receipts",
    "k10_morning_review_work_items",
    "k10_research_round_results",
)


def _paid_historical_schema9(tmp_path: Path) -> tuple[Path, tuple[tuple[str, ...], ...], tuple[tuple[str, ...], ...]]:
    """Build an owned paid Schema-9 ledger before the B78 extensions exist."""
    db, provider = _receipt_provider(tmp_path)
    response = httpx.MockTransport(lambda _request: httpx.Response(200, json={
        "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
    }))
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="schema9", attempt=1):
        assert provider.chat([ChatMessage(role="user", content="preserve historical paid response")],
                             enable_search=False, model_options={"maxTokens": 128}, transport=response).ok
    with sqlite3.connect(db) as conn:
        task_rows = tuple(conn.execute(
            "SELECT task_id,input_version,payload_json FROM k10_tasks ORDER BY task_id"
        ))
        receipt_rows = tuple(conn.execute(
            "SELECT attempt_id,task_id,request_sha256,payload_json,payload_sha256,received_at "
            "FROM k10_model_response_receipts ORDER BY attempt_id"
        ))
    assert task_rows and receipt_rows
    with sqlite3.connect(db) as conn:
        for table in _V10_TABLES:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
            conn.execute(f"DROP TABLE {table}")
        conn.execute("DELETE FROM k10_schema_migrations WHERE version=10")
        assert conn.execute("SELECT MAX(version) FROM k10_schema_migrations").fetchone() == (9,)
    return db, task_rows, receipt_rows


def test_schema9_forwards_to_10_without_rewriting_paid_ledger_rows(tmp_path):
    db, before_tasks, before_receipts = _paid_historical_schema9(tmp_path)

    assert schema.initialize_schema(db) == 10
    assert schema.schema_version(db) == 10
    with sqlite3.connect(db) as conn:
        assert tuple(conn.execute(
            "SELECT task_id,input_version,payload_json FROM k10_tasks ORDER BY task_id"
        )) == before_tasks
        assert tuple(conn.execute(
            "SELECT attempt_id,task_id,request_sha256,payload_json,payload_sha256,received_at "
            "FROM k10_model_response_receipts ORDER BY attempt_id"
        )) == before_receipts
        assert {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?,?)", _V10_TABLES
        )} == set(_V10_TABLES)


def test_schema9_to_10_failure_rolls_back_extensions_and_paid_ledger(tmp_path, monkeypatch):
    db, before_tasks, before_receipts = _paid_historical_schema9(tmp_path)
    original = schema._apply_v10

    def interrupted(conn):
        original(conn)
        raise RuntimeError("injected b78 migration interruption")

    monkeypatch.setattr(schema, "_apply_v10", interrupted)
    with pytest.raises(RuntimeError, match="interruption"):
        schema.initialize_schema(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT MAX(version) FROM k10_schema_migrations").fetchone() == (9,)
        assert tuple(conn.execute(
            "SELECT task_id,input_version,payload_json FROM k10_tasks ORDER BY task_id"
        )) == before_tasks
        assert tuple(conn.execute(
            "SELECT attempt_id,task_id,request_sha256,payload_json,payload_sha256,received_at "
            "FROM k10_model_response_receipts ORDER BY attempt_id"
        )) == before_receipts
        assert not {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?,?)", _V10_TABLES
        )}
