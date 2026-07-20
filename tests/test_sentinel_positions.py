"""持仓台账单测(plan 阶段3)。极简 CRUD——开仓/清仓/查询,不做任何仓位纪律
校验或盈亏计算(系统只审计不拦人手动录入,§3.8 铁律「系统永不自动下单」的
延伸:本表也不该替用户判断"能不能开这仓")。"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.sentinel.positions import (
    STATUS_CLOSED,
    STATUS_OPEN,
    close_position,
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
