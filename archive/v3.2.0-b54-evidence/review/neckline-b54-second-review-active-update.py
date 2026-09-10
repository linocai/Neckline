import json,os,tempfile
from pathlib import Path
from datetime import datetime
root=Path(tempfile.mkdtemp(prefix='neckline-b54-active-update-'))
os.environ['PYTHON_DOTENV_DISABLED']='1';os.environ['DB_PATH']=str(root/'unused.sqlite')
import pytest
from tests.test_b54_review_regressions import later_scan,client_for
from tests.test_k10_end_to_end import FakeProvider,_result
from tests.test_k10_api import _freeze_k10_clocks
from neckline.k10 import store,morning_runtime
from neckline.k10.providers import ProviderResolution
with pytest.MonkeyPatch.context() as mp:
    provider=FakeProvider([_result(json.dumps({'material':False,'reasonStatus':'current','observationStatus':'current','summary':'完整复核无变化，原催化继续有效','materialContraryEvidence':[]}))])
    mp.setattr(morning_runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'deepseek',None))
    db,old,task,_=later_scan(root,mp,lambda payload,value:None)
    _freeze_k10_clocks(mp,'2026-09-09T09:12:00+08:00')
    with client_for(db) as client:
        window=client.get('/api/v1/k10/company-windows/'+old['companyWindowId']).json()
        card=next(x for x in client.get('/api/v1/k10/v2/reports/latest?window=morning').json()['report']['updatedCards'] if x['companyWindowId']==old['companyWindowId'])
        route='/api/v1/k10/company-windows/'+old['companyWindowId']+'/selection'
        skipped=client.post(route,json={'action':'skip','idempotencyKey':'skip-still-active'})
        skipped_window=client.get('/api/v1/k10/company-windows/'+old['companyWindowId']).json()
        restored=client.post(route,json={'action':'restore','idempotencyKey':'restore-still-active'})
        print('ACTIVE_UPDATE',{'scanTask':task.status,'opportunityState':store.list_opportunities(db_path=db)[0]['state'],'windowLifecycles':[x['lifecycle'] for x in window['opportunities']],'windowCanSelect':window['canSelect'],'cardCanSelect':card['canSelect'],'skipHTTP':skipped.status_code,'skippedHistoryState':skipped_window['currentSelectionState'],'historyCanSelect':skipped_window['canSelect'],'restoreHTTP':restored.status_code,'restoreState':restored.json()['state']})
        assert card['canSelect'] is True
        assert window['canSelect'] is False
print('ROOT',root)
