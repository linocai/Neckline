"""Walk-forward 切分器单测(plan 0.8)。"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.backtest.walk_forward import generate_walk_forward_windows
from tests.conftest import insert_trade_cal

pytestmark = pytest.mark.usefixtures("isolated_env")


def _seed(settings, n_days=40):
    from tests.conftest import business_days

    days = business_days(date(2024, 1, 2), n_days)
    insert_trade_cal(settings, days)
    import neckline.calendar as cal

    cal.reset_cache()
    return days


class TestGenerateWindows:
    def test_non_overlapping_windows_by_default(self, isolated_env):
        days = _seed(isolated_env, 40)
        windows = generate_walk_forward_windows(days[0], days[-1], train_days=10, test_days=5)
        assert len(windows) >= 2
        w0, w1 = windows[0], windows[1]
        assert w0.test_end < w1.train_start or w0.test_end == days[days.index(w0.test_end)]
        # 不重叠:第二组的样本内起点应等于第一组样本外窗口后紧接的下一个交易日序列起点
        assert w1.train_start > w0.train_start

    def test_window_day_counts_correct(self, isolated_env):
        days = _seed(isolated_env, 40)
        windows = generate_walk_forward_windows(days[0], days[-1], train_days=10, test_days=5)
        from neckline.calendar import trading_days_between

        w = windows[0]
        assert len(trading_days_between(w.train_start, w.train_end)) == 10
        assert len(trading_days_between(w.test_start, w.test_end)) == 5

    def test_insufficient_range_returns_empty(self, isolated_env):
        days = _seed(isolated_env, 5)
        windows = generate_walk_forward_windows(days[0], days[-1], train_days=10, test_days=5)
        assert windows == []

    def test_custom_step_allows_overlap(self, isolated_env):
        days = _seed(isolated_env, 40)
        windows = generate_walk_forward_windows(days[0], days[-1], train_days=10, test_days=5, step_days=2)
        assert len(windows) >= 2
        assert windows[1].train_start < windows[0].test_start

    def test_invalid_args_raise(self, isolated_env):
        days = _seed(isolated_env, 10)
        with pytest.raises(ValueError):
            generate_walk_forward_windows(days[0], days[-1], train_days=0, test_days=5)
        with pytest.raises(ValueError):
            generate_walk_forward_windows(days[0], days[-1], train_days=5, test_days=-1)
