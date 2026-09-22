"""The original recovered publication must not retain its earlier failure notice."""
import json
import socket
import sqlite3
import pytest
from datetime import datetime, timezone

from neckline.k10 import store, v2_store, pipeline
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from tests import v340_acceptance_fixture as acceptance
from tests.test_v350_cli_api import DirectRoundTransport, read_actual_api

STOPPED = '本次报告在公开前终止，未生成新的排序或机会卡。'


@pytest.mark.parametrize("failure_boundary", ["before_publication", "inside_publication"])
def test_real_failed_task_recovery_replaces_diagnostic_without_reopening_publication(tmp_path, monkeypatch, failure_boundary):
    original_publish = pipeline._publish_scan
    fail = [True]
    recovered = []
    preserved_candidates = []
    def reject_first_publication(**kwargs):
        if fail[0]:
            fail[0] = False
            raise RuntimeError('isolated pause before publication')
        return original_publish(**kwargs)
    real_run_once = acceptance.run_once
    def run_once(**kwargs):
        task = real_run_once(**kwargs)
        if task.status == 'failed' and not recovered:
            db = kwargs['db_path']
            before = read_actual_api(db)[0]['report']
            assert before['availableAt'] is None and STOPPED in before['coverageGaps']
            with sqlite3.connect(db) as c:
                preserved_candidates.extend(c.execute('select * from k10_candidates order by candidate_id').fetchall())
            frozen = store.task_execution_input(task_id=task.task_id, db_path=db)
            scan = frozen['checkpoint'].get('scanId')
            if scan is None:
                with sqlite3.connect(db) as c:
                    scan = c.execute('select scan_id from k10_scan_execution_bindings where task_id=?', (task.task_id,)).fetchone()[0]
            profile = store.task_execution_profile(task_id=task.task_id, db_path=db)
            assert recover_scan(db_path=db, scan_id=scan, execution_config_id=profile['configId'],
                execution_config_revision=profile['revision'],
                confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan, db_path=db),
                now=datetime.now(timezone.utc)) == task.task_id
            recovered.append(task.task_id)
            if failure_boundary == "inside_publication":
                import time
                time.sleep(1.1)  # Recovery occurs in a later wall-clock second.
            task = real_run_once(**kwargs)
        return task
    monkeypatch.setattr(acceptance, 'TITLE_COUNT', 2)
    monkeypatch.setattr(acceptance, 'DeterministicTransport', DirectRoundTransport)
    monkeypatch.setattr(acceptance, 'run_once', run_once)
    if failure_boundary == 'before_publication':
        monkeypatch.setattr(pipeline, '_publish_scan', reject_first_publication)
    else:
        original_write = v2_store._write_report_delivery
        def reject_inside(conn, **kwargs):
            if fail[0] and kwargs['delivery']['outcome'] in {'complete', 'partial'}:
                fail[0] = False
                raise RuntimeError('isolated failure inside publication transaction')
            return original_write(conn, **kwargs)
        monkeypatch.setattr(v2_store, '_write_report_delivery', reject_inside)
    monkeypatch.setattr(socket.socket, 'connect', acceptance._deny_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', acceptance._deny_network)
    flow = acceptance.run_full_scale_flow(tmp_path, monkeypatch, name='b89-recovered',
        selected_event_count=1, expect_handler_failure=True)
    assert recovered == [flow.task_id] and flow.task_status == 'completed'
    report = read_actual_api(flow.db_path)[0]['report']
    assert report['delivery']['outcome'] == 'complete' and len(report['eveningCards']) == 1
    assert STOPPED not in report['coverageGaps']
    assert flow.calls['research:research_round'] == 1
    with sqlite3.connect(flow.db_path) as c:
        if failure_boundary == 'inside_publication':
            assert preserved_candidates and c.execute('select * from k10_candidates order by candidate_id').fetchall() == preserved_candidates
        raw = c.execute('select content_json from k10_v2_report_coverage where report_id=?', (report['reportId'],)).fetchone()[0]
        coverage = json.loads(raw)
        assert STOPPED not in coverage['coverageGaps']  # Producer/persistence fixed.
        # The already-published B88 record is retained. Reading its compatibility
        # gap list must not mutate the manifest, cards, windows or database.
        coverage['coverageGaps'] += [STOPPED, 'a real legacy data gap']
        c.execute('update k10_v2_report_coverage set content_json=? where report_id=?', (json.dumps(coverage), report['reportId']))
    with sqlite3.connect(flow.db_path) as c:
        before_dump = '\n'.join(c.iterdump())
    reread = read_actual_api(flow.db_path)[0]['report']
    assert STOPPED not in reread['coverageGaps']
    assert 'a real legacy data gap' in reread['coverageGaps']
    for field in ['availableAt', 'cutoffAt', 'reportId', 'eveningCards', 'delivery']:
        assert reread[field] == report[field]
    with sqlite3.connect(flow.db_path) as c:
        assert '\n'.join(c.iterdump()) == before_dump
        assert not c.execute("select 1 from k10_external_attempts where state in ('started','running','unknown')").fetchone()


def test_collection_clock_tolerance_preserves_fact_and_evidence_conflicts():
    from copy import deepcopy
    from neckline.k10.discovery import _same_candidate_comparison
    first = {'summary': 'same judgment', 'marketContext': {'300002.SZ': {
        'asOf': '2026-09-22T13:00:00Z', 'collectedAt': '2026-09-22T13:00:01Z',
        'sourceRefs': [{'tradeDate': '2026-09-22'}], 'recentDays': [{'close': 12.0}]}}}
    second = deepcopy(first)
    second['marketContext']['300002.SZ']['collectedAt'] = '2026-09-22T13:05:00Z'
    assert _same_candidate_comparison(first, second)
    for key, value in [('asOf', '2026-09-21T13:00:00Z'), ('recentDays', [{'close': 13.0}]),
                       ('sourceRefs', []), ('status', 'available')]:
        changed = deepcopy(second)
        changed['marketContext']['300002.SZ'][key] = value
        assert not _same_candidate_comparison(first, changed)
    second['summary'] = 'different judgment'
    assert not _same_candidate_comparison(first, second)
    assert not _same_candidate_comparison({'value': False}, {'value': 0})
    assert not _same_candidate_comparison({}, {'marketContext': None})
