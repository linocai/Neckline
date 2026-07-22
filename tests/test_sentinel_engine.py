"""单拍编排单测(plan 阶段3)。整条链路(关注池→拉价(注入)→四哨兵→防重→推送)
用 `isolated_env` 隔离 + `quotes_fn` 注入合成行情,不联网、不碰真实项目数据。
重点断言编排顺序的两条铁律:①非交易时段整拍跳过;②退潮生效后买点哨兵本拍
整体抑制,证伪/持仓哨兵不受影响;以及防重跨拍生效(同一事件第二拍不再推)。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, seed_active_rule_v1, write_daily_fixture

from neckline.report import store
from neckline.report.candidates import Candidate
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


def _candidate(ts_code="600001.SH", **overrides) -> Candidate:
    base = dict(
        ts_code=ts_code, name="示例甲", close=10.0, score=90.0, rank=1, board="MAIN",
        pattern_tags=[], hot_sectors=[], sector_names=[],
        entry_plan="回调低吸...", stop_loss="止损...", target="目标...",
        invalidation_text="证伪...",
        invalidation_spec={"low_open_pct": -0.02, "vwap_break": True, "vol_ratio_low": 0.8, "vol_ratio_high": 3.0},
        entry_spec={"buypoint": "pullback", "ma10": 9.5, "prev_close": 10.0, "breakout_vol_expand": 1.5},
    )
    base.update(overrides)
    return Candidate(**base)


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


def _save_report(settings, report_day: date, candidates):
    store.save_report(
        report_day, strategy_version="v1", sentiment={}, sectors=[],
        candidates=[c.public_dict() for c in candidates], markdown="# test", db_path=settings.db_path,
    )


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


class TestEntrySentinelFiresAndDedupes:
    def test_fires_once_and_dedupes_on_second_tick(self, isolated_env):
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        # vol=200000 让 prev5_avg_vol=200000,配合 quote 的 current_vol=60000/elapsed=60min
        # 折算比=1.2,落在 pullback 下限(0.8)与证伪高位异常线(3.0)之间,单独触发买点、
        # 不同时触发证伪(两者本可能同时为真,这里刻意构造成互斥以隔离测试意图)。
        _setup_calendar_and_history(isolated_env, "600001.SH", report_day, today, vol=200000.0)
        seed_active_rule_v1(isolated_env)
        _save_report(isolated_env, report_day, [_candidate("600001.SH")])
        insert_stock_basic(isolated_env, [{"ts_code": "600001.SH", "name": "示例甲", "market": "主板"}])

        now = datetime.combine(today, time(10, 30))  # elapsed=60min,脱离 early 窗口

        def quotes_fn(codes):
            return {
                "600001.SH": Quote(
                    code="600001", name="示例甲", price=10.2, pre_close=10.0, open=10.0, high=10.3, low=10.0,
                    volume=60000.0, amount=10.2 * 60000 * 100 * 0.95, ts="", source="sina",
                )
            }

        cap = _CapturingChannel()
        r1 = run_tick(
            now, channels=[cap], db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir,
            quotes_fn=quotes_fn,
        )
        assert len(r1.entry_signals) == 1
        assert len(cap.messages) == 1
        assert already_pushed(today, "entry", "600001.SH", "trigger", db_path=isolated_env.db_path) is True

        cap2 = _CapturingChannel()
        r2 = run_tick(
            now, channels=[cap2], db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir,
            quotes_fn=quotes_fn,
        )
        assert len(r2.entry_signals) == 1  # 判定仍然成立(纯函数不知道"推过"这回事)
        assert r2.skipped_duplicate == 1
        assert cap2.messages == []  # 但第二拍不应再实际推送


class TestRetreatSuppressesEntryButNotInvalidationOrHolding:
    def test_retreat_active_blocks_entry_this_tick(self, isolated_env):
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        _setup_calendar_and_history(isolated_env, "600001.SH", report_day, today, vol=15000.0)
        seed_active_rule_v1(isolated_env)
        candidate_would_enter = _candidate("600001.SH")
        _save_report(isolated_env, report_day, [candidate_would_enter])
        insert_stock_basic(isolated_env, [{"ts_code": "600001.SH", "name": "示例甲", "market": "主板"}])

        now = datetime.combine(today, time(10, 30))

        def quotes_fn(codes):
            out = {
                "600001.SH": Quote(
                    code="600001", name="示例甲", price=10.2, pre_close=10.0, open=10.0, high=10.3, low=10.0,
                    volume=60000.0, amount=10.2 * 60000 * 100 * 0.95, ts="", source="sina",
                )
            }
            return out

        # 预先手工记一条今日已触发的退潮刹车事件,模拟"更早一拍已经触发"
        from neckline.sentinel.dedup import record_pushed

        record_pushed(today, "retreat", "", "brake", db_path=isolated_env.db_path)

        cap = _CapturingChannel()
        result = run_tick(
            now, channels=[cap], db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir,
            quotes_fn=quotes_fn,
        )
        assert result.retreat_active is True
        assert result.entry_signals == []  # 本该触发买点,但退潮生效当日整体抑制


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
        _save_report(isolated_env, report_day, [_candidate(c, hot_sectors=["半导体"]) for c in x_codes])
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

    def test_yellow_single_condition_does_not_suppress_entry_or_push(self, isolated_env):
        reset_retreat_process_state()
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        _setup_calendar_and_history(isolated_env, "600001.SH", report_day, today, vol=200000.0)
        y = _candidate("600001.SH")  # 会触发买点(上涨),无 hot_sectors → 不进主线样本
        x_codes = ["600201.SH", "600202.SH", "600203.SH"]
        xs = [_candidate(c, hot_sectors=["半导体"]) for c in x_codes]
        _save_report(isolated_env, report_day, [y] + xs)
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
        assert "600001.SH" in {s.ts_code for s in r.entry_signals}  # 黄色不抑制买点
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
        assert result.entry_signals == []
        assert result.holding_alerts == []
        assert result.pushed_events == []


class TestNeverRecommendsNewStocks:
    """原则守护(§2.4 铁律「盘中不产生任何新决策,永不盘中推荐新票」)的直接单测:
    即便某只【非候选】代码的行情完美满足买点哨兵的全部触发条件,只要它不在昨晚
    报告的候选列表里,就永远不会被评估、更不会出现在 entry_signals 里——买点
    哨兵结构上只遍历 `wu.candidates`(+ v1.1-C 起也含 `wu.watchlist_candidates`,
    见 `TestWatchlistCandidateTreatedAsCandidate`——**同样是昨晚 16:35 报告生成
    时已经算好、写死的自选体检快照,不是盘中临时决定**,不违反本铁律),不遍历
    "拉到行情的全部代码"。"""

    def test_non_candidate_code_with_perfect_entry_conditions_is_never_surfaced(self, isolated_env):
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        _setup_calendar_and_history(isolated_env, "600001.SH", report_day, today, vol=200000.0)
        seed_active_rule_v1(isolated_env)
        _save_report(isolated_env, report_day, [_candidate("600001.SH")])  # 唯一候选
        insert_stock_basic(isolated_env, [{"ts_code": "600001.SH", "name": "示例甲", "market": "主板"}])

        now = datetime.combine(today, time(10, 30))

        # NOT_A_CANDIDATE.SH 完美满足"候选600001.SH的买点+确认条件"(站稳同一个
        # ma10/量比/VWAP),但它压根不是候选、也没进关注池(不在 candidates/
        # positions/breadth_extra 任何一路里)——哨兵结构上不该碰它。
        def quotes_fn(codes):
            good_quote = Quote(
                code="600001", name="示例甲", price=10.2, pre_close=10.0, open=10.0, high=10.3, low=10.0,
                volume=60000.0, amount=10.2 * 60000 * 100 * 0.95, ts="", source="sina",
            )
            return {code: good_quote for code in codes} | {
                "NOT_A_CANDIDATE.SH": Quote(
                    code="999999", name="非候选", price=10.2, pre_close=10.0, open=10.0, high=10.3, low=10.0,
                    volume=60000.0, amount=10.2 * 60000 * 100 * 0.95, ts="", source="sina",
                )
            }

        result = run_tick(
            now, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir, quotes_fn=quotes_fn,
        )
        assert result.watched_codes == 1  # 关注池只有候选600001.SH,NOT_A_CANDIDATE.SH 从未进入关注池
        entry_codes = {sig.ts_code for sig in result.entry_signals}
        assert entry_codes == {"600001.SH"}
        assert "NOT_A_CANDIDATE.SH" not in entry_codes

    def test_entry_codes_always_subset_of_report_candidates(self, isolated_env):
        """更一般的结构性断言:任意一拍产出的 entry_signals 代码集合,必须是
        "昨晚报告候选代码集合"的子集——不依赖某一次具体构造,而是断言这个
        结构性不变量本身。"""
        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        _setup_calendar_and_history(isolated_env, "600001.SH", report_day, today, vol=200000.0)
        seed_active_rule_v1(isolated_env)
        candidate_codes = {"600001.SH"}
        _save_report(isolated_env, report_day, [_candidate(c) for c in candidate_codes])
        insert_stock_basic(isolated_env, [{"ts_code": c, "name": c, "market": "主板"} for c in candidate_codes])

        now = datetime.combine(today, time(10, 30))

        def quotes_fn(codes):
            return {
                code: Quote(
                    code=code, name=code, price=10.2, pre_close=10.0, open=10.0, high=10.3, low=10.0,
                    volume=60000.0, amount=10.2 * 60000 * 100 * 0.95, ts="", source="sina",
                )
                for code in codes
            }

        result = run_tick(
            now, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir, quotes_fn=quotes_fn,
        )
        entry_codes = {sig.ts_code for sig in result.entry_signals}
        assert entry_codes.issubset(candidate_codes)


def _watchlist_check_dict(ts_code: str, **overrides) -> dict:
    """手工构造一条自选体检快照(`WatchlistCheckItem.public_dict()` 形状),供
    `universe._build_watchlist_candidates` 转成 `Candidate` 消费。"""
    base = dict(
        ts_code=ts_code, name="示例自选", pinned=False, source="manual", has_data=True,
        close=10.0, board="MAIN", score=80.0, pattern_tags=[], hot_sectors=[], sector_names=[],
        green_light=True, disqualifiers=[], buy_point_triggered=True,
        entry_plan="回调低吸...", stop_loss="止损...", target="目标...", invalidation_text="证伪...",
        invalidation_spec={"low_open_pct": -0.02, "vwap_break": True, "vol_ratio_low": 0.8, "vol_ratio_high": 3.0},
        entry_spec={"buypoint": "pullback", "ma10": 9.5, "prev_close": 10.0, "breakout_vol_expand": 1.5},
        status_changed=False, llm_judgment=None,
    )
    base.update(overrides)
    return base


class TestWatchlistCandidateTreatedAsCandidate:
    """v1.1-C.2「自选票享候选同级待遇」:昨晚自选体检快照已判定触发买点的自选票,
    盘中买点/证伪哨兵与候选一视同仁——entry_spec/invalidation_spec 都是昨晚
    (16:35 报告生成时)写死的,盘中只读不重算,不违反§2.4「不产生新决策」。"""

    def test_triggered_watchlist_code_fires_entry_signal(self, isolated_env):
        from neckline.watchlist import add_watchlist

        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        _setup_calendar_and_history(isolated_env, "600002.SH", report_day, today, vol=200000.0)
        seed_active_rule_v1(isolated_env)
        add_watchlist("600002.SH", db_path=isolated_env.db_path)
        # 报告候选是空的(300001.SZ,与本票无关)——600002.SH 完全靠自选体检快照进关注池
        store.save_report(
            report_day, strategy_version="v1", sentiment={}, sectors=[], candidates=[],
            markdown="# test", watchlist=[_watchlist_check_dict("600002.SH")], db_path=isolated_env.db_path,
        )
        insert_stock_basic(isolated_env, [{"ts_code": "600002.SH", "name": "示例自选", "market": "主板"}])

        now = datetime.combine(today, time(10, 30))

        def quotes_fn(codes):
            return {
                "600002.SH": Quote(
                    code="600002", name="示例自选", price=10.2, pre_close=10.0, open=10.0, high=10.3, low=10.0,
                    volume=60000.0, amount=10.2 * 60000 * 100 * 0.95, ts="", source="sina",
                )
            }

        result = run_tick(
            now, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir, quotes_fn=quotes_fn,
        )
        assert {sig.ts_code for sig in result.entry_signals} == {"600002.SH"}

    def test_not_triggered_watchlist_code_never_fires(self, isolated_env):
        """自选票昨晚未触发买点(`buy_point_triggered=False`)→ 不进
        `watchlist_candidates`,即便行情完美满足触发条件也不会被评估。"""
        from neckline.watchlist import add_watchlist

        days = business_days(date(2026, 7, 1), 30)
        report_day, today = days[-2], days[-1]
        _setup_calendar_and_history(isolated_env, "600002.SH", report_day, today, vol=200000.0)
        seed_active_rule_v1(isolated_env)
        add_watchlist("600002.SH", db_path=isolated_env.db_path)
        store.save_report(
            report_day, strategy_version="v1", sentiment={}, sectors=[], candidates=[],
            markdown="# test",
            watchlist=[_watchlist_check_dict("600002.SH", buy_point_triggered=False)],
            db_path=isolated_env.db_path,
        )
        insert_stock_basic(isolated_env, [{"ts_code": "600002.SH", "name": "示例自选", "market": "主板"}])

        now = datetime.combine(today, time(10, 30))

        def quotes_fn(codes):
            return {
                "600002.SH": Quote(
                    code="600002", name="示例自选", price=10.2, pre_close=10.0, open=10.0, high=10.3, low=10.0,
                    volume=60000.0, amount=10.2 * 60000 * 100 * 0.95, ts="", source="sina",
                )
            }

        result = run_tick(
            now, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir, quotes_fn=quotes_fn,
        )
        assert result.entry_signals == []
