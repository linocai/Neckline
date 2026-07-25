"""呼吸试验仓 T 子账存取层单测(plan §五 v1.2-G 验收①)。

覆盖:T 子账 CRUD + `position_id` 外键关联(底仓不存在 → `add_trade` 返回 None,
不建孤儿行)、底仓摊薄成本 / 先手距离派生(含 T 净盈利拉低成本 / 净亏损推高成本
两种方向)、费用逐笔如实入账(不硬编费率)、`DELETE` 幂等 + not-found 语义、T 盈亏
公式方向无关性(先买后卖 / 先卖后买同式)。
"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.breathing import (
    BreathingTrade,
    add_trade,
    compute_base_cost_adj,
    compute_edge_to_price,
    compute_t_pnl,
    delete_trade,
    get_trade,
    list_trades,
)
from neckline.sentinel.positions import open_position

pytestmark = pytest.mark.usefixtures("isolated_env")


def _open(db_path, buy_price=10.0, qty=1000) -> int:
    return open_position("600001.SH", buy_price, qty, date(2026, 7, 1), db_path=db_path)


class TestTPnlFormula:
    """G.1「T 盈亏 = (sell−buy)×qty−fees 同式,方向仅备注」——同一公式无论「先买
    后卖」还是「先卖后买」都适用,不需要一个显式方向字段。"""

    def test_buy_low_sell_high_positive(self):
        assert compute_t_pnl(buy_price=10.0, sell_price=10.5, qty=1000, fees=20.0) == pytest.approx(480.0)

    def test_sell_high_buy_back_low_still_positive_same_formula(self):
        """「先卖后买」(先在高位卖出手上部分底仓、再低位买回)本质上仍是
        `sell_price > buy_price` 时的净赚——落库时买/卖价各自记录,公式不变。"""
        assert compute_t_pnl(buy_price=9.8, sell_price=10.2, qty=500, fees=15.0) == pytest.approx(185.0)

    def test_loss_direction(self):
        assert compute_t_pnl(buy_price=10.0, sell_price=9.9, qty=1000, fees=20.0) == pytest.approx(-120.0)

    def test_fees_are_not_estimated_here(self):
        """费用是入参,不是本函数按某个费率算出来的——传 0 与传一个大数字都原样
        参与计算,函数内部没有任何费率字面量。"""
        assert compute_t_pnl(10.0, 10.1, 1000, fees=0.0) == pytest.approx(100.0)
        assert compute_t_pnl(10.0, 10.1, 1000, fees=999.0) == pytest.approx(-899.0)


class TestBaseCostAdjAndEdge:
    """G.3:底仓摊薄成本 / 先手距离派生——两种方向(T 净盈利 / 净亏损)都要覆盖。"""

    def test_no_trades_cost_equals_raw_buy_price(self):
        assert compute_base_cost_adj(buy_price=10.0, qty=1000, trades=[]) == pytest.approx(10.0)

    def test_net_positive_t_pnl_lowers_cost(self):
        """做 T 净赚 500 元,摊到 1000 股底仓上,每股降 0.5。"""
        trades = [
            BreathingTrade(id=1, position_id=1, buy_price=10.0, sell_price=10.3, qty=1000,
                            fees=100.0, t_date="20260702", note=None, created_at="x"),  # t_pnl=200
            BreathingTrade(id=2, position_id=1, buy_price=10.1, sell_price=10.5, qty=800,
                            fees=20.0, t_date="20260703", note=None, created_at="x"),   # t_pnl=300
        ]
        assert sum(t.t_pnl for t in trades) == pytest.approx(500.0)
        adj = compute_base_cost_adj(buy_price=10.0, qty=1000, trades=trades)
        assert adj == pytest.approx(9.5)

    def test_net_negative_t_pnl_raises_cost(self):
        """做 T 净亏 300 元,摊到 1000 股底仓上,每股升 0.3(先手劣势)。"""
        trades = [
            BreathingTrade(id=1, position_id=1, buy_price=10.0, sell_price=9.7, qty=1000,
                            fees=0.0, t_date="20260702", note=None, created_at="x"),  # t_pnl=-300
        ]
        adj = compute_base_cost_adj(buy_price=10.0, qty=1000, trades=trades)
        assert adj == pytest.approx(10.3)

    def test_qty_non_positive_returns_none(self):
        assert compute_base_cost_adj(buy_price=10.0, qty=0, trades=[]) is None
        assert compute_base_cost_adj(buy_price=10.0, qty=None, trades=[]) is None

    def test_edge_to_price_positive_when_price_above_cost(self):
        # 口径 = 相对自己的摊薄成本(2026-07-25 用户拍板,浮盈率读数,非相对现价):
        # baseCostAdj=9.5, price=10.0 → (10-9.5)/9.5
        assert compute_edge_to_price(9.5, 10.0) == pytest.approx((10.0 - 9.5) / 9.5)

    def test_edge_to_price_negative_when_price_below_cost(self):
        assert compute_edge_to_price(10.3, 10.0) == pytest.approx((10.0 - 10.3) / 10.3)

    def test_edge_to_price_none_without_price(self):
        assert compute_edge_to_price(9.5, None) is None

    def test_edge_to_price_none_without_base_cost(self):
        assert compute_edge_to_price(None, 10.0) is None

    def test_edge_to_price_none_when_price_non_positive(self):
        assert compute_edge_to_price(9.5, 0.0) is None
        assert compute_edge_to_price(9.5, -1.0) is None

    def test_edge_to_price_none_when_base_cost_non_positive(self):
        """分母是摊薄成本本身,成本非正(除数无意义)也要防崩——不像旧口径那样
        只需担心 price 那一侧。"""
        assert compute_edge_to_price(0.0, 10.0) is None
        assert compute_edge_to_price(-0.5, 10.0) is None


class TestAddTradeFKAndPersistence:
    def test_add_trade_under_existing_position(self, isolated_env):
        db = isolated_env.db_path
        pid = _open(db)
        row = add_trade(pid, buy_price=10.0, sell_price=10.3, qty=500, fees=20.0,
                         t_date=date(2026, 7, 2), note="日内低吸", db_path=db)
        assert row is not None
        assert row.id >= 1
        assert row.position_id == pid
        assert row.buy_price == 10.0 and row.sell_price == 10.3
        assert row.qty == 500 and row.fees == 20.0
        assert row.t_date == "20260702"
        assert row.note == "日内低吸"
        assert row.t_pnl == pytest.approx((10.3 - 10.0) * 500 - 20.0)

    def test_add_trade_missing_position_returns_none(self, isolated_env):
        """外键关联:底仓不存在 → 不建孤儿 T 子账行。"""
        db = isolated_env.db_path
        row = add_trade(999999, buy_price=10.0, sell_price=10.3, qty=500, fees=20.0, db_path=db)
        assert row is None
        assert list_trades(999999, db_path=db) == []

    def test_fees_stored_verbatim_not_estimated(self, isolated_env):
        """G.2:传入 fees 原样落库——不是按 0.1% 或固定 20 元估算出来的。"""
        db = isolated_env.db_path
        pid = _open(db)
        row_a = add_trade(pid, buy_price=10.0, sell_price=10.1, qty=1000, fees=0.0, db_path=db)
        row_b = add_trade(pid, buy_price=10.0, sell_price=10.1, qty=1000, fees=37.5, db_path=db)
        assert row_a.fees == 0.0
        assert row_b.fees == 37.5   # 既非 20、也非 0.1%×成交额(=10) —— 如实入账

    def test_t_date_defaults_to_today(self, isolated_env):
        db = isolated_env.db_path
        pid = _open(db)
        row = add_trade(pid, buy_price=10.0, sell_price=10.1, qty=100, fees=5.0, db_path=db)
        assert row.t_date == date.today().strftime("%Y%m%d")

    def test_list_trades_ordered_by_t_date(self, isolated_env):
        db = isolated_env.db_path
        pid = _open(db)
        add_trade(pid, buy_price=10.0, sell_price=10.1, qty=100, fees=5.0, t_date=date(2026, 7, 5), db_path=db)
        add_trade(pid, buy_price=10.0, sell_price=10.1, qty=100, fees=5.0, t_date=date(2026, 7, 2), db_path=db)
        rows = list_trades(pid, db_path=db)
        assert [r.t_date for r in rows] == ["20260702", "20260705"]

    def test_list_trades_scoped_to_position(self, isolated_env):
        """不同底仓的 T 子账互不串。"""
        db = isolated_env.db_path
        pid_a = _open(db)
        pid_b = _open(db)
        add_trade(pid_a, buy_price=10.0, sell_price=10.1, qty=100, fees=5.0, db_path=db)
        add_trade(pid_b, buy_price=20.0, sell_price=20.2, qty=100, fees=5.0, db_path=db)
        add_trade(pid_b, buy_price=20.0, sell_price=19.9, qty=100, fees=5.0, db_path=db)
        assert len(list_trades(pid_a, db_path=db)) == 1
        assert len(list_trades(pid_b, db_path=db)) == 2

    def test_get_trade_roundtrip(self, isolated_env):
        db = isolated_env.db_path
        pid = _open(db)
        created = add_trade(pid, buy_price=10.0, sell_price=10.2, qty=200, fees=8.0, db_path=db)
        fetched = get_trade(created.id, db_path=db)
        assert fetched == created

    def test_get_trade_missing_returns_none(self, isolated_env):
        assert get_trade(999999, db_path=isolated_env.db_path) is None


class TestDeleteTrade:
    def test_delete_removes_row(self, isolated_env):
        db = isolated_env.db_path
        pid = _open(db)
        row = add_trade(pid, buy_price=10.0, sell_price=10.1, qty=100, fees=5.0, db_path=db)
        assert delete_trade(row.id, db_path=db) is True
        assert get_trade(row.id, db_path=db) is None
        assert list_trades(pid, db_path=db) == []

    def test_delete_missing_returns_false(self, isolated_env):
        assert delete_trade(999999, db_path=isolated_env.db_path) is False

    def test_delete_is_idempotent_safe(self, isolated_env):
        """删两次:第二次不报错,只是返回 False(该 id 已不存在)。"""
        db = isolated_env.db_path
        pid = _open(db)
        row = add_trade(pid, buy_price=10.0, sell_price=10.1, qty=100, fees=5.0, db_path=db)
        assert delete_trade(row.id, db_path=db) is True
        assert delete_trade(row.id, db_path=db) is False

    def test_delete_does_not_touch_other_trades(self, isolated_env):
        db = isolated_env.db_path
        pid = _open(db)
        a = add_trade(pid, buy_price=10.0, sell_price=10.1, qty=100, fees=5.0, db_path=db)
        b = add_trade(pid, buy_price=10.0, sell_price=10.2, qty=100, fees=5.0, db_path=db)
        delete_trade(a.id, db_path=db)
        remaining = list_trades(pid, db_path=db)
        assert [r.id for r in remaining] == [b.id]


class TestNoAutomaticWritePath:
    """写入只经本模块函数;本模块任何函数都不触发下单/撤单(§3.8)——add_trade/
    delete_trade 绝不写 `positions` 表(不改底仓语义)。"""

    def test_add_trade_does_not_mutate_position(self, isolated_env):
        from neckline.sentinel.positions import get_position

        db = isolated_env.db_path
        pid = _open(db, buy_price=10.0, qty=1000)
        before = get_position(pid, db_path=db)
        add_trade(pid, buy_price=10.0, sell_price=10.5, qty=500, fees=20.0, db_path=db)
        after = get_position(pid, db_path=db)
        assert before == after   # 底仓行逐字段未变(buy_price/qty/status 等)
