import os,json,sqlite3,tempfile
from pathlib import Path
from datetime import datetime,timedelta
root=Path(tempfile.mkdtemp(prefix='neckline-k10v2-price-results-'))
os.environ['PYTHON_DOTENV_DISABLED']='1';os.environ['DB_PATH']=str(root/'unused.sqlite')
import pytest
import tests.test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for
from tests.test_k10_api import _freeze_k10_clocks
from neckline.k10 import store,market_context,evaluation_runtime
from neckline.k10.evaluation_schedule import maintain_evaluations
from neckline.k10.worker import run_once
from neckline.k10.windows import SHANGHAI

def market(**kwargs):
    code=kwargs['company_code']
    return {'status':'available','asOf':kwargs['cutoff_at'],'collectedAt':'2026-09-08T21:00:01+08:00','sourceRefs':[{'url':f'market-data://daily/2026-09-08/{code}','tradeDate':'2026-09-08'}], 'recentDays':[{'tradeDate':'2026-09-08','open':10.0,'high':11.5,'low':10.0,'close':11.2,'preClose':10.0,'pctChg':12.0,'adjFactor':1.0}]}
with pytest.MonkeyPatch.context() as mp:
    mp.setattr(market_context,'collect_market_context',market)
    db,task_id,task,calls,gateway=e2e._run(root,mp,v2=True)
    assert task.status=='completed',task
    candidates=store.list_candidates(scan_id=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['scanId'],state='offered',db_path=db)
    assert candidates and candidates[0]['comparison']['marketContext']['300002.SZ']['recentDays'][0]['pctChg']==12.0
    with client_for(db) as client:
        evening=client.get('/api/v1/k10/v2/reports/latest?window=evening').json()
        detail=client.get('/api/v1/k10/company-windows/'+evening['report']['eveningCards'][0]['companyWindowId']).json()
    card=evening['report']['eveningCards'][0]
    assert card['priceReaction'] is None
    (root/'price-dto.json').write_text(json.dumps({'evening':evening,'detail':detail,'storedMarketContext':candidates[0]['comparison']['marketContext']},ensure_ascii=False,indent=2))
    print('PRICE',json.dumps({'candidateMarketContext':candidates[0]['comparison']['marketContext'],'cardPriceReaction':card['priceReaction'],'detailComparisonKeys':list(detail['samples'][0]['comparison'])},ensure_ascii=False))
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
    (root/'results-dto.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print('RESULTS',json.dumps(results,ensure_ascii=False))
    print('ROOT',root)
