"""行业强度单一源单测(plan §五 v1.4-②-A 验收)。

覆盖:①核心计算与 `research/k4p_h6_theme.py::industry_persistence` **逐位对拍**
(排名/强度日集合/持续天数三项,含 quantile 敏感性 ±1 格参数化);②成员数
`< _MIN_MEMBERS` 的行业当日不参与排名;③无前视(T 日结果不受 T 之后数据影响);
④公开 I/O 入口 `compute_industry_strength`(全市场 daily + `stock_basic.industry`
接线正确性);⑤`stock_persist_days`/`stock_industry_rank` 查无行业/行业不达标的
缺省行为(A2/B3 判据 + ③ 排序键的消费方式)。

**①组直接 `import research.k4p_h6_theme`**——这是 plan §五 v1.4-②-A 的硬要求
("单测硬要求:与 research/k4p_h6_theme.py...逐位对拍"),与 `conftest.py` "tests/
不应依赖 research/"的一般建议刻意例外:`industry_persistence` 是纯函数(只接收
`panel` 参数,不读 DB/parquet,import 与调用均无副作用,已验证),且**正是本模块要
对齐的参照实现本身**——不直接比对就无法机器验证"口径逐条对齐"这条硬要求,比对
本身就是这条要求存在的意义,不属于 conftest.py 那条注释想防的"一般测试基础设施
耦合到易变研究脚本"。
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import polars as pl
import pytest

from neckline.report import industry_strength as ist
from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, write_daily_fixture


# ————————————————————————————————————————————————————————————————
# ① 核心计算逐位对拍 research/k4p_h6_theme.py::industry_persistence
# ————————————————————————————————————————————————————————————————

def _synthetic_panel(seed: int = 42, n_industries: int = 6, n_members: int = 8, n_days: int = 20) -> pl.DataFrame:
    """构造一份带"部分行业轮流持续强势"模式的合成 (ts_code, trade_date, industry,
    ret_1d) 面板(纯 polars 对象,不落盘)——喂两套实现(research 参照 + 本模块)做
    逐位对拍。"""
    rng = random.Random(seed)
    industries = [f"IND{i}" for i in range(n_industries)]
    dates = [date(2024, 1, 1) + timedelta(days=d) for d in range(n_days)]
    rows = []
    for d_i, d in enumerate(dates):
        for ind_i, ind in enumerate(industries):
            center = (0.02 if (d_i // 3) % n_industries == ind_i else 0.0) + rng.gauss(0, 0.01)
            for m in range(n_members):
                rows.append({
                    "ts_code": f"{ind}_{m}", "trade_date": d, "industry": ind,
                    "ret_1d": center + rng.gauss(0, 0.005),
                })
    return pl.DataFrame(rows)


def test_core_table_matches_research_bit_for_bit():
    from research.k4p_h6_theme import industry_persistence

    panel = _synthetic_panel()
    mine = ist._compute_daily_table(panel, ist._STRENGTH_QUANTILE)
    research = industry_persistence(panel, ist._STRENGTH_QUANTILE)

    # research 只返回强度日行;逐行核对 mine 里 is_strength_day=True 的子集与 research 完全一致。
    mine_str = (
        mine.filter(pl.col("is_strength_day"))
        .select(["trade_date", "industry", "industry_persist_days"])
        .sort(["trade_date", "industry"])
    )
    research_sorted = research.select(["trade_date", "industry", "persist"]).sort(["trade_date", "industry"])
    assert mine_str.height == research_sorted.height > 0    # 合成数据确实产生了强度日,不是空对空
    joined = mine_str.join(research_sorted, on=["trade_date", "industry"], how="inner")
    assert joined.height == mine_str.height                 # 两边 (trade_date, industry) 集合完全对上
    assert (joined["industry_persist_days"] == joined["persist"]).all()   # 持续天数逐位相同


def test_non_strength_day_persist_is_zero():
    """非强度日(含达标行业当天未过阈值)持续天数显式为 0,这是本模块比 research 脚本
    多产出的部分(research 只返回强度日行)——见模块 docstring。"""
    panel = _synthetic_panel()
    mine = ist._compute_daily_table(panel, ist._STRENGTH_QUANTILE)
    non_str = mine.filter(~pl.col("is_strength_day"))
    assert non_str.height > 0
    assert (non_str["industry_persist_days"] == 0).all()


def test_rank_forms_1_to_n_each_day():
    """每天的 industry_rank 覆盖当天全部达标行业,形成无缺口的 1..N。"""
    panel = _synthetic_panel()
    mine = ist._compute_daily_table(panel, ist._STRENGTH_QUANTILE)
    for d in mine["trade_date"].unique().to_list():
        day = mine.filter(pl.col("trade_date") == d)
        assert sorted(day["industry_rank"].to_list()) == list(range(1, day.height + 1))


@pytest.mark.parametrize("q", [0.85, 0.80, 0.70])
def test_quantile_sensitivity_parametrizable(q):
    """阈值敏感性 ±1 格(0.85/0.80/0.70)只作参数化能力(plan 原文,默认仍 0.80);
    每一格都应与 research 参照逐位对拍通过。"""
    from research.k4p_h6_theme import industry_persistence

    panel = _synthetic_panel()
    mine = ist._compute_daily_table(panel, q)
    research = industry_persistence(panel, q)
    mine_str = (
        mine.filter(pl.col("is_strength_day"))
        .select(["trade_date", "industry", "industry_persist_days"])
        .sort(["trade_date", "industry"])
    )
    research_sorted = research.select(["trade_date", "industry", "persist"]).sort(["trade_date", "industry"])
    assert mine_str.height == research_sorted.height > 0
    joined = mine_str.join(research_sorted, on=["trade_date", "industry"], how="inner")
    assert joined.height == mine_str.height
    assert (joined["industry_persist_days"] == joined["persist"]).all()


def test_min_members_excludes_thin_industries():
    """成员数 < `_MIN_MEMBERS`(=5)的行业当日不参与排名——整天不出现在返回表里
    (不是出现但 rank=None,见模块 docstring 的"缺省即弱"设计)。"""
    d = date(2024, 1, 1)
    rows = (
        [{"ts_code": f"BIG_{i}", "trade_date": d, "industry": "大行业", "ret_1d": 0.01 * i} for i in range(6)]
        + [{"ts_code": f"SMALL_{i}", "trade_date": d, "industry": "小行业", "ret_1d": 0.02} for i in range(3)]
    )
    panel = pl.DataFrame(rows)
    mine = ist._compute_daily_table(panel, 0.8)
    assert set(mine["industry"].to_list()) == {"大行业"}


# ————————————————————————————————————————————————————————————————
# ② 无前视(§3.8)
# ————————————————————————————————————————————————————————————————

def test_no_forward_looking():
    """T 日的中位数/排名/强度日/持续天数不因 T 之后追加数据而改变。"""
    panel = _synthetic_panel(n_days=15)
    d10 = panel["trade_date"].unique().sort().to_list()[9]
    short = panel.filter(pl.col("trade_date") <= d10)   # 只喂到 T=d10
    full = panel                                        # 含 T 之后 5 天

    cols = ["industry", "median_ret", "member_count", "industry_rank", "is_strength_day", "industry_persist_days"]
    a = ist._compute_daily_table(short, 0.8).filter(pl.col("trade_date") == d10).sort("industry").select(cols)
    b = ist._compute_daily_table(full, 0.8).filter(pl.col("trade_date") == d10).sort("industry").select(cols)
    assert a.height > 0
    assert a.equals(b)


# ————————————————————————————————————————————————————————————————
# ③ 公开 I/O 入口:compute_industry_strength(全市场 daily + stock_basic.industry)
# ————————————————————————————————————————————————————————————————

def _seed_industry_market(env, dates, industries: dict) -> None:
    """`industries`: {行业名: [(code, ret_1d 序列(长度=len(dates),首日值被忽略——
    首日 pre_close=close 恒 ret_1d=0))...]}。按序列递推收盘价,写 daily + stock_basic。"""
    codes_rets = [(code, ind, rets) for ind, members in industries.items() for code, rets in members]
    closes = {}
    for code, _ind, rets in codes_rets:
        seq = [10.0]
        for r in rets[1:]:
            seq.append(seq[-1] * (1 + r))
        closes[code] = seq
    for i, d in enumerate(dates):
        rows = []
        for code, _ind, _rets in codes_rets:
            c = closes[code][i]
            pre = closes[code][i - 1] if i > 0 else c
            rows.append({"ts_code": code, "open": c, "high": c, "low": c, "close": c, "pre_close": pre,
                        "vol": 1000.0, "amount": 100.0})
        write_daily_fixture(env, "daily", d, rows)
    insert_stock_basic(env, [
        {"ts_code": code, "industry": ind, "list_date": dates[0] - timedelta(days=400)}
        for code, ind, _rets in codes_rets
    ])


def test_compute_industry_strength_end_to_end(isolated_env):
    """真实 I/O 入口:两个达标行业(6/5 只成员)+ 一个不足 `_MIN_MEMBERS` 的行业,daily
    面板手工构造已知 `ret_1d`,验证中位数/成员数/达标行业出现/不达标行业缺席/强弱排名
    正确。持续天数的**逐位算法正确性**已由①组对拍单测覆盖,这里只验 I/O 接线正确。"""
    dates = business_days(date(2024, 1, 2), 3)
    insert_trade_cal(isolated_env, dates)
    strong = [(f"60000{i}.SH", [0.0, 0.05, 0.05]) for i in range(6)]    # 6 只,末日 +5%
    weak = [(f"60010{i}.SH", [0.0, 0.01, 0.01]) for i in range(5)]      # 5 只,末日 +1%
    thin = [(f"90000{i}.SZ", [0.0, 0.02, 0.02]) for i in range(3)]      # 3 只(不足 5)
    _seed_industry_market(isolated_env, dates, {"强行业": strong, "弱行业": weak, "样本不足行业": thin})

    scores = ist.compute_industry_strength(
        dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path
    )
    by_ind = {s.industry: s for s in scores}
    assert set(by_ind) == {"强行业", "弱行业"}             # 样本不足行业(3<5)整天缺席
    assert by_ind["强行业"].member_count == 6
    assert by_ind["强行业"].median_ret == pytest.approx(0.05, abs=1e-9)
    assert by_ind["弱行业"].median_ret == pytest.approx(0.01, abs=1e-9)
    assert by_ind["强行业"].industry_rank == 1              # 中位数更高排第一
    assert by_ind["弱行业"].industry_rank == 2


def test_compute_industry_strength_no_daily_data_returns_empty(isolated_env):
    """`daily` 表当日无行(空 parquet 目录)→ 空列表,优雅降级(同 `compute_sector_strength`
    先例),不崩。"""
    assert ist.compute_industry_strength(
        date(2024, 1, 2), parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path
    ) == []


# ————————————————————————————————————————————————————————————————
# ④ stock_persist_days / stock_industry_rank(A2/B3 判据 + ③ 排序键的消费方式)
# ————————————————————————————————————————————————————————————————

def test_stock_persist_days_and_rank_lookup():
    hot = ist.industry_strength_lookup([
        ist.IndustryStrength(industry="半导体", median_ret=0.03, member_count=10,
                             industry_rank=1, is_strength_day=True, persist_days=5),
        ist.IndustryStrength(industry="银行", median_ret=0.01, member_count=20,
                             industry_rank=2, is_strength_day=False, persist_days=0),
    ])
    industry_of = {"600001.SH": "半导体", "600002.SH": "银行", "600003.SH": "冷门行业(不在热表)"}

    assert ist.stock_persist_days("600001.SH", industry_of, hot) == 5
    assert ist.stock_persist_days("600002.SH", industry_of, hot) == 0
    assert ist.stock_persist_days("600003.SH", industry_of, hot) == 0    # 行业不在 hot(未达标/查无)→ 0
    assert ist.stock_persist_days("600099.SH", industry_of, hot) == 0    # 票无 industry → 0

    assert ist.stock_industry_rank("600001.SH", industry_of, hot) == 1
    assert ist.stock_industry_rank("600002.SH", industry_of, hot) == 2
    assert ist.stock_industry_rank("600003.SH", industry_of, hot) is None   # 不参与排名 → None,不是 0
    assert ist.stock_industry_rank("600099.SH", industry_of, hot) is None


# ————————————————————————————————————————————————————————————————
# ⑤ load_industry_map(stock_basic 非空过滤)
# ————————————————————————————————————————————————————————————————

def test_load_industry_map_filters_blank(isolated_env):
    insert_stock_basic(isolated_env, [
        {"ts_code": "600001.SH", "industry": "半导体"},
        {"ts_code": "600002.SH", "industry": "  "},     # 空白 → 过滤
        {"ts_code": "600003.SH", "industry": None},     # None → 过滤
    ])
    out = ist.load_industry_map(db_path=isolated_env.db_path)
    assert out == {"600001.SH": "半导体"}
