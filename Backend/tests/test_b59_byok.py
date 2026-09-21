"""BYOK acceptance: authenticated settings, real workers, deterministic HTTP only."""
import json
from datetime import timedelta
from pathlib import Path
import sqlite3

import httpx
import pytest

from neckline import settings_store
from neckline.k10 import pipeline, runtime, store
from neckline.k10 import metering
from neckline.k10.providers import resolve_deepseek_v4_pro, runtime_execution_profile
from neckline.k10.worker import run_once
from neckline.k10.v2_store import read_report
from tests.test_b54_review_regressions import client_for
from tests.test_k10_api import _freeze_k10_clocks
import tests.test_v310_pipeline_e2e as e2e

PATH = '/api/v1/settings/providers'


def _allow_isolated_capacity(monkeypatch, *, endpoint: str, model: str) -> None:
    """Tests must name a reviewed endpoint/model bound; production has none for arbitrary BYOK."""
    monkeypatch.setitem(metering._MODEL_CAPABILITIES, (endpoint, model), {
        "contextTokens": 1_000_000, "maxOutputTokens": 384_000, "counter": "isolated-test-v41-bound",
    })


def test_settings_edit_switch_rotate_clear_delete(client, AUTH, api_env):
    a = {'name':'常用 模型', 'baseUrl':'https://gateway.example/v1/', 'model':'vendor/model-a', 'apiKey':'secret-a'}
    response = client.post(PATH, headers=AUTH, json=a)
    assert response.status_code == 201, response.text
    assert response.json()['baseUrl'] == 'https://gateway.example/v1/chat/completions'
    assert 'secret-a' not in response.text
    b = {**a, 'name':'备用', 'model':'model-b', 'apiKey':'secret-b'}
    assert client.post(PATH, headers=AUTH, json=b).status_code == 201
    assert [(r.name,r.enabled) for r in settings_store.list_providers(db_path=api_env.db_path)] == [('常用 模型',False),('备用',True)]
    assert client.post(PATH, headers=AUTH, json=a).status_code == 409
    assert settings_store.get_provider_record('备用', db_path=api_env.db_path).enabled
    edit = client.put(PATH+'/常用 模型', headers=AUTH, json={'enabled':True,'model':'new-model'})
    assert edit.status_code == 200 and edit.json()['keySet']
    assert settings_store.get_provider_record('常用 模型', db_path=api_env.db_path).api_key == 'secret-a'
    assert not settings_store.get_provider_record('备用', db_path=api_env.db_path).enabled
    blocked = client.put(PATH+'/常用 模型', headers=AUTH, json={'baseUrl':'https://different.example/v1'})
    assert blocked.status_code == 422
    assert settings_store.get_provider_record('常用 模型', db_path=api_env.db_path).base_url == a['baseUrl'].rstrip('/')+'/chat/completions'
    assert client.put(PATH+'/常用 模型', headers=AUTH, json={'baseUrl':'https://different.example/v1','apiKey':'rotated-key'}).status_code == 200
    assert client.put(PATH+'/常用 模型', headers=AUTH, json={'apiKey':''}).json()['keySet'] is False
    assert client.delete(PATH+'/常用 模型', headers=AUTH).status_code == 200
    assert 'secret-' not in client.get(PATH,headers=AUTH).text


@pytest.mark.parametrize('patch', [
    {'baseUrl':'http://gateway.example/v1'}, {'baseUrl':'https://user:pass@gateway.example'},
    {'baseUrl':'https://gateway.example/v1?key=secret'}, {'baseUrl':'https://gateway.example/v1/responses'},
    {'baseUrl':'https://gateway.example/v1/messages'}, {'baseUrl':None}, {'model':None}, {'model':'  '},
    {'model':'bad model'}, {'baseUrl':'https://gateway.example:99999'},
])
def test_invalid_connection_never_overwrites_key_or_settings(client, AUTH, api_env, patch):
    assert client.post(PATH,headers=AUTH,json={'name':'one','baseUrl':'https://gateway.example/v1','model':'model-a','apiKey':'secret'}).status_code == 201
    assert client.put(PATH+'/one',headers=AUTH,json=patch).status_code == 422
    row=settings_store.get_provider_record('one',db_path=api_env.db_path)
    assert row.model=='model-a' and row.api_key=='secret' and row.enabled


def _analysis_task(tmp_path, monkeypatch):
    db, _, _, _, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    _freeze_k10_clocks(monkeypatch,e2e.RUN_AT.isoformat())
    card=read_report(db_path=db)['eveningCards'][0]
    with client_for(db) as client:
        task_id=client.post('/api/v1/k10/company-windows/'+card['companyWindowId']+'/selection',
            json={'action':'keep','idempotencyKey':'byok-analysis'}).json()['analysisJobId']
    return db,task_id


def test_real_analysis_worker_uses_saved_endpoint_model_key_and_pins_resume(tmp_path, monkeypatch, client, AUTH, api_env):
    from tests.debate_fixture import debate_text
    import neckline.api.app as api
    db,task_id=_analysis_task(tmp_path,monkeypatch)
    monkeypatch.setattr(api,'_DB_PATH_OVERRIDE',db)
    assert client.post(PATH,headers=AUTH,json={'name':'my-model','baseUrl':'https://gateway.example/custom/v1','model':'vendor/model-x','apiKey':'key-x'}).status_code==201
    _allow_isolated_capacity(monkeypatch, endpoint='https://gateway.example/custom/v1/chat/completions', model='vendor/model-x')
    calls=[]
    def respond(request):
        calls.append(request)
        # Switch in the middle of pro/con. Current task keeps its pinned provider.
        if len(calls)==1:
            settings_store.create_provider('second','https://second.example/v1','model-y',api_key='key-y',db_path=db)
        return httpx.Response(200,json={'choices':[{'message':{'content':debate_text('合成输出')},'finish_reason':'stop'}]})
    monkeypatch.setattr(httpx,'Client',lambda **kw:e2e._HTTPX_CLIENT(**{**kw,'transport':httpx.MockTransport(respond)}))
    task=run_once(db_path=db,worker_id='byok',lease_for=timedelta(minutes=5),clock=lambda:e2e.RUN_AT,
        handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),task_id=task_id)
    assert task.status=='completed',task
    assert len(calls)==2
    for request in calls:
        assert str(request.url)=='https://gateway.example/custom/v1/chat/completions'
        assert request.headers['authorization']=='Bearer key-x'
        wire=json.loads(request.content)
        assert wire['model']=='vendor/model-x' and wire['max_tokens']>0
        assert 'thinking' not in wire and 'reasoning_effort' not in wire
    checkpoint=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']
    assert checkpoint['providerBinding']=={'name':'my-model','endpoint':'https://gateway.example/custom/v1/chat/completions','model':'vendor/model-x'}
    assert 'key-x' not in json.dumps(checkpoint)
    configuration=store.load_task_analysis_config(task_id=task_id,db_path=db)['payload']
    pinned=resolve_deepseek_v4_pro(configuration=configuration,task='analysis',task_id=task_id,db_path=db)
    assert pinned.provider.model=='vendor/model-x'
    settings_store.update_provider('my-model',api_key='rotated',db_path=db)
    assert resolve_deepseek_v4_pro(configuration=configuration,task='analysis',task_id=task_id,db_path=db).provider.api_key=='rotated'
    settings_store.update_provider('my-model',model='changed',db_path=db)
    assert resolve_deepseek_v4_pro(configuration=configuration,task='analysis',task_id=task_id,db_path=db).state=='not_configured'
    fresh=resolve_deepseek_v4_pro(configuration=configuration,task='analysis',db_path=db)
    assert fresh.provider.model=='model-y'
    assert len(calls)==2


def test_request_and_cache_identity_include_provider_but_not_credentials(tmp_path):
    from tests.test_k10_providers import _config,_record
    from neckline.llm.base import ChatMessage
    first=resolve_deepseek_v4_pro(configuration=_config(),task='analysis',db_path=tmp_path/'none',provider_records=[_record()]).provider
    other=resolve_deepseek_v4_pro(configuration=_config(),task='analysis',db_path=tmp_path/'none',provider_records=[_record(base_url='https://other.example/v1',model='other-model')]).provider
    options={'response_format':'json','model_options':{'maxTokens':100}}
    args=([ChatMessage(role='user',content='input')],)
    assert first._request_input_sha256(args,options)!=other._request_input_sha256(args,options)
    before=first._request_input_sha256(args,options)
    first.api_key='rotated'
    assert first._request_input_sha256(args,options)==before
    profile={'payload':{'discovery':{'model':'deepseek-v4-pro'}}}
    effective=runtime_execution_profile(profile,other)
    assert 'runtimeProvider' not in profile and effective['runtimeProvider']['model']=='other-model'
    assert 'rotated' not in json.dumps(effective)


def test_saved_byok_drives_real_cli_discovery_through_publication(tmp_path,monkeypatch,client,AUTH,api_env):
    """A saved provider must survive the B78 direct-round CLI path.

    The previous fixture asserted calls to retired research sub-stages.  This
    uses the production CLI/worker path and rejects any such call while still
    proving that every model request uses the saved endpoint, key and model.
    """
    import socket
    from tests import v340_acceptance_fixture as base
    from tests.test_v350_cli_api import DirectRoundTransport

    seen = []
    class ObservedDirectTransport(DirectRoundTransport):
        def respond(self, request):
            wire = json.loads(request.content)
            seen.append((str(request.url), request.headers.get('authorization'), wire))
            return super().respond(request)

    original_seed = base.seed_database
    def seed_with_saved_provider(path, **kwargs):
        result = original_seed(path, **kwargs)
        settings_store.create_provider('my-gateway', 'https://gateway.example/v1', 'vendor/custom-model',
                                       api_key='custom-fixture', db_path=path)
        return result

    original_provider = base.MeteredProvider
    class SavedProvider(original_provider):
        def __init__(self, **kwargs):
            super().__init__(**{**kwargs, 'api_key': 'custom-fixture', 'model': 'vendor/custom-model',
                                'api_url': 'https://gateway.example/v1/chat/completions'})

    monkeypatch.setattr(base, 'TITLE_COUNT', 130)
    monkeypatch.setattr(base, 'DeterministicTransport', ObservedDirectTransport)
    monkeypatch.setattr(base, 'seed_database', seed_with_saved_provider)
    monkeypatch.setattr(base, 'MeteredProvider', SavedProvider)
    monkeypatch.setattr(socket.socket, 'connect', base._deny_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', base._deny_network)
    _allow_isolated_capacity(monkeypatch, endpoint='https://gateway.example/v1/chat/completions', model='vendor/custom-model')

    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name='saved-byok', selected_event_count=1)
    assert flow.task_status == 'completed', flow
    assert seen
    assert all(url == 'https://gateway.example/v1/chat/completions' for url, _, _ in seen)
    assert all(authorization == 'Bearer custom-fixture' for _, authorization, _ in seen)
    assert all(wire['model'] == 'vendor/custom-model' and wire['max_tokens'] > 0
               and 'thinking' not in wire for _, _, wire in seen)
    assert not any(name.startswith('forbidden:') for name in flow.calls)
    assert flow.calls.get('research:research_round') == 1
    saved = settings_store.get_provider_record('my-gateway', db_path=flow.db_path)
    assert saved and saved.model == 'vendor/custom-model'


def test_invalid_key_type_never_echoes_submitted_secret(client,AUTH):
    response=client.post(PATH,headers=AUTH,json={'name':'bad','baseUrl':'https://gateway.example/v1','model':'model','apiKey':{'secret':'never-echo-this'}})
    assert response.status_code==422
    assert 'never-echo-this' not in response.text
