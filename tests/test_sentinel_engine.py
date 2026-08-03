"""单拍编排单测(plan 阶段3)。整条链路(关注池→拉价(注入)→四哨兵→防重→推送)
用 `isolated_env` 隔离 + `quotes_fn` 注入合成行情,不联网、不碰真实项目数据。
重点断言编排顺序的两条铁律:①非交易时段整拍跳过;②退潮生效后买点哨兵本拍
整体抑制,证伪/持仓哨兵不受影响;以及防重跨拍生效(同一事件第二拍不再推)。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, seed_active_rule_v1, write_daily_fixture

from neckline.report import store
from neckline.sentinel.channels import PushChannel
from neckline.sentinel.dedup import already_pushed
from neckline.sentinel.engine import reset_retreat_process_state, run_tick
from neckline.sentinel.positions import open_position
from neckline.sentinel.quotes import Quote

pytestmark = pytest.mark.usefixtures("isolated_env")


class _CapturingChannel(PushChannel):
    name = "capture"

    def __init__(self):
        self.messages = []

    def send(self, title, body, *, level="info", transport=None):
        self.messages.append((title, body, level))
        return True


def _seed_basket_members(settings, report_day: date, codes, *, tier=1, key="k1"):
    """**V2-⑬-1**:关注池 / 证伪哨兵的判定对象由「昨晚候选」换成「D0 冻结的 T1/T2
    篮子成员」,本文件的构造随之从 `_candidate()` + `save_report(candidates=…)` 换成
    直接种 `baskets`/`basket_members` 两张表(裸 SQL,不经任何应用层写口)。"""
    from neckline.db import connection

    with connection(settings.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (report_day.strftime("%Y%m%d"), key, "篮" + key, "驱动", "theme", tier,
             "K4-pack-v1", 1, "v1.3.3", "auto", "ok", "2026-08-02T00:00:00+08:00"),
        )
        bid = int(cur.lastrowid)
        for c in codes:
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, c, "core", None, 0, "理由", 1, "2026-08-02T00:00:00+08:00"),
            )
    return bid


def _setup_calendar_and_history(settings, code: str, report_day: date, today: date, *, vol=1000.0):
    """铺一段覆盖 report_day 前5交易日~today 的交易日历 + `daily` 历史(供
    `load_prev5_avg_volume` 用),`report_day`/`today` 必须落在这段交易日序列里。"""
    days = business_days(report_day - timedelta(days=20), 30)
    assert report_day in days and today in days
    insert_trade_cal(settings, days)
    for d in days:
        if d >= today:
            continue  # today 尚未收盘,不应有 daily 数据(真实系统里也不会有)
        write_daily_fixture(settings, "daily", d, [
            {"ts_code": code, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "pre_close": 10.0,
             "vol": vol, "amount": 10000.0},
        ])
    return days


def _save_report(settings, report_day: date):
    store.save_report(
        report_day, strategy_version="v1", sentiment={}, sectors=[],
        candidates=[], markdown="# test", db_path=settings.db_path,
    )


def _seed_mainline_sample(settings, monkeypatch, report_day: date, codes):
    """**V2-⑧-F**:把「主线板块跳水」的样本喂成新口径 = ④ 机械种子成分 ∩ 关注池的
    **机械成分**(⛔ 不再是 T1/T2 篮子成员——那条路已被 🟡-4 裁掉,见
    `sentinel/mainline.py` 模块头)。两件事都要做,缺一样样本就是空:

    ① `limit_derived` 分区把这些码标成 D0 涨停 → 它们才会经 `prev_limit_up` 这条
       **机械路**进关注池(篮子成员那条路进的池**不进样本**);
    ② stub 掉 ④ 的种子生成(那是读五张表的重活,单测不铺全套),只留「这些码是热点
       行业种子的原始成分」这一个事实 —— `derive_mainline_sample` 的交集/排序/来源
       标签逻辑仍是**生产那一份**,没有被 stub 掉。
    """
    from neckline.scan.seeds import HOT_INDUSTRY, DriverSeed, SeedSet
    from neckline.sentinel import mainline

    write_daily_fixture(settings, "limit_derived", report_day, [
        {"ts_code": c, "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
         "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
         "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": 1}
        for c in codes
    ])
    seed_set = SeedSet(
        trade_date=report_day.strftime("%Y%m%d"), pack_version="K4-pack-v1",
        hot_industry=(DriverSeed(seed_key="s1", seed_kind=HOT_INDUSTRY, label="测试行业",
                                 member_codes=tuple(codes)),),
    )
    monkeypatch.setattr("neckline.scan.seeds.generate_seeds",
                        lambda *a, **k: seed_set)
    mainline.reset_seed_cache()


class TestSkipsOutsideTradingHours:
    def test_weekend_is_skipped_without_touching_watch_universe(self, isolated_env):
        saturday = datetime(2026, 7, 18, 10, 30)  # 周六
        result = run_tick(saturday, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert result.skipped_non_trading is True
        assert result.watched_codes == 0

    def test_before_open_is_skipped(self, isolated_env):
        weekday = datetime(2026, 7, 20, 9, 0)  # 假设是交易日但未开盘(静态兜底按工作日近似)
        result = run_tick(weekday, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert result.skipped_non_trading is True


class TestRetreatDoesNotSuppressInvalidationOrHolding:
    """⚠ **V2-⑬-1**:退潮红色刹车原本抑制的是**买点哨兵**,而买点哨兵已随 K1
    `entry_spec` 一并退役 —— 编排里已无「开仓许可信号」这类产物。本类因此改为锁死
    剩下那半句纪律:**退潮不抑制证伪与持仓**(管理已有风险任何时候都要做)。"""

    def test_retreat_active_does_not_block_invalidation(self, isolated_env):
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        _setup_calendar_and_history(isolated_env, "600001.SH", report_day, today, vol=15000.0)
        seed_active_rule_v1(isolated_env)
        _save_report(isolated_env, report_day)
        _seed_basket_members(isolated_env, report_day, ["600001.SH"])
        insert_stock_basic(isolated_env, [{"ts_code": "600001.SH", "name": "示例甲", "market": "主板"}])

        now = datetime.combine(today, time(10, 30))

        def quotes_fn(codes):
            # 低开 -3% 且截至此刻未翻红 → 命中证伪(退潮当日照样该报)
            return {
                "600001.SH": Quote(
                    code="600001", name="示例甲", price=9.75, pre_close=10.0, open=9.7, high=9.8, low=9.7,
                    volume=60000.0, amount=9.75 * 60000 * 100 * 0.95, ts="", source="sina",
                )
            }

        # 预先手工记一条今日已触发的退潮刹车事件,模拟"更早一拍已经触发"
        from neckline.sentinel.dedup import record_pushed

        record_pushed(today, "retreat", "", "brake", db_path=isolated_env.db_path)

        cap = _CapturingChannel()
        result = run_tick(
            now, channels=[cap], db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir,
            quotes_fn=quotes_fn,
        )
        assert result.retreat_active is True
        # 证伪不受退潮抑制——"剔除勿进"任何时候都是有效信息(编排注释原文)
        assert "600001.SH" in {s.ts_code for s in result.invalidation_signals}


class TestRetreatTwoTierEngineWiring:
    """v1.1-H2 双级制在编排层的接线:黄色只落看板不推送不抑制买点;红色(≥2 条件同拍
    或连续 2 拍)推送 + 抑制买点 + 闩锁;进程首拍红色降级为黄色(保守闸)。"""

    def _crash_quotes(self, price=9.0):
        """关注池全线暴跌:主板 pre_close=10 → down 限价 9.0。price=9.0 即跌停;
        return=-10% 拖主线跳水。任一 codes 都给同一构造。"""
        def fn(codes):
            return {
                c: Quote(
                    code=c.split(".")[0], name=c, price=price, pre_close=10.0, open=9.6,
                    high=9.7, low=price, volume=1000.0, amount=price * 1000 * 100, ts="", source="sina",
                )
                for c in codes
            }
        return fn

    def test_first_tick_yellow_then_next_tick_red_escalation(self, isolated_env):
        reset_retreat_process_state()
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        insert_trade_cal(isolated_env, days)
        x_codes = [f"60010{i}.SH" for i in range(6)]  # 6 只 → 跌停家数 6≥5 + 主线跳水,两条件同拍
        _save_report(isolated_env, report_day)
        _seed_basket_members(isolated_env, report_day, x_codes)
        insert_stock_basic(isolated_env, [{"ts_code": c, "name": c, "market": "主板"} for c in x_codes])

        qn = self._crash_quotes()

        # —— 首拍(10:30):≥2 条件本会红,但进程首拍保守 → 只黄色,不推送、不闩锁 ——
        cap1 = _CapturingChannel()
        r1 = run_tick(
            datetime.combine(today, time(10, 30)), channels=[cap1],
            db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir, quotes_fn=qn,
        )
        assert r1.retreat_active is False
        assert r1.retreat_warning is not None
        assert r1.retreat_alert is None
        assert not any("退潮刹车" in m[0] for m in cap1.messages)  # 黄色不推送
        assert already_pushed(today, "retreat", "", "warn", db_path=isolated_env.db_path) is True
        assert already_pushed(today, "retreat", "", "brake", db_path=isolated_env.db_path) is False

        # —— 次拍(10:31):非首拍 + ≥2 条件同拍 → 红色刹车,推送 + 闩锁 ——
        cap2 = _CapturingChannel()
        r2 = run_tick(
            datetime.combine(today, time(10, 31)), channels=[cap2],
            db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir, quotes_fn=qn,
        )
        assert r2.retreat_active is True
        assert r2.retreat_alert is not None
        assert any("退潮刹车" in m[0] for m in cap2.messages)
        assert already_pushed(today, "retreat", "", "brake", db_path=isolated_env.db_path) is True

    def test_yellow_single_condition_does_not_suppress_entry_or_push(self, isolated_env, monkeypatch):
        reset_retreat_process_state()
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        _setup_calendar_and_history(isolated_env, "600001.SH", report_day, today, vol=200000.0)
        x_codes = ["600201.SH", "600202.SH", "600203.SH"]
        _save_report(isolated_env, report_day)
        # ⚠ V2-⑧-F:主线样本 = ④ 机械种子成分 ∩ 关注池机械成分(不再是 T1/T2 篮子成员)。
        # 600001.SH 是持仓票、不在种子成分里,故不进主线跳水样本 —— 与原用例意图一致。
        # 篮子仍种(关注池/证伪哨兵照旧盯它们),但**它不再是主线样本的来源**。
        _seed_basket_members(isolated_env, report_day, x_codes)
        _seed_mainline_sample(isolated_env, monkeypatch, report_day, x_codes)
        open_position("600001.SH", 10.0, 100, report_day, db_path=isolated_env.db_path)
        insert_stock_basic(
            isolated_env, [{"ts_code": c, "name": c, "market": "主板"} for c in ["600001.SH"] + x_codes]
        )

        def quotes_fn(codes):
            out = {}
            for c in codes:
                if c == "600001.SH":
                    out[c] = Quote(
                        code="600001", name="Y", price=10.2, pre_close=10.0, open=10.0, high=10.3, low=10.0,
                        volume=60000.0, amount=10.2 * 60000 * 100 * 0.95, ts="", source="sina",
                    )
                else:
                    out[c] = Quote(  # -5%:够主线跳水(≤-3%),不够跌停(>9.0)
                        code=c.split(".")[0], name=c, price=9.5, pre_close=10.0, open=9.8, high=9.9, low=9.5,
                        volume=1000.0, amount=9.5 * 1000 * 100, ts="", source="sina",
                    )
            return out

        cap = _CapturingChannel()
        r = run_tick(
            datetime.combine(today, time(10, 30)), channels=[cap],
            db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir, quotes_fn=quotes_fn,
        )
        assert r.retreat_warning is not None and "主线跳水" in r.retreat_warning
        assert r.retreat_active is False          # 黄色不闩锁
        assert r.retreat_alert is None
        assert not any("退潮刹车" in m[0] for m in cap.messages)   # 黄色不推退潮刹车
        assert already_pushed(today, "retreat", "", "warn", db_path=isolated_env.db_path) is True
        assert already_pushed(today, "retreat", "", "brake", db_path=isolated_env.db_path) is False

    def test_metrics_row_recorded_each_tick_even_empty(self, isolated_env):
        reset_retreat_process_state()
        import sqlite3

        days = business_days(date(2026, 7, 1), 10)
        insert_trade_cal(isolated_env, days)
        today = days[-1]
        run_tick(
            datetime.combine(today, time(10, 30)),
            db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir, quotes_fn=lambda codes: {},
        )
        conn = sqlite3.connect(str(isolated_env.db_path))
        try:
            row = conn.execute(
                "SELECT hhmm, tier, sample_size FROM retreat_metrics WHERE trade_date=?",
                (today.strftime("%Y%m%d"),),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == "1030" and row[1] == "none" and row[2] == 0


class TestHoldingSentinelReadsActiveRuleStopPct:
    def test_uses_configured_stop_pct_not_hardcoded_default(self, isolated_env):
        days = business_days(date(2026, 7, 1), 20)
        insert_trade_cal(isolated_env, days)
        today = days[-1]
        seed_active_rule_v1(isolated_env, extra_config={"stop_pct": 0.08})  # 非默认的 0.05
        open_position("600002.SH", 10.0, 100, days[-3], db_path=isolated_env.db_path)

        now = datetime.combine(today, time(10, 30))
        # 回撤6%:低于默认0.05止损线(早该报"已破位"),但低于rule配置的0.08止损线
        # 减去2pp缓冲(0.06起预警)——用它来断言"引擎确实读了0.08,不是硬编0.05"。

        def quotes_fn(codes):
            return {
                "600002.SH": Quote(
                    code="600002", name="X", price=9.4, pre_close=10.0, open=10.0, high=10.0, low=9.4,
                    volume=1000.0, amount=9.4 * 1000 * 100, ts="", source="sina",
                )
            }

        cap = _CapturingChannel()
        result = run_tick(
            now, channels=[cap], db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir,
            quotes_fn=quotes_fn,
        )
        assert len(result.holding_alerts) == 1
        alert = result.holding_alerts[0]
        assert "stop_approach" in alert.alerts
        assert "已跌破止损线" not in alert.alerts["stop_approach"]  # 0.08线下回撤6%只是"逼近",没破位


class TestGracefulEmptyState:
    def test_no_report_no_positions_returns_empty_without_crash(self, isolated_env):
        days = business_days(date(2026, 7, 1), 10)
        insert_trade_cal(isolated_env, days)
        now = datetime.combine(days[-1], time(10, 30))
        result = run_tick(now, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir, quotes_fn=lambda codes: {})
        assert result.watched_codes == 0
        assert result.holding_alerts == []
        assert result.pushed_events == []


class TestV2Bypasses:
    """⑧-B/⑧-C 挂在 `run_tick` 上的两条旁路。**四哨兵与熔断一行没改**,这里只验
    「旁路真的在跑」以及「旁路炸了不影响判定」。"""

    def _seed(self, isolated_env, report_day, today, code="600001.SH"):
        from neckline.db import connection

        _setup_calendar_and_history(isolated_env, code, report_day, today)
        seed_active_rule_v1(isolated_env)
        insert_stock_basic(isolated_env, [{"ts_code": code, "name": "示例甲", "market": "主板"}])
        store.save_report(report_day, strategy_version="v1", sentiment={}, sectors=[],
                          candidates=[], markdown="# t",
                          db_path=isolated_env.db_path)
        with connection(isolated_env.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
                " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (report_day.strftime("%Y%m%d"), "k1", "篮甲", "驱动", "theme", 1,
                 "K4-pack-v1", 1, "v1.3.3", "auto", "ok", "2026-08-02T00:00:00+08:00"),
            )
            bid = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, code, "core", None, 0, "理由", 1, "2026-08-02T00:00:00+08:00"),
            )
        from neckline.selection import basket_card as bc
        from neckline.selection.basket_store import save_basket_card

        mechs = [bc.MemberMech(ts_code=code, name=code, close=10.0, ma20=9.2,
                               limit_up=11.0, limit_down=9.0, stop_price=9.5)]
        save_basket_card(bid, {
            "verification_spec": bc.build_verification_spec("k1", report_day, mechs),
            "invalidation_spec": bc.build_invalidation_spec("k1", report_day, mechs, stop_pct=0.05),
        }, db_path=isolated_env.db_path)
        return bid

    def _quotes_fn(self, code, price):
        def _fn(codes):
            return {code: Quote(code=code.split(".")[0], name="示例甲", price=price, pre_close=10.0,
                                open=10.0, high=price, low=price, volume=1000.0, amount=1e6,
                                ts="", source="sina")}
        return _fn

    def test_tick_feeds_capture_and_basket_verification(self, isolated_env):
        from neckline.sentinel import basket_verify_store as bvs
        from neckline.sentinel import capture

        capture.reset_capture_state()
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        bid = self._seed(isolated_env, report_day, today)
        now = datetime.combine(today, time(10, 30))

        result = run_tick(now, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir,
                          quotes_fn=self._quotes_fn("600001.SH", 10.5))
        assert result.captured_ticks == 1                       # 存拍收到这一拍(未落盘)
        assert capture.buffered_rows(today) == 1
        assert result.basket_states[bid] == "verified"
        assert bvs.list_rows(bid, today, db_path=isolated_env.db_path)[0].source == "intraday"
        capture.reset_capture_state()

    def test_bypass_failure_never_breaks_the_tick(self, isolated_env, monkeypatch):
        """存拍 / 验证任一炸掉 → 只 WARNING,**四哨兵照常出结果**(⑧-B 硬约束)。"""
        from neckline.sentinel import basket_verify, capture

        capture.reset_capture_state()
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        self._seed(isolated_env, report_day, today)
        now = datetime.combine(today, time(10, 30))

        def _boom(*a, **k):
            raise RuntimeError("旁路炸了")

        monkeypatch.setattr(capture, "record_intraday_tick", _boom)
        monkeypatch.setattr(basket_verify, "run_intraday_verification", _boom)
        result = run_tick(now, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir,
                          quotes_fn=self._quotes_fn("600001.SH", 10.5))
        assert result.quotes_fetched == 1 and result.breadth_snapshot is not None
        assert result.captured_ticks == 0 and result.basket_states == {}
        capture.reset_capture_state()
