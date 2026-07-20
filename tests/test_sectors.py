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
