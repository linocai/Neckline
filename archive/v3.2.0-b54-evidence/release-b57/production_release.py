"""One-shot B53 -> B57 deployment. Explicit phases; paid work remains paused.

Requires the archived B39 helper beside this file as release_helpers.py.
It is imported for snapshot/fingerprint/file operations only, never its main().
"""
from pathlib import Path
import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import tarfile
import release_helpers as h

ROOT = Path('/opt/neckline')
DB = ROOT/'data/neckline.db'
PAYLOAD = Path('/tmp/neckline-b57-release')
STAGE = PAYLOAD/'staged'
INPUTS = PAYLOAD/'inputs'
BACKUP = ROOT/'data/backups/v3.2.0-b57-predeploy'
RELEASE = 'v3.2.0-b57'
BINDING = Path('/etc/neckline/k10.env')
h.ROOT, h.DB, h.RELEASE, h.BACKUP = ROOT, DB, RELEASE, BACKUP


def assert_target():
    assert os.geteuid() == 0 and socket.gethostname() == 'ser657204219523'
    assert ROOT.resolve() == ROOT and DB.resolve() == DB
    assert h.file_metadata(ROOT) == {'uid': 0, 'gid': 0, 'mode': 0o755}
    user = h._neckline()
    assert h.file_metadata(DB) == {'uid': user.pw_uid, 'gid': user.pw_gid, 'mode': 0o600}


def paused():
    assert all(h.unit_state(name) == 'inactive' for name in h.TIMERS + h.WRITERS)
    assert all(subprocess.run(['systemctl','is-enabled',name],capture_output=True,text=True).stdout.strip() == 'disabled'
               for name in h.TIMERS + ['neckline.service','neckline-k10-worker.service'])
    with sqlite3.connect(DB.as_uri()+'?mode=ro',uri=True) as conn:
        assert conn.execute("SELECT state FROM k10_run_controls WHERE control_key='k10_discovery'").fetchone() == ('closed',)


def manifest_check(root, manifest):
    for name, digest in manifest['backendFiles'].items():
        assert h.sha256(root/name) == digest, 'runtime drift: '+name


def local_python(args, cwd):
    result = subprocess.run(['sudo','-n','-u','neckline','env','PYTHON_DOTENV_DISABLED=1',
        'PYTHONDONTWRITEBYTECODE=1','PYTHONPATH='+str(cwd),str(ROOT/'.venv/bin/python'),*args],
        cwd=cwd, text=True, capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError(result.stderr[-2200:])
    return result.stdout


def migrate(db, cwd):
    local_python(['-c', 'from pathlib import Path; from neckline.k10.schema import initialize_schema; '
                  'assert initialize_schema(Path('+repr(str(db))+')) == 8'], cwd)


def configure(db, cwd):
    return json.loads(local_python([str(PAYLOAD/'configure_release.py'),'--db',str(db),'--inputs',str(INPUTS)],cwd))


def preserve_history(before):
    # Configuration appends are allowed; all old business rows must stay identical.
    business = dict(before)
    business['tables'] = {k:v for k,v in before['tables'].items() if k not in {
        'k10_run_config_revisions','k10_execution_config_revisions','k10_title_triage_policy_revisions'}}
    h.preserved(business, DB)
    with sqlite3.connect(DB) as conn:
        conn.execute('ATTACH DATABASE ? AS prior',(str(BACKUP/'neckline-pre.db'),))
        for name in before['tables']:
            columns = ','.join(h._quoted(c) for c in before['tables'][name]['columns'])
            assert conn.execute('SELECT '+columns+' FROM prior.'+h._quoted(name)+
                ' EXCEPT SELECT '+columns+' FROM main.'+h._quoted(name)).fetchone() is None, name


def bind(config):
    updates = {'K10_CONFIG_ID':config['strategy']['configId'], 'K10_CONFIG_REVISION':str(config['strategy']['revision']),
               'K10_EXECUTION_CONFIG_ID':config['execution']['configId'],
               'K10_EXECUTION_CONFIG_REVISION':str(config['execution']['revision'])}
    lines, seen = [], set()
    for line in BINDING.read_text().splitlines():
        key = line.split('=',1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else None
        if key in updates:
            assert key not in seen
            seen.add(key)
            line = key+'='+updates[key]
        lines.append(line)
    assert seen == set(updates)
    meta = h.file_metadata(BINDING)
    temp = BINDING.with_suffix('.b57')
    temp.write_text('\n'.join(lines)+'\n')
    os.chown(temp,meta['uid'],meta['gid']);os.chmod(temp,meta['mode']);os.replace(temp,BINDING)


def validate_api(config):
    code = '''
import json,urllib.request,urllib.error
from neckline.config import settings
from neckline.api.k10_schemas import ConfigurationOut,V2ReportEnvelope,ResultsOut,OpportunityListOut
headers={'Authorization':'Bearer '+settings.api_token}
def get(path):
 return json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8002/api/v1/k10'+path,headers=headers),timeout=20))
c=ConfigurationOut.model_validate(get('/configuration'))
assert c.configId == 'k10-v2-production' and c.executionConfigId == 'k10-v2-execution-production'
assert c.strategyVersion=='K10-v2' and c.runControl.state=='paused'
assert len(c.scopes)==4 and all(s.state=='configured' and not s.errors and not s.missing for s in c.scopes)
assert c.profileReviewStatus=='local_draft_awaiting_user'
out={'configuration':c.model_dump(mode='json')}
for window in ('evening','morning'):
 value=V2ReportEnvelope.model_validate(get('/v2/reports/latest?window='+window))
 assert value.state=='empty'
 out[window]=value.model_dump(mode='json')
out['results']=ResultsOut.model_validate(get('/results')).model_dump(mode='json')
out['opportunities']=OpportunityListOut.model_validate(get('/opportunities')).model_dump(mode='json')
try: urllib.request.urlopen('http://127.0.0.1:8002/api/v1/k10/configuration',timeout=10)
except urllib.error.HTTPError as e: assert e.code==401
else: raise AssertionError('Unauthenticated request accepted')
print(json.dumps(out,ensure_ascii=False))
'''
    # Only local authenticated reads. No readiness calls that examine providers.
    out = json.loads(h.nk(code))
    assert out['configuration']['configRevision'] == config['strategy']['revision']
    assert out['configuration']['executionConfigRevision'] == config['execution']['revision']
    h.write_json(BACKUP/'api-responses.json',out)
    return {'configuredScopes':4,'strategy':'K10-v2','runControl':'paused','unauthenticatedStatus':401,
            'evening':out['evening']['state'],'morning':out['morning']['state']}


def prepare():
    assert_target();paused()
    prior = json.loads((ROOT/'release-manifest.json').read_text())
    assert prior['releaseSet']=='v3.1.0-b53' and prior['commit']=='76c0a9f97937065ed17a5e6f64f4ee8829bbb756'
    manifest_check(ROOT,prior)
    manifest = json.loads((PAYLOAD/'runtime-manifest.json').read_text())
    assert manifest['releaseSet']==RELEASE and h.sha256(PAYLOAD/'Neckline-v3.2.0-b57-Backend.tar.gz')==manifest['archiveSha256']
    assert h.sha256(PAYLOAD/manifest['wheelFile'])==manifest['wheelSha256']
    assert not BACKUP.exists() and not STAGE.exists()
    STAGE.mkdir(mode=0o755)
    with tarfile.open(PAYLOAD/'Neckline-v3.2.0-b57-Backend.tar.gz') as tar:tar.extractall(STAGE,filter='data')
    manifest_check(STAGE,manifest)
    INPUTS.mkdir(mode=0o750)
    with tarfile.open(PAYLOAD/'company-inputs.tar.gz') as tar:tar.extractall(INPUTS,filter='data')
    h.run(['chown','-R','neckline:neckline',str(INPUTS)])
    BACKUP.mkdir(mode=0o700);os.chown(BACKUP,h._neckline().pw_uid,h._neckline().pw_gid)
    shutil.copy2(BINDING,BACKUP/'k10.env')
    restore = BACKUP/'runtime';restore.mkdir(mode=0o755)
    for name in [*prior['backendFiles'],'release-manifest.json']:
        dest=restore/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,dest)
    pre=h.snapshot(BACKUP/'neckline-pre.db');before=h.fingerprint(DB)
    assert before['schema']==7
    rehearsal=BACKUP/'rehearsal.sqlite';shutil.copy2(BACKUP/'neckline-pre.db',rehearsal)
    os.chown(rehearsal,h._neckline().pw_uid,h._neckline().pw_gid)
    migrate(rehearsal,STAGE);h.preserved(before,rehearsal)
    config=configure(rehearsal,STAGE)
    assert configure(rehearsal,STAGE)['strategy']==config['strategy']
    restored=BACKUP/'restore-check.sqlite';shutil.copy2(rehearsal,restored)
    with sqlite3.connect(BACKUP/'neckline-pre.db') as source,sqlite3.connect(restored) as dest:source.backup(dest)
    assert h.fingerprint(restored)==before and h.fingerprint(DB)==before
    receipt={'status':'prepared','releaseSet':RELEASE,'commit':manifest['commit'],'baseline':before,
        'preDatabase':pre,'configuration':config,'oldTablesPreserved':len(before['tables']),
        'rehearsalRestoreVerified':True,'rootMetadata':h.file_metadata(ROOT),'dbMetadata':h.file_metadata(DB),
        'bindingMetadata':h.file_metadata(BINDING),'newPaidCalls':0}
    h.write_json(BACKUP/'receipt.json',receipt)
    print(json.dumps({k:v for k,v in receipt.items() if k!='baseline'}))


def deploy():
    assert_target();paused()
    receipt=json.loads((BACKUP/'receipt.json').read_text());assert receipt['status']=='prepared'
    before=h.fingerprint(BACKUP/'neckline-pre.db');assert h.fingerprint(DB)==before
    manifest=json.loads((PAYLOAD/'runtime-manifest.json').read_text())
    manifest_check(ROOT,json.loads((BACKUP/'runtime/release-manifest.json').read_text()))
    changed=False;post=None
    try:
        changed=True;h.install_payload(STAGE,0o755)
        h.run([str(ROOT/'.venv/bin/python'),'-m','pip','install','--no-index','--no-deps','--force-reinstall',str(PAYLOAD/manifest['wheelFile'])],capture_output=True)
        migrate(DB,ROOT);h.preserved(before,DB)
        receipt['configuration']=configure(DB,ROOT);preserve_history(before)
        bind(receipt['configuration'])
        (ROOT/'release-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        os.chmod(ROOT/'release-manifest.json',0o644);manifest_check(ROOT,manifest);assert_target()
        receipt['postDatabase']=h.snapshot(BACKUP/'neckline-post.db');post=h.fingerprint(DB)
        h.run(['systemctl','start','neckline.service'])
        receipt['health']=h.wait_health(RELEASE)
        receipt['api']=validate_api(receipt['configuration'])
        assert h.health('https://nk.linotsai.top')['releaseSet']==RELEASE
        assert h.fingerprint(DB)==post, 'Read-only API validation changed the database'
        assert all(h.unit_state(n)=='inactive' for n in h.TIMERS+['neckline-k10-worker.service'])
        release_dir=ROOT/'releases'/RELEASE;release_dir.mkdir(mode=0o755)
        for name in ['Neckline-v3.2.0-b57-Backend.tar.gz',manifest['wheelFile'],'runtime-manifest.json']:
            shutil.copy2(PAYLOAD/name,release_dir/name);os.chmod(release_dir/name,0o644)
        h.run(['systemctl','enable','neckline.service'],capture_output=True)
        receipt.update(status='completed',schema=8,notificationSchema=2,discoveryResumed=False,apiActive=True)
    except Exception as exc:
        h.run(['systemctl','stop','neckline.service'])
        receipt.update(status='failed',failureType=type(exc).__name__,failure=str(exc)[-1200:])
        if changed and (post is None or h.fingerprint(DB)==post):
            h.restore_runtime(BACKUP/'runtime',root_mode=0o755)
            old=ROOT/'releases/v3.1.0-b53/neckline_runtime-3.1.0-py3-none-any.whl'
            h.run([str(ROOT/'.venv/bin/python'),'-m','pip','install','--no-index','--no-deps','--force-reinstall',str(old)],capture_output=True)
            with sqlite3.connect(BACKUP/'neckline-pre.db') as source,sqlite3.connect(DB) as dest:source.backup(dest)
            h.restore_file(BACKUP/'k10.env',BINDING,receipt['bindingMetadata'])
            assert h.fingerprint(DB)==before;assert_target()
            receipt.update(status='rolled_back',rollback='B53 source, wheel, database and binding restored; all services paused')
        h.write_json(BACKUP/'receipt.json',receipt)
        raise
    h.write_json(BACKUP/'receipt.json',receipt)
    print(json.dumps({k:v for k,v in receipt.items() if k!='baseline'}))


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','deploy'])
    args=parser.parse_args()
    (prepare if args.phase=='prepare' else deploy)()
