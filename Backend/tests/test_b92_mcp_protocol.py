"""Offline protocol fixtures, not recordings or a live Jin10 acceptance claim.

These exercise the public client against the 2025-11-25 Streamable HTTP
contract, including an SSE stream that stays open after its response.
"""
from __future__ import annotations

import json

import httpx
import pytest

from neckline.k10.jin10_mcp import ENDPOINT, PROTOCOL_VERSION, Jin10Client, Jin10Error


TOKEN = "b92-isolated-protocol-token"
SESSION = "b92-isolated-session"


def rpc(body, result, *, headers=None):
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result},
                          headers=headers)


class ProtocolWire:
    def __init__(self, reply=None, *, session=True, tools=None):
        self.requests = []
        self.reply = reply
        self.session = session
        self.tools = tools if tools is not None else [{
            "name": "search_flash", "inputSchema": {
                "type": "object", "properties": {"keyword": {"type": "string"}},
                "required": ["keyword"], "additionalProperties": False,
            },
        }]

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append((request, body))
        assert str(request.url) == ENDPOINT
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert {"application/json", "text/event-stream"} <= {
            part.strip() for part in request.headers["accept"].split(",")
        }
        method = body["method"]
        if method == "initialize":
            assert "mcp-session-id" not in request.headers
            assert body["params"]["protocolVersion"] == PROTOCOL_VERSION
            return rpc(body, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                              "serverInfo": {"name": "isolated-wire", "version": "1"}},
                       headers={"MCP-Session-Id": SESSION} if self.session else {})
        assert request.headers["mcp-protocol-version"] == PROTOCOL_VERSION
        if self.session:
            assert request.headers["mcp-session-id"] == SESSION
        else:
            assert "mcp-session-id" not in request.headers
        if method == "notifications/initialized":
            assert "id" not in body
            return httpx.Response(202)
        if method == "tools/list":
            return rpc(body, {"tools": self.tools})
        assert method == "tools/call"
        if self.reply is not None:
            return self.reply(request, body)
        return rpc(body, {"structuredContent": {"status": 200, "data": []}})

    @property
    def tool_calls(self):
        return [body for _, body in self.requests if body["method"] == "tools/call"]


def client(wire):
    return Jin10Client(token=TOKEN, timeout_seconds=1, transport=httpx.MockTransport(wire))


@pytest.mark.parametrize("session", [False, True])
def test_json_handshake_headers_and_numeric_business_success(session):
    wire = ProtocolWire(session=session)
    with client(wire) as current:
        first = current.call_tool("search_flash", {"keyword": "测试公司"})
        second = current.call_tool("search_flash", {"keyword": "另一家公司"})
    assert first == second == {"status": 200, "data": []}
    methods = [body["method"] for _, body in wire.requests]
    assert methods == ["initialize", "notifications/initialized", "tools/list", "tools/call", "tools/call"]
    ids = [body["id"] for _, body in wire.requests if "id" in body]
    assert len(ids) == len(set(ids))
    assert TOKEN not in json.dumps(first) and SESSION not in json.dumps(first)


class ResponseThenOpenStream(httpx.SyncByteStream):
    def __init__(self, body, *, prime=False):
        self.body = body
        self.prime = prime
        self.closed = False

    def __iter__(self):
        if self.prime:
            yield b"id: stream-start\ndata:\n\n"
        notice = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}}
        yield ("data: " + json.dumps(notice) + "\n\n").encode()
        response = {"jsonrpc": "2.0", "id": self.body["id"],
                    "result": {"structuredContent": {"status": 200, "data": []}}}
        yield ("data: " + json.dumps(response) + "\n\n").encode()
        raise AssertionError("Client must stop reading after its response, not wait for SSE to close")

    def close(self):
        self.closed = True


@pytest.mark.parametrize("prime", [False, True])
def test_sse_skips_progress_and_empty_prime_without_waiting_for_close(prime):
    streams = []
    def reply(request, body):
        stream = ResponseThenOpenStream(body, prime=prime)
        streams.append(stream)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
    wire = ProtocolWire(reply)
    with client(wire) as current:
        assert current.call_tool("search_flash", {"keyword": "测试公司"})["status"] == 200
    assert len(wire.tool_calls) == 1 and streams[0].closed


@pytest.mark.parametrize("status,code", [(401, "unauthorized"), (429, "rate_limited"),
                                          (500, "http_500"), (302, "redirect_rejected")])
def test_http_errors_and_redirects_do_not_reissue_or_leak_auth(status, code):
    wire = ProtocolWire(lambda request, body: httpx.Response(
        status, headers={"location": "https://untrusted.invalid/steal"}, text=TOKEN))
    with client(wire) as current, pytest.raises(Jin10Error) as error:
        current.call_tool("search_flash", {"keyword": "测试公司"})
    assert error.value.code == code
    assert TOKEN not in str(error.value)
    assert len(wire.tool_calls) == 1
    assert all(request.url.host == "mcp.jin10.com" for request, _ in wire.requests)


@pytest.mark.parametrize("result,code", [
    ({"isError": True, "content": [{"type": "text", "text": "private"}]}, "tool_is_error"),
    ({"structuredContent": {"status": 403, "data": []}}, "business_status_error"),
    ({"content": [{"type": "text", "text": "empty is not success"}]}, "structured_content_missing"),
])
def test_rpc_success_does_not_disguise_business_or_tool_failure(result, code):
    wire = ProtocolWire(lambda request, body: rpc(body, result))
    with client(wire) as current, pytest.raises(Jin10Error) as error:
        current.call_tool("search_flash", {"keyword": "测试公司"})
    assert error.value.code == code and len(wire.tool_calls) == 1


def test_raw_result_retains_rejected_business_reply_for_durable_revalidation():
    rejected = {"structuredContent": {"status": 200, "unrecognized_shape": {"raw": "evidence"}}}
    wire = ProtocolWire(lambda request, body: rpc(body, rejected))
    with client(wire) as current:
        assert current.call_tool_raw("search_flash", {"keyword": "测试公司"}) == rejected
    assert len(wire.tool_calls) == 1


def test_wrong_rpc_id_cannot_be_accepted_as_our_evidence():
    wire = ProtocolWire(lambda request, body: httpx.Response(200, json={
        "jsonrpc": "2.0", "id": body["id"] + 1, "result": {"structuredContent": {"status": 200}},
    }))
    with client(wire) as current, pytest.raises(Jin10Error) as error:
        current.call_tool("search_flash", {"keyword": "测试公司"})
    assert error.value.code == "jsonrpc_id_mismatch" and len(wire.tool_calls) == 1


def test_sent_timeout_remains_unknown_and_is_not_automatically_repeated():
    def timeout(request, body):
        raise httpx.ReadTimeout("private transport detail " + TOKEN, request=request)
    wire = ProtocolWire(timeout)
    with client(wire) as current, pytest.raises(Jin10Error) as error:
        current.call_tool("search_flash", {"keyword": "测试公司"})
    assert error.value.unknown and error.value.code == "transport_unknown"
    assert TOKEN not in str(error.value) and len(wire.tool_calls) == 1


@pytest.mark.parametrize("endpoint", ["http://mcp.jin10.com/mcp", "https://untrusted.invalid/mcp",
                                      "https://mcp.jin10.com.untrusted.invalid/mcp",
                                      "https://name:password@mcp.jin10.com/mcp"])
def test_unapproved_endpoint_is_rejected_before_credentials_can_leave(endpoint):
    wire = ProtocolWire()
    with pytest.raises(ValueError):
        Jin10Client(token=TOKEN, endpoint=endpoint, timeout_seconds=1, transport=httpx.MockTransport(wire))
    assert wire.requests == []


def test_unapproved_tool_never_triggers_handshake_or_provider_call():
    wire = ProtocolWire()
    with client(wire) as current, pytest.raises(ValueError):
        current.call_tool("get_quote", {})
    assert wire.requests == []


def test_expired_session_is_discarded_and_next_call_reinitializes_without_old_id():
    calls = []
    def reply(request, body):
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(404)
        return rpc(body, {"structuredContent": {"status": 200, "data": []}})
    wire = ProtocolWire(reply)
    with client(wire) as current:
        with pytest.raises(Jin10Error) as error:
            current.call_tool("search_flash", {"keyword": "测试公司"})
        assert error.value.code == "session_expired"
        assert len(calls) == 1, "Do not silently repeat a business request"
        assert current.call_tool("search_flash", {"keyword": "另一个明确请求"})["status"] == 200
    assert sum(body["method"] == "initialize" for _, body in wire.requests) == 2


@pytest.mark.parametrize("schema", [
    {"type": "object", "properties": {"keyword": {"type": "integer"}}, "required": ["keyword"]},
    {"type": "object", "properties": {"keyword": {"type": "string"}, "newRequired": {"type": "string"}},
     "required": ["keyword", "newRequired"]},
])
def test_changed_advertised_tool_schema_fails_before_business_call(schema):
    wire = ProtocolWire(tools=[{"name": "search_flash", "inputSchema": schema}])
    with client(wire) as current, pytest.raises((ValueError, Jin10Error)):
        current.call_tool("search_flash", {"keyword": "测试公司"})
    assert wire.tool_calls == [], "Discovering a schema without checking it is not a compatibility check"


def test_malformed_response_after_tool_send_is_not_proof_of_unbilled_failure():
    wire = ProtocolWire(lambda request, body: httpx.Response(
        200, headers={"content-type": "application/json"}, content=b'{"jsonrpc":"2.0","result":'))
    with client(wire) as current, pytest.raises(Jin10Error) as error:
        current.call_tool("search_flash", {"keyword": "测试公司"})
    assert len(wire.tool_calls) == 1
    assert error.value.unknown, "An unusable paid response must not permit a fresh automatic POST"
