import os,tempfile
from pathlib import Path
from datetime import timedelta
root=Path(tempfile.mkdtemp(prefix='neckline-b54-closed-restore-'))
os.environ['PYTHON_DOTENV_DISABLED']='1';os.environ['DB_PATH']=str(root/'unused.sqlite')
import pytest
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for
from tests.test_k10_api import _freeze_k10_clocks
from neckline.k10 import store
from neckline.k10.v2_store import read_report
with pytest.MonkeyPatch.context() as mp:
    db,_,_,_,_=e2e._run(root,mp,v2=True)
    now=e2e.RUN_AT+timedelta(minutes=1)
    _freeze_k10_clocks(mp,now.isoformat())
    card=read_report(db_path=db)['eveningCards'][0]
    opportunity=store.list_opportunities(db_path=db)[0]
    with client_for(db) as client:
        route='/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection'
        skipped=client.post(route,json={'action':'skip','idempotencyKey':'skip-before-withdrawal'})
        store.withdraw_opportunity(opportunity_id=opportunity['opportunityId'],reason='已核事实推翻唯一理由',source_refs=[],withdrawn_at=now.isoformat(),db_path=db)
        before=client.get('/api/v1/k10/v2/reports/latest').json()['report']['eveningCards'][0]
        kept=client.post(route,json={'action':'keep','idempotencyKey':'keep-closed'})
        restored=client.post(route,json={'action':'restore','idempotencyKey':'restore-closed'})
        print({'skipHTTP':skipped.status_code,'closedCanSelect':before['canSelect'],'closedSelection':before['currentSelectionState'],'keepHTTP':kept.status_code,'restoreHTTP':restored.status_code,'restoreState':restored.json()['state'],'lifecycle':store.list_opportunities(db_path=db)[0]['state']})
print('ROOT',root)
