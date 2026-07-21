"""持仓台账单测(plan 阶段3)。极简 CRUD——开仓/清仓/查询,不做任何仓位纪律
校验或盈亏计算(系统只审计不拦人手动录入,§3.8 铁律「系统永不自动下单」的
延伸:本表也不该替用户判断"能不能开这仓")。"""

from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import insert_trade_cal

from neckline.sentinel.positions import (
    STATUS_CLOSED,
    STATUS_OPEN,
    close_position,
    count_opens_on,
    d_count,
    get_position,
    load_all_positions,
    load_open_positions,
    open_position,
)

pytestmark = pytest.mark.usefixtures("isolated_env")


class TestOpenPosition:
    def test_open_returns_id_and_persists(self, isolated_env):
        pid = open_position("600519.SH", 1720.0, 100, date(2026, 7, 20), db_path=isolated_env.db_path)
        assert pid > 0
        pos = get_position(pid, db_path=isolated_env.db_path)
        assert pos is not None
        assert pos.ts_code == "600519.SH"
        assert pos.buy_price == pytest.approx(1720.0)
        assert pos.qty == 100
        assert pos.buy_date == "20260720"
        assert pos.status == STATUS_OPEN
        assert pos.sell_price is None

    def test_note_optional(self, isolated_env):
        pid = open_position("600519.SH", 10.0, 100, date(2026, 7, 20), note="低吸建仓", db_path=isolated_env.db_path)
        assert get_position(pid, db_path=isolated_env.db_path).note == "低吸建仓"

    def test_same_code_can_open_multiple_batches(self, isolated_env):
        """一票可分批多次开仓,各自一行,不合并。"""
        p1 = open_position("600519.SH", 10.0, 100, date(2026, 7, 20), db_path=isolated_env.db_path)
        p2 = open_position("600519.SH", 10.5, 100, date(2026, 7, 21), db_path=isolated_env.db_path)
        assert p1 != p2
        open_positions = load_open_positions(db_path=isolated_env.db_path)
        assert len([p for p in open_positions if p.ts_code == "600519.SH"]) == 2


class TestClosePosition:
    def test_close_updates_status_and_sell_fields(self, isolated_env):
        pid = open_position("600519.SH", 10.0, 100, date(2026, 7, 20), db_path=isolated_env.db_path)
        ok = close_position(pid, 10.8, date(2026, 7, 22), db_path=isolated_env.db_path)
        assert ok is True
        pos = get_position(pid, db_path=isolated_env.db_path)
        assert pos.status == STATUS_CLOSED
        assert pos.sell_price == pytest.approx(10.8)
        assert pos.sell_date == "20260722"

    def test_close_unknown_id_returns_false(self, isolated_env):
        assert close_position(9999, 10.0, date(2026, 7, 22), db_path=isolated_env.db_path) is False

    def test_close_already_closed_returns_false(self, isolated_env):
        pid = open_position("600519.SH", 10.0, 100, date(2026, 7, 20), db_path=isolated_env.db_path)
        close_position(pid, 10.8, date(2026, 7, 22), db_path=isolated_env.db_path)
        assert close_position(pid, 11.0, date(2026, 7, 23), db_path=isolated_env.db_path) is False


class TestLoadPositions:
    def test_open_positions_excludes_closed(self, isolated_env):
        p1 = open_position("A.SH", 10.0, 100, date(2026, 7, 18), db_path=isolated_env.db_path)
        open_position("B.SH", 20.0, 100, date(2026, 7, 19), db_path=isolated_env.db_path)
        close_position(p1, 10.5, date(2026, 7, 20), db_path=isolated_env.db_path)
        open_codes = {p.ts_code for p in load_open_positions(db_path=isolated_env.db_path)}
        assert open_codes == {"B.SH"}

    def test_load_all_includes_closed(self, isolated_env):
        p1 = open_position("A.SH", 10.0, 100, date(2026, 7, 18), db_path=isolated_env.db_path)
        close_position(p1, 10.5, date(2026, 7, 20), db_path=isolated_env.db_path)
        all_codes = {p.ts_code for p in load_all_positions(db_path=isolated_env.db_path)}
        assert all_codes == {"A.SH"}

    def test_empty_db_returns_empty_list_not_crash(self, isolated_env):
        assert load_open_positions(db_path=isolated_env.db_path) == []


class TestDCount:
    """D 计数(plan v1.1 铁律「买入日=D1,交易日历口径」)边界。"""

    def _cal(self, settings, days):
        insert_trade_cal(settings, days)
        import neckline.calendar.trading_calendar as tc
        tc.reset_cache()

    def test_buy_day_is_d1(self, isolated_env):
        # 2026-07-13(周一)交易日,当天即 D1
        d = date(2026, 7, 13)
        self._cal(isolated_env, [date(2026, 7, 10), d, date(2026, 7, 14)])
        assert d_count(d, d) == 1

    def test_next_trading_day_is_d2(self, isolated_env):
        d1, d2 = date(2026, 7, 13), date(2026, 7, 14)
        self._cal(isolated_env, [d1, d2])
        assert d_count(d1, d2) == 2

    def test_cross_weekend_does_not_count_weekend(self, isolated_env):
        # 周五买入,下周一 = D2(周六周日不计)
        fri, mon = date(2026, 7, 17), date(2026, 7, 20)
        self._cal(isolated_env, [fri, mon])   # insert_trade_cal 稠密写 gap 的周末为 is_open=0
        assert d_count(fri, mon) == 2

    def test_cross_long_holiday(self, isolated_env):
        # 国庆式长假:9-30(周三)买入,节后第一个交易日 10-08 = D2(中间 10-01~10-07 休市)
        buy = date(2026, 9, 30)
        resume = date(2026, 10, 8)
        # 只把 buy 与 resume 设为交易日,其间自然日全 is_open=0(长假)
        self._cal(isolated_env, [buy, resume])
        assert d_count(buy, resume) == 2

    def test_weekend_buy_date_counts_from_first_trading_day(self, isolated_env):
        # 容差:周六(非交易日)录入的 buy_date,从其后第一个交易日起计 D1
        sat = date(2026, 7, 18)
        mon = date(2026, 7, 20)
        self._cal(isolated_env, [date(2026, 7, 17), mon])
        assert d_count(sat, mon) == 1   # 只有周一一个交易日落在 [sat, mon] 闭区间

    def test_future_buy_date_is_zero(self, isolated_env):
        d = date(2026, 7, 13)
        self._cal(isolated_env, [date(2026, 7, 10), d])
        assert d_count(date(2026, 7, 14), d) == 0   # buy > trade_date(防御,生产不该出现)


class TestCountOpensOn:
    def test_counts_opens_by_buy_date(self, isolated_env):
        db = isolated_env.db_path
        open_position("A.SH", 10.0, 100, date(2026, 7, 20), db_path=db)
        open_position("B.SH", 20.0, 100, date(2026, 7, 20), db_path=db)
        open_position("C.SH", 30.0, 100, date(2026, 7, 21), db_path=db)
        assert count_opens_on(date(2026, 7, 20), db_path=db) == 2
        assert count_opens_on(date(2026, 7, 21), db_path=db) == 1
        assert count_opens_on(date(2026, 7, 22), db_path=db) == 0

    def test_closed_same_day_still_counts_as_recorded(self, isolated_env):
        db = isolated_env.db_path
        pid = open_position("A.SH", 10.0, 100, date(2026, 7, 20), db_path=db)
        close_position(pid, 10.5, date(2026, 7, 20), db_path=db)
        assert count_opens_on(date(2026, 7, 20), db_path=db) == 1   # 当天买当天清也算「有补录」
