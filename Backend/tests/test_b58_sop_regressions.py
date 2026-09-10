"""B57 architecture findings: isolated real producers and deterministic transports."""
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
import httpx

from neckline.k10 import morning_runtime, pipeline, runtime, store
from neckline.k10.providers import ProviderResolution
from neckline.k10.schema import initialize_schema
from neckline.k10.v2_store import publish_cards, read_report
from neckline.k10.windows import SHANGHAI
from neckline.k10.worker import run_once
from tests.test_b54_review_regressions import client_for, later_scan
from tests.test_k10_api import _freeze_k10_clocks
from tests.test_k10_end_to_end import _result, _debate_result
from tests.test_k10_lifecycle import _calendar, _candidate, _input
import tests.test_v310_pipeline_e2e as e2e


@pytest.mark.parametrize('role', ['pro', 'con'])
def test_known_failure_receipt_survives_before_artifact_write(tmp_path, monkeypatch, role):
    from neckline.k10.metering import MeteredProvider
    from tests.debate_fixture import debate_text
    db, _, _, _, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    _freeze_k10_clocks(monkeypatch,e2e.RUN_AT.isoformat())
    card = read_report(db_path=db)['eveningCards'][0]
    with client_for(db) as client:
        task_id = client.post('/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection',
            json={'action':'keep','idempotencyKey':'invalid-receipt'}).json()['analysisJobId']
    calls = []
    failure_index = 1 if role == 'pro' else 2
    def respond(request):
        calls.append(request.content)
        content = '{}' if len(calls)==failure_index else debate_text('有效输出')
        return httpx.Response(200,json={'choices':[{'message':{'content':content},'finish_reason':'stop'}]})
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx,'Client',lambda **kw:e2e._HTTPX_CLIENT(**{**kw,'transport':transport}))
    provider = MeteredProvider(ledger_db=db,ledger_task='analysis',api_key='fixture',model='deepseek-v4-pro',
        name='fixture',api_url='https://api.deepseek.com/chat/completions',read_timeout=1,use_streaming=False)
    monkeypatch.setattr(runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'fixture',None))
    record = runtime.record_analysis_artifact
    def interrupt(**kwargs):
        if kwargs['artifact'].role==role and kwargs['artifact'].status=='failed':
            raise SystemExit('confirmed invalid result, artifact not committed')
        return record(**kwargs)
    def work(at):
        return run_once(db_path=db,worker_id='invalid-receipt',lease_for=timedelta(minutes=5),clock=lambda:at,
            handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),task_id=task_id)
    monkeypatch.setattr(runtime,'record_analysis_artifact',interrupt)
    with pytest.raises(SystemExit):
        work(e2e.RUN_AT)
    assert len(store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['knownFailedResultAttemptIds'])==1
    monkeypatch.setattr(runtime,'record_analysis_artifact',record)
    at=e2e.RUN_AT+timedelta(minutes=6)
    resumed=work(at)
    assert resumed.status=='failed' and len(calls)==failure_index
    with client_for(db) as client:
        assert client.post('/api/v1/k10/jobs/'+task_id+'/retry',json={'expectedAttemptCount':resumed.attempt_count}).status_code==200
    assert work(at+timedelta(seconds=1)).status=='completed' and len(calls)==3
    with sqlite3.connect(db) as conn:
        counts=dict(conn.execute('SELECT stage,COUNT(*) FROM k10_external_attempts WHERE task_id=? GROUP BY stage',(task_id,)))
    assert counts==({'analysisPro':2,'analysisCon':1} if role=='pro' else {'analysisPro':1,'analysisCon':2})


@pytest.mark.parametrize('first_status,lose_retry_result', [(402,False),(200,False),(200,True)])
def test_explicit_retry_only_authorizes_confirmed_failed_attempt(tmp_path, monkeypatch, first_status, lose_retry_result):
    from neckline.k10.metering import MeteredProvider
    from tests.debate_fixture import debate_text
    db, _, _, _, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    _freeze_k10_clocks(monkeypatch, e2e.RUN_AT.isoformat())
    card = read_report(db_path=db)['eveningCards'][0]
    with client_for(db) as client:
        task_id = client.post('/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection',
            json={'action':'keep','idempotencyKey':'explicit-retry'}).json()['analysisJobId']
    calls = []
    def respond(request):
        calls.append(request.content)
        if len(calls) == 1:
            return httpx.Response(first_status, json={'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]})
        return httpx.Response(200, json={'choices':[{'message':{'content':debate_text('有效输出')},'finish_reason':'stop'}]})
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx,'Client',lambda **kw:e2e._HTTPX_CLIENT(**{**kw,'transport':transport}))
    provider = MeteredProvider(ledger_db=db,ledger_task='analysis',api_key='fixture',model='deepseek-v4-pro',
        name='fixture',api_url='https://api.deepseek.com/chat/completions',read_timeout=1,use_streaming=False)
    monkeypatch.setattr(runtime,'resolve_deepseek_v4_pro',lambda **_:ProviderResolution('configured',provider,'fixture',None))
    def work(at):
        return run_once(db_path=db,worker_id='explicit-retry',lease_for=timedelta(minutes=5),clock=lambda:at,
            handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),task_id=task_id)
    first = work(e2e.RUN_AT)
    assert first.status == 'failed' and len(calls) == 1
    with client_for(db) as client:
        response = client.post('/api/v1/k10/jobs/'+task_id+'/retry',json={'expectedAttemptCount':first.attempt_count})
        assert response.status_code == 200,response.text
    if not lose_retry_result:
        assert work(e2e.RUN_AT+timedelta(seconds=1)).status == 'completed'
        assert len(calls) == 3
        return
    record = runtime.record_analysis_artifact
    def interrupt(**kwargs):
        raise SystemExit('second paid response lost before replacing old failed artifact')
    monkeypatch.setattr(runtime,'record_analysis_artifact',interrupt)
    with pytest.raises(SystemExit):
        work(e2e.RUN_AT+timedelta(seconds=1))
    monkeypatch.setattr(runtime,'record_analysis_artifact',record)
    assert work(e2e.RUN_AT+timedelta(minutes=6)).status == 'failed'
    assert len(calls) == 2


@pytest.mark.parametrize('status', [402, 429])
def test_extract_failure_receipt_survives_interruption(tmp_path, monkeypatch, status):
    from neckline.k10.discovery import ProviderThrottleYield
    from neckline.k10.verification import TavilyEvidenceGateway
    from neckline.search.tavily import TavilySearchClient
    from tests.test_v310_tavily import _SearchExtract, _gateway, _request, _frozen, QUESTION, PATH, _event, NOW, COMPLETED_AT
    calls = []
    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(status, headers={'Retry-After': '60'}, json={})
        return httpx.Response(200, json={'results': [{'url': json.loads(request.content)['urls'][0], 'raw_content': '完整正文'}], 'usage': {'credits': 1}})
    paid = TavilySearchClient('fixture', transport=httpx.MockTransport(respond))
    class Client(_SearchExtract):
        def extract(self, url):
            return paid.extract(url)
    db = tmp_path/'extract.sqlite'
    client = Client()
    gateway, task_id = _gateway(db, client)
    _frozen(db, task_id, 1)
    doc = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW, question=QUESTION, query_path=PATH).eligible_documents[0]
    def crash(**kwargs):
        raise SystemExit('ledger and failure receipt saved; task not yet yielded')
    monkeypatch.setattr(gateway, '_failed_response', crash)
    def fetch(g):
        return g.fetch_fulltext(event=_event(), document=doc, question=QUESTION, request=_request(doc), cutoff_at=NOW)
    with pytest.raises(SystemExit):
        fetch(gateway)
    restarted = TavilyEvidenceGateway(db_path=db, task_id=task_id, client=client,
        clock=lambda: COMPLETED_AT+timedelta(seconds=59), network_max_attempts=2)
    if status == 402:
        assert fetch(restarted).coverage['reason'] == 'insufficient_balance'
        assert len(calls) == 1
        return
    with pytest.raises(ProviderThrottleYield) as waiting:
        fetch(restarted)
    assert waiting.value.delay == 1 and len(calls) == 1
    restarted.clock = lambda: COMPLETED_AT+timedelta(seconds=60)
    assert fetch(restarted).state == 'available' and len(calls) == 2


@pytest.mark.parametrize('initial_status', [200, 402])
def test_morning_user_retry_accepts_confirmed_failure(tmp_path, monkeypatch, initial_status):
    from neckline.k10.metering import MeteredProvider
    calls = []
    def resolve(**kwargs):
        def respond(request):
            calls.append(request.content)
            answer = {} if len(calls) == 1 else {'material':False,'reasonStatus':'current','observationStatus':'current',
                'summary':'完整复核无新增变化','materialContraryEvidence':[]}
            return httpx.Response(initial_status if len(calls)==1 else 200,
                json={'choices':[{'message':{'content':json.dumps(answer)},'finish_reason':'stop'}]})
        transport = httpx.MockTransport(respond)
        monkeypatch.setattr(httpx,'Client',lambda **kw:e2e._HTTPX_CLIENT(**{**kw,'transport':transport}))
        provider = MeteredProvider(ledger_db=kwargs['db_path'],ledger_task='morning',api_key='fixture',model='deepseek-v4-pro',
            name='fixture',api_url='https://api.deepseek.com/chat/completions',read_timeout=1,use_streaming=False)
        return ProviderResolution('configured',provider,'fixture',None)
    monkeypatch.setattr(morning_runtime,'resolve_deepseek_v4_pro',resolve)
    db, _, _, _ = later_scan(tmp_path,monkeypatch,lambda *_:None)
    with sqlite3.connect(db) as conn:
        task_id, count, status = conn.execute("SELECT task_id,attempt_count,status FROM k10_tasks WHERE kind='morning_review'").fetchone()
    assert status == 'failed' and len(calls) == 1
    with client_for(db) as client:
        response = client.post('/api/v1/k10/jobs/'+task_id+'/retry',json={'expectedAttemptCount':count})
        assert response.status_code == 200,response.text
    at = datetime(2026,9,9,9,11,tzinfo=SHANGHAI)
    result = run_once(db_path=db,worker_id='morning-explicit',lease_for=timedelta(minutes=5),clock=lambda:at,
        handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),task_id=task_id)
    assert result.status == 'completed' and len(calls) == 2


def test_historical_evidence_rejects_superseded_source_revision(tmp_path):
    from tests.test_v310_research_storage import _seed, _claim, NOW, LATER
    from neckline.k10.research_store import create_research_snapshot, advance_research_snapshot, load_prior_research_evidence
    from neckline.k10.research_contracts import ResearchStageResult
    db = tmp_path/'historical.sqlite'
    snapshot = _seed(db)
    create_research_snapshot(snapshot=snapshot, db_path=db)
    advance_research_snapshot(snapshot_id=snapshot.snapshot_id, expected_revision=1, research_status='continue_research',
        execution_status='ok', stage_result=ResearchStageResult('extract_claims', claims=(_claim(),)),
        input_sha256='1'*64, updated_at=LATER, verification_cutoff_at=LATER, db_path=db)
    def load():
        return load_prior_research_evidence(task_id='next', event_id='event-1', input_source_refs=[],
            news_cutoff_at=LATER, verification_cutoff_at=LATER, prompt_contract_revision='k10-investigation-v1',
            model_parameters_sha256='c'*64, include_historical_sources=True, db_path=db)
    assert load()['claims']
    store.append_document_version(document_id='source-1', source_key='fixture', external_id='source-1', canonical_url=None,
        content_sha256='d'*64, published_at=NOW, published_precision='exact', fetched_at=LATER,
        original_text='更正旧原文', excerpt='更正', fetch_version='fixture', metadata={}, created_at=LATER, db_path=db)
    packet = load()
    assert packet['claims'] == []
    assert packet['isolated'][0]['reason'] == 'source_revision_superseded'


@pytest.mark.parametrize('final_relation', ['buyer', None])
def test_history_reuses_latest_relation_set_including_deletion(tmp_path, final_relation):
    from tests.test_v310_research_storage import _seed, NOW, LATER
    from neckline.k10.research_store import create_research_snapshot, advance_research_snapshot, load_prior_research_evidence
    from neckline.k10.research_contracts import ResearchStageResult
    db = tmp_path/'relation-revision.sqlite'
    snapshot = _seed(db)
    create_research_snapshot(snapshot=snapshot,db_path=db)
    for revision, relationship in [(1,'supplier'),(2,final_relation)]:
        mappings = [] if relationship is None else [{'companyCode':'300001.SZ','affectedStage':'送样',
            'relationEvidence':[{'documentId':'source-1','revision':1}], 'inference':{'relationship':relationship}, 'uncertainty':'待核'}]
        advance_research_snapshot(snapshot_id=snapshot.snapshot_id,expected_revision=revision,research_status='ready_for_comparison',
            execution_status='ok',stage_result=ResearchStageResult('close_research',conclusion={'companyMappings':mappings}),
            input_sha256=str(revision)*64,updated_at=LATER,verification_cutoff_at=LATER,db_path=db)
    packet = load_prior_research_evidence(task_id='next',event_id='event-1',input_source_refs=[],news_cutoff_at=LATER,
        verification_cutoff_at=LATER,prompt_contract_revision='k10-investigation-v1',model_parameters_sha256='c'*64,
        include_historical_sources=True,db_path=db)
    assert [row['inference']['relationship'] for row in packet['companyRelations']] == ([] if final_relation is None else [final_relation])


def test_title_interruption_does_not_authorize_another_paid_attempt(tmp_path, monkeypatch):
    from neckline.k10 import model_execution
    persist = model_execution._persist
    def interrupt(**kwargs):
        if kwargs['status'] == 'completed':
            raise SystemExit('provider returned; checkpoint not yet persisted')
        return persist(**kwargs)
    monkeypatch.setattr(model_execution, '_persist', interrupt)
    with pytest.raises(SystemExit):
        e2e._run(tmp_path, monkeypatch, v2=True)
    db = tmp_path/'b39-e2e.sqlite'
    with sqlite3.connect(db) as conn:
        task_id = conn.execute('SELECT task_id FROM k10_tasks').fetchone()[0]
        before = conn.execute('SELECT COUNT(*) FROM k10_external_attempts').fetchone()[0]
    monkeypatch.setattr(model_execution, '_persist', persist)
    at = e2e.RUN_AT+timedelta(minutes=6)
    monkeypatch.setattr(pipeline, '_now', lambda: at)
    task = run_once(db_path=db, worker_id='title-interruption', lease_for=timedelta(minutes=5), clock=lambda: at,
        handlers=pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet'), task_id=task_id)
    assert task.status == 'failed'
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM k10_external_attempts').fetchone()[0] == before == 1


def test_morning_success_before_cache_interruption_never_repeats_payment(tmp_path, monkeypatch):
    from neckline.k10 import v2_store
    from neckline.k10.metering import MeteredProvider
    calls = []
    def respond(request):
        calls.append(request.content)
        answer = {'material': False, 'reasonStatus': 'current', 'observationStatus': 'current',
                  'summary': '原理由继续观察', 'materialContraryEvidence': []}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(answer)}, 'finish_reason': 'stop'}]})
    transport = httpx.MockTransport(respond)
    def resolve(**kwargs):
        monkeypatch.setattr(httpx, 'Client', lambda **opts: e2e._HTTPX_CLIENT(**{**opts, 'transport': transport}))
        provider = MeteredProvider(ledger_db=kwargs['db_path'], ledger_task='morning', api_key='fixture', model='deepseek-v4-pro',
            name='fixture', api_url='https://api.deepseek.com/chat/completions', read_timeout=1, use_streaming=False)
        return ProviderResolution('configured', provider, 'fixture', None)
    monkeypatch.setattr(morning_runtime, 'resolve_deepseek_v4_pro', resolve)
    save = v2_store.save_morning_result
    def interrupt(**kwargs):
        raise SystemExit('paid morning result not yet cached')
    monkeypatch.setattr(v2_store, 'save_morning_result', interrupt)
    with pytest.raises(SystemExit):
        later_scan(tmp_path, monkeypatch, lambda *_: None)
    db = tmp_path/'b39-e2e.sqlite'
    with sqlite3.connect(db) as conn:
        task_id, lease_until = conn.execute("SELECT task_id,lease_until FROM k10_tasks WHERE kind='morning_review'").fetchone()
        assert conn.execute("SELECT state FROM k10_external_attempts WHERE task_id=?", (task_id,)).fetchone()[0] == 'succeeded'
    monkeypatch.setattr(v2_store, 'save_morning_result', save)
    at = datetime.fromisoformat(lease_until)+timedelta(seconds=1)
    task = run_once(db_path=db, worker_id='morning-interruption', lease_for=timedelta(minutes=5), clock=lambda: at,
        handlers=pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet'), task_id=task_id)
    assert task.status == 'failed' and len(calls) == 1


def test_next_day_new_articles_reuse_prior_facts_and_relations(tmp_path, monkeypatch):
    packets = []
    def capture(payload, value):
        if payload.get('action') == 'plan_gaps':
            packets.append(payload['evidencePacket'])
    db, _, task, _ = later_scan(tmp_path, monkeypatch, capture)
    assert task.status == 'completed' and packets
    packet = packets[0]
    prior = packet['reusableSourceEvidence']
    assert prior['claims'] and prior['companyRelations'], 'new-day inputs must not erase reusable prior evidence'
    old_refs = {(row['sourceRef']['documentId'], row['sourceRef']['revision']) for row in prior['claims']}
    new_refs = {(row['sourceRef']['documentId'], row['sourceRef']['revision']) for row in packet['claims']}
    assert not old_refs & new_refs
    allowed = {(ref['documentId'], ref['revision']) for ref in packet['allowedEvidenceRefs']}
    assert old_refs <= allowed
    assert 'originalText' not in json.dumps(prior)


@pytest.mark.parametrize('status', [402, 429])
def test_real_scan_tavily_failure_stops_or_resumes_affected_step(tmp_path, monkeypatch, status):
    from neckline.k10.verification import TavilyEvidenceGateway
    from neckline.search.tavily import TavilySearchClient
    calls = []
    def respond(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(status, headers={'Retry-After': '60'}, json={})
        return httpx.Response(200, json={'results': [], 'usage': {'credits': 1}})
    transport = httpx.MockTransport(respond)
    # Explicit isolated transport remains independent of the model fixture.
    class Client(TavilySearchClient):
        def search(self, query):
            with monkeypatch.context() as local:
                local.setattr(httpx, 'Client', lambda **kw: e2e._HTTPX_CLIENT(**{**kw, 'transport': transport}))
                return super().search(query)
    def gateway():
        db = tmp_path/'b39-e2e.sqlite'
        with sqlite3.connect(db) as conn:
            task_id = conn.execute('SELECT task_id FROM k10_tasks').fetchone()[0]
        policy = store.task_execution_profile(task_id=task_id, db_path=db)['payload']['discovery']
        return TavilyEvidenceGateway(db_path=db, task_id=task_id, client=Client('fixture'),
            clock=lambda: pipeline._now(), network_max_attempts=policy['networkMaxAttempts'])
    monkeypatch.setattr(e2e, '_Gateway', gateway)
    db, task_id, task, model_calls, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    assert len(calls) == 1
    if status == 402:
        assert task.status == 'failed'
        with sqlite3.connect(db) as conn:
            tail = conn.execute('SELECT error_code FROM k10_external_attempts WHERE task_id=? ORDER BY rowid DESC', (task_id,)).fetchone()[0]
        assert tail == 'insufficient_balance'
        return
    assert task.status == 'queued'
    before = list(model_calls)
    def work(at):
        monkeypatch.setattr(pipeline, '_now', lambda: at)
        handlers = pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet')
        return run_once(db_path=db, worker_id='throttle-resume', lease_for=timedelta(minutes=5), clock=lambda: at,
            handlers=handlers, task_id=task_id)
    assert work(e2e.RUN_AT+timedelta(seconds=59)) is None
    assert len(calls) == 1 and model_calls == before
    result = work(e2e.RUN_AT+timedelta(seconds=60))
    assert result.status == 'completed'
    assert calls[0]['query'] == calls[1]['query']
    assert model_calls.count('research:plan_gaps') == 1


@pytest.mark.parametrize('role', ['pro', 'con'])
def test_successful_paid_call_without_artifact_is_not_sent_again(tmp_path, monkeypatch, role):
    from neckline.k10.metering import MeteredProvider
    from tests.debate_fixture import debate_text
    db, _, _, _, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    _freeze_k10_clocks(monkeypatch, e2e.RUN_AT.isoformat())
    card = read_report(db_path=db)['eveningCards'][0]
    with client_for(db) as client:
        response = client.post('/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection',
            json={'action': 'keep', 'idempotencyKey': 'lost-result'})
        assert response.status_code == 200, response.text
        task_id = response.json()['analysisJobId']
    calls = []
    def respond(request):
        calls.append(request.content)
        return httpx.Response(200, json={'choices': [{'message': {'content': debate_text('已付费结果')}, 'finish_reason': 'stop'}]})
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, 'Client', lambda **opts: e2e._HTTPX_CLIENT(**{**opts, 'transport': transport}))
    provider = MeteredProvider(ledger_db=db, ledger_task='analysis', api_key='fixture', model='deepseek-v4-pro',
        name='fixture', api_url='https://api.deepseek.com/chat/completions', read_timeout=1, use_streaming=False)
    monkeypatch.setattr(runtime, 'resolve_deepseek_v4_pro', lambda **_: ProviderResolution('configured', provider, 'fixture', None))
    record = runtime.record_analysis_artifact
    def interrupt(**kwargs):
        if kwargs['artifact'].role == role:
            raise SystemExit('success settled; artifact not yet saved')
        return record(**kwargs)
    monkeypatch.setattr(runtime, 'record_analysis_artifact', interrupt)
    def work(at):
        return run_once(db_path=db, worker_id='lost-result', lease_for=timedelta(minutes=5), clock=lambda: at,
            handlers=pipeline.production_handlers(tushare_token='fixture', parquet_dir=tmp_path/'parquet'), task_id=task_id)
    with pytest.raises(SystemExit):
        work(e2e.RUN_AT)
    before = len(calls)
    assert before == (1 if role == 'pro' else 2)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT state FROM k10_external_attempts WHERE task_id=? ORDER BY rowid DESC LIMIT 1', (task_id,)).fetchone()[0] == 'succeeded'
    monkeypatch.setattr(runtime, 'record_analysis_artifact', record)
    task = work(e2e.RUN_AT+timedelta(minutes=6))
    assert len(calls) == before, 'a lost paid result must never authorize another natural attempt'
    assert task.status == 'failed'


def test_morning_reads_original_documents_fetched_after_news_cutoff(tmp_path, monkeypatch):
    class News(e2e._News):
        def fetch_incremental(self, request):
            fetched = request.window.cutoff_at + timedelta(minutes=1)
            result = super().fetch_incremental(request)
            return replace(result, documents=tuple(replace(doc, fetched_at=fetched) for doc in result.documents))
    monkeypatch.setattr(e2e, '_News', News)
    inputs = []
    class Provider:
        def chat(self, messages, **kwargs):
            inputs.append(json.loads(messages[-1].content.split('<untrusted-evidence>\n')[1].split('\n</untrusted-evidence>')[0]))
            return _result(json.dumps({'material': False, 'reasonStatus': 'current',
                'observationStatus': 'current', 'summary': '原资料与新增材料已对照', 'materialContraryEvidence': []}))
    monkeypatch.setattr(morning_runtime, 'resolve_deepseek_v4_pro', lambda **_: ProviderResolution('configured', Provider(), 'fixture', None))
    db, old, task, _ = later_scan(tmp_path, monkeypatch, lambda payload, value: None)
    assert task.status == 'completed' and inputs
    original = inputs[0]['original']
    assert original['documents'], 'news cutoff must not discard the published evidence fetched afterwards'
    assert len(original['documents']) == len(original['frozenEvidenceRefs'])
    assert any('供应商称创业板公司' in (doc['originalText'] or '') for doc in original['documents'])
    assert all(datetime.fromisoformat(doc['fetchedAt']) <= datetime.fromisoformat(original['cutoffAt']) for doc in original['documents'])
    assert store.get_opportunity(opportunity_id=old['opportunityId'], db_path=db)['d1TradeDate'] == old['d1TradeDate']


def test_publication_hook_crossing_open_rolls_back_all_records(tmp_path):
    db = tmp_path / 'boundary.sqlite'
    initialize_schema(db)
    _calendar(db, ('20260906', 1), ('20260907', 1), ('20260908', 1), ('20260909', 1))
    candidate, event = _candidate(db, suffix='hook', scan_id='scan-hook')
    at = datetime(2026, 9, 7, 9, 29, 59, tzinfo=SHANGHAI)
    def write_cards(conn, available):
        nonlocal at
        # An isolated stand-in for the real v2 hook's durable work, in the same transaction.
        conn.execute("INSERT INTO k10_events VALUES ('hook-write','hook-write',?)", (available,))
        at += timedelta(seconds=2)
    with pytest.raises(store.K10Conflict, match='跨越固定 D1'):
        store.publish_opportunities(batch_id='batch-hook', scan_id='scan-hook', publication_kind='morning',
            inputs=[_input(candidate, event, key='hook')], db_path=db, clock=lambda: at, publication_hook=write_cards)
    assert store.get_publication_batch(batch_id='batch-hook', db_path=db) is None
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT 1 FROM k10_events WHERE event_id='hook-write'").fetchone() is None
        assert conn.execute('SELECT count(*) FROM k10_company_windows').fetchone()[0] == 0


def test_real_keep_shares_all_catalyst_originals_with_both_roles(tmp_path, monkeypatch):
    _multi_analysis(tmp_path, monkeypatch)


def test_pre_b58_completed_pro_resumes_its_original_frozen_input(tmp_path, monkeypatch):
    _multi_analysis(tmp_path, monkeypatch, legacy=True)


def _multi_analysis(tmp_path, monkeypatch, legacy=False):
    db, _, _, _, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    stamp = e2e.RUN_AT.isoformat()
    store.create_scan(scan_id='multi', window_kind='evening', cutoff_at='2026-09-08T21:00:00+08:00',
        config_id='b39', config_revision=1, status='completed', coverage={}, created_at=stamp, completed_at=stamp, db_path=db)
    items = []
    for index in (1, 2):
        doc = store.append_document_version(document_id=f'multi-doc-{index}', source_key='tushare-major-news',
            external_id=f'multi-{index}', canonical_url=None, content_sha256=str(index)*64,
            published_at='2026-09-08T20:00:00+08:00', published_precision='exact', fetched_at='2026-09-08T21:01:00+08:00',
            original_text=f'催化{index}独有原文与条件', excerpt=None, fetch_version='fixture', metadata={}, created_at=stamp, db_path=db)
        refs = [{'documentId': f'multi-doc-{index}', 'revision': doc.revision}]
        event = store.append_event_revision(event_id=f'multi-event-{index}', stable_key=f'multi-event-{index}',
            headline=f'独立催化{index}', event_kind='company', facts={}, source_refs=refs, supersedes_revision=None, created_at=stamp, db_path=db)
        item = _input(f'multi-candidate-{index}', event.event_id, key=f'multi-key-{index}', company='300002.SZ', source_marker='evening')
        comparison = {**item.comparison, 'evidenceRefs': refs}
        store.create_candidate(candidate_id=item.candidate_id, scan_id='multi', event_id=event.event_id, event_revision=1,
            company_code=item.company_code, comparison=comparison, evidence=refs, created_at=stamp, db_path=db)
        items.append(replace(item, evidence_refs=tuple(refs), comparison=comparison))
    store.publish_opportunities(batch_id='multi-batch', scan_id='multi', publication_kind='evening', inputs=items,
        db_path=db, clock=lambda: e2e.RUN_AT, publication_hook=lambda conn, available: publish_cards(conn,
            report_id='multi-report', scan_id='multi', kind='evening', snapshot_id='k10-v2-20260909', inputs=items, available_at=available))
    card = read_report(db_path=db, report_id='multi-report')['eveningCards'][0]
    _freeze_k10_clocks(monkeypatch, stamp)
    with client_for(db) as client:
        response = client.post('/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection',
            json={'action': 'keep', 'idempotencyKey': 'multi-keep'})
        assert response.status_code == 200, response.text
        task_id = response.json()['analysisJobId']
    messages = []
    class Provider:
        def chat(self, prompt, **kwargs):
            messages.append('\n'.join(message.content for message in prompt))
            return _debate_result('完整讨论')
    monkeypatch.setattr(runtime, 'resolve_deepseek_v4_pro', lambda **_: ProviderResolution('configured', Provider(), 'fixture', None))
    def work(at):
        return run_once(db_path=db, worker_id='multi', lease_for=timedelta(minutes=5), clock=lambda: at,
            handlers=pipeline.production_handlers(tushare_token='fixture', parquet_dir=tmp_path/'parquet'), task_id=task_id)
    if legacy:
        record = runtime.record_analysis_artifact
        def interrupt(**kwargs):
            record(**kwargs)
            raise SystemExit('B57 completed pro, con not started')
        with monkeypatch.context() as old:
            old.setattr(runtime, '_attach_window_catalysts', lambda snapshot, *args, **kwargs: snapshot)
            old.setattr(runtime, 'record_analysis_artifact', interrupt)
            with pytest.raises(SystemExit):
                work(e2e.RUN_AT)
        task = work(e2e.RUN_AT+timedelta(minutes=6))
    else:
        task = work(e2e.RUN_AT)
    assert task.status == 'completed' and len(messages) == 2
    for message in messages:
        assert '催化1独有原文与条件' in message
        assert ('催化2独有原文与条件' in message) == (not legacy)
    with client_for(db) as client:
        chain = client.get('/api/v1/k10/company-windows/'+card['companyWindowId']+'/analysis-chain').json()
    assert all(len(artifact['inputLineage']['documentVersions']) == (1 if legacy else 2) for item in chain['items'] for artifact in item['analyses'])
    (tmp_path/'b58_multi_analysis.json').write_text(json.dumps(chain, ensure_ascii=False))
