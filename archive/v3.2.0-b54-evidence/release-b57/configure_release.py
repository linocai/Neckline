"""B57 explicit offline deployment step; run only on a backed-up target."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import socket


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    args = parser.parse_args()
    # Importing providers is allowed; opening any external connection is not.
    def denied(*a, **kw):
        raise AssertionError('No provider/network calls during release configuration')
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    from neckline.k10.schema import initialize_schema
    from neckline.k10.notifications import require_notifications_schema
    from neckline.k10.cli import main as cli
    from neckline.k10 import store
    from neckline.k10.v2_store import binding_status
    from neckline.api.k10 import create_router
    from neckline.api.k10_schemas import ConfigurationOut, V2ReportEnvelope
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from contextlib import redirect_stdout
    from io import StringIO
    db = args.db.resolve(strict=True)
    assert initialize_schema(db) == 8
    require_notifications_schema(db)
    def command(*parts):
        out = StringIO()
        with redirect_stdout(out):
            assert cli([*parts, '--db', str(db)]) == 0
        return json.loads(out.getvalue())
    imported = command('import-v2-profiles', '--confirmed-target', str(db),
                       '--universe-file', str(args.inputs/'universe.json'),
                       '--profiles-dir', str(args.inputs/'profiles'),
                       '--universe-id', 'k10-v2-initial-20260909',
                       '--profiles-id', 'k10-v2-profiles-20260909')
    strategy = command('configure', '--config-id', 'k10-v2-production',
                       '--file', 'neckline/config/k10-v2.json')
    execution = command('configure-execution', '--config-id', 'k10-v2-execution-production',
                        '--file', 'neckline/config/k10-execution-v4.json')
    pack = store.read_execution_config(config_id=execution['configId'], revision=execution['revision'], db_path=db)
    policy = pack['payload']['discovery']['titleTriagePolicy']
    old = store.read_title_triage_policy(policy_id=policy['policyId'], revision=policy['revision'], db_path=db)
    if old:
        assert old['content'] == policy['content'] and old['approvalState'] == 'approved'
    else:
        now = datetime.now(timezone.utc).isoformat()
        revision = store.append_title_triage_policy(policy_id=policy['policyId'], content=policy['content'],
            approval_state='approved', created_at=now, approved_at=now, db_path=db)
        assert revision == policy['revision']
    command('bind-v2-strategy', '--snapshot-id', 'k10-v2-20260909',
            '--config-id', strategy['configId'], '--config-revision', str(strategy['revision']),
            '--execution-config-id', execution['configId'], '--execution-config-revision', str(execution['revision']))
    app = FastAPI()
    app.include_router(create_router(lambda: db, lambda: None, lambda: db.parent,
        lambda: (strategy['configId'], strategy['revision'], None),
        lambda: (execution['configId'], execution['revision'], None)))
    with TestClient(app) as client:
        response = client.get('/api/v1/k10/configuration')
        assert response.status_code == 200
        config = ConfigurationOut.model_validate(response.json())
        assert config.configId == strategy['configId'] and config.configRevision == strategy['revision']
        assert config.executionConfigId == execution['configId'] and config.executionConfigRevision == execution['revision']
        assert len(config.scopes) == 4 and all(s.state == 'configured' and not s.errors and not s.missing for s in config.scopes)
        assert config.strategyVersion == 'K10-v2' and config.profileReviewStatus == 'local_draft_awaiting_user'
        for kind in ('evening', 'morning'):
            response = client.get('/api/v1/k10/v2/reports/latest', params={'window': kind})
            assert response.status_code == 200
            value = V2ReportEnvelope.model_validate(response.json())
            assert value.state == 'empty', response.json()
    assert store.run_control_status(db_path=db)['state'] == 'closed'
    print(json.dumps({'schema': 8, 'strategy': strategy, 'execution': execution,
        'profiles': imported, 'configuredScopes': 4, 'noV2ScanReadiness': True,
        'runControl': 'closed', 'externalCalls': 0}))


if __name__ == '__main__':
    main()
