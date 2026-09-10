"""Compact read-only progress without provider material or credentials."""
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
db=Path('/opt/neckline/data/neckline.db')
task_id='task_c9c0feab5c83a08e8a17138ebfb0041a'
with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as conn:
    conn.row_factory=sqlite3.Row
    task=dict(conn.execute('SELECT status,stage,attempt_count,error_text,updated_at FROM k10_tasks WHERE task_id=?',(task_id,)).fetchone())
    counts=[dict(row) for row in conn.execute('SELECT stage,status,COUNT(*) AS count FROM k10_execution_item_checkpoints WHERE task_id=? GROUP BY stage,status',(task_id,))]
    usage=[dict(row) for row in conn.execute('SELECT stage,state,error_code,COUNT(*) AS calls,SUM(total_tokens) AS tokens FROM k10_external_attempts WHERE task_id=? GROUP BY stage,state,error_code',(task_id,))]
    scan=dict(conn.execute('SELECT status,completed_at FROM k10_scans WHERE scan_id=?',('scan_d39fbd5c631ea9a7df5f569dbd432b4d',)).fetchone())
print(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),'task':task,'scan':scan,'checkpoints':counts,'usage':usage},ensure_ascii=False))
