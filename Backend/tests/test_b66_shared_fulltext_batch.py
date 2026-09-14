import copy
import json
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from neckline.k10 import pipeline, research_runtime, store
from neckline.k10.verification import VerificationEvidenceBundle
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b60_pool_filtering import edit_responses


@pytest.mark.parametrize('interrupt', [False, True])
def test_real_worker_batches_admitted_requests_and_resumes_between_writes(tmp_path, monkeypatch, interrupt):
    fulltext_reads = []
    model_bodies = []
    assessments = [0]
    latest_packet = [{}]
    read_requested = [False]
    def edit(value):
        if value.get('action') == 'plan_gaps':
            value['questions'].append({**copy.deepcopy(value['questions'][0]), 'questionId': 'q-2',
                                       'question': '另一关键条件是否确认'})
        if value.get('action') == 'assess_evidence':
            assessments[0] += 1
            if assessments[0] == 1:
                value['fulltextRequests'] = [{'requestId': 'read-'+qid, 'questionId': qid,
                    'sourceRef': source_ref[0], 'reasonExcerptInsufficient': '需要正文条件',
                    'expectedJudgmentChange': '确认阶段', 'state': 'requested', 'admissionRef': None}
                    for qid in ('q-1', 'q-2')]
            elif latest_packet[0].get('fullTextDocuments') and not read_requested[0]:
                # B69 admits the fetched source first, then the model explicitly
                # reads its needed structural unit rather than receiving a body dump.
                read_requested[0] = True
                value['contextRequests'] = [{'kind': 'source', 'sourceRef': source_ref[0],
                    'location': 'paragraph:1', 'purpose': 'Check the announcement condition',
                    'questionId': 'q-1'}]
    edit_responses(monkeypatch, edit)
    source_ref = []
    def observe(request):
        message = json.loads(request.content)['messages'][-1]['content']
        payload = json.loads(message.split('<untrusted-k10-evidence>\n', 1)[1].split('\n</untrusted-k10-evidence>', 1)[0])
        if payload.get('action') == 'assess_evidence':
            packet = payload['evidencePacket']
            source_ref[:] = [{'documentId':'b66-external-source', 'revision':1}]
            latest_packet[0] = packet
            if assessments[0] == 0:
                assert source_ref[0] in packet['allowedEvidenceRefs']
            for row in packet.get('fullTextDocuments', []):
                assert 'text' not in row
                assert row['locators'][0]['locator'] == 'paragraph:1'
            for row in packet.get('contextResults', []):
                if row.get('value', {}).get('sourceRef') == source_ref[0]:
                    model_bodies.append(row['value']['text'])
                    assert source_ref[0] in packet['allowedEvidenceRefs']
    def fetch_fulltext(self, **kwargs):
        request, doc = kwargs['request'], kwargs['document']
        fulltext_reads.append(request.request_id)
        return VerificationEvidenceBundle('available', (doc,), (doc,), {
            'state': 'available', 'requestState': 'completed', 'operation': 'extract',
            'admissionState': 'fulfilled', 'admissionRef': request.source_ref})
    def fetch(self, **kwargs):
        self.search_paths.append(kwargs['query_path'].path_id)
        # An external Extract regression must use an actual additional source,
        # not relabel the already-read original TuShare article as a search hit.
        stamp = e2e.RUN_AT.isoformat()
        store.append_document_version(document_id='b66-external-source', source_key='tavily_verification',
            external_id='b66-external-source', canonical_url='https://example.test/announcement',
            content_sha256='e'*64, published_at=(e2e.RUN_AT-timedelta(hours=2)).isoformat(),
            published_precision='exact', fetched_at=stamp, original_text='Additional announcement body',
            excerpt='Additional announcement excerpt', fetch_version='fixture-search', metadata={},
            created_at=stamp, db_path=tmp_path/'b39-e2e.sqlite')
        rows = store.load_document_versions(refs=[{'documentId':'b66-external-source','revision':1}], db_path=tmp_path/'b39-e2e.sqlite')
        from neckline.k10.discovery import DiscoveryDocument
        docs = tuple(DiscoveryDocument(row['documentId'], row['revision'], row['publishedAt'], row['fetchedAt'],
            row['originalText'], row['excerpt'], row['metadata']) for row in rows)
        return VerificationEvidenceBundle('available', docs, docs, {'state':'available','requestState':'completed'})
    monkeypatch.setattr(e2e._Gateway, 'fetch', fetch)
    monkeypatch.setattr(e2e._Gateway, 'fetch_fulltext', fetch_fulltext)
    tick, tripped = [0.0], [False]
    monkeypatch.setattr(pipeline, 'time', SimpleNamespace(monotonic=lambda: tick[0]))
    original = research_runtime._Investigation._record
    def persist_then_interrupt(self, result, **kwargs):
        original(self, result, **kwargs)
        if interrupt and result.fulltext_requests and result.fulltext_requests[0].state == 'fulfilled' and not tripped[0]:
            tripped[0] = True
            tick[0] = 10000.0
    monkeypatch.setattr(research_runtime._Investigation, '_record', persist_then_interrupt)
    db, task_id, task, calls, gateway = e2e._run(tmp_path, monkeypatch, v2=True, request_observer=observe)
    if interrupt:
        assert task.status == 'queued' and fulltext_reads == ['read-q-1']
        tick[0] = 0.0
        with sqlite3.connect(db) as conn:
            due = datetime.fromisoformat(conn.execute('SELECT not_before_at FROM k10_task_retry_schedules WHERE task_id=?', (task_id,)).fetchone()[0])
        task = run_once(db_path=db, task_id=task_id, worker_id='b66-resume', lease_for=timedelta(minutes=5),
            handlers=pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet'),
            clock=lambda: due+timedelta(seconds=1))
    else:
        assert calls.count('research:assess_evidence') == 3  # excerpt, structural directory, then the one requested paragraph
    assert task.status == 'completed' and fulltext_reads == ['read-q-1', 'read-q-2']
    assert len(model_bodies) == 1 and model_bodies[0].strip()  # shared source body read once, including across a pause
    assert calls.count('understand') == 1 and calls.count('titleBatch') == 1
    assert read_report(db_path=db)['eveningCards'] and not read_report(db_path=db)['incompleteReviews']
