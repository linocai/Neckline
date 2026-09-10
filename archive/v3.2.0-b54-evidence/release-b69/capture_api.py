"""Private DTO capture for current Swift decoding; never captures credentials."""
from pathlib import Path
import json
import socket
import sys
import urllib.request
from dotenv import load_dotenv

assert socket.gethostname()=='ser657204219523'
load_dotenv('/opt/neckline/.env',override=True)
load_dotenv('/etc/neckline/k10.env',override=True)
from neckline.config import settings
from neckline.api.k10_schemas import V2ReportEnvelope
routes={'configuration':'k10/configuration','evening':'k10/v2/reports/latest?window=evening',
    'morning':'k10/v2/reports/latest?window=morning','results':'k10/results',
    'opportunities':'k10/opportunities','providers':'settings/providers','health':'health'}
value={}
for key,route in routes.items():
    request=urllib.request.Request('http://127.0.0.1:8002/api/v1/'+route,
        headers={'Authorization':'Bearer '+settings.api_token})
    with urllib.request.urlopen(request,timeout=30) as response:value[key]=json.load(response)
assert value['health']['releaseSet']=='v3.2.0-b69'
envelope=V2ReportEnvelope.model_validate(value['evening'])
report=envelope.report
assert report and report.reportId=='report_scan_d39fbd5c631ea9a7df5f569dbd432b4d'
if '--require-published' in sys.argv:
    assert report.availableAt and report.status in {'completed','partial'}
path=Path('/opt/neckline/data/backups/v3.2.0-b69-predeploy/report-read-api.json')
path.write_text(json.dumps(value,ensure_ascii=False)+'\n');path.chmod(0o600)
print(json.dumps({'status':report.status,'availableAt':report.availableAt,'cards':len(report.eveningCards),
    'coverageGaps':report.coverageGaps,'incompleteReviews':len(report.incompleteReviews)},ensure_ascii=False))
