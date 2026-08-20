"""盘中数据纯函数单测(plan 阶段3 §2.4)。移植自 LinoN
`/Users/linotsai/Lino/LinoN/backend/tests/test_intraday.py`(逻辑不变,函数改名
`_is_intraday_window`→`is_intraday_now`、`Quote` 换本包实现),交易日历改用
`isolated_env` + `insert_trade_cal` 隔离(不依赖真实 `data/neckline.db`)。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import pytest

from tests.conftest import insert_trade_cal

from neckline.sentinel.intraday import elapsed_trading_minutes, intraday_vol_ratio, is_intraday_now, vwap_of
from neckline.data.realtime import Quote

pytestmark = pytest.mark.usefixtures("isolated_env")

_TRADING_DATE = date(2024, 1, 2)       # 周二
_NON_TRADING_DATE = date(2024, 1, 6)   # 周六


@pytest.fixture(autouse=True)
def _seed_calendar(isolated_env):
    days = [_TRADING_DATE + timedelta(days=i) for i in range(-10, 10) if (_TRADING_DATE + timedelta(days=i)).weekday() < 5]
    insert_trade_cal(isolated_env, days)


def _dt(d: date, hh: int, mm: int) -> datetime:
    return datetime.combine(d, datetime.min.time().replace(hour=hh, minute=mm))


def _make_quote(
    price: float,
    volume: float,
    *,
    pre_close: float = 10.0,
    open_: float = 10.0,
    amount: Optional[float] = None,
) -> Quote:
    """构造真实比例的假 Quote:amount ≈ price × volume × 100(元)。致命回归门:
    若 vwap_of 误写 amount/volume(少除 100),用这组真实比例数据算出的 vwap
    会离谱大 100 倍,is_above_vwap 判定会翻转出错,可被断言抓出。"""
    if amount is None:
        amount = price * volume * 100.0
    return Quote(
        code="000001", name="测试股", price=price, pre_close=pre_close, open=open_,
        high=max(price, open_), low=min(price, open_), volume=volume, amount=amount,
        ts="2024-01-02 10:30:00", source="sina",
    )


class TestIsIntradayNow:
    def test_true_at_noon_break(self):
        """午休 12:00 仍算盘中(明定死行为)。"""
        assert is_intraday_now(_dt(_TRADING_DATE, 12, 0)) is True

    def test_true_at_open_and_just_before_close(self):
        assert is_intraday_now(_dt(_TRADING_DATE, 9, 30)) is True
        assert is_intraday_now(_dt(_TRADING_DATE, 14, 59)) is True

    def test_false_before_open(self):
        assert is_intraday_now(_dt(_TRADING_DATE, 9, 20)) is False

    def test_false_at_and_after_close(self):
        assert is_intraday_now(_dt(_TRADING_DATE, 15, 1)) is False
        assert is_intraday_now(_dt(_TRADING_DATE, 15, 0)) is False  # 边界:< 15:00 才算

    def test_false_on_non_trading_day(self):
        assert is_intraday_now(_dt(_NON_TRADING_DATE, 10, 0)) is False


class TestElapsedTradingMinutes:
    def test_before_open_is_zero(self):
        assert elapsed_trading_minutes(_dt(_TRADING_DATE, 9, 0)) == 0

    def test_morning_partial(self):
        assert elapsed_trading_minutes(_dt(_TRADING_DATE, 10, 30)) == 60

    def test_across_noon_break_stays_120(self):
        assert elapsed_trading_minutes(_dt(_TRADING_DATE, 12, 0)) == 120
        assert elapsed_trading_minutes(_dt(_TRADING_DATE, 12, 30)) == 120

    def test_afternoon(self):
        assert elapsed_trading_minutes(_dt(_TRADING_DATE, 13, 30)) == 150
        assert elapsed_trading_minutes(_dt(_TRADING_DATE, 14, 59)) == 239

    def test_closing_edge_is_full_day(self):
        assert elapsed_trading_minutes(_dt(_TRADING_DATE, 15, 0)) == 240
        assert elapsed_trading_minutes(_dt(_TRADING_DATE, 20, 0)) == 240


class TestIntradayVolRatio:
    def test_early_below_60min(self):
        ratio, note = intraday_vol_ratio(current_vol=1000, prev5_avg_vol=2000, elapsed_min=59)
        assert ratio is None and note == "early"

    def test_ok_normal_case(self):
        ratio, note = intraday_vol_ratio(current_vol=120000, prev5_avg_vol=200000, elapsed_min=120)
        assert ratio == 1.2 and note == "ok"

    def test_no_base_when_prev5_zero_or_negative(self):
        ratio, note = intraday_vol_ratio(current_vol=1000, prev5_avg_vol=0, elapsed_min=120)
        assert ratio is None and note == "no_base"
        ratio2, note2 = intraday_vol_ratio(current_vol=1000, prev5_avg_vol=-5, elapsed_min=120)
        assert ratio2 is None and note2 == "no_base"

    def test_closed_edge_at_240(self):
        ratio, note = intraday_vol_ratio(current_vol=200000, prev5_avg_vol=200000, elapsed_min=240)
        assert ratio == 1.0 and note == "closed"


class TestVwapOf:
    def test_price_above_vwap_true(self):
        q = _make_quote(price=11.0, volume=1000, pre_close=10.0, amount=11.0 * 1000 * 100 * 0.9)
        vwap, is_above = vwap_of(q)
        assert vwap is not None
        assert vwap < 11.0
        assert is_above is True

    def test_price_below_vwap_false(self):
        q = _make_quote(price=9.0, volume=1000, pre_close=10.0, amount=9.0 * 1000 * 100 * 1.2)
        vwap, is_above = vwap_of(q)
        assert vwap is not None
        assert vwap > 9.0
        assert is_above is False

    def test_zero_volume_degrades_to_none(self):
        q = _make_quote(price=10.0, volume=0, amount=0)
        assert vwap_of(q) == (None, None)

    def test_none_quote_degrades_to_none(self):
        assert vwap_of(None) == (None, None)
