"""Back up and drain only the authorized current report before the B68 cutover."""
from pathlib import Path
from datetime import datetime, timezone
import json,os,socket,sqlite3,subprocess,sys
from hashlib import sha256
from neckline.k10 import store
root=Path('/opt/neckline');db=root/'data/neckline.db';backup=db.parent/'backups/v3.2.0-b66-predeploy/pre-b68-drain.db'
assert socket.gethostname()=='ser657204219523' and os.geteuid()==0
manifest=json.loads((root/'release-manifest.json').read_text())
assert manifest['releaseSet']=='v3.2.0-b66' and manifest['commit']=='6eab734c939f63a7813a4ad9dd8d65ac9b09b6f6'
assert (root.stat().st_uid,root.stat().st_gid,root.stat().st_mode&0o777)==(0,0,0o755)
assert (db.stat().st_uid,db.stat().st_gid,db.stat().st_mode&0o777)==(997,988,0o600)
t='task_c9c0feab5c83a08e8a17138ebfb0041a'
if sys.argv[1]=='close':
 assert not backup.exists() and store.get_task(task_id=t,db_path=db).status in {'queued','running'}
 with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(backup) as dest:
  source.backup(dest);assert dest.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 os.chown(backup,997,988);backup.chmod(0o600)
 store.set_run_control(state='closed',reason_code='b68_cutover_drain',changed_at=datetime.now(timezone.utc).isoformat(),changed_by='authorized-report-repair',db_path=db)
 print(json.dumps({'state':'closed','backup':str(backup),'sha256':sha256(backup.read_bytes()).hexdigest()}))
elif sys.argv[1]=='reopen':
 assert backup.exists()
 assert subprocess.run(['systemctl','is-active','neckline-trial-20260910-evening-b66.service'],capture_output=True,text=True).stdout.strip()=='inactive'
 with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as c:
  assert c.execute("SELECT COUNT(*) FROM k10_external_attempts WHERE task_id=? AND state='started'",(t,)).fetchone()[0]==0
  assert c.execute("SELECT COUNT(*) FROM k10_execution_item_checkpoints WHERE task_id=? AND status='running'",(t,)).fetchone()[0]==0
 assert store.get_task(task_id=t,db_path=db).status=='failed'
 store.set_run_control(state='open',reason_code='b68_same_task_continuation',changed_at=datetime.now(timezone.utc).isoformat(),changed_by='authorized-report-repair',db_path=db)
 print(json.dumps({'state':'open','task':t,'readyForCodeCutover':True}))
else:raise ValueError('Unknown phase')
