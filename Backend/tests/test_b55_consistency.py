"""Preserve the four full-spec reviewer counterexamples through real entries."""
import json, sqlite3
from datetime import datetime,timedelta
import pytest
import tests.test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import later_scan,client_for
from tests.test_k10_end_to_end import FakeProvider,_result
from tests.debate_fixture import debate_text
from tests.test_k10_api import _freeze_k10_clocks
from neckline.k10 import store,morning_runtime,market_context,evaluation_runtime,pipeline,runtime
from neckline.k10.providers import ProviderResolution
from neckline.k10.evaluation_schedule import maintain_evaluations
from neckline.k10.worker import run_once
from neckline.k10.windows import SHANGHAI


def test_material_morning_without_recommendation(tmp_path,monkeypatch):
    mp=monkeypatch
    marker="本晨重要事实与论点已改变，但本轮比较后不再推荐此公司"
    provider=FakeProvider([_result(json.dumps({'material':True,'reasonStatus':'current','observationStatus':'current','summary':marker,'materialContraryEvidence':[]}))])
    mp.setattr(morning_runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'deepseek',None))
    def change(payload,value):
        if payload.get('action')=='compare_companies':
            for row in value['companyAssessments']:
                if row['companyCode']=='300002.SZ':row.update(role='excluded',rank=None,summary='本轮不推荐旧催化')
    db,old,task,_=later_scan(tmp_path,mp,change)
    _freeze_k10_clocks(mp,'2026-09-09T09:12:00+08:00')
    with client_for(db) as client:
        current=client.get('/api/v1/k10/v2/reports/latest?window=morning').json()
        (tmp_path/'b55_morning.json').write_text(json.dumps(current,ensure_ascii=False))
        assert current['report']['updatedCards']==current['report']['addedCards']==[]
        update=next(x for x in current['report']['lifecycleUpdates'] if x['opportunityId']==old['opportunityId'])
        assert update['kind']=='evidence_update' and update['reason']==marker
        assert update['sourceRefs'] and update['createdAt']
        assert len(store.list_company_windows(db_path=db))==1


def market(**kwargs):
    code=kwargs['company_code']
    return {'status':'available','asOf':kwargs['cutoff_at'],'collectedAt':'2026-09-08T21:00:01+08:00','sourceRefs':[{'url':f'market-data://daily/2026-09-08/{code}','tradeDate':'2026-09-08'}], 'recentDays':[{'tradeDate':'2026-09-08','open':10.0,'high':11.5,'low':10.0,'close':11.2,'preClose':10.0,'pctChg':12.0,'adjFactor':1.0}]}


def test_frozen_price_and_consecutive_result(tmp_path,monkeypatch):
    mp=monkeypatch
    mp.setattr(market_context,'collect_market_context',market)
    db,task_id,task,calls,gateway=e2e._run(tmp_path,mp,v2=True)
    assert task.status=='completed',task
    candidates=store.list_candidates(scan_id=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['scanId'],state='offered',db_path=db)
    assert candidates and candidates[0]['comparison']['marketContext']['300002.SZ']['recentDays'][0]['pctChg']==12.0
    with client_for(db) as client:
        evening=client.get('/api/v1/k10/v2/reports/latest?window=evening').json()
        detail=client.get('/api/v1/k10/company-windows/'+evening['report']['eveningCards'][0]['companyWindowId']).json()
    (tmp_path/'b55_evening.json').write_text(json.dumps(evening,ensure_ascii=False))
    card=evening['report']['eveningCards'][0]
    assert '+12.00%' in card['priceReaction'] and '2026-09-08' in card['priceReaction']
    assert card['priceContext']['asOf']==candidates[0]['comparison']['marketContext'][card['companyCode']]['asOf']
    assert card['priceContext']['sourceRefs']
    code=card['companyCode'];now=datetime(2026,9,10,15,5,tzinfo=SHANGHAI)
    def fact(**kwargs):
        day=kwargs['trade_date'];is_d1=day=='2026-09-09';prior=10.0 if is_d1 else 12.0;limit=12.0 if is_d1 else 14.4
        return {'companyCode':kwargs['company_code'],'tradeDate':day,'availability':'available','openPrice':prior,'highPrice':limit,'lowPrice':prior,'closePrice':limit,'preClose':prior,'limitUpPrice':limit,'adjFactor':1.0,'closeLimitUp':True,'touchedLimitUp':True,'sourceRefs':[{'url':f'fixture://daily/{day}/{code}'}],'obtainedAt':now.isoformat()}
    handlers={'collect_market_day_fact':lambda context:evaluation_runtime.market_day_fact_handler(context,clock=lambda:now,market_fact_fetcher=fact),'evaluate_company_window':lambda context:evaluation_runtime.evaluation_handler(context,clock=lambda:now)}
    maintain_evaluations(db_path=db,now=now)
    def queued(kind):
        with sqlite3.connect(db) as conn:return [row[0] for row in conn.execute('SELECT task_id FROM k10_tasks WHERE status=\'queued\' AND kind=? ORDER BY created_at,task_id',(kind,))]
    for job in queued('collect_market_day_fact'):
        result=run_once(db_path=db,worker_id='price-result-review',lease_for=timedelta(minutes=5),task_id=job,handlers=handlers,clock=lambda:now)
        assert result.status=='completed',result
    maintain_evaluations(db_path=db,now=now)
    for job in queued('evaluate_company_window'):
        result=run_once(db_path=db,worker_id='price-result-review',lease_for=timedelta(minutes=5),task_id=job,handlers=handlers,clock=lambda:now)
        assert result.status=='completed',result
    _freeze_k10_clocks(mp,now.isoformat())
    with client_for(db) as client:
        response=client.get('/api/v1/k10/results?strategy_version=K10-v2');assert response.status_code==200,response.text
        results=response.json()
    (tmp_path/'b55_results.json').write_text(json.dumps(results,ensure_ascii=False))
    assert results['records'][0]['consecutiveLimitUp'] is True
    assert results['primary']['all']['hitCount']==1 and results['primary']['all']['eligibleCount']==1
    assert results['primary']['all']['consecutiveLimitUpCount']==1


pro='正方：消息描述项目送样。\n公司关联属于待核线索。\n直接对象仍需公告确认。\n两日内关注公开澄清。\n核心未知：客户身份尚未披露。'
con='反方：已完整阅读正方。\n逐项检查现有公司关联。\n区分已披露与推断。\n价格上涨不是事实确认。\n双方共同事实：只有送样说法，没有量产订单。\n主要分歧：送样是否足以证明短期受益。\n共同未知：客户身份与具体收入占比。'


def test_structured_debate_survives_real_worker_and_api(tmp_path,monkeypatch):
    mp=monkeypatch
    db,_,task,_,_=e2e._run(tmp_path,mp,v2=True);assert task.status=='completed'
    _freeze_k10_clocks(mp,e2e.RUN_AT.isoformat())
    with client_for(db) as client:
        card=client.get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']['eveningCards'][0]
        response=client.post('/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection',json={'action':'keep','idempotencyKey':'consistency-debate'})
        assert response.status_code==200,response.text
        job=response.json()['analysisJobId']
        provider=FakeProvider([_result(debate_text(pro)),_result(debate_text(con))])
        mp.setattr(runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'fixture',None))
        done=run_once(db_path=db,worker_id='consistency-debate',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:e2e.RUN_AT,task_id=job)
        assert done.status=='completed' and provider.results==[],done
        result=client.get('/api/v1/k10/company-windows/'+card['companyWindowId']+'/analysis-chain')
        assert result.status_code==200,result.text
        dto=result.json();analyses=dto['items'][0]['analyses'];assert len(analyses)==2
        (tmp_path/'b55_analysis.json').write_text(json.dumps(dto,ensure_ascii=False))
        assert all(a['summary']['commonFacts'] and a['summary']['disagreements'] and a['summary']['unknowns'] for a in analyses)
        assert analyses[1]['fullText']==con
        assert analyses[0]['inputCutoffAt']==analyses[1]['inputCutoffAt']


@pytest.mark.parametrize('kind,material,expected', [
    ('evidence_update',True,True),('evidence_update',False,False),
    ('risk',False,True),('withdrawal',False,True),('expired',False,True)])
def test_report_materiality_is_explicit(kind,material,expected):
    assert store.reportable_lifecycle_update(kind,{'material':material}) is expected


def test_price_cutoff_missing_and_no_future_backfill():
    from neckline.k10.market_context import card_price_context
    source=market(company_code='300002.SZ',cutoff_at='2026-09-09T09:00:00+08:00')
    source['recentDays'].append({'tradeDate':'2026-09-09','pctChg':99})
    text, value=card_price_context([source],company_code='300002.SZ',cutoff_at=source['asOf'])
    assert value['tradeDate']=='2026-09-08' and '+12.00%' in text and '99' not in text
    source['asOf']='2026-09-09T15:00:00+08:00'
    text,value=card_price_context([source],company_code='300002.SZ',cutoff_at='2026-09-09T09:00:00+08:00')
    assert value is None and '暂无' in text
    assert card_price_context([None],company_code='300002.SZ',cutoff_at=source['asOf'])[1] is None


@pytest.mark.parametrize('d1,d2,state,sample,expected,count', [
    (True,True,'completed','primary',True,1),
    (True,False,'completed','primary',False,0),
    (False,True,'completed','primary',False,0),
    (True,None,'incomplete','primary',None,0),
    (True,True,'pending','primary',True,0),
    (True,True,'completed','overlap',True,1)])
def test_consecutive_metric_eligibility_and_unknown(d1,d2,state,sample,expected,count):
    from neckline.api.k10 import _metrics,_consecutive_limit
    from neckline.api.k10_schemas import CompanyWindowEvaluationOut,MarketDayOut
    def day(value):
        return MarketDayOut(tradeDate='2026-09-10',availability='data_gap' if value is None else 'available',closeLimitUp=value,touchedLimitUp=value)
    one,two=day(d1),day(d2)
    assert _consecutive_limit(one.model_dump(),two.model_dump()) is expected
    record=CompanyWindowEvaluationOut(companyWindowId='one',companyCode='300002.SZ',sampleClass=sample,state=state,revision=1,updatedAt='2026-09-10T15:05:00+08:00',d1=one,d2=two,primaryEligible=sample=='primary',closeLimitHitAny=True)
    metrics=_metrics([record],sample_class=sample)
    assert metrics.consecutiveLimitUpCount==count
    assert metrics.hitCount<=1
    if sample=='overlap':assert _metrics([record]).hitCount==_metrics([record]).consecutiveLimitUpCount==0


@pytest.mark.parametrize('raw', ['plain unstructured text', '{}', '[]',
    '{"fullText":"全文","summary":{"commonFacts":[],"disagreements":[],"unknowns":"待核"}}'])
def test_new_debate_missing_summary_is_explicit_failure(raw):
    from neckline.k10.analysis import run_pro
    from tests.test_k10_analysis import _context
    provider=FakeProvider([_result(raw)])
    value=run_pro(context=_context(),cutoff_at='2026-09-06T21:00:00+08:00',provider=provider)
    assert value.status=='failed' and value.error and value.summary is None
    assert value.full_text==raw and provider.results==[]


def test_summary_empty_categories_and_checkpoint_roundtrip():
    from neckline.k10.analysis import run_pro
    from neckline.k10.runtime import _artifact
    from tests.test_k10_analysis import _context
    summary={'commonFacts':[],'disagreements':[],'unknowns':['资料尚待核实']}
    value=run_pro(context=_context(),cutoff_at='2026-09-06T21:00:00+08:00',provider=FakeProvider([_result(debate_text('没有足够资料，不编造反证',summary))]))
    assert value.status=='completed' and _artifact(value.to_dict()).summary==summary
    historical=value.to_dict();historical.pop('summary');historical['promptVersion']='k10-debate-v1'
    assert _artifact(historical).summary is None
