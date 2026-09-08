"""One-shot B40 code-only hotfix; preserves the frozen first evening task."""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, os, shutil, socket, sqlite3, subprocess, tarfile

spec=importlib.util.spec_from_file_location('prior', '/tmp/neckline-b40-release/prior.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
ROOT=h.ROOT;DB=h.DB;P=Path('/tmp/neckline-b40-release')
B=ROOT/'data/backups/v3.1.0-b40-predeploy'
TASK='task_8ee84154911c2463c54880feeefb93a5'
assert os.geteuid()==0 and socket.gethostname()=='ser657204219523'
assert h.file_metadata(ROOT)=={'uid':0,'gid':0,'mode':493}
assert h.health()['releaseSet']=='v3.1.0-b39'
old=json.loads((ROOT/'release-manifest.json').read_text())
new=json.loads((P/'runtime-manifest.json').read_text())
assert old['releaseSet']=='v3.1.0-b39' and new['releaseSet']=='v3.1.0-b40'
assert all(h.sha256(ROOT/n)==digest for n,digest in old['backendFiles'].items())
assert set(old['backendFiles']) <= set(new['backendFiles'])
assert h.sha256(P/'Neckline-v3.1.0-b40-Backend.tar.gz')==new['archiveSha256']
wheel=P/'wheels'/new['wheelFile'];assert h.sha256(wheel)==new['wheelSha256']
oldwheel=ROOT/'releases/v3.1.0-b39/neckline_runtime-3.1.0-py3-none-any.whl';assert h.sha256(oldwheel)==old['wheelSha256']
for unit in h.SCAN_UNITS:
 assert h.unit_state(unit)=='inactive'
with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as c:
 assert c.execute("SELECT state FROM k10_run_controls WHERE control_key='k10_discovery'").fetchone()==('closed',)
 assert c.execute('SELECT status FROM k10_tasks WHERE task_id=?',(TASK,)).fetchone()==('failed',)
 assert c.execute("SELECT count(*) FROM k10_execution_item_checkpoints WHERE task_id=? AND status='completed'",(TASK,)).fetchone()==(6,)
 assert c.execute("SELECT count(*) FROM k10_tasks WHERE status IN ('running','queued')").fetchone()==(0,)
assert not B.exists();B.mkdir(mode=0o700)
prior_units={u:h.unit_state(u) for u in h.TIMERS+h.WRITERS}
stage=P/'staged';stage.mkdir(exist_ok=False)
with tarfile.open(P/'Neckline-v3.1.0-b40-Backend.tar.gz') as tar:
 assert all(not Path(m.name).is_absolute() and '..' not in Path(m.name).parts and m.isfile() for m in tar.getmembers())
 tar.extractall(stage)
assert all(h.sha256(stage/n)==digest for n,digest in new['backendFiles'].items())
shutil.copy2(ROOT/'release-manifest.json',B/'release-manifest.json')
shutil.copy2('/etc/neckline/k10.env',B/'k10.env')
os.chmod(B/'k10.env',0o600)
for n in old['backendFiles']:
 target=B/'runtime'/n;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/n,target)
receipt={'releaseSet':'v3.1.0-b40','commit':new['commit'],'backup':str(B),'startedAt':datetime.now(timezone.utc).isoformat(),'status':'prepared'}
h.write_json(B/'receipt.json',receipt)
def copy_payload(source,files):
 for n in files:
  target=ROOT/n;target.parent.mkdir(parents=True,exist_ok=True)
  shutil.copy2(source/n,target);os.chmod(target,0o755 if os.access(source/n,os.X_OK) else 0o644)
  owner=h.pwd.getpwnam('deploy');os.chown(target,owner.pw_uid,h._neckline().pw_gid)
def pip_install(path):
 h.run([str(ROOT/'.venv/bin/pip'),'install','--no-deps','--force-reinstall',str(path)],capture_output=True)
try:
 h.run(['systemctl','stop',*h.TIMERS,*h.WRITERS],capture_output=True)
 receipt['preBackup']=h.snapshot(B/'neckline-pre.db')
 baseline=h.fingerprint(DB)
 assert baseline['schema']==7
 copy_payload(stage,new['backendFiles']);pip_install(wheel)
 shutil.copy2(P/'runtime-manifest.json',ROOT/'release-manifest.json');os.chmod(ROOT/'release-manifest.json',0o644)
 assert h.file_metadata(ROOT)=={'uid':0,'gid':0,'mode':493}
 h.run(['systemctl','start','neckline.service'],capture_output=True)
 receipt['health']=h.wait_health('v3.1.0-b40')
 assert h.fingerprint(DB)==baseline
 assert all(h.sha256(ROOT/n)==digest for n,digest in new['backendFiles'].items())
 receipt['readiness']=json.loads(h.nk('''
import json, urllib.request
from neckline.config import settings
from neckline.api.k10_schemas import OperationsReadinessOut
req=urllib.request.Request('http://127.0.0.1:8002/api/v1/k10/operations/readiness',headers={'Authorization':'Bearer '+settings.api_token})
data=json.load(urllib.request.urlopen(req,timeout=10))
OperationsReadinessOut.model_validate(data)
print(json.dumps(data))
'''))
 receipt['postBackup']=h.snapshot(B/'neckline-post.db')
 release=ROOT/'releases/v3.1.0-b40';release.mkdir(exist_ok=False)
 for source in (wheel,P/'runtime-manifest.json',P/'Neckline-v3.1.0-b40-Backend.tar.gz'):
  shutil.copy2(source,release/source.name)
 receipt['status']='completed';receipt['completedAt']=datetime.now(timezone.utc).isoformat()
except Exception as exc:
 h.run(['systemctl','stop','neckline.service'],capture_output=True)
 copy_payload(B/'runtime',old['backendFiles'])
 for n in set(new['backendFiles'])-set(old['backendFiles']):
  (ROOT/n).unlink(missing_ok=True)
 shutil.copy2(B/'release-manifest.json',ROOT/'release-manifest.json');pip_install(oldwheel)
 h.run(['systemctl','start','neckline.service'],capture_output=True)
 receipt['status']='rolled_back';receipt['failureType']=type(exc).__name__;receipt['rollbackHealth']=h.wait_health('v3.1.0-b39')
 h.write_json(B/'receipt.json',receipt)
 raise
finally:
 for unit,state in prior_units.items():
  if state=='active' and unit not in h.SCAN_UNITS:
   h.run(['systemctl','start',unit],capture_output=True)
 h.write_json(B/'receipt.json',receipt)
print(json.dumps(receipt,ensure_ascii=False))
