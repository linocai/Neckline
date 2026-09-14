"""Physical non-response failures retain the existing bounded recovery admission.

These tests exercise MeteredProvider -> httpx.MockTransport -> the actual attempt
store. The narrow 402 fixture sets only a previously-authorized attempt ID; real
CLI recovery authorization has its separate B58/B68 end-to-end coverage.
"""
import json
import sqlite3
import itertools
import uuid

import httpx
import pytest

from neckline.k10 import store
from neckline.k10.metering import provider_spend_context
from neckline.llm.base import ChatMessage
from tests.test_v330_b69 import _receipt_provider, _restarted_receipt_provider


@pytest.mark.parametrize('status', [429, 402])
def test_known_nonresponse_can_follow_existing_retry_authorization_without_reusing_a_fake_receipt(tmp_path, monkeypatch, status):
    db, provider = _receipt_provider(tmp_path)
    # Both responses can settle within one recorded second. UUID lexical order
    # must never select the older refusal over the later paid successful reply.
    ids = itertools.count(99999, -1)
    monkeypatch.setattr('neckline.k10.store.uuid.uuid4', lambda: uuid.UUID(int=next(ids)))
    monkeypatch.setattr('neckline.k10.metering._safe_now', lambda: '2026-09-13T13:00:01+00:00')
    calls = []
    def respond(request):
        calls.append(request.content)
        if len(calls) == 1:
            return httpx.Response(status, json={'error': {'code': str(status)}}, headers={'Retry-After':'1'})
        return httpx.Response(200, json={
            'choices':[{'message':{'content':'{}'}, 'finish_reason':'stop'}],
            'usage':{'prompt_tokens':2,'completion_tokens':1,'total_tokens':3}})
    options = {'enable_search':False, 'model_options':{'maxTokens':128}, 'transport':httpx.MockTransport(respond)}
    messages = [ChatMessage(role='user', content='same input following a definite non-response')]
    with provider_spend_context(provider=provider, task_id='receipt-task', stage='understand', item_key='first', attempt=1):
        failed = provider.chat(messages, **options)
    assert failed.error_code == ('rate_limited' if status == 429 else 'insufficient_balance')
    with sqlite3.connect(db) as conn:
        old = conn.execute('SELECT * FROM k10_external_attempts').fetchone()
        attempt_id = conn.execute('SELECT attempt_id FROM k10_external_attempts').fetchone()[0]
        receipt = json.loads(conn.execute('SELECT payload_json FROM k10_model_response_receipts').fetchone()[0])
    assert receipt['responseReceived'] is False and receipt['rawResponses'] == []
    restarted = _restarted_receipt_provider(db)
    if status == 402:
        with provider_spend_context(provider=restarted, task_id='receipt-task', stage='understand', item_key='no-grant', attempt=2):
            blocked = restarted.chat(messages, **options)
        assert blocked.error_code == 'insufficient_balance' and len(calls) == 1
        # Test prerequisite outside this provider admission boundary: represent
        # the explicit same-task grant issued by the separately tested CLI.
        with sqlite3.connect(db) as conn:
            checkpoint = json.loads(conn.execute('SELECT checkpoint_json FROM k10_tasks').fetchone()[0])
            checkpoint['authorizedRetryAttemptIds'] = [attempt_id]
            conn.execute('UPDATE k10_tasks SET checkpoint_json=?', (json.dumps(checkpoint),))
    with provider_spend_context(provider=restarted, task_id='receipt-task', stage='understand', item_key='retry', attempt=2):
        result = restarted.chat(messages, **options)
    assert result.ok and not getattr(result, 'local_reuse', False)
    assert len(calls) == 2 and calls[0] == calls[1]
    with sqlite3.connect(db) as conn:
        assert old in conn.execute('SELECT * FROM k10_external_attempts').fetchall()
    # The private recovery export is sorted by audit identity, not receipt
    # insertion time. Restore it into the isolated matching ledger and prove
    # that an older refusal cannot shadow an already-paid successful response.
    from neckline.k10.schema import export_model_response_receipts, restore_model_response_receipts
    exported = tmp_path / 'receipts.json'
    export_model_response_receipts(db, export_path=exported)
    with sqlite3.connect(db) as conn:
        conn.execute('DELETE FROM k10_model_response_receipts')
    restore_model_response_receipts(db, export_path=exported)
    # Once a real successful reply exists, a further internal ID must reuse it.
    with provider_spend_context(provider=restarted, task_id='receipt-task', stage='understand', item_key='after-reply', attempt=3):
        reused = restarted.chat(messages, **options)
    assert reused.ok and reused.local_reuse and len(calls) == 2
    assert store.external_attempt_summary(task_id='receipt-task', db_path=db)['succeeded'] == 1
