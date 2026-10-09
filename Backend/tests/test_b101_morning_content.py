"""The morning review's separate wire parser shares the model JSON boundary."""
import json
import socket
import sqlite3

import httpx
import pytest

from tests import test_b92_report_loopback as current
from tests import test_b98_report_isolation as b98


@pytest.mark.parametrize('fault', ['control', 'summary_surrogate', 'nan'])
def test_bad_review_content_keeps_independent_discovery(fault, tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('No real network in B101')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    original = current._FlashReportTransport.respond
    injected = []
    def respond(self, request):
        message = json.loads(request.content)['messages'][-1]['content']
        packet = self._packet(request) if '<untrusted-k10-evidence>' in message else {}
        if (isinstance(packet.get('documentId'), str)
                and packet['documentId'] in type(self).morning_document_ids):
            ref = {'documentId': packet['documentId'], 'revision': packet['revision']}
            return self._ok({'events': [{'canonicalKey': 'event-002', 'stageKey': 'morning-new',
                'eventState': 'rumor', 'headline': '独立公司的隔夜新事实', 'eventKind': 'rumor', 'facts': {},
                'sourceRefs': [ref], 'claims': [{'text': '独立公司项目送样', 'kind': 'rumor', 'novelty': 'new_fact',
                    'speaker': '供应商', 'subject': '项目', 'object': '样品', 'action': '送样',
                    'stageOrCondition': '待确认', 'timeText': '隔夜', 'verificationStatus': 'unverified',
                    'decisionImpact': '影响独立公司判断', 'sourceRef': ref, 'location': 'paragraph:1'}]}],
                'needsFullText': False})
        response = original(self, request)
        if '<untrusted-evidence>' in message and fault != 'control':
            envelope = response.json()
            value = json.loads(envelope['choices'][0]['message']['content'])
            if fault == 'summary_surrogate':
                value['summary'] = '外部坏文本\ud800'
            else:
                value['wireNote'] = float('nan')
            envelope['choices'][0]['message']['content'] = json.dumps(value, ensure_ascii=True)
            injected.append(fault)
            return httpx.Response(200, json=envelope)
        return response
    monkeypatch.setattr(current._FlashReportTransport, 'respond', respond)
    # Normal helper expects zero morning discovery and completed reviews.
    # Both scenarios deliberately use a new independent event; assert actual
    # persisted outcomes below without repairing any producer-owned state.
    try:
        current.generate_b92_loopback(tmp_path, monkeypatch, material_contrary=True)
    except AssertionError:
        pass
    db, task, report, materials = b98.read_case(tmp_path, 'morning')
    assert task[1] == 'completed' and report['addedCards'] and materials['items']
    assert report['resultAvailableAt']
    reviews = report['morningReview']['items']
    assert reviews
    if fault == 'control':
        assert not injected and all(row['status'] == 'completed' for row in reviews)
        assert report['lifecycleUpdates']
    else:
        assert injected and report['status'] == 'partial'
        assert any(row['status'] != 'completed' for row in reviews)
        assert report['delivery']['gaps']
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
        assert conn.execute("SELECT count(*) FROM k10_external_attempts WHERE state IN ('started','running','unknown')").fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM k10_model_response_receipts').fetchone()[0] > 0
