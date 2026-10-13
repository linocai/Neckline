"""Pool exclusions are local routing decisions, never batch execution failures."""
from datetime import timedelta
import json
import sqlite3

import httpx
import pytest

from neckline.k10 import pipeline, store, title_runtime
from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
from neckline.k10.title_triage import TitleTriageProtocolError
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e


def edit_responses(monkeypatch, edit):
    transport = httpx.MockTransport
    def wrap(handler):
        def respond(request):
            response = handler(request)
            body = response.json()
            value = json.loads(body['choices'][0]['message']['content'])
            edit(value)
            body['choices'][0]['message']['content'] = json.dumps(value)
            return httpx.Response(response.status_code, json=body, headers=response.headers)
        return transport(respond)
    monkeypatch.setattr(httpx, 'MockTransport', wrap)


def title_codes(value, codes):
    if 'items' in value:
        for row in value['items']:
            row['companyCodes'] = codes


@pytest.mark.parametrize('codes,expected', [
    (['300002.SZ', '300487.SZ'], ['300002.SZ']),
    (['300487.SZ'], []),
    (['300002.SZ', '300487.SZ', '300002.SZ'], ['300002.SZ']),
])
def test_cli_worker_discards_only_outside_title_hints(tmp_path, monkeypatch, codes, expected):
    edit_responses(monkeypatch, lambda value: title_codes(value, codes))
    db, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True, require_title_hint=bool(expected))
    assert task.status == 'completed'
    assert calls.count('titleBatch') == 1 and calls.count('understand') == 1
    with sqlite3.connect(db) as conn:
        hints = conn.execute('SELECT company_codes_json FROM k10_v2_title_company_hints WHERE task_id=?', (task_id,)).fetchall()
    assert [json.loads(row[0]) for row in hints] == [expected]
    assert [card['companyCode'] for card in read_report(db_path=db)['eveningCards']] == ['300002.SZ']



def test_mixed_research_company_hints_keep_in_pool_work(tmp_path, monkeypatch):
    """A direct B78 research reply may contain a discardable pool outsider.

    The in-pool mapping remains publishable; the response must not revive the
    old multi-stage research route or fail the whole event.
    """
    import socket
    from tests import v340_acceptance_fixture as base
    from tests.test_v350_cli_api import DirectRoundTransport

    class MixedScopeTransport(DirectRoundTransport):
        def respond(self, request):
            response = super().respond(request)
            payload = self._packet(request)
            if payload.get('action') != 'research_round':
                return response
            body = response.json()
            value = json.loads(body['choices'][0]['message']['content'])
            outsider = '300487.SZ'
            conclusion = value['conclusion']
            conclusion['companyMappings'].append({
                **conclusion['companyMappings'][0], 'companyCode': outsider,
            })
            value['companyAssessments'].append({
                **value['companyAssessments'][0], 'companyCode': outsider,
            })
            body['choices'][0]['message']['content'] = json.dumps(value)
            return httpx.Response(response.status_code, json=body, headers=response.headers)

    monkeypatch.setattr(base, 'TITLE_COUNT', 130)
    monkeypatch.setattr(base, 'DeterministicTransport', MixedScopeTransport)
    monkeypatch.setattr(socket.socket, 'connect', base._deny_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', base._deny_network)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name='mixed-direct-pool', selected_event_count=1)
    assert flow.task_status == 'completed', flow
    assert flow.calls.get('research:research_round') == 1
    assert not any(name.startswith('forbidden:') for name in flow.calls)
    assert [row['companyCode'] for row in read_report(db_path=flow.db_path)['eveningCards']] == [flow.company_codes[0]]


# Resolve after production implementation; absent on the red regression run.
title_runtime_filter = getattr(title_runtime, '_filter_company_hints', None)
