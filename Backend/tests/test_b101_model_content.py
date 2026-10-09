"""Raw wire content faults stay inside their dependency scope through real B92 tasks."""
import hashlib
import json
import socket
import sqlite3

import httpx
import pytest

from neckline.k10 import store
from neckline.k10.schema import SqliteWriteBusy
from tests import test_b92_report_loopback as current
from tests import test_b98_report_isolation as b98
from tests.test_b101_wire_boundaries import mixed_wire


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('B101 regressions forbid real network')
    monkeypatch.setattr(socket, 'create_connection', deny)
    monkeypatch.setattr(socket.socket, 'connect', deny)


@pytest.mark.parametrize('stage', ['titleBatch', 'titleReconcile', 'understand', 'investigation', 'prioritize'])
@pytest.mark.parametrize('fault', ['high_surrogate', 'low_surrogate_key', 'nan', 'infinity'])
def test_raw_json_fault_preserves_independent_delivery(stage, fault, tmp_path, monkeypatch):
    original = current._FlashReportTransport.respond
    victim = []
    injected = []
    def respond(self, request):
        packet = self._packet(request)
        found = ('titleReconcile' if 'inputCount' in packet else
                 'titleBatch' if 'items' in packet else
                 'understand' if 'documentId' in packet else
                 'investigation' if packet.get('action') == 'research_round' else
                 'prioritize' if 'companies' in packet and 'choices' in packet.get('output', {}) else None)
        response = original(self, request)
        unit = (packet.get('documentId') if found == 'understand' else
                packet['evidencePacket']['event']['canonicalKey'] if found == 'investigation' else found)
        if found != stage:
            return response
        if found == 'investigation' and unit != 'event-000':
            return response
        if found == 'understand' and self.document_numbers[unit] != 0:
            return response
        if not victim:
            victim.append(unit)
        if unit != victim[0]:
            return response
        value = json.loads(response.json()['choices'][0]['message']['content'])
        # B102 title rows isolate consumed fields before canonical validation;
        # unused root notes must not discard otherwise complete title results.
        # Other operations retain their existing content validation boundary.
        if fault == 'high_surrogate':
            value['wireNote'] = '\ud800'
        elif fault == 'low_surrogate_key':
            value['wireNote\udfff'] = 'bad key'
        else:
            value['wireNote'] = float('nan' if fault == 'nan' else 'inf')
        envelope = response.json()
        envelope['choices'][0]['message']['content'] = json.dumps(value, ensure_ascii=True)
        injected.append(found)
        return httpx.Response(200, content=json.dumps(envelope, ensure_ascii=True).encode('ascii'),
                              headers={'Content-Type': 'application/json'})
    monkeypatch.setattr(current._FlashReportTransport, 'respond', respond)
    db, task, report, materials, _, _ = b98.make_collected_case(
        tmp_path, monkeypatch, wire=mixed_wire('control'))
    assert injected and task[1] == 'completed' and report['status'] == 'partial'
    assert report['resultAvailableAt'] and report['delivery']['gaps']
    assert len(materials['items']) == (4 if stage in {'prioritize', 'titleBatch'} else 3)
    if stage == 'prioritize':
        assert report['delivery']['rankingScope'] == 'none' and not report['eveningCards']
    else:
        assert {'300004.SZ', '300005.SZ'} <= {c['companyCode'] for c in report['eveningCards']}
    # Check persisted dispositions, not just completed/HTTP 200.
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
        assert conn.execute("SELECT count(*) FROM k10_external_attempts WHERE state IN ('started','running','unknown')").fetchone()[0] == 0
        operation = 'investigation_research_round' if stage == 'investigation' else stage
        if stage == 'titleBatch':
            rows = conn.execute("SELECT status,result_json FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:titleBatch'", (task[0],)).fetchall()
            assert len(rows) == 1 and rows[0][0] == 'completed'
            assert set(json.loads(rows[0][1])) == {'items'}
            assert not any(g['stage'] == 'title_triage' for g in report['delivery']['gaps'])
        elif stage != 'prioritize':
            failed = conn.execute("SELECT safe_error_code FROM k10_execution_item_checkpoints WHERE task_id=? AND stage=? AND status='failed'", (task[0], 'model:' + operation)).fetchall()
            assert failed and all(code in {'model_result_not_json', 'model_json_repair_exhausted'} for (code,) in failed)
        else:
            # Ranking deliberately persists a usable empty selection plus its
            # gap, allowing already completed materials to be published.
            assert any(g['stage'] == 'prioritize' for g in report['delivery']['gaps'])
        assert conn.execute('SELECT count(*) FROM k10_model_response_receipts').fetchone()[0] > 0


def test_bad_sort_reply_resumes_without_rebilling(tmp_path, monkeypatch):
    original = current._FlashReportTransport.respond
    wire_hashes = []
    def respond(self, request):
        wire_hashes.append(hashlib.sha256(request.content).hexdigest())
        response = original(self, request)
        packet = self._packet(request)
        if 'companies' in packet and 'choices' in packet.get('output', {}):
            envelope = response.json()
            envelope['choices'][0]['message']['content'] = '{"choices":[],"note":"\\ud800"}'
            return httpx.Response(200, json=envelope)
        return response
    monkeypatch.setattr(current._FlashReportTransport, 'respond', respond)
    record = store.record_execution_checkpoint
    interrupted = []
    def checkpoint(**kwargs):
        if not interrupted and kwargs['stage'] == 'model:prioritize' and kwargs['status'] in {'completed', 'failed'}:
            interrupted.append(True)
            raise SqliteWriteBusy('after paid sort receipt, before local failure checkpoint')
        return record(**kwargs)
    monkeypatch.setattr(store, 'record_execution_checkpoint', checkpoint)
    db, task, report, materials, _, slices = b98.make_collected_case(
        tmp_path, monkeypatch, wire=mixed_wire('control'))
    assert interrupted and slices[0] == 'queued' and slices[-1] == 'completed'
    assert len(wire_hashes) == len(set(wire_hashes))
    assert task[1] == 'completed' and len(materials['items']) == 4
    assert report['delivery']['rankingScope'] == 'none' and report['delivery']['gaps']
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
        assert conn.execute("SELECT state,count(*) FROM k10_external_attempts WHERE stage='prioritize' GROUP BY state").fetchall() == [('succeeded', 1)]


@pytest.mark.parametrize('fault', ['valid_unicode', 'derived_surrogate', 'derived_key', 'derived_nan', 'derived_cycle'])
def test_derived_result_cannot_break_checkpoint_persistence(fault, tmp_path):
    # This is intentionally a narrow execution-ledger unit, not a producer
    # acceptance. The current producer path is covered by the tests above.
    from tests.test_k10_execution import _seed, _model_result, _model_input
    from neckline.llm.base import LLMResult
    path = tmp_path / 'derived.sqlite'
    _seed(path)
    valid = {'facts': ['中文、emoji 😀、补充平面字符 𠀀']}
    value = {'derived_surrogate': {'text': '\ud800'},
             'derived_key': {'key\udfff': 'text'},
             'derived_nan': {'number': float('nan')},
             'valid_unicode': valid}.get(fault)
    if fault == 'derived_cycle':
        value = {'loop': []}
        value['loop'].append(value)
    result = _model_result(task_id='task-1', operation='understand', item_key='doc@1',
        input_sha256=_model_input(fault), path=path,
        operation_call=lambda: LLMResult(ok=True, content='{"valid":true}'),
        validate=lambda _: value)
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as conn:
        rows = conn.execute("SELECT status,safe_error_code,result_json FROM k10_execution_item_checkpoints WHERE stage='model:understand'").fetchall()
    if fault == 'valid_unicode':
        assert result.status == 'completed' and result.value == valid
        assert json.loads(rows[0][2]) == valid
    else:
        assert result.status == 'failed'
        assert rows and all(row[0] == 'failed' and row[1] == 'model_result_not_json' and row[2] is None for row in rows)
