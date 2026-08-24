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
