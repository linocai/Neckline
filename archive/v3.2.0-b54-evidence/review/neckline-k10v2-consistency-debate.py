import os,json,tempfile
from pathlib import Path
from datetime import timedelta
root=Path(tempfile.mkdtemp(prefix='neckline-k10v2-debate-consistency-'))
os.environ['PYTHON_DOTENV_DISABLED']='1';os.environ['DB_PATH']=str(root/'unused.sqlite')
import pytest
import tests.test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for
from tests.test_k10_api import _freeze_k10_clocks
from tests.test_k10_end_to_end import FakeProvider,_result
from neckline.k10 import pipeline,runtime
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
pro='正方：消息描述项目送样。\n公司关联属于待核线索。\n直接对象仍需公告确认。\n两日内关注公开澄清。\n核心未知：客户身份尚未披露。'
con='反方：已完整阅读正方。\n逐项检查现有公司关联。\n区分已披露与推断。\n价格上涨不是事实确认。\n双方共同事实：只有送样说法，没有量产订单。\n主要分歧：送样是否足以证明短期受益。\n共同未知：客户身份与具体收入占比。'
with pytest.MonkeyPatch.context() as mp:
    db,_,task,_,_=e2e._run(root,mp,v2=True);assert task.status=='completed'
    _freeze_k10_clocks(mp,e2e.RUN_AT.isoformat())
    with client_for(db) as client:
        card=client.get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']['eveningCards'][0]
        response=client.post('/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection',json={'action':'keep','idempotencyKey':'consistency-debate'})
        assert response.status_code==200,response.text
        job=response.json()['analysisJobId']
        provider=FakeProvider([_result(pro),_result(con)])
        mp.setattr(runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'fixture',None))
        done=run_once(db_path=db,worker_id='consistency-debate',lease_for=timedelta(minutes=5),handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=root/'parquet'),clock=lambda:e2e.RUN_AT,task_id=job)
        assert done.status=='completed' and provider.results==[],done
        result=client.get('/api/v1/k10/company-windows/'+card['companyWindowId']+'/analysis-chain')
        assert result.status_code==200,result.text
        dto=result.json();analyses=dto['items'][0]['analyses'];assert len(analyses)==2
        (root/'analysis-dto.json').write_text(json.dumps(dto,ensure_ascii=False,indent=2))
        print('API_ANALYSES',json.dumps([{'role':a['role'],'status':a['status'],'keys':list(a),'fullText':a['fullText']} for a in analyses],ensure_ascii=False))
        print('SWIFT_PREVIEW_RULE',json.dumps({a['role']:'\n'.join(a['fullText'].splitlines()[:4]) for a in analyses},ensure_ascii=False))
        print('ROOT',root)
