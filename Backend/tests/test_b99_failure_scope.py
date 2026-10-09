"""B92 CLI/worker/API proof: a settled malformed HTTP response is content, not frozen corruption."""
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from tests import test_b92_report_loopback as current
from tests import test_b98_report_isolation as b98
from tests import v340_acceptance_fixture as base
from neckline.k10 import store

def emit(name, value):
    import os
    root = os.environ.get("NK_B99_OUTPUT_DIR")
    if root:
        folder = Path(root)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

@pytest.mark.parametrize('fault_stage', ['control', 'titleBatch', 'titleReconcile', 'prioritize'])
def test_envelope(fault_stage, tmp_path, monkeypatch):
    original = current._FlashReportTransport.respond
    injected = []
    def respond(self, request):
        packet = self._packet(request)
        stage = ('titleReconcile' if 'inputCount' in packet else
                 'titleBatch' if 'items' in packet and 'inputCount' not in packet else
                 'prioritize' if 'companies' in packet and 'choices' in packet.get('output', {}) else None)
        response = original(self, request)
        if stage == fault_stage:
            injected.append(stage)
            return httpx.Response(200, json={'choices': [], 'usage': {'prompt_tokens': 17, 'completion_tokens': 1, 'total_tokens': 18}})
        return response
    monkeypatch.setattr(current._FlashReportTransport, 'respond', respond)
    helper_failure = None
    try:
        b98.make_collected_case(tmp_path, monkeypatch, expected_status='completed')
    except (AssertionError, TypeError, KeyError) as exc:
        helper_failure = type(exc).__name__
    db = tmp_path / 'b92-flash.sqlite'
    assert db.exists(), helper_failure
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
        task = conn.execute("SELECT task_id,status,stage,checkpoint_json FROM k10_tasks WHERE kind='evening_scan'").fetchone()
        checkpoints = conn.execute("SELECT stage,status,safe_error_code,count(*) FROM k10_execution_item_checkpoints WHERE task_id=? GROUP BY 1,2,3 ORDER BY 1,2", (task[0],)).fetchall()
        attempts = conn.execute("SELECT state,count(*) FROM k10_external_attempts GROUP BY state").fetchall()
        receipt_count = conn.execute("SELECT count(*) FROM k10_model_response_receipts").fetchone()[0]
        report_count = conn.execute("SELECT count(*) FROM k10_v2_report_runs").fetchone()[0]
        research_counts = conn.execute('SELECT execution_status,research_status,count(*) FROM k10_research_snapshot_revisions a WHERE a.revision=(SELECT max(b.revision) FROM k10_research_snapshot_revisions b WHERE b.snapshot_id=a.snapshot_id) GROUP BY 1,2').fetchall()
    with base.actual_api(db, config_id='b92-isolated-run', config_revision=1,
                         execution_id='b92-isolated-execution', execution_revision=1) as client:
        api = client.get('/api/v1/k10/v2/reports/latest?window=evening')
        envelope = api.json()
        report = envelope.get('report')
        materials = client.get(f"/api/v1/k10/v2/reports/{report['reportId']}/materials").json() if report else None
    scan = store.get_scan(scan_id=json.loads(task[3]).get('scanId'), db_path=db)
    result = {'case': fault_stage, 'injectedCount': len(injected), 'taskStatus': task[1], 'taskStage': task[2],
              'helperAssertionFailure': helper_failure, 'checkpointSafeErrorCode': json.loads(task[3]).get('safeErrorCode'),
              'scanStatus': scan.get('status') if scan else None, 'checkpoints': checkpoints,
              'externalAttemptStates': attempts, 'receiptCount': receipt_count, 'researchCounts': research_counts,
              'reportCount': report_count, 'apiStatus': api.status_code, 'reportExists': report is not None,
              'reportStatus': report.get('status') if report else None,
              'cardCount': len(report.get('eveningCards', [])) if report else None,
              'materialCount': len(materials.get('items', [])) if materials else None,
              'gaps': report.get('delivery', {}).get('gaps', []) if report else None}
    emit(f'envelope-{fault_stage}.json', result)
    if fault_stage == 'control':
        assert task[1] == 'completed' and report and materials['items'] and report['eveningCards']
    else:
        assert injected and task[1] == 'completed'
        if fault_stage != 'prioritize':
            assert any(row[2] == 'response_structure_invalid' for row in checkpoints)
        assert all(state not in {'started', 'unknown'} for state, _ in attempts)
        assert report['status'] == 'partial'
        public_code = 'title_reconcile_partial' if fault_stage == 'titleReconcile' else 'response_structure_invalid'
        assert any(g['reasonCode'] == public_code for g in report['delivery']['gaps'])
        assert len(materials['items']) == (2 if fault_stage == 'prioritize' else 1)
        assert receipt_count > 0
        if fault_stage == 'prioritize':
            assert report['delivery']['rankingScope'] == 'none' and not report['eveningCards']
            assert report['availableAt'] is None and report['resultAvailableAt']


"""Narrow real B92 evidence for malformed discovery envelope and body references."""
import json
import sqlite3
from pathlib import Path
from threading import current_thread

import httpx
import pytest

from tests import test_b92_report_loopback as current
from tests import test_b98_report_isolation as b98
from tests import test_b98_research_provider_scope as scope



@pytest.mark.parametrize('fault', [False, True])
def test_morning_envelope(fault, tmp_path, monkeypatch):
    original = current._FlashReportTransport.respond
    injected = []
    def respond(self, request):
        message = json.loads(request.content)['messages'][-1]['content']
        packet = self._packet(request) if '<untrusted-k10-evidence>' in message else {}
        if (isinstance(packet.get('documentId'), str)
                and packet['documentId'] in type(self).morning_document_ids):
            ref = {'documentId': packet['documentId'], 'revision': packet['revision']}
            return self._ok({'events': [{'canonicalKey': 'event-002', 'stageKey': 'morning-new',
                'eventState': 'rumor', 'headline': '独立公司的隔夜新事实', 'eventKind': 'rumor', 'facts': {},
                'sourceRefs': [ref], 'claims': [{'text': '独立公司项目送样', 'kind': 'rumor', 'novelty': 'new_fact',
                    'speaker': '供应商', 'subject': '项目', 'object': '样品', 'action': '送样',
                    'stageOrCondition': '待确认', 'timeText': '隔夜', 'verificationStatus': 'unverified',
                    'decisionImpact': '影响独立公司判断', 'sourceRef': ref, 'location': 'paragraph:1'}]}],
                'needsFullText': False})
        if ('k10-morning-discovery' in current_thread().name and 'companies' in packet
                and 'choices' in packet.get('output', {}) and fault):
            injected.append('morning-prioritize')
            return httpx.Response(200, json={'choices': [], 'usage': {'prompt_tokens': 17, 'completion_tokens': 1, 'total_tokens': 18}})
        return original(self, request)
    monkeypatch.setattr(current._FlashReportTransport, 'respond', respond)
    helper_failure = None
    try:
        current.generate_b92_loopback(tmp_path, monkeypatch, material_contrary=True)
    except AssertionError:
        helper_failure = 'normal-fixture-assertion'
    db, task, report, materials = b98.read_case(tmp_path, 'morning')
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
        work_items = conn.execute('SELECT status,count(*) FROM k10_morning_review_work_items GROUP BY status').fetchall()
        checkpoints = conn.execute('SELECT stage,status,safe_error_code,count(*) FROM k10_execution_item_checkpoints WHERE task_id=? GROUP BY 1,2,3', (task[0],)).fetchall()
        attempts = conn.execute('SELECT state,count(*) FROM k10_external_attempts WHERE task_id=? GROUP BY state', (task[0],)).fetchall()
    result = {'case': 'morning-envelope-fault' if fault else 'morning-control', 'taskStatus': task[1],
        'injectedCount': len(injected), 'helperAssertion': helper_failure, 'savedReviewStates': work_items,
        'apiReviewStates': [row['status'] for row in (report.get('morningReview') or {}).get('items', [])],
        'reportStatus': report['status'], 'addedCardCount': len(report['addedCards']),
        'materialCount': len(materials['items']), 'lifecycleUpdateCount': len(report['lifecycleUpdates']),
        'discovery': report.get('discovery'), 'checkpoints': checkpoints, 'externalAttemptStates': attempts,
        'gaps': report.get('delivery', {}).get('gaps', [])}
    emit(result['case'] + '.json', result)
    assert any(state == 'completed' for state, _ in work_items)
    assert all(state not in {'started', 'unknown'} for state, _ in attempts)
    if fault:
        assert injected and task[1] == 'completed' and report['status'] == 'partial'
        assert report['morningReview']['items'] and report['lifecycleUpdates']
        assert all(row['status'] == 'completed' for row in report['morningReview']['items'])
        assert materials['items'] and report['delivery']['rankingScope'] == 'none'
    else:
        assert task[1] == 'completed' and report['addedCards'] and report['lifecycleUpdates']
        assert all(row['status'] == 'completed' for row in report['morningReview']['items'])

@pytest.mark.parametrize('mode', ['missing_claim_source', 'wrong_claim_source'])
def test_body_reference(mode, tmp_path, monkeypatch):
    original = current._FlashReportTransport.respond
    victim = []
    injected = []
    def respond(self, request):
        packet = self._packet(request)
        response = original(self, request)
        if isinstance(packet.get('documentId'), str):
            if not victim:
                victim.append(packet['documentId'])
            if packet['documentId'] == victim[0]:
                value = json.loads(response.json()['choices'][0]['message']['content'])
                for event in value.get('events', []):
                    for claim in event.get('claims', []):
                        if mode == 'missing_claim_source':
                            claim.pop('sourceRef', None)
                        else:
                            claim['sourceRef'] = {'documentId': 'not-visible-to-this-body', 'revision': 1}
                injected.append(packet['documentId'])
                return self._ok(value)
        return response
    monkeypatch.setattr(current._FlashReportTransport, 'respond', respond)
    db, task, report, materials, transport, slices = b98.make_collected_case(
        tmp_path, monkeypatch, wire=scope._three_flash_wire())
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
        checkpoints = conn.execute('SELECT stage,status,safe_error_code,count(*) FROM k10_execution_item_checkpoints WHERE task_id=? GROUP BY 1,2,3', (task[0],)).fetchall()
        attempts = conn.execute('SELECT state,count(*) FROM k10_external_attempts GROUP BY state').fetchall()
    result = {'case': mode, 'taskStatus': task[1], 'reportStatus': report['status'],
              'injectedCount': len(injected), 'cardCount': len(report['eveningCards']),
              'materialCount': len(materials['items']), 'checkpoints': checkpoints,
              'slices': slices, 'externalAttemptStates': attempts, 'gaps': report['delivery']['gaps']}
    emit(mode + '.json', result)
    assert injected and task[1] == 'completed' and report['eveningCards'] and materials['items']
    assert all(state not in {'started', 'unknown'} for state, _ in attempts)
    if mode == 'wrong_claim_source':
        assert any(gap['stage'] == 'understand' for gap in report['delivery']['gaps'])
    else:
        assert not any(gap['stage'] == 'understand' for gap in report['delivery']['gaps'])


"""Main-session follow-up of independent review's interrupted receipt probe."""
import json
import socket
import sqlite3
from pathlib import Path

import httpx
import pytest
from neckline.k10 import store
from neckline.k10.schema import SqliteWriteBusy
from tests import test_b92_report_loopback as current
from tests import test_b98_report_isolation as b98


@pytest.mark.parametrize('interrupt', [False, True])
def test_truncated_receipt_resume(tmp_path, monkeypatch, interrupt):
    def deny(*args, **kwargs):
        raise AssertionError('No external network in review')
    monkeypatch.setattr(socket, 'create_connection', deny)
    monkeypatch.setattr(socket, 'getaddrinfo', deny)
    original = current._FlashReportTransport.respond
    wires = []
    def respond(self, request):
        packet = self._packet(request)
        if 'inputCount' in packet:
            wires.append('titleReconcile')
            return httpx.Response(200, json={'choices': [{'message': {'content': '{'}, 'finish_reason': 'length'}],
                'usage': {'prompt_tokens': 17, 'completion_tokens': 1, 'total_tokens': 18}})
        return original(self, request)
    monkeypatch.setattr(current._FlashReportTransport, 'respond', respond)
    record = store.record_execution_checkpoint
    interrupted = []
    def checkpoint(**kwargs):
        if interrupt and not interrupted and kwargs.get('stage') == 'model:titleReconcile':
            interrupted.append(True)
            raise SqliteWriteBusy('Receipt durable before derived result')
        return record(**kwargs)
    monkeypatch.setattr(store, 'record_execution_checkpoint', checkpoint)
    fixture_assertion = None
    try:
        b98.make_collected_case(tmp_path, monkeypatch)
    except AssertionError as exc:
        fixture_assertion = str(exc)
    db, task, report, materials = b98.read_case(tmp_path)
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
        rows = conn.execute('SELECT stage,status,safe_error_code FROM k10_execution_item_checkpoints WHERE task_id=?', (task[0],)).fetchall()
        attempts = conn.execute('SELECT state,count(*) FROM k10_external_attempts WHERE task_id=? GROUP BY state', (task[0],)).fetchall()
    result = {'interrupt': interrupt, 'interrupted': bool(interrupted), 'taskStatus': task[1],
        'reportStatus': report['status'], 'materials': len(materials['items']), 'wireCount': len(wires),
        'checkpoints': rows, 'attempts': attempts, 'fixtureAssertion': fixture_assertion}
    emit(f'truncated-resume-{interrupt}.json', result)
    assert wires and (not interrupt or interrupted)
    assert all(state not in {'started', 'unknown'} for state, _ in attempts)
    if interrupt:
        assert task[1] == 'completed' and report['status'] == 'partial'
        assert len(wires) == 1 and len(materials['items']) == 1
        assert any(row[2] == 'titlereconcile_json_output_truncated' for row in rows)
    else:
        assert task[1] == 'completed' and report['status'] == 'partial'
        assert len(wires) == 2 and len(materials['items']) == 1


@pytest.mark.parametrize("code", ["model_cache_corrupt", "provider_response_receipt_invalid", "external_attempt_unknown", "research_snapshot_missing", "lease_lost", "sqlite_busy", "title_inputs_changed"])
def test_protected_errors_are_not_content_gaps(code):
    from neckline.k10.failure_scope import local_model_failure_code
    from neckline.k10.title_runtime import _local_failure_code
    from neckline.k10.pipeline import PipelineError
    error = PipelineError("protected", code=code)
    assert local_model_failure_code(error) is None
    assert _local_failure_code(error) is None
