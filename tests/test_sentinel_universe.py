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


def _save_report(settings, trade_date: date, candidates):
    store.save_report(
        trade_date, strategy_version="v1", sentiment={}, sectors=[],
        candidates=[c.public_dict() for c in candidates], markdown="# test",
        db_path=settings.db_path,
    )


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




# ══════════════════════════════════════════════════════════════════════════
# V2-⑧-A 关注池改组:持仓 + T1/T2 篮子成员 + 相关板块指数 + 昨日涨停;自选池退役
# ══════════════════════════════════════════════════════════════════════════

def _seed_basket(settings, d0: date, codes, *, tier: int, key: str) -> int:
    from neckline.db import connection

    with connection(settings.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d0.strftime("%Y%m%d"), key, f"篮{key}", "驱动", "theme", tier,
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


class TestV2WatchPoolComposition:
    """plan §五 V2-⑧-A 验收:**四类来源齐、去重、上限、自选池不再进**。"""

    def test_four_sources_present_and_deduped(self, isolated_env):
        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]

        insert_stock_basic(isolated_env, [
            {"ts_code": "600001.SH", "name": "沪主板", "market": "主板"},
            {"ts_code": "300001.SZ", "name": "创业板", "market": "创业板"},
            {"ts_code": "600003.SH", "name": "持仓票", "market": "主板"},
        ])
        _save_report(isolated_env, report_day, [_candidate("600009.SH")])
        open_position("600003.SH", 10.0, 100, report_day, db_path=isolated_env.db_path)
        _seed_basket(isolated_env, report_day, ["600001.SH", "300001.SZ"], tier=1, key="k1")
        _seed_basket(isolated_env, report_day, ["600002.SH"], tier=2, key="k2")
        _seed_basket(isolated_env, report_day, ["600004.SH"], tier=3, key="k3")   # T3 不进盘中池
        write_daily_fixture(isolated_env, "limit_derived", report_day, [
            {"ts_code": "700001.SH", "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
             "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
             "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": 1},
        ])

        wu = load_watch_universe(today, db_path=isolated_env.db_path,
                                 parquet_dir=isolated_env.parquet_dir)
        # ① 持仓 ② T1/T2 篮子成员(T3 不进)③ 板块指数 ④ 昨日涨停 —— 四类齐
        assert "600003.SH" in wu.codes
        assert set(wu.basket_codes) == {"600001.SH", "300001.SZ", "600002.SH"}
        assert "600004.SH" not in wu.codes
        assert wu.index_codes == ["000001.SH", "399006.SZ"]     # 沪主板 + 创业板,确定性排序
        assert set(wu.index_codes) <= set(wu.codes)
        assert wu.breadth_extra_codes == ["700001.SH"]
        # 候选(V1 残留,⑬-1 才删)仍在
        assert "600009.SH" in wu.codes
        # 去重:codes 无重复
        assert len(wu.codes) == len(set(wu.codes))
        assert [b.tier for b in wu.baskets] == [1, 2]

    def test_universe_module_does_not_read_watchlist_at_all(self):
        """守门:`universe.py` 里不许再出现 `neckline.watchlist` 的 import(⑧-A 立,
        ⑬-11 起该模块已物理删除 —— 本断言从"不许读"升级为"读也读不到",**仍然保留**:
        它是关注池组成的锚点,防止未来有人把某个新的"自选式"来源接回来。全仓级的
        零 import 守门另见 `tests/test_v1_retirement_guard.py`。"""
        import ast
        from pathlib import Path

        src = (Path(__file__).resolve().parent.parent / "neckline" / "sentinel"
               / "universe.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "watchlist" not in node.module
            elif isinstance(node, ast.Import):
                assert all("watchlist" not in a.name for a in node.names)

    def test_cap_is_respected_and_indexes_yield_first(self, isolated_env):
        """总量 ≤ `breadth_cap`;真要挤,先挤**没有纪律消费方**的指数(优先序:
        持仓 > T1/T2 成员 > 候选 > 指数)。"""
        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        insert_stock_basic(isolated_env, [{"ts_code": "600001.SH", "name": "甲", "market": "主板"}])
        _save_report(isolated_env, report_day, [_candidate("600009.SH")])
        open_position("600003.SH", 10.0, 100, report_day, db_path=isolated_env.db_path)
        _seed_basket(isolated_env, report_day, ["600001.SH"], tier=1, key="k1")

        wu = load_watch_universe(today, breadth_cap=3, db_path=isolated_env.db_path,
                                 parquet_dir=isolated_env.parquet_dir)
        assert len(wu.codes) == 3
        assert set(wu.codes) == {"600003.SH", "600001.SH", "600009.SH"}   # 指数被挤掉
        assert wu.index_codes == []

    def test_no_baskets_is_a_legal_state(self, isolated_env):
        """V2 引擎还没跑 / 今日无定档篮子 → 空篮子列表,关注池照常组装(不崩)。"""
        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        open_position("600003.SH", 10.0, 100, report_day, db_path=isolated_env.db_path)
        wu = load_watch_universe(today, db_path=isolated_env.db_path,
                                 parquet_dir=isolated_env.parquet_dir)
        assert wu.baskets == [] and wu.basket_codes == []
        assert wu.codes == ["600003.SH"]

    def test_index_codes_do_not_pollute_retreat_breadth_sample(self, isolated_env):
        """指数进了关注池,但**退潮宽度样本不受影响** —— `compute_breadth_snapshot`
        对查无 `stock_basic` 元数据的代码结构上就跳过(⑧-D:纪律判定零改动)。"""
        from neckline.sentinel.quotes import Quote
        from neckline.sentinel.retreat import compute_breadth_snapshot

        insert_stock_basic(isolated_env, [{"ts_code": "600001.SH", "name": "甲", "market": "主板"}])
        meta = load_stock_meta(["600001.SH", "000001.SH"], db_path=isolated_env.db_path)
        quotes = {
            code: Quote(code=code, name=code, price=11.0, pre_close=10.0, open=10.0,
                        high=11.0, low=10.0, volume=1.0, amount=1.0, ts="", source="sina")
            for code in ("600001.SH", "000001.SH")
        }
        snap = compute_breadth_snapshot(date(2026, 7, 24), quotes, meta)
        assert snap.sample_size == 1 and snap.limit_up_count == 1     # 指数没进分母
