"""`scripts/smoke_precall.py` 合成集合竞价快照的纯函数单测(plan v1.1-A「合成竞价式
冒烟」)。只锁 `synthesize_auction_quote` 这个从真实 EOD 反推竞价快照的纯函数——脚本
其余部分(复制 DB / build_report / 跑 run_precall_tick)是探索性工具,同
`smoke_sentinel.py` 一样不纳入 pytest(见 test_smoke_sentinel_synth.py 的既有先例),
但「造竞价数据」的纯函数值得锁死,避免改坏了不自知。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import smoke_precall as smoke  # noqa: E402


def _row(ts_code="600001.SH", *, open_=10.2, pre_close=10.0, vol=200000.0, amount=5000.0):
    return {"ts_code": ts_code, "open": open_, "high": 10.5, "low": 9.9, "close": 10.3,
            "pre_close": pre_close, "vol": vol, "amount": amount}


def test_auction_quote_uses_open_as_price():
    """集合竞价阶段 open 即当前价:price==open,high/low 均=open(竞价无盘中波动)。"""
    q = smoke.synthesize_auction_quote(_row(open_=10.2))
    assert q.price == pytest.approx(10.2)
    assert q.open == pytest.approx(10.2)
    assert q.high == pytest.approx(10.2)
    assert q.low == pytest.approx(10.2)


def test_pre_close_passed_through():
    q = smoke.synthesize_auction_quote(_row(pre_close=10.0))
    assert q.pre_close == pytest.approx(10.0)


def test_auction_volume_is_fraction_of_daily_no_unit_conversion():
    """daily.vol 单位=手,Quote.volume 也=手(不 /100);竞价量 = 当日量 × AUCTION_VOL_FRAC。
    daily.amount 单位=千元 → ×1000 归元,再乘同一比例。"""
    q = smoke.synthesize_auction_quote(_row(vol=200000.0, amount=5000.0))
    assert q.volume == pytest.approx(200000.0 * smoke.AUCTION_VOL_FRAC)
    assert q.amount == pytest.approx(5000.0 * 1000.0 * smoke.AUCTION_VOL_FRAC)


def test_source_marked_synthetic_auction():
    q = smoke.synthesize_auction_quote(_row())
    assert q.source == "synthetic-auction"
