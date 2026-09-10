"""B56 independent reproduction: due 429 retries claimed after the frozen 6 h deadline.
Run from the frozen target Backend with its offline_guard and source on PYTHONPATH.
"""
import tempfile
from pathlib import Path
from datetime import timedelta
import pytest
import tests.conftest
from tests import test_b56_review_regressions as cases
from neckline.k10 import store

real = cases.run_once
observations = []

def delayed_worker(**kwargs):
    at = kwargs['clock']()
    is_due = kwargs['worker_id'] in {'review-recovery', 'morning-recovery'} and at.second == 0 and at.minute in {1, 11}
    if is_due:
        execution = store.task_execution_input(task_id=kwargs['task_id'], db_path=kwargs['db_path'])
        late = at + timedelta(hours=7)
        kwargs['clock'] = lambda: late
        result = real(**kwargs)
        observations.append({'worker': kwargs['worker_id'], 'scheduledAt': at.isoformat(), 'actualClaim': late.isoformat(),
                             'started': execution['checkpoint'].get('executionStartedAt'), 'status': result.status})
        return result
    return real(**kwargs)

for name, scenario, extra in [
    ('analysis', cases.test_paid_analysis_error_contract, {'status': 429, 'retry_succeeds': True, 'failed_role': 'pro'}),
    ('morning', cases.test_paid_morning_review_error_contract, {'status': 429}),
]:
    root = Path(tempfile.mkdtemp(prefix='neckline-b56-late-' + name + '-'))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cases, 'run_once', delayed_worker)
        scenario(root, mp, **extra)
print('EXPIRED_RETRY_OBSERVATIONS', observations)
