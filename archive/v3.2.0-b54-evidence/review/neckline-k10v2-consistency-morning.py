import json,os,tempfile,sqlite3
from pathlib import Path
root=Path(tempfile.mkdtemp(prefix='neckline-k10v2-morning-consistency-'))
os.environ['PYTHON_DOTENV_DISABLED']='1';os.environ['DB_PATH']=str(root/'unused.sqlite')
import pytest
from tests.test_b54_review_regressions import later_scan,client_for
from tests.test_k10_end_to_end import FakeProvider,_result
from tests.test_k10_api import _freeze_k10_clocks
from neckline.k10 import store,morning_runtime
from neckline.k10.providers import ProviderResolution
marker='本晨重要事实与论点已改变，但本轮比较后不再推荐此公司'
with pytest.MonkeyPatch.context() as mp:
    provider=FakeProvider([_result(json.dumps({'material':True,'reasonStatus':'current','observationStatus':'current','summary':marker,'materialContraryEvidence':[]}))])
    mp.setattr(morning_runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'deepseek',None))
    def change(payload,value):
        if payload.get('action')=='compare_companies':
            for row in value['companyAssessments']:
                if row['companyCode']=='300002.SZ':row.update(role='excluded',rank=None,summary='本轮不推荐旧催化')
    db,old,task,_=later_scan(root,mp,change)
    _freeze_k10_clocks(mp,'2026-09-09T09:12:00+08:00')
    with client_for(db) as client:
        current=client.get('/api/v1/k10/v2/reports/latest?window=morning').json()
        legacy=client.get('/api/v1/k10/morning-reports/latest').json()
        # Resolve actual supported route below if old route is absent.
        events=store.list_opportunity_lifecycle_events(opportunity_id=old['opportunityId'],db_path=db)
        with sqlite3.connect(db) as conn:
            jobs=conn.execute("SELECT task_id,status,checkpoint_json FROM k10_tasks WHERE kind='morning_review'").fetchall()
        print('ROOT',root)
        print('TASK',task.status)
        print('LIFECYCLE',[(x['kind'],x['reason']) for x in events])
        print('V2_REPORT',current)
        print('LEGACY_REPORT',legacy)
        print('JOBS',jobs)
        (root/'current-dto.json').write_text(json.dumps(current,ensure_ascii=False,indent=2))
