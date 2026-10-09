"""B101: real B92 collection/producer/worker/API, deterministic raw wire faults."""
import json
import sqlite3
import socket
import traceback
from pathlib import Path

import httpx
import pytest

from neckline.k10 import pipeline, collection_gateway, store
from neckline.k10.schema import SqliteWriteBusy
from tests import test_b92_report_loopback as current
from tests import test_b98_report_isolation as b98
from tests import v340_acceptance_fixture as base
from tests.test_b100_source_isolation import summarize

from tests.test_b100_source_isolation import emit

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('B101 regressions forbid network')
    monkeypatch.setattr(socket, 'create_connection', denied)


def mixed_wire(case):
    wire = current._news_wire(article=True)
    original = wire.reply
    def reply(request, body):
        response = original(request, body)
        name = body['params']['name']
        value = response.json()
        data = value['result']['structuredContent']['data']
        if name == 'list_flash':
            data['items'] = [{'id': f'flash-{n:03d}',
                'url': f'https://flash.jin10.com/detail/flash-{n:03d}',
                'time':'2026-09-26T07:00:00+08:00','title':None,
                'content':f'独立原件 {n}：样品已送达测试客户，订单尚未确认。'} for n in range(3)]
        if name in {'list_news','get_news'}:
            rows=data.get('items',[data])
            for row in rows:
                row['title']='离线验收标题 0000：公司新增经营事实'
                if name=='get_news' and case.startswith('article_min_time'):
                    row['time']='0001-01-01T00:00:00+08:00'
                if name=='get_news' and case=='article_max_time':
                    row['time']='9999-12-31T23:59:59-08:00'
        return httpx.Response(200, json=value)
    wire.reply=reply
    return wire


@pytest.mark.parametrize('case',['control','article_min_time','article_max_time','title_reason_surrogate','title_matter_surrogate','article_min_time_after_receipt','title_reason_surrogate_after_receipt'])
def test_source_boundary_pair(case,tmp_path,monkeypatch):
    wire=mixed_wire(case)
    interrupted=[]
    if case=='title_reason_surrogate_after_receipt':
        original_checkpoint=store.record_execution_checkpoint
        def record(**kwargs):
            if not interrupted and kwargs['stage']=='model:titleBatch' and kwargs['status'] in {'completed', 'failed'}:
                interrupted.append('after exact model receipt, before derived checkpoint')
                raise SqliteWriteBusy('controlled interruption before title derivative write')
            return original_checkpoint(**kwargs)
        monkeypatch.setattr(store,'record_execution_checkpoint',record)
    if case=='article_min_time_after_receipt':
        original_settle=collection_gateway._settle_received_reply
        def settle(**kwargs):
            result=original_settle(**kwargs)
            data=kwargs['raw'].get('structuredContent',{}).get('data',{})
            if data.get('id')=='news-002' and not interrupted:
                interrupted.append('after exact get_news receipt, before normalization')
                raise SqliteWriteBusy('controlled interruption after get_news receipt')
            return result
        monkeypatch.setattr(collection_gateway,'_settle_received_reply',settle)
    original_respond=current._FlashReportTransport.respond
    injected=[]
    model_wire_hashes=[]
    import hashlib
    def respond(self,request):
        result=original_respond(self,request)
        packet=self._packet(request)
        model_wire_hashes.append(hashlib.sha256(request.content).hexdigest())
        if case.startswith('title_') and 'items' in packet and 'inputCount' not in packet:
            value=json.loads(result.json()['choices'][0]['message']['content'])
            key='reason' if case.startswith('title_reason_surrogate') else 'matterKey'
            value['items'][0][key]='外部坏字符串\ud800'
            injected.append({'stage':'titleBatch','field':key,'value':value['items'][0][key]})
            # The HTTP bytes are valid ASCII JSON. The inner JSON independently
            # contains a \ud800 escape; no in-memory canonical data is edited.
            envelope=result.json()
            envelope['choices'][0]['message']['content']=json.dumps(value,ensure_ascii=True)
            return httpx.Response(200, content=json.dumps(envelope,ensure_ascii=True).encode('ascii'),
                                  headers={'Content-Type':'application/json'})
        return result
    monkeypatch.setattr(current._FlashReportTransport,'respond',respond)
    original_handler=pipeline.production_scan_handler
    handlers=[]
    def handler(*args,**kwargs):
        try:
            result=original_handler(*args,**kwargs)
        except Exception as exc:
            handlers.append({'raised':type(exc).__name__,'safeMessage':str(exc),
                'frames':[[i.filename,i.lineno,i.name] for i in traceback.extract_tb(exc.__traceback__)]})
            raise
        handlers.append({'status':result.status,'stage':result.stage,'error':result.error,
            'checkpoint':{k:v for k,v in result.checkpoint.items() if k in ['scanId','safeErrorCode']}})
        return result
    monkeypatch.setattr(pipeline,'production_scan_handler',handler)
    expected='completed'
    try:
        db,task,report,materials,transport,slices=b98.make_collected_case(tmp_path,monkeypatch,wire=wire,expected_status=expected)
    except AssertionError as exc:
        # Capture actual outcomes even when the probe's expected hypothesis was
        # wrong. That assertion is not a product finding.
        db,task,report,materials=b98.read_case(tmp_path)
        transport=None
        slices=[]
        probe_expectation_error=str(exc)
    else:
        probe_expectation_error=None
    if transport:
        result=summarize(db,task,report,materials,wire,transport,slices,handlers)
    else:
        result={'reportTaskId':task[0],'taskStatus':task[1],'reportStatus':report['status'],
                'cardCount':len(report.get('eveningCards',[])), 'materialCount':len(materials.get('items',[])),
                'publicGaps':report.get('delivery',{}).get('gaps',[]),'handlerResults':handlers}
    with sqlite3.connect(f'file:{db}?mode=ro',uri=True) as conn:
        rows=conn.execute('SELECT stage,state,error_code,count(*) FROM k10_external_attempts GROUP BY 1,2,3').fetchall()
        result.update(case=case,injected=injected,interrupted=interrupted,probeExpectationError=probe_expectation_error,
            externalAttempts=rows,wireHashes=model_wire_hashes,
            independentCardRefs={c['companyCode']:c['sourceRefs'] for c in report.get('eveningCards',[]) if c['companyCode'] in {'300004.SZ','300005.SZ'}},
            cardCompanies=[c['companyCode'] for c in report.get('eveningCards',[])],
            cardEventIds=sorted({cat['eventId'] for c in report.get('eveningCards',[]) for cat in c['catalysts']}),
            privateReceipts=conn.execute('SELECT count(*) FROM k10_model_response_receipts').fetchone()[0],
            collectionCheckpoints=conn.execute("SELECT status,checkpoint_json FROM k10_tasks WHERE kind='collect_news'").fetchall(),
            sourceDocumentCount=conn.execute('SELECT source_key,count(*) FROM k10_source_documents GROUP BY 1').fetchall())
    emit(f'b101-wire-{case}.json', result)
    assert probe_expectation_error is None
    assert result['sourceDocumentCount'] == [('jin10-flash',3),('jin10-news',1)]
    assert all(state not in {'started','unknown','running'} for _,state,_,_ in rows)
    if case=='control':
        assert task[1]=='completed' and report['status']=='partial'
        assert set(result['cardCompanies'])=={'300002.SZ','300004.SZ','300005.SZ'}
        assert len(report['eveningCards']) == 3 and len(materials['items']) == 4
        independent_source_ids={row[1] for row in result['sourceVersions'] if row[0]=='jin10-flash'}
        assert set(result['independentCardRefs'])=={'300004.SZ','300005.SZ'}
        assert all(ref['documentId'] in independent_source_ids for refs in result['independentCardRefs'].values() for ref in refs)

    else:
        assert probe_expectation_error is None
        assert task[1]=='completed' and report['status']=='partial'
        assert len(materials['items']) == 3
        assert {'300004.SZ','300005.SZ'} <= set(result['cardCompanies'])
        assert result['publicGaps']
        assert not any(h.get('raised') in {'UnicodeEncodeError','OverflowError'} for h in handlers)

    if case.endswith('after_receipt'):
        assert interrupted and len(interrupted)==1
        assert result['workerResults'][0]=='queued' and result['workerResults'][-1]=='completed'
        assert len(model_wire_hashes)==len(set(model_wire_hashes))
        if case.startswith('title_'):
            assert [name for name,_ in result['modelCalls']].count('titleBatch')==1
            assert next(row[3] for row in rows if row[0]=='titleBatch') == 1
        else:
            assert result['toolCalls'].count('get_news')==1
