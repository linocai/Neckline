import json, sqlite3, tempfile, os
from pathlib import Path
from datetime import date, datetime, timedelta
root=Path(tempfile.mkdtemp(prefix='neckline-b54-review-more-'))
os.environ['PYTHON_DOTENV_DISABLED']='1';os.environ['DB_PATH']=str(root/'unused.sqlite')
import pytest, httpx
import tests.test_v310_pipeline_e2e as e2e
from neckline.k10 import pipeline,store
from neckline.k10.cli import enqueue_scan
from neckline.k10.worker import run_once
from neckline.k10.v2_store import read_report
from neckline.k10.windows import SHANGHAI

def intercept(mp, fn):
    mock=httpx.Client()._transport
    def respond(request):
        response=mock.handle_request(request); body=response.json()
        message=json.loads(request.content)['messages'][-1]['content']
        payload=json.loads(message.split('<untrusted-k10-evidence>\n',1)[1].split('\n</untrusted-k10-evidence>',1)[0])
        value=json.loads(body['choices'][0]['message']['content'])
        fn(payload,value)
        body['choices'][0]['message']['content']=json.dumps(value)
        return httpx.Response(200,json=body)
    transport=httpx.MockTransport(respond)
    mp.setattr(httpx,'Client',lambda **kwargs:e2e._HTTPX_CLIENT(**{**kwargs,'transport':transport}))

with pytest.MonkeyPatch.context() as mp:
    original=e2e._http_transport
    def no_paths(mp,**kwargs):
        calls=original(mp,**kwargs)
        def change(payload,value):
            if payload.get('action')=='plan_queries':value['queryPaths']=[]
        intercept(mp,change)
        return calls
    mp.setattr(e2e,'_http_transport',no_paths)
    work=root/'no_paths';work.mkdir()
    db,task_id,task,calls,gateway=e2e._run(work,mp,v2=True)
    report=read_report(db_path=db)
    print('NO_PATHS',task.status,'cards',len(report['eveningCards']),'calls',calls)
    with sqlite3.connect(db) as conn:
        print('NO_PATHS_SNAPSHOT',conn.execute('SELECT research_status,execution_status FROM k10_research_snapshot_revisions ORDER BY revision DESC LIMIT 1').fetchall())

with pytest.MonkeyPatch.context() as mp:
    work=root/'denial';work.mkdir()
    db,old_task_id,old_task,old_calls,gateway=e2e._run(work,mp,v2=True)
    old=store.list_opportunities(db_path=db)[0]
    later=datetime(2026,9,9,9,10,tzinfo=SHANGHAI)
    mp.setattr(pipeline,'_now',lambda:later)
    calls=e2e._http_transport(mp,v2=True)
    observed={}
    def change(payload,value):
        action=payload.get('action')
        if action=='assess_evidence':
            ref=payload['evidencePacket']['allowedEvidenceRefs'][0]
            value['claims']=[{'claimId':'article-claim-1','verificationStatus':'contradicted','decisionImpact':'已核实公司否认，原送样催化不成立'}]
            value['evidenceUpdates']=[{'claimId':'article-claim-1','sourceRef':ref,'relation':'contradicts','location':'paragraph:1','applicability':{}}]
        if action=='compare_companies':
            for row in value['companyAssessments']:
                row.update(role='excluded',rank=None,summary='公司已否认，核心催化被推翻')
                row['evidenceDisclosure'].update(verificationStatus='contradicted',isRumor=False,unverifiedReasons=[],conditionalAnalysis=None)
        if isinstance(payload.get('output'),dict) and 'kind' in payload['output']:
            observed[payload['companyCode']]=payload['verification']['state']
            prior=payload['previousOpportunities']
            value.update(kind='invalidated' if prior else 'background',relatedOpportunityId=prior[0]['opportunityId'] if prior else None,
                         reason='公告已核实否认原送样事项，核心理由不成立')
    intercept(mp,change)
    task_id=enqueue_scan(db_path=db,kind='morning',trading_day=date(2026,9,9),config_id='b39',config_revision=1,
        execution_config_id='b39-execution',execution_config_revision=1,now=later)
    # Morning per-opportunity review uses a separately bound provider; an isolated result is sufficient to inspect the prior durable discovery/update boundary.
    result=run_once(db_path=db,worker_id='review-denial',lease_for=timedelta(minutes=5),
        handlers={'morning_scan':lambda context:pipeline.production_scan_handler(context,tushare_token='fixture-token',parquet_dir=work/'parquet',now=lambda:later)},clock=lambda:later,task_id=task_id)
    report=read_report(db_path=db,window='morning')
    print('DENIAL',result.status,'classifier_verification',observed)
    print('DENIAL_REPORT',report)
    with sqlite3.connect(db) as conn:
        print('DENIAL_LIFECYCLE',conn.execute('SELECT kind,reason FROM k10_opportunity_lifecycle_events WHERE opportunity_id=? ORDER BY created_at',(old['opportunityId'],)).fetchall())
    print('DENIAL_OPPORTUNITY_STATE',store.list_opportunities(db_path=db)[0]['state'])
    print('DENIAL_PATH',work)
print('ROOT',root)
