"""BYOK acceptance: authenticated settings, real workers, deterministic HTTP only."""
import json
from datetime import timedelta
from pathlib import Path
import sqlite3

import httpx
import pytest

from neckline import settings_store
from neckline.k10 import pipeline, runtime, store
from neckline.k10.providers import resolve_deepseek_v4_pro, runtime_execution_profile
from neckline.k10.worker import run_once
from neckline.k10.v2_store import read_report
from tests.test_b54_review_regressions import client_for
from tests.test_k10_api import _freeze_k10_clocks
import tests.test_v310_pipeline_e2e as e2e

PATH = '/api/v1/settings/providers'


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
    import neckline.api.app as api
    observed=[]
    def configure(db):
        monkeypatch.setattr(api,'_DB_PATH_OVERRIDE',db)
        response=client.post(PATH,headers=AUTH,json={'name':'my-gateway','baseUrl':'https://gateway.example/v1','model':'vendor/custom-model','apiKey':'custom-fixture'})
        assert response.status_code==201,response.text
    def observe(request):
        wire=json.loads(request.content)
        observed.append(wire['model'])
        assert str(request.url)=='https://gateway.example/v1/chat/completions'
        assert request.headers['authorization']=='Bearer custom-fixture'
        assert wire['model']=='vendor/custom-model'
        assert 'thinking' not in wire and wire['max_tokens']>0
    db,task_id,task,calls,_=e2e._run(tmp_path,monkeypatch,v2=True,provider_setup=configure,request_observer=observe)
    assert task.status=='completed',task
    assert {'titleBatch','understand','research:compare_companies'} <= set(calls)
    assert len(observed)==len(calls)
    assert read_report(db_path=db)['eveningCards']
    binding=store.task_execution_input(task_id=task_id,db_path=db)['checkpoint']['providerBinding']
    assert binding['model']=='vendor/custom-model'
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT DISTINCT model FROM k10_fact_cache').fetchall()==[('vendor/custom-model',)]


def test_invalid_key_type_never_echoes_submitted_secret(client,AUTH):
    response=client.post(PATH,headers=AUTH,json={'name':'bad','baseUrl':'https://gateway.example/v1','model':'model','apiKey':{'secret':'never-echo-this'}})
    assert response.status_code==422
    assert 'never-echo-this' not in response.text
