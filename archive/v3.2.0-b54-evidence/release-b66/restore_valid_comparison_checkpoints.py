"""Undo only the five proven legacy-rule cache labels; retain every result and bill."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json, socket, sqlite3
from neckline.k10.investigation import decode_stage_result

assert socket.gethostname() == 'ser657204219523'
db=Path('/opt/neckline/data/neckline.db')
backup=db.parent/'backups/v3.2.0-b66-predeploy'
before=backup/'neckline-pre.db'
task_id='task_c9c0feab5c83a08e8a17138ebfb0041a'
scan_id='scan_d39fbd5c631ea9a7df5f569dbd432b4d'
assert json.loads(Path('/opt/neckline/release-manifest.json').read_text())['releaseSet']=='v3.2.0-b66'
assert sha256(before.read_bytes()).hexdigest()=='60c9a1934e897664df174740ca97a33edd8599e183adedbae0071d396a8dee8c'
recovery=backup/'pre-checkpoint-label-repair.db'
assert not recovery.exists()
with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(recovery) as dest:src.backup(dest)
recovery.chmod(0o600)
canonical=lambda x:json.dumps(x,sort_keys=True,ensure_ascii=False)
digest=lambda rows:sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
with sqlite3.connect(before.as_uri()+'?mode=ro',uri=True) as old, sqlite3.connect(db) as conn:
    conn.execute('BEGIN IMMEDIATE')
    task=conn.execute('SELECT status,checkpoint_json,input_cutoff_at FROM k10_tasks WHERE task_id=?',(task_id,)).fetchone()
    assert task[0] in {'queued','running','completed'} and datetime.fromisoformat(task[2])==datetime.fromisoformat('2026-09-10T21:00:00+08:00')
    authorized=json.loads(task[1])['recoveryAuthorized']['authorizedAt']
    assert authorized=='2026-09-10T23:49:05.852054+08:00'
    columns=[r[1] for r in conn.execute('PRAGMA table_info(k10_execution_item_checkpoints)')]
    rows=old.execute("SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND status='completed' ORDER BY item_key,stage",(task_id,)).fetchall()
    changed=[]
    for row in rows:
        prior=dict(zip(columns,row))
        key=(task_id,prior['item_kind'],prior['item_key'],prior['stage'])
        actual=conn.execute('SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND item_kind=? AND item_key=? AND stage=?',key).fetchone()
        if actual==row:continue
        now=dict(zip(columns,actual))
        assert prior['stage']=='model:investigation_compare_companies'
        assert {k for k in columns if now[k]!=prior[k]}=={'status','safe_error_code','updated_at'}
        assert now['status']=='failed' and now['safe_error_code']=='investigation_cached_contract_invalid' and now['updated_at']==authorized
        result=decode_stage_result(json.loads(prior['result_json']),action='compare_companies')
        assert any(r['role'] in {'primary','alternative','tied'} for r in result.company_assessments)
        snapshots=conn.execute("SELECT s.result_json,r.research_status FROM k10_research_stage_results s JOIN k10_research_snapshot_revisions r ON r.snapshot_id=s.snapshot_id AND r.revision=s.revision WHERE r.task_id=? AND s.action='compare_companies'",(task_id,)).fetchall()
        assert any(state=='pending_verification' and canonical(json.loads(raw))==canonical(json.loads(prior['result_json'])) for raw,state in snapshots)
        changed.append((key,prior))
    assert len(rows)==536 and len(changed)==5
    for key,prior in changed:
        assert conn.execute('UPDATE k10_execution_item_checkpoints SET status=?,safe_error_code=?,updated_at=? WHERE task_id=? AND item_kind=? AND item_key=? AND stage=?',('completed',prior['safe_error_code'],prior['updated_at'],*key)).rowcount==1
    old_tasks=old.execute('SELECT * FROM k10_tasks WHERE task_id!=? ORDER BY task_id',(task_id,)).fetchall()
    assert conn.execute('SELECT * FROM k10_tasks WHERE task_id!=? ORDER BY task_id',(task_id,)).fetchall()==old_tasks
    attempts=old.execute('SELECT * FROM k10_external_attempts WHERE task_id=? ORDER BY rowid',(task_id,)).fetchall()
    for row in attempts:assert conn.execute('SELECT * FROM k10_external_attempts WHERE attempt_id=?',(row[0],)).fetchone()==row
    titles=old.execute("SELECT * FROM k10_execution_item_checkpoints WHERE task_id=? AND stage='model:titleBatch' ORDER BY item_key",(task_id,)).fetchall()
    value={'taskId':task_id,'scanId':scan_id,'releaseSet':'v3.2.0-b66','resumedAt':authorized,
        'frozenInputSha256':'f9c9e0bd0b4a00513eb8886d2cac63f65165dd85c5453691e05f32d1d3ea5770',
        'checkpointsAndBillingUnchangedByRecovery':False,'recoveryAudit':'Five valid pending-verification comparison cache labels were invalidated by a legacy rule; exact original metadata restored after typed and historical-state validation.',
        'restoredCacheLabels':5,'originalResultsAndBillingPreserved':True,'paidTitleCount':len(titles),
        'paidTitleSha256':digest(titles),'oldTasksSha256':digest(old_tasks),'externalAttemptsSha256':digest(attempts),
        'allCompletedCheckpointCount':len(rows),'allCompletedCheckpointSha256':digest(rows),
        'repairBackup':str(recovery),'repairBackupSha256':sha256(recovery.read_bytes()).hexdigest(),
        'repairedAt':datetime.now(timezone.utc).isoformat()}
path=backup/'resume-launch.json';path.write_text(json.dumps(value,indent=2)+'\n');path.chmod(0o600)
print(json.dumps(value))
