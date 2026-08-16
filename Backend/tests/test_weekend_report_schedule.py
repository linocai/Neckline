"""Friday-report deferral and stale-report guardrails."""

from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

from scripts import evening as evening_script


BACKEND_ROOT = Path(__file__).resolve().parent.parent


def test_sunday_slot_binds_exactly_to_the_immediately_preceding_friday():
    assert evening_script._scheduled_trade_date(date(2026, 8, 16)) == date(2026, 8, 14)


def test_weekday_slot_stays_on_that_calendar_day():
    assert evening_script._scheduled_trade_date(date(2026, 8, 17)) == date(2026, 8, 17)
    assert evening_script._scheduled_trade_date(date(2026, 8, 20)) == date(2026, 8, 20)


def test_scheduled_holiday_is_clean_noop_and_never_falls_back(monkeypatch):
    monkeypatch.setattr(evening_script, "_today", lambda: date(2026, 8, 16))
    monkeypatch.setattr(evening_script, "is_trading_day", lambda value: False)
    monkeypatch.setattr(evening_script, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(
        evening_script,
        "run_evening_chain",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    monkeypatch.setattr(sys, "argv", ["evening.py", "--scheduled"])
    assert evening_script.main() == 0


def test_sunday_slot_skips_when_friday_report_was_already_generated_that_day(
    tmp_path, monkeypatch,
):
    db_path = tmp_path / "scheduled.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE reports (trade_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO reports VALUES (?, ?)",
            ("20260814", "2026-08-16T10:30:00+00:00"),
        )

    assert evening_script._report_generated_on_local_day(
        date(2026, 8, 14), date(2026, 8, 16), db_path,
    )
    assert not evening_script._report_generated_on_local_day(
        date(2026, 8, 14), date(2026, 8, 15), db_path,
    )

    monkeypatch.setattr(evening_script, "_today", lambda: date(2026, 8, 16))
    monkeypatch.setattr(evening_script, "is_trading_day", lambda value: True)
    monkeypatch.setattr(evening_script, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(
        evening_script,
        "run_evening_chain",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    monkeypatch.setattr(sys, "argv", ["evening.py", "--scheduled", "--db", str(db_path)])
    assert evening_script.main() == 0


def test_timer_and_all_three_services_share_the_scheduled_date_contract():
    timer = (BACKEND_ROOT / "deploy" / "neckline-evening.timer").read_text(encoding="utf-8")
    calendars = [line for line in timer.splitlines() if line.startswith("OnCalendar=")]
    assert calendars == [
        "OnCalendar=Mon-Thu 16:35 Asia/Shanghai",
        "OnCalendar=Sun 19:00 Asia/Shanghai",
    ]

    service_names = ("neckline-scan.service", "neckline-basket.service", "neckline-report.service")
    for service_name in service_names:
        unit = (BACKEND_ROOT / "deploy" / service_name).read_text(encoding="utf-8")
        exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
        assert "scripts/evening.py --scheduled " in exec_start

    basket = (BACKEND_ROOT / "deploy" / "neckline-basket.service").read_text(encoding="utf-8")
    assert "direction-pipeline.v2.4.2-balanced.json" in basket
