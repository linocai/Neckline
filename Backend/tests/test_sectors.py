"""强势板块单测(plan 2.2/1.6)。① `_add_board_age` streak 计算(独立于研究侧
`research/p2_sector_age.py`,同算法生产侧重新实现,自己的单测锁死);② 板块排序
按 20 日动量降序 + 早期年龄(1-5天)软加分;③ 无前视(截断未来不改变过去的
board_age/ret_20d);④ 成分映射与热榜查找;⑤ 文件缺失优雅降级。"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from tests.conftest import business_days, write_flat_parquet

from neckline.report.sectors import (
    EARLY_AGE_BONUS,
    _add_board_age,
    compute_sector_strength,
    load_member_map,
    sector_hot_lookup,
)

pytestmark = pytest.mark.usefixtures("isolated_env")


class TestAddBoardAge:
    def test_streak_starts_when_close_crosses_above_ma20(self):
        dates = business_days(date(2024, 1, 2), 23)
        closes = [100.0] * 20 + [101.0, 103.0, 106.0]
        df = pl.DataFrame({"ts_code": ["AAA.TI"] * 23, "trade_date": dates, "close": closes})
        out = _add_board_age(df).sort("trade_date")
        assert out.row(19, named=True)["board_age"] == 0   # close(19)==ma20(19),不算"站上"(严格 >)
        assert out.row(20, named=True)["board_age"] == 1   # 首次站上
        assert out.row(21, named=True)["board_age"] == 2
        assert out.row(22, named=True)["board_age"] == 3   # 最后一天


def _seed_three_boards(settings, dates):
    n = len(dates)
    ccc = [100.0 + i * 2 for i in range(n)]          # 长期在 MA20 上方(早已站稳,不该拿早期加分)
    aaa = [100.0] * (n - 4) + [101.0, 103.0, 106.0, 110.0]  # 最后几天才转强(早期加分区间)
    bbb = [100.0 - i * 0.5 for i in range(n)]         # 持续下行,不站上 MA20
    rows = []
    for d, c in zip(dates, ccc):
        rows.append({"ts_code": "CCC.TI", "trade_date": d, "close": c})
    for d, c in zip(dates, aaa):
        rows.append({"ts_code": "AAA.TI", "trade_date": d, "close": c})
    for d, c in zip(dates, bbb):
        rows.append({"ts_code": "BBB.TI", "trade_date": d, "close": c})
    write_flat_parquet(settings, "ths_daily.parquet", rows)
    write_flat_parquet(
        settings,
        "ths_index.parquet",
        [
            {"ts_code": "AAA.TI", "name": "人工智能"},
            {"ts_code": "BBB.TI", "name": "白酒"},
            {"ts_code": "CCC.TI", "name": "新能源汽车"},
        ],
    )


class TestComputeSectorStrength:
    def test_ranks_by_20d_momentum_and_applies_early_bonus(self, isolated_env):
        dates = business_days(date(2024, 1, 2), 28)
        _seed_three_boards(isolated_env, dates)
        trade_date = dates[-1]

        scores = compute_sector_strength(trade_date, parquet_dir=isolated_env.parquet_dir, top_n=10)
        codes_in_order = [s.index_code for s in scores]
        assert codes_in_order == ["CCC.TI", "AAA.TI", "BBB.TI"]  # 动量降序:CCC最高,BBB垫底(负收益)

        by_code = {s.index_code: s for s in scores}
        assert by_code["CCC.TI"].board_age > 5      # 长期站稳,不在早期加分区间
        assert by_code["CCC.TI"].bonus == 0.0
        assert 1 <= by_code["AAA.TI"].board_age <= 5  # 刚转强
        assert by_code["AAA.TI"].bonus == EARLY_AGE_BONUS
        assert by_code["BBB.TI"].board_age == 0
        assert by_code["BBB.TI"].bonus == 0.0
        assert by_code["AAA.TI"].name == "人工智能"
        assert by_code["AAA.TI"].ret_20d > 0
        assert by_code["BBB.TI"].ret_20d < 0

    def test_top_n_truncates(self, isolated_env):
        dates = business_days(date(2024, 1, 2), 28)
        _seed_three_boards(isolated_env, dates)
        scores = compute_sector_strength(dates[-1], parquet_dir=isolated_env.parquet_dir, top_n=2)
        assert len(scores) == 2
        assert [s.rank for s in scores] == [1, 2]

    def test_no_lookahead_truncation_invariance(self, isolated_env):
        """在某历史日出报告,截断掉该日之后的数据不应改变当日算出的 board_age/ret_20d
        (§3.8 无前视铁律,呼应 test_features.py 的同类断言)。"""
        dates = business_days(date(2024, 1, 2), 28)
        _seed_three_boards(isolated_env, dates)
        mid_date = dates[20]  # 第一个 ma20 非空之后没几天的历史日

        full = compute_sector_strength(mid_date, parquet_dir=isolated_env.parquet_dir, top_n=10)

        # 换一份【只到 mid_date 为止】的 ths_daily(模拟"当时"还没有后面日子的数据)
        truncated_rows = (
            pl.read_parquet(isolated_env.parquet_dir / "ths_daily.parquet")
            .filter(pl.col("trade_date") <= mid_date)
            .to_dicts()
        )
        write_flat_parquet(isolated_env, "ths_daily.parquet", truncated_rows)
        truncated = compute_sector_strength(mid_date, parquet_dir=isolated_env.parquet_dir, top_n=10)

        full_by_code = {s.index_code: (s.board_age, round(s.ret_20d, 8)) for s in full}
        trunc_by_code = {s.index_code: (s.board_age, round(s.ret_20d, 8)) for s in truncated}
        assert full_by_code == trunc_by_code

    def test_missing_ths_daily_file_returns_empty_list(self, isolated_env):
        assert compute_sector_strength(date(2024, 1, 2), parquet_dir=isolated_env.parquet_dir) == []

    def test_missing_index_names_falls_back_to_code(self, isolated_env):
        dates = business_days(date(2024, 1, 2), 28)
        rows = [{"ts_code": "AAA.TI", "trade_date": d, "close": 100.0 + i} for i, d in enumerate(dates)]
        write_flat_parquet(isolated_env, "ths_daily.parquet", rows)
        # 故意不写 ths_index.parquet
        scores = compute_sector_strength(dates[-1], parquet_dir=isolated_env.parquet_dir)
        assert scores[0].name == "AAA.TI"  # 名称缺失时退化为代码本身


class TestMemberMapAndHotLookup:
    def test_load_member_map_groups_by_stock_code(self, isolated_env):
        write_flat_parquet(
            isolated_env,
            "ths_member.parquet",
            [
                {"index_code": "AAA.TI", "con_code": "600001.SH", "con_name": "示例甲"},
                {"index_code": "BBB.TI", "con_code": "600001.SH", "con_name": "示例甲"},
                {"index_code": "CCC.TI", "con_code": "300002.SZ", "con_name": "示例乙"},
            ],
        )
        m = load_member_map(parquet_dir=isolated_env.parquet_dir)
        assert set(m["600001.SH"]) == {"AAA.TI", "BBB.TI"}
        assert m["300002.SZ"] == ["CCC.TI"]

    def test_load_member_map_missing_file_returns_empty_dict(self, isolated_env):
        assert load_member_map(parquet_dir=isolated_env.parquet_dir) == {}

    def test_sector_hot_lookup_by_index_code(self, isolated_env):
        dates = business_days(date(2024, 1, 2), 28)
        _seed_three_boards(isolated_env, dates)
        scores = compute_sector_strength(dates[-1], parquet_dir=isolated_env.parquet_dir)
        lut = sector_hot_lookup(scores)
        assert lut["AAA.TI"].name == "人工智能"
        assert "ZZZ.TI" not in lut


# —— v1.4-①-C 板块数据过期告警(§七 P0-3)——————————————————————————————————

class TestSectorFreshness:
    """`compute_sector_strength` 当日无行时**返回空列表且不报错**(优雅降级)——从报告上
    分不清「今天没行情」与「板块表根本没更新」。这组断言锁死那个开关。"""

    def _seed(self, settings, dates):
        from neckline.data.concept_data import upsert_ths_daily

        upsert_ths_daily(pl.DataFrame({
            "ts_code": ["883300.TI"] * len(dates),
            "trade_date": [d.strftime("%Y%m%d") for d in dates],
            "close": [100.0] * len(dates),
        }), settings.parquet_dir)

    def test_missing_file_is_unavailable_not_fresh(self, isolated_env):
        """文件不存在 → `lagDays=-1`(哨兵值)且 `stale=True`。**绝不是 0/新鲜**。"""
        from neckline.report.sectors import SECTOR_LAG_UNKNOWN, compute_sector_freshness

        f = compute_sector_freshness(date(2026, 7, 28), isolated_env.parquet_dir)
        assert (f.sector_data_date, f.lag_days, f.stale) == ("", SECTOR_LAG_UNKNOWN, True)
        assert f.unavailable is True and "完全缺失" in f.note()

    def test_same_day_is_fresh_with_empty_note(self, isolated_env):
        from tests.conftest import insert_trade_cal
        from neckline.report.sectors import compute_sector_freshness

        days = business_days(date(2026, 7, 20), 5)
        insert_trade_cal(isolated_env, days)
        self._seed(isolated_env, days)
        f = compute_sector_freshness(days[-1], isolated_env.parquet_dir)
        assert (f.lag_days, f.stale, f.note()) == (0, False, "")

    def test_lag_counted_in_trading_days(self, isolated_env):
        """落后天数按**交易日**算(跨周末不该被算成 3 天)。"""
        from tests.conftest import insert_trade_cal
        from neckline.report.sectors import compute_sector_freshness

        days = business_days(date(2026, 7, 20), 8)
        insert_trade_cal(isolated_env, days)
        self._seed(isolated_env, days[:5])                       # 数据到第 5 个交易日
        f = compute_sector_freshness(days[6], isolated_env.parquet_dir)   # 报告日是第 7 个
        assert f.lag_days == 2 and f.stale is False              # 恰在容忍上限内
        assert "落后 2 个交易日" in f.note() and "不可信" not in f.note()

    def test_beyond_tolerance_is_stale_and_says_untrustworthy(self, isolated_env):
        from tests.conftest import insert_trade_cal
        from neckline.report.sectors import SECTOR_DATA_STALE_MAX_LAG_DAYS, compute_sector_freshness

        days = business_days(date(2026, 7, 20), 10)
        insert_trade_cal(isolated_env, days)
        self._seed(isolated_env, days[:5])
        f = compute_sector_freshness(days[5 + SECTOR_DATA_STALE_MAX_LAG_DAYS], isolated_env.parquet_dir)
        assert f.lag_days == SECTOR_DATA_STALE_MAX_LAG_DAYS + 1
        assert f.stale is True
        # 「当日暴起板块」与「题材持续天数」两路必须被点名说不可信
        assert "不可信" in f.note()

    def test_public_dict_contract_shape(self, isolated_env):
        from neckline.report.sectors import compute_sector_freshness

        d = compute_sector_freshness(date(2026, 7, 28), isolated_env.parquet_dir).to_public_dict()
        assert set(d) == {"sectorDataDate", "sectorLagDays", "stale"}
