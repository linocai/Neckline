"""B100: validate model references before success; retain independent research."""
import json
from hashlib import sha256
import sqlite3
from pathlib import Path

import pytest

from neckline.k10 import pipeline, research_runtime
from neckline.k10.schema import SqliteWriteBusy
from tests import test_b92_report_loopback as current
from tests import test_b98_report_isolation as b98
from tests.test_b98_research_provider_scope import _three_flash_wire

from tests.test_b100_source_isolation import emit, no_network


@pytest.mark.parametrize('mode', ['control', 'foreign_refs', 'foreign_refs_after_round_interruption'])
def test_research_reference_abort(mode, tmp_path, monkeypatch):
    original = current._FlashReportTransport.respond
    injected = []
    wires = []
    wire_hashes = []
    interrupted = []
    failures = []
    outcomes = []
    final_errors = []
    def respond(self, request):
        packet = self._packet(request)
        response = original(self, request)
        if packet.get('action') == 'research_round':
            event = packet['evidencePacket']['event']['canonicalKey']
            wires.append(event)
            wire_hashes.append((event, sha256(request.content).hexdigest()))
            if mode != 'control' and event == 'event-000':
                injected.append(event)
                value = json.loads(response.json()['choices'][0]['message']['content'])
                value['companyAssessments'][0]['sourceRefs'] = [{'documentId': 'foreign-to-packet', 'revision': 1}]
                return self._ok(value)
        return response
    monkeypatch.setattr(current._FlashReportTransport, 'respond', respond)
    mark = research_runtime._Investigation._b78_mark_failed
    def mark_failed(self, **kwargs):
        try:
            value = mark(self, **kwargs)
            if mode == 'foreign_refs_after_round_interruption' and not interrupted and self.event.canonical_key == 'event-000':
                interrupted.append('failed_round_durable')
                raise SqliteWriteBusy('deterministic interruption after failed round is durable')
            return value
        except SqliteWriteBusy:
            raise
        except Exception as exc:
            failures.append({'canonicalKey': self.event.canonical_key,
                'triggerCode': kwargs['safe_error_code'], 'latestRevision': self.snapshot.revision,
                'executionStatus': self.snapshot.execution_status, 'researchStatus': self.snapshot.research_status,
                'exception': type(exc).__name__, 'message': str(exc)})
            raise
    monkeypatch.setattr(research_runtime._Investigation, '_b78_mark_failed', mark_failed)
    outcome = research_runtime._Investigation._outcome
    def outcome_checked(self, *args, **kwargs):
        try:
            return outcome(self, *args, **kwargs)
        except Exception as exc:
            outcomes.append({'canonicalKey': self.event.canonical_key, 'errorCode': getattr(exc, 'code', None)})
            raise
    monkeypatch.setattr(research_runtime._Investigation, '_outcome', outcome_checked)
    research = research_runtime.research_outcome
    def research_checked(**kwargs):
        try:
            return research(**kwargs)
        except Exception as exc:
            final_errors.append({'canonicalKey': kwargs['event'].canonical_key,
                'exception': type(exc).__name__, 'errorCode': getattr(exc, 'code', None)})
            raise
    monkeypatch.setattr(research_runtime, 'research_outcome', research_checked)
    append = research_runtime._Investigation._b78_append
    def append_then_interrupt(self, **kwargs):
        result = append(self, **kwargs)
        if (mode == 'foreign_refs_after_round_interruption' and not interrupted
                and self.event.canonical_key == 'event-000'):
            interrupted.append('research_round_durable')
            raise SqliteWriteBusy('deterministic interruption after round is durable')
        return result
    monkeypatch.setattr(research_runtime._Investigation, '_b78_append', append_then_interrupt)
    db, task, report, materials, transport, slices = b98.make_collected_case(tmp_path, monkeypatch,
        wire=_three_flash_wire(), expected_status='completed')
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
        attempts = conn.execute('SELECT stage,state,error_code,count(*) FROM k10_external_attempts WHERE task_id=? GROUP BY 1,2,3', (task[0],)).fetchall()
        snapshots = conn.execute('SELECT snapshot_id,revision,execution_status,research_status FROM k10_research_snapshot_revisions WHERE task_id=? ORDER BY snapshot_id,revision', (task[0],)).fetchall()
        rounds = conn.execute("SELECT snapshot_id,revision,input_sha256,json_extract(result_json,'$.safeErrorCode'),json_extract(result_json,'$.companyAssessments[0].sourceRefs[0].documentId') FROM k10_research_round_results").fetchall()
        checkpoints = conn.execute('SELECT stage,status,safe_error_code,count(*) FROM k10_execution_item_checkpoints WHERE task_id=? GROUP BY 1,2,3', (task[0],)).fetchall()
        receipts = conn.execute('SELECT count(*) FROM k10_model_response_receipts WHERE task_id=?', (task[0],)).fetchone()[0]
    card_event_ids = sorted({catalyst['eventId'] for card in report['eveningCards']
                             for catalyst in card['catalysts']})
    card_source_ids = sorted({ref['documentId'] for card in report['eveningCards']
                              for ref in card['sourceRefs'] if ref.get('documentId')})
    value = dict(case=mode, injected=injected, interrupted=interrupted, taskId=task[0],
        taskStatus=task[1], reportStatus=report['status'], cardCount=len(report['eveningCards']),
        cardEventIds=card_event_ids, cardSourceIds=card_source_ids,
        materialCount=len(materials['items']), resultAvailableAt=report['resultAvailableAt'],
        rankingScope=report['delivery']['rankingScope'], researchWires=wires, slices=slices,
        outcomes=outcomes, finalErrors=final_errors, failureDisposition=failures, researchSnapshots=snapshots,
        roundResults=rounds, checkpoints=checkpoints, externalAttempts=attempts, receiptCount=receipts,
        apiStatus={'report': 200, 'materials': 200}, gaps=report['delivery']['gaps'])
    emit('research-reference-' + mode + '.json', value)
    assert all(row[1] not in {'started', 'unknown'} for row in attempts)
    # Initial response plus the existing bounded repair prompt are distinct
    # paid inputs. A resumed failed round must add neither input again.
    assert len(wire_hashes) == len(set(wire_hashes))
    assert wires.count('event-000') == (1 if mode == 'control' else 2)
    if mode == 'control':
        assert task[1] == 'completed' and report['eveningCards'] and len(materials['items']) == 3
    else:
        assert injected and not failures
        assert not any(row['errorCode'] == 'research_storage_unavailable' for row in final_errors)
        assert task[1] == 'completed' and report['status'] == 'partial'
        assert len(report['eveningCards']) == 2 and len(materials['items']) == 2
        assert set(card_event_ids) == {pipeline._event_id('event-001'), pipeline._event_id('event-002')}
        assert 'foreign-to-packet' not in card_source_ids
        assert not any(row[4] == 'foreign-to-packet' for row in rounds)
        assert any(g['reasonCode'] == 'model_json_repair_exhausted' for g in value['gaps'])
        if mode == 'foreign_refs_after_round_interruption':
            assert interrupted and 'queued' in slices


def test_terminal_projection_failure_is_checked_before_success_commit(tmp_path, monkeypatch):
    from neckline.k10.investigation import InvestigationError
    original = research_runtime._Investigation._outcome
    def reject_one(self, *args, **kwargs):
        if self.event.canonical_key == 'event-000':
            raise InvestigationError('deterministic projection failure',
                                     code='investigation_reference_invalid')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(research_runtime._Investigation, '_outcome', reject_one)
    db, task, report, materials, _, _ = b98.make_collected_case(
        tmp_path, monkeypatch, wire=_three_flash_wire(), expected_status='completed')
    assert len(report['eveningCards']) == 2 and len(materials['items']) == 2
    assert any(g['reasonCode'] == 'investigation_reference_invalid' for g in report['delivery']['gaps'])
    with sqlite3.connect(db) as conn:
        failed = conn.execute("SELECT snapshot_id FROM k10_research_round_results "
            "WHERE json_extract(result_json,'$.safeErrorCode')='investigation_reference_invalid'").fetchall()
        assert len(failed) == 1
        assert conn.execute("SELECT execution_status FROM k10_research_snapshot_revisions "
            "WHERE snapshot_id=? ORDER BY revision", failed[0]).fetchall()[-1] == ('failed',)
        assert conn.execute("SELECT count(*) FROM k10_research_snapshot_revisions "
            "WHERE snapshot_id=? AND research_status='ready_for_comparison'", failed[0]).fetchone()[0] == 0
