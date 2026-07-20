"""`scripts/smoke_sentinel.py` 合成盘中快照的纯函数单测(plan 阶段3「合成盘中式
冒烟」)。只测 `_synthesize_price`/`synthesize_quote` 这两个从真实 EOD OHLC 反推
检查点行情的纯函数——脚本其余部分(复制DB/调用build_report/跑engine)是手工
验证的探索性工具,同项目里 `scripts/report.py`/`scripts/backfill.py` 一样不纳入
pytest(见 PROJECT_PLAN.md 阶段0 遗留问题#5 的既有先例),但这两个"造假数据"的
纯函数值得锁死,避免以后改坏了自己都不知道。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import smoke_sentinel as smoke  # noqa: E402


def _row(ts_code="600001.SH", *, open_=10.0, high=10.5, low=9.5, close=9.6, pre_close=10.0, vol=100000.0, amount=1000.0):
    return {"ts_code": ts_code, "open": open_, "high": high, "low": low, "close": close, "pre_close": pre_close,
            "vol": vol, "amount": amount}


class TestSynthesizePrice:
    def test_final_checkpoint_uses_real_close(self):
        cp = smoke.Checkpoint("尾盘", 14, 50, 1.0, is_final=True)
        row = _row(close=9.6)
        assert smoke._synthesize_price(row, cp) == 9.6

    def test_down_day_interpolates_toward_low(self):
        cp = smoke.Checkpoint("盘中", 10, 35, 0.45)
        row = _row(open_=10.0, low=9.0, close=9.5)  # 下跌日(close<=open)
        price = smoke._synthesize_price(row, cp)
        # 10.0 - (10.0-9.0)*0.6 = 9.4,严格介于 open 与 low 之间,偏向 low
        assert price == pytest.approx(9.4)
        assert row["low"] <= price <= row["open"]

    def test_up_day_interpolates_toward_high(self):
        cp = smoke.Checkpoint("盘中", 10, 35, 0.45)
        row = _row(open_=10.0, high=11.0, close=10.8)  # 上涨日(close>open)
        price = smoke._synthesize_price(row, cp)
        # 10.0 + (11.0-10.0)*0.6 = 10.6
        assert price == pytest.approx(10.6)
        assert row["open"] <= price <= row["high"]

    def test_early_checkpoint_is_not_final_uses_interpolation_not_close(self):
        """早盘检查点(is_final=False)不应直接等于收盘价——它反映的是"当时"的
        合成价,不是"事后已知"的收盘价(否则会有前视嫌疑,即便只是冒烟脚本也不
        该留这个坏习惯)。"""
        cp = smoke.Checkpoint("早盘", 9, 45, 0.10)
        row = _row(open_=10.0, low=9.0, high=10.5, close=8.0)  # 收盘远低于插值结果
        price = smoke._synthesize_price(row, cp)
        assert price != row["close"]


class TestSynthesizeQuote:
    def test_final_checkpoint_uses_real_high_low(self):
        cp = smoke.Checkpoint("尾盘", 14, 50, 1.0, is_final=True)
        row = _row(open_=10.0, high=10.8, low=9.2, close=9.6, pre_close=10.0)
        q = smoke.synthesize_quote(row, cp)
        assert q.high == pytest.approx(10.8)
        assert q.low == pytest.approx(9.2)
        assert q.price == pytest.approx(9.6)

    def test_non_final_checkpoint_derives_high_low_from_open_and_price(self):
        cp = smoke.Checkpoint("盘中", 10, 35, 0.45)
        row = _row(open_=10.0, low=9.0, high=10.5, close=9.5)
        q = smoke.synthesize_quote(row, cp)
        assert q.high == max(row["open"], q.price)
        assert q.low == min(row["open"], q.price)

    def test_volume_and_amount_scaled_by_fraction_no_unit_conversion_needed(self):
        """daily.vol 单位=手,Quote.volume 单位也=手——不应再做 /100 这类多余换算
        (施工中曾经手滑写过一次,已发现修正,这里锁死回归)。daily.amount 单位
        =千元,Quote.amount 单位=元,应 ×1000。"""
        cp = smoke.Checkpoint("尾盘", 14, 50, 0.5, is_final=True)
        row = _row(vol=200000.0, amount=5000.0)
        q = smoke.synthesize_quote(row, cp)
        assert q.volume == pytest.approx(200000.0 * 0.5)
        assert q.amount == pytest.approx(5000.0 * 1000.0 * 0.5)

    def test_pre_close_and_open_passed_through_unchanged(self):
        cp = smoke.Checkpoint("早盘", 9, 45, 0.10)
        row = _row(open_=10.2, pre_close=10.0)
        q = smoke.synthesize_quote(row, cp)
        assert q.pre_close == pytest.approx(10.0)
        assert q.open == pytest.approx(10.2)

    def test_source_marked_synthetic(self):
        cp = smoke.Checkpoint("早盘", 9, 45, 0.10)
        q = smoke.synthesize_quote(_row(), cp)
        assert q.source == "synthetic"
