"""单拍编排单测(plan 阶段3)。整条链路(关注池→拉价(注入)→持仓哨兵→防重→推送)
用 `isolated_env` 隔离 + `quotes_fn` 注入合成行情,不联网、不碰真实项目数据。

🔴 **V2.4.0 P0 换血(施工纪律 4:旧断言必须写明被谁取代,⛔ 不删测试换绿)**:

    · 原 `TestRetreatDoesNotSuppressInvalidationOrHolding`(退潮不抑制证伪)
      与 `TestRetreatTwoTierEngineWiring`(黄/红双级制、首拍保守闸、逐拍落指标)
      **整体被下方 `TestP0RetiredIntradayJudgements` 取代** —— 被取代的原因不是
      "断言写错了",而是**它们断言的那两项功能已被撤销判断权**:
      P0.1 表「瞬时跌破 VWAP / 低开暂未翻红 / 通用折算量比异常 / 个股『剔除勿进』
      盘中事件 / 代理关注池 →『大盘退潮』/『今日计划作废、禁开新仓』/ 退潮推送」
      **七行全部 = 删**。
    · 新类是**反向断言**:同样的输入(命中旧阈值的行情)喂进 `run_tick`,断言
      **什么都不产生** —— 这才是「生产判断删除 100%」的机器证据(P0.7 判据 #1/#6),
      也覆盖 P0.8 验收用例 1–7。
    · `TestV2Bypasses::test_bypass_failure_never_breaks_the_tick` 原断言
      `result.breadth_snapshot is not None` 改为断言 `TickResult` **不再有这个属性**
      —— 同一条 P0.1「代理关注池 →『大盘退潮』= 删」。

保留不动的:①非交易时段整拍跳过;②持仓哨兵读现役章程 `stop_pct`;③空态不崩;
④ ⑧-B 两条旁路(存拍 / 篮子验证)。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, seed_active_rule_v1, write_daily_fixture

from neckline.report import store
from neckline.sentinel.channels import PushChannel
from neckline.sentinel.engine import run_tick
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


# ⛔ V2.4.0 P0:夹具 `_seed_mainline_sample`(把「主线板块跳水」样本喂成 ④ 机械种子的
# crc32 配额切片)随退潮判级一并删除 —— 它构造的输入已无消费方。
# ⚠ 该夹具的**方法论**(样本即判据 → 样本组成不得沾 LLM)没有失效,记在
# `sentinel/mainline.py` 模块头;要再做无偏取样回那里读。


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


class TestP0RetiredIntradayJudgements:
    """🔴 **V2.4.0 P0 反向断言:两项盘中判决已撤销判断权**(P0.8 验收用例 1–7)。

    **取代关系(施工纪律 4)**:本类整体取代原
    `TestRetreatDoesNotSuppressInvalidationOrHolding` 与 `TestRetreatTwoTierEngineWiring`
    —— 那两类断言的是「证伪在退潮日照样报」「黄色只落看板、红色推送并闩锁、首拍
    保守降级、每拍落 `retreat_metrics`」,而 P0.1 表把这些行为**逐行判为删**。
    ⚠ 被删的行为不再有"正确值"可断言,唯一还能断言的是**它不再发生** —— 所以这里
    喂的行情**刻意仍命中旧阈值**(低开 / 破 VWAP / 极端量比 / 全池跌停),
    断言 `sentinel_events` 与 `retreat_metrics` **一行都不长**。
    """

    def _crash_quotes(self, price=9.0):
        """关注池全线暴跌(主板 pre_close=10 → 跌停价 9.0):旧口径下这是
        「跌停家数 ≥5 + 主线跳水」两条件同拍,**旧代码会升红色刹车**。"""
        def fn(codes):
            return {
                c: Quote(
                    code=c.split(".")[0], name=c, price=price, pre_close=10.0, open=9.6,
                    high=9.7, low=price, volume=1000.0, amount=price * 1000 * 100, ts="", source="sina",
                )
                for c in codes
            }
        return fn

    def _sentinel_rows(self, isolated_env, today, sentinel: str) -> int:
        import sqlite3

        conn = sqlite3.connect(str(isolated_env.db_path))
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM sentinel_events WHERE trade_date=? AND sentinel=?",
                (today.strftime("%Y%m%d"), sentinel),
            ).fetchone()
        finally:
            conn.close()
        return int(row[0])

    def _retreat_metric_rows(self, isolated_env, today) -> int:
        import sqlite3

        conn = sqlite3.connect(str(isolated_env.db_path))
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM retreat_metrics WHERE trade_date=?",
                (today.strftime("%Y%m%d"),),
            ).fetchone()
        finally:
            conn.close()
        return int(row[0])

    def _seed_member(self, isolated_env, code="600001.SH", vol=15000.0):
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        _setup_calendar_and_history(isolated_env, code, report_day, today, vol=vol)
        seed_active_rule_v1(isolated_env)
        _save_report(isolated_env, report_day)
        _seed_basket_members(isolated_env, report_day, [code])
        insert_stock_basic(isolated_env, [{"ts_code": code, "name": "示例甲", "market": "主板"}])
        return report_day, today

    def _run(self, isolated_env, today, quotes_fn, hhmm=(10, 30)):
        cap = _CapturingChannel()
        result = run_tick(
            datetime.combine(today, time(*hhmm)), channels=[cap],
            db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir,
            quotes_fn=quotes_fn,
        )
        return result, cap

    # —— P0.8 用例 1:瞬时跌破 VWAP —————————————————————————————————————————
    def test_vwap_break_produces_no_sentinel_event(self, isolated_env):
        _report_day, today = self._seed_member(isolated_env)

        def quotes_fn(codes):
            # 现价 9.90 < 当日 VWAP(amount/volume/100 ≈ 10.30):旧口径命中「跌破VWAP」。
            return {"600001.SH": Quote(
                code="600001", name="示例甲", price=9.90, pre_close=10.0, open=10.0, high=10.6,
                low=9.85, volume=60000.0, amount=10.30 * 60000 * 100, ts="", source="sina",
            )}

        result, cap = self._run(isolated_env, today, quotes_fn)
        assert not hasattr(result, "invalidation_signals")
        assert self._sentinel_rows(isolated_env, today, "invalidation") == 0
        assert cap.messages == []

    # —— P0.8 用例 2:低开 4% 且未翻红 ———————————————————————————————————————
    def test_low_open_not_recovered_produces_no_invalidation(self, isolated_env):
        _report_day, today = self._seed_member(isolated_env)

        def quotes_fn(codes):
            # 低开 -4%(旧阈 low_open_pct=-2%)且现价仍 < 昨收:旧口径必命中「剔除勿进」。
            return {"600001.SH": Quote(
                code="600001", name="示例甲", price=9.65, pre_close=10.0, open=9.6, high=9.7,
                low=9.55, volume=60000.0, amount=9.6 * 60000 * 100, ts="", source="sina",
            )}

        result, cap = self._run(isolated_env, today, quotes_fn)
        assert self._sentinel_rows(isolated_env, today, "invalidation") == 0
        assert result.pushed_events == []
        assert cap.messages == []

    # —— P0.8 用例 3:折算量比极高 / 极低 ——————————————————————————————————
    @pytest.mark.parametrize("volume,label", [(1.0, "地量"), (5_000_000.0, "天量")])
    def test_extreme_volume_ratio_produces_no_intraday_verdict(self, isolated_env, volume, label):
        _report_day, today = self._seed_member(isolated_env)

        def quotes_fn(codes):
            return {"600001.SH": Quote(
                code="600001", name="示例甲", price=10.1, pre_close=10.0, open=10.0, high=10.2,
                low=10.0, volume=volume, amount=10.05 * volume * 100, ts="", source="sina",
            )}

        _result, cap = self._run(isolated_env, today, quotes_fn)
        assert self._sentinel_rows(isolated_env, today, "invalidation") == 0, label
        assert cap.messages == []

    # —— P0.8 用例 4:代理关注池命中旧退潮阈值 ——————————————————————————————
    def test_proxy_pool_crash_produces_no_warning_brake_or_push(self, isolated_env):
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        insert_trade_cal(isolated_env, days)
        x_codes = [f"60010{i}.SH" for i in range(6)]   # 6 只全跌停 = 旧口径「≥2 条件同拍」
        _save_report(isolated_env, report_day)
        _seed_basket_members(isolated_env, report_day, x_codes)
        insert_stock_basic(isolated_env, [{"ts_code": c, "name": c, "market": "主板"} for c in x_codes])
        qn = self._crash_quotes()

        # 连跑两拍:旧口径下第二拍必升红色刹车(首拍保守闸只降级一次)。
        for hhmm in ((10, 30), (10, 31)):
            result, cap = self._run(isolated_env, today, qn, hhmm=hhmm)
            # `TickResult` 上这四个位已随判级删除(⛔ 不是置成恒 False)。
            for gone in ("retreat_active", "retreat_alert", "retreat_warning", "breadth_snapshot"):
                assert not hasattr(result, gone), gone
            assert cap.messages == []
        assert self._sentinel_rows(isolated_env, today, "retreat") == 0
        assert self._retreat_metric_rows(isolated_env, today) == 0

    # —— P0.8 用例 5 / 6:库里预置当天旧行 → 新链路既不消费也不回写 ————————————
    def test_preexisting_legacy_rows_are_left_untouched_and_drive_nothing(self, isolated_env):
        """⚠ **取代原 `test_retreat_active_does_not_block_invalidation`**:那条断言
        「今日已有 retreat/brake 行时证伪照报」,而两者都已退役。这里改断言
        **预置的旧行既不被读成状态、也不被删掉**(P0.5 末条:⛔ 不通过删历史行
        让界面看起来修好了)。"""
        from neckline.sentinel.dedup import record_pushed

        _report_day, today = self._seed_member(isolated_env)
        record_pushed(today, "retreat", "", "brake", payload={"body": "旧刹车"},
                      db_path=isolated_env.db_path)
        record_pushed(today, "invalidation", "600001.SH", "trigger", payload={"body": "旧证伪"},
                      db_path=isolated_env.db_path)

        def quotes_fn(codes):
            return {"600001.SH": Quote(
                code="600001", name="示例甲", price=9.65, pre_close=10.0, open=9.6, high=9.7,
                low=9.55, volume=60000.0, amount=9.6 * 60000 * 100, ts="", source="sina",
            )}

        result, _cap = self._run(isolated_env, today, quotes_fn)
        # 旧行**照旧在库里**(只读保留),且**没有新增**。
        assert self._sentinel_rows(isolated_env, today, "retreat") == 1
        assert self._sentinel_rows(isolated_env, today, "invalidation") == 1
        # 旧行不构成任何本拍状态 —— 连能承载它的字段都没有了。
        assert not hasattr(result, "retreat_active")

    # —— P0.8 用例 7:跑多拍不长新行 ————————————————————————————————————————
    def test_many_ticks_add_no_invalidation_retreat_or_metric_rows(self, isolated_env):
        _report_day, today = self._seed_member(isolated_env)

        def quotes_fn(codes):
            return {"600001.SH": Quote(
                code="600001", name="示例甲", price=9.65, pre_close=10.0, open=9.6, high=9.7,
                low=9.55, volume=1.0, amount=9.6 * 100, ts="", source="sina",
            )}

        before_inv = self._sentinel_rows(isolated_env, today, "invalidation")
        before_ret = self._sentinel_rows(isolated_env, today, "retreat")
        before_metrics = self._retreat_metric_rows(isolated_env, today)
        for m in range(30, 36):     # 6 拍 = 6 个 60s tick
            self._run(isolated_env, today, quotes_fn, hhmm=(10, m))
        assert self._sentinel_rows(isolated_env, today, "invalidation") - before_inv == 0
        assert self._sentinel_rows(isolated_env, today, "retreat") - before_ret == 0
        assert self._retreat_metric_rows(isolated_env, today) - before_metrics == 0


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
        # ⚠ 原断言是 `result.breadth_snapshot is not None`(旁路炸了、退潮宽度快照
        # 照样算出来)。**被 P0.1 表「代理关注池 →『大盘退潮』= 删」取代** —— 现在
        # `TickResult` 连这个位都没有了,改断言「拉价照常成功 + 该位确实不存在」。
        assert result.quotes_fetched == 1
        assert not hasattr(result, "breadth_snapshot")
        assert result.captured_ticks == 0 and result.basket_states == {}
        capture.reset_capture_state()
