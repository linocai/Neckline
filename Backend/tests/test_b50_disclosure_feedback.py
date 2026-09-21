"""A direct B78 receipt repairs invalid uncertainty disclosure once, in place."""
from __future__ import annotations

from copy import deepcopy
import json

import httpx
import pytest

from neckline.k10 import pipeline
import tests.test_v310_pipeline_e2e as e2e
from tests.test_v310_pipeline_e2e import _run


@pytest.mark.parametrize("mutation,field,expected", [
    ({"originStatus": "unknown", "originEvidenceRef": {"documentId": "input", "revision": 1}},
     "evidenceDisclosure.originEvidenceRef", "null_when_origin_unknown"),
    ({"verificationStatus": "unverified", "unverifiedReasons": []},
     "evidenceDisclosure.unverifiedReasons", "non_empty_string_array_when_unverified"),
    ({"isRumor": True, "verificationStatus": "verified"},
     "evidenceDisclosure.verificationStatus", "not_verified_when_isRumor_true"),
])
def test_direct_round_disclosure_has_specific_bounded_repair(tmp_path, monkeypatch, mutation, field, expected):
    attempts, feedback = [0], []
    original_transport = e2e._http_transport

    def invalid_first_reply(mp, **kwargs):
        calls = original_transport(mp, **kwargs)
        transport = httpx.Client()._transport

        def respond(request):
            response = transport.handle_request(request)
            message = json.loads(request.content)["messages"][-1]["content"]
            packet = json.loads(message.split("<untrusted-k10-evidence>\n", 1)[1]
                               .split("\n</untrusted-k10-evidence>", 1)[0])
            if packet.get("action") != "research_round":
                return response
            attempts[0] += 1
            if attempts[0] != 1:
                return response
            body = response.json()
            value = json.loads(body["choices"][0]["message"]["content"])
            value["companyAssessments"][0]["evidenceDisclosure"].update(deepcopy(mutation))
            body["choices"][0]["message"]["content"] = json.dumps(value)
            return httpx.Response(200, json=body)

        mp.setattr(httpx, "Client", lambda **options: e2e._HTTPX_CLIENT(
            **{**options, "transport": httpx.MockTransport(respond)}))
        return calls

    original = pipeline.DeepSeekDiscoveryModel.advance_research_round

    def observe_feedback(self, **kwargs):
        if self._thread_usage.repair_feedback:
            feedback.append(self._thread_usage.repair_feedback)
        return original(self, **kwargs)

    monkeypatch.setattr(e2e, "_http_transport", invalid_first_reply)
    monkeypatch.setattr(pipeline.DeepSeekDiscoveryModel, "advance_research_round", observe_feedback)
    _, _, task, calls, gateway = _run(tmp_path, monkeypatch)

    assert task.status == "completed"
    assert attempts == [2]
    assert calls.count("research:research_round") == 2
    assert gateway.search_paths == []
    assert feedback and {"field": field, "expected": expected}.items() <= feedback[0]["validationErrors"][0].items()
