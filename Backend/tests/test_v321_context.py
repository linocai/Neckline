"""Minimal reproductions of B71 structural-key recall; no private data."""
import json
import pytest
from neckline.k10 import v2_profiles


def _direct_query_round(payload, *, paths):
    """Ask the B78 runner for bounded searches in one direct round."""
    packet = payload['evidencePacket']
    ref = packet['allowedEvidenceRefs'][0]
    claim_id = packet['claims'][0]['claimId']
    question = {
        'questionId': 'q-1', 'claimIds': [claim_id], 'companyCodes': ['300001.SZ'],
        'question': '送样是否获公司确认', 'knownEvidence': [ref], 'missingEvidence': ['公司确认'],
        'supportCondition': '公司确认', 'refuteCondition': '公司否认',
        'decisionImpact': '影响主推', 'state': 'open', 'resumeCondition': '出现公司公告',
    }
    return {
        'action': 'research_round', 'questions': [question],
        'queryPaths': [{
            'pathId': path_id, 'questionId': 'q-1', 'query': query, 'intent': '确认送样',
            'targetSource': source, 'newPathReason': '首批必要来源',
            'expectedInformationGain': '确认主体', 'expectedJudgmentChange': '改变比较',
            'purposeKind': 'event_fact', 'targetRefs': [{'kind': 'claim', 'claimId': claim_id}],
            'state': 'planned', 'resultSummary': None,
        } for path_id, query, source in paths],
        'conclusion': {
            'researchStatus': 'continue_research', 'companyMappings': [],
            'materialGaps': ['公司确认'], 'stopReason': '需要必要公开资料',
            'resumeCondition': '取得公司公告',
        },
        'companyAssessments': [],
    }

@pytest.fixture
def profiles(monkeypatch):
    terms = ['UPS', 'PS', 'AR', 'GE', 'AI', '亚马逊云科技']
    index = [dict(ts_code=f'30000{i}.SZ', name=f'公司{i}', match_terms=[term]) for i, term in enumerate(terms)]
    rows = [dict(identity={'ts_code': row['ts_code'], 'name': row['name']}, summary=term+'业务，未经确认',
        review_status='local_draft_awaiting_user', compiled_at='2026-09-09', raw_evidence_file='private.json',
        sources=[], businesses=[], relationships=[]) for row,term in zip(index,terms)]
    def read(**kwargs):
        data=index if kwargs.get('index_only') else rows
        codes=kwargs.get('codes')
        return [row for row in data if codes is None or row.get('ts_code',row.get('identity',{}).get('ts_code')) in codes]
    monkeypatch.setattr(v2_profiles,'read_profiles',read)
    return lambda query: v2_profiles.retrieve_company_context(db_path=None,profiles_id='fixture',query=query)

@pytest.mark.parametrize('query', [json.dumps({'upstream_relevance':'电池','summary':'税率','coverage':'已公开','stage':'落地'}),
    'upstream summary coverage stage'])
def test_structural_names_never_recall_short_latin_terms(profiles,query):
    assert profiles(query)['candidateCompanyCodes']==[]

@pytest.mark.parametrize('query,expected',[('UPS电源',['300000.SZ']),('生成式AI认证变化',['300004.SZ']),
    ('亚马逊云科技认证变化',['300005.SZ']),('AR 产品',['300002.SZ'])])
def test_real_business_terms_keep_latin_boundaries_and_chinese(profiles,query,expected):
    assert profiles(query)['candidateCompanyCodes']==expected


def test_real_worker_never_sends_pool_or_history_as_research_input(tmp_path, monkeypatch):
    from tests.test_v310_pipeline_e2e import _run, assert_search_routes
    requests=[]
    def observe(request):
        wire=json.loads(request.content)
        text=wire['messages'][-1]['content']
        payload=json.loads(text.split('<untrusted-k10-evidence>\n',1)[1].split('\n</untrusted-k10-evidence>',1)[0])
        if payload.get('action'): requests.append(payload)
    db,task_id,task,calls,gateway=_run(tmp_path,monkeypatch,v2=True,request_observer=observe)
    assert task.status=='completed'
    assert requests
    for payload in requests:
        packet=payload['evidencePacket']
        assert 'fixedPool' not in packet['companyScope']
        assert not packet.get('toolOutcomes')
        from neckline.k10.research_context import PROTOCOL
        assert packet['contextProtocol'] == PROTOCOL


def test_local_projection_ignores_closed_unrelated_history_and_requires_visible_source():
    from neckline.k10.research_context import project_packet, public_packet
    from copy import deepcopy
    ref={'documentId':'d','revision':1}
    packet={'companyScope':{'fixedPool':[{'companyCode':'300002.SZ'}], 'companyProfiles':[]},
        'claims':[{'claimId':'c','sourceRef':ref,'text':'真实限定语'}],
        'questions':[{'questionId':'q','state':'open','claimIds':['c'],'knownEvidence':[]}],
        'allowedEvidenceRefs':[ref,{'documentId':'hidden','revision':1}], 'evidenceUpdates':[],
        'evidenceCards':[{**ref,'sourceStatements':[{'statement':'真实限定语'}]}]}
    first=project_packet('assess_evidence',packet)
    larger=deepcopy(packet)
    larger['questions'].append({'questionId':'unrelated','state':'answered','claimIds':['old'],'knownEvidence':[],'question':'冗余历史'*100})
    larger['claims'].append({'claimId':'old','sourceRef':{'documentId':'hidden','revision':1},'text':'旧历史'*100})
    assert public_packet(first)==public_packet(project_packet('assess_evidence',larger))
    assert first['allowedEvidenceRefs']==[ref]
    assert json.dumps(public_packet(first),ensure_ascii=False).count('真实限定语')==1


@pytest.mark.parametrize("repeat_request", [False, True])
@pytest.mark.parametrize("mixed_reply", [False, True])
def test_real_worker_local_field_read_continues_same_action(tmp_path, monkeypatch, repeat_request, mixed_reply):
    import httpx
    from tests.test_v310_pipeline_e2e import _run, assert_search_routes
    original=httpx.MockTransport
    seen=[]
    def transport(handler):
        def respond(request):
            message=json.loads(request.content)['messages'][-1]['content']
            payload=json.loads(message.split('<untrusted-k10-evidence>\n',1)[1].split('\n</untrusted-k10-evidence>',1)[0])
            if payload.get('action') == 'research_round':
                seen.append(payload)
                if not payload['evidencePacket'].get('contextResults') or (repeat_request and len(seen) == 2):
                    return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'action':'research_round','contextRequests':[
                        {'kind':'company_fields','companyCode':'300002.SZ','fields':['relationships'], 'purpose':'认证是否构成订单', **({'questionId':'draft-only'} if mixed_reply else {})}]},ensure_ascii=False)},'finish_reason':'stop'}],
                        'usage':{'prompt_tokens':3,'completion_tokens':3,'total_tokens':6}})
                result=payload['evidencePacket']['contextResults'][0]
                assert result['status']=='found'
                if mixed_reply:
                    assert 'questionId' not in result['request']
                    assert 'draft-only' not in json.dumps(payload['evidencePacket'].get('questions', []))
                    assert 'draft-only' not in json.dumps(payload['evidencePacket'])
                assert '不把认证扩大为未披露订单' in json.dumps(result,ensure_ascii=False)
            return handler(request)
        return original(respond)
    monkeypatch.setattr(httpx,'MockTransport',transport)
    db,task_id,task,calls,gateway=_run(tmp_path,monkeypatch,v2=True)
    assert task.status=='completed'
    # A repeated local request has no new visible input.  It must not create a
    # third paid B78 round or revive a retired feedback stage.
    assert len(seen) == (1 if mixed_reply else 2)
    if repeat_request:
        if not mixed_reply:
            assert seen[-1]['evidencePacket']['contextResults'][0]['status'] == 'found'
    from neckline.k10 import store
    from neckline.k10.research_context import PROTOCOL
    assert store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['contextProtocol'] == PROTOCOL


def test_shared_search_and_extract_ignore_event_and_invented_question_ids(tmp_path):
    from dataclasses import replace
    from tests.test_v310_tavily import _gateway, _SearchExtract, QUESTION, PATH, _request
    from tests.test_k10_verification import _event, NOW
    client=_SearchExtract()
    gateway,task=_gateway(tmp_path/'shared.sqlite',client)
    event=_event()
    one=gateway.fetch(event=event,retrieved_at=NOW,cutoff_at=NOW,question=QUESTION,query_path=PATH)
    related=replace(event,canonical_key='related-event',headline='同一来源另一影响')
    q={**QUESTION,'questionId':'new-id'}
    path={**PATH,'questionId':'new-id','pathId':'new-path'}
    two=gateway.fetch(event=related,retrieved_at=NOW,cutoff_at=NOW,question=q,query_path=path)
    assert client.calls==1
    assert one.documents==two.documents
    from tests.test_v310_tavily import _frozen
    _frozen(tmp_path/'shared.sqlite',task,1)
    request=_request(one.documents[0])
    first=gateway.fetch_fulltext(event=event,document=one.documents[0],question=QUESTION,request=request,cutoff_at=NOW)
    second=gateway.fetch_fulltext(event=related,document=one.documents[0],question=q,
        request={**request,'questionId':'new-id'},cutoff_at=NOW)
    assert client.extract_calls==1
    assert first.documents==second.documents
    changed={**path,'intent':'核查新增否认','query':'项目最新否认'}
    gateway.fetch(event=related,retrieved_at=NOW,cutoff_at=NOW,question=q,query_path=changed)
    assert client.calls==2  # independent contrary evidence remains executable


def test_unknown_local_source_and_pool_field_never_become_evidence():
    from neckline.k10.research_context import read_context
    result=read_context({'kind':'source','purpose':'核对否认','sourceRef':{'documentId':'invented','revision':1},'location':'paragraph:1'},
        state={'questions':[]},documents={},binding=None,eligible_refs=set())
    assert result['status']=='unknown_reference' and result['value'] is None


def test_same_task_shared_source_facts_keep_cutoff_and_no_company_inference(tmp_path):
    from dataclasses import replace
    from tests.test_v310_research_storage import _seed, _claim, NOW, LATER
    from neckline.k10 import store
    from neckline.k10.research_contracts import ResearchStageResult
    from neckline.k10.research_store import create_research_snapshot, advance_research_snapshot, load_prior_research_evidence
    db=tmp_path/'facts.sqlite'
    snapshot=_seed(db)
    create_research_snapshot(snapshot=snapshot,db_path=db)
    advance_research_snapshot(snapshot_id=snapshot.snapshot_id,expected_revision=1,research_status='continue_research',execution_status='ok',
        stage_result=ResearchStageResult('extract_claims',claims=(_claim(),)),input_sha256='1'*64,updated_at=LATER,db_path=db)
    store.append_event_revision(event_id='related',stable_key='related',headline='同文另一公司影响',event_kind='disclosure',facts={},
        source_refs=[{'documentId':'source-1','revision':1}],supersedes_revision=None,created_at=NOW,db_path=db)
    create_research_snapshot(snapshot=replace(snapshot,snapshot_id='receiving',event_id='related'),db_path=db)
    args=dict(task_id='task-1',event_id='related',input_source_refs=[{'documentId':'source-1','revision':1}],
        news_cutoff_at=NOW,verification_cutoff_at=LATER,prompt_contract_revision=snapshot.prompt_contract_revision,
        model_parameters_sha256=snapshot.model_parameters_sha256,db_path=db,exclude_snapshot_id='receiving')
    result=load_prior_research_evidence(**args)
    assert [c['text'] for c in result['claims']]==[_claim().text]
    assert result['companyRelations']==[]
    # Same source identity is insufficient when receiving event cutoffs differ.
    early=load_prior_research_evidence(**(args|{'news_cutoff_at':'2026-09-08T12:00:00+00:00'}))
    assert not early['claims']


def test_concurrent_shared_search_uses_one_physical_request_and_replays(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from dataclasses import replace
    from tests.test_v310_tavily import _gateway, _SearchExtract, QUESTION, PATH
    from tests.test_k10_verification import _event, NOW
    started,release=Event(),Event()
    class Client(_SearchExtract):
        def search(self,*args,**kwargs):
            started.set()
            assert release.wait(10)
            return super().search(*args,**kwargs)
    client=Client();gateway,task=_gateway(tmp_path/'concurrent.sqlite',client)
    args=dict(event=_event(),retrieved_at=NOW,cutoff_at=NOW,question=QUESTION,query_path=PATH)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(gateway.fetch,**args)
        assert started.wait(10)
        waiting=gateway.fetch(**(args|{'event':replace(_event(),canonical_key='related')}))
        assert waiting.coverage['reason']=='tavily_request_outcome_unknown'
        release.set()
        assert first.result().documents
    assert gateway.fetch(**args).coverage['requestState']=='reused'
    assert client.calls==1


def test_context_request_cannot_smuggle_unvalidated_business_updates():
    from neckline.k10.research_contracts import ResearchStageResult, ResearchContractError
    from tests.test_v310_research_storage import _claim
    with pytest.raises(ResearchContractError):
        ResearchStageResult('assess_evidence',claims=(_claim(),),context_requests=({'kind':'source','purpose':'回读'},))


def test_hidden_persisted_claim_cannot_gain_support_without_visible_content():
    from neckline.k10.research_context import project_packet
    from neckline.k10.investigation import decode_stage_result
    from tests.test_v310_research_storage import _claim
    claim=_claim().to_dict()
    packet=project_packet('assess_evidence',{'companyScope':{'fixedPool':[]},'claims':[claim],
        'questions':[], 'evidenceCards':[], 'allowedEvidenceRefs':[claim['sourceRef']], 'evidenceUpdates':[]})
    result=decode_stage_result({'action':'assess_evidence','claims':[{'claimId':claim['claimId'],'verificationStatus':'verified'}],
        'evidenceUpdates':[{'claimId':claim['claimId'],'sourceRef':claim['sourceRef'],'relation':'supports','location':'p:2','applicability':{}}]},
        action='assess_evidence',evidence_packet=packet)
    assert not result.claims and not result.evidence_updates


def test_repeated_route_uses_frozen_evidence_dependency_and_keeps_real_increment():
    from neckline.k10.research_context import collapse_repeated_work,question_dependency
    q={'questionId':'q','question':'是否形成订单','claimIds':['c'],'companyCodes':['300002.SZ'],
        'knownEvidence':[{'documentId':'d','revision':1}],'state':'open'}
    old={'pathId':'old','questionId':'q','intent':'核对订单','targetSource':'公司公告','state':'searched'}
    new={**old,'pathId':'new','state':'planned'}
    packet={'contextProtocol':'k10-v2-context-3.2.1','_localState':{'questions':[q],'queryPaths':[old],
        'pathDependencies':{'old':question_dependency(q)}}}
    assert not collapse_repeated_work({'queryPaths':[new]},packet)['queryPaths']
    packet['_localState']['questions']=[{**q,'knownEvidence':[{'documentId':'d','revision':2}]}]
    assert collapse_repeated_work({'queryPaths':[new]},packet)['queryPaths']==[new]


def test_real_worker_rejects_exact_duplicate_query_before_it_can_rebill(tmp_path,monkeypatch):
    import httpx
    from tests.test_v310_pipeline_e2e import _run, assert_search_routes
    original = httpx.MockTransport
    rounds = []

    def transport(handler):
        def respond(request):
            payload = json.loads(json.loads(request.content)['messages'][-1]['content'].split(
                '<untrusted-k10-evidence>\n', 1)[1].split('\n</untrusted-k10-evidence>', 1)[0])
            if payload.get('action') == 'research_round':
                rounds.append(payload)
                if len(rounds) == 1:
                        # Exact semantic/source duplication is locally blocked;
                        # it cannot become a second paid search.
                    return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(
                        _direct_query_round(payload, paths=[
                            ('path-1', '公告编号更正', '公司公告'),
                            ('path-1-duplicate', '公告编号更正', '公司公告'),
                        ]))}, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 3, 'completion_tokens': 3, 'total_tokens': 6}})
            return handler(request)
        return original(respond)

    monkeypatch.setattr(httpx, 'MockTransport', transport)
    db,_,task,_calls,gateway=_run(tmp_path,monkeypatch,v2=True)
    assert task.status=='completed'
    assert_search_routes(gateway, [('公告编号更正','公司公告','q-1')])
    assert len(rounds) == 1


def test_real_worker_keeps_distinct_necessary_first_round_sources(tmp_path, monkeypatch):
    import httpx
    from tests.test_v310_pipeline_e2e import _run, assert_search_routes
    original = httpx.MockTransport
    rounds = []

    def transport(handler):
        def respond(request):
            payload = json.loads(json.loads(request.content)['messages'][-1]['content'].split(
                '<untrusted-k10-evidence>\n', 1)[1].split('\n</untrusted-k10-evidence>', 1)[0])
            if payload.get('action') == 'research_round':
                rounds.append(payload)
                if len(rounds) == 1:
                    return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(
                        _direct_query_round(payload, paths=[
                            ('path-1', '项目送样公告', '公司公告'),
                            ('path-1-industry', 'site:industry.example 项目送样', '行业媒体'),
                        ]))}, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 3, 'completion_tokens': 3, 'total_tokens': 6}})
            return handler(request)
        return original(respond)

    monkeypatch.setattr(httpx, 'MockTransport', transport)
    _, _, task, _calls, gateway = _run(tmp_path, monkeypatch, v2=True, cli_entry=True)
    assert task.status == "completed"
    # A same-source duplicate cannot rebill, while the independent industry
    # route remains necessary evidence for the same accepted question.
    assert_search_routes(gateway, [('项目送样公告','公司公告','q-1'), ('site:industry.example 项目送样','行业媒体','q-1')])
    assert len(rounds) == 1


@pytest.mark.parametrize('action', ['plan_gaps', 'plan_queries', 'assess_evidence', 'close_research', 'compare_companies'])
def test_mixed_reply_reads_first_without_accepting_draft_evidence(action):
    from neckline.k10.investigation import decode_stage_result
    from neckline.k10.research_context import read_context
    request = {'kind':'source','sourceRef':{'documentId':'invented','revision':1},
               'location':'paragraph:1','purpose':'核对原文','questionId':'draft'}
    result = decode_stage_result({'action':action,'contextRequests':[request],
        'questions':[{'questionId':'draft'}], 'claims':'invalid draft',
        'conclusion':{'companyMappings':[{'companyCode':'invented'}]}},action=action)
    assert not result.questions and not result.claims and result.conclusion is None
    assert result.context_requests[0]['sourceRef']==request['sourceRef']
    read = read_context(result.context_requests[0],state={'questions':[]},documents={},binding=None,eligible_refs=set())
    assert read['status']=='unknown_reference' and read['value'] is None  # No invented source becomes evidence.


def test_context_requests_keep_existing_question_associations_and_reject_bad_shape():
    from neckline.k10.investigation import decode_stage_result, InvestigationError
    request={'kind':'question','id':'existing','questionId':'existing','purpose':'查看问题'}
    value={'action':'plan_gaps','contextRequests':[request],'questions':[{'questionId':'existing'}]}
    result=decode_stage_result(value,action='plan_gaps',evidence_packet={'_localState':{'questions':[{'questionId':'existing'}]}})
    assert result.context_requests[0]==request
    with pytest.raises(InvestigationError) as error:
        decode_stage_result({'action':'plan_gaps','contextRequests':{'kind':'source'}},action='plan_gaps')
    assert error.value.__cause__.field_name=='contextRequests'


def test_real_worker_retry_reports_innermost_context_constraint(tmp_path, monkeypatch):
    import httpx
    from tests.test_v310_pipeline_e2e import _run, assert_search_routes
    original = httpx.MockTransport
    seen = []
    def transport(handler):
        def respond(request):
            text = json.loads(request.content)['messages'][-1]['content']
            payload = json.loads(text.split('<untrusted-k10-evidence>\n', 1)[1].split('\n</untrusted-k10-evidence>', 1)[0])
            if payload.get('action') == 'research_round':
                seen.append(text)
                if len(seen) == 1:
                    return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps({
                        'action':'research_round','contextRequests':[{'kind':'invalid-kind','purpose':'核对'}]})},'finish_reason':'stop'}],
                        'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
                assert '上次输出未通过校验：' in text
                assert 'contextRequests' in text
            return handler(request)
        return original(respond)
    monkeypatch.setattr(httpx, 'MockTransport', transport)
    _, _, task, _, _ = _run(tmp_path, monkeypatch, v2=True)
    assert task.status == 'completed'
    assert len(seen) == 2
