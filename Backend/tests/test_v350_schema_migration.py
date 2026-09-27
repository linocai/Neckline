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


def test_pre_b92_schema9_cannot_forward_or_read_paid_history(tmp_path):
    db, _tasks, _receipts = _paid_historical_schema9(tmp_path)
    # Explicit historical snapshot has no new-start identity, regardless of
    # how this isolated test originally constructed its schema and paid rows.
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA application_id=0")
    from neckline.fresh_start import RetiredDataError
    with pytest.raises(RetiredDataError):
        schema.initialize_schema(db)
    with pytest.raises(RetiredDataError):
        with schema.read_connection(db):
            pytest.fail("retired paid history exposed")


def test_marked_noncurrent_schema_is_not_silently_migrated(tmp_path):
    db, _tasks, _receipts = _paid_historical_schema9(tmp_path)
    with pytest.raises(schema.SchemaUnavailable, match="旧版本迁移已退役"):
        schema.initialize_schema(db)
