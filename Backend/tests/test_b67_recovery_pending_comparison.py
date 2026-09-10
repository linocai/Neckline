from datetime import timedelta
import sqlite3

from neckline.k10 import pipeline, store
from neckline.k10.cli import recover_scan, frozen_scan_input_sha256
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e


def test_recovery_preserves_completed_labeled_unverified_comparison(tmp_path, monkeypatch):
    original = pipeline.DeepSeekDiscoveryModel.prioritize
    def interrupted(self, **kwargs):
        raise pipeline.PipelineError('interrupted after comparison', code='execution_paused')
    monkeypatch.setattr(pipeline.DeepSeekDiscoveryModel, 'prioritize', interrupted)
    db, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True, pending_ranking='legacy_wrong')
    assert task.status == 'failed' and calls.count('research:compare_companies') == 1
    with sqlite3.connect(db) as conn:
        before = conn.execute("SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:investigation_compare_companies'", (task_id,)).fetchall()
    assert len(before) == 1 and before[0][5] == 'completed'
    scan = store.task_execution_input(task_id=task_id, db_path=db)['checkpoint']['scanId']
    assert recover_scan(db_path=db, scan_id=scan, execution_config_id='b39-execution', execution_config_revision=1,
        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan, db_path=db), now=e2e.RUN_AT) == task_id
    with sqlite3.connect(db) as conn:
        after = conn.execute("SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:investigation_compare_companies'", (task_id,)).fetchall()
    assert after == before
    monkeypatch.setattr(pipeline.DeepSeekDiscoveryModel, 'prioritize', original)
    resumed = e2e._http_transport(monkeypatch, v2=True)
    task = run_once(db_path=db, task_id=task_id, worker_id='b67', lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet'), clock=lambda:e2e.RUN_AT)
    assert task.status == 'completed' and 'research:compare_companies' not in resumed
    assert read_report(db_path=db)['eveningCards']
