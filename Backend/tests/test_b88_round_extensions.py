"""Harmless wire extensions must not discard complete paid research."""
from copy import deepcopy
import json
import socket

import httpx
import pytest

from neckline.k10.research_contracts import ResearchContractError, ResearchRoundResult
from tests.test_v350_research_round import _complete_round
from tests.test_v350_cli_api import DirectRoundTransport, read_actual_api
from tests import v340_acceptance_fixture as acceptance


@pytest.mark.parametrize('extension', [None, '', '  ', [], {}])
def test_empty_wire_extension_is_not_business_content(extension):
    raw = {**_complete_round(), 'companyMappings_note': extension}
    before = deepcopy(raw)
    assert ResearchRoundResult.from_dict(raw, model_reply=True) == ResearchRoundResult.from_dict(_complete_round())
    assert raw == before
    with pytest.raises(ResearchContractError):
        ResearchRoundResult.from_dict(raw)  # Stored canonical state stays strict.


def test_identical_conclusion_repeat_is_removed_only_on_model_boundary():
    raw = _complete_round()
    raw['companyMappings'] = deepcopy(raw['conclusion']['companyMappings'])
    assert ResearchRoundResult.from_dict(raw, model_reply=True) == ResearchRoundResult.from_dict(_complete_round())
    with pytest.raises(ResearchContractError):
        ResearchRoundResult.from_dict(raw)
    raw['companyMappings'][0]['uncertainty'] = 'A conflicting claim'
    with pytest.raises(ResearchContractError):
        ResearchRoundResult.from_dict(raw, model_reply=True)


@pytest.mark.parametrize('extension', ['new claim', 0, False, [None], {'claim': None}])
def test_nonempty_unknown_wire_fields_remain_rejected(extension):
    with pytest.raises(ResearchContractError):
        ResearchRoundResult.from_dict({**_complete_round(), 'unknown': extension}, model_reply=True)


def test_real_cli_worker_publishes_canonical_result_without_extra_paid_repair(tmp_path, monkeypatch):
    class ExtensionsTransport(DirectRoundTransport):
        def respond(self, request):
            response = super().respond(request)
            if self._packet(request).get('action') == 'research_round':
                body = response.json()
                value = json.loads(body['choices'][0]['message']['content'])
                value['companyMappings_note'] = None
                value['companyMappings'] = deepcopy(value['conclusion']['companyMappings'])
                body['choices'][0]['message']['content'] = json.dumps(value)
                return httpx.Response(200, json=body)
            return response
    monkeypatch.setattr(acceptance, 'TITLE_COUNT', 2)
    monkeypatch.setattr(acceptance, 'DeterministicTransport', ExtensionsTransport)
    monkeypatch.setattr(socket.socket, 'connect', acceptance._deny_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', acceptance._deny_network)
    flow = acceptance.run_full_scale_flow(tmp_path, monkeypatch, name='b88-extensions', selected_event_count=1)
    assert flow.task_status == 'completed' and flow.calls['research:research_round'] == 1
    report = read_actual_api(flow.db_path)[0]['report']
    assert report['delivery']['outcome'] == 'complete' and len(report['eveningCards']) == 1
