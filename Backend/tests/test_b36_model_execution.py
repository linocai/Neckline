"""Real provider wire tests for explicitly bounded B36 model execution."""
import json

import httpx
import pytest

from neckline.llm.base import ChatMessage
from neckline.llm.openai_compat import OpenAICompatProvider


def provider():
    p = OpenAICompatProvider(api_key="isolated-test-only", model="deepseek-v4-pro",
                             api_url="https://example.invalid/chat/completions", has_web_search=False)
    p.max_attempts = 1
    return p


def response(*, finish="stop", content='{"events":[]}'):
    return {"choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 130, "completion_tokens": 20, "total_tokens": 150}}


def test_explicit_execution_options_reach_wire_without_model_switch():
    seen = []
    transport = httpx.MockTransport(lambda req: (seen.append(json.loads(req.content)), httpx.Response(200, json=response()))[1])
    p = provider()
    for options in [
        {"thinking": {"type": "disabled"}, "maxTokens": 2048},
        {"thinking": {"type": "enabled"}, "reasoningEffort": "high", "maxTokens": 8192},
    ]:
        assert p.chat([ChatMessage("user", "json")], enable_search=False,
                      model_options=options, response_format={"type": "json_object"}, transport=transport).ok
    assert seen[0]["model"] == seen[1]["model"] == "deepseek-v4-pro"
    assert seen[0]["thinking"] == {"type": "disabled"} and seen[0]["max_tokens"] == 2048
    assert "reasoning_effort" not in seen[0]
    assert seen[1]["reasoning_effort"] == "high" and seen[1]["max_tokens"] == 8192
    assert all(item["response_format"] == {"type": "json_object"} for item in seen)


@pytest.mark.parametrize("options", [
    {"model": "anything"}, {"maxTokens": True}, {"maxTokens": 0},
    {"thinking": {"type": []}}, {"thinking": {"type": "disabled", "extra": "x"}},
    {"reasoningEffort": []}, {"thinking": {"type": "disabled"}, "reasoningEffort": "high"},
])
def test_bad_execution_options_never_reach_transport(options):
    def forbidden(req):
        pytest.fail("invalid options made an upstream request")
    with pytest.raises(ValueError):
        provider().chat([ChatMessage("user", "json")], enable_search=False,
                        model_options=options, transport=httpx.MockTransport(forbidden))


@pytest.mark.parametrize("finish,content,code", [
    ("length", '{"events":[]}', "response_truncated"),
    ("length", '{"events":[', "response_truncated"),
    ("stop", "", "response_empty"),
    ("content_filter", "", "response_filtered"),
])
def test_noncomplete_response_never_success_and_still_accounts_paid_tokens(finish, content, code):
    result = provider().chat([ChatMessage("user", "json")], enable_search=False,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response(finish=finish, content=content))))
    assert not result.ok and result.error_code == code and result.finish_reason == finish
    assert result.total_tokens == 150 and result.usage_unavailable is False
    assert result.content == ""


def test_transport_exception_diagnostic_does_not_disclose_request_or_credentials(caplog):
    def broken(req):
        raise httpx.ConnectError("secret-token-and-private-url-must-not-leak", request=req)
    result = provider().chat([ChatMessage("user", "json")], enable_search=False,
                             transport=httpx.MockTransport(broken))
    assert not result.ok and result.error_code == "provider_transport"
    assert "secret-token" not in result.reason and "secret-token" not in caplog.text


def test_malformed_choices_returns_safe_failure_with_reported_usage():
    body = response()
    body["choices"] = ["unexpected confidential content"]
    result = provider().chat([ChatMessage("user", "json")], enable_search=False,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
    assert not result.ok and result.error_code == "response_structure_invalid"
    assert "confidential" not in result.reason and result.total_tokens == 150


def test_truncation_is_persisted_as_failed_usage_with_safe_reason(tmp_path):
    from neckline.db import init_schema, readonly_connection
    from neckline.k10.metering import MeteredProvider

    db = tmp_path / "usage.sqlite"
    init_schema(db)
    p = MeteredProvider(ledger_db=db, ledger_task="discovery", api_key="isolated-only",
                        model="deepseek-v4-pro", api_url="https://example.invalid/chat/completions")
    result = p.chat([ChatMessage("user", "json")], enable_search=False,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response(finish="length"))))
    assert not result.ok
    with readonly_connection(db) as conn:
        row = conn.execute("SELECT outcome,total_tokens,failure_reason FROM llm_usage_events").fetchone()
    assert row == ("failed", 150, "response_truncated")


@pytest.mark.parametrize("content", ['{"broken":', '[{"events":[]}]', 'secret-invalid-upstream-content'])
def test_json_mode_invalid_object_does_not_enter_success_ledger(content):
    result = provider().chat([ChatMessage("user", "json")], enable_search=False,
        response_format={"type":"json_object"},
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response(content=content))))
    assert not result.ok and result.error_code == "response_json_invalid"
    assert result.total_tokens == 150 and result.content == "" and content not in result.reason
