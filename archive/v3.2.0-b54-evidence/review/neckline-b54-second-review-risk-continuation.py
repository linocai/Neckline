import os,tempfile
from pathlib import Path
root=Path(tempfile.mkdtemp(prefix='neckline-b54-risk-continuation-'))
os.environ['PYTHON_DOTENV_DISABLED']='1';os.environ['DB_PATH']=str(root/'unused.sqlite')
import pytest
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import later_scan,client_for
from tests.test_k10_api import _freeze_k10_clocks
from neckline.k10 import store
with pytest.MonkeyPatch.context() as mp:
    original=e2e._run
    def with_existing_risk(path,monkeypatch,**kwargs):
        result=original(path,monkeypatch,**kwargs)
        db=result[0];opportunity=store.list_opportunities(db_path=db)[0]
        store.append_opportunity_update(lifecycle_event_id='risk-before-evening',opportunity_id=opportunity['opportunityId'],kind='risk',reason='原风险仍待核验',source_refs=[],content={'reasonStatus':'needs_review','sourceStatus':'complete'},occurred_at='2026-09-09T08:00:00+08:00',created_at='2026-09-09T08:00:00+08:00',db_path=db)
        return result
    mp.setattr(e2e,'_run',with_existing_risk)
    db,old,task,_=later_scan(root,mp,lambda payload,value:None,kind='evening')
    _freeze_k10_clocks(mp,'2026-09-09T22:01:00+08:00')
    with client_for(db) as client:
        window=client.get('/api/v1/k10/company-windows/'+old['companyWindowId']).json()
        card=client.get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']['eveningCards'][0]
        print('RISK_CONTINUATION',{'taskStatus':task.status,'windowLifecycle':window['opportunities'][0]['lifecycle'],'dailyLifecycle':card['catalysts'][0]['lifecycleState'],'windowCanSelect':window['canSelect'],'cardCanSelect':card['canSelect']})
        assert window['opportunities'][0]['lifecycle']=='risk'
        assert card['catalysts'][0]['lifecycleState']=='active'
print('ROOT',root)
