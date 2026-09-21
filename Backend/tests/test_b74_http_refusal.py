"""A definite HTTP refusal may receive one explicitly authorized continuation."""
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.metering import provider_spend_context
from neckline.k10.worker import run_once
from neckline.llm.base import ChatMessage
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b61_output_recovery import api_for
from tests.test_v330_b69 import _receipt_provider


@pytest.mark.parametrize('streaming', [False, True])
def test_private_http_diagnostic_does_not_change_receipt_or_public_failure(tmp_path, streaming):
    db, provider = _receipt_provider(tmp_path)
    provider.use_streaming = streaming
    calls = []
    body = 'request rejected: fixture credential must not be retained'
    def refuse(request):
        calls.append(request.content)
        return httpx.Response(400, text=body)
    with provider_spend_context(provider=provider, task_id='receipt-task', stage='investigation', item_key='step', attempt=1):
        result = provider.chat([ChatMessage(role='user', content='private request text')], enable_search=False,
            model_options={'maxTokens':128}, transport=httpx.MockTransport(refuse))
    assert result.error_code == 'provider_http_400' and result.content == '' and len(calls) == 1
    assert body not in result.reason and result.usage_unavailable
    files = list(tmp_path.glob('provider-diagnostics/*/*.json'))
    assert len(files) == 1 and files[0].stat().st_mode & 0o777 == 0o600
    detail = json.loads(files[0].read_text())
    assert detail['responseBody'] == body.replace('fixture', '[redacted]')
    assert 'private request text' not in files[0].read_text()
    with sqlite3.connect(db) as c:
        receipt = json.loads(c.execute('select payload_json from k10_model_response_receipts').fetchone()[0])
        assert c.execute('select state,input_sha256 from k10_external_attempts').fetchone() == ('failed',detail['requestSha256'])
    assert receipt['rawResponses'] == [] and receipt['responseReceived'] is False


def test_diagnostic_disk_failure_cannot_trigger_another_post(tmp_path, monkeypatch):
    db, provider = _receipt_provider(tmp_path)
    calls = []
    def refuse(request):
        calls.append(1)
        return httpx.Response(400, json={'error':{'message':'invalid request'}})
    mkdir = Path.mkdir
    def fail(path, *a, **k):
        if 'provider-diagnostics' in path.parts:
            raise OSError('disk unavailable')
        return mkdir(path, *a, **k)
    monkeypatch.setattr(Path, 'mkdir', fail)
    with provider_spend_context(provider=provider, task_id='receipt-task', stage='investigation', item_key='step', attempt=1):
        result = provider.chat([ChatMessage(role='user', content='one request')], enable_search=False,
            model_options={'maxTokens':128}, transport=httpx.MockTransport(refuse))
    assert result.error_code == 'provider_http_400' and calls == [1]


@pytest.mark.parametrize('status,refuse_recovery', [(400,False), (400,True), (403,True)])
def test_real_cli_http_refusal_is_not_auto_bypassed(tmp_path, monkeypatch, status, refuse_recovery):
    real_client = e2e._HTTPX_CLIENT
    phase = ['initial']
    refused_wires = []
    accepted_wires = []
    def client(**kwargs):
        original = kwargs['transport']
        def intercept(request):
            wire = json.loads(request.content)
            text = wire['messages'][-1]['content']
            payload = json.loads(text.split('<untrusted-k10-evidence>\n',1)[1].split('\n</untrusted-k10-evidence>',1)[0])
            if payload.get('action') == 'research_round':
                if phase[0] == 'initial' or refuse_recovery:
                    refused_wires.append(request.content)
                    return httpx.Response(status, json={'error':{'message':'synthetic request refusal'}})
                accepted_wires.append(request.content)
            return original.handle_request(request)
        return real_client(**{**kwargs, 'transport':httpx.MockTransport(intercept)})
    monkeypatch.setattr(e2e, '_HTTPX_CLIENT', client)
    db, tid, first, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True, cli_entry=True)
    if status == 400:
        # An event-local refusal is published as an honest partial report. It
        # cannot be reopened into another paid request after publication.
        assert first.status == 'completed' and len(refused_wires) == 2
        assert refused_wires[0] == refused_wires[1]
        report = api_for(db).get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']
        assert report['status'] == 'partial' and report['coverageGaps']
        handlers = pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet')
        assert run_once(db_path=db,task_id=tid,worker_id='ordinary',lease_for=timedelta(minutes=5),handlers=handlers,clock=lambda:e2e.RUN_AT) is None
        with pytest.raises(RuntimeError, match='只有当前 failed 或 not_configured'):
            recover_scan(db_path=db,scan_id=store.task_execution_input(task_id=tid,db_path=db)['checkpoint']['scanId'],
                execution_config_id='b39-execution',execution_config_revision=1,
                confirmed_input_sha256=frozen_scan_input_sha256(scan_id=store.task_execution_input(task_id=tid,db_path=db)['checkpoint']['scanId'],db_path=db),now=e2e.RUN_AT)
        assert len(refused_wires) == 2
        return
    assert first.status == 'failed' and len(refused_wires) == 1
    original = store.task_execution_input(task_id=tid,db_path=db)
    scan = original['checkpoint']['scanId']
    frozen = frozen_scan_input_sha256(scan_id=scan,db_path=db)
    with sqlite3.connect(db) as c:
        completed = c.execute("select * from k10_execution_item_checkpoints where task_id=? and status='completed'",(tid,)).fetchall()
        ledger = c.execute('select * from k10_external_attempts where task_id=?',(tid,)).fetchall()
    handlers = pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet')
    # An ordinary worker wake-up cannot renew a failed task's HTTP budget.
    assert run_once(db_path=db,task_id=tid,worker_id='ordinary',lease_for=timedelta(minutes=5),handlers=handlers,clock=lambda:e2e.RUN_AT) is None
    phase[0] = 'recovery'
    def recover():
        assert recover_scan(db_path=db,scan_id=scan,execution_config_id='b39-execution',execution_config_revision=1,
            confirmed_input_sha256=frozen,now=e2e.RUN_AT)==tid
        return run_once(db_path=db,task_id=tid,worker_id='b74',lease_for=timedelta(minutes=5),handlers=handlers,clock=lambda:e2e.RUN_AT)
    second = recover()
    if refuse_recovery:
        expected_calls = 1
        assert second.status == 'failed' and len(refused_wires) == expected_calls
        assert recover().status == 'failed' and len(refused_wires) == expected_calls
    else:
        assert second.status == 'completed' and accepted_wires
        assert accepted_wires[0] == refused_wires[0] == refused_wires[1]
        assert api_for(db).get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']['status']=='completed'
    with sqlite3.connect(db) as c:
        assert all(r in c.execute('select * from k10_execution_item_checkpoints where task_id=?',(tid,)).fetchall() for r in completed)
        assert all(r in c.execute('select * from k10_external_attempts where task_id=?',(tid,)).fetchall() for r in ledger)
        assert c.execute('select count(*) from k10_tasks where task_id=?',(tid,)).fetchone()==(1,)
    assert frozen_scan_input_sha256(scan_id=scan,db_path=db)==frozen
    final = store.task_execution_input(task_id=tid,db_path=db)
    assert final['checkpoint']['executionStartedAt']==original['checkpoint']['executionStartedAt']
