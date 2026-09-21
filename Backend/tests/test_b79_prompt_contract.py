"""Use the actual model wire contract before returning any research fields."""
import json
import sqlite3

import pytest

from tests import v340_acceptance_fixture as base
from tests.test_v350_cli_api import DirectRoundTransport, read_actual_api


def assert_declared(value, shape, path="output"):
    if isinstance(value, dict) and isinstance(shape, dict) and shape:
        assert value.keys() <= shape.keys(), (path, value.keys() - shape.keys())
        for key, item in value.items():
            assert_declared(item, shape[key], path + "." + key)
    elif isinstance(value, list) and isinstance(shape, list) and shape:
        for item in value:
            assert_declared(item, shape[0], path + "[]")


@pytest.mark.parametrize("needs_query", [False, True])
def test_declared_round_contract_can_compare_or_request_necessary_search(tmp_path, monkeypatch, needs_query):
    original = base.DeterministicTransport

    class ContractBoundTransport(original if needs_query else DirectRoundTransport):
        def respond(self, request):
            payload = self._packet(request)
            response = super().respond(request)
            if payload.get("action") == "research_round":
                shape = payload["outputContract"]
                assert {"questions", "queryPaths", "fulltextRequests", "claims", "evidenceUpdates"} <= shape.keys()
                assert "answered" in shape["questions"][0]["state"]
                answer = json.loads(response.json()["choices"][0]["message"]["content"])
                # Current wires leave execution bookkeeping to the program.
                for key, retired in (("queryPaths", {"pathId", "state", "resultSummary"}),
                                     ("fulltextRequests", {"requestId", "state", "admissionRef"})):
                    if key in answer:
                        answer[key] = [{k: v for k, v in row.items() if k not in retired} for row in answer[key]]
                if needs_query and payload["evidencePacket"].get("queryPaths"):
                    packet = payload["evidencePacket"]
                    question = packet["questions"][0]
                    ref = packet["allowedEvidenceRefs"][-1]
                    answer["questions"] = [{**question, "state": "answered",
                        "knownEvidence": [ref], "missingEvidence": []}]
                    answer["evidenceUpdates"] = [{"claimId": question["claimIds"][0],
                        "sourceRef": ref, "relation": "partially_supports", "location": "excerpt",
                        "applicability": {}}]
                    instruction = "\n".join(item["content"] for item in json.loads(request.content)["messages"])
                    assert "不能只给 ID 和变化字段" in instruction
                    assert "更新已有命题或问题时只输出 ID" not in instruction
                    response = self._ok(answer)
                # An offline provider must not silently invent a capability
                # missing from the actual request sent to a real model.
                assert_declared(answer, shape)
                response = self._ok(answer)
            return response

    monkeypatch.setattr(base, "TITLE_COUNT", 1)
    monkeypatch.setattr(base, "DeterministicTransport", ContractBoundTransport)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name="declared-contract", selected_event_count=1)
    assert flow.task_status == "completed"
    assert len(flow.gateway_calls) == int(needs_query)
    assert flow.calls["research:research_round"] == (2 if needs_query else 1)
    report, _, _, _ = read_actual_api(flow.db_path)
    assert report["report"]["delivery"]["outcome"] == "complete"
    assert report["report"]["eveningCards"]
    if needs_query:
        with sqlite3.connect(flow.db_path) as conn:
            results = [json.loads(row[0]) for row in conn.execute("SELECT result_json FROM k10_research_round_results")]
        assert any(any(q["state"] == "answered" for q in r["questions"]) and r["evidenceUpdates"] for r in results)
