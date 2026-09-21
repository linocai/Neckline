import copy
import sqlite3

from neckline.k10.research_store import list_research_assessments
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for
from tests.test_b60_pool_filtering import edit_responses


def test_real_worker_preserves_multiple_recommendations_and_shared_rank(tmp_path, monkeypatch):
    expected = {'301631.SZ':('primary',1),'301282.SZ':('primary',2),'301487.SZ':('alternative',3),
                '301360.SZ':('alternative',4),'300890.SZ':('tied',4)}
    def edit(value):
        if value.get('action') == 'research_round' and value['conclusion'].get('companyMappings'):
            row=value['conclusion']['companyMappings'][0]
            value['conclusion']['companyMappings']=[{**copy.deepcopy(row),'companyCode':code} for code in expected]
        if value.get('action') == 'research_round':
            row=value['companyAssessments'][0]
            value['companyAssessments']=[{**copy.deepcopy(row),'companyCode':code,'role':role,'rank':rank} for code,(role,rank) in expected.items()]
    edit_responses(monkeypatch,edit)
    db,task_id,first,calls,_=e2e._run(tmp_path,monkeypatch,v2=True,pending_ranking='legacy_wrong')
    assert first.status=='completed' and calls.count('research:research_round')==1
    with sqlite3.connect(db) as conn:
        before=conn.execute("SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:investigation_research_round'",(task_id,)).fetchall()
    assert len(before)==1 and before[0][5]=='completed'
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:investigation_research_round'",(task_id,)).fetchall()==before
    rows=list_research_assessments(db_path=db,task_id=task_id)
    assert {r['companyCode']:(r['role'],r['rank']) for r in rows}==expected
    report=read_report(db_path=db)
    assert len(report['eveningCards'])==5 and not report['incompleteReviews']
    actual=client_for(db).get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']
    assert {c['companyCode'] for c in actual['eveningCards']}==set(expected)
