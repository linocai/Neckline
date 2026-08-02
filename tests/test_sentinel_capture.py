"""V2-⑧-B 盘中存拍(plan §五 V2-⑧-B 验收:写-读往返 dtype 合声明 + `capture_status`
三态 + 落盘失败不拖垮哨兵)。

⚠ 本文件**不测**四哨兵任何判定 —— 存拍是旁路,判定归 `test_sentinel_engine.py`。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from tests.conftest import business_days, insert_trade_cal

from neckline.data.market_data import TABLE_FLOAT_COLS, day_file_path
from neckline.sentinel import capture

pytestmark = pytest.mark.usefixtures("isolated_env")

D = date(2026, 7, 24)


class _Q:
    def __init__(self, price=10.0, volume=100.0, amount=1000.0, pre_close=9.5, source="sina"):
        self.price, self.volume, self.amount = price, volume, amount
        self.pre_close, self.source = pre_close, source
        self.low = price
        self.high = price
        self.open = price


@pytest.fixture(autouse=True)
def _clean_buffers():
    capture.reset_capture_state()
    yield
    capture.reset_capture_state()


def _tick(hh, mm, quotes):
    return capture.record_intraday_tick(D, datetime(D.year, D.month, D.day, hh, mm), quotes)


# ══════════════════════════════════════════════════════════════════════════
# 写-读往返 + dtype 合声明
# ══════════════════════════════════════════════════════════════════════════

def test_round_trip_dtypes_match_declaration(isolated_env):
    insert_trade_cal(isolated_env, business_days(date(2026, 7, 20), 5))
    _tick(9, 30, {"600000.SH": _Q(10.0, 100.0, 1000.0)})
    _tick(9, 31, {"600000.SH": _Q(10.2, 250.0, 2600.0)})
    _tick(14, 59, {"600000.SH": _Q(10.4, 900.0, 9300.0)})
    capture.record_auction_snapshot(D, datetime(D.year, D.month, D.day, 9, 25),
                                    {"600000.SH": _Q(9.9, 30.0, 300.0, pre_close=9.5)})

    res = capture.flush_day(D, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    assert res.ran and res.tick_rows == 3 and res.auction_rows == 1

    df = pl.read_parquet(day_file_path("intraday_ticks", D, isolated_env.parquet_dir))
    assert df.height == 3
    for col in TABLE_FLOAT_COLS["intraday_ticks"]:
        assert df.schema[col] == pl.Float64, col
    assert df.schema["trade_date"] == pl.Date and df.schema["ts"] == pl.String
    assert set(df["ts"]) == {"09:30:00", "09:31:00", "14:59:00"}
    assert df["source"].to_list() == ["sina"] * 3

    auc = pl.read_parquet(day_file_path("auction_snapshots", D, isolated_env.parquet_dir))
    for col in TABLE_FLOAT_COLS["auction_snapshots"]:
        assert auc.schema[col] == pl.Float64, col
    assert auc["gap_pct"][0] == pytest.approx(9.9 / 9.5 - 1)


def test_first_tick_delta_is_null_not_zero(isolated_env):
    """当日首次观测算不出"本拍增量" → 落 `null`,⛔ 不写 0 冒充"这一分钟没成交"。"""
    insert_trade_cal(isolated_env, business_days(date(2026, 7, 20), 5))
    _tick(9, 30, {"600000.SH": _Q(10.0, 100.0, 1000.0)})
    _tick(9, 31, {"600000.SH": _Q(10.1, 260.0, 2700.0)})
    capture.flush_day(D, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    df = pl.read_parquet(day_file_path("intraday_ticks", D, isolated_env.parquet_dir)).sort("ts")
    assert df["volume"][0] is None and df["amount"][0] is None
    assert df["volume"][1] == pytest.approx(160.0) and df["amount"][1] == pytest.approx(1700.0)
    assert df["cum_volume"].to_list() == [100.0, 260.0]


def test_source_rollback_yields_null_delta_not_negative(isolated_env):
    """免费源快照抖动导致累计值回退 → 增量落 `null`(数据有问题不伪装成没成交)。"""
    insert_trade_cal(isolated_env, business_days(date(2026, 7, 20), 5))
    _tick(9, 30, {"600000.SH": _Q(10.0, 500.0, 5000.0)})
    _tick(9, 31, {"600000.SH": _Q(10.1, 400.0, 4000.0)})
    capture.flush_day(D, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    df = pl.read_parquet(day_file_path("intraday_ticks", D, isolated_env.parquet_dir)).sort("ts")
    assert df["volume"][1] is None


# ══════════════════════════════════════════════════════════════════════════
# capture_status 三态
# ══════════════════════════════════════════════════════════════════════════

def test_status_missing_when_nothing_captured(isolated_env):
    insert_trade_cal(isolated_env, business_days(date(2026, 7, 20), 5))
    res = capture.flush_day(D, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    assert res.tick_status == capture.STATUS_MISSING and res.tick_rows == 0
    assert not day_file_path("intraday_ticks", D, isolated_env.parquet_dir).exists()
    # 台账仍要落 —— 「没记过」与「记了是 missing」必须分得开
    st = capture.load_capture_status(D, db_path=isolated_env.db_path)
    assert st["recorded"] is True and st["capture_status"] == capture.STATUS_MISSING


def test_status_full_when_head_and_tail_covered(isolated_env):
    insert_trade_cal(isolated_env, business_days(date(2026, 7, 20), 5))
    _tick(9, 32, {"600000.SH": _Q()})       # 开盘后 2 分钟(在宽限内)
    _tick(11, 0, {"600000.SH": _Q()})
    _tick(14, 58, {"600000.SH": _Q()})      # 收盘前 2 分钟(在宽限内)
    res = capture.flush_day(D, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    assert res.tick_status == capture.STATUS_FULL
    assert res.covered_minutes == 3 and res.expected_minutes == 240      # 原始数字如实落
    st = capture.load_capture_status(D, db_path=isolated_env.db_path)
    assert st["covered_minutes"] == 3 and st["empty_ticks"] == 0


def test_status_partial_when_started_late(isolated_env):
    """进程中途启动(首条 tick 已过开盘宽限)→ `partial`,⛔ 不装完整。"""
    insert_trade_cal(isolated_env, business_days(date(2026, 7, 20), 5))
    _tick(13, 30, {"600000.SH": _Q()})
    _tick(14, 59, {"600000.SH": _Q()})
    res = capture.flush_day(D, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    assert res.tick_status == capture.STATUS_PARTIAL


def test_status_partial_when_a_tick_came_back_empty(isolated_env):
    insert_trade_cal(isolated_env, business_days(date(2026, 7, 20), 5))
    _tick(9, 31, {"600000.SH": _Q()})
    _tick(10, 0, {})                        # 免费源这一拍全挂
    _tick(14, 59, {"600000.SH": _Q()})
    res = capture.flush_day(D, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    assert res.tick_status == capture.STATUS_PARTIAL and res.empty_ticks == 1


def test_status_partial_when_process_died_before_close(isolated_env):
    insert_trade_cal(isolated_env, business_days(date(2026, 7, 20), 5))
    _tick(9, 31, {"600000.SH": _Q()})
    _tick(13, 0, {"600000.SH": _Q()})       # 之后进程挂了
    res = capture.flush_day(D, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    assert res.tick_status == capture.STATUS_PARTIAL


def test_auction_status_partial_when_some_codes_missing(isolated_env):
    insert_trade_cal(isolated_env, business_days(date(2026, 7, 20), 5))
    capture.record_auction_snapshot(
        D, datetime(D.year, D.month, D.day, 9, 25), {"600000.SH": _Q()}, requested=3)
    res = capture.flush_day(D, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    assert res.auction_status == capture.STATUS_PARTIAL
    assert capture.load_capture_status(
        D, capture.EVENT_AUCTION, db_path=isolated_env.db_path)["requested"] == 3


def test_capture_status_unrecorded_day_is_not_full(isolated_env):
    st = capture.load_capture_status(date(2026, 7, 23), db_path=isolated_env.db_path)
    assert st == {"capture_status": capture.STATUS_MISSING, "recorded": False}


# ══════════════════════════════════════════════════════════════════════════
# 幂等 / 韧性
# ══════════════════════════════════════════════════════════════════════════

def test_flush_is_idempotent_across_restart(isolated_env):
    insert_trade_cal(isolated_env, business_days(date(2026, 7, 20), 5))
    _tick(9, 31, {"600000.SH": _Q()})
    first = capture.flush_day(D, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    assert first.ran
    capture.reset_capture_state()           # 模拟进程重启(内存标记没了,台账还在)
    second = capture.flush_day(D, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    assert second.ran is False              # 幂等:不会拿空 buffer 把当日文件覆盖成 0 行
    df = pl.read_parquet(day_file_path("intraday_ticks", D, isolated_env.parquet_dir))
    assert df.height == 1


def test_auction_recorded_only_once_per_day(isolated_env):
    now = datetime(D.year, D.month, D.day, 9, 25)
    assert capture.record_auction_snapshot(D, now, {"600000.SH": _Q()}) == 1
    assert capture.record_auction_snapshot(D, now, {"600001.SH": _Q()}) == 0


def test_write_failure_is_swallowed_and_reported(isolated_env, monkeypatch):
    """落盘失败只 WARNING + 状态标 missing,**绝不抛给调用方**(存拍是旁路)。"""
    insert_trade_cal(isolated_env, business_days(date(2026, 7, 20), 5))
    _tick(9, 31, {"600000.SH": _Q()})
    import neckline.data.market_data as md

    def _boom(*a, **k):
        raise OSError("磁盘满了")

    monkeypatch.setattr(md, "write_table_day", _boom)
    res = capture.flush_day(D, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    assert res.tick_status == capture.STATUS_MISSING and res.errors
    assert "intraday_ticks" in res.errors[0]


def test_record_tick_never_raises_on_weird_quotes(isolated_env):
    """畸形 / 缺字段的行情对象不许掀翻存拍(更不许掀翻哨兵那一拍)。"""
    class _Weird:
        price = None
        volume = "x"
        amount = None
        source = None

    assert _tick(9, 31, {"600000.SH": _Weird()}) == 1
    assert capture.buffered_rows(D) == 1


# ══════════════════════════════════════════════════════════════════════════
# 窗口判定
# ══════════════════════════════════════════════════════════════════════════

def test_windows(isolated_env):
    insert_trade_cal(isolated_env, business_days(date(2026, 7, 20), 5))
    assert capture.is_auction_capture_window(datetime(2026, 7, 24, 9, 25))
    assert not capture.is_auction_capture_window(datetime(2026, 7, 24, 9, 24))
    assert not capture.is_auction_capture_window(datetime(2026, 7, 24, 9, 30))
    assert capture.is_flush_window(datetime(2026, 7, 24, 15, 5))
    assert capture.is_flush_window(datetime(2026, 7, 24, 15, 34))
    assert not capture.is_flush_window(datetime(2026, 7, 24, 15, 4))
    assert not capture.is_flush_window(datetime(2026, 7, 24, 15, 35))
    assert not capture.is_flush_window(datetime(2026, 7, 25, 15, 10))     # 周六
