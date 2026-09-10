"""User-authorized single B70 evening trial; uses the production worker boundary.

Only the CLI-returned task ID is eligible. No old-job recovery, global queue
draining, notification maintenance, or evaluation maintenance is invoked.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import sys
import time
from dotenv import load_dotenv

assert socket.gethostname() == 'ser657204219523'
load_dotenv('/opt/neckline/.env', override=True)
load_dotenv('/etc/neckline/k10.env', override=True)
from neckline.config import settings
from neckline.k10 import store
from neckline.k10.pipeline import production_handlers
from neckline.k10.worker import run_once
from neckline.k10.schema import read_connection, require_schema

db = Path('/opt/neckline/data/neckline.db')
task_id = sys.argv[1]
receipt = Path('/opt/neckline/data/backups/v3.2.0-b70-predeploy/resume-runner-result.json')
with read_connection(db) as conn:
    require_schema(conn)
    task = conn.execute('SELECT kind,input_cutoff_at FROM k10_tasks WHERE task_id=?', (task_id,)).fetchone()
    assert task and task[0] == 'evening_scan'
    assert datetime.fromisoformat(task[1]) == datetime.fromisoformat('2026-09-10T21:00:00+08:00')
handlers = production_handlers(tushare_token=settings.tushare_token, parquet_dir=settings.parquet_dir)
while True:
    task = store.get_task(task_id=task_id, db_path=db)
    assert task is not None
    if task.status not in ('queued', 'running'):
        value = {'taskId': task_id, 'status': task.status, 'finishedAt': datetime.now(timezone.utc).isoformat()}
        receipt.write_text(json.dumps(value) + '\n'); receipt.chmod(0o600)
        print(json.dumps(value), flush=True)
        if task.status == 'completed':
            from deliver_report import deliver
            deliver()
        break
    if store.run_control_status(db_path=db)['state'] != 'open':
        print(json.dumps({'taskId': task_id, 'status': 'paused'}), flush=True)
        break
    # Claims respect durable not-before times and the task's frozen retry limits.
    claimed = run_once(db_path=db, task_id=task_id, worker_id='trial-b70-20260910-evening',
        lease_for=timedelta(minutes=5), handlers=handlers)
    if claimed is not None:
        print(json.dumps({'taskId': task_id, 'status': claimed.status, 'attempt': claimed.attempt_count}), flush=True)
    else:
        time.sleep(2)
