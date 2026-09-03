from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import polars as pl

from neckline.data.eod_validation import daily_basic_gaps
from scripts import backfill, daily_update


DAY = date(2026, 9, 3)


def _frame(*, free_share=100.0, turnover_rate=1.0, turnover_rate_f=1.2) -> pl.DataFrame:
    return pl.DataFrame({
        "ts_code": ["000001.SZ"],
        "trade_date": [DAY],
        "free_share": [free_share],
        "turnover_rate": [turnover_rate],
        "turnover_rate_f": [turnover_rate_f],
    })


def test_daily_basic_semantic_validator_rejects_provider_rows_with_empty_fields():
    gaps = daily_basic_gaps(
        DAY,
        _frame(free_share=None, turnover_rate=None, turnover_rate_f=None),
        expected_daily_codes=["000001.SZ"],
    )
    assert gaps == (
        "daily_basic free_share 有 1/1 行缺失或无效",
        "daily_basic turnover_rate 有 1/1 行缺失或无效",
        "daily_basic turnover_rate_f 有 1/1 行缺失或无效",
    )


def test_daily_basic_semantic_validator_checks_date_identity_and_daily_coverage():
    frame = _frame().with_columns(pl.lit(date(2026, 9, 2)).alias("trade_date"))
    gaps = daily_basic_gaps(
        DAY,
        frame,
        expected_daily_codes=["000001.SZ", "000002.SZ"],
    )
    assert any("交易日错配" in gap for gap in gaps)
    assert any("缺少当日 daily 代码 1/2" in gap for gap in gaps)


def test_daily_update_validator_accepts_complete_same_day_payload(monkeypatch):
    daily = pl.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [DAY]})
    monkeypatch.setattr(
        daily_update,
        "_read_day_partition",
        lambda table, target: daily if table == "daily" and target == DAY else None,
    )
    assert daily_update.validate_daily_basic_payload(DAY, _frame()) == ()


def test_selective_retry_refetches_only_the_incomplete_partition(monkeypatch):
    daily = pl.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [DAY]})
    complete = pl.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [DAY]})
    incomplete_basic = _frame(free_share=None, turnover_rate=None, turnover_rate_f=None)

    def read(table, target):
        assert target == DAY
        if table == "daily":
            return daily
        if table == "daily_basic":
            return incomplete_basic
        return complete

    monkeypatch.setattr(daily_update, "_read_day_partition", read)
    assert daily_update._day_tables_for_run(DAY, retry_incomplete=True) == ["daily_basic"]


def test_backfill_does_not_replace_partition_when_semantic_validation_fails(monkeypatch):
    invalid = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "trade_date": ["20260903"],
        "free_share": [None],
        "turnover_rate": [None],
        "turnover_rate_f": [None],
    })
    monkeypatch.setattr(
        backfill,
        "ts_daily_basic_all",
        lambda _: SimpleNamespace(ok=True, data=invalid, reason=""),
    )
    writes = []
    audits = []
    monkeypatch.setattr(backfill, "write_table_day", lambda *args: writes.append(args))
    monkeypatch.setattr(backfill, "_log_audit", lambda *args: audits.append(args))

    stats = backfill.backfill_day_tables(
        [DAY],
        ["daily_basic"],
        force=True,
        payload_validators={
            "daily_basic": lambda target, frame: daily_basic_gaps(
                target, frame, expected_daily_codes=["000001.SZ"]
            )
        },
    )

    assert writes == []
    assert stats["daily_basic"] == {"fetched": 0, "skipped": 0, "failed": 1, "rows": 0}
    assert audits == [("daily_basic", DAY, "invalid", 1)]


def test_backfill_writes_only_after_semantic_validation_succeeds(monkeypatch):
    valid = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "trade_date": ["20260903"],
        "free_share": [100.0],
        "turnover_rate": [1.0],
        "turnover_rate_f": [1.2],
    })
    monkeypatch.setattr(
        backfill,
        "ts_daily_basic_all",
        lambda _: SimpleNamespace(ok=True, data=valid, reason=""),
    )
    writes = []
    monkeypatch.setattr(backfill, "write_table_day", lambda *args: writes.append(args))
    monkeypatch.setattr(backfill, "_log_audit", lambda *args: None)

    stats = backfill.backfill_day_tables(
        [DAY],
        ["daily_basic"],
        force=True,
        payload_validators={
            "daily_basic": lambda target, frame: daily_basic_gaps(
                target, frame, expected_daily_codes=["000001.SZ"]
            )
        },
    )

    assert len(writes) == 1
    assert stats["daily_basic"] == {"fetched": 1, "skipped": 0, "failed": 0, "rows": 1}
