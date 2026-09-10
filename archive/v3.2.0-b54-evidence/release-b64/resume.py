"""Resume only tonight's user-authorized frozen report through the real CLI."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import urllib.request

from neckline.k10.cli import frozen_scan_input_sha256
from neckline.k10 import store

assert socket.gethostname() == 'ser657204219523'
db = Path('/opt/neckline/data/neckline.db')
backup = db.parent / 'backups/v3.2.0-b64-predeploy'
receipt = json.loads((backup/'receipt.json').read_text())
assert receipt['status'] == 'completed' and receipt['allDatabaseRowsUnchanged']
with urllib.request.urlopen('http://127.0.0.1:8002/api/v1/health') as response:
    assert json.load(response)['releaseSet'] == 'v3.2.0-b64'
task_id = 'task_c9c0feab5c83a08e8a17138ebfb0041a'
scan_id = 'scan_d39fbd5c631ea9a7df5f569dbd432b4d'
assert store.get_task(task_id=task_id, db_path=db).status == 'failed'
assert store.run_control_status(db_path=db)['state'] == 'open'

def snapshot():
    with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True) as conn:
        titles = conn.execute("SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:titleBatch' ORDER BY item_key", (task_id,)).fetchall()
        completed = conn.execute("SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND status='completed' ORDER BY item_key,stage", (task_id,)).fetchall()
        old_tasks = conn.execute('SELECT * FROM k10_tasks WHERE task_id != ? ORDER BY task_id', (task_id,)).fetchall()
        attempts = conn.execute('SELECT * FROM k10_external_attempts WHERE task_id=? ORDER BY rowid', (task_id,)).fetchall()
    digest = lambda rows: sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return {'paidTitleCount': len(titles), 'paidTitleSha256': digest(titles), 'oldTasksSha256': digest(old_tasks),
        'externalAttemptsSha256': digest(attempts), 'allCompletedCheckpointCount': len(completed), 'allCompletedCheckpointSha256': digest(completed)}

before = snapshot()
assert before['paidTitleCount'] == 44
frozen = frozen_scan_input_sha256(scan_id=scan_id, db_path=db)
assert frozen == 'f9c9e0bd0b4a00513eb8886d2cac63f65165dd85c5453691e05f32d1d3ea5770'
result = subprocess.run([sys.executable, '-m', 'neckline.k10.cli', 'recover-scan', '--db', str(db),
    '--scan-id', scan_id, '--execution-config-id', 'k10-v2-execution-production', '--execution-config-revision', '1',
    '--confirm-frozen-input-sha256', frozen], check=True, capture_output=True, text=True)
assert result.stdout.strip() == task_id
assert snapshot() == before
assert store.get_task(task_id=task_id, db_path=db).status == 'queued'
value = {'taskId':task_id, 'scanId':scan_id, 'releaseSet':'v3.2.0-b64', 'frozenInputSha256':frozen,
    'resumedAt':datetime.now(timezone.utc).isoformat(), 'checkpointsAndBillingUnchangedByRecovery':True, **before}
(backup/'resume-launch.json').write_text(json.dumps(value, indent=2)+'\n')
(backup/'resume-launch.json').chmod(0o600)
print(json.dumps(value))
