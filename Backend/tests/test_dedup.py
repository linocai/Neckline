"""现役竞价任务的跨进程防重台账。"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.dedup import already_pushed, record_pushed

pytestmark = pytest.mark.usefixtures("isolated_env")

D = date(2026, 7, 20)


def test_false_before_any_task_run(isolated_env):
    assert not already_pushed(
        D, "auction", "", "checklist_tick", db_path=isolated_env.db_path
    )


def test_record_survives_a_new_connection(isolated_env):
    record_pushed(D, "auction", "", "checklist_tick", db_path=isolated_env.db_path)
    assert already_pushed(
        D, "auction", "", "checklist_tick", db_path=isolated_env.db_path
    )


def test_auction_and_open30_tasks_are_independent(isolated_env):
    record_pushed(D, "auction", "", "checklist_tick", db_path=isolated_env.db_path)
    assert already_pushed(
        D, "auction", "", "checklist_tick", db_path=isolated_env.db_path
    )
    assert not already_pushed(
        D, "auction", "", "settle_tick", db_path=isolated_env.db_path
    )


def test_same_task_on_next_day_is_independent(isolated_env):
    record_pushed(D, "auction", "", "checklist_tick", db_path=isolated_env.db_path)
    assert not already_pushed(
        date(2026, 7, 21), "auction", "", "checklist_tick", db_path=isolated_env.db_path
    )


def test_double_record_is_idempotent(isolated_env):
    record_pushed(D, "auction", "", "settle_tick", db_path=isolated_env.db_path)
    record_pushed(D, "auction", "", "settle_tick", db_path=isolated_env.db_path)
    from neckline.db import connection

    with connection(isolated_env.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM job_events WHERE trade_date=? AND event_key=?",
            ("20260720", "settle_tick"),
        ).fetchone()[0]
    assert count == 1


def test_upgrade_keeps_only_active_auction_records(tmp_path):
    import sqlite3

    from neckline.db import init_schema

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE sentinel_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              trade_date TEXT NOT NULL, sentinel TEXT NOT NULL,
              ts_code TEXT NOT NULL DEFAULT '', event_key TEXT NOT NULL,
              payload_json TEXT NOT NULL DEFAULT '{}', pushed_at TEXT NOT NULL,
              UNIQUE(trade_date, sentinel, ts_code, event_key)
            );
            INSERT INTO sentinel_events
              (trade_date,sentinel,ts_code,event_key,payload_json,pushed_at)
            VALUES
              ('20260720','auction','','checklist_tick','{}','now'),
              ('20260720','retreat','','brake','{}','now');
        """)
    init_schema(db)
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT scope,event_key FROM job_events ORDER BY event_key"
        ).fetchall()
        old_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sentinel_events'"
        ).fetchone()
    assert rows == [("auction", "checklist_tick")]
    assert old_table is None
