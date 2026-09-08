"""Provider length failures resume only unfinished finalization on the frozen task."""
import sqlite3
from datetime import timedelta
import pytest
from neckline.k10 import pipeline, store
from neckline.k10.cli import main, frozen_scan_input_sha256
from neckline.k10.worker import run_once
from tests.test_v310_pipeline_e2e import _run, _http_transport, _api, RUN_AT


@pytest.mark.parametrize('operation', ['classify', 'prioritize'])
def test_explicit_output_repair_preserves_successful_research_and_classification(tmp_path, monkeypatch, operation):
    db, task_id, first, _, gateway = _run(tmp_path, monkeypatch, finalization_truncate=operation)
    assert first.status == 'failed'
    before = store.task_execution_input(task_id=task_id, db_path=db)
    with _api(db) as client:
        failed_scan = client.get('/api/v1/k10/scans/latest?window=evening').json()
    assert failed_scan['status'] == 'failed' and failed_scan['publicationStatus'] == 'not_published'
    scan = failed_scan['scanId']
    with sqlite3.connect(db) as conn:
        successes = conn.execute("SELECT item_key,result_json FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:classify' AND status='completed' ORDER BY item_key", (task_id,)).fetchall()
        assert conn.execute("SELECT count(*) FROM k10_execution_item_checkpoints WHERE task_id=? AND stage=? AND safe_error_code='response_truncated'", (task_id, 'model:'+operation)).fetchone() == (1,)
    assert len(successes) == (2 if operation == 'classify' else 3)
    assert main(['recover-scan', '--db', str(db), '--scan-id', scan, '--execution-config-id', 'b39-execution',
                 '--execution-config-revision', '1', '--confirm-frozen-input-sha256', frozen_scan_input_sha256(scan_id=scan, db_path=db),
                 '--finalization-max-tokens', '512']) == 0
    original = pipeline.DeepSeekDiscoveryModel._request_json
    observed = []
    def capture(self, **kwargs):
        observed.append(kwargs['model_options']['maxTokens'])
        return original(self, **kwargs)
    monkeypatch.setattr(pipeline.DeepSeekDiscoveryModel, '_request_json', capture)
    calls = _http_transport(monkeypatch)
    done = run_once(db_path=db, worker_id='output-repair', lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet'), clock=lambda: RUN_AT)
    assert done.status == 'completed'
    assert calls == (['classify', 'prioritize'] if operation == 'classify' else ['prioritize'])
    assert observed == [512] * len(calls)
    assert len(gateway.search_paths) == 2
    after = store.task_execution_input(task_id=task_id, db_path=db)
    assert after['checkpoint']['executionStartedAt'] == before['checkpoint']['executionStartedAt']
    assert after['checkpoint']['runtimeRepair']['finalizationModelOptions']['companyComparison']['maxTokens'] == 512
    with sqlite3.connect(db) as conn:
        for key, raw in successes:
            assert conn.execute("SELECT result_json FROM k10_execution_item_checkpoints WHERE task_id=? AND item_key=? AND status='completed'", (task_id, key)).fetchone() == (raw,)
    with _api(db) as client:
        assert client.get('/api/v1/k10/scans/'+scan).json()['publicationStatus'] == 'published'
    assert len(store.list_candidates(scan_id=scan, state='offered', db_path=db)) == 1
