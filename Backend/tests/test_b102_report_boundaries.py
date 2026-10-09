"""B102: original B101 review reproductions, now asserting usable delivery."""
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import socket
import traceback
from unittest.mock import patch

import httpx
import pytest

from neckline.k10 import pipeline, store, collection_gateway
from neckline.k10.schema import SqliteWriteBusy
from tests import test_b92_report_loopback as current
from tests import test_b98_report_isolation as existing
from tests import test_b101_wire_boundaries as mixed
from tests import v340_acceptance_fixture as base

def emit(name, result):
    destination = os.environ.get("NK_B102_EVIDENCE_DIR")
    if destination:
        root = Path(destination)
        root.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(json.dumps(result, ensure_ascii=True, indent=2))

@pytest.fixture(autouse=True)
def prevent_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('independent review forbids external network')
    monkeypatch.setattr(socket, 'create_connection', deny)
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)

def observe(db, *, case, handlers, wire, expectation_error=None):
    from neckline.api import k10 as api
    class BusinessClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current.RUN_AT.astimezone(tz) if tz else current.RUN_AT.replace(tzinfo=None)
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
        tasks = conn.execute('SELECT task_id,kind,status,stage,checkpoint_json FROM k10_tasks ORDER BY created_at,task_id').fetchall()
        attempts = conn.execute('SELECT task_id,stage,state,error_code,count(*) FROM k10_external_attempts GROUP BY 1,2,3,4').fetchall()
        scan_rows = conn.execute('SELECT scan_id,status,coverage_json FROM k10_scans').fetchall()
        count = conn.execute('SELECT count(*) FROM k10_model_response_receipts').fetchone()[0]
    with patch.object(api, 'datetime', BusinessClock), patch.object(store, 'datetime', BusinessClock), base.actual_api(db, config_id='b92-isolated-run', config_revision=1,
                         execution_id='b92-isolated-execution', execution_revision=1) as client:
        report_response = client.get('/api/v1/k10/v2/reports/latest?window=evening')
        config = client.get('/api/v1/k10/configuration').json()
        report = report_response.json().get('report')
        materials = client.get(f"/api/v1/k10/v2/reports/{report['reportId']}/materials").json() if report else {}
    result = dict(case=case, taskResults=[dict(taskId=t[0],kind=t[1],status=t[2],stage=t[3]) for t in tasks],
                  externalAttempts=attempts, modelReceipts=count, handlers=handlers,
                  reportHTTP=report_response.status_code, envelope=report_response.json(), report=report, materials=materials,
                  runControl=config['runControl'], expectationError=expectation_error,
                  scanStates=[dict(scanId=s[0],status=s[1],pipelineState=json.loads(s[2]).get('pipelineState')) for s in scan_rows],
                  toolNames=[t['params']['name'] for t in wire.tool_calls])
    emit(f'{case}.json', result)
    return result

@pytest.mark.parametrize('case', ['get_news_control', 'get_news_unused_surrogate', 'get_news_body_surrogate', 'get_news_unused_nan', 'get_news_whitespace_id', 'get_news_unused_surrogate_after_receipt', 'get_news_unused_surrogate_ambiguous_commit'])
def test_source_receipt_boundary(case, tmp_path, monkeypatch):
    wire = mixed.mixed_wire('control')
    reply = wire.reply
    def respond(request, body):
        response = reply(request, body)
        value = response.json()
        tool = body['params']['name']
        if tool == 'get_news':
            data = value['result']['structuredContent']['data']
            if case.startswith('get_news_unused_surrogate'):
                data['optionalComment'] = 'unused escaped string\ud800'
            elif case == 'get_news_body_surrogate':
                data['content'] += '\ud800'
            elif case == 'get_news_unused_nan':
                data['optionalComment'] = float('nan')
        if case == 'get_news_whitespace_id' and tool in {'list_news', 'get_news'}:
            data = value['result']['structuredContent']['data']
            for row in data.get('items', [data]):
                row['id'] = '   '
        return httpx.Response(200, content=json.dumps(value, ensure_ascii=True).encode('ascii'),
                              headers={'Content-Type':'application/json'})
    wire.reply = respond
    interrupted = []
    if case.endswith('after_receipt'):
        original_settle = collection_gateway._settle_received_reply
        def settle(**kwargs):
            result = original_settle(**kwargs)
            data = kwargs['raw'].get('structuredContent', {}).get('data', {})
            if 'optionalComment' in data and not interrupted:
                interrupted.append(True)
                raise SqliteWriteBusy('B102 interruption after durable Jin10 reply')
            return result
        monkeypatch.setattr(collection_gateway, '_settle_received_reply', settle)
    if case.endswith('ambiguous_commit'):
        original_settle = store.settle_tool_attempt
        def settle(**kwargs):
            result = original_settle(**kwargs)
            if '\\ud800' in json.dumps(kwargs, ensure_ascii=True, default=str) and not interrupted:
                interrupted.append(True)
                raise SqliteWriteBusy('B102 committed Jin10 reply with interrupted acknowledgement')
            return result
        monkeypatch.setattr(store, 'settle_tool_attempt', settle)
    handlers=[]
    original = pipeline.production_scan_handler
    def handler(*args, **kwargs):
        try:
            result = original(*args, **kwargs)
        except Exception as exc:
            handlers.append(dict(exception=type(exc).__name__, code=getattr(exc,'code',None),
                frames=[dict(path=t.filename, line=t.lineno, function=t.name) for t in traceback.extract_tb(exc.__traceback__)]))
            raise
        handlers.append(dict(status=result.status,stage=result.stage))
        return result
    monkeypatch.setattr(pipeline,'production_scan_handler',handler)
    error=None
    slices=[]
    try:
        _, _, _, _, _, slices = existing.make_collected_case(tmp_path, monkeypatch, wire=wire)
    except AssertionError as exc:
        error=str(exc)
    result = observe(tmp_path/'b92-flash.sqlite', case=case, handlers=handlers,wire=wire,expectation_error=error)
    assert error is None
    assert result['reportHTTP'] == 200 and result['report']['status'] == 'partial'
    assert result['report']['eveningCards']
    assert all(card['canSelect'] for card in result['report']['eveningCards'])
    assert not any(a[2] in {'started', 'unknown'} for a in result['externalAttempts'])
    if case == 'get_news_body_surrogate':
        assert len(result['materials']['items']) == 3
        assert result['report']['delivery']['gaps']
    elif case != 'get_news_whitespace_id':
        assert len(result['report']['eveningCards']) == 3
        assert len(result['materials']['items']) == 4
    if 'surrogate' in case:
        with sqlite3.connect(tmp_path/'b92-flash.sqlite') as conn:
            checkpoints = conn.execute("SELECT checkpoint_json FROM k10_tasks WHERE kind='evening_scan'").fetchall()
        raw_replies = [receipt['result'] for (raw,) in checkpoints
                       for receipt in json.loads(raw).get('toolReceipts', {}).values()]
        assert any('\\ud800' in json.dumps(reply, ensure_ascii=True) for reply in raw_replies)
    if case.endswith(('after_receipt', 'ambiguous_commit')):
        assert interrupted == [True]
        assert result['toolNames'].count('get_news') == 1
        if case.endswith('after_receipt'):
            assert slices[0] == 'queued' and slices[-1] == 'completed'

def test_unrelated_unknown_collection_does_not_block_new_report(tmp_path,monkeypatch):
    from neckline.k10.collection_runtime import create_collection_handler
    from neckline.k10.jin10_mcp import Jin10Client
    from neckline.k10.worker import run_once
    wire = mixed.mixed_wire('control')
    historical=[]
    def before_report(db):
        old_slot = current.SLOT-timedelta(days=1)
        task_id=current._cli('enqueue-collection','--db',str(db),'--slot',old_slot.isoformat(),
                            '--config-id','b92-isolated-collection','--config-revision','1')
        bad_wire=current._news_wire()
        normal=bad_wire.reply
        def timeout(request,body):
            if body['params']['name']=='list_flash':
                raise httpx.ReadTimeout('deterministic provider timeout',request=request)
            return normal(request,body)
        bad_wire.reply=timeout
        def factory(**kwargs):
            return Jin10Client(**kwargs,transport=httpx.MockTransport(bad_wire))
        outcome=run_once(db_path=db,task_id=task_id,worker_id='review-old-unknown',
                lease_for=timedelta(minutes=5),clock=lambda:current.RUN_AT,
                handlers={'collect_news':create_collection_handler(tushare_token=None,
                        jin10_token=current.JIN10_TOKEN,client_factory=factory)},require_b76_contract=True)
        assert outcome.status=='failed'
        historical.append(task_id)
    # The reused helper asserts globally no unknown, so inspect its real
    # outcome after that assertion without changing bindings/checkpoints.
    try:
        existing.make_collected_case(tmp_path,monkeypatch,wire=wire,before_report=before_report)
    except AssertionError:
        pass
    result=observe(tmp_path/'b92-flash.sqlite',case='unrelated_unknown',handlers=[],wire=wire)
    assert historical and result['runControl']['executionState']=='blocked'
    assert result['runControl']['state']=='ready' and result['runControl']['unknownCount']==1
    report_task=next(t for t in result['taskResults'] if t['kind']=='evening_scan')
    assert report_task['status']=='completed' and result['report']['eveningCards']
    assert result['materials']['items']
    assert len([a for a in result['externalAttempts'] if a[0]==historical[0] and a[2]=='unknown'])==1

@pytest.mark.parametrize('case',['title_sibling_control','title_sibling_reason_type','title_sibling_foreign_index', 'title_sibling_before_model', 'title_sibling_before_gap', 'title_sibling_after_gap'])
def test_single_title_row_scope(case,tmp_path,monkeypatch):
    wire=mixed.mixed_wire('control')
    reply=wire.reply
    def response(request,body):
        value=reply(request,body).json()
        if body['params']['name']=='list_flash':
            for index,row in enumerate(value['result']['structuredContent']['data']['items'],start=1):
                row['title']=f'离线验收标题 {index:04d}：独立新增经营事实'
        return httpx.Response(200,json=value)
    wire.reply=response
    normal=current._FlashReportTransport.respond
    injected=[]
    wire_hashes=[]
    interrupted=[]
    if case.endswith(('before_model', 'before_gap', 'after_gap')):
        original_checkpoint = store.record_execution_checkpoint
        target_stage = 'model:titleBatch' if case.endswith('before_model') else 'title_batch_gap'
        def checkpoint(**kwargs):
            hit = kwargs['stage'] == target_stage and kwargs['status'] == 'completed' and not interrupted
            if hit and not case.endswith('after_gap'):
                interrupted.append(True)
                raise SqliteWriteBusy('B102 interrupted title derivative')
            result = original_checkpoint(**kwargs)
            if hit:
                interrupted.append(True)
                raise SqliteWriteBusy('B102 interrupted after durable title gap')
            return result
        monkeypatch.setattr(store, 'record_execution_checkpoint', checkpoint)
    def model(self,request):
        wire_hashes.append(hashlib.sha256(request.content).hexdigest())
        response=normal(self,request)
        packet=self._packet(request)
        if 'items' in packet and 'inputCount' not in packet and case!='title_sibling_control':
            value=json.loads(response.json()['choices'][0]['message']['content'])
            assert len(value['items'])==4
            if case=='title_sibling_foreign_index':
                value['items'][0]['i']=999999
            else:
                value['items'][0]['reason']=42
            injected.append(dict(inputRefs=[dict(documentId=row['documentId'],revision=row['revision']) for row in packet['items']],
                                 outputRows=value['items']))
            return self._ok(value)
        return response
    monkeypatch.setattr(current._FlashReportTransport,'respond',model)
    _, _, _, _, _, slices = existing.make_collected_case(tmp_path,monkeypatch,wire=wire)
    result=observe(tmp_path/'b92-flash.sqlite',case=case,handlers=[],wire=wire)
    result['injected']=injected
    emit(f'{case}.json', result)
    if case=='title_sibling_control':
        assert result['report']['eveningCards'] and len(result['materials']['items'])==4
    else:
        assert injected and result['report']['eveningCards']
        assert all(card['canSelect'] for card in result['report']['eveningCards'])
        assert len(result['materials']['items']) == 3
        assert result['report']['delivery']['counts']['titleFailed'] == 1
        assert result['report']['delivery']['counts']['titleProcessed'] == 3
        assert result['report']['delivery']['rankingScope'] == 'completed_subset'
        gaps = [g for g in result['report']['delivery']['gaps'] if g['stage'] == 'title_triage']
        assert len(gaps) == 1 and len(gaps[0]['sourceRefs']) == 1
        assert gaps[0]['sourceRefs'][0]['documentId'] == injected[0]['inputRefs'][0]['documentId']
    if case.endswith(('before_model', 'before_gap', 'after_gap')):
        assert interrupted == [True] and slices[0] == 'queued' and slices[-1] == 'completed'
        assert len(injected) == 1 and len(wire_hashes) == len(set(wire_hashes))
        assert not any(a[2] in {'started', 'unknown'} for a in result['externalAttempts'])

@pytest.mark.parametrize('case',['research_sibling_control','research_sibling_status_type','research_sibling_company_type','research_sibling_empty_conclusion'])
def test_research_common_field_scope(case,tmp_path,monkeypatch):
    wire=mixed.mixed_wire('control')
    normal=current._FlashReportTransport.respond
    chosen=[]
    def model(self,request):
        response=normal(self,request)
        packet=self._packet(request)
        if packet.get('action')=='research_round' and case!='research_sibling_control':
            key=packet['evidencePacket']['event']['canonicalKey']
            if not chosen:
                chosen.append(key)
            if key==chosen[0]:
                value=json.loads(response.json()['choices'][0]['message']['content'])
                if case=='research_sibling_status_type':
                    value['conclusion']['researchStatus']=[]
                elif case=='research_sibling_company_type':
                    value['conclusion']['companyMappings'][0]['companyCode']=['bad']
                else:
                    value['conclusion']={}
                return self._ok(value)
        return response
    monkeypatch.setattr(current._FlashReportTransport,'respond',model)
    existing.make_collected_case(tmp_path,monkeypatch,wire=wire)
    result=observe(tmp_path/'b92-flash.sqlite',case=case,handlers=[],wire=wire)
    assert result['report']['status']=='partial'
    assert result['report']['eveningCards'] and result['materials']['items']
    if case!='research_sibling_control':
        assert result['report']['delivery']['gaps']

def test_b92_current_morning_inflight_deadline(tmp_path,monkeypatch):
    from threading import Event, Thread
    from neckline.api import k10 as api
    from neckline.k10.cli import enqueue_collection
    from neckline.k10.collection_runtime import create_collection_handler
    from neckline.k10.jin10_mcp import Jin10Client
    from neckline.k10.metering import MeteredProvider
    from neckline.k10.providers import ProviderResolution
    from neckline.k10.worker import run_once
    db=tmp_path/'b92-deadline.sqlite'
    run_id,run_rev,exec_id,exec_rev,collection_id,collection_rev=current._bindings(db)
    at=datetime(2026,9,27,8,35,tzinfo=current.SHANGHAI)
    deadline=at.replace(hour=9,minute=20)
    business_now=[at]
    current._cli('collection-control','--db',str(db),'--state','open',
                 '--config-id',collection_id,'--config-revision',str(collection_rev))
    collected_id=enqueue_collection(db_path=db,slot=at.replace(minute=0),
                    config_id=collection_id,config_revision=collection_rev,now=at)
    wire=current._morning_wire()
    def client_factory(**kwargs):
        return Jin10Client(**kwargs,transport=httpx.MockTransport(wire))
    collected=run_once(db_path=db,task_id=collected_id,worker_id='review-deadline-collect',
        lease_for=timedelta(minutes=5),clock=lambda:at,
        handlers={'collect_news':create_collection_handler(tushare_token=None,
                    jin10_token=current.JIN10_TOKEN,client_factory=client_factory)},
        require_b76_contract=True)
    assert collected.status=='failed'  # TuShare absent, valid Jin10 flash retained.
    collected_input=store.task_execution_input(task_id=collected_id,db_path=db)
    refs=collected_input['checkpoint']['sources']['jin10-flash']['documentRefs']
    entered,release=Event(),Event()
    class BlockingTransport(current._FlashReportTransport):
        def respond(self,request):
            packet=self._packet(request)
            if packet.get('documentId') in self.morning_document_ids:
                entered.set()
                if not release.wait(30):
                    raise AssertionError('review did not release in-flight request')
            return super().respond(request)
    monkeypatch.setattr(BlockingTransport,'morning_document_ids',{r['documentId'] for r in refs})
    monkeypatch.setattr(base,'TITLE_COUNT',1)
    monkeypatch.setattr(base,'DeterministicTransport',BlockingTransport)
    base.install_offline_transports(monkeypatch,refusal_event=None,selected_event_count=0,fixture_run_at=at)
    provider=MeteredProvider(ledger_db=db,ledger_task='discovery',api_key='fixture',
        model='deepseek-v4-pro',name='fixture',api_url='https://fixture.invalid/v1/chat/completions',
        read_timeout=1,use_streaming=False)
    provider.max_attempts=1
    monkeypatch.setattr(pipeline,'resolve_deepseek_v4_pro',
        lambda **_:ProviderResolution('configured',provider,'fixture',None))
    monkeypatch.setattr(pipeline,'_now',lambda:business_now[0])
    class BusinessDateTime(datetime):
        @classmethod
        def now(cls,tz=None):
            return business_now[0].astimezone(tz) if tz else business_now[0].replace(tzinfo=None)
    monkeypatch.setattr(api,'datetime',BusinessDateTime)
    task_id=current._cli('enqueue','--db',str(db),'--kind','morning','--trading-day','2026-09-27',
        '--config-id',run_id,'--config-revision',str(run_rev),
        '--execution-config-id',exec_id,'--execution-config-revision',str(exec_rev))
    binding=store.task_execution_input(task_id=task_id,db_path=db)
    assert binding['executionProfile']['payload']['discovery']['reportInputContract']=='k10-collected-input-3.6.1-b92'
    results,faults=[],[]
    def handle(context):
        try:
            return pipeline.production_scan_handler(context,tushare_token=None,
                parquet_dir=tmp_path/'parquet',now=lambda:business_now[0])
        except BaseException as exc:
            faults.append(f'{type(exc).__name__}: {exc}')
            raise
    def execute():
        results.append(run_once(db_path=db,task_id=task_id,worker_id='review-deadline-report',
            lease_for=timedelta(minutes=5),clock=lambda:datetime.now(current.SHANGHAI),
            handlers={'morning_scan':handle},require_b76_contract=True))
    thread=Thread(target=execute,name='review-deadline-thread')
    thread.start()
    try:
        assert entered.wait(15),dict(results=results,faults=faults)
        with base.actual_api(db,config_id=run_id,config_revision=run_rev,
                            execution_id=exec_id,execution_revision=exec_rev) as client:
            initial=client.get('/api/v1/k10/v2/reports/latest?window=morning').json()
            business_now[0]=deadline
            due=client.get('/api/v1/k10/v2/reports/latest?window=morning').json()
            assert initial['reason']['reason']=='report_processing'
            assert datetime.fromisoformat(due['report']['deliveryDeadlineAt'])==deadline
            assert due['report']['reportId']==initial['report']['reportId']
            by_id=client.get(f"/api/v1/k10/v2/reports/{due['report']['reportId']}").json()
            output=dict(case='b92_morning_inflight_deadline',taskId=task_id,
                contract=binding['executionProfile']['payload']['discovery']['reportInputContract'],
                beforeDeadline=initial,atDeadline=due,byId=by_id)
            emit('b92_morning_inflight_deadline.json', output)
            assert due['reason']['reason']=='delivery_deadline_reached'
            assert by_id['reason']['reason']=='delivery_deadline_reached'
            assert due['state']=='processing' and due['report']['delivery'] is None
            assert due['report']['availableAt'] is None
    finally:
        release.set()
        thread.join(20)
    assert not thread.is_alive() and not faults, faults

@pytest.mark.parametrize('case',['tavily_receipt_control','tavily_receipt_unused_surrogate', 'tavily_receipt_unused_surrogate_after_receipt'])
def test_tavily_exact_receipt_boundary(case,tmp_path,monkeypatch):
    class TavilyResearchTransport(current._FlashReportTransport):
        research_mode='tavily'
    normal=base._TavilyWire.respond
    injected=[]
    request_hashes=[]
    def tavily(self,request):
        request_hashes.append(hashlib.sha256(request.content).hexdigest())
        response=normal(self,request)
        query=json.loads(request.content).get('query','')
        if case!='tavily_receipt_control' and 'event-000' in query:
            value=response.json()
            value['optionalComment']='unused provider string\ud800'
            injected.append(query)
            return httpx.Response(200,content=json.dumps(value,ensure_ascii=True).encode('ascii'),
                                  headers={'Content-Type':'application/json'})
        return response
    monkeypatch.setattr(base._TavilyWire,'respond',tavily)
    wire=mixed.mixed_wire('control')
    handlers=[]
    receipt_failures=[]
    interrupted=[]
    if case.endswith('after_receipt'):
        original_checkpoint = store.record_execution_checkpoint
        def checkpoint(**kwargs):
            if kwargs['stage'] == 'tavily_evidence' and injected and not interrupted:
                interrupted.append(True)
                raise SqliteWriteBusy('B102 interrupted after durable Tavily receipt')
            return original_checkpoint(**kwargs)
        monkeypatch.setattr(store, 'record_execution_checkpoint', checkpoint)
    settle=store.settle_tavily_response_with_receipt
    def receipt(**kwargs):
        try:
            return settle(**kwargs)
        except Exception as exc:
            receipt_failures.append(dict(exception=type(exc).__name__,
                frames=[dict(path=t.filename,line=t.lineno,function=t.name) for t in traceback.extract_tb(exc.__traceback__)]))
            raise
    monkeypatch.setattr(store,'settle_tavily_response_with_receipt',receipt)
    original=pipeline.production_scan_handler
    def handler(*args,**kwargs):
        try:
            result=original(*args,**kwargs)
        except Exception as exc:
            handlers.append(dict(exception=type(exc).__name__,code=getattr(exc,'code',None),
                frames=[dict(path=t.filename,line=t.lineno,function=t.name) for t in traceback.extract_tb(exc.__traceback__)]))
            raise
        handlers.append(dict(status=result.status,stage=result.stage))
        return result
    monkeypatch.setattr(pipeline,'production_scan_handler',handler)
    error=None
    try:
        existing.make_collected_case(tmp_path,monkeypatch,wire=wire,transport_type=TavilyResearchTransport)
    except (AssertionError,store.K10Conflict) as exc:
        error=str(exc)
    result=observe(tmp_path/'b92-flash.sqlite',case=case,handlers=handlers,wire=wire,expectation_error=error)
    result['receiptFailures']=receipt_failures
    emit(f'{case}.json', result)
    assert error is None and not receipt_failures
    assert len(result['report']['eveningCards']) == 3
    assert len(result['materials']['items']) == 4
    assert not any(a[2] in {'started', 'unknown'} for a in result['externalAttempts'])
    if case != 'tavily_receipt_control':
        assert injected
        with sqlite3.connect(tmp_path/'b92-flash.sqlite') as conn:
            receipt_rows = conn.execute('SELECT task_id,item_key,input_sha256 FROM k10_tavily_response_receipts').fetchall()
        receipts = [store.load_tavily_response_receipt(task_id=row[0], item_key=row[1], input_sha256=row[2],
                    db_path=tmp_path/'b92-flash.sqlite') for row in receipt_rows]
        assert all(receipts)
        assert any('\\ud800' in json.dumps(r['payload'], ensure_ascii=True) for r in receipts)
    if case.endswith('after_receipt'):
        assert interrupted == [True]
        assert len(request_hashes) == len(set(request_hashes))
