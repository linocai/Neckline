"""盘前校准 tick 单测(plan v1.1-A / B.2 验收)。纯判定函数走边界(合成集合竞价
`Quote` 注入),run_precall_tick 走 `isolated_env` 隔离 + `quotes_fn` 注入(同
`smoke_sentinel.py` 姿势),不联网、不碰真实数据。覆盖:四类判定各触发/不触发边界、
盘前窗口 gating(9:25:30 触发 / 9:24 与 9:31 不触发)、当日防重一次、D5 扫描
`==max_hold_days` 触发且读 config 非硬编。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from tests.conftest import (
    business_days,
    insert_stock_basic,
    insert_trade_cal,
    seed_active_rule_v1,
    write_daily_fixture,
)

from neckline.report import store
from neckline.sentinel import precall
from neckline.sentinel.dedup import already_pushed, load_events_for_date
from neckline.sentinel.positions import Position, open_position
from neckline.sentinel.precall import (
    D5EXIT_EVENT_KEY,
    EVENT_TICK,
    is_precall_window,
    judge_auction_volume,
    judge_gap_up_invalidate,
    judge_low_open_falsify,
    judge_position_low_open,
    run_precall_tick,
    scan_d5_exits,
)
from neckline.sentinel.quotes import Quote


def _quote(*, open_: float, pre_close: float = 10.0, price: float = None, volume: float = 0.0,
           code: str = "600001.SH") -> Quote:
    return Quote(
        code=code, name="示例", price=(price if price is not None else open_), pre_close=pre_close,
        open=open_, high=max(open_, pre_close), low=min(open_, pre_close),
        volume=volume, amount=0.0, ts="", source="test",
    )


def _script(ts_code="600001.SH", ref_close=9.5, stop_line=9.7) -> precall.MemberScript:
    """**V2-⑬-7**:盘前判定对象由「昨晚候选」换成「T1/T2 篮子成员 + 卡里冻结的两个
    价位」。`ref_close` = 卡的 D0 收盘锚(判高开偏离),`stop_line` = 卡的
    `close_below_stop_line`(判开盘即失效)。判定阈值与文案口径均沿用 V1。"""
    return precall.MemberScript(ts_code=ts_code, basket_key="k1",
                                ref_close=ref_close, stop_line=stop_line)


def _position(buy_price=10.0, buy_date="20260716", ts_code="600001.SH", pid=1) -> Position:
    return Position(id=pid, ts_code=ts_code, buy_price=buy_price, qty=100, buy_date=buy_date,
                    status="open", sell_price=None, sell_date=None, note=None)


# ————————————————————————————————————————————————————————————————
# 1) 四类纯规则判定的触发 / 不触发边界
# ————————————————————————————————————————————————————————————————

class TestGapUpInvalidate:
    def test_gap_up_over_threshold_deviates_from_frozen_script(self):
        # ref_close=9.5,open=10.0 → 高开 5.3% > 3% → 偏离剧本
        assert judge_gap_up_invalidate(_script(), _quote(open_=10.0)) is not None

    def test_gap_up_below_threshold_ok(self):
        # ref_close=9.5,open=9.6 → 高开 1.05% < 3% → 不触发
        assert judge_gap_up_invalidate(_script(), _quote(open_=9.6)) is None

    def test_threshold_is_measured_against_the_frozen_anchor_not_pre_close(self):
        """阈值锚在**卡里冻结的 ref_close**上,不是昨收 —— 两者不同时必须按前者判。"""
        sc = _script(ref_close=10.0)
        assert judge_gap_up_invalidate(sc, _quote(open_=10.4, pre_close=12.0)) is not None  # +4% > 3%
        assert judge_gap_up_invalidate(sc, _quote(open_=10.2, pre_close=8.0)) is None       # +2% < 3%

    def test_missing_frozen_anchor_no_judgment(self):
        """卡里没给这只票的锚 → **不判**(不拿现价现推一个阈值顶上)。"""
        assert judge_gap_up_invalidate(_script(ref_close=None), _quote(open_=12.0)) is None


class TestLowOpenFalsify:
    def test_open_below_frozen_stop_line_warns(self):
        # stop_line=9.7,open=9.6 → 开盘即在失效位下方
        assert judge_low_open_falsify(_script(), _quote(open_=9.6, pre_close=10.0)) is not None

    def test_open_above_stop_line_ok(self):
        assert judge_low_open_falsify(_script(), _quote(open_=9.9, pre_close=10.0)) is None

    def test_high_open_not_falsify(self):
        assert judge_low_open_falsify(_script(), _quote(open_=10.5, pre_close=10.0)) is None

    def test_missing_frozen_stop_line_no_judgment(self):
        assert judge_low_open_falsify(_script(stop_line=None), _quote(open_=1.0, pre_close=10.0)) is None


class TestAuctionVolume:
    def test_high_auction_volume_flagged(self):
        # frac = 1500/10000 = 15% ≥ 10% → 放量
        assert judge_auction_volume(_quote(open_=10.0, volume=1500.0), 10000.0) is not None

    def test_low_auction_volume_flagged(self):
        # frac = 30/10000 = 0.3% ≤ 0.5% → 地量
        assert judge_auction_volume(_quote(open_=10.0, volume=30.0), 10000.0) is not None

    def test_normal_auction_volume_ok(self):
        # frac = 500/10000 = 5% → 落在 0.5%~10% 之间 → 无异常
        assert judge_auction_volume(_quote(open_=10.0, volume=500.0), 10000.0) is None

    def test_no_base_no_judgment(self):
        assert judge_auction_volume(_quote(open_=10.0, volume=1500.0), 0.0) is None


class TestPositionLowOpen:
    def test_open_breaks_stop_line(self):
        # buy=10,stop_pct=0.05 → stop_line=9.5;open=9.4 → 跌破
        r = judge_position_low_open(_position(), _quote(open_=9.4), stop_pct=0.05)
        assert r is not None and "跌破" in r

    def test_open_approaches_stop_line(self):
        # open=9.6 → drawdown 4% ≥ (5%-2%),但 9.6>9.5 未破 → 逼近
        r = judge_position_low_open(_position(), _quote(open_=9.6), stop_pct=0.05)
        assert r is not None and "逼近" in r

    def test_shallow_low_open_ok(self):
        # open=9.8 → drawdown 2% < 3% → 不触发
        assert judge_position_low_open(_position(), _quote(open_=9.8), stop_pct=0.05) is None


# ————————————————————————————————————————————————————————————————
# 2) 盘前窗口 gating
# ————————————————————————————————————————————————————————————————

class TestPrecallWindow:
    def test_window_boundaries(self, isolated_env):
        # 用真实交易日历(seed 一个交易日),精确判 9:25:30 / 9:24 / 9:31 边界
        d = date(2026, 7, 21)   # 周二
        insert_trade_cal(isolated_env, [d])
        import neckline.calendar.trading_calendar as tc
        tc.reset_cache()
        assert is_precall_window(datetime.combine(d, time(9, 25, 30))) is True
        assert is_precall_window(datetime.combine(d, time(9, 29, 59))) is True
        assert is_precall_window(datetime.combine(d, time(9, 24, 0))) is False   # 窗口前
        assert is_precall_window(datetime.combine(d, time(9, 31, 0))) is False   # 窗口后(盘中)
        assert is_precall_window(datetime.combine(d, time(9, 30, 0))) is False   # 9:30 归盘中

    def test_weekend_not_window(self, isolated_env):
        sat = date(2026, 7, 18)
        insert_trade_cal(isolated_env, [date(2026, 7, 17)])   # 周五交易日,周六 gap=0
        import neckline.calendar.trading_calendar as tc
        tc.reset_cache()
        assert is_precall_window(datetime.combine(sat, time(9, 25, 30))) is False


# ————————————————————————————————————————————————————————————————
# 3) run_precall_tick 集成:落库 + 防重 + 非窗口跳过
# ————————————————————————————————————————————————————————————————

def _setup(settings, *, report_day: date, today: date, member_codes=(), positions=(), daily_vol=None,
           ref_close=9.5, stop_pct=0.05):
    """铺交易日历 + (可选)prev5 日线 + **D0 冻结的 T1 篮子与卡** + 持仓,供
    `run_precall_tick` 消费(V2-⑬-7:判定对象已从候选换成篮子成员)。

    卡走 `basket_card.build_verification_spec`/`build_invalidation_spec` **本尊**产出,
    不手拼 JSON —— 保证测试和生产读的是同一份结构(键名一改这里就红)。"""
    from neckline.db import connection
    from neckline.selection import basket_card as bc
    from neckline.selection.basket_store import save_basket_card

    days = business_days(report_day - timedelta(days=20), 30)
    assert report_day in days and today in days
    insert_trade_cal(settings, days)
    if daily_vol is not None:
        for dd in days:
            if dd >= today:
                continue
            write_daily_fixture(settings, "daily", dd, [
                {"ts_code": c, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
                 "pre_close": 10.0, "vol": daily_vol, "amount": 10000.0}
                for c in member_codes
            ])
    store.save_report(report_day, strategy_version="v1", sentiment={}, sectors=[],
                      candidates=[], markdown="# t", db_path=settings.db_path)
    if member_codes:
        with connection(settings.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
                " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (report_day.strftime("%Y%m%d"), "k1", "篮甲", "驱动", "theme", 1,
                 "K4-pack-v1", 1, "v1.3.3", "auto", "ok", "2026-08-02T00:00:00+08:00"),
            )
            bid = int(cur.lastrowid)
            for c in member_codes:
                conn.execute(
                    "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                    " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (bid, c, "core", None, 0, "理由", 1, "2026-08-02T00:00:00+08:00"),
                )
        mechs = [bc.MemberMech(ts_code=c, name=c, close=ref_close, ma20=9.2, limit_up=11.0,
                               limit_down=9.0, stop_price=round(ref_close * (1 - stop_pct), 2))
                 for c in member_codes]
        save_basket_card(bid, {
            "verification_spec": bc.build_verification_spec("k1", report_day, mechs),
            "invalidation_spec": bc.build_invalidation_spec("k1", report_day, mechs, stop_pct=stop_pct),
        }, db_path=settings.db_path)
    insert_stock_basic(settings, [{"ts_code": c, "name": c} for c in member_codes]
                       + [{"ts_code": p.ts_code, "name": "持仓票"} for p in positions])
    for p in positions:
        open_position(p.ts_code, p.buy_price, p.qty, datetime.strptime(p.buy_date, "%Y%m%d").date(),
                      db_path=settings.db_path)
    import neckline.calendar.trading_calendar as tc
    tc.reset_cache()
    return days


def test_run_precall_records_and_dedupes(isolated_env):
    settings = isolated_env
    days = business_days(date(2026, 6, 1), 30)
    report_day, today = days[-2], days[-1]
    seed_active_rule_v1(settings)
    _setup(settings, report_day=report_day, today=today, member_codes=["600001.SH"])  # ref_close=9.5

    now = datetime.combine(today, time(9, 25, 30))
    # 高开偏离剧本:open=10.0 vs 冻结锚 9.5 → +5.3%
    quotes = {"600001.SH": _quote(open_=10.0, pre_close=10.0, code="600001.SH")}
    res = run_precall_tick(now, db_path=settings.db_path,
                           parquet_dir=settings.parquet_dir, quotes_fn=lambda codes: quotes)
    assert res.ran is True
    assert res.gap_up == ["600001.SH"]
    assert already_pushed(today, "precall", "600001.SH", precall.EVENT_GAP_UP, db_path=settings.db_path)
    assert already_pushed(today, "precall", "", EVENT_TICK, db_path=settings.db_path)   # 市场级 tick 标记

    # 第二拍:当日已跑 → 直接跳过,不重复判定
    res2 = run_precall_tick(now, db_path=settings.db_path,
                            parquet_dir=settings.parquet_dir, quotes_fn=lambda codes: quotes)
    assert res2.ran is False and res2.skipped_reason == "already_ran"


def test_run_precall_skips_outside_window(isolated_env):
    settings = isolated_env
    days = business_days(date(2026, 6, 1), 30)
    report_day, today = days[-2], days[-1]
    seed_active_rule_v1(settings)
    _setup(settings, report_day=report_day, today=today, member_codes=["600001.SH"])
    now = datetime.combine(today, time(9, 31, 0))   # 盘中,非盘前窗口
    res = run_precall_tick(now, db_path=settings.db_path, parquet_dir=settings.parquet_dir,
                           quotes_fn=lambda codes: {})
    assert res.ran is False and res.skipped_reason == "not_precall_window"
    # 未落任何 precall 事件
    assert not already_pushed(today, "precall", "", EVENT_TICK, db_path=settings.db_path)


def test_run_precall_low_open_and_position_and_auction(isolated_env):
    settings = isolated_env
    days = business_days(date(2026, 6, 1), 30)
    report_day, today = days[-2], days[-1]
    seed_active_rule_v1(settings)
    pos = _position(buy_price=10.0, buy_date=report_day.strftime("%Y%m%d"), ts_code="600900.SH", pid=1)
    # daily_vol=10000 → prev5_avg_vol=10000;竞价量 1500 → frac 15% 放量
    # ref_close=10.0 → 冻结失效位 = 10.0×(1−5%) = 9.5;open=9.4 已在失效位下方
    _setup(settings, report_day=report_day, today=today, member_codes=["600001.SH"],
           positions=[pos], daily_vol=10000.0, ref_close=10.0)

    now = datetime.combine(today, time(9, 26, 0))
    quotes = {
        "600001.SH": _quote(open_=9.4, pre_close=10.0, volume=1500.0, code="600001.SH"),   # 跌破冻结失效位 9.5 + 竞价放量
        "600900.SH": _quote(open_=9.3, pre_close=10.0, code="600900.SH"),                  # 持仓跌破止损线 9.5
    }
    res = run_precall_tick(now, db_path=settings.db_path,
                           parquet_dir=settings.parquet_dir, quotes_fn=lambda codes: quotes)
    assert res.ran is True
    assert res.low_open == ["600001.SH"]
    assert res.auction == ["600001.SH"]
    assert res.position_low_open == ["600900.SH"]
    assert res.gap_up == []
    # 看板事件已落库(每条判定一行,市场级 tick 标记不进事件列表由 API 过滤)
    kinds = {(e["sentinel"], e["event_key"]) for e in load_events_for_date(today, db_path=settings.db_path)}
    assert ("precall", precall.EVENT_LOW_OPEN) in kinds
    assert ("precall", precall.EVENT_AUCTION) in kinds
    assert ("precall", precall.EVENT_POS_LOW_OPEN) in kinds
    assert res.summary_actionable == 2   # 低开候选 + 持仓预警(竞价异常是附注,不计入)


# ————————————————————————————————————————————————————————————————
# 3b) 熔断锁定 → 盘前强提醒(审计 🟡-4:§2.1 第 7 条「次日只减不加」的那一半)
# ————————————————————————————————————————————————————————————————

def _precall_with_circuit(isolated_env, *, locked: bool):
    """跑一拍盘前 tick;`locked=True` 时先造一条未解锁的熔断触发行。"""
    from neckline.db import connection
    settings = isolated_env
    days = business_days(date(2026, 6, 1), 30)
    report_day, today = days[-2], days[-1]
    seed_active_rule_v1(settings)
    _setup(settings, report_day=report_day, today=today, member_codes=["600001.SH"])
    if locked:
        with connection(settings.db_path) as conn:
            conn.execute(
                "INSERT INTO circuit_breaker (triggered_at, trigger_reason, trigger_ref_date, "
                "basis_json, unlocked_at, unlocked_via, created_at) VALUES (?,?,?,?,?,?,?)",
                ("2026-07-20T08:00:00+00:00", "consecutive_stops", "20260720", "{}", None, None,
                 "2026-07-20T08:00:00+00:00"),
            )
    now = datetime.combine(today, time(9, 25, 30))
    # open 9.55 vs ma10 9.5 → 高开 +0.5%(未超 3% 阈)、pre_close 10.0 → 低开 -4.5%?
    # 用 open=pre_close=9.55 令四类判定全部不触发,专测「零判定 + 熔断锁定」这一格。
    quotes = {"600001.SH": _quote(open_=9.55, pre_close=9.55, code="600001.SH")}
    res = run_precall_tick(now, db_path=settings.db_path,
                           parquet_dir=settings.parquet_dir, quotes_fn=lambda codes: quotes)
    return settings, today, res


def test_precall_circuit_locked_forces_summary(isolated_env):
    """锁定态 → `circuit_locked=True`、零判定也 `should_push_summary`、看板留痕已落。"""
    settings, today, res = _precall_with_circuit(isolated_env, locked=True)
    assert res.ran is True
    assert res.summary_actionable == 0          # 本拍确实没有其它判定
    assert res.circuit_locked is True
    assert res.should_push_summary is True      # 不被「平静清晨不轰炸」门槛吞掉
    assert already_pushed(today, "precall", "", precall.EVENT_CIRCUIT_LOCKED,
                          db_path=settings.db_path)


def test_precall_circuit_unlocked_no_reminder(isolated_env):
    """阴性方向:未锁定 → 不带提醒、零判定时也不推(不制造每日噪音)。"""
    settings, today, res = _precall_with_circuit(isolated_env, locked=False)
    assert res.ran is True
    assert res.circuit_locked is False
    assert res.should_push_summary is False
    assert not already_pushed(today, "precall", "", precall.EVENT_CIRCUIT_LOCKED,
                              db_path=settings.db_path)


# ————————————————————————————————————————————————————————————————
# 4) D5 时间退出扫描:== max_hold_days 触发且读 config 非硬编
# ————————————————————————————————————————————————————————————————

def test_d5_scan_triggers_exactly_at_max_hold_days(isolated_env):
    """D5 扫描语义:d_count==max_hold_days 当天才触发,且随 config 变(非硬编 5)。"""
    days = business_days(date(2026, 6, 1), 12)
    insert_trade_cal(isolated_env, days)
    import neckline.calendar.trading_calendar as tc
    tc.reset_cache()

    buy = days[0]
    pos = _position(buy_date=buy.strftime("%Y%m%d"), ts_code="600001.SH")
    d5_day, d4_day, d3_day = days[4], days[3], days[2]   # 买入日=D1

    r5 = scan_d5_exits([pos], d5_day, max_hold_days=5)
    assert r5 and r5[0].d == 5
    assert scan_d5_exits([pos], d4_day, max_hold_days=5) == []       # D4 不触发
    # 读 config:max_hold=3 时,D3 当天触发(证明非硬编 5),D5 已过不再触发
    assert scan_d5_exits([pos], d3_day, max_hold_days=3)
    assert scan_d5_exits([pos], d5_day, max_hold_days=3) == []


def test_run_precall_d5_exit_records_and_dedupes(isolated_env):
    settings = isolated_env
    days = business_days(date(2026, 6, 1), 30)
    report_day, today = days[-2], days[-1]
    # 让持仓 buy_date 使 today 恰为 D5(max_hold_days=5)
    buy_day = days[-5]
    assert len([d for d in days if buy_day <= d <= today]) == 5   # 闭区间 5 个交易日
    seed_active_rule_v1(settings)   # max_hold_days=5
    pos = _position(buy_price=10.0, buy_date=buy_day.strftime("%Y%m%d"), ts_code="600900.SH")
    _setup(settings, report_day=report_day, today=today, member_codes=["600001.SH"], positions=[pos])

    now = datetime.combine(today, time(9, 25, 30))
    quotes = {"600900.SH": _quote(open_=10.0, pre_close=10.0, code="600900.SH")}   # 无止损预警,只测 D5
    res = run_precall_tick(now, db_path=settings.db_path,
                           parquet_dir=settings.parquet_dir, quotes_fn=lambda codes: quotes)
    assert [e.ts_code for e in res.d5_exits] == ["600900.SH"]
    assert res.d5_exits[0].d == 5
    assert already_pushed(today, "d5exit", "600900.SH", D5EXIT_EVENT_KEY, db_path=settings.db_path)

    # 第二拍防重(整个 tick 已跑)
    res2 = run_precall_tick(now, db_path=settings.db_path,
                            parquet_dir=settings.parquet_dir, quotes_fn=lambda codes: quotes)
    assert res2.ran is False


