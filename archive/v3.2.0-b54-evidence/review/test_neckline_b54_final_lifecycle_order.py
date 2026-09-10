"""Independent narrow review: durable ordering and matching actual API projections."""
import os
import tempfile
from pathlib import Path
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['DB_PATH'] = str(Path(tempfile.mkdtemp(prefix='neckline-review-import-')) / 'unused.sqlite')
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b54_review_regressions import client_for
from tests.test_k10_api import _freeze_k10_clocks
from neckline.k10 import store


def test_ordered_risk_release_and_return_match_both_real_api_projections(tmp_path, monkeypatch):
    db, *_ = e2e._run(tmp_path, monkeypatch, v2=True)
    _freeze_k10_clocks(monkeypatch, '2026-09-09T12:00:00+08:00')
    old = store.list_opportunities(db_path=db)[0]
    oid = old['opportunityId']
    route = '/api/v1/k10/company-windows/' + old['companyWindowId']
    verified = {'reasonStatus': 'current', 'sourceStatus': 'complete'}
    stages = [
        ('risk-first', 'risk', '2026-09-09T09:10:00+08:00', {}, 'risk', 'risk', True),
        ('late-arriving-older-clear', 'evidence_update', '2026-09-09T01:00:00+00:00', verified, 'risk', 'risk', True),
        ('later-complete-clear', 'evidence_update', '2026-09-09T01:15:00+00:00', verified, 'evidence_update', 'active', True),
        ('same-instant-later-risk', 'risk', '2026-09-09T09:15:00+08:00', {}, 'risk', 'risk', True),
        ('plain-after-risk', 'evidence_update', '2026-09-09T09:16:00+08:00', {}, 'risk', 'risk', True),
        ('partial-after-risk', 'evidence_update', '2026-09-09T01:17:00+00:00', {'reasonStatus': 'current', 'sourceStatus': 'partial'}, 'risk', 'risk', True),
        ('second-complete-clear', 'evidence_update', '2026-09-09T09:18:00+08:00', verified, 'evidence_update', 'active', True),
        ('terminal', 'withdrawal', '2026-09-09T01:19:00+00:00', {}, 'withdrawal', 'withdrawn', False),
        ('clear-after-terminal', 'evidence_update', '2026-09-09T09:20:00+08:00', verified, 'withdrawal', 'withdrawn', False),
    ]
    with client_for(db) as client:
        for label, kind, at, content, window_state, card_state, selectable in stages:
            store.append_opportunity_update(lifecycle_event_id=label, opportunity_id=oid, kind=kind,
                reason=label, source_refs=[], content=content, occurred_at=at,
                created_at='2026-09-09T11:00:00+08:00', db_path=db)
            window_response = client.get(route)
            daily_response = client.get('/api/v1/k10/v2/reports/latest?window=evening')
            assert window_response.status_code == daily_response.status_code == 200
            window = window_response.json()
            opportunity = next(x for x in window['opportunities'] if x['opportunityId'] == oid)
            card = next(x for x in daily_response.json()['report']['eveningCards'] if x['companyWindowId'] == old['companyWindowId'])
            catalyst = next(x for x in card['catalysts'] if x['opportunityId'] == oid)
            actual = (opportunity['lifecycle'], catalyst['lifecycleState'], window['canSelect'], card['canSelect'])
            assert actual == (window_state, card_state, selectable, selectable), (label, actual)
            print(label, actual)
