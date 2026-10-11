"""B104 collection-boundary regressions through the real CLI and worker."""
from datetime import datetime, timedelta, timezone

import pytest

from tests import test_b92_collection_flow as flow
from neckline.k10 import store
from neckline.k10.collection_runtime import create_collection_handler
from neckline.k10.worker import run_once

CLOCK = lambda: datetime(2026, 9, 26, 12, tzinfo=timezone.utc)


def item(name, stamp, identity=''):
    return {'id': name + ':' + stamp + identity,
            'url': 'https://jin10.com/' + name + identity,
            'time': stamp, 'title': None if name == 'list_flash' else '测试文章',
            'content': '测试完整快讯' if name == 'list_flash' else None,
            'intro': None if name == 'list_flash' else '测试目录摘要'}


class ScriptedJin10(flow.FixtureJin10):
    pages = []
    calls = []

    def call_tool_raw(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        index = int(arguments.get('cursor', '0'))
        page = self.pages[index]
        rows = page['items'](name)
        more = index + 1 < len(self.pages)
        return {'isError': False, 'structuredContent': {'status': 200,
                'data': {'items': rows, 'has_more': more,
                         'next_cursor': str(index + 1) if more else None}}}


def run(database, config_id, revision, slot, pages):
    ScriptedJin10.pages = pages
    task_id = flow._cli('enqueue-collection', '--db', str(database), '--slot', slot,
                       '--config-id', config_id, '--config-revision', str(revision))
    result = run_once(db_path=database, worker_id='b103-independent-review',
                      lease_for=timedelta(minutes=5), clock=CLOCK,
                      task_id=task_id, require_b76_contract=True,
                      handlers={'collect_news': create_collection_handler(
                          tushare_token='fixture-token', jin10_token='fixture-token',
                          client_factory=ScriptedJin10, tushare_request=flow._tushare)})
    assert result is not None and result.task_id == task_id
    return result


def first_gap(tmp_path):
    database, config_id, revision = flow._configured(tmp_path)
    ScriptedJin10.calls = []
    first = run(database, config_id, revision, '2026-09-25T08:00:00+08:00',
                [{'items': lambda name: [item(name, '2026-09-25T01:00:00+08:00')]}])
    assert first.status == 'failed'
    assert store.latest_source_watermark(source_key='jin10-flash', db_path=database) is None
    return database, config_id, revision


def evidence(database, task_id, label):
    checkpoint = store.task_execution_input(task_id=task_id, db_path=database)['checkpoint']
    return checkpoint['sources']['jin10-flash']


@pytest.mark.parametrize('case', ['rejected', 'empty', 'disjoint'])
def test_acknowledged_gap_must_not_promote_new_incomplete_window(tmp_path, case):
    database, config_id, revision = first_gap(tmp_path)
    def rows(name):
        if case == 'empty':
            return []
        valid = item(name, '2026-09-25T19:00:00+08:00')
        return [valid, {'time': '2026-09-25T18:00:00+08:00'}] if case == 'rejected' else [valid]
    second = run(database, config_id, revision, '2026-09-25T20:00:00+08:00', [{'items': rows}])
    source = evidence(database, second.task_id, 'new-window-' + case)
    assert source['state'] == 'partial', source
    assert source['coverageThrough'] is None
    assert store.latest_source_watermark(source_key='jin10-flash', db_path=database) is None


def test_resume_stops_at_requested_start_instead_of_paying_for_older_pages(tmp_path):
    database, config_id, revision = first_gap(tmp_path)
    before = len(ScriptedJin10.calls)
    pages = [
        {'items': lambda name: [item(name, '2026-09-25T19:00:00+08:00')]},
        {'items': lambda name: [item(name, '2026-09-25T00:00:00+08:00')]},
        {'items': lambda name: [item(name, '2026-09-24T23:00:00+08:00')]},
        {'items': lambda name: [item(name, '2026-09-24T22:00:00+08:00')]},
    ]
    second = run(database, config_id, revision, '2026-09-25T20:00:00+08:00', pages)
    source = evidence(database, second.task_id, 'unbounded-pagination')
    assert source['requestedStartAt'] == '2026-09-25T07:00:00+08:00'
    new_calls = ScriptedJin10.calls[before:]
    assert len(new_calls) == 4, new_calls  # 2 boundary-reaching pages, 2 independent sources

def test_interrupted_after_page_checkpoint_resumes_without_cursor_loop(tmp_path, monkeypatch):
    database, config_id, revision = flow._configured(tmp_path)
    ScriptedJin10.calls = []
    original_save = __import__('neckline.k10.collection_runtime', fromlist=['_save'])._save
    fired = {'value': False}
    module = __import__('neckline.k10.collection_runtime', fromlist=['_save'])
    def interrupt_after_first_flash_page(context, source_key, state, **kwargs):
        result = original_save(context, source_key, state, **kwargs)
        if source_key == 'jin10-flash' and state.get('pagesFetched') == 1 and not fired['value']:
            fired['value'] = True
            raise KeyboardInterrupt('simulated process interruption after durable cursor checkpoint')
        return result
    monkeypatch.setattr(module, '_save', interrupt_after_first_flash_page)
    pages = [
        {'items': lambda name: [item(name, '2026-09-25T01:00:00+08:00')]},
        {'items': lambda name: [item(name, '2026-09-25T00:00:00+08:00')]},
    ]
    task_id = flow._cli('enqueue-collection', '--db', str(database), '--slot', '2026-09-25T08:00:00+08:00',
                         '--config-id', config_id, '--config-revision', str(revision))
    kwargs = dict(db_path=database, worker_id='b103-interrupt', lease_for=timedelta(seconds=1),
                  clock=lambda: datetime(2026, 9, 26, 12, tzinfo=timezone.utc), task_id=task_id,
                  require_b76_contract=True, handlers={'collect_news': create_collection_handler(
                      tushare_token='fixture-token', jin10_token='fixture-token',
                      client_factory=ScriptedJin10, tushare_request=flow._tushare)})
    ScriptedJin10.pages = pages
    with pytest.raises(KeyboardInterrupt):
        run_once(**kwargs)
    interrupted = store.task_execution_input(task_id=task_id, db_path=database)['checkpoint']
    flash = interrupted['sources']['jin10-flash']
    assert flash['state'] == 'running' and flash['cursor'] == '1' and flash['pagesFetched'] == 1
    # Lease expiry is simulated by the frozen worker clock; same task is claimed.
    kwargs['clock'] = lambda: datetime(2026, 9, 26, 12, 0, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(module, '_save', original_save)
    recovered = run_once(**kwargs)
    assert recovered.status == 'completed', recovered
    checkpoint = store.task_execution_input(task_id=task_id, db_path=database)['checkpoint']
    flash = checkpoint['sources']['jin10-flash']
    assert flash['errorCode'] != 'cursor_loop'
    assert flash['pagesFetched'] == 2 and flash['cursor'] is None
    evidence(database, task_id, 'interrupted-recovery')

def test_interrupted_after_terminal_page_checkpoint_does_not_turn_into_cursor_loop(tmp_path, monkeypatch):
    database, config_id, revision = flow._configured(tmp_path)
    ScriptedJin10.calls = []
    module = __import__('neckline.k10.collection_runtime', fromlist=['_save'])
    original_save = module._save
    fired = {'value': False}
    def interrupt_after_terminal_page_checkpoint(context, source_key, state, **kwargs):
        result = original_save(context, source_key, state, **kwargs)
        if source_key == 'jin10-flash' and state.get('pagesFetched') == 2 and not fired['value']:
            fired['value'] = True
            raise KeyboardInterrupt('simulated interruption after terminal page durable page checkpoint')
        return result
    monkeypatch.setattr(module, '_save', interrupt_after_terminal_page_checkpoint)
    ScriptedJin10.pages = [
        {'items': lambda name: [item(name, '2026-09-25T01:00:00+08:00')]},
        {'items': lambda name: [item(name, '2026-09-25T00:00:00+08:00')]},
    ]
    task_id = flow._cli('enqueue-collection', '--db', str(database), '--slot', '2026-09-25T08:00:00+08:00',
                         '--config-id', config_id, '--config-revision', str(revision))
    kwargs = dict(db_path=database, worker_id='b103-terminal-interrupt', lease_for=timedelta(seconds=1),
                  clock=lambda: datetime(2026, 9, 26, 12, tzinfo=timezone.utc), task_id=task_id,
                  require_b76_contract=True, handlers={'collect_news': create_collection_handler(
                      tushare_token='fixture-token', jin10_token='fixture-token',
                      client_factory=ScriptedJin10, tushare_request=flow._tushare)})
    with pytest.raises(KeyboardInterrupt):
        run_once(**kwargs)
    interrupted = store.task_execution_input(task_id=task_id, db_path=database)['checkpoint']
    assert interrupted['sources']['jin10-flash']['pagesFetched'] == 2
    assert interrupted['sources']['jin10-flash']['cursor'] is None
    # Recovery must replay the settled terminal wire locally; current code sees
    # the same empty-cursor fingerprint and wrongly reports cursor_loop.
    kwargs['clock'] = lambda: datetime(2026, 9, 26, 12, 0, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(module, '_save', original_save)
    recovered = run_once(**kwargs)
    evidence(database, task_id, 'terminal-page-interrupted-recovery')
    assert recovered.status == 'completed', recovered
    checkpoint = store.task_execution_input(task_id=task_id, db_path=database)['checkpoint']
    assert checkpoint['sources']['jin10-flash']['errorCode'] != 'cursor_loop'

def test_resume_uses_previous_window_end_not_oldest_observed_for_bounded_fetch(tmp_path):
    database, config_id, revision = flow._configured(tmp_path)
    ScriptedJin10.calls = []
    # Previous slot reaches provider oldest visible 10:00, while its slot ended
    # at 20:00. Next slot should resume around 19:00, not 09:00.
    run(database, config_id, revision, '2026-09-25T20:00:00+08:00',
        [{'items': lambda name: [item(name, '2026-09-25T10:00:00+08:00')]}])
    before = len(ScriptedJin10.calls)
    pages = [
        {'items': lambda name: [item(name, '2026-09-25T18:00:00+08:00')]},
        {'items': lambda name: [item(name, '2026-09-25T17:00:00+08:00')]},
        {'items': lambda name: [item(name, '2026-09-25T16:00:00+08:00')]},
        {'items': lambda name: [item(name, '2026-09-25T15:00:00+08:00')]},
        {'items': lambda name: [item(name, '2026-09-25T08:00:00+08:00')]},
    ]
    second = run(database, config_id, revision, '2026-09-26T08:00:00+08:00', pages)
    source = evidence(database, second.task_id, 'resume-oldest-boundary')
    new_calls = ScriptedJin10.calls[before:]
    # Correct prior-end/lookback boundary (19:00 previous end) stops at 18:00:
    # one paid page per Jin10 source, not a replay to 08:00/09:00.
    assert len(new_calls) == 2, new_calls
    assert source['pagesFetched'] == 1
