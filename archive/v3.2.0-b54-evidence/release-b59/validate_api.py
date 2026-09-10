"""B59 read-only release acceptance; offline mode denies all socket connections."""
import argparse
import json
import os
from pathlib import Path
import socket

parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=['offline', 'live'])
parser.add_argument('--db', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
from dotenv import dotenv_values
if args.mode == 'live':
    os.environ.update({k: v for k, v in dotenv_values('/opt/neckline/.env').items() if v is not None})
os.environ.update({k: v for k, v in dotenv_values('/etc/neckline/k10.env').items() if v is not None})
os.environ['DB_PATH'] = args.db
os.environ['PARQUET_DIR'] = '/opt/neckline/data/parquet'
if args.mode == 'offline':
    def denied(*_args, **_kwargs):
        raise AssertionError('Network is forbidden during offline release acceptance')
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    os.environ['API_TOKEN'] = 'offline-b59-release-token'
from neckline.config import settings
from neckline.api.k10_schemas import ConfigurationOut, V2ReportEnvelope, ResultsOut, OpportunityListOut
from neckline.api.schemas import ProvidersListOut
headers = {'Authorization': 'Bearer ' + settings.api_token}

def verify(get):
    out = {}
    c = ConfigurationOut.model_validate(get('/k10/configuration', True))
    assert (c.configId, c.configRevision) == ('k10-v2-production', 1)
    assert (c.executionConfigId, c.executionConfigRevision) == ('k10-v2-execution-production', 1)
    assert c.strategyVersion == 'K10-v2' and c.runControl.state == 'paused'
    assert len(c.scopes) == 4 and all(s.state == 'configured' and not s.errors and not s.missing for s in c.scopes)
    assert c.profileReviewStatus == 'local_draft_awaiting_user'
    out['configuration'] = c.model_dump(mode='json')
    for window in ('evening', 'morning'):
        report = V2ReportEnvelope.model_validate(get('/k10/v2/reports/latest?window=' + window, True))
        assert report.state == 'empty'
        out[window] = report.model_dump(mode='json')
    out['results'] = ResultsOut.model_validate(get('/k10/results', True)).model_dump(mode='json')
    out['opportunities'] = OpportunityListOut.model_validate(get('/k10/opportunities', True)).model_dump(mode='json')
    providers = ProvidersListOut.model_validate(get('/settings/providers', True))
    assert len(providers.items) == 1
    p = providers.items[0]
    assert (p.name, p.model, p.enabled, p.keySet) == ('deepseek', 'deepseek-v4-pro', True, True)
    out['providers'] = providers.model_dump(mode='json')
    assert get('/k10/configuration', False) == 401
    health = get('/health', False)
    assert health['releaseSet'] == 'v3.2.0-b59' and health['status'] == 'ok'
    out['health'] = health
    path = Path(args.output)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
    path.chmod(0o600)
    print(json.dumps({'mode': args.mode, 'configuredScopes': 4, 'bindings': 'K10-v2 rev1 / execution rev1',
        'runControl': 'paused', 'evening': 'empty', 'morning': 'empty', 'providerUnchanged': True,
        'unauthenticatedStatus': 401, 'readOnly': True}))

if args.mode == 'offline':
    from fastapi.testclient import TestClient
    from neckline.api.app import app
    with TestClient(app) as client:
        def get(path, authenticated):
            response = client.get('/api/v1' + path, headers=headers if authenticated else {})
            if response.status_code == 401:
                return 401
            assert response.status_code == 200, path
            return response.json()
        verify(get)
else:
    import urllib.request
    import urllib.error
    def get(path, authenticated):
        try:
            request = urllib.request.Request('http://127.0.0.1:8002/api/v1' + path,
                headers=headers if authenticated else {})
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return 401
            raise RuntimeError('Read-only API failed: ' + path) from None
    verify(get)
