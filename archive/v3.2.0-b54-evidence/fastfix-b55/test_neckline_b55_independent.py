import os,json,sqlite3
from pathlib import Path
from datetime import datetime,timedelta
os.environ['DB_PATH']='/tmp/neckline-b55-independent-no-working-db.sqlite'
import pytest
import tests.test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for,later_scan
from tests.test_k10_api import _freeze_k10_clocks
from tests.test_k10_end_to_end import _result
from neckline.k10 import store,runtime,pipeline,morning_runtime
from neckline.k10.worker import run_once
from neckline.k10.providers import ProviderResolution
from neckline.k10.market_context import card_price_context

SUMMARY={'commonFacts':['来源中只有送样说法'],'disagreements':['短期受益仍有分歧'],'unknowns':['客户身份未知']}
def valid(role):return _result(json.dumps({'fullText':role+'全文与完整引用','summary':SUMMARY},ensure_ascii=False))
class RecordedProvider:
    def __init__(self,values):self.values=list(values);self.calls=[]
    def chat(self,messages,**kwargs):self.calls.append((messages,kwargs));return self.values.pop(0)

@pytest.mark.parametrize('failure',['pro_summary','con_summary','con_commit'])
def test_keep_retry_keeps_frozen_pair_and_summary(tmp_path,monkeypatch,failure):
    db,_,task,_,_=e2e._run(tmp_path,monkeypatch,v2=True);assert task.status=='completed'
    _freeze_k10_clocks(monkeypatch,e2e.RUN_AT.isoformat())
    wrong=_result(json.dumps({'fullText':'有全文但没有合法摘要','summary':{'commonFacts':[],'disagreements':[]}}))
    provider=RecordedProvider([wrong] if failure=='pro_summary' else [valid('正方'),wrong if failure=='con_summary' else valid('反方')])
    monkeypatch.setattr(runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'fixture',None))
    actual=runtime.record_analysis_artifact
    if failure=='con_commit':
        def interrupted(**kwargs):
            actual(**kwargs)
            if kwargs['artifact'].role=='con':raise RuntimeError('independent interruption after durable con write')
        monkeypatch.setattr(runtime,'record_analysis_artifact',interrupted)
    handlers=pipeline.production_handlers(tushare_token='fixture',parquet_dir=tmp_path/'parquet')
    with client_for(db) as client:
        card=client.get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']['eveningCards'][0]
        route='/api/v1/k10/company-windows/'+card['companyWindowId']
        before=store.list_company_windows(db_path=db)
        response=client.post(route+'/selection',json={'action':'keep','idempotencyKey':'b55-independent-keep'});assert response.status_code==200,response.text
        job=response.json()['analysisJobId'];first=run_once(db_path=db,worker_id='b55-independent',lease_for=timedelta(minutes=5),task_id=job,handlers=handlers,clock=lambda:e2e.RUN_AT)
        assert first.status=='failed'
        artifacts=client.get(route+'/analysis-chain').json()['items'][0]['analyses']
        if failure!='con_commit':
            failed=next(a for a in artifacts if a['status']=='failed');assert failed['error'] and failed['summary'] is None
        if failure!='pro_summary':
            saved_pro=next(a for a in artifacts if a['role']=='pro');assert saved_pro['summary']==SUMMARY
        retry_values=([valid('正方恢复'),valid('反方恢复')] if failure=='pro_summary' else [valid('反方恢复')] if failure=='con_summary' else [])
        retry_provider=RecordedProvider(retry_values)
        monkeypatch.setattr(runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',retry_provider,'fixture',None))
        monkeypatch.setattr(runtime,'record_analysis_artifact',actual)
        response=client.post('/api/v1/k10/jobs/'+job+'/retry',json={'expectedAttemptCount':first.attempt_count});assert response.status_code==200,response.text
        done=run_once(db_path=db,worker_id='b55-independent',lease_for=timedelta(minutes=5),task_id=job,handlers=handlers,clock=lambda:e2e.RUN_AT)
        assert done.status=='completed';assert len(retry_provider.calls)==len(retry_values)
        pair=client.get(route+'/analysis-chain').json()['items'][0]['analyses']
        assert all(a['status']=='completed' and a['summary']==SUMMARY for a in pair)
        assert pair[0]['inputCutoffAt']==pair[1]['inputCutoffAt']
        if failure!='pro_summary':assert next(a for a in pair if a['role']=='pro')==saved_pro
        if retry_provider.calls:
            con_prompt='\n'.join(m.content for m in retry_provider.calls[-1][0])
            assert next(a['fullText'] for a in pair if a['role']=='pro') in con_prompt
        after=store.list_company_windows(db_path=db)
        assert [(x['companyWindowId'],x['d1TradeDate'],x['d2TradeDate']) for x in before]==[(x['companyWindowId'],x['d1TradeDate'],x['d2TradeDate']) for x in after]
        (tmp_path/'pair.json').write_text(json.dumps(pair,ensure_ascii=False,indent=2))

@pytest.mark.parametrize('order',['before','after'])
def test_material_update_report_link_both_durable_orders(tmp_path,monkeypatch,order):
    provider=RecordedProvider([_result(json.dumps({'material':False,'reasonStatus':'current','observationStatus':'current','summary':'无额外实质变化','materialContraryEvidence':[]}))])
    monkeypatch.setattr(morning_runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'fixture',None))
    publish=store.publish_opportunities
    captured={}
    def publish_with_update(**kwargs):
        if kwargs['publication_kind']!='morning':return publish(**kwargs)
        old=store.list_opportunities(db_path=kwargs['db_path'])[0];captured['opportunity']=old
        def append():
            store.append_opportunity_update(lifecycle_event_id='b55-independent-material',opportunity_id=old['opportunityId'],kind='evidence_update',reason='资料中出现实质论点变化',source_refs=[],content={'material':True,'scanId':kwargs['scan_id']},occurred_at='2026-09-09T09:10:00+08:00',created_at='2026-09-09T09:10:00+08:00',db_path=kwargs['db_path'])
        if order=='before':append()
        result=publish(**kwargs)
        if order=='after':append()
        return result
    monkeypatch.setattr(store,'publish_opportunities',publish_with_update)
    def exclude(payload,value):
        if payload.get('action')=='compare_companies':
            for row in value['companyAssessments']:
                if row['companyCode']=='300002.SZ':row.update(role='excluded',rank=None)
    db,old,task,_=later_scan(tmp_path,monkeypatch,exclude)
    assert task.status=='completed'
    _freeze_k10_clocks(monkeypatch,'2026-09-09T09:12:00+08:00')
    with client_for(db) as client:
        dto=client.get('/api/v1/k10/v2/reports/latest?window=morning').json()['report']
        assert dto['updatedCards']==dto['addedCards']==[]
        matches=[x for x in dto['lifecycleUpdates'] if x['updateId']=='b55-independent-material']
        assert len(matches)==1 and matches[0]['kind']=='evidence_update'
        assert len(store.list_company_windows(db_path=db))==1


def test_price_cutoff_timezone_invalid_and_missing_values():
    def value(asof,days):return {'status':'available','asOf':asof,'sourceRefs':[{'url':'fixture://price/300002.SZ'}],'recentDays':days}
    d={'tradeDate':'2026-09-08','pctChg':float('nan'),'close':11.2,'preClose':10.0}
    msg,ctx=card_price_context([value('2026-09-09T01:00:00Z',[d,{'tradeDate':'2026-09-09','pctChg':99}])],company_code='300002.SZ',cutoff_at='2026-09-09T09:00:00+08:00')
    assert '+12.00%' in msg and ctx['tradeDate']=='2026-09-08'
    msg,ctx=card_price_context([value('2026-09-09T01:00:00Z',[{'tradeDate':'2026-09-08','pctChg':float('inf'),'close':None,'preClose':0}])],company_code='300002.SZ',cutoff_at='2026-09-09T09:00:00+08:00')
    assert ctx is None and '暂无' in msg
