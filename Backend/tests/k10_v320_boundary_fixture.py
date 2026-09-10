"""Actual worker failure and closed historical API responses, temporary databases only."""
import json
from dataclasses import replace
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from neckline.k10 import morning_runtime,store
from neckline.k10.providers import ProviderResolution
from tests.test_b54_review_regressions import later_scan,client_for
from tests.test_k10_end_to_end import FakeProvider,_result
from tests.k10_v320_repair_fixture import build_repair_fixture
from tests.k10_v320_fixture import create_app


def export_boundary(root: Path,output: Path):
    root.mkdir(parents=True,exist_ok=True);output.mkdir(parents=True,exist_ok=True)
    with pytest.MonkeyPatch.context() as mp:
        provider=FakeProvider([replace(_result(''),ok=False)])
        mp.setattr(morning_runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'deepseek',None))
        db,_,_,_=later_scan(root,mp,lambda payload,value:None)
        with client_for(db) as client:
            response=client.get('/api/v1/k10/v2/reports/latest?window=morning');response.raise_for_status()
            value=response.json();assert value['report']['updatedCards'] or value['report']['addedCards']
            (output/'boundary_morning_failure.json').write_text(json.dumps(value,ensure_ascii=False,indent=2))
    history=root/'history.sqlite';build_repair_fixture(history)
    old=next(row for row in store.list_opportunities(db_path=history) if row['companyCode']=='300002.SZ' and 'evening' in row['firstBatchId'])
    store.append_company_window_action(action_id='boundary-skip',company_window_id=old['companyWindowId'],action='skip',idempotency_key='boundary-skip',reason=None,created_at='2026-09-09T09:31:00+08:00',db_path=history)
    with TestClient(create_app(history)) as client:
        response=client.get('/api/v1/k10/company-windows');response.raise_for_status()
        (output/'boundary_windows.json').write_text(json.dumps(response.json(),ensure_ascii=False,indent=2))
    print(json.dumps({'failureDb':str(db),'historyDb':str(history),'output':str(output)}))

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();export_boundary(a.root,a.output)
