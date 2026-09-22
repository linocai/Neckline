"""Paid research replies: preserve text, reject ambiguity, keep raw receipts."""
import json
import socket

import httpx
import pytest

from neckline.llm.base import ChatMessage
from neckline.llm.openai_compat import OpenAICompatProvider
from tests import v340_acceptance_fixture as acceptance
from tests.test_v350_cli_api import DirectRoundTransport, read_actual_api


@pytest.mark.parametrize('content,expected', [
    ('{"text":"a\nb\tc\rd"}', {'text': 'a\nb\tc\rd'}),
    (' {"items":[{"a":1}]} } ', {'items': [{'a': 1}]}),
    ('{"text":"a\nb"}}', {'text': 'a\nb'}),
])
def test_live_and_paid_replay_normalize_only_unambiguous_json_syntax(monkeypatch, content, expected):
    body = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 5, 'completion_tokens': 7, 'total_tokens': 12}}
    original = json.dumps(body)
    client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: client(**{**kwargs,
        'transport': httpx.MockTransport(lambda request: httpx.Response(200, json=body))}))
    provider = OpenAICompatProvider(api_key='fixture', model='fixture', name='fixture',
                                   api_url='https://fixture.invalid/chat', use_streaming=False)
    live = provider.chat([ChatMessage(role='user', content='fixture')], response_format={'type': 'json_object'})
    replay = provider.revalidate_received_response([body], enable_search=False,
        response_format={'type': 'json_object'}, json_array_key=None)
    for result in (live, replay):
        assert result.ok and json.loads(result.content) == expected
        assert result.total_tokens == 12 and result.raw_responses == [body]
    assert json.dumps(body) == original


@pytest.mark.parametrize('content', [
    '{"a":1} {"b":2}', '{"a":1},"b":2}', '{"a":1}]', '{"a":1}}}',
    '{"a":"unfinished}', '{"a":1,}', '{"a":"x\x00y"}', 'prose {"a":1}',
])
def test_json_repair_never_discards_fields_objects_or_truncation(content):
    provider = OpenAICompatProvider(api_key='fixture', model='fixture', name='fixture',
                                   api_url='https://fixture.invalid/chat', use_streaming=False)
    body = {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]}
    result = provider.revalidate_received_response([body], enable_search=False,
        response_format={'type': 'json_object'}, json_array_key=None)
    assert not result.ok and result.error_code == 'response_json_invalid'


def test_actual_cli_worker_delivers_complete_research_with_json_surface_errors(tmp_path, monkeypatch):
    class SyntaxTransport(DirectRoundTransport):
        def respond(self, request):
            response = super().respond(request)
            if self._packet(request).get('action') == 'research_round':
                body = response.json()
                content = body['choices'][0]['message']['content']
                content = content.replace('仅为离线验收，送样不是订单。', '仅为离线验收，\n送样不是订单。') + '}'
                body['choices'][0]['message']['content'] = content
                return httpx.Response(200, json=body)
            return response
    monkeypatch.setattr(acceptance, 'TITLE_COUNT', 2)
    monkeypatch.setattr(acceptance, 'DeterministicTransport', SyntaxTransport)
    monkeypatch.setattr(socket.socket, 'connect', acceptance._deny_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', acceptance._deny_network)
    flow = acceptance.run_full_scale_flow(tmp_path, monkeypatch, name='b85-json', selected_event_count=1)
    assert flow.task_status == 'completed' and flow.calls['research:research_round'] == 1
    report = read_actual_api(flow.db_path)[0]['report']
    assert report['delivery']['outcome'] == 'complete' and len(report['eveningCards']) == 1


@pytest.mark.parametrize("interrupt_append", [False, True])
def test_drained_real_slice_authorizes_only_paid_revalidation_before_publication(tmp_path, monkeypatch, interrupt_append):
    import sqlite3
    from datetime import datetime, timezone
    from neckline.k10 import pipeline, store
    from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
    from neckline.llm import openai_compat

    class PaidSyntaxTransport(DirectRoundTransport):
        def respond(self, request):
            response = super().respond(request)
            if self._packet(request).get('action') == 'research_round':
                body = response.json()
                body['choices'][0]['message']['content'] += '}'
                return httpx.Response(200, json=body)
            return response

    from contextlib import contextmanager
    from neckline.k10 import research_store
    from neckline.k10.schema import SqliteWriteBusy
    write = research_store.write_connection
    interrupted = []

    @contextmanager
    def interrupted_write(database):
        with write(database) as connection:
            class Proxy:
                deleted = False
                def execute(self, sql, args=()):
                    if (self.deleted and not interrupted
                            and sql.startswith('INSERT INTO k10_research_snapshot_revisions')):
                        interrupted.append(True)
                        raise SqliteWriteBusy('isolated failure after removing the failure placeholder')
                    result = connection.execute(sql, args)
                    if sql.startswith('DELETE FROM k10_research_round_results'):
                        self.deleted = True
                    return result
                def __getattr__(self, name):
                    return getattr(connection, name)
            yield Proxy()
    if interrupt_append:
        monkeypatch.setattr(research_store, 'write_connection', interrupted_write)

    current_decoder = openai_compat._decode_json_content
    monkeypatch.setattr(openai_compat, '_decode_json_content', lambda value: (json.loads(value), False))
    original_outcome = pipeline._research_outcome
    yielded = []

    def fail_then_slice(**kwargs):
        try:
            return original_outcome(**kwargs)
        finally:
            with sqlite3.connect(kwargs['db_path']) as connection:
                failed = connection.execute("SELECT 1 FROM k10_research_snapshot_revisions "
                    "WHERE task_id=? AND execution_status='failed'", (kwargs['task_id'],)).fetchone()
            if failed and not yielded:
                yielded.append(True)
                raise pipeline.DiscoverySliceYield()

    real_run_once = acceptance.run_once
    grants = []
    pending = []

    def authorize(task, **kwargs):
            database = kwargs['db_path']
            frozen = store.task_execution_input(task_id=task.task_id, db_path=database)
            scan_id = frozen['checkpoint']['scanId']
            profile = store.task_execution_profile(task_id=task.task_id, db_path=database)
            args = dict(db_path=database, scan_id=scan_id, execution_config_id=profile['configId'],
                execution_config_revision=profile['revision'],
                confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan_id, db_path=database),
                now=datetime.now(timezone.utc))
            # An actually leased task or any unknown paid outcome remains
            # outside the between-slice recovery boundary.
            for state in ('leased', 'unknown'):
                copy = tmp_path / f'{state}-recovery.sqlite'
                with sqlite3.connect(database) as source, sqlite3.connect(copy) as target:
                    source.backup(target)
                    if state == 'leased':
                        target.execute("UPDATE k10_tasks SET status='running',lease_owner='other' WHERE task_id=?", (task.task_id,))
                    else:
                        target.execute("UPDATE k10_external_attempts SET state='unknown' WHERE task_id=? AND stage='investigation'", (task.task_id,))
                with pytest.raises(RuntimeError):
                    recover_scan(**{**args, 'db_path': copy})
            # This boundary cannot silently expand output/time budgets.
            with pytest.raises(RuntimeError, match='执行参数'):
                recover_scan(**args, research_max_tokens=999999)
            monkeypatch.setattr(openai_compat, '_decode_json_content', current_decoder)
            assert recover_scan(**args) == task.task_id
            saved = store.task_execution_input(task_id=task.task_id, db_path=database)
            grants.append(saved['checkpoint']['recoveryAuthorized'])
            assert grants[0]['receiptOnly'] is True
            assert saved['inputCutoffAt'] == frozen['inputCutoffAt']
            assert saved['executionProfile'] == frozen['executionProfile']

    def run_once(**kwargs):
        if pending:
            authorize(pending.pop(), **kwargs)
        task = real_run_once(**kwargs)
        if task.status == 'queued' and yielded and not grants:
            pending.append(task)
        return task

    monkeypatch.setattr(acceptance, 'TITLE_COUNT', 2)
    monkeypatch.setattr(acceptance, 'DeterministicTransport', PaidSyntaxTransport)
    monkeypatch.setattr(pipeline, '_research_outcome', fail_then_slice)
    monkeypatch.setattr(acceptance, 'run_once', run_once)
    monkeypatch.setattr(socket.socket, 'connect', acceptance._deny_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', acceptance._deny_network)
    flow = acceptance.run_full_scale_flow(tmp_path, monkeypatch, name='b85-queued-paid', selected_event_count=1,
        expected_continuation_codes=('DISCOVERY_SLICE', 'sqlite_busy'))
    assert grants and flow.task_status == 'completed'
    assert bool(interrupted) is interrupt_append
    assert flow.calls['research:research_round'] == 2  # Initial + repair before the local fix only.
    assert read_actual_api(flow.db_path)[0]['report']['delivery']['outcome'] == 'complete'
    with sqlite3.connect(flow.db_path) as connection:
        assert not connection.execute("SELECT 1 FROM k10_external_attempts WHERE state IN ('started','running','unknown')").fetchone()
    with sqlite3.connect(flow.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM k10_research_snapshot_revisions WHERE execution_status='failed'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM k10_execution_item_checkpoints WHERE stage='model:investigation_research_round' AND status='failed'").fetchone()[0] == 1
    frozen = store.task_execution_input(task_id=flow.task_id, db_path=flow.db_path)
    profile = store.task_execution_profile(task_id=flow.task_id, db_path=flow.db_path)
    with pytest.raises(RuntimeError, match='发布|恢复'):
        recover_scan(db_path=flow.db_path, scan_id=frozen['checkpoint']['scanId'],
            execution_config_id=profile['configId'], execution_config_revision=profile['revision'],
            confirmed_input_sha256=grants[0]['frozenInputSha256'], now=datetime.now(timezone.utc))
