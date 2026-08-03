"""关注池组装单测(plan 阶段3)。覆盖:①从盘后报告读候选(含 entry_spec/
invalidation_spec 完整往返)+ 持仓 + 昨日涨停股三路合并去重;②报告缺失时优雅
降级为空候选(不崩,不是"报告本身没有候选");③前5日均量;④股票元数据(板块/
ST/上市日)查询;⑤新股豁免窗口判定。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, write_daily_fixture

import neckline.sentinel.universe as universe
from neckline.data.board import Board
from neckline.report import store
from neckline.sentinel import mainline
from neckline.sentinel.positions import open_position
from neckline.sentinel.universe import (
    is_new_stock_exempt,
    load_prev5_avg_volume,
    load_stock_meta,
    load_watch_universe,
)

pytestmark = pytest.mark.usefixtures("isolated_env")


def _save_report(settings, trade_date: date):
    store.save_report(
        trade_date, strategy_version="v1", sentiment={}, sectors=[],
        candidates=[], markdown="# test", db_path=settings.db_path,
    )


class TestLoadWatchUniverse:
    def test_targets_built_from_prior_trading_day_baskets(self, isolated_env):
        """**V2-⑬-1**:证伪哨兵的判定对象由「昨晚候选」换成「D0 冻结的 T1/T2 篮子成员」
        —— `WatchTarget` 逐位带上码 / 名 / 全局证伪 spec / 所属篮子。"""
        from neckline.sentinel.invalidation import invalidation_spec

        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        insert_stock_basic(isolated_env, [{"ts_code": "600001.SH", "name": "示例甲", "market": "主板"}])
        _save_report(isolated_env, report_day)
        _seed_basket(isolated_env, report_day, ["600001.SH"], tier=1, key="k1")

        wu = load_watch_universe(today, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert wu.report_found is True
        assert wu.report_date == report_day
        assert [t.ts_code for t in wu.targets] == ["600001.SH"]
        assert wu.targets[0].name == "示例甲"
        assert wu.targets[0].basket_key == "k1"
        # 证伪 spec 是**全局常量那一份**(零入参),不是 per-code 重算出来的
        assert wu.targets[0].invalidation_spec == invalidation_spec()

    def test_no_baskets_degrades_to_empty_targets_not_crash(self, isolated_env):
        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        wu = load_watch_universe(days[-1], db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert wu.report_found is False
        assert wu.targets == []

    def test_target_name_falls_back_to_code_when_meta_missing(self, isolated_env):
        """查不到 `stock_basic` 元数据 → 名字退回代码,**不猜**(也不崩)。"""
        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        _seed_basket(isolated_env, report_day, ["600001.SH"], tier=1, key="k1")
        wu = load_watch_universe(today, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert [t.name for t in wu.targets] == ["600001.SH"]

    def test_positions_included_and_deduped_with_targets(self, isolated_env):
        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        _save_report(isolated_env, report_day)
        _seed_basket(isolated_env, report_day, ["600001.SH"], tier=1, key="k1")
        open_position("600001.SH", 10.0, 100, report_day, db_path=isolated_env.db_path)  # 恰好也是篮子成员
        open_position("600002.SH", 20.0, 100, report_day, db_path=isolated_env.db_path)

        wu = load_watch_universe(today, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert len(wu.positions) == 2
        # codes 去重:600001.SH 既是候选又是持仓,只出现一次
        assert wu.codes.count("600001.SH") == 1
        assert set(wu.codes) == {"600001.SH", "600002.SH"}

    def test_breadth_extra_codes_from_prior_limit_up_capped(self, isolated_env):
        """review 判定线 🟡-N1(2026-08-03,PROJECT_PLAN §五 ⑧-G-D 第②条):截断序
        改判为 `crc32(ts_code)` 升序 —— 不再优先保留连板数高的(旧序与被测量的量
        相关,见 `_load_prev_limit_up_codes` docstring)。"""
        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        codes = [f"60000{i}.SH" for i in range(3)]
        rows = [
            {
                "ts_code": c, "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
                "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
                "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": i,
            }
            for i, c in enumerate(codes)
        ]
        write_daily_fixture(isolated_env, "limit_derived", report_day, rows)

        wu = load_watch_universe(
            today, breadth_cap=2, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir
        )
        assert len(wu.breadth_extra_codes) == 2
        # crc32(ts_code) 升序取前 2:恰好保留 600000/600001(连板数最低的两只)、
        # 排除连板数最高的 600002 —— 与旧行为(保留连板数最高的两只)相反,直接
        # 证明截断不再与 `consec_limit_up_days` 相关。
        expect = sorted(codes, key=mainline.crc_rank)[:2]
        assert expect == ["600000.SH", "600001.SH"]  # 锁死本用例的前提,序变了要重算
        assert wu.breadth_extra_codes == expect
        # ⑧-G-D 追加要求:需求量(3)vs 实际采纳量(2)必须留痕,不能只看 `size`。
        assert wu.breadth_extra_needed == 3
        assert wu.breadth_extra_payload() == {"codes": expect, "size": 2, "restricted_from": 3}

    def test_breadth_extra_needed_equals_size_when_not_restricted(self, isolated_env):
        """需求量 ≤ 池位预算时不截断:`restricted_from` 留 `None`(⛔ 不是"截断到
        0" —— 与 `mainline.MainlineSample` 的 `restricted_from` 语义同款)。"""
        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        write_daily_fixture(isolated_env, "limit_derived", report_day, [
            {"ts_code": "600001.SH", "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
             "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
             "is_limit_down": False, "is_zaban": False, "consec_limit_up_days": 1},
        ])

        wu = load_watch_universe(today, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
        assert wu.breadth_extra_needed == 1
        assert wu.breadth_extra_codes == ["600001.SH"]
        assert wu.breadth_extra_payload() == {"codes": ["600001.SH"], "size": 1, "restricted_from": None}


class TestPrevLimitUpSortOrder:
    """review 判定线 🟡-N1(2026-08-03,PROJECT_PLAN §五 ⑧-G-D 第②条同一件事):
    `_load_prev_limit_up_codes` 的截断序 = **确定性**(不吃 parquet 行序)+ **无偏**
    (不与 `consec_limit_up_days` 相关,该量经本项目 ⑦-K7 审计证实是双尾放大器
    ——次日跌停约 3× 于同簇其余成员,而这份样本正好喂 `retreat.compute_breadth_
    snapshot` 算跌停数/炸板率)。"""

    @staticmethod
    def _rows(codes, consec_by_code):
        return [
            {
                "ts_code": c, "board": "MAIN", "status": "limit_up", "limit_pct": 0.10,
                "limit_up_price": 11.0, "limit_down_price": 9.0, "is_limit_up": True,
                "is_limit_down": False, "is_zaban": False,
                "consec_limit_up_days": consec_by_code[c],
            }
            for c in codes
        ]

    def test_order_is_crc_rank_ascending_not_consec_descending(self, isolated_env):
        codes = [f"6{i:05d}.SH" for i in range(40)]
        # consec 与代码索引强相关(索引越大连板数越高):若排序仍偏向"连板高优先",
        # 结果前几名会集中在索引大的那一段,crc32 序应当反证这一点。
        consec = {c: i for i, c in enumerate(codes)}
        report_day = date(2026, 7, 13)
        write_daily_fixture(isolated_env, "limit_derived", report_day, self._rows(codes, consec))

        result = universe._load_prev_limit_up_codes(report_day, parquet_dir=isolated_env.parquet_dir)
        assert set(result) == set(codes)
        assert result == sorted(codes, key=mainline.crc_rank)
        # 反证:consec 降序排列的结果与 crc32 序不同(不是同一个排列)
        assert result != sorted(codes, key=lambda c: -consec[c])

    def test_two_loads_after_partition_row_shuffle_are_bit_identical(self, isolated_env):
        """⚠ 重写分区(行序打乱,模拟数据修缮/回填)后重读,结果必须逐位相同 ——
        crc32 只看代码字符串、不吃 parquet 行序,这正是 🟡-N1 点名的可复现性破口
        (旧实现的并列由行序打散,重写分区会静默换一批样本)。"""
        codes = [f"6{i:05d}.SH" for i in range(30)]
        consec = {c: 1 for c in codes}   # 全部并列(现实中绝大多数涨停股 consec=1)
        report_day = date(2026, 7, 13)

        write_daily_fixture(isolated_env, "limit_derived", report_day, self._rows(codes, consec))
        first = universe._load_prev_limit_up_codes(report_day, parquet_dir=isolated_env.parquet_dir)

        shuffled = list(reversed(codes))     # 模拟重写分区后行序被打乱
        write_daily_fixture(isolated_env, "limit_derived", report_day, self._rows(shuffled, consec))
        second = universe._load_prev_limit_up_codes(report_day, parquet_dir=isolated_env.parquet_dir)

        assert first == second == sorted(codes, key=mainline.crc_rank)


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
        _save_report(isolated_env, report_day)
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
        # (V1 候选那一类已随 ⑬-1 删除,不再是关注池来源)
        assert "600003.SH" in wu.codes
        assert set(wu.basket_codes) == {"600001.SH", "300001.SZ", "600002.SH"}
        assert "600004.SH" not in wu.codes
        assert wu.index_codes == ["000001.SH", "399006.SZ"]     # 沪主板 + 创业板,确定性排序
        assert set(wu.index_codes) <= set(wu.codes)
        assert wu.breadth_extra_codes == ["700001.SH"]
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
        持仓 > T1/T2 成员 > 指数)。"""
        days = business_days(date(2026, 7, 13), 5)
        insert_trade_cal(isolated_env, days)
        report_day, today = days[-2], days[-1]
        insert_stock_basic(isolated_env, [{"ts_code": "600001.SH", "name": "甲", "market": "主板"}])
        _save_report(isolated_env, report_day)
        open_position("600003.SH", 10.0, 100, report_day, db_path=isolated_env.db_path)
        _seed_basket(isolated_env, report_day, ["600001.SH", "600009.SH"], tier=1, key="k1")

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
