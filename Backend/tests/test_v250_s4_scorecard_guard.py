"""V2.5.0 S4 覆盖率成绩线的**结构性**守门(PROJECT_PLAN §5.8.1 / §6 S4 验收)。

本文件锁四件事:

| # | 断言 | 出处 |
|---|---|---|
| 1 | 🔴 `scorecard/**` **零 import `neckline.k9`** —— 尺子不许读被量的东西 | §5.8.1 |
| 2 | 🔴 `coverage_all` 的整条计算路径读不到任何 §8 待标定参数 | §6 S4 验收 |
| 3 | 漏检归因是**闭合枚举**,⛔ 代码里不许现编归因字符串 | §5.8.1 |
| 4 | ⛔ 成绩线之间不合并:scorecard 存储层无「行业分 + 选票分」的合计字段(G13 的提前落位) | §5.8.2 |

⚠ 第 4 条这一版只能守住**它现在有的东西**(覆盖率两张表)。清单成绩五指标的
`listing.py` 归 S17,那时要把 G13 的断言补全到那张表上。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import List, Set, Tuple

import pytest

from neckline.scorecard import coverage as cov
from tests import guard_scan

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "neckline"
_SCORECARD = sorted((_PKG / "scorecard").rglob("*.py"))

#: 覆盖率是**参数包回来之前唯一能跑起来的成绩线**,做成尺子。它一旦读到策略参数,
#: 就不再是独立于策略的尺子了(§5.8.1)。
FORBIDDEN_FOR_SCORECARD: Tuple[str, ...] = ("neckline.k9",)

#: §8 待标定参数的关键词。归因值(如 `excluded_by_boundary`)是**转述** D−1 disposition
#: 里已经写好的结论,不是本层判的 —— 故这里禁的是「读参数」,不是「提到边界」。
PARAM_WORD_ROOTS: Tuple[str, ...] = (
    "minMembers", "heatAbsentPolicy", "relaySource", "relayScoring",
    "newListingDays", "liquidityBottomPct", "spikeFade", "upsideRoomMechDays",
    "K9Params", "load_params",
)


def test_scan_covers_the_scorecard_package():
    names = {p.name for p in _SCORECARD}
    assert {"coverage.py", "store.py"} <= names


def test_scorecard_never_imports_the_strategy_layer():
    """🔴 尺子不许读被量的东西。策略侧的信息只能经 `k9_disposition` 这条**数据**
    通道进来(`DispositionRow` 是本层自己的 DTO),⛔ 不通过 import 进来。

    🔴 扫描器走 `tests/guard_scan.py`(S15 收敛):本文件原来抄了一份跳过相对 import
    的 `_imported_modules`,`from ..k9 import ranking` 一行就能穿过去。"""
    hits = guard_scan.import_hits(_SCORECARD, FORBIDDEN_FOR_SCORECARD, root=_ROOT)
    assert hits == [], "覆盖率线开始 import 策略层了:\n" + "\n".join(hits)


def test_scorecard_source_mentions_no_calibration_parameter():
    """连**参数名**都不该在这一层出现 —— 出现了就说明有人开始「顺手判一下边界」。"""
    hits = []
    for path in _SCORECARD:
        src = path.read_text(encoding="utf-8")
        for root in PARAM_WORD_ROOTS:
            if root in src:
                hits.append(f"{path.relative_to(_ROOT)} → {root}")
    assert hits == [], "覆盖率线里冒出了待标定参数:\n" + "\n".join(hits)


def test_compute_day_takes_no_params_argument():
    names = list(inspect.signature(cov.compute_day).parameters)
    assert names == ["pack", "listing", "dispositions"], (
        "`compute_day` 的签名变了 —— `coverage_all` 「不依赖任何待标定数字」这条承诺"
        "靠的就是它**收不下**参数包")


def test_refresh_day_takes_no_params_argument():
    names = set(inspect.signature(cov.refresh_day).parameters)
    assert not any("param" in n.lower() for n in names), names


def test_miss_reasons_are_a_closed_enum():
    assert len(cov.MISS_REASONS) == 6
    assert len(set(cov.MISS_REASONS)) == 6


def test_no_reason_string_is_invented_outside_the_enum():
    """⛔ 归因值是拿来分类统计的,现编一个字符串等于在报表里开一个哑巴分类。

    做法:把 `coverage.py` 里 `_attribute()` 返回的每个字面量收出来,
    断言它们全在 `MISS_REASONS` 里。"""
    src = (_PKG / "scorecard" / "coverage.py").read_text(encoding="utf-8")
    fn = next(
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.FunctionDef) and n.name == "_attribute"
    )
    returned: Set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
            first = node.value.elts[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                returned.add(first.value)
            elif isinstance(first, ast.Name):
                returned.add(first.id)
    unknown = {
        r for r in returned
        if r not in set(cov.MISS_REASONS) and getattr(cov, r, None) not in set(cov.MISS_REASONS)
    }
    assert unknown == set(), f"`_attribute` 返回了枚举外的归因:{unknown}"


def test_scorecard_store_has_no_combined_total_field():
    """G13 的提前落位(§5.8.2):**行业分与选票分必须分开存,⛔ 不给任何合计字段**
    (同 `review/cashflow.py` 刻意不给「账户净变动」的先例)。

    ⚠ 本条现在只能守住覆盖率那两张表;清单成绩五指标归 S17,那时要把它补全。"""
    from neckline.scorecard import store as scorecard_store

    src = Path(inspect.getsourcefile(scorecard_store)).read_text(encoding="utf-8")
    for banned in ("industry_plus_pick", "combined_score", "total_score"):
        assert banned not in src, banned


@pytest.mark.parametrize("table", ["k9_coverage_daily", "k9_coverage_misses"])
def test_new_tables_are_created_on_an_empty_db(tmp_path, table):
    """纯新增表(§9.2:⛔ 不 ALTER、⛔ 不 DROP、⛔ 不 UPDATE 任何 K8 表)。"""
    import sqlite3

    from neckline.db import init_schema

    db = tmp_path / "n.db"
    init_schema(db)
    init_schema(db)              # 幂等重复跑
    conn = sqlite3.connect(str(db))
    try:
        got = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert got == 1
