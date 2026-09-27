"""A parent fence stops publication and new calls, never an already paid receipt."""
import json
import sqlite3
from threading import Event

import httpx

from neckline.k10.metering import provider_spend_context
from neckline.llm.base import ChatMessage
from tests.test_v330_b69 import _receipt_provider, _restarted_receipt_provider


def test_fence_before_body_preserves_exact_paid_reply_and_usage(tmp_path):
    database, provider = _receipt_provider(tmp_path)
    fence = Event()
    provider.response_fence = fence
    calls = []
    body = {
        "choices": [{"message": {"content": '{"late":true}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20},
    }

    def wire(request):
        calls.append(request.content)
        fence.set()  # Parent seals before a complete HTTP response reaches the provider.
        return httpx.Response(200, json=body)

    options = {"enable_search": False, "model_options": {"maxTokens": 128},
               "transport": httpx.MockTransport(wire)}
    messages = [ChatMessage(role="user", content="late exact paid reply")]
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="morning",
                                item_key="company-a:review-round:0", attempt=1):
        result = provider.chat(messages, **options)
        blocked = provider.chat([ChatMessage(role="user", content="must not start another call")], **options)
    assert not result.ok and result.error_code == "provider_response_after_report_close"
    assert not blocked.ok and blocked.error_code == result.error_code
    assert len(calls) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT state FROM k10_external_attempts").fetchall() == [("succeeded",)]
        receipt = json.loads(connection.execute("SELECT payload_json FROM k10_model_response_receipts").fetchone()[0])
        assert receipt["rawReceiptOnly"] is True and receipt["rawResponses"] == [body]
        assert (receipt["promptTokens"], receipt["completionTokens"], receipt["totalTokens"]) == (13, 7, 20)
        assert connection.execute("SELECT prompt_tokens,completion_tokens,total_tokens FROM llm_usage_events").fetchall() == [(13, 7, 20)]
    # Local exact-input revalidation remains possible for accounting/recovery;
    # it does not create another task or publish/reopen an existing report.
    restarted = _restarted_receipt_provider(database)
    with provider_spend_context(provider=restarted, task_id="receipt-task", stage="morning",
                                item_key="company-a:review-round:0", attempt=1, receipt_only=True):
        replayed = restarted.chat(messages, **options)
    assert replayed.ok and replayed.local_reuse and json.loads(replayed.content) == {"late": True}
    assert len(calls) == 1
