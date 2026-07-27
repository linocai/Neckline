"""两档时间退出扫描 + 分类器单测(§五 v1.3-①-C,sentinel/precall)。

锁死:①config 未启用两档 → `scan_time_exits` 退回单档 `d==max_hold_days` = v1.1
`scan_d5_exits` 完全一致;②两档启用三态(time_exit_next_day / profit_exempt /
hard_cap_exit)按 d_count + **定格判向**判对;③profit_exempt 也 emit(供看板)但不在 actionable;
④定格未知(provider=None)保守判非浮盈;⑤`classify_time_exit`(定格时刻)/ `resolve_time_exit`
(消费点)四态(含 HOLDING)对 PositionOut。

**审计 🔴-1(2026-07-27 用户拍板方案 A「D5 判一次定格」)**:`scan_time_exits` 已从
`net_float_provider`(逐日重判)改为 `locked_state_provider`(读定格值)——本文件的两档用例
随之改喂定格串;新增 `TestResolveTimeExit` 双向锁死「定格后不得改口」两个方向。
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
    resolve_time_exit,
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


# —— resolve_time_exit(消费点解析;审计 🔴-1「D5 判一次定格」双向锁死)——————————————

class TestResolveTimeExit:
    def test_k1_single_tier_identical_to_classify(self):
        """K1 单档:定格参数被忽略,与 `classify_time_exit` 逐位相同(回归护栏)。"""
        cfg = _k1()
        for d in range(0, 20):
            assert resolve_time_exit(d, cfg, None) == classify_time_exit(d, cfg)
            # 即便硬塞一个定格串,单档也不理会(单档退出与浮亏浮盈无关)
            assert resolve_time_exit(d, cfg, PROFIT_EXEMPT) == classify_time_exit(d, cfg)

    def test_profit_exempt_frozen_survives_later_loss(self):
        """① D5 浮盈豁免定格后,D7 跌回浮亏**不得**改推时间退出(正向偏差堵死)。"""
        cfg = _v13()
        assert resolve_time_exit(5, cfg, PROFIT_EXEMPT) == (PROFIT_EXEMPT, 15)
        # D6/D7 无论当日净浮盈如何(本函数根本收不到净浮盈)——判向照定格
        assert resolve_time_exit(6, cfg, PROFIT_EXEMPT) == (PROFIT_EXEMPT, 15)
        assert resolve_time_exit(7, cfg, PROFIT_EXEMPT) == (PROFIT_EXEMPT, 15)

    def test_time_exit_frozen_is_not_laundered_by_later_profit(self):
        """② D5 判该走定格后,D6/D7 转浮盈**不得**改口豁免(违纪不被事后追认)。"""
        cfg = _v13()
        assert resolve_time_exit(5, cfg, TIME_EXIT_NEXT_DAY) == (TIME_EXIT_NEXT_DAY, 5)
        assert resolve_time_exit(6, cfg, TIME_EXIT_NEXT_DAY) == (TIME_EXIT_NEXT_DAY, 5)
        assert resolve_time_exit(7, cfg, TIME_EXIT_NEXT_DAY) == (TIME_EXIT_NEXT_DAY, 5)

    def test_hard_cap_still_by_d_count(self):
        """③ D15 硬上限仍按 d_count 判(定格判向不能挡住硬上限)。"""
        cfg = _v13()
        assert resolve_time_exit(15, cfg, PROFIT_EXEMPT) == (HARD_CAP_EXIT, 15)
        assert resolve_time_exit(16, cfg, TIME_EXIT_NEXT_DAY) == (HARD_CAP_EXIT, 15)
        assert resolve_time_exit(15, cfg, None) == (HARD_CAP_EXIT, 15)

    def test_no_lock_is_conservative_not_exempt(self):
        """尚无定格(EOD 管线断跑等异常)→ 保守判 time_exit_next_day,绝不默认豁免。"""
        cfg = _v13()
        assert resolve_time_exit(5, cfg, None) == (TIME_EXIT_NEXT_DAY, 5)
        assert resolve_time_exit(9, cfg, None) == (TIME_EXIT_NEXT_DAY, 5)
        assert resolve_time_exit(4, cfg, None) == (HOLDING, 5)

    def test_unknown_lock_string_falls_back_conservative(self):
        """未识别定格串(库脏/未来新增态)→ 保守判非豁免,不误放行。"""
        assert resolve_time_exit(6, _v13(), "some_future_state") == (TIME_EXIT_NEXT_DAY, 5)


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
        """D5 定格豁免 → profit_exempt(emit 供看板,但不在 actionable 推送集)。"""
        te = scan_time_exits([_pos()], _date_at_held(5), _v13(),
                             locked_state_provider=lambda p: PROFIT_EXEMPT)
        assert len(te) == 1 and te[0].state == PROFIT_EXEMPT and te[0].max_hold_effective == 15
        assert te[0].state not in _ACTIONABLE_TIME_EXIT

    def test_two_tier_nonprofit_time_exit_actionable(self):
        te = scan_time_exits([_pos()], _date_at_held(5), _v13(),
                             locked_state_provider=lambda p: TIME_EXIT_NEXT_DAY)
        assert te[0].state == TIME_EXIT_NEXT_DAY and te[0].state in _ACTIONABLE_TIME_EXIT
        assert te[0].two_tier is True

    def test_two_tier_hard_cap_actionable(self):
        te = scan_time_exits([_pos()], _date_at_held(15), _v13(),
                             locked_state_provider=lambda p: PROFIT_EXEMPT)
        assert te[0].state == HARD_CAP_EXIT and te[0].max_hold_effective == 15
        assert te[0].state in _ACTIONABLE_TIME_EXIT

    def test_two_tier_holding_not_emitted(self):
        assert scan_time_exits([_pos()], _date_at_held(3), _v13(),
                               locked_state_provider=lambda p: PROFIT_EXEMPT) == []

    def test_two_tier_none_provider_conservative(self):
        """provider=None(尚无定格)→ 保守判非浮盈 → time_exit_next_day。"""
        te = scan_time_exits([_pos()], _date_at_held(5), _v13(), locked_state_provider=None)
        assert te[0].state == TIME_EXIT_NEXT_DAY

    def test_two_tier_frozen_exempt_not_reversed_by_scan(self):
        """审计 🔴-1 反例锁死:D5 定格豁免的单子,到 D7 扫描仍是 profit_exempt(不进推送集)——
        旧口径会因「上一份 EOD 净浮盈跌成负」翻成 time_exit_next_day 催早退。"""
        te = scan_time_exits([_pos()], _date_at_held(7), _v13(),
                             locked_state_provider=lambda p: PROFIT_EXEMPT)
        assert te[0].state == PROFIT_EXEMPT and te[0].state not in _ACTIONABLE_TIME_EXIT

    def test_two_tier_frozen_exit_not_laundered_by_scan(self):
        """审计 🔴-1 反向漏洞锁死:D5 定格「该走」的单子到 D7 仍 actionable(不被转浮盈洗白)。"""
        te = scan_time_exits([_pos()], _date_at_held(7), _v13(),
                             locked_state_provider=lambda p: TIME_EXIT_NEXT_DAY)
        assert te[0].state == TIME_EXIT_NEXT_DAY and te[0].state in _ACTIONABLE_TIME_EXIT

    def test_names_resolve(self):
        te = scan_time_exits([_pos()], _date_at_held(5), _k1(), names={"600001.SH": "示例股"})
        assert te[0].name == "示例股"
