"""信息卡与考卷同构单测(plan §五 v1.4-④ 验收)。覆盖:

  · 纯函数(温和带边界 / 归一100 / 快照数值 / RS线 / 行业分歧线 / 消息面域判定 /
    龙虎榜回看)——手搓最小数据,精确断言(同 `test_industry_strength.py` 对拍风格);
  · `build_info_card` 端到端(隔离库真 parquet):RS 线基准=SSE_INDEX、行业分歧线
    标注"成员中位数合成"、④-C 四路抽源各自独立缺省(不连带其余各路)、红黄牌
    "不重算"(原样 decorate 传入的 k4_flags)、温和带真实命中;
  · `attach_info_card_summaries`:零额外 parquet 读取(直接吃 `Candidate.raw`/
    `intel_rank`),旧候选(raw/intel_rank 皆空)优雅降级不崩;
  · `InfoCardSummary` 不含 60 日序列(payload 键集断言)。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Optional

import polars as pl
import pytest

from neckline.report import info_card as ic
from neckline.report.candidates import Candidate
from tests.conftest import (
    business_days,
    insert_stock_basic,
    insert_trade_cal,
    seed_industry_strength,
    write_daily_fixture,
)

pytestmark = pytest.mark.usefixtures("isolated_env")


# ======================================================================
#  Tier 1:纯函数 / 手搓最小数据(不落盘)
# ======================================================================

def test_is_mild_band_boundaries():
    assert ic.is_mild_band(None) is False
    assert ic.is_mild_band(0.019) is False
    assert ic.is_mild_band(0.02) is True     # 下边界含
    assert ic.is_mild_band(0.025) is True
    assert ic.is_mild_band(0.03) is True     # 上边界含
    assert ic.is_mild_band(0.031) is False


def test_normalize_to_100_basic():
    dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]
    pts = ic._normalize_to_100(dates, [10.0, 20.0, 5.0])
    assert [p.value for p in pts] == [100.0, 200.0, 50.0]
    assert pts[0].trade_date == "20240101"


def test_normalize_to_100_empty_input():
    assert ic._normalize_to_100([], []) == []


def test_normalize_to_100_zero_base_returns_empty():
    """基准(首个非 None 值)为 0 → 除零,返回空(调用方按"该线不可用"处理)。"""
    dates = [date(2024, 1, 1), date(2024, 1, 2)]
    assert ic._normalize_to_100(dates, [0.0, 5.0]) == []


def test_normalize_to_100_skips_leading_none_for_base():
    dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]
    pts = ic._normalize_to_100(dates, [None, 10.0, 15.0])
    # 首个非 None(10.0)作基准;None 那一天直接不出现在结果里。
    assert [p.trade_date for p in pts] == ["20240102", "20240103"]
    assert pts[0].value == 100.0
    assert pts[1].value == 150.0


def test_build_snapshot_with_row_matches_row_fields_bit_for_bit():
    """快照七项数值与面板逐位一致(④ 验收原话)。"""
    row = {
        "close": 11.0, "ma250": 10.0, "vol_ratio_5": 1.234, "turnover_rate": 5.67,
        "dist_from_high_20d": -0.05, "consec_limit_up_days": 2,
    }
    snap = ic._build_snapshot(row, industry_rank=3, industry_persist_days=1)
    assert snap.vol_ratio5 == pytest.approx(1.234)
    assert snap.turnover_rate == pytest.approx(5.67)
    assert snap.industry_rank == 3
    assert snap.industry_persist_days == 1
    assert snap.above_ma250 is True   # 11.0 > 10.0
    assert snap.dist_from_ma250_pct == pytest.approx(0.1)   # 11/10-1
    assert snap.dist_from_high20d_pct == pytest.approx(-0.05)
    assert snap.consec_limit_up_days == 2


def test_build_snapshot_below_ma250():
    row = {"close": 9.0, "ma250": 10.0}
    snap = ic._build_snapshot(row, None, 0)
    assert snap.above_ma250 is False
    assert snap.dist_from_ma250_pct == pytest.approx(-0.1)


def test_build_snapshot_ma250_none_or_zero_treated_as_unavailable():
    assert ic._build_snapshot({"close": 9.0, "ma250": None}, None, 0).above_ma250 is None
    assert ic._build_snapshot({"close": 9.0, "ma250": 0.0}, None, 0).above_ma250 is None


def test_build_snapshot_no_row_still_carries_industry_fields():
    """当日无 EOD 行(row=None)→ 价量字段全 None/0,但行业口径(独立于当日 K 线)原样带。"""
    snap = ic._build_snapshot(None, industry_rank=5, industry_persist_days=2)
    assert snap.vol_ratio5 is None and snap.turnover_rate is None
    assert snap.above_ma250 is None and snap.dist_from_ma250_pct is None
    assert snap.consec_limit_up_days == 0
    assert snap.industry_rank == 5
    assert snap.industry_persist_days == 2


def test_build_rs_line_hand_computed():
    window_panel = pl.DataFrame({
        "trade_date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
        "close": [10.0, 10.0, 20.0],
    })
    idx_states = pl.DataFrame({
        "trade_date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
        "sse_close": [100.0, 100.0, 100.0],
    })
    available, line, reason = ic._build_rs_line(window_panel, idx_states)
    assert available is True and reason is None
    assert [round(p.value, 4) for p in line] == [100.0, 100.0, 200.0]


def test_build_rs_line_unavailable_when_stock_panel_empty():
    available, line, reason = ic._build_rs_line(pl.DataFrame(), pl.DataFrame({"trade_date": [], "sse_close": []}))
    assert available is False and line == [] and "K线数据" in reason


def test_build_rs_line_unavailable_when_index_missing():
    window_panel = pl.DataFrame({"trade_date": [date(2024, 1, 1)], "close": [10.0]})
    available, line, reason = ic._build_rs_line(window_panel, pl.DataFrame())
    assert available is False and "大盘指数" in reason


def test_build_rs_line_unavailable_when_no_overlap():
    window_panel = pl.DataFrame({"trade_date": [date(2024, 1, 1)], "close": [10.0]})
    idx_states = pl.DataFrame({"trade_date": [date(2024, 5, 1)], "sse_close": [100.0]})
    available, line, reason = ic._build_rs_line(window_panel, idx_states)
    assert available is False and "重叠交易日" in reason


def test_build_industry_divergence_unavailable_no_industry():
    available, line, reason = ic._build_industry_divergence(
        "600001.SH", "", None, pl.DataFrame(), date(2024, 1, 1), date(2024, 1, 2), None,
    )
    assert available is False and line == [] and "无行业分类" in reason


def test_build_industry_divergence_unavailable_not_qualifying():
    """② 判定"当日成员<5,不参与排名"(industry_rank=None)→ 分歧线如实标不可得,
    不调用现算参考实现硬凑(样本不足时不该假装能合成出线)。

    v1.4-⑩-E:`industry_ready=True` 表示行业强度表**当日有数据**(看了),此时
    `industry_rank=None` 才是真正的「样本不足」;表整个没数据(没看)是另一档理由,
    见 `test_build_industry_divergence_unavailable_when_table_not_ready`。"""
    available, line, reason = ic._build_industry_divergence(
        "600001.SH", "小众行业", None, pl.DataFrame({"trade_date": [date(2024, 1, 1)], "close": [10.0]}),
        date(2024, 1, 1), date(2024, 1, 2), None, industry_ready=True,
    )
    assert available is False and line == []
    assert "样本不足" in reason and "小众行业" in reason


def test_build_industry_divergence_unavailable_when_table_not_ready(isolated_env):
    """v1.4-⑩-E 新增第三档理由:行业强度表当日**整段缺失**(「没看」)→ 文案必须与
    「行业样本不足」(「看了,不够格」)**分开**,不许混成一句(§3.8)。"""
    available, line, reason = ic._build_industry_divergence(
        "600001.SH", "某行业", None, pl.DataFrame({"trade_date": [date(2024, 1, 1)], "close": [10.0]}),
        date(2024, 1, 1), date(2024, 1, 2), isolated_env.db_path, industry_ready=False,
    )
    assert available is False and line == []
    assert "行业强度数据未就绪" in reason
    assert "样本不足" not in reason


def test_top_list_summary_covered_and_hits():
    d0, d1, d2 = date(2024, 1, 3), date(2024, 1, 2), date(2024, 1, 1)
    lookback = [
        (d2, {"600001.SH": {"reason": "日涨幅偏离值达7%", "net_amount": 100.0, "net_rate": 2.0}}),
        (d1, None),   # 该天本地没有落盘,真查不到(不是"查过确认没上榜")
        (d0, {"600002.SH": {"reason": "x", "net_amount": 1.0, "net_rate": 1.0}}),  # T0 未上榜
    ]
    summary = ic._top_list_summary_for_code("600001.SH", lookback)
    assert summary.on_list_today is False       # T0(最后一项)没有 600001.SH
    assert summary.lookback_days_covered == 2   # 只有 d2/d0 两天本地有数据
    assert summary.lookback_hit_days == 1        # 只在 d2 命中过


def test_top_list_summary_on_list_today():
    lookback = [(date(2024, 1, 1), {"600001.SH": {"reason": "涨停", "net_amount": 500.0, "net_rate": 3.3}})]
    summary = ic._top_list_summary_for_code("600001.SH", lookback)
    assert summary.on_list_today is True
    assert summary.reason == "涨停"
    assert summary.net_amount == pytest.approx(500.0)
    assert summary.lookback_days_covered == 1
    assert summary.lookback_hit_days == 1


def test_news_summary_not_in_domain():
    s = ic._news_summary_for_code("600001.SH", date(2024, 1, 1), set(), items=[])
    assert s.scanned is False
    assert s.items == []
    assert s.unavailable_reason == ic.NEWS_DOMAIN_UNAVAILABLE_REASON


def test_news_summary_in_domain_filters_by_code():
    items = [
        {"ts_code": "600001.SH", "category": "REDUCTION", "summary": "张三减持", "source": "tushare_holdertrade"},
        {"ts_code": "600002.SH", "category": "BLOWUP", "summary": "别的票", "source": "llm_glm"},
    ]
    s = ic._news_summary_for_code("600001.SH", date(2024, 1, 1), {"600001.SH", "600002.SH"}, items=items)
    assert s.scanned is True
    assert s.unavailable_reason is None
    assert len(s.items) == 1
    assert s.items[0].category == "REDUCTION" and s.items[0].summary == "张三减持"


def test_news_summary_in_domain_but_zero_hits():
    s = ic._news_summary_for_code("600001.SH", date(2024, 1, 1), {"600001.SH"}, items=[])
    assert s.scanned is True and s.items == [] and s.unavailable_reason is None


# ======================================================================
#  Tier 2:端到端(隔离库真 parquet,`build_info_card`)
# ======================================================================

_N_DAYS = 70
_INDUSTRY = "电气设备"
_MEMBER_DAILY_RET = 0.002   # 600002~600005.SH 每日固定涨幅(驱动行业中位数,与目标票 600001.SH 区分开)


def _seed(env) -> List[date]:
    dates = business_days(date(2024, 1, 2), _N_DAYS)
    insert_trade_cal(env, dates)

    prev_close: dict = {}

    def _row(code, close, turnover=5.0, vol=100000.0):
        pre = prev_close.get(code, close)
        prev_close[code] = close
        daily_row = {"ts_code": code, "open": close, "high": close, "low": close, "close": close,
                     "pre_close": pre, "vol": vol, "amount": close * vol}
        adj_row = {"ts_code": code, "adj_factor": 1.0}
        basic_row = {"ts_code": code, "turnover_rate": turnover, "volume_ratio": 1.0,
                     "circ_mv": 1_000_000.0, "total_mv": 1_000_000.0, "free_share": 100_000.0}
        return daily_row, adj_row, basic_row

    index_prev_close = {}

    def _index_row(code, close):
        pre = index_prev_close.get(code, close)
        index_prev_close[code] = close
        return {"ts_code": code, "open": close, "high": close, "low": close, "close": close,
                "pre_close": pre, "vol": 0.0, "amount": 0.0}

    for i, d in enumerate(dates):
        daily, adj, basic = [], [], []
        # 600001.SH:目标票,恒 10.0,末日跳 10.25(ret_1d=+2.5%,落温和带 [2%,3%])。
        close_600001 = 10.25 if i == _N_DAYS - 1 else 10.0
        # 600002~600005.SH:同行业(电气设备),每日固定 +0.2%,驱动行业中位数随时间变化
        # (与 600001.SH 区分开,证明行业分歧线与 RS 线是两条独立计算的线,不是同一份拷贝)。
        for code in ["600001.SH"]:
            r = _row(code, close_600001)
            daily.append(r[0]); adj.append(r[1]); basic.append(r[2])
        for code in ["600002.SH", "600003.SH", "600004.SH", "600005.SH"]:
            close = 10.0 * (1 + _MEMBER_DAILY_RET) ** i
            r = _row(code, close)
            daily.append(r[0]); adj.append(r[1]); basic.append(r[2])
        # 700001.SZ:「小众行业」独苗(仅它自己,成员数<5,行业分歧线应标"样本不足")。
        r = _row("700001.SZ", 10.0)
        daily.append(r[0]); adj.append(r[1]); basic.append(r[2])
        # 700002.SZ:无行业(stock_basic.industry 缺失,分歧线应标"无行业分类")。
        r = _row("700002.SZ", 10.0)
        daily.append(r[0]); adj.append(r[1]); basic.append(r[2])

        write_daily_fixture(env, "daily", d, daily)
        write_daily_fixture(env, "index_daily", d, [_index_row("000001.SH", 100.0)])
        write_daily_fixture(env, "adj_factor", d, adj)
        write_daily_fixture(env, "daily_basic", d, basic)

    insert_stock_basic(env, [
        {"ts_code": "600001.SH", "name": "示例甲", "industry": _INDUSTRY, "list_date": dates[0] - timedelta(days=800)},
        {"ts_code": "600002.SH", "name": "示例乙", "industry": _INDUSTRY, "list_date": dates[0] - timedelta(days=800)},
        {"ts_code": "600003.SH", "name": "示例丙", "industry": _INDUSTRY, "list_date": dates[0] - timedelta(days=800)},
        {"ts_code": "600004.SH", "name": "示例丁", "industry": _INDUSTRY, "list_date": dates[0] - timedelta(days=800)},
        {"ts_code": "600005.SH", "name": "示例戊", "industry": _INDUSTRY, "list_date": dates[0] - timedelta(days=800)},
        {"ts_code": "700001.SZ", "name": "示例己", "industry": "小众行业", "list_date": dates[0] - timedelta(days=800)},
        {"ts_code": "700002.SZ", "name": "示例庚", "industry": None, "list_date": dates[0] - timedelta(days=800)},
    ] + [{"ts_code": f"9{j:05d}.SZ", "name": f"背景{j}", "industry": "背景填充行业"} for j in range(50)])
    # v1.4-⑩(§七 P0-23):信息卡在线路径**只读** `industry_strength_daily` 预计算表,
    # 故夹具要把 16:05 日更那一步补上(走生产同一条写入路径,不是测试专用第二套写法)。
    seed_industry_strength(env, dates)
    return dates


def test_build_info_card_kline_and_snapshot(isolated_env):
    dates = _seed(isolated_env)
    trade_date = dates[-1]
    card = ic.build_info_card(
        trade_date, "600001.SH", k4_flags=[], news_domain_codes=set(), news_items=[],
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
    )
    assert card.kline_available is True
    assert card.kline_unavailable_reason is None
    assert len(card.kline) == ic.DISPLAY_WINDOW_TRADING_DAYS
    assert card.kline[-1].trade_date == trade_date.strftime("%Y%m%d")
    assert card.kline[-1].close == pytest.approx(10.25)
    # 只有 70 天历史(<250 交易日),ma250 全程 null——不是"均线为0"。
    assert all(bar.ma250 is None for bar in card.kline)
    assert card.snapshot.turnover_rate == pytest.approx(5.0)
    assert card.mild_band is True   # 末日 +2.5% 落温和带


def test_build_info_card_rs_line_uses_sse_index_constant_and_matches_manual_calc(isolated_env):
    dates = _seed(isolated_env)
    trade_date = dates[-1]
    card = ic.build_info_card(
        trade_date, "600001.SH", k4_flags=[], news_domain_codes=set(), news_items=[],
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
    )
    assert card.rs_available is True
    assert card.rs_benchmark == "000001.SH"   # 断言复用 SSE_INDEX 常量,未新起一个基准代码
    assert card.rs_line[0].value == pytest.approx(100.0)
    # 大盘指数全程恒 100、600001.SH 只有末日从 10.0 跳到 10.25 → RS 线末值 = 102.5。
    assert card.rs_line[-1].value == pytest.approx(102.5, rel=1e-4)


def test_build_info_card_industry_divergence_available_and_diverges_from_rs_line(isolated_env):
    """行业分歧线标注"成员中位数合成";与 RS 线数值不同(证明两条线基准独立,不是
    同一份拷贝)——手算行业指数(每日中位数固定 0.2%,由 600002~600005.SH 驱动)。"""
    dates = _seed(isolated_env)
    trade_date = dates[-1]
    card = ic.build_info_card(
        trade_date, "600001.SH", k4_flags=[], news_domain_codes=set(), news_items=[],
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
    )
    assert card.industry_divergence_available is True
    assert card.industry == "电气设备"
    assert card.industry_divergence_note == "行业线=行业成员中位数合成,非申万官方指数"
    assert card.industry_divergence_line[0].value == pytest.approx(100.0)

    window_len = len(card.rs_line)
    expected_industry_index_last = 100.0 * (1 + _MEMBER_DAILY_RET) ** (window_len - 1)
    expected_last = (10.25 / expected_industry_index_last) / (10.0 / 100.0) * 100.0
    assert card.industry_divergence_line[-1].value == pytest.approx(expected_last, rel=1e-4)
    # 与 RS 线末值(102.5,基准恒定不变)明显不同——证明分歧线确实用了独立、随时间
    # 变化的行业基准,不是 RS 线的拷贝。
    assert card.industry_divergence_line[-1].value != pytest.approx(card.rs_line[-1].value, rel=1e-3)


def test_build_info_card_industry_divergence_unavailable_not_qualifying_does_not_affect_other_sections(isolated_env):
    """④-C 抽源①:行业样本不足(700001.SZ 独苗,成员<5)→ 分歧线如实缺省,
    **不连带**K线/RS线/其余各路"看起来也不可用"(隔离性)。"""
    dates = _seed(isolated_env)
    trade_date = dates[-1]
    card = ic.build_info_card(
        trade_date, "700001.SZ", k4_flags=[], news_domain_codes=set(), news_items=[],
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
    )
    assert card.industry_divergence_available is False
    assert "样本不足" in card.industry_divergence_unavailable_reason
    assert card.industry == "小众行业"
    # 其余各路不受影响:
    assert card.kline_available is True
    assert card.rs_available is True


def test_build_info_card_industry_divergence_unavailable_no_industry(isolated_env):
    dates = _seed(isolated_env)
    trade_date = dates[-1]
    card = ic.build_info_card(
        trade_date, "700002.SZ", k4_flags=[], news_domain_codes=set(), news_items=[],
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
    )
    assert card.industry_divergence_available is False
    assert "无行业分类" in card.industry_divergence_unavailable_reason
    assert card.industry == ""


def test_build_info_card_market_context_unavailable_when_no_index_data(isolated_env):
    """④-C 抽源②:抽掉大盘数据(不落 index_daily)→ 市场语境 + RS 线均如实缺省,
    **不连带**其余各路。"""
    dates = business_days(date(2024, 1, 2), _N_DAYS)
    insert_trade_cal(isolated_env, dates)
    for i, d in enumerate(dates):
        write_daily_fixture(isolated_env, "daily", d, [
            {"ts_code": "600001.SH", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "pre_close": 10.0, "vol": 100000.0, "amount": 1000000.0},
        ])
        write_daily_fixture(isolated_env, "adj_factor", d, [{"ts_code": "600001.SH", "adj_factor": 1.0}])
    insert_stock_basic(isolated_env, [{"ts_code": "600001.SH", "name": "示例甲", "industry": None}])

    trade_date = dates[-1]
    card = ic.build_info_card(
        trade_date, "600001.SH", k4_flags=[], news_domain_codes=set(), news_items=[],
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
    )
    assert card.rs_available is False
    assert "大盘指数" in card.rs_unavailable_reason
    assert card.market.index_line == []
    assert card.market.above_ma20 is None
    # K 线不受影响(个股自己的数据仍在):
    assert card.kline_available is True


def test_build_info_card_top_list_lookback_partial_coverage(isolated_env):
    """④-C 抽源③:近 5 个交易日只补了部分本地文件(不为凑齐而回补历史)→
    `lookbackDaysCovered` 如实反映"查到几天",不冒充全覆盖。"""
    dates = _seed(isolated_env)
    trade_date = dates[-1]
    lookback_days = ic._recent_trading_days(trade_date, ic.TOP_LIST_LOOKBACK_TRADING_DAYS)
    assert len(lookback_days) == 5
    # 只给 T0 与 T-1 落龙虎榜日文件,T-2/T-3/T-4 缺失(本地没有,不回补)。
    write_daily_fixture(isolated_env, "top_list", lookback_days[-1], [
        {"ts_code": "600001.SH", "name": "示例甲", "close": 10.25, "pct_change": 2.5,
         "turnover_rate": 5.0, "l_buy": 100.0, "l_sell": 50.0, "net_amount": 50.0,
         "net_rate": 1.0, "reason": "日涨幅偏离值达7%"},
    ])
    write_daily_fixture(isolated_env, "top_list", lookback_days[-2], [
        {"ts_code": "600002.SH", "name": "示例乙", "close": 10.0, "pct_change": 1.0,
         "turnover_rate": 4.0, "l_buy": 1.0, "l_sell": 1.0, "net_amount": 1.0,
         "net_rate": 1.0, "reason": "换手率达20%"},
    ])
    card = ic.build_info_card(
        trade_date, "600001.SH", k4_flags=[], news_domain_codes=set(), news_items=[],
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
    )
    assert card.top_list.on_list_today is True
    assert card.top_list.reason == "日涨幅偏离值达7%"
    assert card.top_list.lookback_days_covered == 2   # 只查到 T0/T-1 两天
    assert card.top_list.lookback_hit_days == 1        # 只在 T0 命中(600001.SH 不在 T-1 榜上)


def test_build_info_card_news_unavailable_when_not_in_domain(isolated_env):
    """④-C 抽源④:候选不在消息面扫描域(仅持仓+自选)→ 如实缺省,不冒充"扫了没有"。"""
    dates = _seed(isolated_env)
    trade_date = dates[-1]
    card = ic.build_info_card(
        trade_date, "600001.SH", k4_flags=[], news_domain_codes=set(), news_items=[],
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
    )
    assert card.news.scanned is False
    assert card.news.unavailable_reason == ic.NEWS_DOMAIN_UNAVAILABLE_REASON
    assert card.news.items == []
    # 其余各路不受影响:
    assert card.kline_available is True and card.rs_available is True


def test_build_info_card_news_available_when_in_domain(isolated_env):
    dates = _seed(isolated_env)
    trade_date = dates[-1]
    items = [{"ts_code": "600001.SH", "category": "REDUCTION", "summary": "股东减持 5 万股", "source": "tushare_holdertrade"}]
    card = ic.build_info_card(
        trade_date, "600001.SH", k4_flags=[], news_domain_codes={"600001.SH"}, news_items=items,
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
    )
    assert card.news.scanned is True
    assert card.news.unavailable_reason is None
    assert len(card.news.items) == 1 and card.news.items[0].summary == "股东减持 5 万股"


def test_build_info_card_k4_flags_decorated_without_recomputation(isolated_env):
    """红黄牌"复用③已算好的 k4_flags...不重算"——不给任何真实 K4 判据,只给一份
    人为构造的命中码列表,应原样 decorate 成 label/level/section/evidenceStrength,
    与"这个隔离库里的 600001.SH 实际会不会真的命中这两条"完全无关。"""
    dates = _seed(isolated_env)
    trade_date = dates[-1]
    card = ic.build_info_card(
        trade_date, "600001.SH",
        k4_flags=["A1_turnover_gt_10", "B2_dual_golden_cross", "不存在的码"],
        news_domain_codes=set(), news_items=[],
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
    )
    by_code = {f.code: f for f in card.k4_flags}
    assert set(by_code) == {"A1_turnover_gt_10", "B2_dual_golden_cross"}   # 未知码静默跳过
    assert by_code["A1_turnover_gt_10"].level == "strong"
    assert by_code["A1_turnover_gt_10"].section == "avoid_flag"   # K4 行未落库(隔离测试库)→ 缺省 section
    assert by_code["A1_turnover_gt_10"].evidence_strength == "price_volume"
    assert by_code["A1_turnover_gt_10"].evidence   # 走 _FALLBACK_EVIDENCE 兜底,非空


def test_build_info_card_code_with_no_eod_data_degrades_everything_gracefully(isolated_env):
    """当日无 EOD 行(比如查了个压根没铺过数据的代码)—— K 线/RS线/快照如实缺省,不崩。"""
    dates = _seed(isolated_env)
    trade_date = dates[-1]
    card = ic.build_info_card(
        trade_date, "999999.SH", k4_flags=[], news_domain_codes=set(), news_items=[],
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
    )
    assert card.kline_available is False
    assert card.rs_available is False
    assert card.snapshot.turnover_rate is None


# ======================================================================
#  attach_info_card_summaries(pipeline 批量摘要路径)
# ======================================================================

def _fake_candidate(ts_code: str, *, raw: Optional[dict] = None, intel_rank: Optional[dict] = None) -> Candidate:
    return Candidate(
        ts_code=ts_code, name="示例", close=10.0, score=80.0, rank=1, board="MAIN",
        pattern_tags=[], hot_sectors=[], sector_names=[],
        entry_plan="", stop_loss="", target="", invalidation_text="", invalidation_spec={},
        intel_rank=intel_rank or {}, raw=raw or {},
    )


def test_attach_info_card_summaries_reuses_raw_zero_extra_parquet_reads(isolated_env, monkeypatch):
    """`attach_info_card_summaries` 走候选生成时已装配好的 `Candidate.raw` +
    `intel_rank`,**零额外 parquet 读取**——monkeypatch `get_stock_history` 断言
    从未被调用,证明批量摘要路径没有偷偷重新拉一遍单票历史。"""
    def _boom(*args, **kwargs):
        raise AssertionError("attach_info_card_summaries 不应读取单票历史(应直接吃 candidate.raw)")

    monkeypatch.setattr(ic, "get_stock_history", _boom)

    cands = [_fake_candidate(
        "600001.SH",
        raw={"close": 10.25, "ma250": None, "vol_ratio_5": 1.1, "turnover_rate": 5.0,
             "dist_from_high_20d": -0.02, "consec_limit_up_days": 0, "ret_1d": 0.025},
        intel_rank={"industryRank": 3, "industryPersistDays": 1},
    )]
    ic.attach_info_card_summaries(
        cands, date(2024, 1, 1),
        news_items=[{"ts_code": "600001.SH", "category": "REDUCTION", "summary": "x", "source": "tushare_holdertrade"}],
        news_domain_codes={"600001.SH"},
        top_list={"600001.SH": {"reason": "涨停", "net_amount": 10.0, "net_rate": 1.0}},
    )
    summary = cands[0].info_card_summary
    assert summary["snapshot"]["industryRank"] == 3
    assert summary["snapshot"]["turnoverRate"] == pytest.approx(5.0)
    assert summary["mildBand"] is True
    assert summary["news"]["scanned"] is True and summary["news"]["items"][0]["summary"] == "x"
    assert summary["topList"]["onListToday"] is True


def test_attach_info_card_summaries_old_candidate_without_raw_degrades_gracefully():
    """`raw`/`intel_rank` 皆空(理论上不该发生,但旧路径/异常构造须防御)→ 摘要
    字段全部 None/0/False,不抛异常。"""
    cands = [_fake_candidate("600002.SH")]
    ic.attach_info_card_summaries(cands, date(2024, 1, 1), news_items=[], news_domain_codes=set(), top_list={})
    summary = cands[0].info_card_summary
    assert summary["snapshot"]["industryRank"] is None
    assert summary["mildBand"] is False
    assert summary["news"]["scanned"] is False
    assert summary["topList"]["onListToday"] is False


def test_info_card_summary_payload_excludes_60day_series():
    """`CandidateOut.infoCard` 摘要位不含 60 日序列(payload 键集断言,④ 验收原话)。"""
    summary = ic.InfoCardSummary().to_public_dict()
    assert set(summary.keys()) == {"snapshot", "mildBand", "news", "topList"}
    for forbidden in ("kline", "rsLine", "industryDivergenceLine", "market", "k4Flags"):
        assert forbidden not in summary
