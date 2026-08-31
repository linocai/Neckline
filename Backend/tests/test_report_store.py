"""K9 报告存储只测试现役双日期与三态契约。"""

from __future__ import annotations

from datetime import date

from neckline.db import init_schema
from neckline.report import store


def _save(db, trade_date, report_date, state="has_list", size=2):
    init_schema(db)
    store.save_k9_report(
        trade_date=trade_date, report_date=report_date, state=state,
        headline="标题", gaps=[], markdown="# 标题", structured={"ok": True},
        strategy="K9", strategy_version="K9-v3",
        params_package_version="p", pack_id="id", pack_version="fp-4",
        listing_size=size,
        db_path=db)


def test_both_dates_survive_round_trip(tmp_path):
    db = tmp_path / "report.db"
    _save(db, date(2026, 8, 14), date(2026, 8, 16))
    row = store.load_k9_report(date(2026, 8, 14), db_path=db)
    assert row["trade_date"] == "20260814" and row["report_date"] == "20260816"


def test_not_run_null_and_trusted_empty_zero_stay_distinct(tmp_path):
    db = tmp_path / "states.db"
    _save(db, date(2026, 8, 14), date(2026, 8, 16), "not_run", None)
    _save(db, date(2026, 8, 15), date(2026, 8, 15), "empty", 0)
    assert store.load_k9_report(date(2026, 8, 14), db_path=db)["listing_size"] is None
    assert store.load_k9_report(date(2026, 8, 15), db_path=db)["listing_size"] == 0


def test_same_trade_date_rerun_replaces_in_place(tmp_path):
    db = tmp_path / "rerun.db"
    day = date(2026, 8, 21)
    _save(db, day, day, "empty", 0)
    _save(db, day, day, "has_list", 3)
    row = store.load_k9_report(day, db_path=db)
    assert row["state"] == "has_list" and row["listing_size"] == 3


def test_not_run_retry_cannot_downgrade_a_trusted_report(tmp_path):
    db = tmp_path / "preserve.db"
    day = date(2026, 8, 21)
    _save(db, day, day, "has_list", 3)
    _save(db, day, day, "not_run", None)
    row = store.load_k9_report(day, db_path=db)
    assert row["state"] == "has_list" and row["listing_size"] == 3


def test_reading_unmigrated_or_missing_date_is_safe_empty(tmp_path):
    db = tmp_path / "unmigrated.db"
    db.touch()
    assert store.load_k9_report(date(2026, 8, 21), db_path=db) is None
