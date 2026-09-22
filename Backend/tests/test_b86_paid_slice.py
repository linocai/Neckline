"""A paid result must persist even when its response crosses a worker slice."""
import socket
import sqlite3
from types import SimpleNamespace
import pytest

from neckline.k10 import pipeline
from neckline.k10 import research_runtime
from tests import v340_acceptance_fixture as acceptance
from tests.test_v350_cli_api import DirectRoundTransport, read_actual_api


@pytest.mark.parametrize('local_read', [False, True])
def test_real_worker_persists_paid_research_before_yielding_slice(tmp_path, monkeypatch, local_read):
    elapsed = [0.0]
    observed = []

    class SlowResearch(DirectRoundTransport):
        def respond(self, request):
            payload = self._packet(request)
            if (local_read and payload.get('action') == 'research_round'
                    and not payload['evidencePacket'].get('contextResults')):
                self._record('research:research_round', payload['evidencePacket']['event']['canonicalKey'])
                elapsed[0] += 10000
                return self._ok({'action': 'research_round', 'contextRequests': [{
                    'kind': 'company_fields', 'purpose': 'Read relevant business before comparison',
                    'companyCode': self._company_for_event(payload['evidencePacket']['event']['canonicalKey']),
                    'fields': ['businesses']}]})
            response = super().respond(request)
            if self._packet(request).get('action') == 'research_round':
                elapsed[0] += 10000  # Response arrives after the frozen slice budget.
            return response

    real_run_once = acceptance.run_once
    real_research_round = research_runtime.run_research_round
    pools = []

    def research_round(*args, **kwargs):
        pool = kwargs['evidence_packet']['_localState']['fixedPool']
        assert len(pool) == 1089
        pools.append(pool)
        assert pool == pools[0]
        return real_research_round(*args, **kwargs)

    monkeypatch.setattr(research_runtime, 'run_research_round', research_round)

    def run_once(**kwargs):
        task = real_run_once(**kwargs)
        with sqlite3.connect(kwargs['db_path']) as conn:
            states = conn.execute('SELECT research_status,execution_status FROM '
                'k10_research_snapshot_revisions r WHERE task_id=? AND revision='
                '(SELECT MAX(revision) FROM k10_research_snapshot_revisions x '
                'WHERE x.snapshot_id=r.snapshot_id)', (task.task_id,)).fetchall()
            rounds = conn.execute('SELECT COUNT(*) FROM k10_research_round_results').fetchone()[0]
            calls = conn.execute("SELECT COUNT(*) FROM k10_external_attempts WHERE stage='investigation'").fetchone()[0]
        if states:
            observed.append((states, rounds, calls))
        return task

    monkeypatch.setattr(pipeline, 'time', SimpleNamespace(monotonic=lambda: elapsed[0]))
    monkeypatch.setattr(acceptance, 'TITLE_COUNT', 2)
    monkeypatch.setattr(acceptance, 'DeterministicTransport', SlowResearch)
    monkeypatch.setattr(acceptance, 'run_once', run_once)
    monkeypatch.setattr(socket.socket, 'connect', acceptance._deny_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', acceptance._deny_network)
    flow = acceptance.run_full_scale_flow(tmp_path, monkeypatch, name='b86-paid-slice', selected_event_count=1)
    assert observed[0] == ([('continue_research' if local_read else 'ready_for_comparison', 'ok')], 1, 1)
    assert flow.task_status == 'completed'
    assert flow.calls['research:research_round'] == (2 if local_read else 1)
    assert read_actual_api(flow.db_path)[0]['report']['delivery']['outcome'] == 'complete'


def test_shared_input_boundary_keeps_lease_and_revision_guards(tmp_path):
    from datetime import datetime, timedelta
    from neckline.k10.research_store import freeze_research_input_boundary
    from tests.test_v350_round_persistence import _setup, _append
    from neckline.k10.store import K10Conflict
    db, snapshot = _setup(tmp_path)
    later = (datetime.fromisoformat(snapshot.updated_at) + timedelta(seconds=10)).isoformat()

    def lost_lease():
        raise RuntimeError('lease lost')

    with pytest.raises(RuntimeError, match='lease lost'):
        freeze_research_input_boundary(snapshot=snapshot, as_of=later, db_path=db, lease_guard=lost_lease)
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM k10_execution_item_checkpoints WHERE stage='research_input_boundary'").fetchone()[0] == 0
    frozen = freeze_research_input_boundary(snapshot=snapshot, as_of=snapshot.updated_at, db_path=db)
    assert frozen[0] == snapshot.updated_at and frozen[1] > 0
    assert freeze_research_input_boundary(snapshot=snapshot, as_of=later, db_path=db) == frozen
    _append(db, snapshot)
    with pytest.raises(K10Conflict, match='revision changed'):
        freeze_research_input_boundary(snapshot=snapshot, as_of=later, db_path=db)
