import copy
import sqlite3
from datetime import timedelta

from neckline.k10 import pipeline, research_runtime, store
from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
from neckline.k10.opportunity_discovery import ComparisonValidationError
from neckline.k10.research_store import list_research_assessments
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for
from tests.test_b60_pool_filtering import edit_responses


def test_recover_paid_comparison_preserves_multiple_recommendations_and_shared_rank(tmp_path, monkeypatch):
    expected = {'301631.SZ':('primary',1),'301282.SZ':('primary',2),'301487.SZ':('alternative',3),
                '301360.SZ':('alternative',4),'300890.SZ':('tied',4)}
    def edit(value):
        if value.get('action') == 'close_research' and value['conclusion'].get('companyMappings'):
            row=value['conclusion']['companyMappings'][0]
            value['conclusion']['companyMappings']=[{**copy.deepcopy(row),'companyCode':code} for code in expected]
        if value.get('action') == 'compare_companies':
            row=value['companyAssessments'][0]
            value['companyAssessments']=[{**copy.deepcopy(row),'companyCode':code,'role':role,'rank':rank} for code,(role,rank) in expected.items()]
    edit_responses(monkeypatch,edit)
    validator=research_runtime.validate_event_comparison
    def retired_gate(**kwargs):
        raise ComparisonValidationError('Retired one-primary gate',code='compare_company_role_invalid')
    monkeypatch.setattr(research_runtime,'validate_event_comparison',retired_gate)
    db,task_id,first,calls,_=e2e._run(tmp_path,monkeypatch,v2=True,pending_ranking='legacy_wrong')
    assert first.status=='failed' and calls.count('research:compare_companies')==1
    with sqlite3.connect(db) as conn:
        before=conn.execute("SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:investigation_compare_companies'",(task_id,)).fetchall()
    assert len(before)==1 and before[0][5]=='completed'
    scan=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['scanId']
    assert recover_scan(db_path=db,scan_id=scan,execution_config_id='b39-execution',execution_config_revision=1,
        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan,db_path=db),now=e2e.RUN_AT)==task_id
    monkeypatch.setattr(research_runtime,'validate_event_comparison',validator)
    resumed=e2e._http_transport(monkeypatch,v2=True)
    task=run_once(db_path=db,task_id=task_id,worker_id='b70',lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:e2e.RUN_AT)
    assert task.status=='completed' and 'research:compare_companies' not in resumed
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:investigation_compare_companies'",(task_id,)).fetchall()==before
    rows=list_research_assessments(db_path=db,task_id=task_id)
    assert {r['companyCode']:(r['role'],r['rank']) for r in rows}==expected
    report=read_report(db_path=db)
    assert len(report['eveningCards'])==5 and not report['incompleteReviews']
    actual=client_for(db).get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']
    assert {c['companyCode'] for c in actual['eveningCards']}==set(expected)
