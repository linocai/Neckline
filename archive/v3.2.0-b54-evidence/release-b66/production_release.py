"""One-shot, code-only B65 -> B66 cutover. No migration or provider call.

Imports the archived B39 helper as release_helpers.py for verified file/snapshot
operations only; its historical main() is never invoked. Run prepare then deploy.
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
import zipfile
import release_helpers as h

ROOT = Path('/opt/neckline')
DB = ROOT / 'data/neckline.db'
RELEASE = 'v3.2.0-b66'
PRIOR = 'v3.2.0-b65'
PRIOR_COMMIT = '49a2974bebcaaad801b69af76768f6ed4f694708'
PAYLOAD = Path('/tmp/neckline-b66-release')
STAGE = PAYLOAD / 'staged'
BACKUP = ROOT / 'data/backups/v3.2.0-b66-predeploy'
BINDING = Path('/etc/neckline/k10.env')
ARCHIVE = 'Neckline-v3.2.0-b66-Backend.tar.gz'
h.ROOT, h.DB, h.RELEASE, h.BACKUP = ROOT, DB, RELEASE, BACKUP

def assert_target():
    assert os.geteuid() == 0 and socket.gethostname() == 'ser657204219523'
    assert ROOT.resolve() == ROOT and DB.resolve() == DB
    assert h.file_metadata(ROOT) == {'uid': 0, 'gid': 0, 'mode': 0o755}
    user = h._neckline()
    assert h.file_metadata(DB) == {'uid': user.pw_uid, 'gid': user.pw_gid, 'mode': 0o600}

def paused(api_active):
    assert h.unit_state('neckline-trial-20260910-evening-b65.service') == 'inactive'
    assert h.unit_state('neckline-trial-20260910-evening-b64.service') == 'inactive'
    assert h.unit_state('neckline-trial-20260910-evening-b63.service') == 'inactive'
    assert h.unit_state('neckline-trial-20260910-evening-b62.service') == 'inactive'
    assert h.unit_state('neckline-trial-20260910-evening-b61.service') == 'inactive'
    assert h.unit_state('neckline-trial-20260910-evening-b60.service') == 'inactive'
    assert h.unit_state('neckline-trial-20260910-evening.service') == 'inactive'
    assert all(h.unit_state(n) == 'inactive' for n in h.TIMERS + [n for n in h.WRITERS if n != 'neckline.service'])
    for name in h.TIMERS + ['neckline-k10-worker.service']:
        assert subprocess.run(['systemctl', 'is-enabled', name], capture_output=True, text=True).stdout.strip() == 'disabled'
    assert h.unit_state('neckline.service') == ('active' if api_active else 'inactive')
    assert subprocess.run(['systemctl', 'is-enabled', 'neckline.service'], capture_output=True, text=True).stdout.strip() == 'enabled'
    with sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True) as conn:
        assert conn.execute("SELECT state FROM k10_run_controls WHERE control_key='k10_discovery'").fetchone() == ('open',)

def manifest_check(root, manifest):
    for name, digest in manifest['backendFiles'].items():
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        assert h.sha256(root / name) == digest, 'Runtime drift: ' + name

def validate(mode, db, cwd):
    result = subprocess.run(['sudo', '-n', '-u', 'neckline', 'env', 'PYTHON_DOTENV_DISABLED=1',
        'PYTHONDONTWRITEBYTECODE=1', 'PYTHONPATH=' + str(cwd), str(ROOT / '.venv/bin/python'),
        str(PAYLOAD / 'validate_api.py'), mode, '--db', str(db), '--output', str(BACKUP / (mode + '-api.json'))],
        cwd=cwd, text=True, capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError('Read-only API validation failed: ' + result.stderr[-1800:])
    return json.loads(result.stdout)

def same_state(before):
    assert h.fingerprint(DB) == before, 'Database changed'
    assert h.sha256(BINDING) == h.sha256(BACKUP / 'k10.env'), 'Binding changed'
    assert h.sha256(ROOT / '.env') == h.sha256(BACKUP / 'runtime.env'), 'Environment changed'
    assert_target()

def prepare():
    assert_target(); paused(True)
    prior = json.loads((ROOT / 'release-manifest.json').read_text())
    assert prior['releaseSet'] == PRIOR and prior['commit'] == PRIOR_COMMIT
    manifest_check(ROOT, prior)
    old_wheel = ROOT / 'releases' / PRIOR / prior['wheelFile']
    assert h.sha256(old_wheel) == prior['wheelSha256']
    manifest = json.loads((PAYLOAD / 'runtime-manifest.json').read_text())
    assert manifest['releaseSet'] == RELEASE and manifest['schema'] == 8
    assert h.sha256(PAYLOAD / ARCHIVE) == manifest['archiveSha256']
    assert h.sha256(PAYLOAD / manifest['wheelFile']) == manifest['wheelSha256']
    assert not BACKUP.exists() and not STAGE.exists() and not (ROOT / 'releases' / RELEASE).exists()
    STAGE.mkdir(mode=0o755)
    with tarfile.open(PAYLOAD / ARCHIVE) as tar:
        tar.extractall(STAGE, filter='data')
    manifest_check(STAGE, manifest)
    with zipfile.ZipFile(PAYLOAD / manifest['wheelFile']) as wheel:
        expected = {n for n in manifest['backendFiles'] if n.startswith('neckline/')}
        assert {n for n in wheel.namelist() if n.startswith('neckline/')} == expected
        for name in expected:
            assert wheel.read(name) == (STAGE / name).read_bytes(), name
    h.run(['systemctl', 'stop', 'neckline.service'])
    try:
        paused(False)
        BACKUP.mkdir(mode=0o700)
        os.chown(BACKUP, h._neckline().pw_uid, h._neckline().pw_gid)
        for src, name in [(BINDING, 'k10.env'), (ROOT / '.env', 'runtime.env')]:
            shutil.copy2(src, BACKUP / name); os.chmod(BACKUP / name, 0o600)
        restore = BACKUP / 'runtime'; restore.mkdir(mode=0o755)
        for name in [*prior['backendFiles'], 'release-manifest.json']:
            dest = restore / name; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, dest)
        pre = h.snapshot(BACKUP / 'neckline-pre.db')
        before = h.fingerprint(DB)
        assert before['schema'] == 8 and before == h.fingerprint(BACKUP / 'neckline-pre.db')
        rehearsal = BACKUP / 'rehearsal.sqlite'
        shutil.copy2(BACKUP / 'neckline-pre.db', rehearsal)
        os.chown(rehearsal, h._neckline().pw_uid, h._neckline().pw_gid)
        api = validate('offline', rehearsal, STAGE)
        assert h.fingerprint(rehearsal) == before
        # Verify the standard SQLite restore mechanism on an isolated destination.
        restored = BACKUP / 'restore-check.sqlite'
        with sqlite3.connect(BACKUP / 'neckline-pre.db') as source, sqlite3.connect(restored) as dest:
            source.backup(dest)
        assert h.fingerprint(restored) == before
        same_state(before)
        receipt = {'status': 'prepared', 'releaseSet': RELEASE, 'commit': manifest['commit'],
            'priorRelease': PRIOR, 'priorCommit': PRIOR_COMMIT, 'preDatabase': pre,
            'schema': 8, 'notificationSchema': 2, 'tablesPreserved': len(before['tables']),
            'offlineAPI': api, 'isolatedRestoreVerified': True, 'newPaidCalls': 0,
            'bindingUnchanged': True, 'rootMetadata': h.file_metadata(ROOT), 'dbMetadata': h.file_metadata(DB)}
        h.write_json(BACKUP / 'receipt.json', receipt)
    finally:
        h.run(['systemctl', 'start', 'neckline.service'])
        h.wait_health(PRIOR)
    paused(True)
    print(json.dumps(receipt))

def deploy():
    assert_target(); paused(True)
    receipt = json.loads((BACKUP / 'receipt.json').read_text())
    assert receipt['status'] == 'prepared'
    before = h.fingerprint(BACKUP / 'neckline-pre.db'); same_state(before)
    prior = json.loads((BACKUP / 'runtime/release-manifest.json').read_text())
    manifest_check(ROOT, prior)
    manifest = json.loads((PAYLOAD / 'runtime-manifest.json').read_text())
    assert manifest['commit'] == receipt['commit']
    manifest_check(STAGE, manifest)
    assert h.sha256(PAYLOAD / manifest['wheelFile']) == manifest['wheelSha256']
    changed = False
    h.run(['systemctl', 'stop', 'neckline.service'])
    try:
        paused(False); same_state(before)
        changed = True
        h.install_payload(STAGE, 0o755)
        h.run([str(ROOT / '.venv/bin/python'), '-m', 'pip', 'install', '--no-index', '--no-deps',
            '--force-reinstall', str(PAYLOAD / manifest['wheelFile'])], capture_output=True)
        (ROOT / 'release-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        os.chmod(ROOT / 'release-manifest.json', 0o644)
        manifest_check(ROOT, manifest); same_state(before)
        h.run(['systemctl', 'start', 'neckline.service'])
        receipt['health'] = h.wait_health(RELEASE)
        receipt['api'] = validate('live', DB, ROOT)
        assert h.health('https://nk.linotsai.top')['releaseSet'] == RELEASE
        same_state(before); paused(True)
        receipt['postDatabase'] = h.snapshot(BACKUP / 'neckline-post.db')
        assert h.fingerprint(BACKUP / 'neckline-post.db') == before
        release_dir = ROOT / 'releases' / RELEASE; release_dir.mkdir(mode=0o755)
        for name in [ARCHIVE, manifest['wheelFile'], 'runtime-manifest.json']:
            shutil.copy2(PAYLOAD / name, release_dir / name); os.chmod(release_dir / name, 0o644)
        receipt.update(status='completed', apiActive=True, apiEnabled=True, discoveryResumed=False,
            allDatabaseRowsUnchanged=True, workerAndTimersPaused=True)
    except Exception as exc:
        h.run(['systemctl', 'stop', 'neckline.service'])
        receipt.update(status='failed', failureType=type(exc).__name__, failure=str(exc)[-1800:])
        # Never restore a database over subsequent user writes. This code-only
        # rollback restores runtime/wheel, while the original database remains.
        if changed:
            receipt['failureDatabase'] = h.snapshot(BACKUP / 'neckline-failure.db')
            h.restore_runtime(BACKUP / 'runtime', root_mode=0o755)
            old_wheel = ROOT / 'releases' / PRIOR / prior['wheelFile']
            assert h.sha256(old_wheel) == prior['wheelSha256']
            h.run([str(ROOT / '.venv/bin/python'), '-m', 'pip', 'install', '--no-index', '--no-deps',
                '--force-reinstall', str(old_wheel)], capture_output=True)
            manifest_check(ROOT, prior)
        h.run(['systemctl', 'start', 'neckline.service']); h.wait_health(PRIOR)
        paused(True)
        receipt.update(status='rolled_back', databaseRestored=False)
        h.write_json(BACKUP / 'receipt.json', receipt)
        raise
    h.write_json(BACKUP / 'receipt.json', receipt)
    print(json.dumps(receipt))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['prepare', 'deploy'])
    args = parser.parse_args()
    (prepare if args.phase == 'prepare' else deploy)()
