from datetime import timedelta
import sqlite3
import socket
import sys
import subprocess
import pytest
from fastapi.testclient import TestClient

from neckline.k10 import pipeline, store
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
from tests.test_v310_pipeline_e2e import _run, RUN_AT
from tests.k10_v320_fixture import build_fixture, create_app


def test_v2_actual_cli_worker_fixed_pool_and_unverified_publication(tmp_path,monkeypatch):
    db,task_id,task,calls,gateway=_run(tmp_path,monkeypatch,v2=True)
    assert task.status=='completed',store.task_execution_input(task_id=task_id,db_path=db)
    report=read_report(db_path=db)
    assert [card['companyCode'] for card in report['eveningCards']]==['300002.SZ']
    card=report['eveningCards'][0]
    assert card['currentSelectionState']=='unhandled'
    assert card['catalysts'][0]['verificationStatus']=='unverified'
    assert calls.count('understand')==1 and calls.count('research:compare_companies')==1
    assert len(gateway.search_paths)==2
    assert 'classify' in calls
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM k10_v2_title_company_hints WHERE company_codes_json LIKE ?', ('%300002.SZ%',)).fetchone()[0] == 1
        usage = conn.execute('SELECT input_characters,profile_characters,profile_count FROM k10_v2_stage_input_usage').fetchall()
        assert usage and all(row[0] > 0 for row in usage) and any(row[2] > 0 for row in usage)



@pytest.mark.parametrize('status',[402,429])
def test_provider_failure_through_cli_worker_has_no_downstream_calls(tmp_path,monkeypatch,status):
    db,task_id,task,calls,gateway=_run(tmp_path,monkeypatch,v2=True,provider_status=status)
    assert calls==['http_'+str(status)]
    assert not gateway.search_paths
    report=read_report(db_path=db)
    assert report["eveningCards"]==[] and report["availableAt"] is None
    assert report["status"] == ("failed" if status==402 else "retry_pending")
    if status==402:
        assert task.status=='failed'
        with sqlite3.connect(db) as conn:
            assert conn.execute('SELECT count(*) FROM k10_task_retry_schedules WHERE task_id=?',(task_id,)).fetchone()[0]==0
    else:
        assert task.status=='queued'
        second=run_once(db_path=db,worker_id='retry',lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:RUN_AT)
        assert second.status=='completed',store.task_execution_input(task_id=task_id,db_path=db)
        assert calls.count('http_429')==1 and calls.count('titleBatch')==1
        assert len(read_report(db_path=db)['eveningCards'])==1


def test_mixed_card_action_never_changes_old_frozen_group_and_api_is_read_only(tmp_path):
    db=tmp_path/'mixed.sqlite';build_fixture(db)
    evening=read_report(db_path=db)
    morning=read_report(db_path=db,window='morning')
    old=next(card for card in evening['eveningCards'] if card['companyCode']=='300002.SZ')
    new=morning['updatedCards'][0]
    assert old['currentSelectionState']=='kept' and new['currentSelectionState']=='unhandled'
    assert new['sampleClass']=='overlap' and new['companyWindowId']!=old['companyWindowId']
    assert len(new['catalysts'])==2
    frozen=store.get_company_window_selection(company_window_id=old['companyWindowId'],db_path=db)
    store.append_company_window_action(action_id='skip-new',company_window_id=new['companyWindowId'],action='skip',idempotency_key='skip-new',
        reason=None,created_at='2026-09-09T09:20:00+08:00',db_path=db)
    assert store.get_company_window_selection(company_window_id=old['companyWindowId'],db_path=db)==frozen
    assert read_report(db_path=db,window='morning')['updatedCards'][0]['currentSelectionState']=='skipped'
    before=db.read_bytes()
    with TestClient(create_app(db)) as client:
        history = client.get('/api/v1/k10/v2/reports?limit=1').json()
        assert len(history['items']) == 1 and history['page']['nextCursor']
        page2 = client.get('/api/v1/k10/v2/reports', params={'cursor':history['page']['nextCursor'], 'limit':1}).json()
        assert page2['items'][0]['reportId'] != history['items'][0]['reportId']
        for route in ('health','settings','settings/providers','usage/summary'):
            assert client.get('/api/v1/'+route).status_code == 200
        config=client.get('/api/v1/k10/configuration').json()
        assert all(scope['state']=='configured' for scope in config['scopes'])
        assert config['profileReviewStatus']=='local_draft_awaiting_user'
        windows=client.get('/api/v1/k10/company-windows').json()['items']
        assert all(row['companyName'] for row in windows if row.get('strategyVersion')=='K10-v2')
        assert client.get('/api/v1/k10/v2/reports/latest?window=morning').json()['report']['updatedCards'][0]['companyWindowId']==new['companyWindowId']
        a=client.get('/api/v1/k10/results?strategy_version=K10-v2').json()
        b=client.get('/api/v1/k10/results?strategy_version=K10-v1.4').json()
        assert a['records'] and b['records']
        assert not ({row['companyWindowId'] for row in a['records']} & {row['companyWindowId'] for row in b['records']})
    assert db.read_bytes()==before


def test_python_subprocess_inherits_external_network_denial():
    result=subprocess.run([sys.executable,'-c',"import socket;socket.create_connection(('1.1.1.1',443))"],capture_output=True,text=True)
    assert result.returncode != 0 and 'Offline tests deny external' in result.stderr


def test_pool_outside_question_is_discarded_before_any_search(tmp_path, monkeypatch):
    db,task_id,task,calls,gateway = _run(tmp_path,monkeypatch,v2=True,outside_pool=True)
    assert task.status == 'completed'
    assert 'research:plan_gaps' in calls
    assert 'research:plan_queries' not in calls and not gateway.search_paths
    assert all(row['companyCode'] != '600000.SH' for row in read_report(db_path=db)['eveningCards'])


def test_publication_and_daily_cards_rollback_together_on_interruption(tmp_path, monkeypatch):
    import tests.k10_v320_fixture as fixture
    real = fixture.publish_cards
    def interrupted(conn, **kwargs):
        real(conn, **kwargs)
        raise RuntimeError('synthetic interruption after card writes')
    monkeypatch.setattr(fixture, 'publish_cards', interrupted)
    db = tmp_path/'atomic.sqlite'
    with pytest.raises(RuntimeError, match='synthetic interruption'):
        fixture.build_fixture(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM k10_v2_report_runs').fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM k10_publication_batches WHERE scan_id LIKE 'v2-scan-%'").fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM k10_publication_batches').fetchone()[0] > 0


def test_native_network_subprocess_cannot_bypass_guard():
    with pytest.raises(RuntimeError, match='Offline tests'):
        subprocess.run(['curl', 'https://api.deepseek.com'])
    with pytest.raises(RuntimeError, match='Offline tests'):
        subprocess.run('curl https://api.deepseek.com', shell=True)


@pytest.mark.parametrize('status', [402, 429])
def test_later_provider_failure_preserves_successful_work_without_search(tmp_path, monkeypatch, status):
    db,task_id,task,calls,gateway = _run(tmp_path,monkeypatch,v2=True,provider_status=status,failure_action='plan_queries')
    assert 'research:plan_gaps' in calls and not gateway.search_paths
    assert task.status == ('failed' if status == 402 else 'queued')
    if status == 429:
        resumed = run_once(db_path=db,worker_id='late-retry',lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:RUN_AT)
        assert resumed.status == 'completed'
        assert calls.count('titleBatch') == calls.count('understand') == calls.count('research:plan_gaps') == 1
        assert calls.count('http_429') == 1 and len(read_report(db_path=db)['eveningCards']) == 1
