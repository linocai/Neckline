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


# —— v1.4-①-B 第五态 suspended_hold(§七 P0-2;停牌/无当日 EOD 行 → 判向挂起)————————

class TestSuspendedHold:
    """**这是对既有保守兜底的定向收窄,不是放宽** —— 只对「当日无 EOD 行」这一情形改判
    挂起;其余无定格情形(EOD 管线断跑等)一字不改仍保守判 time_exit_next_day。"""

    def test_default_arg_is_bitwise_identical(self):
        """**回归护栏(最要紧的一条)**:不传 `data_unavailable` 时,两档 / 单档 / 各种
        定格串下的返回值与新增该参数之前逐位相同——本块只加一条分支,不动老路径。"""
        from neckline.sentinel.precall import SUSPENDED_HOLD

        for cfg in (_k1(), _v13()):
            for lock in (None, PROFIT_EXEMPT, TIME_EXIT_NEXT_DAY):
                for d in range(0, 20):
                    got = resolve_time_exit(d, cfg, lock)
                    assert got == resolve_time_exit(d, cfg, lock, data_unavailable=False)
                    assert got[0] != SUSPENDED_HOLD

    def test_suspended_at_judgement_point_hangs_instead_of_pushing_exit(self):
        """两档 + 到判定点 + 无定格 + 当日无 EOD 行 → 挂起(不再催用户去卖一只卖不掉的票)。"""
        from neckline.sentinel.precall import SUSPENDED_HOLD

        cfg = _v13()
        assert resolve_time_exit(5, cfg, None, data_unavailable=True) == (SUSPENDED_HOLD, 5)
        assert resolve_time_exit(9, cfg, None, data_unavailable=True) == (SUSPENDED_HOLD, 5)
        # 对照:同样无定格,但当日**有** EOD 行(只是管线判不出浮盈)→ 仍保守判该走
        assert resolve_time_exit(5, cfg, None) == (TIME_EXIT_NEXT_DAY, 5)

    def test_suspended_before_judgement_point_is_plain_holding(self):
        """还没到判定点就停牌 → 仍是 HOLDING(没到判定点,谈不上挂起)。"""
        assert resolve_time_exit(3, _v13(), None, data_unavailable=True) == (HOLDING, 5)
        assert resolve_time_exit(3, _k1(), None, data_unavailable=True) == (HOLDING, 5)

    def test_existing_lock_wins_over_suspension(self):
        """**已有定格 → 定格值优先,停牌不撤回既有判向**:判向是在有真数据那天一次性做出的
        决定(审计 🔴-1),否则「D5 判该走 → 用户没走 → 停牌 → 系统改口」又是一条违纪被
        事后合法化的路。"""
        cfg = _v13()
        assert resolve_time_exit(6, cfg, TIME_EXIT_NEXT_DAY, data_unavailable=True) == (TIME_EXIT_NEXT_DAY, 5)
        assert resolve_time_exit(6, cfg, PROFIT_EXEMPT, data_unavailable=True) == (PROFIT_EXEMPT, 15)

    def test_hard_cap_also_hangs_when_no_data_and_never_locked(self):
        """硬上限提醒同样挂起(plan 明写「不推 D5/硬上限提醒」);eff 仍按 d 已到的档给,
        免得客户端显示成 D18/D5 这种自相矛盾的文案。"""
        from neckline.sentinel.precall import SUSPENDED_HOLD

        assert resolve_time_exit(18, _v13(), None, data_unavailable=True) == (SUSPENDED_HOLD, 15)

    def test_suspended_hold_is_not_actionable(self):
        """挂起态绝不进 D5 执行提醒推送白名单(不推 = 契约级保证,不是调用点自觉)。"""
        from neckline.sentinel.precall import SUSPENDED_HOLD

        assert SUSPENDED_HOLD not in _ACTIONABLE_TIME_EXIT

    def test_single_tier_also_hangs(self):
        """单档老 config 同理挂起(病根一样:催用户卖一只停牌票)。"""
        from neckline.sentinel.precall import SUSPENDED_HOLD

        assert resolve_time_exit(5, _k1(), None, data_unavailable=True) == (SUSPENDED_HOLD, 5)


class TestPrecallSuspendedHold:
    """**盘前是 P0-2 病根最尖锐的形态**:9:26 汇总推送把「D5 该走」推到用户锁屏,而那只票
    今天根本卖不掉。锁死:停牌票不进 actionable、其余票行为逐位不变。"""

    def test_suspended_position_not_pushed_two_tier(self):
        from neckline.sentinel.precall import SUSPENDED_HOLD

        cfg = _v13()
        positions = [_pos(1, "600001.SH"), _pos(2, "002036.SZ")]
        td = _date_at_held(5)
        exits = scan_time_exits(
            positions, td, cfg,
            locked_state_provider=lambda p: None,                 # 都还没定格
            data_unavailable_provider=lambda p: p.ts_code == "002036.SZ",
        )
        by_code = {e.ts_code: e for e in exits}
        assert by_code["600001.SH"].state == TIME_EXIT_NEXT_DAY   # 正常票照旧判该走
        assert by_code["002036.SZ"].state == SUSPENDED_HOLD
        actionable = [e.ts_code for e in exits if e.state in _ACTIONABLE_TIME_EXIT]
        assert actionable == ["600001.SH"]                        # 停牌票**不推**

    def test_suspended_position_not_pushed_single_tier(self):
        """单档(老 config)同理:恰达 D5 的停牌票不 emit(病根一样)。"""
        cfg = _k1()
        positions = [_pos(1, "600001.SH"), _pos(2, "002036.SZ")]
        exits = scan_time_exits(
            positions, _date_at_held(5), cfg,
            data_unavailable_provider=lambda p: p.ts_code == "002036.SZ",
        )
        assert [e.ts_code for e in exits] == ["600001.SH"]

    def test_no_provider_is_bitwise_identical(self):
        """不注入 provider(既有调用点)→ 与新增该参数之前逐位相同(回归护栏)。"""
        for cfg in (_k1(), _v13()):
            for d in (4, 5, 6, 15, 16):
                td = _date_at_held(d)
                base = scan_time_exits([_pos(1)], td, cfg, locked_state_provider=lambda p: None)
                same = scan_time_exits([_pos(1)], td, cfg, locked_state_provider=lambda p: None,
                                       data_unavailable_provider=lambda p: False)
                assert [(e.state, e.max_hold_effective, e.d) for e in base] == \
                       [(e.state, e.max_hold_effective, e.d) for e in same]

    def test_provider_reads_latest_snapshot(self, isolated_env):
        """`holding_store.data_unavailable_provider`:有快照读快照;无快照 / 老快照未记这一位
        → **False**(保守,维持既有推送行为)。"""
        from neckline.report import holding_store

        class _It:
            def __init__(self, pid, has_data):
                self.position_id, self.has_data = pid, has_data
                self.d_count, self.net_float = 5, None
                self.time_exit_state, self.max_hold_effective = TIME_EXIT_NEXT_DAY, 5
                self.has_strong = self.scenario_review = False
                self.time_exit_locked_state = self.time_exit_locked_date = None
                self.time_exit_locked_net_float = None

            def hits_public(self):
                return []

        db = isolated_env.db_path
        holding_store.save_holding_eod_checks(
            date(2026, 7, 27), [_It(1, has_data=False), _It(2, has_data=True)], db_path=db)
        provider = holding_store.data_unavailable_provider(db_path=db)
        assert provider(_pos(1)) is True
        assert provider(_pos(2)) is False
        assert provider(_pos(99)) is False        # 无快照 → 保守 False
