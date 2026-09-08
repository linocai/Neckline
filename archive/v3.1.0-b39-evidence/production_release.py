"""B39 guarded server-local release. No scanner or paid worker is started."""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import sqlite3
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import zipfile

ROOT=Path("/opt/neckline")
DB=ROOT / "data/neckline.db"
RELEASE="v3.1.0-b39"
PRIOR_RELEASE="v3.0.4-b36"
PRIOR_COMMIT="6017a0c36bf1bbc6e842ef512ca66a29b7e7f607"
EXPECTED_HOST="ser657204219523"
PAYLOAD_ROOT=Path("/tmp/neckline-b39-release")
STAGE=Path("/tmp/neckline-b39-release/staged")
BACKUP=ROOT / "data/backups/v3.1.0-b39-predeploy-3"
TIMERS=["neckline-k10-evening.timer","neckline-k10-morning.timer","neckline-market-update.timer","neckline-market-retry.timer","neckline-backup.timer"]
WRITERS=["neckline.service","neckline-k10-worker.service","neckline-k10-evening.service","neckline-k10-morning.service","neckline-market-update.service","neckline-market-retry.service","neckline-backup.service"]
SCAN_UNITS=["neckline-k10-worker.service","neckline-k10-evening.timer","neckline-k10-morning.timer"]

def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, timeout=180, **kwargs)

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chown(path, _neckline().pw_uid, _neckline().pw_gid)
    os.chmod(path, 0o600)

def _neckline():
    return pwd.getpwnam("neckline")

def unit_state(name: str) -> str:
    return subprocess.run(["systemctl", "is-active", name], check=False, text=True,
                          capture_output=True).stdout.strip()

def nk(code: str, *, cwd: Path = ROOT) -> str:
    result = subprocess.run(
        ["sudo", "-n", "-u", "neckline", "env", "PYTHONDONTWRITEBYTECODE=1", str(ROOT / ".venv/bin/python"), "-"],
        cwd=cwd, input=code, text=True, capture_output=True, timeout=180,
    )
    if result.returncode:
        raise RuntimeError("server-side safe validation failed: " + result.stderr[-1200:])
    return result.stdout

def health(base: str = "http://127.0.0.1:8002") -> dict:
    with urllib.request.urlopen(base + "/api/v1/health", timeout=10) as response:
        return json.load(response)

def wait_health(release: str) -> dict:
    for _ in range(80):
        try:
            value = health()
            if value.get("releaseSet") == release:
                return value
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(0.25)
    raise RuntimeError("API did not reach the requested release set")

def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'

def fingerprint(path: Path) -> dict[str, object]:
    """Stable full-table digest used to prove migration preserves old rows."""
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        tables: dict[str, object] = {}
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall():
            info = conn.execute("PRAGMA table_info(" + _quoted(name) + ")").fetchall()
            columns = [str(row[1]) for row in info]
            keys = [str(row[1]) for row in sorted(info, key=lambda row: row[5]) if row[5]] or columns
            digest, count = hashlib.sha256(), 0
            for row in conn.execute("SELECT * FROM " + _quoted(name) + " ORDER BY " + ",".join(_quoted(key) for key in keys)):
                digest.update(repr(tuple(row)).encode("utf-8") + b"\n")
                count += 1
            tables[str(name)] = {
                "columns": columns, "keyColumns": keys, "count": count, "sha256": digest.hexdigest(),
            }
        migration_rows: dict[str, list[tuple[object, ...]]] = {}
        for name in ("k10_schema_migrations", "k10_notification_schema_migrations"):
            if name in tables:
                migration_rows[name] = [tuple(row) for row in conn.execute(
                    "SELECT * FROM " + _quoted(name) + " ORDER BY version"
                ).fetchall()]
        return {
            "schema": conn.execute("SELECT MAX(version) FROM k10_schema_migrations").fetchone()[0],
            "tables": tables, "migrationRows": migration_rows,
        }

def snapshot(path: Path) -> dict[str, str]:
    assert not path.exists()
    with closing(sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(path)) as destination:
            source.backup(destination)
    nk_user = _neckline()
    os.chown(path, nk_user.pw_uid, nk_user.pw_gid)
    os.chmod(path, 0o600)
    fingerprint(path)
    return {"path": str(path), "sha256": sha256(path)}

def install_payload(source: Path, root_mode: int) -> None:
    try:
        run([
            "rsync", "-a", "--delete", "--exclude=/data/", "--exclude=/.venv/", "--exclude=/.env",
            "--exclude=*.p8", "--exclude=/releases/", str(source) + "/", str(ROOT) + "/",
        ], capture_output=True)
    finally:
        os.chown(ROOT, 0, 0)
        os.chmod(ROOT, root_mode)
    for relative in ("neckline", "scripts", "deploy"):
        run(["chown", "-R", "deploy:neckline", str(ROOT / relative)])

def file_metadata(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"uid": stat.st_uid, "gid": stat.st_gid, "mode": stat.st_mode & 0o777}

def restore_file(source: Path, destination: Path, metadata: dict[str, int]) -> None:
    shutil.copy2(source, destination)
    os.chown(destination, metadata["uid"], metadata["gid"])
    os.chmod(destination, metadata["mode"])

def restore_runtime(source: Path, *, root_mode: int) -> None:
    """Restore the verified B36 tree without retaining newly introduced B39 files."""
    try:
        run([
            "rsync", "-a", "--delete", "--exclude=/data/", "--exclude=/.venv/", "--exclude=/.env",
            "--exclude=*.p8", "--exclude=/releases/", str(source) + "/", str(ROOT) + "/",
        ], capture_output=True)
    finally:
        os.chown(ROOT, 0, 0)
        os.chmod(ROOT, root_mode)
    for relative in ("neckline", "scripts", "deploy"):
        run(["chown", "-R", "deploy:neckline", str(ROOT / relative)])

def preserved(before, after_path):
    with closing(sqlite3.connect(after_path.as_uri()+'?mode=ro',uri=True)) as c:
        for name, info in before['tables'].items():
            if name in {'k10_schema_migrations','k10_notification_schema_migrations'}:
                prior=before['migrationRows'][name]
                now=[tuple(x) for x in c.execute('SELECT * FROM '+_quoted(name)+' ORDER BY version')]
                assert now[:len(prior)]==prior, name
                continue
            columns=[r[1] for r in c.execute('PRAGMA table_info('+_quoted(name)+')')]
            assert columns[:len(info['columns'])]==info['columns'], name
            q='SELECT '+','.join(_quoted(x) for x in info['columns'])+' FROM '+_quoted(name)+' ORDER BY '+','.join(_quoted(x) for x in info['keyColumns'])
            digest=hashlib.sha256(); count=0
            for row in c.execute(q): digest.update(repr(tuple(row)).encode()+b'\n'); count+=1
            assert count==info['count'] and digest.hexdigest()==info['sha256'], name

def migrate_schema(path,payload):
    return json.loads(nk(f'''
from pathlib import Path
import json
from neckline.k10.schema import initialize_schema,schema_version
from neckline.k10.notifications import initialize_notifications_schema
p=Path({str(path)!r})
assert initialize_schema(p)==7
assert initialize_notifications_schema(p)==2
print(json.dumps({{"schema":schema_version(p),"notificationSchema":2}}))
''',cwd=payload))

def configure(path,payload,expected_strategy,expected_execution):
    return json.loads(nk(f'''
from pathlib import Path
from datetime import datetime,timezone
import json
from neckline.k10.cli import configure,configure_execution
from neckline.k10 import store
p=Path({str(path)!r}); now=datetime.now(timezone.utc)
a=configure(db_path=p,config_id="k10-v1.4-production",file_path=Path({str(payload/'neckline/config/k10-v1.4.json')!r}),now=now)
b=configure_execution(db_path=p,config_id="k10-execution-production",file_path=Path({str(payload/'neckline/config/k10-execution-v3.json')!r}),now=now)
assert a[1]=={expected_strategy} and b[1]=={expected_execution}
store.set_run_control(state="closed",reason_code="user_paused_pending_validation",changed_at=now.isoformat(),changed_by="release-b39",db_path=p)
assert store.run_control_status(db_path=p)["state"]=="closed"
print(json.dumps({{"strategyRevision":a[1],"executionRevision":b[1],"runControl":"closed"}}))
''',cwd=payload))

def assert_no_scan_readiness(payload):
    return json.loads(nk('''
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime,timezone
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neckline.api.k10 import create_router
from neckline.api.k10_schemas import ConfigurationOut
from neckline.k10.schema import initialize_schema
from neckline.k10.cli import configure,configure_execution
with TemporaryDirectory(prefix="b39-no-scan-") as folder:
 p=Path(folder)/"isolated.sqlite"; initialize_schema(p); now=datetime.now(timezone.utc)
 a=configure(db_path=p,config_id="bound",file_path=Path("neckline/config/k10-v1.4.json"),now=now)
 b=configure_execution(db_path=p,config_id="bound-execution",file_path=Path("neckline/config/k10-execution-v3.json"),now=now)
 app=FastAPI();app.include_router(create_router(lambda:p,lambda:None,lambda:Path(folder),lambda:(a[0],a[1],None),lambda:(b[0],b[1],None)))
 with TestClient(app) as client:
  r=client.get("/api/v1/k10/configuration"); assert r.status_code==200
  value=ConfigurationOut.model_validate(r.json())
  assert value.configId=="bound" and value.configRevision==1
  assert len(value.scopes)==4 and all(s.state=="configured" and not s.errors and not s.missing for s in value.scopes)
 print(json.dumps({"noScan":True,"explicitBindings":True,"configuredScopes":len(value.scopes)}))
''',cwd=payload))

def replace_binding(binding):
    updates={'K10_CONFIG_ID':'k10-v1.4-production','K10_CONFIG_REVISION':'3','K10_EXECUTION_CONFIG_ID':'k10-execution-production','K10_EXECUTION_CONFIG_REVISION':'2'}
    lines=[]; seen=set(); info=file_metadata(binding)
    for line in binding.read_text().splitlines():
        key=line.split('=',1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else None
        if key in updates:
            assert key not in seen; seen.add(key); line=key+'='+updates[key]
        lines.append(line)
    assert seen==set(updates)
    new=binding.with_suffix('.b39');new.write_text('\n'.join(lines)+'\n')
    os.chown(new,info['uid'],info['gid']);os.chmod(new,info['mode']);os.replace(new,binding)

def api_validation():
    return json.loads(nk('''
import json,urllib.request,urllib.error
from neckline.config import settings
from neckline.api.k10_schemas import ConfigurationOut,OperationsReadinessOut,OpportunityListOut,PublicationListOut,SelectionListOut,MorningReportListOut
headers={"Authorization":"Bearer "+settings.api_token}
def get(path):
 req=urllib.request.Request("http://127.0.0.1:8002/api/v1/k10"+path,headers=headers)
 return json.load(urllib.request.urlopen(req,timeout=20))
c=ConfigurationOut.model_validate(get("/configuration"))
assert (c.configId,c.configRevision)==("k10-v1.4-production",3)
assert len(c.scopes)==4 and all(s.state=="configured" and not s.errors and not s.missing for s in c.scopes)
r=OperationsReadinessOut.model_validate(get("/operations/readiness"))
assert r.runControl.state=="paused", "unexpected run-control API state: "+r.runControl.state
assert r.notificationReadiness.state=="ready", "APNs readiness: "+r.notificationReadiness.state+"/"+str(r.notificationReadiness.reasonCode)
counts={}
for path,cls in [("/opportunities",OpportunityListOut),("/publications",PublicationListOut),("/selections",SelectionListOut),("/morning-reports",MorningReportListOut)]:
 v=cls.model_validate(get(path));counts[path]=len(v.items)
try: urllib.request.urlopen("http://127.0.0.1:8002/api/v1/k10/configuration",timeout=10)
except urllib.error.HTTPError as e: assert e.code==401
else: raise AssertionError("unauthenticated request accepted")
print(json.dumps({"configuredScopes":4,"strategyRevision":3,"executionRevision":2,"runControl":"closed","runControlApi":"paused","apnsState":"ready","unauthenticatedStatus":401,"readCounts":counts}))
'''))

def restore_prior_states(states):
    # Discovery stays disabled; only restore unrelated previously-active market timers.
    for name in TIMERS:
        if name not in SCAN_UNITS and states.get(name)=='active': run(['systemctl','start',name])
    assert all(unit_state(name)!='active' for name in SCAN_UNITS)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    assert args.execute and os.geteuid()==0 and socket.gethostname()==EXPECTED_HOST
    assert DB.resolve()==DB and ROOT.resolve()==ROOT
    owner=_neckline(); root_meta=file_metadata(ROOT); db_meta=file_metadata(DB)
    assert root_meta=={'uid':0,'gid':0,'mode':493} and db_meta['uid']==owner.pw_uid
    assert not BACKUP.exists() and not STAGE.exists() and not (ROOT/'releases'/RELEASE).exists()
    assert all(unit_state(n)!='active' for n in SCAN_UNITS)
    assert all(subprocess.run(['systemctl','is-enabled',n],capture_output=True,text=True).stdout.strip()=='disabled' for n in SCAN_UNITS)
    prior=json.loads((ROOT/'release-manifest.json').read_text())
    assert prior['releaseSet']==PRIOR_RELEASE and prior['commit']==PRIOR_COMMIT
    for name,digest in prior['backendFiles'].items(): assert sha256(ROOT/name)==digest, 'runtime drift: '+name
    assert health()['releaseSet']==PRIOR_RELEASE
    manifest=json.loads((PAYLOAD_ROOT/'runtime-manifest.json').read_text())
    archive=PAYLOAD_ROOT/'Neckline-v3.1.0-b39-Backend.tar.gz';wheel=PAYLOAD_ROOT/'wheels/neckline_runtime-3.1.0-py3-none-any.whl'
    assert manifest['releaseSet']==RELEASE and sha256(archive)==manifest['archiveSha256'] and sha256(wheel)==manifest['wheelSha256']
    old_wheel=ROOT/'releases'/PRIOR_RELEASE/'neckline_runtime-3.0.4-py3-none-any.whl'
    assert sha256(old_wheel)==prior['wheelSha256']
    STAGE.mkdir(mode=0o755)
    with tarfile.open(archive) as tar: tar.extractall(STAGE,filter='data')
    payload=STAGE/'Backend'
    for name,digest in manifest['backendFiles'].items(): assert sha256(payload/name)==digest,name
    # Validate actual packaged imports/config files outside the repository before replacing production.
    wheel_check=PAYLOAD_ROOT/'installed-check'
    run([str(ROOT/'.venv/bin/python'),'-m','pip','install','--no-index','--no-deps','--target',str(wheel_check),str(wheel)],capture_output=True)
    for name,digest in manifest['backendFiles'].items():
        if name.startswith('neckline/') and name.endswith(('.py','.json')): assert sha256(wheel_check/name)==digest,name
    receipt={'releaseSet':RELEASE,'commit':manifest['commit'],'priorRelease':PRIOR_RELEASE,'status':'prepared','rootMetadata':root_meta,'dbMetadata':db_meta,'newPaidCalls':0,'discoveryResumed':False}
    receipt['noScanReadiness']=assert_no_scan_readiness(payload)
    BACKUP.mkdir(mode=0o700);os.chown(BACKUP,owner.pw_uid,owner.pw_gid)
    binding=Path('/etc/neckline/k10.env'); binding_meta=file_metadata(binding)
    shutil.copy2(binding,BACKUP/'k10.env');os.chmod(BACKUP/'k10.env',0o600)
    # Runtime archive excludes credentials, all databases and environment state.
    run(['tar','-czf',str(BACKUP/'runtime.tar.gz'),'--exclude=./data','--exclude=./.venv','--exclude=./.env','--exclude=*.p8','--exclude=./releases','--exclude=*/__pycache__','--exclude=./.pytest_cache','-C',str(ROOT),'.'],capture_output=True)
    os.chmod(BACKUP/'runtime.tar.gz',0o600)
    restore_tree=BACKUP/'runtime';restore_tree.mkdir(mode=0o755)
    with tarfile.open(BACKUP/'runtime.tar.gz') as tar:tar.extractall(restore_tree,filter='data')
    for name,digest in prior['backendFiles'].items():assert sha256(restore_tree/name)==digest,name
    states={n:unit_state(n) for n in TIMERS+WRITERS};receipt['preStates']=states
    receipt['bindingMetadata']=binding_meta;receipt['runtimeBackupSha256']=sha256(BACKUP/'runtime.tar.gz')
    write_json(BACKUP/'receipt.json',receipt)
    changed=False;api_started=False;published_fingerprint=None
    try:
        run(['systemctl','stop',*TIMERS]);run(['systemctl','stop',*WRITERS])
        assert all(unit_state(n)!='active' for n in TIMERS+WRITERS)
        with closing(sqlite3.connect(DB)) as conn:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)');conn.execute('PRAGMA journal_mode=DELETE')
        assert not any(Path(str(DB)+s).exists() for s in ['-wal','-shm','-journal'])
        before=fingerprint(DB);assert before['schema']==4
        receipt['baseline']=before;receipt['preDatabase']=snapshot(BACKUP/'neckline-pre.db')
        rehearsal=BACKUP/'rehearsal.sqlite';shutil.copy2(BACKUP/'neckline-pre.db',rehearsal);os.chown(rehearsal,owner.pw_uid,owner.pw_gid)
        receipt['rehearsalMigration']=migrate_schema(rehearsal,payload)
        preserved(before,rehearsal)
        receipt['oldTablesPreserved']=len(before['tables'])
        receipt['rehearsalConfiguration']=configure(rehearsal,payload,3,2)
        assert fingerprint(rehearsal)['schema']==7
        # Rehearsal restore proves this exact B36 snapshot and package remain usable.
        restored=BACKUP/'restore-check.sqlite';shutil.copy2(rehearsal,restored)
        with closing(sqlite3.connect(BACKUP/'neckline-pre.db')) as source,closing(sqlite3.connect(restored)) as dest: source.backup(dest)
        assert fingerprint(restored)==before
        receipt['rehearsalRestoreVerified']=True
        assert fingerprint(DB)==before, 'production changed while stopped'
        install_payload(payload,root_meta['mode']);changed=True
        run([str(ROOT/'.venv/bin/python'),'-m','pip','install','--no-index','--no-deps','--force-reinstall',str(wheel)],capture_output=True)
        receipt['migration']=migrate_schema(DB,ROOT);preserved(before,DB)
        receipt['configuration']=configure(DB,ROOT,3,2);replace_binding(binding)
        receipt['postDatabase']=snapshot(BACKUP/'neckline-post.db');published_fingerprint=fingerprint(DB)
        (ROOT/'release-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n');os.chmod(ROOT/'release-manifest.json',0o644)
        for name,digest in manifest['backendFiles'].items(): assert sha256(ROOT/name)==digest,name
        assert file_metadata(ROOT)==root_meta and file_metadata(DB)==db_meta
        # No unit files changed in the B36->B39 delta. Preserve scan disablement.
        api_started=True;run(['systemctl','start','neckline.service'])
        receipt['health']=wait_health(RELEASE);receipt['api']=api_validation()
        assert health('https://nk.linotsai.top')['releaseSet']==RELEASE
        assert fingerprint(DB)==published_fingerprint, 'readiness probes unexpectedly wrote business state'
        assert all(unit_state(n)!='active' for n in SCAN_UNITS)
        assert all(subprocess.run(['systemctl','is-enabled',n],capture_output=True,text=True).stdout.strip()=='disabled' for n in SCAN_UNITS)
        release_dir=ROOT/'releases'/RELEASE;release_dir.mkdir(mode=0o755)
        for src in [archive,wheel,PAYLOAD_ROOT/'runtime-manifest.json']:
            shutil.copy2(src,release_dir/src.name);os.chmod(release_dir/src.name,0o644)
        restore_prior_states(states)
        receipt.update(status='completed',schema=7,notificationSchema=2,discoveryResumed=False,existingDataPreserved=True)
        write_json(BACKUP/'receipt.json',receipt)
        print(json.dumps({k:receipt[k] for k in ['status','releaseSet','commit','schema','oldTablesPreserved','rehearsalRestoreVerified','api','discoveryResumed','newPaidCalls']}))
    except Exception as error:
        run(['systemctl','stop',*TIMERS]);run(['systemctl','stop',*WRITERS])
        receipt['failureType']=type(error).__name__;receipt['status']='failed'
        can_restore=not api_started or (published_fingerprint is not None and fingerprint(DB)==published_fingerprint)
        if changed and can_restore:
            restore_runtime(restore_tree,root_mode=root_meta['mode'])
            run([str(ROOT/'.venv/bin/python'),'-m','pip','install','--no-index','--no-deps','--force-reinstall',str(old_wheel)],capture_output=True)
            with closing(sqlite3.connect(BACKUP/'neckline-pre.db')) as source,closing(sqlite3.connect(DB)) as dest:source.backup(dest)
            os.chown(DB,db_meta['uid'],db_meta['gid']);os.chmod(DB,db_meta['mode'])
            restore_file(BACKUP/'k10.env',binding,binding_meta)
            assert fingerprint(DB)==before
            receipt['rollback']='verified B36 runtime/database/binding restored';receipt['status']='rolled_back'
        elif changed:
            receipt['rollback']='forward repair required; potential post-start writes preserved'
        else:receipt['rollback']='no production runtime/database changed'
        if not changed or can_restore:
            run(['systemctl','start','neckline.service']);wait_health(PRIOR_RELEASE);restore_prior_states(states)
        write_json(BACKUP/'receipt.json',receipt)
        raise
    finally:
        for path in [STAGE,wheel_check,restore_tree]:
            if path.exists():shutil.rmtree(path)
        for name in ['rehearsal.sqlite','restore-check.sqlite']:
            for suffix in ['', '-wal', '-shm']:(BACKUP/(name+suffix)).unlink(missing_ok=True)

if __name__=='__main__':main()
