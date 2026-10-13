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
    assert calls.count('understand') == calls.count('research:research_round') == 1
    assert not ({'research:plan_research', 'research:assess_and_decide', 'research:compare_companies'} & set(calls))
    # The direct round can publish from the admitted event evidence; it must
    # not buy an otherwise unnecessary verification search.
    assert not gateway.search_paths
    assert 'classify' not in calls  # Valid first v2 recommendations have a derivable initial identity.
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
    assert report["status"] == ("partial" if status==402 else "retry_pending")
    if status==402:
        assert task.status=='completed'
        assert report['resultAvailableAt'] and report['delivery']['rankingScope']=='none'
        assert report['discovery']['outcome']=='not_completed'
        assert report['delivery']['gaps']
        with sqlite3.connect(db) as conn:
            assert conn.execute('SELECT count(*) FROM k10_task_retry_schedules WHERE task_id=?',(task_id,)).fetchone()[0]==0
    else:
        assert task.status=='queued'
        second=run_once(db_path=db,worker_id='retry',lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:RUN_AT)
        assert second.status=='completed',store.task_execution_input(task_id=task_id,db_path=db)
        assert calls.count('http_429')==1 and calls.count('titleBatch')==1
        assert len(read_report(db_path=db)['eveningCards'])==1



def test_python_subprocess_inherits_external_network_denial():
    result=subprocess.run([sys.executable,'-c',"import socket;socket.create_connection(('1.1.1.1',443))"],capture_output=True,text=True)
    assert result.returncode != 0 and 'Offline tests deny external' in result.stderr


def test_pool_outside_question_is_discarded_before_any_search(tmp_path, monkeypatch):
    db,task_id,task,calls,gateway = _run(tmp_path,monkeypatch,v2=True,outside_pool=True)
    assert task.status == 'completed'
    assert 'research:research_round' in calls
    assert not ({'research:plan_research', 'research:plan_queries'} & set(calls)) and not gateway.search_paths
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
    db,task_id,task,calls,gateway = _run(tmp_path,monkeypatch,v2=True,provider_status=status,failure_action='research_round')
    # B78 has one direct research receipt. A refusal leaves the earlier
    # title/body work durable and never starts a verification search.
    assert calls[:3] == ['titleBatch', 'titleGlobal', 'understand']
    assert calls[-1] == f'http_{status}'
    assert 'research:research_round' not in calls and not gateway.search_paths
    assert task.status == ('completed' if status == 402 else 'queued')
    if status == 402:
        report = read_report(db_path=db)
        assert report['status']=='partial' and report['resultAvailableAt']
        assert report['discovery']['outcome']=='not_completed' and not report['eveningCards']
        assert report['delivery']['gaps']
    if status == 429:
        resumed = run_once(db_path=db,worker_id='late-retry',lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:RUN_AT)
        assert resumed.status == 'completed'
        assert calls.count('titleBatch') == calls.count('understand') == calls.count('research:research_round') == 1
        assert calls.count('http_429') == 1 and len(read_report(db_path=db)['eveningCards']) == 1
