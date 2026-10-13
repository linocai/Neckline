"""Real producer/worker regressions for truncated output and resumed status."""
from datetime import timedelta
import json
import sqlite3

import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e
from tests.k10_v320_fixture import create_app
from fastapi.testclient import TestClient


def api_for(db):
    return TestClient(create_app(db, config_id='b39', execution_config_id='b39-execution'))


# B78 merges the former third title review into the direct research round. Keep
# compact-repair coverage at the remaining output boundaries.
@pytest.mark.parametrize('stage', ['titleGlobal', 'understand'])
def test_output_truncation_uses_compact_repair_within_bound_policy(tmp_path, monkeypatch, stage):
    seen=[]
    def observe(request):
        wire=json.loads(request.content)
        if '上次达到输出长度限制' in wire['messages'][-1]['content']:
            seen.append(wire)
    db, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True,
        finalization_truncate=stage, request_observer=observe)
    assert task.status == 'completed'
    assert calls.count(stage) == 2
    assert seen and all(row['thinking']=={'type':'disabled'} and 'reasoning_effort' not in row for row in seen)
    assert api_for(db).get('/api/v1/k10/v2/reports/latest').json()['report']['eveningCards']


