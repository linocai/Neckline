"""Original-article rereads stay local, including a durable slice interruption."""
import json
import sqlite3
from datetime import timedelta

import httpx
import pytest

from neckline.k10 import pipeline, store
from neckline.k10.research_runtime import _Investigation
from neckline.k10.verification_checkpoints import VerificationCheckpointError
from neckline.k10.worker import run_once
from tests.test_v310_pipeline_e2e import _Gateway, _run, RUN_AT


@pytest.mark.parametrize('interrupt', [False, True])
def test_cli_worker_rereads_original_without_external_extract(tmp_path, monkeypatch, interrupt):
    original_transport = httpx.MockTransport
    requested, reads, tools = [], [], []

    def forbidden_extract(self, **kwargs):
        # Production Tavily correctly rejects the TuShare source namespace.
        # The coordinator must route this source to its frozen local body.
        tools.append(kwargs['document'].evidence_ref)
        raise VerificationCheckpointError('research_fulltext_source_unknown')

    monkeypatch.setattr(_Gateway, 'fetch_fulltext', forbidden_extract)
    def search_original(self, **kwargs):
        from neckline.k10.discovery import DiscoveryDocument
        from neckline.k10.verification import VerificationEvidenceBundle
        refs = [{'documentId': r.document_id, 'revision': r.revision} for r in kwargs['event'].source_refs]
        rows = store.load_document_versions(refs=refs, db_path=tmp_path/'b39-e2e.sqlite')
        docs = tuple(DiscoveryDocument(r['documentId'], r['revision'], r.get('publishedAt'), r['fetchedAt'],
            r.get('originalText'), r.get('excerpt'), r.get('metadata') or {}) for r in rows)
        return VerificationEvidenceBundle('available', docs, docs, {'state':'available', 'requestState':'completed'})
    monkeypatch.setattr(_Gateway, 'fetch', search_original)

    def transport(handler):
        def respond(request):
            message = json.loads(request.content)['messages'][-1]['content']
            packet = json.loads(message.split('<untrusted-k10-evidence>\n', 1)[1].split('\n</untrusted-k10-evidence>', 1)[0])
            response = handler(request)
            if packet.get('action') == 'assess_evidence':
                evidence = packet['evidencePacket']
                if evidence.get('fullTextDocuments'):
                    reads.extend(evidence['fullTextDocuments'])
                if not requested:
                    ref = evidence['allowedEvidenceRefs'][0]
                    requested.append(ref)
                    body = response.json()
                    value = json.loads(body['choices'][0]['message']['content'])
                    value['fulltextRequests'] = [{'requestId': 'read-original', 'questionId': 'q-1',
                        'sourceRef': ref, 'reasonExcerptInsufficient': '摘要为空，核对原文限定语。',
                        'expectedJudgmentChange': '核对是否只是转述，不把同文回读当作独立确认。',
                        'state': 'requested', 'admissionRef': None}]
                    body['choices'][0]['message']['content'] = json.dumps(value)
                    return httpx.Response(200, json=body)
            return response
        return original_transport(respond)

    monkeypatch.setattr(httpx, 'MockTransport', transport)
    original_tool = _Investigation._tool
    yielded = []
    def save_then_yield(runtime, bundle, **kwargs):
        original_tool(runtime, bundle, **kwargs)
        if interrupt and bundle.coverage.get('provider') == 'local' and not yielded:
            yielded.append(True)
            raise pipeline.DiscoverySliceYield()
    monkeypatch.setattr(_Investigation, '_tool', save_then_yield)
    db, task_id, task, calls, _ = _run(tmp_path, monkeypatch, v2=True)
    if interrupt:
        assert yielded and task.status == 'queued'
        with sqlite3.connect(db) as conn:
            paid = conn.execute('select attempt_id from k10_external_attempts where task_id=?', (task_id,)).fetchall()
        task = run_once(db_path=db, task_id=task_id, worker_id='b67-resume', lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet'),
            clock=lambda: RUN_AT + timedelta(seconds=2))
        with sqlite3.connect(db) as conn:
            assert all(row in conn.execute('select attempt_id from k10_external_attempts where task_id=?', (task_id,)).fetchall() for row in paid)
    assert task.status == 'completed', task.status
    assert tools == []
    assert len(reads) == 1 and reads[0]['text'] == '供应商称创业板公司可能进入新项目送样阶段。'
    assert reads[0]['materialOrigin'] == 'original_article' and reads[0]['independentVerification'] is False
    assert {k: reads[0][k] for k in ('documentId', 'revision')} == requested[0]
    assert calls.count('understand') == 1 and calls.count('titleBatch') == 1
    from tests.test_b66_body_resilience import api_for
    report = api_for(db).get('/api/v1/k10/v2/reports/latest').json()['report']
    assert report['status'] == 'completed' and report['eveningCards']
    # Rereading the same article does not promote its rumor to independent proof.
    with sqlite3.connect(db) as conn:
        outcomes = [json.loads(raw)['conclusion']['runtimeEvidence'] for raw, in conn.execute(
            "select result_json from k10_research_stage_results where action='assess_evidence'")
            if 'runtimeEvidence' in (json.loads(raw).get('conclusion') or {})]
    local = [o for o in outcomes if o['coverage'].get('provider') == 'local']
    assert len(local) == 1 and local[0]['coverage']['independentVerification'] is False
    assert local[0]['documentRefs'] == [requested[0]]


@pytest.mark.parametrize('original', [True, False])
def test_missing_original_is_a_gap_and_external_source_keeps_gateway(original):
    from types import SimpleNamespace
    from neckline.k10.discovery import DiscoveryDocument, EvidenceRef
    from neckline.k10.verification import VerificationEvidenceBundle
    question = {'questionId':'q', 'question':'Check attribution', 'claimIds':['c'], 'companyCodes':['300002.SZ'],
        'knownEvidence':[], 'missingEvidence':['Original wording'], 'supportCondition':'Original agrees',
        'refuteCondition':'Original disagrees', 'decisionImpact':'Attribution', 'state':'open', 'resumeCondition':None}
    runtime = object.__new__(_Investigation)
    ref = EvidenceRef('original' if original else 'search-result', 1)
    runtime.event = SimpleNamespace(source_refs=(EvidenceRef('original', 1),))
    doc = DiscoveryDocument(ref.document_id, 1, RUN_AT.isoformat(), RUN_AT.isoformat(), None, 'Only an excerpt', {})
    runtime.documents, runtime.allowed = {ref: doc}, {ref}
    runtime.cutoff, runtime.cutoff_inclusive = RUN_AT, False
    runtime.state = {'questions':[question], 'fulltextRequests':[{
        'requestId':'requested', 'questionId':question['questionId'],
        'sourceRef':{'documentId':ref.document_id, 'revision':1}, 'reasonExcerptInsufficient':'Need exact text',
        'expectedJudgmentChange':'Check attribution', 'state':'requested', 'admissionRef':None}]}
    runtime._external_guard = lambda: None
    calls, bundles = [], []
    def external(**kwargs):
        calls.append(kwargs)
        return VerificationEvidenceBundle('pending', (), (), {'requestState':'completed'})
    runtime.verifier = SimpleNamespace(fetch_fulltext=external)
    def record(bundle, **kwargs):
        bundles.append(bundle)
        runtime.state['fulltextRequests'][0]['state'] = 'rejected'
    runtime._tool = record
    assert runtime._fulltexts() is True
    assert len(calls) == (0 if original else 1)
    assert bundles[0].documents == ()
    if original:
        assert bundles[0].coverage['reason'] == 'original_fulltext_unavailable'
        assert bundles[0].coverage['independentVerification'] is False
