"""两档时间退出扫描 + 分类器单测(§五 v1.3-①-C,sentinel/precall)。

锁死:①config 未启用两档 → `scan_time_exits` 退回单档 `d==max_hold_days` = v1.1
`scan_d5_exits` 完全一致;②两档启用三态(time_exit_next_day / profit_exempt /
hard_cap_exit)按 d_count + 净浮盈判对;③profit_exempt 也 emit(供看板)但不在 actionable;
④净浮盈未知(provider=None)保守判非浮盈;⑤`classify_time_exit` 四态(含 HOLDING)对 PositionOut。
"""

from __future__ import annotations

from datetime import date, timedelta

from neckline.calendar import trading_days_between
from neckline.sentinel.positions import Position
from neckline.sentinel.precall import (
    HARD_CAP_EXIT,
    HOLDING,
    PROFIT_EXEMPT,
    TIME_EXIT_NEXT_DAY,
    _ACTIONABLE_TIME_EXIT,
    classify_time_exit,
    is_two_tier_time_exit,
    scan_d5_exits,
    scan_time_exits,
)
from neckline.strategy.momentum import MomentumConfig

_BUY = date(2024, 1, 2)


def _date_at_held(n: int, buy: date = _BUY) -> date:
    d = buy
    while len(trading_days_between(buy, d)) < n:
        d += timedelta(days=1)
    return d


def _pos(pid=1, code="600001.SH") -> Position:
    return Position(id=pid, ts_code=code, buy_price=10.0, qty=1000, buy_date=_BUY.strftime("%Y%m%d"),
                    status="open", sell_price=None, sell_date=None, note=None)


def _k1() -> MomentumConfig:
    return MomentumConfig(max_hold_days=5)   # 未设两档字段 → 默认单档


def _v13() -> MomentumConfig:
    return MomentumConfig(max_hold_days=5, max_hold_days_profit=15, time_exit_only_if_unprofitable=True)


# —— classify_time_exit(纯函数,PositionOut 派生复用)————————————————————————

class TestClassify:
    def test_k1_single_tier(self):
        cfg = _k1()
        assert not is_two_tier_time_exit(cfg)
        assert classify_time_exit(3, cfg) == (HOLDING, 5)
        assert classify_time_exit(5, cfg) == (TIME_EXIT_NEXT_DAY, 5)
        assert classify_time_exit(6, cfg) == (TIME_EXIT_NEXT_DAY, 5)   # >= 语义(与 todayAction 一致)

    def test_two_tier_states(self):
        cfg = _v13()
        assert is_two_tier_time_exit(cfg)
        assert classify_time_exit(4, cfg, 100.0) == (HOLDING, 5)
        assert classify_time_exit(5, cfg, 100.0) == (PROFIT_EXEMPT, 15)
        assert classify_time_exit(5, cfg, -1.0) == (TIME_EXIT_NEXT_DAY, 5)
        assert classify_time_exit(9, cfg, 100.0) == (PROFIT_EXEMPT, 15)
        assert classify_time_exit(15, cfg, 100.0) == (HARD_CAP_EXIT, 15)
        assert classify_time_exit(15, cfg, -1.0) == (HARD_CAP_EXIT, 15)  # 硬上限无条件,不看净浮盈

    def test_two_tier_unknown_net_float_conservative(self):
        """净浮盈未知(None)→ 保守判非浮盈(豁免需正向证据)。"""
        assert classify_time_exit(5, _v13(), None) == (TIME_EXIT_NEXT_DAY, 5)


# —— scan_time_exits ————————————————————————————————————————————————————

class TestScanTimeExits:
    def test_fallback_matches_scan_d5_exits(self):
        """config 未启用 → 退回单档 == max_hold_days,与 v1.1 scan_d5_exits 同集合。"""
        pos = _pos()
        d5 = _date_at_held(5)
        te = scan_time_exits([pos], d5, _k1())
        d5old = scan_d5_exits([pos], d5, 5)
        assert [e.ts_code for e in te] == [e.ts_code for e in d5old] == ["600001.SH"]
        assert te[0].state == TIME_EXIT_NEXT_DAY and te[0].two_tier is False
        # D4 不触发、D6 不触发(单档只恰达 D5)
        assert scan_time_exits([pos], _date_at_held(4), _k1()) == []
        assert scan_time_exits([pos], _date_at_held(6), _k1()) == []

    def test_two_tier_profit_exempt_emitted_not_actionable(self):
        """D5 浮盈 → profit_exempt(emit 供看板,但不在 actionable 推送集)。"""
        te = scan_time_exits([_pos()], _date_at_held(5), _v13(), net_float_provider=lambda p: 500.0)
        assert len(te) == 1 and te[0].state == PROFIT_EXEMPT and te[0].max_hold_effective == 15
        assert te[0].state not in _ACTIONABLE_TIME_EXIT

    def test_two_tier_nonprofit_time_exit_actionable(self):
        te = scan_time_exits([_pos()], _date_at_held(5), _v13(), net_float_provider=lambda p: -10.0)
        assert te[0].state == TIME_EXIT_NEXT_DAY and te[0].state in _ACTIONABLE_TIME_EXIT
        assert te[0].two_tier is True

    def test_two_tier_hard_cap_actionable(self):
        te = scan_time_exits([_pos()], _date_at_held(15), _v13(), net_float_provider=lambda p: 500.0)
        assert te[0].state == HARD_CAP_EXIT and te[0].max_hold_effective == 15
        assert te[0].state in _ACTIONABLE_TIME_EXIT

    def test_two_tier_holding_not_emitted(self):
        assert scan_time_exits([_pos()], _date_at_held(3), _v13(), net_float_provider=lambda p: 500.0) == []

    def test_two_tier_none_provider_conservative(self):
        """provider=None(precall 9:25:30 D5 收盘未出)→ 保守判非浮盈 → time_exit_next_day。"""
        te = scan_time_exits([_pos()], _date_at_held(5), _v13(), net_float_provider=None)
        assert te[0].state == TIME_EXIT_NEXT_DAY

    def test_names_resolve(self):
        te = scan_time_exits([_pos()], _date_at_held(5), _k1(), names={"600001.SH": "示例股"})
        assert te[0].name == "示例股"
