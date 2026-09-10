"""Real append-only lifecycle DTOs for B54 review repairs; temporary database only."""
import json
from pathlib import Path
from fastapi.testclient import TestClient
from neckline.k10 import store
from tests.k10_v320_fixture import build_fixture, create_app


def build_repair_fixture(path: Path):
    build_fixture(path)
    opportunities=store.list_opportunities(db_path=path)
    def find(code, marker):
        return next(row for row in opportunities if row['companyCode']==code and marker in row['firstBatchId'])
    old=find('300002.SZ','v2-batch-evening')
    closed=find('300004.SZ','v2-batch-evening')
    risk=find('300005.SZ','v2-batch-morning')
    store.observe_company_window(action_id='repair-old-keep',observation_id='repair-old-observation',task_id='repair-old-analysis',outbox_id='repair-old-outbox',
        company_window_id=closed['companyWindowId'],idempotency_key='repair-old-keep',task_input_version='fixture-v2',task_input_cutoff_at='2026-09-09T09:16:00+08:00',
        task_payload={'configId':'v2','configRevision':1},task_budget={},created_at='2026-09-09T09:16:00+08:00',db_path=path,
        execution_binding={'configId':'v2-execution','revision':1})
    store.freeze_company_window_selection(company_window_id=closed['companyWindowId'],frozen_at='2026-09-09T09:30:00+08:00',db_path=path)

    for suffix,opportunity,kind,reason in [('old-withdrawal',old,'withdrawal','原催化已被公司公告否认；另一新催化继续保留'),
                                          ('closed-withdrawal',closed,'withdrawal','唯一推荐理由已被公开事实推翻'),
                                          ('risk',risk,'risk','新增风险信息尚未核实，继续观察并披露不确定性')]:
        store.append_opportunity_update(lifecycle_event_id='repair-'+suffix,opportunity_id=opportunity['opportunityId'],kind=kind,reason=reason,
            source_refs=[{'documentId':'doc-fixture','revision':1}],content={'scanId':'v2-scan-morning'},
            occurred_at='2026-09-09T09:20:00+08:00',created_at='2026-09-09T09:20:00+08:00',db_path=path)
    active=find('300002.SZ','v2-batch-morning')
    for suffix,opportunity in [('risk-continuation',risk),('active-continuation',active)]:
        store.append_opportunity_update(lifecycle_event_id='repair-'+suffix,opportunity_id=opportunity['opportunityId'],kind='evidence_update',
            reason='普通后续资料，未新增已核风险解除结论',source_refs=[{'documentId':'doc-fixture','revision':1}],content={'scanId':'v2-scan-morning'},
            occurred_at='2026-09-09T09:21:00+08:00',created_at='2026-09-09T09:21:00+08:00',db_path=path)
    # Existing historical windows have actual expiry facts linked to the evening report.
    # They have no v2 recommendation card; do not create a new sample to show expiry.
    return path


def export_repair(path: Path, output: Path):
    if not path.exists():build_repair_fixture(path)
    output.mkdir(parents=True,exist_ok=True)
    with TestClient(create_app(path)) as client:
        for name,route in {'repair_evening':'/v2/reports/latest?window=evening',
                           'repair_morning':'/v2/reports/latest?window=morning',
                           'repair_windows':'/company-windows'}.items():
            response=client.get('/api/v1/k10'+route);response.raise_for_status()
            (output/(name+'.json')).write_text(json.dumps(response.json(),ensure_ascii=False,indent=2))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--db',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();export_repair(args.db,args.output)
