"""Publisher navigation must not become event evidence or change source IDs."""
import json
from hashlib import sha256

import pytest

from neckline.k10.discovery import DiscoveryDocument
from neckline.k10 import research_material as material
from neckline.k10.research_context import project_packet, digest
from neckline.k10.research_navigation import navigation_view, sina_source, NAVIGATION_VERSION


def article():
    titles = ['其他公司资讯','财经早报','操盘必读：其他市场要闻','其他行业资讯','股海导航：公告提示',
              '其他公司变动','人物访谈','市场涨跌','投资资讯','四大证券报头版头条内容精华摘要']
    widget = '\n'.join(f'- {i:02}/{title}' for i,title in enumerate(titles,1))
    return ('注册资本5000万元，双方各持股45%。 [...] '+widget+
            ' [...] 该平台仍在技术验证阶段，尚无收入确认，不代表已签署客户合同。\n\n'
            '风险提示：上述投入不构成业绩承诺，未来进度存在不确定性。')


def document():
    text = article()
    return DiscoveryDocument('navigation-source',1,'2026-09-15T10:00:00+08:00','2026-09-15T20:00:00+08:00',
        text,text,{'publisher':'finance.sina.com.cn','title':'合资平台阶段说明'},analysis_text=text)


def test_widget_projection_preserves_prose_qualifiers_and_every_offset():
    text = article(); visible, manifest = navigation_view(text,enabled=True)
    assert manifest['version']==NAVIGATION_VERSION and len(text)==len(visible)
    assert manifest['originalTextSha256']==sha256(text.encode()).hexdigest()
    assert manifest['visibleTextSha256']==sha256(visible.encode()).hexdigest()
    assert len(manifest['removedNavigationRanges'])==1
    a,b = (manifest['removedNavigationRanges'][0][k] for k in ('startOffset','endOffset'))
    assert visible[:a]==text[:a] and visible[b:]==text[b:]
    assert [i for i,c in enumerate(text) if c=='\n']==[i for i,c in enumerate(visible) if c=='\n']
    assert all(label not in visible for label in ('操盘必读','股海导航','四大证券报'))
    assert '尚无收入确认，不代表已签署客户合同' in visible and '各持股45%' in visible
    assert navigation_view(visible,enabled=True)==(visible,None)


@pytest.mark.parametrize('change', ['not-publisher','generic-list','interleaved-prose','missing-rank'])
def test_does_not_strip_unproven_or_incomplete_lists(change):
    text=article()
    if change=='generic-list':text=text.replace('操盘必读','业务阶段').replace('股海导航','业务进展')
    if change=='interleaved-prose':text=text.replace('\n- 04/','\n这是必须保留的正文限定。\n- 04/')
    if change=='missing-rank':text=text.replace('- 06/','- 16/')
    assert navigation_view(text,enabled=change!='not-publisher')==(text,None)
    assert not sina_source({'publisher':'http://[invalid'})


def test_local_reads_keep_old_structural_ids_offsets_and_adjacent_qualifiers(monkeypatch):
    doc=document()
    with monkeypatch.context() as old:
        old.setattr(material,'navigation_view',lambda text,**kw:(text,None))
        before=material.document_outline(doc,max_characters=12000)
    after=material.document_outline(doc,max_characters=12000)
    shape=lambda x:[(r['locator'],r['startOffset'],r['endOffset']) for r in x['locators']]
    assert shape(before)==shape(after)
    assert after['sourceContentSha256']==before['sourceContentSha256']
    assert after['sourceViewProjection']['version']==NAVIGATION_VERSION
    first=material.read_locator(doc,after['locators'][0]['locator'],max_characters=12000)
    assert '尚无收入确认' in first['text'] and '操盘必读' not in json.dumps(first,ensure_ascii=False)
    assert '不构成业绩承诺' in json.dumps(first,ensure_ascii=False)
    assert first['textSha256']==sha256(first['text'].encode()).hexdigest()
    assert material.document_outline(doc,query='操盘必读',max_characters=12000)['matchingLocatorCount']==0
    assert '操盘必读' not in json.dumps(material.bounded_excerpt(doc,max_characters=12000),ensure_ascii=False)
    body=material.source_material_for_understand(doc,max_characters=12000)
    assert '操盘必读' not in body['text'] and '各持股45%' in body['text']
    assert doc.original_text==article()


def packet():
    doc=document();ref={'documentId':doc.document_id,'revision':1}
    card={**ref,'excerpt':article(),'provenance':{'publisher':'finance.sina.com.cn'}}
    return {'companyScope':{},'claims':[],'questions':[],'evidenceUpdates':[],'queryPaths':[],
        'fulltextRequests':[],'evidenceCards':[card],'newEvidenceRefs':[ref],'allowedEvidenceRefs':[ref],
        'fullTextDocuments':[{**card,'text':article()}]}


def test_final_packet_and_restored_reads_are_clean_and_projection_is_idempotent():
    p=packet();ref=p['allowedEvidenceRefs'][0]
    stale={'sourceRef':ref,'indexVersion':material.INDEX_VERSION,'text':'操盘必读：这是旧索引的局部预览'}
    request={'kind':'source','sourceRef':ref,'location':'paragraph:1','purpose':'核对收入阶段'}
    p['contextResults']=[{'request':request,'value':stale,'contentSha256':digest(stale)}]
    clean=project_packet('assess_evidence',p)
    assert '操盘必读' not in json.dumps(clean,ensure_ascii=False)
    assert clean['contextResults'][0]['value']['status']=='requires_current_read_protocol'
    assert project_packet('assess_evidence',clean)==clean
    assert clean['allowedEvidenceRefs']==[ref]
    assert clean['evidenceCards'][0]['sourceViewProjection']['version']==NAVIGATION_VERSION
    current=material.read_locator(document(),'paragraph:1',max_characters=12000)
    p['contextResults']=[{'request':request,'value':current,'contentSha256':digest(current)}]
    clean=project_packet('assess_evidence',p)
    assert clean['contextResults'][0]['value']==current
    assert project_packet('assess_evidence',clean)==clean
    assert p['evidenceCards'][0]['excerpt']==article()


@pytest.mark.parametrize('still_refused', [False, True])
def test_real_cli_recovery_cleans_only_navigation_and_never_loops_content_refusal(tmp_path,monkeypatch,still_refused):
    import sqlite3
    import httpx
    from datetime import timedelta
    from neckline.k10 import store,pipeline,research_context
    from neckline.k10.cli import recover_scan,frozen_scan_input_sha256
    from neckline.k10.worker import run_once
    from neckline.k10.verification import VerificationEvidenceBundle
    from neckline.llm import openai_compat
    from tests import test_v310_pipeline_e2e as e2e
    from tests.test_b61_output_recovery import api_for
    real_client=e2e._HTTPX_CLIENT
    wires=[]
    def client(**kw):
        transport=kw['transport']
        def intercept(request):
            body=json.loads(request.content)
            payload=json.loads(body['messages'][-1]['content'].split('<untrusted-k10-evidence>\n',1)[1].split('\n</untrusted-k10-evidence>',1)[0])
            if payload.get('action')=='assess_evidence':
                wires.append(request.content)
                if '操盘必读' in request.content.decode() or still_refused:
                    return httpx.Response(400,json={'error':{'code':'invalid_request_error','message':'Content Exists Risk'}})
            return transport.handle_request(request)
        return real_client(**{**kw,'transport':httpx.MockTransport(intercept)})
    monkeypatch.setattr(e2e,'_HTTPX_CLIENT',client)
    def fetch(gateway,**kw):
        path=kw['query_path'];gateway.search_paths.append(path.path_id)
        name='sina-'+path.path_id;text=article();stamp=e2e.RUN_AT.isoformat()
        published=(kw['cutoff_at']-timedelta(minutes=1)).isoformat();meta={'publisher':'finance.sina.com.cn'}
        store.append_document_version(document_id=name,source_key='fixture-sina',external_id=name,canonical_url=None,
            content_sha256=sha256(text.encode()).hexdigest(),published_at=published,published_precision='exact',
            fetched_at=stamp,original_text=None,excerpt=text,fetch_version='fixture',metadata=meta,created_at=stamp,
            db_path=tmp_path/'b39-e2e.sqlite')
        doc=DiscoveryDocument(name,1,published,stamp,None,text,meta)
        return VerificationEvidenceBundle('available',(doc,),(doc,),{'state':'available','requestState':'completed'})
    monkeypatch.setattr(e2e._Gateway,'fetch',fetch)
    with monkeypatch.context() as old:
        old.setattr(research_context,'navigation_view',lambda text,**kw:(text,None))
        old.setattr(material,'navigation_view',lambda text,**kw:(text,None))
        old.setattr(openai_compat,'_content_policy_refused',lambda response:False)
        db,tid,first,calls,_=e2e._run(tmp_path,monkeypatch,v2=True,cli_entry=True)
    assert first.status=='failed' and len(wires)==2
    original=store.task_execution_input(task_id=tid,db_path=db);scan=original['checkpoint']['scanId']
    frozen=frozen_scan_input_sha256(scan_id=scan,db_path=db)
    with sqlite3.connect(db) as c:
        completed=c.execute("select * from k10_execution_item_checkpoints where task_id=? and status='completed'",(tid,)).fetchall()
        paid=c.execute('select * from k10_external_attempts where task_id=?',(tid,)).fetchall()
        sources=c.execute('select * from k10_source_document_versions').fetchall()
    def recover():
        assert recover_scan(db_path=db,scan_id=scan,execution_config_id='b39-execution',execution_config_revision=1,
            confirmed_input_sha256=frozen,now=e2e.RUN_AT)==tid
        return run_once(db_path=db,task_id=tid,worker_id='b75',lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token='fixture-token',parquet_dir=tmp_path/'parquet'),clock=lambda:e2e.RUN_AT)
    result=recover()
    assert len(wires)>=3 and wires[0]==wires[1] and wires[2]!=wires[0]
    fixed=wires[2].decode()
    assert '操盘必读' not in fixed and '尚无收入确认' in fixed and '各持股45%' in fixed
    if still_refused:
        assert result.status=='failed' and len(wires)==3
        assert recover().status=='failed' and len(wires)==3
        with sqlite3.connect(db) as c:
            assert c.execute("select count(*) from k10_external_attempts where task_id=? and error_code='content_policy_refused'",(tid,)).fetchone()==(1,)
    else:
        assert result.status=='completed'
        report=api_for(db).get('/api/v1/k10/v2/reports/latest?window=evening').json()['report']
        assert report['status']=='completed' and report['availableAt']
    with sqlite3.connect(db) as c:
        assert all(r in c.execute('select * from k10_execution_item_checkpoints where task_id=?',(tid,)).fetchall() for r in completed)
        assert all(r in c.execute('select * from k10_external_attempts where task_id=?',(tid,)).fetchall() for r in paid)
        assert all(r in c.execute('select * from k10_source_document_versions').fetchall() for r in sources)
    assert frozen_scan_input_sha256(scan_id=scan,db_path=db)==frozen
    assert store.task_execution_input(task_id=tid,db_path=db)['checkpoint']['executionStartedAt']==original['checkpoint']['executionStartedAt']


def test_restored_partial_widget_is_invalidated_when_only_raw_body_has_navigation():
    from dataclasses import replace
    from types import SimpleNamespace
    from neckline.k10.research_runtime import _Investigation
    from neckline.k10.discovery import EvidenceRef
    doc=replace(document(),excerpt='注册资本5000万元，双方各持股45%。')
    ref={'documentId':doc.document_id,'revision':1};key=EvidenceRef(doc.document_id,1)
    obj=object.__new__(_Investigation);obj.allowed={key};obj.documents={key:doc};obj.event=SimpleNamespace(source_refs=(key,))
    obj.state={'stageResults':[],'claims':[{'claimId':'c1','sourceRef':ref,'text':'双方各持股45%',
        'location':'paragraph:1','kind':'factual_assertion','verificationStatus':'unverified'}]}
    p=packet();p['claims']=obj.state['claims'];p['evidenceCards']=obj._cards();p.pop('fullTextDocuments')
    assert p['evidenceCards'][0]['excerpt'] is None
    stale={'sourceRef':ref,'indexVersion':material.INDEX_VERSION,'text':'操盘必读：旧局部片段'}
    p['contextResults']=[{'request':{'kind':'source','sourceRef':ref,'location':'paragraph:1'},'value':stale,'contentSha256':digest(stale)}]
    result=project_packet('plan_gaps',p)
    assert result['contextResults'][0]['value']['status']=='requires_current_read_protocol'
    assert '操盘必读' not in json.dumps(result,ensure_ascii=False)
    p['evidenceCards']=[];p['fullTextDocuments']=[{**ref,'text':article(),'excerpt':'注册资本5000万元','publisher':'finance.sina.com.cn'}]
    result=project_packet('assess_evidence',p)
    assert result['contextResults'][0]['value']['status']=='requires_current_read_protocol'
