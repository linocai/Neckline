"""Reviewer CLI/worker counterexamples for shared current lifecycle projection."""
import json
from tests.test_b54_review_regressions import later_scan,client_for
from tests.test_k10_end_to_end import FakeProvider,_result
from tests.test_k10_api import _freeze_k10_clocks
from tests import test_v310_pipeline_e2e as e2e
from neckline.k10 import store,morning_runtime
from neckline.k10.providers import ProviderResolution


def test_active_continuation_stays_selectable_in_history(tmp_path,monkeypatch):
    provider=FakeProvider([_result(json.dumps({'material':False,'reasonStatus':'current','observationStatus':'current','summary':'完整复核无变化','materialContraryEvidence':[]}))])
    monkeypatch.setattr(morning_runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'deepseek',None))
    db,old,task,_=later_scan(tmp_path,monkeypatch,lambda payload,value:None)
    _freeze_k10_clocks(monkeypatch,'2026-09-09T09:12:00+08:00')
    assert task.status=='completed'
    with client_for(db) as client:
        route='/api/v1/k10/company-windows/'+old['companyWindowId']
        window=client.get(route).json()
        card=client.get('/api/v1/k10/v2/reports/latest?window=morning').json()['report']['updatedCards'][0]
        assert window['opportunities'][0]['lifecycle']=='evidence_update'
        assert card['canSelect'] is True and window['canSelect'] is True
        assert client.post(route+'/selection',json={'action':'skip','idempotencyKey':'active-skip'}).status_code==200
        assert client.get(route).json()['canSelect'] is True
        assert client.post(route+'/selection',json={'action':'restore','idempotencyKey':'active-restore'}).status_code==200


def test_risk_survives_real_evening_continuation_until_explicit_complete_review(tmp_path,monkeypatch):
    original=e2e._run
    def with_risk(path,mp,**kwargs):
        result=original(path,mp,**kwargs);db=result[0];old=store.list_opportunities(db_path=db)[0]
        store.append_opportunity_update(lifecycle_event_id='prior-risk',opportunity_id=old['opportunityId'],kind='risk',reason='待核风险',source_refs=[],content={'reasonStatus':'needs_review','sourceStatus':'complete'},occurred_at='2026-09-09T08:00:00+08:00',created_at='2026-09-09T08:00:00+08:00',db_path=db)
        return result
    monkeypatch.setattr(e2e,'_run',with_risk)
    db,old,task,_=later_scan(tmp_path,monkeypatch,lambda payload,value:None,kind='evening')
    _freeze_k10_clocks(monkeypatch,'2026-09-09T22:01:00+08:00')
    assert task.status=='completed'
    with client_for(db) as client:
        def projections():
            window=client.get('/api/v1/k10/company-windows/'+old['companyWindowId']).json()
            card=client.get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']['eveningCards'][0]
            return window,card
        window,card=projections()
        assert window['opportunities'][0]['lifecycle']=='risk'
        assert card['catalysts'][0]['lifecycleState']=='risk'
        assert window['canSelect'] and card['canSelect']
        for index,coverage in enumerate(('partial','complete')):
            store.append_opportunity_update(lifecycle_event_id='review-'+coverage,opportunity_id=old['opportunityId'],kind='evidence_update',reason='复核结果',source_refs=[],content={'reasonStatus':'current','sourceStatus':coverage},occurred_at=f'2026-09-09T22:0{index+2}:00+08:00',created_at=f'2026-09-09T22:0{index+2}:00+08:00',db_path=db)
            window,card=projections()
            assert card['catalysts'][0]['lifecycleState']==('risk' if coverage=='partial' else 'active')
            assert window['opportunities'][0]['lifecycle']==('risk' if coverage=='partial' else 'evidence_update')
            assert window['canSelect'] and card['canSelect']


def test_shared_projection_never_reopens_terminal_or_unknown_states():
    updates=[{'kind':'risk','content':{}},{'kind':'evidence_update','content':{'reasonStatus':'current','sourceStatus':'complete'}}]
    for state in ('withdrawn','expired','unknown','unexpected'):
        assert not store.selection_allowed_for_states([store.lifecycle_state(store.project_opportunity_lifecycle(state,updates))])
    for terminal in ('withdrawal','expired'):
        events=[{'kind':terminal,'content':{}},*updates]
        assert not store.selection_allowed_for_states([store.lifecycle_state(store.project_opportunity_lifecycle('active',events))])
    assert not store.selection_allowed_for_states([store.lifecycle_state('future_state')])
