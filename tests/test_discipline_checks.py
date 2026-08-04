"""纪律红绿灯判定项单测(`neckline/report/discipline_checks.py`)。

**为什么有这个文件**(契约线审计 🔵 B6-②,2026-08-04 A6 补):该函数原住
`report/watchlist_check.py`,V2-⑬-11 自选池整链删除时**原地搬家**保住了函数本身,
但它的行为覆盖(原 `test_watchlist_check.py` 约 11 例正负分支)**随消费方一起陪葬**了
—— 现存断言只剩「问询台用的是同一个函数对象」+「选股域那项确实来自
`base_universe_expr()` 本尊」两条**结构性**守门,和一例「*ST 进 risk_flags」。
函数在、断言没了 = 判据改错了没人拦。本文件按**每条硬线正负各一**补回行为覆盖。

**判据来源不在本文件**(CLAUDE.md 纪律):阈值一律从 `MomentumConfig` / `signals.py`
的默认值取,⛔ 不在测试里抄字面量 —— 抄了就变成"测试锁死测试自己写的数",上游改动
反而不会红。选股域四项**刻意不逐项拆**(它们在 `base_universe_expr()` 内部已经 AND
成一个布尔,拆开等于手写第二份),本文件按"每一项各造一个能单独触发它的行"覆盖。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

import polars as pl
import pytest

from neckline.report.discipline_checks import discipline_checks
from neckline.strategy.momentum import MomentumConfig

D = date(2026, 7, 21)

# 现役 v1.3.3 口径:P4/P5/P6 皆 None、拆墙后 forbid_high_elasticity=False
# (与 `strategy_versions` 现役行同形,不是本文件自己发明的一套)。
CFG_LIVE = MomentumConfig(strength="none", buypoint="pullback",
                          forbid_high_elasticity=False, stop_pct=0.05)
# 三条可配禁买 + 高弹墙全开(v1.3 是切换器白名单里的合法回退目标,分支必须还活着)
CFG_ALL_ON = MomentumConfig(strength="none", buypoint="pullback",
                            forbid_green_bigdown=-0.03, forbid_far_from_high=-0.15,
                            forbid_new_days=120, forbid_high_elasticity=True)


def _row(**overrides: Any) -> Dict[str, Any]:
    """一行**干净的**特征行:任何一条硬线都不该命中。逐项测试只改一个字段,
    "改了才红、不改就绿"才说明测的是那一条而不是别的。"""
    base: Dict[str, Any] = {
        "ts_code": "600001.SH", "trade_date": D, "board": "MAIN", "close": 10.0,
        "amount_ma20": 50000.0, "ma20": 9.0, "is_st": False,
        "ret_1d": -0.01, "dist_from_high_20d": -0.02, "days_since_listing": 900,
    }
    base.update(overrides)
    return base


def _hits(cfg: MomentumConfig, row: Dict[str, Any]) -> List[str]:
    """跑一遍判定项,返回**命中的列名**(与 `api/inquiry.py::run_deterministic_checks`
    同一种代入方式:把每条 expr 求值成一列再读那一行)。"""
    checks = discipline_checks(cfg)
    frame = pl.DataFrame([row]).with_columns([expr.alias(col) for col, _label, expr in checks])
    r = frame.row(0, named=True)
    return [col for col, _label, _expr in checks if r.get(col)]


def _labels(cfg: MomentumConfig) -> Dict[str, str]:
    return {col: label for col, label, _expr in discipline_checks(cfg)}


# ══════════════════════════════════════════════════════════════════════════
# 干净行 = 零命中(负例的公共前提;它不成立的话下面每条正例都不说明问题)
# ══════════════════════════════════════════════════════════════════════════

def test_clean_row_trips_nothing_under_live_config():
    assert _hits(CFG_LIVE, _row()) == []


def test_clean_row_trips_nothing_even_with_every_filter_on():
    assert _hits(CFG_ALL_ON, _row()) == []


# ══════════════════════════════════════════════════════════════════════════
# 选股域一条组合原因(`_dq_base` = ~base_universe_expr(),四项各造一个正例)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("field,value,why", [
    ("is_st", True, "ST / 退市风险"),
    ("board", "BSE", "北交所流动性薄"),
    ("close", 1.5, "股价 < 2 元(面值退市区)"),
    ("amount_ma20", 100.0, "20 日均额 < 2000 万"),
    ("ma20", None, "无 MA20(次新未成形)"),
])
def test_base_universe_each_component_trips_the_single_combined_reason(field, value, why):
    hits = _hits(CFG_LIVE, _row(**{field: value}))
    assert hits == ["_dq_base"], f"{why}:期望只触发选股域那一条,实得 {hits}"


def test_base_universe_boundaries_are_inclusive_on_the_pass_side():
    """边界值**恰好达标**算过(`>=`,不是 `>`)—— 阈值从 `base_universe_expr()` 取,
    本测试只锁"边界站在哪一侧"这个语义,不锁具体数字。"""
    assert _hits(CFG_LIVE, _row(close=2.0, amount_ma20=20000.0)) == []
    assert _hits(CFG_LIVE, _row(close=1.99)) == ["_dq_base"]
    assert _hits(CFG_LIVE, _row(amount_ma20=19999.0)) == ["_dq_base"]


def test_base_universe_reason_text_names_what_it_covers():
    """一条组合原因必须把四项都说清楚(它是用户唯一能看到的解释;拆展示粒度的代价
    换来的是不重抄阈值,那就得靠文案把话说全)。"""
    label = _labels(CFG_LIVE)["_dq_base"]
    for token in ("ST", "北交所", "2元", "2000万", "MA20"):
        assert token in label.replace(" ", ""), f"选股域原因文案缺「{token}」:{label}"


# ══════════════════════════════════════════════════════════════════════════
# 三条可配禁买(P4/P5/P6):config 开才在清单里,开了才判
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("col,field,trip,pass_", [
    ("_dq_bigdown", "ret_1d", -0.05, -0.01),            # 绿盘大阴线(≤ -3%)
    ("_dq_farhigh", "dist_from_high_20d", -0.30, -0.02),  # 距 20 日高点过远(≤ -15%)
    ("_dq_new", "days_since_listing", 30, 900),          # 次新(< 120 自然日)
])
def test_each_configurable_filter_positive_and_negative(col, field, trip, pass_):
    assert _hits(CFG_ALL_ON, _row(**{field: trip})) == [col]
    assert _hits(CFG_ALL_ON, _row(**{field: pass_})) == []


@pytest.mark.parametrize("col,field,trip", [
    ("_dq_bigdown", "ret_1d", -0.05),
    ("_dq_farhigh", "dist_from_high_20d", -0.30),
    ("_dq_new", "days_since_listing", 30),
])
def test_configurable_filters_absent_from_checklist_when_config_is_none(col, field, trip):
    """现役 config 把三项置 `None` → **这条判定项根本不进清单**(不是"进了但恒 False")
    —— 两者对用户是同一个结果、对维护者不是:前者说明"这一版没有这条规则"。"""
    assert col not in _labels(CFG_LIVE)
    assert _hits(CFG_LIVE, _row(**{field: trip})) == []


def test_configurable_filter_labels_carry_the_live_threshold():
    """原因文案里的数字必须来自 cfg(动态格式化),不是写死的 —— 换一档阈值,
    用户看到的说法要跟着变。"""
    cfg = MomentumConfig(strength="none", buypoint="pullback", forbid_green_bigdown=-0.07)
    assert "7%" in _labels(cfg)["_dq_bigdown"]


# ══════════════════════════════════════════════════════════════════════════
# 高弹墙(v1.3.3 拆墙后现役不产出;分支保留供回退)
# ══════════════════════════════════════════════════════════════════════════

def test_high_elasticity_wall_is_off_under_live_config_but_alive_when_rolled_back():
    """拆墙(v1.3.3,用户 2026-07-27 拍板)= **config 关掉**,不是删代码:现役下创业板
    零命中;回退到开墙的 config 立刻回来。⛔ 本函数没有任何硬编板块限制。"""
    gem = _row(board="GEM")
    assert _hits(CFG_LIVE, gem) == []
    assert "_dq_elastic" not in _labels(CFG_LIVE)
    assert _hits(CFG_ALL_ON, gem) == ["_dq_elastic"]
    assert _hits(CFG_ALL_ON, _row(board="STAR")) == ["_dq_elastic"]
    assert _hits(CFG_ALL_ON, _row(board="MAIN")) == []


# ══════════════════════════════════════════════════════════════════════════
# 结构守门:清单形状与"命中即警告不拦人"的语义
# ══════════════════════════════════════════════════════════════════════════

def test_every_check_is_a_named_reason_and_a_boolean_expr():
    for col, label, expr in discipline_checks(CFG_ALL_ON):
        assert col.startswith("_dq_") and isinstance(label, str) and label
        assert isinstance(expr, pl.Expr)


def test_multiple_hard_lines_can_trip_together_and_all_are_reported():
    """命中项是**逐条报**的(用户要知道踩了哪几条),⛔ 不许只报第一条。"""
    hits = _hits(CFG_ALL_ON, _row(is_st=True, ret_1d=-0.09, days_since_listing=10))
    assert set(hits) == {"_dq_base", "_dq_bigdown", "_dq_new"}
