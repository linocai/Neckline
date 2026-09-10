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
    (['300002.SZ', '300487.SZ', '300002.SZ'], ['300002.SZ', '300002.SZ']),
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


def test_recover_real_completed_legacy_title_checkpoint_without_rebilling(tmp_path, monkeypatch):
    edit_responses(monkeypatch, lambda value: title_codes(value, ['300002.SZ', '300487.SZ']))
    original = pipeline._CheckpointedDiscoveryModel.run_title_operation
    # Reproduce B59: raw normalized response becomes completed, then pool check fails.
    with monkeypatch.context() as legacy:
        legacy.setattr(title_runtime, '_filter_company_hints', lambda value, allowed: value)
        def saved_then_rejected(self, **kwargs):
            value = original(self, **kwargs)
            if kwargs['stage'] == 'titleBatch':
                raise TitleTriageProtocolError('标题模型引用池外公司')
            return value
        legacy.setattr(pipeline._CheckpointedDiscoveryModel, 'run_title_operation', saved_then_rejected)
        db, task_id, task, calls, _ = e2e._run(tmp_path, legacy, v2=True)
        assert task.status == 'failed' and calls == ['titleBatch']
        checkpoint = store.task_execution_input(task_id=task_id, db_path=db)['checkpoint']
        scan_id = checkpoint['scanId']
        with sqlite3.connect(db) as conn:
            before = conn.execute("SELECT item_key,status,result_json,attempt_count FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:titleBatch'", (task_id,)).fetchall()
        assert before[0][1] == 'completed' and '300487.SZ' in before[0][2]
        legacy.setattr(title_runtime, '_filter_company_hints', title_runtime_filter)
        legacy.setattr(pipeline._CheckpointedDiscoveryModel, 'run_title_operation', original)
        assert recover_scan(db_path=db, scan_id=scan_id, execution_config_id='b39-execution', execution_config_revision=1,
            confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan_id, db_path=db), now=e2e.RUN_AT) == task_id
        resumed_calls = e2e._http_transport(legacy, v2=True)
        class NoRefetch:
            def __init__(self, **kwargs):
                raise AssertionError('Recovery must use the frozen input')
        legacy.setattr(pipeline, 'TuShareMajorNewsAdapter', NoRefetch)
        done = run_once(db_path=db, task_id=task_id, worker_id='b60-recovery', lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet'), clock=lambda:e2e.RUN_AT)
        assert done.status == 'completed'
        assert 'titleBatch' not in resumed_calls
        with sqlite3.connect(db) as conn:
            after = conn.execute("SELECT item_key,status,result_json,attempt_count FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:titleBatch'", (task_id,)).fetchall()
        assert after == before
        assert read_report(db_path=db)['eveningCards']


def test_mixed_research_company_hints_keep_in_pool_work(tmp_path, monkeypatch):
    def edit(value):
        for question in value.get('questions', []):
            question['companyCodes'].append('300487.SZ')
        conclusion = value.get('conclusion') or {}
        for collection, owner in [('companyMappings', conclusion), ('companyAssessments', value)]:
            rows = owner.get(collection, [])
            if rows:
                rows.append({**rows[0], 'companyCode': '300487.SZ'})
    edit_responses(monkeypatch, edit)
    db, _, task, _, gateway = e2e._run(tmp_path, monkeypatch, v2=True)
    assert task.status == 'completed'
    assert gateway.search_paths == ['path-1', 'path-2']
    assert [row['companyCode'] for row in read_report(db_path=db)['eveningCards']] == ['300002.SZ']


# Resolve after production implementation; absent on the red regression run.
title_runtime_filter = getattr(title_runtime, '_filter_company_hints', None)
