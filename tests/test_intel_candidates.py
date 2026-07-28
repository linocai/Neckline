"""候选情报筛选管线单测(plan §五 v1.3-③-C3 验收)。覆盖 ③ 验收里 C3 相关项:

  · 候选生成**不经 `build_entry_mask`**(K1 entry mask 退役,直接断言)+ 与 K1 哲学相反
    地纳入高弹(GEM/STAR)——解耦证明(§3.8-(b));
  · 卫生线**复用 `board_pool`**(资格/宽基标签板块从 step① 暴起筛选剔除,不另写一份);
  · K4 安检:`hard_cut` 命中拦截出池 / `avoid_flag` 命中打标保留(读 DB section,不抄常量,
    复用 ②-A 镜像);
  · 五板块常驻**按 `ths_index.name` 精确匹配**(禁模糊:"芯片"不误纳"汽车芯片");
  · 题材持续天数**反用**排序(1 天新鲜 > 2-3 天警惕);板块资金流强度排序;
  · 出 **20 只**;
  · 自选体检 / 问询台**纪律红绿灯仍与报告同码**(候选解耦、纪律核对不变);
  · **两处 K4 镜像 I/O 一致性**(② 逐票 loader vs ③ bulk loader 直接对拍,阈值单一源)。
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date, timedelta

import polars as pl
import pytest

import neckline.report.holding_k4_check as hk
import neckline.report.intel_candidates as ic
from neckline.settings_store import set_intel_watch_boards
from neckline.strategy import brain
from tests.conftest import (
    TEST_RULE_V1_CONFIG,
    business_days,
    insert_namechange,
    insert_stock_basic,
    insert_trade_cal,
    write_daily_fixture,
    write_flat_parquet,
)

pytestmark = pytest.mark.usefixtures("isolated_env")

_RULE = {"config": dict(TEST_RULE_V1_CONFIG)}   # forbid_high_elasticity=True(K1 主板 only)——情报管线应无视之


# —— 合成市场构造器 ————————————————————————————————————————————————————————

def _rising(n: int, p0: float = 10.0, r: float = 0.01, last: float | None = None) -> list:
    closes = [p0 * (1 + r) ** i for i in range(n)]
    if last is not None:
        closes[-1] = closes[-2] * (1 + last)
    return closes


def _flat_then_up(n: int, age: int, base: float = 100.0, up: float = 110.0) -> list:
    """前 (n-age) 天恒 base(不站上 MA20)、后 age 天升到 up(连续站上 MA20)→ board_age=age。"""
    return [base] * (n - age) + [up] * age


# 默认测试行业(行业闸用):同板块成员默认同一行业 → 板内 100%。**2026-07-27 share→lift 改造**:
# 闸判据从「板内占比」改「lift=板内占比÷全市场占比」后,板内 100% 不再自动过闸——lift 需要
# 全市场分母**不**恰好等于板内构成(否则 lift≡1,永远卡在阈值上,见 `_market_industry_shares`)。
# `_seed_market` 默认自动铺一份**背景填充市场**(`_MARKET_FILLER_INDUSTRY`,与任何测试行业都
# 不同名)兜底,让"同板块成员默认同行业"继续在 lift 下自动过闸(保留原设计意图:不测行业闸的
# 用例不必关心行业闸)。**要精确摆布 lift 分子分母的用例**(行业闸专测/误杀回归)传
# `market_filler=False` 关掉自动背景,自己用 `_seed_industry_only` 摆一份可控的市场。
_DEFAULT_INDUSTRY = "半导体"
_MARKET_FILLER_INDUSTRY = "背景填充行业"       # 与本文件任何测试行业都不同名的合成占位行业
_MARKET_FILLER_COUNT = 200                    # 够把本文件最大板块(25 只同行业)稀释到 lift≥2(见下)


def _seed_market(env, dates, stocks: list, *, market_filler: bool = True) -> None:
    """`stocks`: [{code, market='主板', closes, turnover=5.0(标量或逐日列表), list_offset=400,
    st=False, name?, industry?}]。写 daily/adj/daily_basic + stock_basic(+namechange)。
    `industry` 缺省 `_DEFAULT_INDUSTRY`(行业闸:同板块成员同行业);传 None 显式无行业。

    `market_filler=True`(默认)**额外**铺 `_MARKET_FILLER_COUNT` 只无价「背景填充行业」股票
    (只进 `stock_basic`,不进任何板块成员、不参与卫生线/K4)——把全市场行业分布从"等于本次
    测试股票的构成"拉开,使 lift 分母不再与分子恒等(否则任意单一行业板块 lift≡1,永远不过
    行业闸阈值,见模块内 `_dominant_industries`)。**行业闸专测/误杀回归用例**需要手动摆布
    板内 vs 全市场的精确比例时传 `market_filler=False` 关闭本机制,自行用 `_seed_industry_only`
    铺可控背景(否则本机制的 200 只会混进分母,打乱精心算好的 lift 数值)。"""
    for i, d in enumerate(dates):
        daily, adj, basic = [], [], []
        for s in stocks:
            closes = s["closes"]
            c = closes[i]
            pre = closes[i - 1] if i > 0 else c
            daily.append({"ts_code": s["code"], "open": c, "high": c, "low": c, "close": c,
                          "pre_close": pre, "vol": s.get("vol", 100000.0), "amount": s.get("amount", 30000.0)})
            adj.append({"ts_code": s["code"], "adj_factor": 1.0})
            to = s.get("turnover", 5.0)
            tv = to[i] if isinstance(to, list) else to
            basic.append({"ts_code": s["code"], "turnover_rate": tv, "volume_ratio": 1.0,
                          "circ_mv": 1_000_000.0, "total_mv": 1_000_000.0, "free_share": 100_000.0})
        write_daily_fixture(env, "daily", d, daily)
        write_daily_fixture(env, "adj_factor", d, adj)
        write_daily_fixture(env, "daily_basic", d, basic)
    sb, nc = [], []
    for s in stocks:
        ld = dates[0] - timedelta(days=s.get("list_offset", 400))
        name = s.get("name", s["code"])
        ind = s["industry"] if "industry" in s else _DEFAULT_INDUSTRY   # None 可显式传(无行业票)
        sb.append({"ts_code": s["code"], "name": name, "market": s.get("market", "主板"),
                   "list_date": ld, "industry": ind})
        if s.get("st"):
            nc.append({"ts_code": s["code"], "name": name, "start_date": ld})
    if market_filler:
        sb.extend({"ts_code": f"9{j:05d}.SZ", "name": f"背景{j}", "industry": _MARKET_FILLER_INDUSTRY}
                  for j in range(_MARKET_FILLER_COUNT))
    insert_stock_basic(env, sb)
    if nc:
        insert_namechange(env, nc)


def _seed_boards(env, index_rows: list, member_rows: list, board_daily: dict | None = None, dates=None) -> None:
    write_flat_parquet(env, "ths_index.parquet", index_rows)
    write_flat_parquet(env, "ths_member.parquet", member_rows)
    if board_daily and dates:
        rows = []
        for board_code, closes in board_daily.items():
            for i, d in enumerate(dates):
                rows.append({"ts_code": board_code, "trade_date": d, "close": closes[i]})
        write_flat_parquet(env, "ths_daily.parquet", rows)


def _seed_moneyflow(env, td, net_by_code: dict) -> None:
    write_daily_fixture(env, "moneyflow_dc", td,
                        [{"ts_code": c, "net_amount": v} for c, v in net_by_code.items()])


def _seed_k4(env) -> None:
    """在隔离库落 K4 advisory 分区(section 归属 = 真实 DB 口径:A* hard_cut / B* avoid_flag)。"""
    brain.save_version("K4", rule={"config": {}, "k4_advisory": {
        "hard_cut": {
            "A1_turnover_gt_10": {"expr": "turnover_rate > 10", "evidence": "换手>10% 次日跌停 3.37×"},
            "A2_theme_persist_ge_4": {"expr": "题材≥4天", "evidence": "题材≥4天过热接盘"},
            "A3_belowyear_limitup": {"expr": "年线下涨停", "evidence": "年线下涨停=诱多域"},
            "A4_base_hygiene": {"expr": "base_universe & days>=120", "evidence": "卫生线"},
        },
        "avoid_flag": {
            "B1_volume_stacking": {"expr": "堆积", "evidence": "堆积后再放量"},
            "B2_dual_golden_cross": {"expr": "双金叉", "evidence": "双金叉四态垫底"},
            "B3_theme_persist_2_3": {"expr": "题材2-3", "evidence": "题材2-3天接盘侧"},
            "B4_chase_strong_red": {"expr": "close>ma20 & ret>5%", "evidence": "追强诱多"},
        },
    }}, changelog="test K4", activate=False, db_path=env.db_path)


def _codes(cands) -> list:
    return [c.ts_code for c in cands]


# 五常驻板块名 → 真实 ths_index code(保底测试用;精确匹配路径)。
_BOARD_CODE = {
    "芯片概念": "885756.TI", "创新药": "886015.TI", "储能": "885921.TI",
    "机器人概念": "885517.TI", "稀土永磁": "885343.TI",
}


def _seed_permanent(env, board_members: dict) -> None:
    """`board_members`: {常驻板块名: [成分股code]}。铺 ths_index(名)+ ths_member(成分)。
    某板块 code 列表为空 → ths_index 有该板块行、ths_member 无成分(测「0 合格票」缺额)。"""
    write_flat_parquet(env, "ths_index.parquet",
                       [{"ts_code": _BOARD_CODE[n], "name": n} for n in board_members])
    write_flat_parquet(env, "ths_member.parquet",
                       [{"index_code": _BOARD_CODE[n], "con_code": c}
                        for n, codes in board_members.items() for c in codes])


def _src(cands) -> dict:
    return {c.ts_code: c.intel_rank["source"] for c in cands}


def _seed_industry_only(env, rows: list) -> None:
    """只写 stock_basic 行(行业),不给行情——用于精确摆布 lift 的分子/分母(它们不过
    ②卫生线〔无价〕、不会成为候选)。**两种用法**:①传给 `_seed_permanent` 当board成员 → 只
    影响该板块的**板内**行业占比(denom=全体成员);②不传给 `_seed_permanent`(纯市场背景)
    → 只影响 `_market_industry_shares` 的**全市场**行业占比分母,不影响任何板块的板内构成。
    `rows`: [{code, industry}]。"""
    insert_stock_basic(env, [{"ts_code": r["code"], "industry": r["industry"]} for r in rows])


# ————————————————————————————————————————————————————————————————
# ① 候选不经 build_entry_mask + 与 K1 解耦(纳入高弹)
# ————————————————————————————————————————————————————————————————

def test_candidates_do_not_go_through_build_entry_mask(isolated_env, monkeypatch):
    """K1 entry mask 退役的**直接断言**:把 `build_entry_mask` 打成一调用即炸,情报管线仍
    正常产候选 → 证明候选生成不经它(§3.8-(b) 候选解耦)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_market(isolated_env, dates, [
        {"code": "600001.SH", "market": "主板", "closes": _rising(30)},
    ])
    _seed_boards(isolated_env, [{"ts_code": "885756.TI", "name": "芯片概念"}],
                 [{"index_code": "885756.TI", "con_code": "600001.SH"}])

    def _boom(*a, **k):
        raise AssertionError("build_entry_mask 不应被候选情报管线调用(K1 entry mask 已退役)")

    monkeypatch.setattr("neckline.strategy.momentum.build_entry_mask", _boom)
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert "600001.SH" in _codes(cands)


def test_high_elasticity_gem_star_included_against_k1_philosophy(isolated_env):
    """生成域刻意含高弹(GEM/STAR)——与 K1 `forbid_high_elasticity=True` 相反:创业板/科创板
    成分应入候选(intelRank.highElasticity=True 标注,机器不禁)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_market(isolated_env, dates, [
        {"code": "600001.SH", "market": "主板", "closes": _rising(30)},
        {"code": "300002.SZ", "market": "创业板", "closes": _rising(30)},
        {"code": "688003.SH", "market": "科创板", "closes": _rising(30)},
    ])
    _seed_boards(isolated_env, [{"ts_code": "885756.TI", "name": "芯片概念"}], [
        {"index_code": "885756.TI", "con_code": "600001.SH"},
        {"index_code": "885756.TI", "con_code": "300002.SZ"},
        {"index_code": "885756.TI", "con_code": "688003.SH"},
    ])
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    by_code = {c.ts_code: c for c in cands}
    assert {"600001.SH", "300002.SZ", "688003.SH"} <= set(by_code)
    assert by_code["300002.SZ"].intel_rank["highElasticity"] is True
    assert by_code["688003.SH"].intel_rank["highElasticity"] is True
    assert by_code["600001.SH"].intel_rank["highElasticity"] is False


def test_bse_excluded_and_hygiene_st_next_new_trend(isolated_env):
    """② 卫生线:BSE 排除、ST 剔、次新(<120天)剔、趋势向下(close<ma20)剔——即便是板块成员。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_market(isolated_env, dates, [
        {"code": "600001.SH", "market": "主板", "closes": _rising(30)},                # 干净入选
        {"code": "830001.BJ", "market": "北交所", "closes": _rising(30)},              # BSE 排除
        {"code": "600002.SH", "market": "主板", "closes": _rising(30), "st": True, "name": "*ST乙"},  # ST 剔
        {"code": "600003.SH", "market": "主板", "closes": _rising(30), "list_offset": 30},           # 次新剔
        {"code": "600004.SH", "market": "主板", "closes": [10.0] * 29 + [9.0]},        # 趋势向下(末日跌破,close<ma20)剔
    ])
    _seed_boards(isolated_env, [{"ts_code": "885756.TI", "name": "芯片概念"}],
                 [{"index_code": "885756.TI", "con_code": c} for c in
                  ("600001.SH", "830001.BJ", "600002.SH", "600003.SH", "600004.SH")])
    codes = _codes(ic.build_intel_candidates(dates[-1], _RULE,
                                             parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path))
    assert codes == ["600001.SH"]


# ————————————————————————————————————————————————————————————————
# ② 卫生线复用 board_pool(资格/宽基板块从 step① 暴起剔除)
# ————————————————————————————————————————————————————————————————

def test_breakout_board_pool_hygiene_excludes_qualification_boards(isolated_env):
    """当日暴起板块**先过 board_pool 卫生线**:资格/宽基类板块(如「深股通」名称模式命中)
    即便拥挤度最高也被剔除,其独有成员不入候选;合法题材板块成员入候选。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    set_intel_watch_boards([], db_path=isolated_env.db_path)   # 清空常驻,只测暴起路径
    _seed_market(isolated_env, dates, [
        {"code": "600050.SH", "market": "主板", "closes": _rising(30)},   # 只属「深股通」(资格,应被卫生线剔)
        {"code": "600051.SH", "market": "主板", "closes": _rising(30)},   # 属「示例题材」(合法)
    ])
    _seed_boards(
        isolated_env,
        [{"ts_code": "880001.TI", "name": "深股通"}, {"ts_code": "880002.TI", "name": "示例题材"}],
        [{"index_code": "880001.TI", "con_code": "600050.SH"},
         {"index_code": "880002.TI", "con_code": "600051.SH"}],
        board_daily={"880001.TI": _rising(30, p0=100.0, r=0.02), "880002.TI": _rising(30, p0=100.0, r=0.015)},
        dates=dates,
    )
    codes = _codes(ic.build_intel_candidates(dates[-1], _RULE,
                                             parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path))
    assert "600051.SH" in codes           # 合法题材成员入选
    assert "600050.SH" not in codes        # 资格板块(深股通)被 board_pool 剔 → 其独有成员不入


# ————————————————————————————————————————————————————————————————
# ③ K4 安检:hard_cut 拦 / avoid_flag 标
# ————————————————————————————————————————————————————————————————

def test_k4_hard_cut_intercepts_avoid_flag_tags(isolated_env):
    """③ K4:A1 换手>10(hard_cut)→ 拦截出池;B4 追强大红(avoid_flag)→ 打标保留(k4_flags)。
    读 DB section(不抄常量),复用 ②-A 镜像评估器。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_k4(isolated_env)
    _seed_market(isolated_env, dates, [
        {"code": "600001.SH", "market": "主板", "closes": _rising(30)},                        # 干净
        {"code": "600002.SH", "market": "主板", "closes": _rising(30),
         "turnover": [5.0] * 29 + [15.0]},                                                       # A1 hard_cut(末日换手15)
        {"code": "600003.SH", "market": "主板", "closes": _rising(30, last=0.06)},               # B4 avoid_flag(末日+6%,close>ma20)
    ])
    _seed_boards(isolated_env, [{"ts_code": "885756.TI", "name": "芯片概念"}],
                 [{"index_code": "885756.TI", "con_code": c} for c in ("600001.SH", "600002.SH", "600003.SH")])
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    by_code = {c.ts_code: c for c in cands}
    assert "600002.SH" not in by_code                          # A1 hard_cut → 拦出池
    assert "600003.SH" in by_code                              # B4 avoid_flag → 保留
    assert "B4_chase_strong_red" in by_code["600003.SH"].k4_flags   # 打标
    assert by_code["600001.SH"].k4_flags == []                 # 干净票无标注


def test_k4_no_db_row_defaults_to_avoid_flag_not_hard_cut(isolated_env):
    """隔离库无 K4 行 → `load_k4_sections` 空 → 命中码全按 `_DEFAULT_SECTION`(avoid_flag)处理:
    B4 追强不被 hard_cut 误剔,只打标(严守 hard_cut 单一源=DB,不在 DB 外自造硬剔)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_market(isolated_env, dates, [
        {"code": "600003.SH", "market": "主板", "closes": _rising(30, last=0.06)},   # B4 命中
    ])
    _seed_boards(isolated_env, [{"ts_code": "885756.TI", "name": "芯片概念"}],
                 [{"index_code": "885756.TI", "con_code": "600003.SH"}])
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    by_code = {c.ts_code: c for c in cands}
    assert "600003.SH" in by_code                                  # 无 DB section → 不 hard_cut
    assert "B4_chase_strong_red" in by_code["600003.SH"].k4_flags   # 仍打标


def test_forced_inquiry_code_exempt_from_hard_cut(isolated_env):
    """问询台海选池强制票(用户点名):即便命中 hard_cut(A1 换手>10)也豁免拦截、保留并全数打标
    (§2.5 强制纳入 + 机器不禁给人判);且非板块成员的强制票也能被纳入。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_k4(isolated_env)
    _seed_market(isolated_env, dates, [
        {"code": "600001.SH", "market": "主板", "closes": _rising(30)},
        {"code": "600009.SH", "market": "主板", "closes": _rising(30), "turnover": [5.0] * 29 + [15.0]},  # A1 命中 + 非板块成员
    ])
    _seed_boards(isolated_env, [{"ts_code": "885756.TI", "name": "芯片概念"}],
                 [{"index_code": "885756.TI", "con_code": "600001.SH"}])
    cands = ic.build_intel_candidates(dates[-1], _RULE, parquet_dir=isolated_env.parquet_dir,
                                      db_path=isolated_env.db_path, forced_codes=["600009.SH"])
    by_code = {c.ts_code: c for c in cands}
    assert "600009.SH" in by_code                                   # 强制票纳入(非成员 + hard_cut 命中均豁免)
    assert "A1_turnover_gt_10" in by_code["600009.SH"].k4_flags     # hard 命中也诚实打标透出


# ————————————————————————————————————————————————————————————————
# ④ 五板块常驻精确匹配(禁模糊)
# ————————————————————————————————————————————————————————————————

def test_permanent_boards_exact_name_match_not_fuzzy(isolated_env):
    """五常驻按 `ths_index.name` **精确匹配**:「芯片概念」命中,近义「汽车芯片」**不**被模糊纳入
    (否则会把不相干成分拉进候选)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_market(isolated_env, dates, [
        {"code": "600001.SH", "market": "主板", "closes": _rising(30)},   # 属「芯片概念」(精确常驻)
        {"code": "600060.SH", "market": "主板", "closes": _rising(30)},   # 属「汽车芯片」(模糊近义,不应纳入)
    ])
    _seed_boards(
        isolated_env,
        [{"ts_code": "885756.TI", "name": "芯片概念"}, {"ts_code": "886001.TI", "name": "汽车芯片"}],
        [{"index_code": "885756.TI", "con_code": "600001.SH"},
         {"index_code": "886001.TI", "con_code": "600060.SH"}],
    )
    codes = _codes(ic.build_intel_candidates(dates[-1], _RULE,
                                             parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path))
    assert "600001.SH" in codes
    assert "600060.SH" not in codes    # 「汽车芯片」非精确常驻名 → 不入 step①


# ————————————————————————————————————————————————————————————————
# ⑤ 情报排序:题材天数反用 + 资金流强度
# ————————————————————————————————————————————————————————————————

def _industry_closes(daily_rets: list) -> list:
    """日收益率脚本(长度 n,index0 值被 `_seed_market` 结构性忽略——首日 pre_close=close
    恒 ret_1d=0)→ 收盘价路径。"""
    out = [10.0]
    for r in daily_rets[1:]:
        out.append(out[-1] * (1 + r))
    return out


def test_theme_persistence_reversed_freshness_ranking(isolated_env):
    """题材持续天数**反用**:行业强度持续天数=1(新鲜)的排在=3(警惕)之前。**v1.4-② 起
    持续天数唯一源 = `industry_strength`**(不再是概念板块 board_age,见
    `report/industry_strength.py`)——本测试因此从"直接摆布板块指数 K 线"改为"摆布行业级
    `ret_1d` 剧本,靠跨行业 top20% 竞争产出目标持续天数"。

    **v1.4-③ 排序键改版后的定位**:本用例构造下「新鲜行业」末日冲高恰好也拿到当日
    中位数最高(→ `industryRank`=1),「老行业」次之(→ `industryRank`=2)——**新排序键①
    (行业强度排名)本身已经把 600001 排到 600002 之前**,不需要单独依赖排序键②(持续
    天数升序)来分出胜负,故本用例的 `order.index` 断言现由 rank(键①)驱动,不再单独
    隔离验证键②。**三级键各自独立生效的隔离证明见
    `test_sort_key_three_level_priority`(纯函数单测,手工构造 rank 相同只 persist 不同的
    样本)**——那个测试才是「排序键②在①并列时才生效」的严格证据。本用例继续作为
    `industry_strength → intel_rank.{themePersistDays,industryPersistDays,industryRank}`
    的端到端接线证明。

    4 个达标行业(各 5 只成员,同行业成员当日收益相同 → 中位数=该值,消除歧义)、
    quantile(0.8, nearest 插值)在 n=4 下稳定选出「当日中位数最高的 2 个行业」过阈
    (口径与量级见 `tests/test_industry_strength.py` 的算法级实测):「填充C/D」全程温和
    上涨、从不进 top2 兜底;「新鲜行业」只在末日冲高(持续天数→1);「老行业」末 3 日抬升
    (持续天数→3)。600001.SH/600002.SH 仍各自是「新鲜题材」/「老题材」两个**板块**的
    唯一成员(候选依旧走既有板块漏斗入选,未改动),只换驱动"题材持续天数"这一个量的
    底层数据源。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    set_intel_watch_boards(["新鲜题材", "老题材"], db_path=isolated_env.db_path)

    n = len(dates)
    fresh_rets = [0.005] * n
    fresh_rets[-1] = 0.05                               # 只末日冲高 → 持续天数=1
    old_rets = [0.005] * n
    old_rets[-1] = old_rets[-2] = old_rets[-3] = 0.02    # 末 3 日抬升 → 持续天数=3
    fillerC_rets = [0.008] * n                           # 全程温和最高,quiet 期兜底占 top2
    fillerD_rets = [0.007] * n

    fresh_closes = _industry_closes(fresh_rets)
    old_closes = _industry_closes(old_rets)
    fillerC_closes = _industry_closes(fillerC_rets)
    fillerD_closes = _industry_closes(fillerD_rets)

    stocks = [
        {"code": "600001.SH", "market": "主板", "closes": fresh_closes, "industry": "新鲜行业"},
        {"code": "600002.SH", "market": "主板", "closes": old_closes, "industry": "老行业"},
    ]
    for k in range(4):    # 凑够 _MIN_MEMBERS=5(1 只主角 + 4 只同行业陪衬,收益路径逐位相同)
        stocks.append({"code": f"60011{k}.SH", "market": "主板", "closes": fresh_closes, "industry": "新鲜行业"})
        stocks.append({"code": f"60021{k}.SH", "market": "主板", "closes": old_closes, "industry": "老行业"})
    for k in range(5):
        stocks.append({"code": f"60031{k}.SH", "market": "主板", "closes": fillerC_closes, "industry": "填充行业C"})
        stocks.append({"code": f"60041{k}.SH", "market": "主板", "closes": fillerD_closes, "industry": "填充行业D"})
    _seed_market(isolated_env, dates, stocks)
    _seed_boards(
        isolated_env,
        [{"ts_code": "880001.TI", "name": "新鲜题材"}, {"ts_code": "880002.TI", "name": "老题材"}],
        [{"index_code": "880001.TI", "con_code": "600001.SH"},
         {"index_code": "880002.TI", "con_code": "600002.SH"}],
    )
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    by_code = {c.ts_code: c for c in cands}
    assert by_code["600001.SH"].intel_rank["themePersistDays"] == 1
    assert by_code["600002.SH"].intel_rank["themePersistDays"] == 3
    # v1.4-③ 新字段:industryPersistDays 与 themePersistDays 同值同源;industryRank 按当日
    # 中位数排名(新鲜行业末日冲高最高 → 排名 1;老行业次之 → 排名 2);yellowCardCount 无
    # K4 seed → 0(无 DB 行时不计黄牌,见 `test_yellow_card_count_zero_when_k4_db_missing`)。
    assert by_code["600001.SH"].intel_rank["industryPersistDays"] == 1
    assert by_code["600002.SH"].intel_rank["industryPersistDays"] == 3
    assert by_code["600001.SH"].intel_rank["industryRank"] == 1
    assert by_code["600002.SH"].intel_rank["industryRank"] == 2
    assert by_code["600001.SH"].intel_rank["yellowCardCount"] == 0
    assert by_code["600002.SH"].intel_rank["yellowCardCount"] == 0
    order = _codes(cands)
    assert order.index("600001.SH") < order.index("600002.SH")   # 新鲜排前(本例由排序键①行业排名驱动)


def test_sector_flow_displayed_but_no_longer_drives_order(isolated_env):
    """③-B:板块资金流强度**退出排序键、只作并列展示**(需求 8)。构造资金流与行业强度
    **反着摆**的场景——「强行业」末日冲高最高(→ `industryRank`=1)但资金流故意给得很小,
    「弱行业」次高(→ `industryRank`=2)但资金流故意给得很大。v1.3 起的旧键(资金流优先)
    会让弱行业票排前;v1.4-③ 新键(行业强度排名优先)让强行业票排前——这就是本用例要
    证的事。同时断言 `intelRank.sectorFlow` 仍正确带出两只的真实净流入值(数据没丢,
    只是不再驱动顺序)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    set_intel_watch_boards(["强势板块", "弱势板块"], db_path=isolated_env.db_path)

    n = len(dates)
    strong_rets = [0.005] * n
    strong_rets[-1] = 0.05     # 末日大涨 → 当日中位数最高 → industryRank=1
    weak_rets = [0.005] * n
    weak_rets[-1] = 0.01       # 末日小涨 → industryRank=2

    strong_closes = _industry_closes(strong_rets)
    weak_closes = _industry_closes(weak_rets)

    stocks = [
        {"code": "600001.SH", "market": "主板", "closes": strong_closes, "industry": "强行业"},
        {"code": "600002.SH", "market": "主板", "closes": weak_closes, "industry": "弱行业"},
    ]
    for k in range(4):    # 凑够 _MIN_MEMBERS=5
        stocks.append({"code": f"60011{k}.SH", "market": "主板", "closes": strong_closes, "industry": "强行业"})
        stocks.append({"code": f"60021{k}.SH", "market": "主板", "closes": weak_closes, "industry": "弱行业"})
    _seed_market(isolated_env, dates, stocks)
    _seed_boards(
        isolated_env,
        [{"ts_code": "880001.TI", "name": "强势板块"}, {"ts_code": "880002.TI", "name": "弱势板块"}],
        [{"index_code": "880001.TI", "con_code": "600001.SH"},
         {"index_code": "880002.TI", "con_code": "600002.SH"}],
    )
    # 资金流反着摆:行业强度更弱的票资金流反而更大——若排序仍受资金流驱动会让它排前,
    # 从而证伪新键;新键下应仍由行业强度排名(600001 更强)驱动,600001 排前。
    _seed_moneyflow(isolated_env, dates[-1], {"600001.SH": 100.0, "600002.SH": 5000.0})
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    by_code = {c.ts_code: c for c in cands}
    # sectorFlow 仍正确带出(并列展示),数值不受排序键改版影响。
    assert by_code["600001.SH"].intel_rank["sectorFlow"] == 100.0
    assert by_code["600002.SH"].intel_rank["sectorFlow"] == 5000.0
    assert by_code["600001.SH"].intel_rank["industryRank"] == 1
    assert by_code["600002.SH"].intel_rank["industryRank"] == 2
    order = _codes(cands)
    assert order.index("600001.SH") < order.index("600002.SH")   # 行业排名驱动,资金流小的仍排前


# ————————————————————————————————————————————————————————————————
# ⑤b `_sort_key` 纯函数单测(v1.4-③-A/C:三级优先级 + 无行业排最后 + 白名单纪律)
# ————————————————————————————————————————————————————————————————

class _KeyTrackingDict(dict):
    """记录 `__getitem__` 实际访问过哪些键的 dict 子类,供白名单单测直接验证
    `_sort_key` **运行期**只碰声明的键(比静态文本搜索更可靠——真的跑一遍取值)。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.accessed: set = set()

    def __getitem__(self, key):
        self.accessed.add(key)
        return super().__getitem__(key)


def _entry(code, industry_rank, industry_persist_days, yellow_card_count, base_score=1.0, **extra):
    d = {
        "code": code, "industry_rank": industry_rank,
        "industry_persist_days": industry_persist_days,
        "yellow_card_count": yellow_card_count, "base_score": base_score,
    }
    d.update(extra)
    return d


def test_sort_key_reads_only_whitelisted_inputs_not_sector_flow(isolated_env):
    """③-B/③-C:`_sort_key` 只读 `_SORT_KEY_INPUTS` 白名单五键,**不读 `sector_flow`**(已
    退为并列展示)、也不读任何其他键(row/board 等)——用可追踪访问的 dict 子类直接断言
    运行期实际访问集合,比"搜代码里有没有出现某字符串"更可靠。"""
    e = _KeyTrackingDict(_entry(
        "600000.SH", industry_rank=3, industry_persist_days=2, yellow_card_count=1, base_score=10.0,
        sector_flow=999999.0, row={"close": 10.0}, board="MAIN", k4_flags=["X"],
    ))
    ic._sort_key(e)
    assert "sector_flow" not in e.accessed          # ③-B 硬要求
    assert e.accessed <= ic._SORT_KEY_INPUTS         # ③-C 白名单:不超出这五键
    assert e.accessed == ic._SORT_KEY_INPUTS         # 且五键确实全部被用到(非摆设)


def test_sort_key_three_level_priority(isolated_env):
    """③-A 三级排序键优先级:industry_rank ASC 优先于 industry_persist_days ASC 优先于
    yellow_card_count ASC;base_score DESC / code ASC 只在前三者全部并列时才生效(确定性
    兜底,不构成独立排序意图)。四条样本逐级只改一个维度,交叉验证优先级顺序。"""
    entries = [
        _entry("A", industry_rank=2, industry_persist_days=0, yellow_card_count=0, base_score=999.0),
        # rank 更差(2>1),即便 persist/yellow/base_score 全面占优也排最后——证明 rank 优先级最高
        _entry("B", industry_rank=1, industry_persist_days=3, yellow_card_count=5, base_score=1.0),
        # rank 并列(=1),persist 更小(更新鲜)排前——证明 persist 优先级次于 rank
        _entry("C", industry_rank=1, industry_persist_days=1, yellow_card_count=5, base_score=1.0),
        # rank/persist 并列,yellow 更少排前——证明 yellow 优先级次于 persist
        _entry("D", industry_rank=1, industry_persist_days=1, yellow_card_count=0, base_score=1.0),
    ]
    entries.sort(key=ic._sort_key)
    assert [e["code"] for e in entries] == ["D", "C", "B", "A"]


def test_sort_key_base_score_and_code_are_deterministic_tiebreak_only(isolated_env):
    """前三级(rank/persist/yellow)全部并列时,才轮到 `base_score` DESC,再到 `code` ASC——
    这两个不构成"第四/五维排序意图",只是同名次时的确定性兜底(plan §五 v1.4-③-A 明写)。"""
    tied = [
        _entry("Z", industry_rank=1, industry_persist_days=0, yellow_card_count=0, base_score=1.0),
        _entry("A", industry_rank=1, industry_persist_days=0, yellow_card_count=0, base_score=5.0),
        _entry("M", industry_rank=1, industry_persist_days=0, yellow_card_count=0, base_score=5.0),
    ]
    tied.sort(key=ic._sort_key)
    # base_score 5.0 的两个(A/M)排在 base_score 1.0 的 Z 之前(DESC);A/M 之间按 code ASC。
    assert [e["code"] for e in tied] == ["A", "M", "Z"]


def test_sort_key_no_industry_rank_sorts_last_not_zero(isolated_env):
    """无行业 / 成员<5 未参与排名(`industry_rank=None`)必须排在**所有**已排名的票之后
    (映射 `+inf`),**即便该未排名票的其余维度全面占优、即便已排名票的排名很差(如
    500)**——不静默当 0(0 会把无行业票错误顶到榜首,plan §五 v1.4-③-A 明写的红线)。"""
    entries = [
        _entry("WORST_RANKED", industry_rank=500, industry_persist_days=3, yellow_card_count=5, base_score=1.0),
        _entry("NO_RANK", industry_rank=None, industry_persist_days=0, yellow_card_count=0, base_score=999.0),
    ]
    entries.sort(key=ic._sort_key)
    assert [e["code"] for e in entries] == ["WORST_RANKED", "NO_RANK"]


# ————————————————————————————————————————————————————————————————
# ⑤c 黄牌数(yellow_card_count,排序键③)语义:仅数 DB avoid_flag,不数 hard_cut/合成码
# ————————————————————————————————————————————————————————————————

def test_yellow_card_count_excludes_hard_cut_hits(isolated_env):
    """③-A:`yellowCardCount` 只数 DB **严格**登记为 `avoid_flag` 的命中,**不数 hard_cut**
    ——用强制纳入票(forced,豁免 hard_cut 拦截、但命中全部诚实打标)同时命中 A1(hard_cut)
    与 B4(avoid_flag)两码,断言 `k4_flags` 两码都在(打标不区分)、但 `yellowCardCount`
    只数 1(只有 B4 计入黄牌,A1 不计入)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_k4(isolated_env)
    _seed_market(isolated_env, dates, [
        {"code": "600001.SH", "market": "主板", "closes": _rising(30)},
        # 600009:末日+6%(B4 avoid_flag:close>ma20 & ret>5%)且换手15(A1 hard_cut)同时命中
        {"code": "600009.SH", "market": "主板", "closes": _rising(30, last=0.06),
         "turnover": [5.0] * 29 + [15.0]},
    ])
    _seed_boards(isolated_env, [{"ts_code": "885756.TI", "name": "芯片概念"}],
                 [{"index_code": "885756.TI", "con_code": "600001.SH"}])
    cands = ic.build_intel_candidates(dates[-1], _RULE, parquet_dir=isolated_env.parquet_dir,
                                      db_path=isolated_env.db_path, forced_codes=["600009.SH"])
    by_code = {c.ts_code: c for c in cands}
    assert "600009.SH" in by_code
    flags = set(by_code["600009.SH"].k4_flags)
    assert "A1_turnover_gt_10" in flags and "B4_chase_strong_red" in flags   # 两码都诚实打标
    assert by_code["600009.SH"].intel_rank["yellowCardCount"] == 1          # 但只数 B4 一个黄牌


def test_yellow_card_count_zero_when_k4_db_missing(isolated_env):
    """③-A:隔离库无 K4 行(`load_k4_sections` 空 dict)时,即便某码按 `_DEFAULT_SECTION`
    默认归 avoid_flag 而**不被拦截**(见 `test_k4_no_db_row_defaults_to_avoid_flag_not_
    hard_cut`),`yellowCardCount` 仍应为 **0**——严格判据 `sections.get(code) == "avoid_flag"`
    不接受默认值,DB 里查无此码(不论是因为整个 K4 行缺失,还是因为码本身是不在 DB 的合成码
    如 `A3b_belowyear_bigvol`)一律不计入黄牌数(不数不在 DB 的合成码"这条纪律的同一段代码
    路径)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_market(isolated_env, dates, [
        {"code": "600003.SH", "market": "主板", "closes": _rising(30, last=0.06)},   # B4 命中
    ])
    _seed_boards(isolated_env, [{"ts_code": "885756.TI", "name": "芯片概念"}],
                 [{"index_code": "885756.TI", "con_code": "600003.SH"}])
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    by_code = {c.ts_code: c for c in cands}
    assert "B4_chase_strong_red" in by_code["600003.SH"].k4_flags   # 仍打标(既有行为不变)
    assert by_code["600003.SH"].intel_rank["yellowCardCount"] == 0  # 但不计黄牌(DB 无此行)


# ————————————————————————————————————————————————————————————————
# ⑥ 出 20 只
# ————————————————————————————————————————————————————————————————

def test_outputs_at_most_top_n_candidates(isolated_env):
    """安检通过者 >20 时,只出 20 只(交用户终选)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    stocks = [{"code": f"6000{i:02d}.SH", "market": "主板", "closes": _rising(30, p0=10.0 + i * 0.01)}
              for i in range(25)]
    _seed_market(isolated_env, dates, stocks)
    _seed_boards(isolated_env, [{"ts_code": "885756.TI", "name": "芯片概念"}],
                 [{"index_code": "885756.TI", "con_code": s["code"]} for s in stocks])
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert len(cands) == ic.TOP_N_CANDIDATES == 20
    assert [c.rank for c in cands] == list(range(1, 21))   # rank 连续 1..20


# ————————————————————————————————————————————————————————————————
# ⑦ 两处 K4 镜像 I/O 一致性(② 逐票 vs ③ bulk 直接对拍)
# ————————————————————————————————————————————————————————————————

def test_bulk_and_percode_loaders_agree(isolated_env):
    """`_build_holding_feature_panel` 的默认逐票 loader(② 持仓)与 ③ 注入的 bulk 全市场 loader
    在同一数据上产出**逐位相同**的 K4 特征/命中列——两处镜像一致性直接对拍(阈值单一源,
    只 I/O 不同,plan「性能坑」二选一之(a))。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_market(isolated_env, dates, [
        {"code": "600001.SH", "market": "主板", "closes": _rising(30)},
        {"code": "600002.SH", "market": "主板", "closes": _rising(30, last=0.06), "turnover": [5.0] * 29 + [12.0]},
        {"code": "300003.SZ", "market": "创业板", "closes": _rising(30)},
    ])
    codes = ["600001.SH", "600002.SH", "300003.SZ"]
    td = dates[-1]
    per_code = hk._build_holding_feature_panel(codes, td, isolated_env.parquet_dir)
    bulk = hk._build_holding_feature_panel(codes, td, isolated_env.parquet_dir, load_fn=ic._bulk_load_codes_table)
    assert not per_code.is_empty() and not bulk.is_empty()
    a = per_code.sort("ts_code")
    b = bulk.sort("ts_code").select(a.columns)
    assert a.equals(b)
    # 命中列确实被算出来(不是两边恰好都空的弱重合):600002.SH 应命中 A1(换手12)与 B4(+6%)。
    row = {r["ts_code"]: r for r in a.to_dicts()}["600002.SH"]
    assert row["_hit_A1"] is True and row["_hit_B4"] is True


# ————————————————————————————————————————————————————————————————
# ⑧ 纪律红绿灯仍与报告同码(候选解耦、纪律核对不变)——§3.8 落地核对
# ————————————————————————————————————————————————————————————————

def test_watchlist_discipline_still_k1_while_candidates_decoupled(isolated_env):
    """§3.8 新表述落地核对:候选生成解耦(GEM 入候选),但**自选体检纪律红绿灯仍与报告同码**
    ——同一只创业板票在自选体检里被 K1 `forbid_high_elasticity` 红灯(禁买),在候选情报管线里
    却因解耦而入选。证明「候选解耦、纪律核对仍 K1 同码」两者并存。"""
    from neckline.report.watchlist_check import score_watchlist
    from neckline.strategy.features import build_research_panel
    from neckline.strategy.momentum import MomentumConfig

    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_market(isolated_env, dates, [
        {"code": "300002.SZ", "market": "创业板", "closes": _rising(30, last=-0.01)},   # 末日回调(K1 pullback 触发)
    ])
    _seed_boards(isolated_env, [{"ts_code": "885756.TI", "name": "芯片概念"}],
                 [{"index_code": "885756.TI", "con_code": "300002.SZ"}])
    td = dates[-1]
    # 候选情报管线:GEM 解耦纳入
    cand_codes = _codes(ic.build_intel_candidates(td, _RULE,
                                                  parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path))
    assert "300002.SZ" in cand_codes
    # 自选体检:同一票纪律红绿灯仍走 K1(forbid_high_elasticity)→ 红灯禁买(与报告同码,不受候选解耦影响)
    cfg = MomentumConfig(**_RULE["config"])
    panel = build_research_panel(td, td, with_forward=False, parquet_dir=isolated_env.parquet_dir)
    items = score_watchlist(panel, cfg, [{"ts_code": "300002.SZ", "name": "示例", "pinned": False, "source": "manual"}],
                            db_path=isolated_env.db_path)
    it = {i.ts_code: i for i in items}["300002.SZ"]
    assert it.green_light is False
    assert any("高弹" in d or "创业板" in d for d in it.disqualifiers)


# ————————————————————————————————————————————————————————————————
# ⑨ 常驻板块保底名额(用户 2026-07-26 拍板:每常驻板块保底 2 只)
# ————————————————————————————————————————————————————————————————

def test_quota_two_per_permanent_board_all_present_total_20(isolated_env):
    """① 五常驻板块各有 ≥2 合格票时:每个板块保底恰 2 只(共 10 只 quota)、总数仍 20、
    其余 10 只按情报排序竞争入选(source=competition)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    prefixes = {"芯片概念": "6001", "创新药": "6002", "储能": "6003", "机器人概念": "6004", "稀土永磁": "6005"}
    board_members, stocks = {}, []
    for name, pfx in prefixes.items():
        codes = [f"{pfx}{j:02d}.SH" for j in range(5)]   # 每板块 5 只
        board_members[name] = codes
        for j, c in enumerate(codes):
            stocks.append({"code": c, "market": "主板", "closes": _rising(30, p0=10.0 + j * 0.01)})
    _seed_market(isolated_env, dates, stocks)
    _seed_permanent(isolated_env, board_members)
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert len(cands) == 20
    quota = [c for c in cands if c.intel_rank["source"] == ic.SOURCE_QUOTA]
    comp = [c for c in cands if c.intel_rank["source"] == ic.SOURCE_COMPETITION]
    assert len(quota) == 10 and len(comp) == 10   # 5 板块 × 2 保底 + 10 竞争
    code_to_board = {c: n for n, cs in board_members.items() for c in cs}
    per_board = Counter(code_to_board[c.ts_code] for c in quota)
    assert all(per_board[n] == ic.QUOTA_PER_PERMANENT_BOARD for n in board_members)   # 每板块恰 2


def test_quota_shortfall_returns_to_common_pool_total_kept(isolated_env):
    """② 某常驻合格票只 1 只 / 0 只时:有几只放几只、缺额退回公共池竞争、总数仍 = top_n。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    board_members = {
        "芯片概念": ["600100.SH"],                                 # 只 1 只 → 保底 1
        "创新药": [],                                              # 0 只(index 有、member 无)→ 保底 0
        "储能": [f"6003{j:02d}.SH" for j in range(5)],             # 5 只 → 保底 2 + 3 退回公共池
    }
    stocks = [{"code": c, "market": "主板", "closes": _rising(30, p0=10.0 + j * 0.01)}
              for cs in board_members.values() for j, c in enumerate(cs)]
    _seed_market(isolated_env, dates, stocks)
    _seed_permanent(isolated_env, board_members)   # 创新药 进 ths_index 但 0 成分
    cands = ic.build_intel_candidates(dates[-1], _RULE, top_n=6,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert len(cands) == 6                                          # 缺额退回后仍填满 top_n
    code_to_board = {c: n for n, cs in board_members.items() for c in cs}
    quota = [c for c in cands if c.intel_rank["source"] == ic.SOURCE_QUOTA]
    comp = [c for c in cands if c.intel_rank["source"] == ic.SOURCE_COMPETITION]
    assert "600100.SH" in [c.ts_code for c in quota]                # 芯片仅 1 只合格 → 保底 1
    assert sum(1 for c in quota if code_to_board[c.ts_code] == "芯片概念") == 1
    assert all(code_to_board.get(c.ts_code) != "创新药" for c in cands)   # 创新药 0 合格 → 无候选
    assert sum(1 for c in quota if code_to_board[c.ts_code] == "储能") == 2
    assert len(quota) == 3 and len(comp) == 3                       # 保底 3 + 缺额退回竞争 3 = 6


def test_hard_cut_not_rescued_by_quota(isolated_env):
    """③ hard_cut 命中的票**绝不因保底被捞回**(保底只从过完 ②卫生线 + ③K4 hard_cut 的池选)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_k4(isolated_env)
    _seed_market(isolated_env, dates, [
        {"code": "600100.SH", "market": "主板", "closes": _rising(30)},                       # 干净
        {"code": "600101.SH", "market": "主板", "closes": _rising(30), "turnover": [5.0] * 29 + [15.0]},  # A1 hard_cut
    ])
    _seed_permanent(isolated_env, {"芯片概念": ["600100.SH", "600101.SH"]})
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    codes = _codes(cands)
    assert "600100.SH" in codes and "600101.SH" not in codes       # hard_cut 不因保底捞回
    assert _src(cands)["600100.SH"] == ic.SOURCE_QUOTA             # 干净票保底入选


def test_shared_stock_takes_single_quota_slot(isolated_env):
    """④ 一票同属两个常驻板块:只占**一个**保底名额,由配置顺序在前的板块认领,另一板块靠其余
    成分凑满 2(不重复计数把名额吃空)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_market(isolated_env, dates, [
        {"code": c, "market": "主板", "closes": _rising(30)}
        for c in ["600100.SH", "600101.SH", "600102.SH", "600200.SH", "600201.SH"]
    ])
    _seed_permanent(isolated_env, {
        "芯片概念": ["600100.SH", "600101.SH", "600102.SH"],   # 配置在前:认领 600100/600101(code 序)
        "创新药":   ["600100.SH", "600200.SH", "600201.SH"],   # 共享 600100 → 靠 600200/600201 凑满 2
    })
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    codes = _codes(cands)
    src = _src(cands)
    assert codes.count("600100.SH") == 1                           # 无重复(只占一个名额)
    assert src["600100.SH"] == ic.SOURCE_QUOTA                     # 由「芯片概念」(配置在前)认领
    # 创新药靠 600200/600201 凑满 2 → 证明 600100 未被创新药重复占名额(否则 600201 不会是 quota)
    assert src["600200.SH"] == ic.SOURCE_QUOTA
    assert src["600201.SH"] == ic.SOURCE_QUOTA


def test_selection_source_marked_in_intel_rank(isolated_env):
    """⑤ 保底票在出参里**可识别来源**:`intel_rank.source` = quota / competition,每只都带标记。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    stocks = [{"code": f"6001{j:02d}.SH", "market": "主板", "closes": _rising(30, p0=10.0 + j * 0.01)}
              for j in range(5)]
    _seed_market(isolated_env, dates, stocks)
    _seed_permanent(isolated_env, {"芯片概念": [s["code"] for s in stocks]})
    cands = ic.build_intel_candidates(dates[-1], _RULE, top_n=4,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert len(cands) == 4
    assert all("source" in c.intel_rank for c in cands)            # 每只都有来源标记
    quota = [c for c in cands if c.intel_rank["source"] == ic.SOURCE_QUOTA]
    comp = [c for c in cands if c.intel_rank["source"] == ic.SOURCE_COMPETITION]
    assert len(quota) == 2 and len(comp) == 2                      # 单板块:保底 2 + 竞争 2
    assert {c.intel_rank["source"] for c in cands} == {ic.SOURCE_QUOTA, ic.SOURCE_COMPETITION}


# ————————————————————————————————————————————————————————————————
# ⑩ 行业闸(用户 2026-07-26 拍板方案二:行业当闸 + 概念当题材;2026-07-27 审计发现「板内占比」
#    判据统计量用错,改判据为 lift〔富集度〕——真实反例锁死 + 误杀回归,见 `_seed_market` 的
#    `market_filler` 说明:本组用例需要精确摆布板内/全市场比例,一律传 `market_filler=False`)
# ————————————————————————————————————————————————————————————————

def test_industry_gate_blocks_medical_distribution_from_robotics(isolated_env):
    """① 真实反例:九州通(600998.SH)/重药控股(000950.SZ)行业=医药商业——医药商业在全市场本就
    常见,相对全市场**并不富集**(lift<2.0)→ **不得因保底进入机器人概念栏**;③ 主导行业
    (专用机械,全市场稀缺、板内相对富集)的票正常入选保底。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    reps = ["600300.SH", "600301.SH"]     # 专用机械(全市场稀缺)有价 → 应保底
    bad = ["600998.SH", "000950.SZ"]      # 医药商业 有价 → 应被行业闸挡出机器人
    # 全市场追加 50 只「医药商业」(市场本就常见,只是不在机器人概念板内)——board_share(医药商业)
    # =2/4=50%、market_share(医药商业)=52/54≈96.3%,lift≈0.52<2.0(不富集,挡);board_share(专用
    # 机械)=2/4=50%、market_share(专用机械)=2/54≈3.7%(全市场稀缺),lift≈13.5≥2.0(富集,过)。
    extra_medical = [f"6004{j:02d}.SH" for j in range(50)]
    _seed_market(isolated_env, dates,
                 [{"code": c, "market": "主板", "closes": _rising(30), "industry": "专用机械"} for c in reps]
                 + [{"code": c, "market": "主板", "closes": _rising(30), "industry": "医药商业"} for c in bad],
                 market_filler=False)
    _seed_industry_only(isolated_env, [{"code": c, "industry": "医药商业"} for c in extra_medical])
    _seed_permanent(isolated_env, {"机器人概念": reps + bad})
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    codes = _codes(cands)
    assert "600998.SH" not in codes and "000950.SZ" not in codes       # 医药商业被行业闸挡出机器人栏
    quota = [c for c in cands if c.intel_rank["source"] == ic.SOURCE_QUOTA]
    assert {c.ts_code for c in quota} == set(reps)                     # 主导行业(专用机械)票正常保底入选
    assert cands[0].intel_rank["industry"] == "专用机械"               # 出参带 industry(客户端说清凭什么在此栏)


def test_industry_gate_blocks_food_stock_from_rare_earth(isolated_env):
    """② 真实反例:中炬高新(食品)——食品在全市场本就常见,相对全市场**并不富集**(lift<2.0)
    → **不得进稀土永磁栏**;矿物制品(全市场稀缺、板内富集)入选。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    reps = ["600400.SH", "600401.SH"]     # 矿物制品(全市场稀缺)有价
    bad = ["600872.SH"]                    # 中炬高新 食品 有价
    # 全市场追加 10 只「食品」(食品本就常见,只是不在稀土永磁板内)——board_share(矿物制品)=
    # 2/3≈66.7%、market_share(矿物制品)≈2/13≈15.4%,lift≈4.3≥2.0(富集,过);board_share(食品)
    # =1/3≈33.3%、market_share(食品)≈11/13≈84.6%,lift≈0.39<2.0(食品全市场更常见,不富集,挡)。
    extra_food = [f"6005{j:02d}.SH" for j in range(10)]
    _seed_market(isolated_env, dates,
                 [{"code": c, "market": "主板", "closes": _rising(30), "industry": "矿物制品"} for c in reps]
                 + [{"code": "600872.SH", "market": "主板", "closes": _rising(30), "industry": "食品"}],
                 market_filler=False)
    _seed_industry_only(isolated_env, [{"code": c, "industry": "食品"} for c in extra_food])
    _seed_permanent(isolated_env, {"稀土永磁": reps + bad})
    codes = _codes(ic.build_intel_candidates(dates[-1], _RULE,
                                             parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path))
    assert "600872.SH" not in codes                                    # 食品(厨邦酱油)被挡出稀土永磁栏
    assert "600400.SH" in codes and "600401.SH" in codes               # 矿物制品(富集,主导)入选


def test_industry_gate_recovers_long_tail_industry_via_lift(isolated_env):
    """⑥ 误杀回归(2026-07-27 share→lift 修复,审计发现旧判据用错统计量):新型电力票板内占比
    <5%(旧 share 闸会挡)但相对全市场显著富集(lift≥2.0)→ 新判据下不再被行业闸挡出储能栏
    (复刻真实 2026-07-22 场景:储能/新型电力 板内2.3%/全市场0.6%/lift4.1,21 只曾被旧闸误杀)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    tail = ["600700.SH", "600701.SH"]                 # 新型电力(长尾,板内少数)有价 → 应过闸入候选
    reps = [f"6001{j:02d}.SH" for j in range(58)]     # 电气设备(板内多数,凑分母,无需有价)
    filler = [f"700{j:03d}.SZ" for j in range(120)]   # 全市场填充(另一行业,压低新型电力全市场占比)
    _seed_market(isolated_env, dates,
                 [{"code": c, "market": "主板", "closes": _rising(30), "industry": "新型电力"} for c in tail],
                 market_filler=False)
    _seed_industry_only(isolated_env, [{"code": c, "industry": "电气设备"} for c in reps])
    _seed_industry_only(isolated_env, [{"code": c, "industry": "填充行业"} for c in filler])
    # 储能板内:新型电力 2/60≈3.3%(<5%,旧 share 闸会挡);全市场:2/180≈1.1%;lift≈3.0(≥2.0,新闸放行)
    _seed_permanent(isolated_env, {"储能": tail + reps})
    codes = _codes(ic.build_intel_candidates(dates[-1], _RULE,
                                             parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path))
    assert "600700.SH" in codes and "600701.SH" in codes   # 长尾富集票不再被旧 5% 板内占比线误杀


def test_industry_gate_recovers_it_equipment_via_lift(isolated_env):
    """⑦ 误杀回归(2026-07-27 share→lift 修复):IT设备票板内占比 <5%(旧闸会挡)但相对全市场
    富集(lift≥2.0)→ 新判据下不再被行业闸挡出芯片概念栏(复刻真实 2026-07-22 场景:芯片概念/
    IT设备 板内3.4%/全市场1.5%/lift2.3,31 只曾被旧闸误杀)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    tail = ["600800.SH", "600801.SH"]                 # IT设备(长尾,板内少数)有价 → 应过闸入候选
    reps = [f"6002{j:02d}.SH" for j in range(57)]     # 半导体(板内多数,凑分母,无需有价)
    filler = [f"701{j:03d}.SZ" for j in range(89)]    # 全市场填充(另一行业,压低IT设备全市场占比)
    _seed_market(isolated_env, dates,
                 [{"code": c, "market": "主板", "closes": _rising(30), "industry": "IT设备"} for c in tail],
                 market_filler=False)
    _seed_industry_only(isolated_env, [{"code": c, "industry": "半导体"} for c in reps])
    _seed_industry_only(isolated_env, [{"code": c, "industry": "填充行业"} for c in filler])
    # 芯片概念板内:IT设备 2/59≈3.4%(<5%,旧 share 闸会挡);全市场:2/148≈1.35%;lift≈2.5(≥2.0,新闸放行)
    _seed_permanent(isolated_env, {"芯片概念": tail + reps})
    codes = _codes(ic.build_intel_candidates(dates[-1], _RULE,
                                             parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path))
    assert "600800.SH" in codes and "600801.SH" in codes   # 长尾富集票不再被旧 5% 板内占比线误杀


def test_industry_gate_no_industry_blocked_and_audited(isolated_env, caplog):
    """④ 无 industry 的票**不通过闸**(保守),且**被审计记录**(落日志,不静默丢);主导行业票正常入选。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_market(isolated_env, dates, [
        {"code": "600100.SH", "market": "主板", "closes": _rising(30), "industry": "半导体"},   # 主导 → 入选
        {"code": "600101.SH", "market": "主板", "closes": _rising(30), "industry": None},         # 无行业 → 挡
    ])
    _seed_permanent(isolated_env, {"芯片概念": ["600100.SH", "600101.SH"]})
    with caplog.at_level(logging.INFO, logger="neckline.report.intel_candidates"):
        codes = _codes(ic.build_intel_candidates(dates[-1], _RULE,
                                                 parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path))
    assert "600100.SH" in codes and "600101.SH" not in codes           # 无行业票被闸挡
    assert any("行业闸" in r.message and "无 industry" in r.message for r in caplog.records)   # 审计留痕


def test_dominant_industries_lift_threshold_boundary():
    """⑤ 阈值边界(2026-07-27 share→lift 改判据):lift 恰 2.0 **通过**、1.9 **不通过**
    (`_dominant_industries` 纯函数直测,`market_shares` 手写死,免 seed)。"""
    # 100 成员:A×20(板内20%)、B×19(板内19%),其余填充。全市场占比手写死 A/B 均 10%:
    # lift(A)=20%/10%=2.0(恰过闸);lift(B)=19%/10%=1.9(恰不过)。
    members, industry_of = [], {}
    for i in range(20):
        c = f"A{i}"; members.append(c); industry_of[c] = "行业A"
    for i in range(19):
        c = f"B{i}"; members.append(c); industry_of[c] = "行业B"
    for i in range(61):
        c = f"F{i}"; members.append(c); industry_of[c] = f"填充{i}"
    assert len(members) == 100
    market_shares = {"行业A": 0.10, "行业B": 0.10}
    dom = ic._dominant_industries(members, industry_of, market_shares, min_lift=ic.INDUSTRY_GATE_MIN_LIFT)
    assert "行业A" in dom          # 20%/10% = lift 2.0 ≥ 2.0 → 主导
    assert "行业B" not in dom       # 19%/10% = lift 1.9 < 2.0 → 非主导


# ————————————————————————————————————————————————————————————————
# ⑪ 常驻板块状态诊断(用户 2026-07-26 拍板:0 只/不足 2 只必须带「为什么」)
# ————————————————————————————————————————————————————————————————

def test_permanent_board_status_explains_empty_and_shortfall(isolated_env):
    """0 只与不足 2 只两种情形,`intelRank.permanentBoardStatus` 的数字与实际筛选漏斗一致
    (稀土永磁复刻 2026-07-22 真实场景:成员多数行业相对全市场并不富集〔食品全市场本就常见〕
    被行业闸挡、唯一过闸的又命中 K4 hard_cut → 0 只;芯片 1 clean + 1 hardcut → 不足 2 只),
    文案说清「为什么」不静默空白。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_k4(isolated_env)
    # 芯片概念:600100 半导体 clean → 保底;600101 半导体 但换手15 命中 A1 hard_cut → 不足 2 只
    # 稀土永磁:600200/600201 食品(全市场常见、不富集 → 行业闸挡);600202 矿物制品(全市场稀缺、
    # 富集)过闸 但换手15 hardcut → 0 只
    _seed_market(isolated_env, dates, [
        {"code": "600100.SH", "market": "主板", "closes": _rising(30), "industry": "半导体"},
        {"code": "600101.SH", "market": "主板", "closes": _rising(30), "industry": "半导体", "turnover": [5.0] * 29 + [15.0]},
        {"code": "600200.SH", "market": "主板", "closes": _rising(30), "industry": "食品"},
        {"code": "600201.SH", "market": "主板", "closes": _rising(30), "industry": "食品"},
        {"code": "600202.SH", "market": "主板", "closes": _rising(30), "industry": "矿物制品", "turnover": [5.0] * 29 + [15.0]},
    ], market_filler=False)
    # 全市场追加 10 只「食品」(食品本就常见,不在稀土永磁板内)——稀土永磁板内:矿物制品 1/3≈
    # 33%、食品 2/3≈67%;全市场(含芯片概念的半导体 2 只做背景):矿物制品 1/15≈6.7%(lift≈5.0
    # ≥2 过)、食品 12/15=80%(lift≈0.83<2 挡);芯片概念板内 100% 半导体,全市场 2/15≈13.3%,
    # lift≈7.5≥2 过。
    extra_food = [f"6003{j:02d}.SH" for j in range(10)]
    _seed_industry_only(isolated_env, [{"code": c, "industry": "食品"} for c in extra_food])
    _seed_permanent(isolated_env, {
        "芯片概念": ["600100.SH", "600101.SH"],
        "稀土永磁": ["600200.SH", "600201.SH", "600202.SH"],
    })
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert cands, "至少 600100 应作芯片保底入选,candidates 不应空(否则状态挂不上)"
    status = {s["board"]: s for s in cands[0].intel_rank["permanentBoardStatus"]}
    assert set(status) == {"芯片概念", "稀土永磁"}                       # 两个已解析常驻各一条

    # 芯片概念:不足 2 只(1 clean 保底 + 1 命中 K4 hard_cut)
    xp = status["芯片概念"]
    assert xp["surviveCount"] == 2 and xp["industryGatePass"] == 2
    assert xp["industryGateBlocked"] == 0 and xp["hardCutBlocked"] == 1
    assert xp["quotaFilled"] == 1
    assert "K4 安检拦截" in xp["note"]                                  # 说清不足 2 只的原因

    # 稀土永磁:0 只(2 食品行业不属主导被挡 + 1 矿物制品过闸但 hardcut)
    rr = status["稀土永磁"]
    assert rr["surviveCount"] == 3 and rr["industryGatePass"] == 1
    assert rr["industryGateBlocked"] == 2 and rr["hardCutBlocked"] == 1
    assert rr["quotaFilled"] == 0
    assert "行业不属本板块主导行业" in rr["note"] and "K4 安检拦截" in rr["note"]
    assert "宁缺毋滥" in rr["note"]                                     # 0 只明标非静默空白


def test_permanent_board_status_full_board_concise_note(isolated_env):
    """满额(2 只)板块的状态:数字齐全、文案简述(不需要「为什么」)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_market(isolated_env, dates, [
        {"code": f"6001{j:02d}.SH", "market": "主板", "closes": _rising(30, p0=10.0 + j * 0.01), "industry": "半导体"}
        for j in range(4)
    ])
    _seed_permanent(isolated_env, {"芯片概念": [f"6001{j:02d}.SH" for j in range(4)]})
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    st = {s["board"]: s for s in cands[0].intel_rank["permanentBoardStatus"]}["芯片概念"]
    assert st["surviveCount"] == 4 and st["industryGatePass"] == 4
    assert st["industryGateBlocked"] == 0 and st["hardCutBlocked"] == 0 and st["quotaFilled"] == 2
    assert "保底 2 只" in st["note"] and "为什么" not in st["note"]


# ————————————————————————————————————————————————————————————————
# ④ 排序输入保险丝(v1.3.5;2026-07-27 生产事故:资金流一炸掀翻整份报告)
# ————————————————————————————————————————————————————————————————

def test_sector_moneyflow_failure_degrades_not_crashes(isolated_env, monkeypatch, caplog):
    """**保险丝直接断言**:板块资金流(④ 排序输入)抛异常时,候选照出、不掀翻报告,
    且**留痕不静默**(WARNING 日志 + 降级为 available=False)。

    2026-07-27 生产真踩:`moneyflow_dc` 分区 schema 分裂 → 该调用抛 SchemaError →
    整个 `build_report` 崩 → 当日无报告。`pipeline.py` 的 C2 展示节早有同款降级,
    唯独候选管线内部这次裸奔。这条测试锁死补上的保险丝不被后人拆掉。
    """
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_market(isolated_env, dates, [
        {"code": "600001.SH", "market": "主板", "closes": _rising(30)},
    ])
    _seed_boards(isolated_env, [{"ts_code": "885756.TI", "name": "芯片概念"}],
                 [{"index_code": "885756.TI", "con_code": "600001.SH"}])

    def _boom(*a, **k):
        raise pl.exceptions.SchemaError(
            "data type mismatch for column pct_change: incoming: Float64 != target: String"
        )

    monkeypatch.setattr(ic, "compute_sector_moneyflow", _boom)
    with caplog.at_level(logging.WARNING, logger="neckline.report.intel_candidates"):
        cands = ic.build_intel_candidates(dates[-1], _RULE,
                                          parquet_dir=isolated_env.parquet_dir,
                                          db_path=isolated_env.db_path)

    assert "600001.SH" in _codes(cands)          # 候选照出,报告不崩
    assert any("板块资金流" in r.getMessage() and "降级" in r.getMessage()
               for r in caplog.records)          # 留痕不静默


def test_sector_moneyflow_unavailable_yields_no_flow_but_same_candidates(isolated_env, monkeypatch):
    """降级后情报排序只是**少一维**(sectorFlow 为空),候选集合本身不因此改变——
    证明资金流是可选输入而非候选生成的必要条件。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    _seed_market(isolated_env, dates, [
        {"code": "600001.SH", "market": "主板", "closes": _rising(30)},
        {"code": "600002.SH", "market": "主板", "closes": _rising(30, p0=12.0)},
    ])
    _seed_boards(isolated_env, [{"ts_code": "885756.TI", "name": "芯片概念"}],
                 [{"index_code": "885756.TI", "con_code": "600001.SH"},
                  {"index_code": "885756.TI", "con_code": "600002.SH"}])
    _seed_moneyflow(isolated_env, dates[-1], {"600001.SH": 5000.0, "600002.SH": 100.0})

    normal = set(_codes(ic.build_intel_candidates(
        dates[-1], _RULE, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)))

    monkeypatch.setattr(ic, "compute_sector_moneyflow",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("资金流挂了")))
    degraded = set(_codes(ic.build_intel_candidates(
        dates[-1], _RULE, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)))

    assert normal == degraded and normal
