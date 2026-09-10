import copy
from datetime import timedelta
import sqlite3

from neckline.k10 import store
from neckline.k10.research_store import list_research_assessments
from neckline.k10.verification import TavilyEvidenceGateway
from neckline.k10.v2_store import read_report
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for
from tests.test_b60_pool_filtering import edit_responses
from tests.test_v310_tavily import _SearchExtract


def test_real_worker_publishes_two_distinct_tied_groups_without_changing_model_order(tmp_path, monkeypatch):
    codes = ['300080.SZ','300376.SZ','300409.SZ','301487.SZ','301658.SZ']
    def edit(value):
        if value.get('action') == 'close_research':
            mappings = value['conclusion'].get('companyMappings')
            if mappings:
                value['conclusion']['companyMappings'] = [{**copy.deepcopy(mappings[0]),'companyCode':code} for code in codes]
        if value.get('action') == 'compare_companies':
            template = value['companyAssessments'][0]
            value['companyAssessments'] = [{**copy.deepcopy(template),'companyCode':code,'role':'tied','rank':1 if i<2 else 2} for i,code in enumerate(codes)]
    edit_responses(monkeypatch, edit)
    db, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True, pending_ranking='legacy_wrong')
    assert task.status == 'completed' and calls.count('research:compare_companies') == 1
    rows = list_research_assessments(db_path=db, task_id=task_id)
    assert {r['companyCode']:r['rank'] for r in rows} == {code:1 if i<2 else 2 for i,code in enumerate(codes)}
    assert len(read_report(db_path=db)['eveningCards']) == 5
    actual = client_for(db).get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']
    assert len(actual['eveningCards']) == 5 and not actual['incompleteReviews']


def test_real_worker_reuses_completed_fulltext_when_only_explanation_changes(tmp_path, monkeypatch):
    db = tmp_path/'b39-e2e.sqlite'
    client = _SearchExtract()
    bodies, saved_rows, source, extracted_refs = [], [], [], []
    class Gateway:
        def __init__(self):
            with sqlite3.connect(db) as conn:
                ids = conn.execute('SELECT task_id FROM k10_tasks').fetchall()
            assert len(ids) == 1
            self.gateway = TavilyEvidenceGateway(db_path=db,client=client,clock=lambda:e2e.RUN_AT,
                task_id=ids[0][0],network_max_attempts=2)
        def fetch(self, **kwargs):
            value = self.gateway.fetch(**kwargs)
            source[:] = [value.documents[0]]
            return value
        def fetch_fulltext(self, **kwargs):
            result = self.gateway.fetch_fulltext(**kwargs)
            if result.documents:
                if not bodies:
                    changed_cutoff = self.gateway.fetch_fulltext(**(kwargs | {'cutoff_at':kwargs['cutoff_at'] + timedelta(hours=1)}))
                    assert changed_cutoff.coverage['reason'] == 'checkpoint_input_mismatch'
                    assert client.extract_calls == 1
                bodies.append(result.documents[0].original_text)
                extracted_refs.append({'documentId':result.documents[0].document_id,'revision':result.documents[0].revision})
                with sqlite3.connect(db) as conn:
                    saved_rows.append(conn.execute("SELECT * FROM k10_execution_item_checkpoints WHERE stage='tavily_evidence' AND status='completed' ORDER BY item_key").fetchall())
            return result
    monkeypatch.setattr(e2e, '_Gateway', Gateway)
    issued, repeated = [False], [False]
    initial=[]
    def edit(value):
        if value.get('action') == 'assess_evidence' and not issued[0]:
            issued[0]=True;doc=source[0]
            initial[:] = [{'requestId':'same-request','questionId':'q-1','sourceRef':{'documentId':doc.document_id,'revision':doc.revision},
                'reasonExcerptInsufficient':'Need the original conditions','expectedJudgmentChange':'Distinguish qualification from an order','state':'requested','admissionRef':None}]
            value['fulltextRequests']=copy.deepcopy(initial)
        elif value.get('action') == 'close_research' and not repeated[0]:
            repeated[0]=True
            value['fulltextRequests']=[{**copy.deepcopy(initial[0]),'sourceRef':extracted_refs[-1],'reasonExcerptInsufficient':'Read the qualification restrictions again','expectedJudgmentChange':'Confirm the same contract stage'}]
    edit_responses(monkeypatch, edit)
    _, task_id, task, calls, _ = e2e._run(tmp_path,monkeypatch,v2=True,pending_ranking='legacy_wrong')
    assert task.status=='completed' and client.extract_calls==1
    assert len(bodies)==2 and bodies[0]==bodies[1]
    assert saved_rows[0]==saved_rows[1]
    assert read_report(db_path=db)['eveningCards']
