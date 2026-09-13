"""Exchange-calendar scheduling through the real CLI and production handler."""
from datetime import date, datetime, timedelta
import json
import sqlite3

import pytest

from neckline.k10 import cli, pipeline, store
from neckline.k10.sources import SourceFetchResult
from neckline.k10.worker import run_once
from neckline.k10.windows import SHANGHAI, morning_window
from tests.test_k10_cli import _db, _execution
from tests.test_k10_api import _freeze_k10_clocks
from tests.test_b54_review_regressions import client_for
from tests import test_v310_pipeline_e2e as e2e


def calendar(db):
    # SSE 2026 holiday notice: Mid-Autumn Sep 25–27, National Day Oct 1–7.
    # Sep 20 / Oct 10 adjusted office working days remain market-closed weekends.
    days = []
    day = date(2026, 9, 1)
    while day <= date(2026, 10, 13):
        holiday = date(2026, 9, 25) <= day <= date(2026, 9, 27) or date(2026, 10, 1) <= day <= date(2026, 10, 7)
        days.append((day.strftime('%Y%m%d'), int(day.weekday() < 5 and not holiday)))
        day += timedelta(days=1)
    with sqlite3.connect(db) as c:
        c.execute('DELETE FROM trade_cal')
        c.executemany("INSERT INTO trade_cal VALUES ('SSE', ?, ?)", days)


def enqueue(db, kind, day, revision, execution_revision, capsys, config_id='fixture', execution_id='fixture-execution', bootstrap=None):
    args = ['enqueue', '--db', str(db), '--kind', kind, '--trading-day', day.isoformat(),
            '--config-id', config_id, '--config-revision', str(revision),
            '--execution-config-id', execution_id, '--execution-config-revision', str(execution_revision)]
    if bootstrap is not None:
        args += ['--bootstrap-cutoff', bootstrap]
    assert cli.main(args) == 0
    return capsys.readouterr().out.strip()


@pytest.mark.parametrize('kind,day,opens', [
    ('evening','2026-09-11',False), ('evening','2026-09-12',False),
    ('evening','2026-09-13',True), ('morning','2026-09-14',True),
    ('morning','2026-09-18',True), ('evening','2026-09-18',False),
    ('morning','2026-09-20',False), ('evening','2026-09-20',True),
    ('evening','2026-09-24',False), ('morning','2026-09-25',False),
    ('evening','2026-09-27',True), ('morning','2026-09-28',True),
    ('evening','2026-09-30',False), ('evening','2026-10-06',False),
    ('evening','2026-10-07',True), ('morning','2026-10-08',True),
    ('morning','2026-10-10',False), ('evening','2026-10-11',True),
])
def test_exchange_calendar_controls_real_enqueue(tmp_path, capsys, kind, day, opens):
    db = tmp_path/'schedule.sqlite'
    revision = _db(db)
    execution = _execution(db)
    calendar(db)
    run_day = date.fromisoformat(day)
    result = enqueue(db, kind, run_day, revision, execution, capsys)
    if not opens:
        assert json.loads(result)['status'] == 'not_trading_day'
        with sqlite3.connect(db) as c:
            assert c.execute('SELECT count(*) FROM k10_tasks').fetchone() == (0,)
        with pytest.raises(RuntimeError, match='非交易日'):
            cli.enqueue_scan(db_path=db, kind=kind, trading_day=run_day, config_id='fixture',
                config_revision=revision, execution_config_id='fixture-execution',
                execution_config_revision=execution, now=datetime.now(SHANGHAI))
    else:
        assert enqueue(db, kind, run_day, revision, execution, capsys) == result
        task = store.get_task(task_id=result, db_path=db)
        assert task.status == 'queued' and task.payload['tradingDay'] == day
        assert store.task_execution_profile(task_id=result, db_path=db)['bindingKind'] == 'scheduled'
        cutoff = store.task_execution_input(task_id=result, db_path=db)['inputCutoffAt']
        assert cutoff == day + ('T21:00:00+08:00' if kind == 'evening' else 'T09:00:00+08:00')


def test_evening_requires_next_day_coverage_even_when_today_is_open(tmp_path, capsys):
    db = tmp_path/'missing.sqlite'
    revision, execution = _db(db), _execution(db)
    calendar(db)
    with sqlite3.connect(db) as c:
        c.execute("DELETE FROM trade_cal WHERE cal_date='20260915'")
    with pytest.raises(RuntimeError, match='交易日历缺覆盖'):
        enqueue(db, 'evening', date(2026,9,14), revision, execution, capsys)


@pytest.mark.parametrize('day', [date(2026,9,14), date(2026,9,28), date(2026,10,8)])
def test_morning_increment_starts_previous_natural_evening(day):
    window = morning_window(observation_day=day)
    assert window.start_at == datetime.combine(day-timedelta(days=1), datetime.min.time().replace(hour=21), SHANGHAI)
    assert window.contains(window.start_at)
    assert not window.contains(window.start_at-timedelta(seconds=1))
    assert window.contains(datetime.combine(day, datetime.min.time().replace(hour=9), SHANGHAI))


def test_sunday_cli_worker_publication_and_monday_increment(tmp_path, monkeypatch, capsys):
    sunday = date(2026,9,13)
    tick = [datetime(2026,9,13,22,tzinfo=SHANGHAI)]
    monkeypatch.setattr(e2e, 'DAY', sunday)
    monkeypatch.setattr(e2e, 'NOW', datetime(2026,9,13,21,tzinfo=SHANGHAI))
    monkeypatch.setattr(e2e, 'RUN_AT', tick[0])

    def real_cli(**kwargs):
        calendar(kwargs['db_path'])
        return enqueue(kwargs['db_path'], kwargs['kind'], kwargs['trading_day'],
            kwargs['config_revision'], kwargs['execution_config_revision'], capsys,
            kwargs['config_id'], kwargs['execution_config_id'], '2026-09-10T21:00:00+08:00')

    monkeypatch.setattr(e2e, 'enqueue_scan', real_cli)
    db, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    assert task.status == 'completed', task
    assert calls
    with monkeypatch.context() as api_clock:
        _freeze_k10_clocks(api_clock, tick[0].isoformat())
        with client_for(db) as api:
            body = api.get('/api/v1/k10/v2/reports/latest?window=evening').json()
    assert len(body['report']['eveningCards']) == 1
    with sqlite3.connect(db) as c:
        checkpoint = json.loads(c.execute('SELECT checkpoint_json FROM k10_tasks WHERE task_id=?',(task_id,)).fetchone()[0])
    evening = store.get_scan(scan_id=checkpoint['scanId'], db_path=db)
    assert datetime.fromisoformat(evening['coverage']['sourceReplay']['nominalStartAt']) == datetime(2026,9,10,21,tzinfo=SHANGHAI)
    assert datetime.fromisoformat(evening['cutoffAt']) == datetime(2026,9,13,21,tzinfo=SHANGHAI)
    windows = store.list_company_windows(db_path=db)
    assert windows[0]['d1TradeDate']=='2026-09-14' and windows[0]['d2TradeDate']=='2026-09-15'

    tick[0] = datetime(2026,9,14,9,2,tzinfo=SHANGHAI)
    monkeypatch.setattr(pipeline, '_now', lambda: tick[0])
    class NoNewNews(e2e._News):
        def fetch_incremental(self, request):
            return SourceFetchResult(documents=(), next_cursor='monday', success_watermark=request.window.cutoff_at,
                pages_fetched=1, pages_expected=1, exhausted=True)
    monkeypatch.setattr(pipeline, 'TuShareMajorNewsAdapter', NoNewNews)
    errors = []
    execute = pipeline.execute_scan
    def captured_scan(**kwargs):
        try:
            return execute(**kwargs)
        except Exception as exc:
            errors.append(repr(exc))
            raise
    monkeypatch.setattr(pipeline, 'execute_scan', captured_scan)
    morning_id = enqueue(db, 'morning', date(2026,9,14), 1, 1, capsys, 'b39', 'b39-execution')
    morning = run_once(db_path=db, task_id=morning_id, worker_id='monday', lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token='fixture-token', parquet_dir=tmp_path/'parquet'), clock=lambda:tick[0])
    assert morning.status == 'completed', errors or morning
    with sqlite3.connect(db) as c:
        checkpoint = json.loads(c.execute('SELECT checkpoint_json FROM k10_tasks WHERE task_id=?',(morning_id,)).fetchone()[0])
    scan = store.get_scan(scan_id=checkpoint['scanId'], db_path=db)
    assert datetime.fromisoformat(scan['coverage']['sourceReplay']['nominalStartAt']) == datetime(2026,9,13,21,tzinfo=SHANGHAI)
    with monkeypatch.context() as api_clock:
        _freeze_k10_clocks(api_clock, tick[0].isoformat())
        with client_for(db) as api:
            actual = api.get('/api/v1/k10/v2/reports/latest?window=morning')
            assert actual.status_code == 200 and actual.json()['state'] == 'available'
