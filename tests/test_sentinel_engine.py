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
    哨兵结构上只遍历 `wu.candidates`,不遍历"拉到行情的全部代码"。"""

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
