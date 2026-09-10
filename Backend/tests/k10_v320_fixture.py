"""Frozen local v2 database and actual FastAPI DTO export; never calls a provider."""
from datetime import datetime
from pathlib import Path
from dataclasses import replace
import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
from neckline.api.k10 import create_router
from neckline.k10 import store
from neckline.k10.v2_profiles import import_profiles, UNIVERSE_ID, PROFILES_ID
from neckline.k10.v2_store import bind_strategy, publish_cards
from neckline.k10.types import OpportunityPublicationInput
from tests.k10_v304_fixture import build_fixture as build_history
from tests.k10_v302_fixture import _comparison

NOW = '2026-09-09T12:00:00+08:00'
SOURCE = Path('/Users/linotsai/Lino/whynotme')


def build_fixture(path: Path, *, morning_additions: int | None = None):
    build_history(path)
    import_profiles(universe_file=SOURCE/'research/K10-v2初始股票池_20260909.json',
        profiles_dir=SOURCE/'artifacts/output/k10-company-profiles-v2-20260909', db_path=path,
        confirmed_target=path, universe_id=UNIVERSE_ID, profiles_id=PROFILES_ID, imported_at=NOW)
    config_dir = Path(__file__).parents[1]/'neckline/config'
    strategy = json.loads((config_dir/'k10-v2.json').read_text())
    execution = json.loads((config_dir/'k10-execution-v4.json').read_text())
    store.append_run_config(config_id='v2', payload=strategy, created_at=NOW, db_path=path)
    policy=execution['discovery']['titleTriagePolicy']
    store.append_title_triage_policy(policy_id=policy['policyId'],content=policy['content'],approval_state='approved',created_at=NOW,approved_at=NOW,db_path=path)
    store.append_execution_config(config_id='v2-execution', payload=execution, created_at=NOW, db_path=path)
    bind_strategy(db_path=path,snapshot_id=strategy['strategySnapshotId'],config_id='v2',config_revision=1,
        execution_config_id='v2-execution',execution_config_revision=1,created_at=NOW)
    with sqlite3.connect(path) as conn:
        dates = {row[0] for row in conn.execute('SELECT cal_date FROM trade_cal')}
        from datetime import timedelta
        for offset in range(20):
            day = datetime(2026,9,1) + timedelta(days=offset)
            if day.strftime('%Y%m%d') not in dates:
                conn.execute("INSERT INTO trade_cal VALUES('SSE',?,?)", (day.strftime('%Y%m%d'), int(day.weekday()<5)))
    def publish(marker, kind, at, companies, old=None, mixed=False):
        scan_id='v2-scan-'+marker
        cutoff = at[:10] + ('T21:00:00+08:00' if kind=='evening' else 'T09:00:00+08:00')
        store.create_scan(scan_id=scan_id, window_kind=kind, cutoff_at=cutoff, config_id='v2', config_revision=1,
            status='completed',coverage={'status':'complete'},created_at=cutoff,completed_at=at,db_path=path)
        all_inputs=[]
        for position, code in enumerate(companies):
            event_id='v2-event-'+marker+'-'+code+('-new' if mixed and position == len(companies)-1 else '')
            key=event_id+':'+code
            classification='independent'
            related=None
            if old and code in old and not (mixed and position == len(companies)-1):
                key=old[code].opportunity_key
                related=store.list_opportunities(company_code=code,db_path=path)[0]['opportunityId']
                classification='continuation'
            refs=[{'documentId':'doc-fixture','revision':1}]
            store.append_event_revision(event_id=event_id,stable_key=event_id,headline='合成验收 · 公司业务出现新消息',event_kind='company',
                facts={'summary':'本地确定性资料，未经真实供应商核验'},source_refs=refs,supersedes_revision=None,created_at=at,db_path=path)
            comparison=_comparison(code=code,opportunity_key=key,role='primary',rank=1,refs=refs)
            comparison['summary']='公司消息与业务相关，关注接下来两个交易日；当前消息尚未官方核实。'
            comparison['differences']['evidenceDisclosure']={'verificationStatus':'unverified','isRumor':True,'originStatus':'unknown',
                'originEvidenceRef':None,'unverifiedReasons':['消息来源尚未得到官方确认'],'conditionalAnalysis':'若消息成立，可能影响相关业务；尚待公开资料验证。'}
            comparison['classification'].update(kind=classification,relatedOpportunityId=related)
            candidate_id='v2-candidate-'+marker+'-'+code+('-new' if mixed and position == len(companies)-1 else '')
            store.create_candidate(candidate_id=candidate_id,scan_id=scan_id,event_id=event_id,event_revision=1,company_code=code,
                comparison=comparison,evidence=refs,created_at=at,db_path=path)
            all_inputs.append(OpportunityPublicationInput(candidate_id=candidate_id,company_code=code,event_id=event_id,event_revision=1,
                opportunity_key=key,catalyst_stage='announcement',category='primary',comparison=comparison,evidence_refs=tuple(refs),
                source_marker=kind,related_opportunity_id=related,display_rank=len(all_inputs)+1))
        store.publish_opportunities(batch_id='v2-batch-'+marker,scan_id=scan_id,publication_kind=kind,
            inputs=[item for item in all_inputs if item.comparison['classification']['kind']!='continuation'],db_path=path,
            clock=lambda:datetime.fromisoformat(at),publication_hook=lambda conn,available:publish_cards(conn,
                report_id='v2-report-'+marker,scan_id=scan_id,kind=kind,snapshot_id=strategy['strategySnapshotId'],inputs=all_inputs,available_at=available))
        return {item.company_code:item for item in all_inputs}
    evening=publish('evening','evening','2026-09-08T21:05:00+08:00',['300002.SZ','300004.SZ'])
    old_window=next(item for item in store.list_company_windows(db_path=path) if item['companyCode']=='300002.SZ')
    store.observe_company_window(action_id='v2-old-keep',observation_id='v2-old-observation',task_id='v2-old-analysis',outbox_id='v2-old-outbox',
        company_window_id=old_window['companyWindowId'],idempotency_key='v2-old-keep',task_input_version='v2-fixture',
        task_input_cutoff_at='2026-09-08T21:05:00+08:00',task_payload={},task_budget={},created_at='2026-09-08T22:00:00+08:00',db_path=path)
    store.freeze_company_window_selection(company_window_id=old_window['companyWindowId'],frozen_at='2026-09-09T09:30:00+08:00',db_path=path)
    morning_companies = ['300002.SZ', '300005.SZ', '300002.SZ']
    if morning_additions is not None:
        with sqlite3.connect(path) as conn:
            codes = [row[0] for row in conn.execute("SELECT company_code FROM k10_v2_universe_members WHERE snapshot_id=? AND company_code NOT IN ('300002.SZ','300004.SZ') ORDER BY company_code LIMIT ?", (UNIVERSE_ID, morning_additions))]
        morning_companies = ['300002.SZ'] + codes
    morning=publish('morning','morning','2026-09-09T09:15:00+08:00',morning_companies,evening,mixed=True)
    return {'evening':evening,'morning':morning}


def create_app(path: Path, *, config_id="v2", execution_config_id="v2-execution"):
    app=FastAPI()
    import neckline.api.app as production_api
    from types import FunctionType
    for route in production_api.app.routes:
        if getattr(route, 'path', None) in {'/api/v1/settings', '/api/v1/settings/providers'} and 'GET' in getattr(route, 'methods', set()):
            endpoint = FunctionType(route.endpoint.__code__, {**route.endpoint.__globals__, '_db': lambda:path}, route.endpoint.__name__)
            app.add_api_route(route.path, endpoint, methods=['GET'], response_model=route.response_model)
    from neckline.api.app import health
    app.add_api_route('/api/v1/health',health,methods=['GET'])
    from neckline.llm.usage import summary
    @app.get('/api/v1/usage/summary')
    def usage_summary(days: int = 5):
        return summary(days=days,db_path=path)
    app.include_router(create_router(lambda:path,lambda:None,lambda:path.parent/'parquet',
        current_config_binding_provider=lambda:(config_id,1,None),
        current_execution_config_binding_provider=lambda:(execution_config_id,1,None)))
    return app


def export(path: Path, output: Path):
    if not path.exists():
        build_fixture(path)
    output.mkdir(parents=True,exist_ok=True)
    with TestClient(create_app(path)) as client:
        routes={'evening':'/v2/reports/latest?window=evening','morning':'/v2/reports/latest?window=morning',
                'configuration':'/configuration','results':'/results?strategy_version=K10-v2','windows':'/company-windows',
                'historical_results':'/results?strategy_version=K10-v1.4'}
        opportunity=next(item for item in store.list_opportunities(db_path=path) if item['companyCode']=='300002.SZ')
        routes['detail']='/opportunities/'+opportunity['opportunityId']
        for name, route in routes.items():
            response=client.get('/api/v1/k10'+route)
            response.raise_for_status()
            (output/(name+'.json')).write_text(json.dumps(response.json(),ensure_ascii=False,indent=2))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    export(args.db,args.output)
