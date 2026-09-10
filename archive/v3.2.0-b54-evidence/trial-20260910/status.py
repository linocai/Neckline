"""Read-only compact status for this exact real trial; no prompts or credentials."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess

task_id = 'task_c9c0feab5c83a08e8a17138ebfb0041a'
scan_id = 'scan_d39fbd5c631ea9a7df5f569dbd432b4d'
def compact(value):
    if isinstance(value, list):
        if value and all(isinstance(x, str) and len(x) < 100 for x in value) and len(value) <= 5:
            return value
        return {'count': len(value)}
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items()}
    return value[:220] if isinstance(value, str) else value

db = Path('/opt/neckline/data/neckline.db')
with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as conn:
    conn.row_factory = sqlite3.Row
    task = dict(conn.execute('SELECT task_id,status,stage,attempt_count,error_text,updated_at,checkpoint_json '
        'FROM k10_tasks WHERE task_id=?', (task_id,)).fetchone())
    task['checkpoint'] = compact(json.loads(task.pop('checkpoint_json')))
    row = conn.execute('SELECT status,coverage_json,completed_at FROM k10_scans WHERE scan_id=?', (scan_id,)).fetchone()
    scan = dict(row) if row else None
    if scan:
        coverage = json.loads(scan.pop('coverage_json'))
        scan['coverage'] = compact(coverage)
        scan['sourceOutcomes'] = [compact(x) for x in coverage.get('sourceOutcomes', [])]
    usage = [dict(r) for r in conn.execute('SELECT stage,state,error_code,COUNT(*) AS calls,'
        'SUM(prompt_tokens) AS input_tokens,SUM(completion_tokens) AS output_tokens,SUM(total_tokens) AS tokens '
        'FROM k10_external_attempts WHERE task_id=? GROUP BY stage,state,error_code', (task_id,))]
    retry = [dict(r) for r in conn.execute('SELECT not_before_at FROM k10_task_retry_schedules WHERE task_id=?', (task_id,))]
unit = subprocess.run(['systemctl', 'show', 'neckline-trial-20260910-evening.service',
    '--property=ActiveState,SubState,ExecMainStatus,Result'], capture_output=True, text=True).stdout.strip()
print(json.dumps({'utc': datetime.now(timezone.utc).isoformat(), 'task': task, 'scan': scan,
    'externalAttempts': usage, 'retry': retry, 'unit': unit}, ensure_ascii=False))
