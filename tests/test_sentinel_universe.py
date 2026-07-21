"""关注池组装单测(plan 阶段3)。覆盖:①从盘后报告读候选(含 entry_spec/
invalidation_spec 完整往返)+ 持仓 + 昨日涨停股三路合并去重;②报告缺失时优雅
降级为空候选(不崩,不是"报告本身没有候选");③前5日均量;④股票元数据(板块/
ST/上市日)查询;⑤新股豁免窗口判定。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, write_daily_fixture

from neckline.data.board import Board
from neckline.report import store
from neckline.report.candidates import Candidate
from neckline.sentinel.positions import open_position
from neckline.sentinel.universe import (
    is_new_stock_exempt,
    load_prev5_avg_volume,
    load_stock_meta,
    load_watch_universe,
)

pytestmark = pytest.mark.usefixtures("isolated_env")


def _candidate(ts_code: str, **overrides) -> Candidate:
    base = dict(
        ts_code=ts_code, name=ts_code, close=10.0, score=90.0, rank=1, board="MAIN",
        pattern_tags=[], hot_sectors=[], sector_names=[],
        entry_plan="回调低吸...", stop_loss="止损...", target="目标...",
        invalidation_text="证伪...",
        invalidation_spec={"low_open_pct": -0.02, "vol_ratio_low": 0.8, "vol_ratio_high": 3.0},
        entry_spec={"buypoint": "pullback", "ma10": 9.5, "prev_close": 10.0},
    )
    base.update(overrides)
    return Candidate(**base)


def _save_report(settings, trade_date: date, candidates, watchlist=None):
    store.save_report(
        trade_date, strategy_version="v1", sentiment={}, sectors=[],
        candidates=[c.public_dict() for c in candidates], markdown="# test",
        watchlist=watchlist, db_path=settings.db_path,
    )


def _watchlist_check_dict(ts_code: str, **overrides) -> dict:
    """手工构造一条自选体检快照(`WatchlistCheckItem.public_dict()` 的形状),
    免依赖 `report.watchlist_check` 跑真实评分管线——本文件只测 `universe.py`
    的合并/裁剪/转换逻辑,不是评分逻辑本身(那是 `test_watchlist_check.py` 的
    职责)。"""
    base = dict(
        ts_code=ts_code, name=ts_code, pinned=False, source="manual", has_data=True,
        close=10.0, board="MAIN", score=80.0, pattern_tags=[], hot_sectors=[], sector_names=[],
        green_light=True, disqualifiers=[], buy_point_triggered=True,
        entry_plan="回调低吸...", stop_loss="止损...", target="目标...", invalidation_text="证伪...",
        invalidation_spec={"low_open_pct": -0.02, "vol_ratio_low": 0.8, "vol_ratio_high": 3.0},
        entry_spec={"buypoint": "pullback", "ma10": 9.5, "prev_close": 10.0},
        status_changed=False, llm_judgment=None,
    )
    base.update(overrides)
    return base


class TestLoadWatchUniverse:
    def test_candidates_read_from_prior_trading_day_report(self, isolated_env):
        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        _save_report(isolated_env, report_day, [_candidate("600001.SH")])

        wu = load_watch_universe(today, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert wu.report_found is True
        assert wu.report_date == report_day
        assert [c.ts_code for c in wu.candidates] == ["600001.SH"]
        # entry_spec/invalidation_spec 完整往返,不是被裁掉的字段
        assert wu.candidates[0].entry_spec["ma10"] == pytest.approx(9.5)
        assert wu.candidates[0].invalidation_spec["vol_ratio_high"] == pytest.approx(3.0)

    def test_no_report_degrades_to_empty_candidates_not_crash(self, isolated_env):
        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        wu = load_watch_universe(days[-1], db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert wu.report_found is False
        assert wu.candidates == []

    def test_positions_included_and_deduped_with_candidates(self, isolated_env):
        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        _save_report(isolated_env, report_day, [_candidate("600001.SH")])
        open_position("600001.SH", 10.0, 100, report_day, db_path=isolated_env.db_path)  # 恰好也是候选
        open_position("600002.SH", 20.0, 100, report_day, db_path=isolated_env.db_path)

        wu = load_watch_universe(today, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert len(wu.positions) == 2
        # codes 去重:600001.SH 既是候选又是持仓,只出现一次
        assert wu.codes.count("600001.SH") == 1
        assert set(wu.codes) == {"600001.SH", "600002.SH"}

    def test_breadth_extra_codes_from_prior_limit_up_capped(self, isolated_env):
        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        rows = [
            {
                "ts_code": f"60000{i}.SH", "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
                "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
                "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": i,
            }
            for i in range(3)
        ]
        write_daily_fixture(isolated_env, "limit_derived", report_day, rows)

        wu = load_watch_universe(
            today, breadth_cap=2, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir
        )
        assert len(wu.breadth_extra_codes) == 2
        # 按连板数降序,应保留最强的两只(600002/600001,不是600000)
        assert set(wu.breadth_extra_codes) == {"600002.SH", "600001.SH"}


class TestLoadPrev5AvgVolume:
    def test_averages_last_five_trading_days(self, isolated_env):
        days = business_days(date(2026, 7, 1), 10)
        insert_trade_cal(isolated_env, days)
        for i, d in enumerate(days):
            write_daily_fixture(isolated_env, "daily", d, [
                {"ts_code": "600001.SH", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
                 "pre_close": 10.0, "vol": 1000.0 * (i + 1), "amount": 10000.0},
            ])
        as_of = days[-1] + timedelta(days=1)  # "今天"(尚无当日行情),取之前5个交易日
        out = load_prev5_avg_volume(["600001.SH"], as_of, parquet_dir=isolated_env.parquet_dir)
        last5 = [1000.0 * (i + 1) for i in range(5, 10)]
        assert out["600001.SH"] == pytest.approx(sum(last5) / 5)

    def test_missing_code_absent_from_result(self, isolated_env):
        days = business_days(date(2026, 7, 1), 5)
        insert_trade_cal(isolated_env, days)
        write_daily_fixture(isolated_env, "daily", days[0], [
            {"ts_code": "600001.SH", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "pre_close": 10.0, "vol": 1000.0, "amount": 10000.0},
        ])
        out = load_prev5_avg_volume(["999999.SH"], days[-1] + timedelta(days=1), parquet_dir=isolated_env.parquet_dir)
        assert "999999.SH" not in out

    def test_empty_codes_returns_empty_dict(self, isolated_env):
        assert load_prev5_avg_volume([], date(2026, 7, 20), parquet_dir=isolated_env.parquet_dir) == {}

    def test_no_data_at_all_returns_empty_dict(self, isolated_env):
        assert load_prev5_avg_volume(["600001.SH"], date(2026, 7, 20), parquet_dir=isolated_env.parquet_dir) == {}


class TestLoadStockMeta:
    def test_board_and_st_detection(self, isolated_env):
        insert_stock_basic(isolated_env, [
            {"ts_code": "600001.SH", "name": "示例甲", "market": "主板"},
            {"ts_code": "300001.SZ", "name": "示例乙", "market": "创业板"},
            {"ts_code": "688001.SH", "name": "示例丙", "market": "科创板"},
            {"ts_code": "920001.BJ", "name": "示例丁", "market": "北交所"},
            {"ts_code": "600002.SH", "name": "*ST示例", "market": "主板"},
        ])
        meta = load_stock_meta(
            ["600001.SH", "300001.SZ", "688001.SH", "920001.BJ", "600002.SH"],
            db_path=isolated_env.db_path,
        )
        assert meta["600001.SH"].board == Board.MAIN
        assert meta["300001.SZ"].board == Board.GEM
        assert meta["688001.SH"].board == Board.STAR
        assert meta["920001.BJ"].board == Board.BSE
        assert meta["600002.SH"].is_st is True
        assert meta["600001.SH"].is_st is False

    def test_missing_code_absent(self, isolated_env):
        insert_stock_basic(isolated_env, [{"ts_code": "600001.SH", "name": "示例甲", "market": "主板"}])
        meta = load_stock_meta(["999999.SH"], db_path=isolated_env.db_path)
        assert "999999.SH" not in meta

    def test_empty_codes_returns_empty(self, isolated_env):
        assert load_stock_meta([], db_path=isolated_env.db_path) == {}


class TestIsNewStockExempt:
    def _meta(self, board, list_date):
        from neckline.sentinel.universe import StockMeta

        return StockMeta(ts_code="X", name="X", board=board, is_st=False, list_date=list_date)

    def test_star_within_5_trading_days_is_exempt(self, isolated_env):
        days = business_days(date(2024, 3, 1), 10)
        insert_trade_cal(isolated_env, days)
        meta = self._meta(Board.STAR, days[0])
        assert is_new_stock_exempt(meta, days[4]) is True  # 第5个交易日仍豁免

    def test_star_6th_day_no_longer_exempt(self, isolated_env):
        days = business_days(date(2024, 3, 1), 10)
        insert_trade_cal(isolated_env, days)
        meta = self._meta(Board.STAR, days[0])
        assert is_new_stock_exempt(meta, days[5]) is False  # 第6个交易日恢复限制

    def test_main_board_only_first_day_exempt(self, isolated_env):
        days = business_days(date(2024, 3, 1), 5)
        insert_trade_cal(isolated_env, days)
        meta = self._meta(Board.MAIN, days[0])
        assert is_new_stock_exempt(meta, days[0]) is True
        assert is_new_stock_exempt(meta, days[1]) is False

    def test_missing_list_date_defaults_not_exempt(self, isolated_env):
        meta = self._meta(Board.MAIN, None)
        assert is_new_stock_exempt(meta, date(2024, 3, 1)) is False

    def test_old_stock_not_exempt(self, isolated_env):
        days = business_days(date(2024, 3, 1), 5)
        insert_trade_cal(isolated_env, days, range_start=date(2015, 1, 1))
        meta = self._meta(Board.MAIN, date(2015, 1, 1))
        assert is_new_stock_exempt(meta, days[-1]) is False


class TestWatchlistMergedIntoUniverse:
    """v1.1-C.2:自选池并入关注池,优先级与候选/持仓同级,合并后 ≤200(此处用小
    `breadth_cap` 逼近边界)上限必须守住。"""

    def test_watchlist_codes_merged_and_deduped(self, isolated_env):
        from neckline.watchlist import add_watchlist

        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        _save_report(isolated_env, report_day, [_candidate("600001.SH")])
        add_watchlist("600002.SH", db_path=isolated_env.db_path)
        add_watchlist("600001.SH", db_path=isolated_env.db_path)  # 与候选重复,不应出现两次

        wu = load_watch_universe(today, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert set(wu.watchlist_codes) == {"600002.SH", "600001.SH"}
        assert wu.codes.count("600001.SH") == 1
        assert set(wu.codes) >= {"600001.SH", "600002.SH"}

    def test_candidates_positions_watchlist_kept_full_before_breadth_extra(self, isolated_env):
        """去重后「候选+持仓+自选」全保留,`_load_prev_limit_up_codes` 只填剩余
        额度——总数不超过 `breadth_cap`,且候选/持仓/自选一个都不因代理样本被挤掉。"""
        from neckline.watchlist import add_watchlist

        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        _save_report(isolated_env, report_day, [_candidate("600001.SH")])
        open_position("600003.SH", 10.0, 100, report_day, db_path=isolated_env.db_path)
        add_watchlist("600002.SH", db_path=isolated_env.db_path)

        rows = [
            {
                "ts_code": f"70000{i}.SH", "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
                "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
                "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": i,
            }
            for i in range(5)
        ]
        write_daily_fixture(isolated_env, "limit_derived", report_day, rows)

        wu = load_watch_universe(
            today, breadth_cap=5, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir
        )
        # 候选(1)+持仓(1)+自选(1)=3,全部保留;剩余额度2 填代理样本,总数恰为cap
        assert len(wu.codes) == 5
        assert {"600001.SH", "600002.SH", "600003.SH"} <= set(wu.codes)
        assert len(wu.breadth_extra_codes) == 2

    def test_breadth_extra_empty_when_priority_codes_fill_cap(self, isolated_env):
        """持仓+自选+候选去重后已达 `breadth_cap` → 代理样本挤占额度为 0,不报错。"""
        from neckline.watchlist import add_watchlist

        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        add_watchlist("600001.SH", db_path=isolated_env.db_path)
        add_watchlist("600002.SH", db_path=isolated_env.db_path)
        write_daily_fixture(isolated_env, "limit_derived", report_day, [
            {"ts_code": "700001.SH", "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
             "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
             "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": 1},
        ])

        wu = load_watch_universe(
            today, breadth_cap=2, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir
        )
        assert wu.breadth_extra_codes == []
        assert set(wu.codes) == {"600001.SH", "600002.SH"}

    def test_priority_trim_order_positions_watchlist_candidates_when_over_cap(self, isolated_env):
        """兜底裁剪(现实中 5+30+20=55 远低于200,不会触发,但函数须防御性守住):
        `breadth_cap` 小于「持仓+自选+候选」去重后的总量时,按「持仓>自选>候选」
        优先序截断(任务原文钉死的裁剪顺序)。"""
        from neckline.watchlist import add_watchlist

        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        _save_report(isolated_env, report_day, [_candidate("900001.SH")])   # 候选(最低优先级)
        add_watchlist("800001.SH", db_path=isolated_env.db_path)            # 自选(中优先级)
        open_position("700001.SH", 10.0, 100, report_day, db_path=isolated_env.db_path)  # 持仓(最高优先级)

        wu = load_watch_universe(
            today, breadth_cap=2, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir
        )
        assert len(wu.codes) == 2
        assert set(wu.codes) == {"700001.SH", "800001.SH"}   # 持仓 + 自选保留,候选被截掉
        assert wu.breadth_extra_codes == []


class TestWatchlistCandidatesFromCheckSnapshot:
    """v1.1-C.2「自选票享候选同级待遇」:昨晚体检快照里已触发买点的自选票转成
    `Candidate` 形状,供买点/证伪哨兵与盘前校准同码消费。"""

    def test_triggered_watchlist_code_becomes_candidate(self, isolated_env):
        from neckline.watchlist import add_watchlist

        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        add_watchlist("600002.SH", db_path=isolated_env.db_path)
        _save_report(
            isolated_env, report_day, [_candidate("600001.SH")],
            watchlist=[_watchlist_check_dict("600002.SH", buy_point_triggered=True)],
        )

        wu = load_watch_universe(today, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert [c.ts_code for c in wu.watchlist_candidates] == ["600002.SH"]
        wc = wu.watchlist_candidates[0]
        assert wc.entry_spec["ma10"] == pytest.approx(9.5)
        assert wc.invalidation_spec["vol_ratio_high"] == pytest.approx(3.0)

    def test_not_triggered_watchlist_code_excluded(self, isolated_env):
        from neckline.watchlist import add_watchlist

        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        add_watchlist("600002.SH", db_path=isolated_env.db_path)
        _save_report(
            isolated_env, report_day, [],
            watchlist=[_watchlist_check_dict("600002.SH", buy_point_triggered=False)],
        )

        wu = load_watch_universe(today, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert wu.watchlist_candidates == []

    def test_removed_from_watchlist_no_longer_becomes_candidate(self, isolated_env):
        """用户已把该票从自选池删除 → 即使昨晚快照判定触发,盘中也不再当候选处理
        (不必等明天报告才生效)。"""
        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        # 注意:本测试故意不调用 add_watchlist——模拟"曾经在自选池、现已被删除"。
        _save_report(
            isolated_env, report_day, [],
            watchlist=[_watchlist_check_dict("600002.SH", buy_point_triggered=True)],
        )

        wu = load_watch_universe(today, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert wu.watchlist_candidates == []

    def test_triggered_code_already_a_candidate_not_duplicated(self, isolated_env):
        """该票既是候选又是自选(且触发)→ 只在 `candidates` 出现一次,不在
        `watchlist_candidates` 里重复构造。"""
        from neckline.watchlist import add_watchlist

        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        add_watchlist("600001.SH", db_path=isolated_env.db_path)
        _save_report(
            isolated_env, report_day, [_candidate("600001.SH")],
            watchlist=[_watchlist_check_dict("600001.SH", buy_point_triggered=True)],
        )

        wu = load_watch_universe(today, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert wu.watchlist_candidates == []
        assert [c.ts_code for c in wu.candidates] == ["600001.SH"]

    def test_no_report_yields_no_watchlist_candidates(self, isolated_env):
        from neckline.watchlist import add_watchlist

        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        add_watchlist("600002.SH", db_path=isolated_env.db_path)
        wu = load_watch_universe(days[-1], db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert wu.watchlist_candidates == []
