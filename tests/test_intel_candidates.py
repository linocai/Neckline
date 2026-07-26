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
