"""涨跌停衍生表单测(plan 0.4b)。纯函数输入(手工构造 DataFrame),不依赖真实数据
/ DB,独立于 backfill 是否跑过。

价格预期值用 Decimal(ROUND_HALF_UP)独立算,不复用被测模块内部的整数分实现
(避免"用同一个实现验证同一个实现"的测试假阳性)。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import polars as pl
import pytest

from neckline.data.limit_derived import GEM_REFORM_DATE, MAIN_ST_REFORM_DATE, compute_limit_derived


def _limit_price(pre_close: float, pct: float, sign: int) -> float:
    d = Decimal(str(pre_close)) * (1 + Decimal(sign) * Decimal(str(pct)))
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _business_days(start: date, n: int) -> list:
    out = []
    cur = start
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def _daily_row(ts_code, trade_date, pre_close, close, high=None, low=None, open_=None):
    return {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "open": open_ if open_ is not None else pre_close,
        "high": high if high is not None else max(pre_close, close),
        "low": low if low is not None else min(pre_close, close),
        "close": close,
        "pre_close": pre_close,
    }


def _stock_basic(rows):
    return pl.DataFrame(rows, schema={"ts_code": pl.Utf8, "market": pl.Utf8, "list_date": pl.Date}, orient="row")


def _empty_namechange():
    return pl.DataFrame(
        schema={"ts_code": pl.Utf8, "name": pl.Utf8, "start_date": pl.Date, "end_date": pl.Date}
    )


def _daily_df(rows):
    return pl.DataFrame(rows).with_columns(pl.col("trade_date").cast(pl.Date))


class TestBoardRatesNonST:
    def setup_method(self):
        self.days = _business_days(date(2024, 1, 2), 5)  # 远离 GEM/ST 改革分界,走"当前"规则分支

    def test_main_board_10pct_limit_up(self):
        d = self.days[2]
        pre_close = 10.00
        up = _limit_price(pre_close, 0.10, +1)
        daily = _daily_df([_daily_row("600001.SH", d, pre_close, up, high=up)])
        sb = _stock_basic([("600001.SH", "主板", date(2015, 1, 1))])
        out = compute_limit_derived(daily, sb, _empty_namechange(), self.days)
        assert len(out) == 1
        row = out.row(0, named=True)
        assert row["status"] == "limit_up"
        assert row["is_limit_up"] is True
        assert row["limit_up_price"] == pytest.approx(up)

    def test_main_board_10pct_limit_down(self):
        d = self.days[2]
        pre_close = 10.00
        down = _limit_price(pre_close, 0.10, -1)
        daily = _daily_df([_daily_row("600001.SH", d, pre_close, down, low=down)])
        sb = _stock_basic([("600001.SH", "主板", date(2015, 1, 1))])
        out = compute_limit_derived(daily, sb, _empty_namechange(), self.days)
        assert len(out) == 1
        assert out.row(0, named=True)["status"] == "limit_down"

    def test_star_20pct(self):
        d = self.days[2]
        pre_close = 50.00
        up = _limit_price(pre_close, 0.20, +1)
        daily = _daily_df([_daily_row("688001.SH", d, pre_close, up, high=up)])
        sb = _stock_basic([("688001.SH", "科创板", date(2015, 1, 1))])
        out = compute_limit_derived(daily, sb, _empty_namechange(), self.days)
        assert len(out) == 1
        assert out.row(0, named=True)["limit_pct"] == pytest.approx(0.20)

    def test_bse_30pct(self):
        d = self.days[2]
        pre_close = 20.00
        up = _limit_price(pre_close, 0.30, +1)
        daily = _daily_df([_daily_row("920001.BJ", d, pre_close, up, high=up)])
        sb = _stock_basic([("920001.BJ", "北交所", date(2015, 1, 1))])
        out = compute_limit_derived(daily, sb, _empty_namechange(), self.days)
        assert len(out) == 1
        assert out.row(0, named=True)["limit_pct"] == pytest.approx(0.30)

    def test_not_at_limit_not_flagged(self):
        d = self.days[2]
        daily = _daily_df([_daily_row("600001.SH", d, 10.00, 10.50)])  # +5%,不是涨停
        sb = _stock_basic([("600001.SH", "主板", date(2015, 1, 1))])
        out = compute_limit_derived(daily, sb, _empty_namechange(), self.days)
        assert len(out) == 0


class TestGemReformDateSplit:
    def test_gem_10pct_before_reform(self):
        days = _business_days(date(2020, 8, 17), 5)  # 全在 0824 之前
        d = days[2]
        assert d < GEM_REFORM_DATE
        pre_close = 20.00
        up = _limit_price(pre_close, 0.10, +1)
        daily = _daily_df([_daily_row("300001.SZ", d, pre_close, up, high=up)])
        sb = _stock_basic([("300001.SZ", "创业板", date(2015, 1, 1))])
        out = compute_limit_derived(daily, sb, _empty_namechange(), days)
        assert len(out) == 1
        assert out.row(0, named=True)["limit_pct"] == pytest.approx(0.10)

    def test_gem_20pct_on_and_after_reform(self):
        days = _business_days(GEM_REFORM_DATE, 5)
        d = days[0]
        assert d == GEM_REFORM_DATE
        pre_close = 20.00
        up = _limit_price(pre_close, 0.20, +1)
        daily = _daily_df([_daily_row("300001.SZ", d, pre_close, up, high=up)])
        sb = _stock_basic([("300001.SZ", "创业板", date(2015, 1, 1))])
        out = compute_limit_derived(daily, sb, _empty_namechange(), days)
        assert len(out) == 1
        assert out.row(0, named=True)["limit_pct"] == pytest.approx(0.20)


class TestMainSTReformDateSplit:
    def test_st_5pct_before_reform(self):
        days = _business_days(date(2026, 6, 1), 10)
        d = days[5]
        assert d < MAIN_ST_REFORM_DATE
        pre_close = 10.00
        up = _limit_price(pre_close, 0.05, +1)
        daily = _daily_df([_daily_row("600002.SH", d, pre_close, up, high=up)])
        sb = _stock_basic([("600002.SH", "主板", date(2015, 1, 1))])
        nc = pl.DataFrame(
            [{"ts_code": "600002.SH", "name": "ST测试", "start_date": date(2020, 1, 1), "end_date": None}]
        ).with_columns(pl.col("start_date").cast(pl.Date), pl.col("end_date").cast(pl.Date))
        out = compute_limit_derived(daily, sb, nc, days)
        assert len(out) == 1
        assert out.row(0, named=True)["limit_pct"] == pytest.approx(0.05)

    def test_st_10pct_on_and_after_reform(self):
        days = _business_days(MAIN_ST_REFORM_DATE, 5)
        d = days[0]
        assert d == MAIN_ST_REFORM_DATE
        pre_close = 10.00
        up = _limit_price(pre_close, 0.10, +1)  # 新规后 ST 与普通股同 10%
        daily = _daily_df([_daily_row("600002.SH", d, pre_close, up, high=up)])
        sb = _stock_basic([("600002.SH", "主板", date(2015, 1, 1))])
        nc = pl.DataFrame(
            [{"ts_code": "600002.SH", "name": "ST测试", "start_date": date(2020, 1, 1), "end_date": None}]
        ).with_columns(pl.col("start_date").cast(pl.Date), pl.col("end_date").cast(pl.Date))
        out = compute_limit_derived(daily, sb, nc, days)
        assert len(out) == 1
        assert out.row(0, named=True)["limit_pct"] == pytest.approx(0.10)

    def test_non_st_always_10pct_both_sides_of_reform(self):
        days = _business_days(date(2026, 6, 1), 40)
        pre_close = 10.00
        up = _limit_price(pre_close, 0.10, +1)
        rows = [_daily_row("600003.SH", d, pre_close, up, high=up) for d in (days[2], days[-2])]
        daily = _daily_df(rows)
        sb = _stock_basic([("600003.SH", "主板", date(2015, 1, 1))])
        out = compute_limit_derived(daily, sb, _empty_namechange(), days)
        assert len(out) == 2
        assert set(round(x, 4) for x in out["limit_pct"].to_list()) == {0.10}


class TestNewIPOExemption:
    def test_star_first_5_days_exempt_even_if_huge_move(self):
        days = _business_days(date(2024, 3, 1), 10)
        list_date = days[0]
        # 第 1~5 个交易日:巨幅波动也不应被判涨停(无涨跌幅限制)
        rows = []
        for i in range(5):
            rows.append(_daily_row("688100.SH", days[i], 100.0, 250.0, high=260.0))  # +150%,远超 20%
        daily = _daily_df(rows)
        sb = _stock_basic([("688100.SH", "科创板", list_date)])
        out = compute_limit_derived(daily, sb, _empty_namechange(), days)
        assert len(out) == 0, "科创板新股前 5 个交易日应豁免涨跌幅限制,不应产出任何命中行"

    def test_star_6th_day_limit_applies(self):
        days = _business_days(date(2024, 3, 1), 10)
        list_date = days[0]
        pre_close = 100.0
        up = _limit_price(pre_close, 0.20, +1)
        daily = _daily_df([_daily_row("688100.SH", days[5], pre_close, up, high=up)])  # 第 6 个交易日
        sb = _stock_basic([("688100.SH", "科创板", list_date)])
        out = compute_limit_derived(daily, sb, _empty_namechange(), days)
        assert len(out) == 1, "第 6 个交易日起应恢复 20% 涨跌幅限制"

    def test_main_board_only_first_day_exempt(self):
        days = _business_days(date(2024, 3, 1), 5)
        list_date = days[0]
        # 上市首日巨幅波动不算涨停
        daily_day1 = _daily_row("600100.SH", days[0], 10.0, 25.0, high=25.0)
        # 第 2 个交易日恢复 10% 限制
        pre_close2 = 25.0
        up2 = _limit_price(pre_close2, 0.10, +1)
        daily_day2 = _daily_row("600100.SH", days[1], pre_close2, up2, high=up2)
        daily = _daily_df([daily_day1, daily_day2])
        sb = _stock_basic([("600100.SH", "主板", list_date)])
        out = compute_limit_derived(daily, sb, _empty_namechange(), days)
        assert len(out) == 1
        assert out.row(0, named=True)["trade_date"] == days[1]


class TestZabanDetection:
    def test_touched_limit_but_closed_below_is_zaban(self):
        days = _business_days(date(2024, 1, 2), 5)
        d = days[2]
        pre_close = 10.00
        up = _limit_price(pre_close, 0.10, +1)
        daily = _daily_df([_daily_row("600001.SH", d, pre_close, close=9.90, high=up)])
        sb = _stock_basic([("600001.SH", "主板", date(2015, 1, 1))])
        out = compute_limit_derived(daily, sb, _empty_namechange(), days)
        assert len(out) == 1
        row = out.row(0, named=True)
        assert row["status"] == "zaban"
        assert row["is_zaban"] is True
        assert row["is_limit_up"] is False


class TestConsecutiveLimitUpStreak:
    def test_streak_counts_consecutive_limit_up_days(self):
        days = _business_days(date(2024, 1, 2), 5)
        rows = []
        pre_close = 10.00
        for i in range(3):
            up = _limit_price(pre_close, 0.10, +1)
            rows.append(_daily_row("600001.SH", days[i], pre_close, up, high=up))
            pre_close = up  # 次日以昨日涨停价为基准再涨停(模拟连板)
        daily = _daily_df(rows)
        sb = _stock_basic([("600001.SH", "主板", date(2015, 1, 1))])
        out = compute_limit_derived(daily, sb, _empty_namechange(), days).sort("trade_date")
        assert out["consec_limit_up_days"].to_list() == [1, 2, 3]

    def test_streak_resets_after_non_limit_day(self):
        days = _business_days(date(2024, 1, 2), 5)
        pre_close = 10.00
        up1 = _limit_price(pre_close, 0.10, +1)
        row1 = _daily_row("600001.SH", days[0], pre_close, up1, high=up1)
        row2 = _daily_row("600001.SH", days[1], up1, up1 * 0.98)  # 次日不涨停(阴跌)
        pre_close3 = row2["close"]
        up3 = _limit_price(pre_close3, 0.10, +1)
        row3 = _daily_row("600001.SH", days[2], pre_close3, up3, high=up3)  # 再次涨停,重新计数
        daily = _daily_df([row1, row2, row3])
        sb = _stock_basic([("600001.SH", "主板", date(2015, 1, 1))])
        out = compute_limit_derived(daily, sb, _empty_namechange(), days).sort("trade_date")
        assert len(out) == 2  # row2 不是命中行,不落盘
        assert out["consec_limit_up_days"].to_list() == [1, 1]


def test_empty_daily_input_returns_empty_result():
    empty = pl.DataFrame(
        schema={
            "ts_code": pl.Utf8, "trade_date": pl.Date, "open": pl.Float64, "high": pl.Float64,
            "low": pl.Float64, "close": pl.Float64, "pre_close": pl.Float64,
        }
    )
    sb = _stock_basic([])
    out = compute_limit_derived(empty, sb, _empty_namechange(), [])
    assert len(out) == 0


# ======================================================================
#  标量镜像(plan 阶段3 §2.4):resolve_limit_pct / resolve_exempt_days /
#  compute_intraday_limit_prices。供盘中哨兵逐票用,规则与上面的向量化
#  `compute_limit_derived` 必须同源——本节除了独立算期望值外,额外对拍向量化
#  路径(`TestScalarMatchesVectorizedPath`),防止两条路径未来漂移。
# ======================================================================

from neckline.data.board import Board  # noqa: E402
from neckline.data.limit_derived import (  # noqa: E402
    compute_intraday_limit_prices,
    resolve_exempt_days,
    resolve_limit_pct,
)


class TestResolveLimitPct:
    def test_star_always_20pct(self):
        assert resolve_limit_pct(Board.STAR, is_st=False, trade_date=date(2020, 1, 1)) == pytest.approx(0.20)
        assert resolve_limit_pct(Board.STAR, is_st=True, trade_date=date(2026, 7, 20)) == pytest.approx(0.20)

    def test_bse_always_30pct(self):
        assert resolve_limit_pct(Board.BSE, is_st=False, trade_date=date(2020, 1, 1)) == pytest.approx(0.30)
        assert resolve_limit_pct(Board.BSE, is_st=True, trade_date=date(2026, 7, 20)) == pytest.approx(0.30)

    def test_gem_before_and_after_reform(self):
        before = GEM_REFORM_DATE - timedelta(days=1)
        assert resolve_limit_pct(Board.GEM, is_st=False, trade_date=before) == pytest.approx(0.10)
        assert resolve_limit_pct(Board.GEM, is_st=False, trade_date=GEM_REFORM_DATE) == pytest.approx(0.20)

    def test_gem_st_before_reform_is_5pct_after_is_20pct(self):
        before = GEM_REFORM_DATE - timedelta(days=1)
        assert resolve_limit_pct(Board.GEM, is_st=True, trade_date=before) == pytest.approx(0.05)
        # 注册制后创业板 ST 不降 5%,与普通股同 20%(见模块头注释)
        assert resolve_limit_pct(Board.GEM, is_st=True, trade_date=GEM_REFORM_DATE) == pytest.approx(0.20)

    def test_main_non_st_always_10pct(self):
        assert resolve_limit_pct(Board.MAIN, is_st=False, trade_date=date(2020, 1, 1)) == pytest.approx(0.10)
        assert resolve_limit_pct(Board.MAIN, is_st=False, trade_date=date(2026, 7, 20)) == pytest.approx(0.10)

    def test_main_st_before_and_after_reform(self):
        before = MAIN_ST_REFORM_DATE - timedelta(days=1)
        assert resolve_limit_pct(Board.MAIN, is_st=True, trade_date=before) == pytest.approx(0.05)
        assert resolve_limit_pct(Board.MAIN, is_st=True, trade_date=MAIN_ST_REFORM_DATE) == pytest.approx(0.10)


class TestResolveExemptDays:
    def test_star_is_5_regardless_of_list_date(self):
        assert resolve_exempt_days(Board.STAR, date(2015, 1, 1)) == 5

    def test_gem_5_if_listed_after_reform_else_1(self):
        assert resolve_exempt_days(Board.GEM, GEM_REFORM_DATE) == 5
        assert resolve_exempt_days(Board.GEM, GEM_REFORM_DATE - timedelta(days=1)) == 1

    def test_main_and_bse_always_1(self):
        assert resolve_exempt_days(Board.MAIN, date(2015, 1, 1)) == 1
        assert resolve_exempt_days(Board.BSE, date(2015, 1, 1)) == 1

    def test_missing_list_date_defaults_conservative_1(self):
        """上市日未知(如元数据缺失)→ 保守按 1 天处理,不长期误判为"仍在豁免期"。"""
        assert resolve_exempt_days(Board.GEM, None) == 1


class TestComputeIntradayLimitPrices:
    def test_matches_independent_decimal_calc(self):
        up, down = compute_intraday_limit_prices(10.0, Board.MAIN, is_st=False, trade_date=date(2024, 1, 2))
        assert up == pytest.approx(_limit_price(10.0, 0.10, +1))
        assert down == pytest.approx(_limit_price(10.0, 0.10, -1))

    def test_st_star_20pct(self):
        up, down = compute_intraday_limit_prices(50.0, Board.STAR, is_st=True, trade_date=date(2024, 1, 2))
        assert up == pytest.approx(_limit_price(50.0, 0.20, +1))
        assert down == pytest.approx(_limit_price(50.0, 0.20, -1))

    def test_zero_or_negative_pre_close_returns_none(self):
        assert compute_intraday_limit_prices(0.0, Board.MAIN, False, date(2024, 1, 2)) == (None, None)
        assert compute_intraday_limit_prices(-1.0, Board.MAIN, False, date(2024, 1, 2)) == (None, None)

    def test_rounding_matches_grid_of_prices(self):
        """网格核对(缩小版,呼应 0.4b 施工期 79200 组网格核对的传统):抽样一批
        价格,标量函数与独立 Decimal 实现必须逐分对齐,不允许浮点误判。"""
        for cents in range(100, 100 + 50):
            price = cents / 100.0
            for board, is_st, pct in [
                (Board.MAIN, False, 0.10),
                (Board.STAR, False, 0.20),
                (Board.BSE, False, 0.30),
                (Board.MAIN, True, 0.05),
            ]:
                up, down = compute_intraday_limit_prices(price, board, is_st, date(2024, 1, 2))
                assert up == pytest.approx(_limit_price(price, pct, +1)), (price, board, is_st)
                assert down == pytest.approx(_limit_price(price, pct, -1)), (price, board, is_st)


class TestScalarMatchesVectorizedPath:
    """标量路径与向量化 `compute_limit_derived` 对拍——同一 pre_close 算出的涨跌停价
    必须完全一致(两条路径共用同一组常量,不应漂移)。"""

    @pytest.mark.parametrize(
        "case_label,ts_code,market,board_enum,pct,trade_date",
        [
            ("主板", "600001.SH", "主板", Board.MAIN, 0.10, date(2024, 1, 2)),
            ("科创板", "688001.SH", "科创板", Board.STAR, 0.20, date(2024, 1, 2)),
            ("北交所", "920001.BJ", "北交所", Board.BSE, 0.30, date(2024, 1, 2)),
            ("创业板(注册制后)", "300001.SZ", "创业板", Board.GEM, 0.20, date(2024, 1, 2)),
            ("创业板(注册制前)", "300002.SZ", "创业板", Board.GEM, 0.10, date(2020, 1, 2)),
        ],
    )
    def test_scalar_agrees_with_vectorized(self, case_label, ts_code, market, board_enum, pct, trade_date):
        days = _business_days(trade_date - timedelta(days=10), 20)
        d = next(x for x in days if x >= trade_date)
        pre_close = 10.00
        up_vec_expected = _limit_price(pre_close, pct, +1)
        daily = _daily_df([_daily_row(ts_code, d, pre_close, up_vec_expected, high=up_vec_expected)])
        # list_date 远早于 GEM 改革日,确保本用例只测「板块正常幅度」分支,不触碰新股豁免窗口。
        sb = _stock_basic([(ts_code, market, date(2015, 1, 1))])
        vec_out = compute_limit_derived(daily, sb, _empty_namechange(), days)
        assert len(vec_out) == 1, case_label
        vec_limit_up = vec_out.row(0, named=True)["limit_up_price"]

        scalar_up, _ = compute_intraday_limit_prices(pre_close, board_enum, is_st=False, trade_date=d)
        assert scalar_up == pytest.approx(vec_limit_up), case_label
