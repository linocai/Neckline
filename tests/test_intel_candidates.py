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


def _seed_market(env, dates, stocks: list) -> None:
    """`stocks`: [{code, market='主板', closes, turnover=5.0(标量或逐日列表), list_offset=400,
    st=False, name?}]。写 daily/adj/daily_basic + stock_basic(+namechange)。"""
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
        sb.append({"ts_code": s["code"], "name": name, "market": s.get("market", "主板"), "list_date": ld})
        if s.get("st"):
            nc.append({"ts_code": s["code"], "name": name, "start_date": ld})
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

def test_theme_persistence_reversed_freshness_ranking(isolated_env):
    """题材持续天数**反用**:两票资金流相同,所属板块 board_age=1(新鲜)的排在 board_age=3(警惕)
    之前(`_theme_freshness_score`:1>2>3)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    set_intel_watch_boards(["新鲜题材", "老题材"], db_path=isolated_env.db_path)
    _seed_market(isolated_env, dates, [
        {"code": "600001.SH", "market": "主板", "closes": _rising(30)},   # 属「新鲜题材」(age=1)
        {"code": "600002.SH", "market": "主板", "closes": _rising(30)},   # 属「老题材」(age=3)
    ])
    _seed_boards(
        isolated_env,
        [{"ts_code": "880001.TI", "name": "新鲜题材"}, {"ts_code": "880002.TI", "name": "老题材"}],
        [{"index_code": "880001.TI", "con_code": "600001.SH"},
         {"index_code": "880002.TI", "con_code": "600002.SH"}],
        board_daily={"880001.TI": _flat_then_up(30, age=1), "880002.TI": _flat_then_up(30, age=3)},
        dates=dates,
    )
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    by_code = {c.ts_code: c for c in cands}
    assert by_code["600001.SH"].intel_rank["themePersistDays"] == 1
    assert by_code["600002.SH"].intel_rank["themePersistDays"] == 3
    order = _codes(cands)
    assert order.index("600001.SH") < order.index("600002.SH")   # 新鲜排前


def test_sector_moneyflow_strength_ranking(isolated_env):
    """资金流强度排序(一级键):所属板块净流入更高的票排前(题材天数相同)。"""
    dates = business_days(date(2024, 1, 2), 30)
    insert_trade_cal(isolated_env, dates)
    set_intel_watch_boards(["强流题材", "弱流题材"], db_path=isolated_env.db_path)
    _seed_market(isolated_env, dates, [
        {"code": "600001.SH", "market": "主板", "closes": _rising(30)},   # 强流
        {"code": "600002.SH", "market": "主板", "closes": _rising(30)},   # 弱流
    ])
    _seed_boards(
        isolated_env,
        [{"ts_code": "880001.TI", "name": "强流题材"}, {"ts_code": "880002.TI", "name": "弱流题材"}],
        [{"index_code": "880001.TI", "con_code": "600001.SH"},
         {"index_code": "880002.TI", "con_code": "600002.SH"}],
    )
    _seed_moneyflow(isolated_env, dates[-1], {"600001.SH": 5000.0, "600002.SH": 100.0})
    cands = ic.build_intel_candidates(dates[-1], _RULE,
                                      parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    by_code = {c.ts_code: c for c in cands}
    assert by_code["600001.SH"].intel_rank["sectorFlow"] == 5000.0
    assert by_code["600002.SH"].intel_rank["sectorFlow"] == 100.0
    order = _codes(cands)
    assert order.index("600001.SH") < order.index("600002.SH")   # 资金流强排前


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
