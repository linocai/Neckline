"""Read-only final acceptance of the authorized September 10 evening report."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import socket
import sqlite3

from neckline.k10.cli import frozen_scan_input_sha256
from neckline.k10.research_store import read_research_state
from neckline.k10.v2_store import read_report

assert socket.gethostname() == 'ser657204219523'
db = Path('/opt/neckline/data/neckline.db')
backup = db.parent/'backups/v3.2.0-b70-predeploy'
task_id = 'task_c9c0feab5c83a08e8a17138ebfb0041a'
scan_id = 'scan_d39fbd5c631ea9a7df5f569dbd432b4d'
report = read_report(db_path=db, report_id='report_'+scan_id)
assert report['availableAt'] and report['status'] in {'completed', 'partial'}
assert report['cutoffAt'] == '2026-09-10T13:00:00+00:00'
assert not report['incompleteReviews']
assert frozen_scan_input_sha256(scan_id=scan_id, db_path=db) == 'f9c9e0bd0b4a00513eb8886d2cac63f65165dd85c5453691e05f32d1d3ea5770'
cards = report['eveningCards']
codes = [c['companyCode'] for c in cards]
assert len(codes) == len(set(codes)) <= 30
with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True) as conn, sqlite3.connect((backup/'neckline-pre.db').as_uri()+'?mode=ro', uri=True) as prior:
    task = conn.execute('SELECT status,checkpoint_json FROM k10_tasks WHERE task_id=?', (task_id,)).fetchone()
    assert task[0] == 'completed'
    checkpoint = json.loads(task[1])
    snapshots = checkpoint['researchSnapshotIds']
    assert len(snapshots) == len(set(snapshots))
    universe = {r[0] for r in conn.execute('SELECT m.company_code FROM k10_v2_universe_members m JOIN k10_v2_strategy_snapshots s ON s.universe_snapshot_id=m.snapshot_id WHERE s.snapshot_id=?', (report['strategySnapshotId'],))}
    assert len(universe) == 1089 and set(codes) <= universe
    old_tasks = conn.execute('SELECT * FROM k10_tasks WHERE task_id != ? ORDER BY task_id', (task_id,)).fetchall()
    resume = json.loads((backup/'resume-launch.json').read_text())
    assert sha256(json.dumps(old_tasks, sort_keys=True).encode()).hexdigest() == resume['oldTasksSha256']
    windows = prior.execute('SELECT * FROM k10_company_windows').fetchall()
    for row in windows:
        assert conn.execute('SELECT * FROM k10_company_windows WHERE company_window_id=?', (row[0],)).fetchone() == row
    # Compare the original completed rows, not merely their counts.
    columns = [x[1] for x in conn.execute('PRAGMA table_info(k10_execution_item_checkpoints)')]
    saved = prior.execute("SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND status='completed'", (task_id,)).fetchall()
    for row in saved:
        item = dict(zip(columns, row))
        assert conn.execute('SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND item_kind=? AND item_key=? AND stage=?', (task_id, item['item_kind'], item['item_key'], item['stage'])).fetchone() == row
    for card in cards:
        assert card['summary'].strip() and card['twoDayReason'].strip() and card['sourceRefs']
        assert card['d1TradeDate'] < card['d2TradeDate']
        for ref in card['sourceRefs']:
            assert conn.execute('SELECT 1 FROM k10_source_document_versions WHERE document_id=? AND revision=?', (ref['documentId'], ref['revision'])).fetchone()
for snapshot_id in snapshots:
    state = read_research_state(snapshot_id=snapshot_id, db_path=db)
    assert state and state['snapshot'].execution_status == 'ok'
    assert any((stage['result'].get('conclusion') or {}).get('runtimeFinal') for stage in state['stageResults'])
value = {'taskId': task_id, 'reportId': report['reportId'], 'reportStatus': report['status'],
         'availableAt': report['availableAt'], 'cutoffAt': report['cutoffAt'], 'companyCount': len(cards),
         'researchEventsComplete': len(snapshots), 'incompleteReviews': 0, 'fixedUniverseOnly': True,
         'originalCompletedCheckpointsPreserved': len(saved), 'oldTasksUnchanged': True,
         'oldCompanyWindowsUnchanged': len(windows), 'coverageGaps': report['coverageGaps'],
         'companyWindows': [{'code': c['companyCode'], 'd1': c['d1TradeDate'], 'd2': c['d2TradeDate']} for c in cards],
         'checkedAt': datetime.now(timezone.utc).isoformat()}
path = backup/'report-acceptance.json'
path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
path.chmod(0o600)
print(json.dumps(value, ensure_ascii=False))
