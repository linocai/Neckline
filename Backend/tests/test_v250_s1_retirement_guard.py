"""V2.5.0 S1「K8 整链退役与两块承重墙搬家」的全仓守门(PROJECT_PLAN §6 S1 验收)。

体例照 `test_v21_retirement_guard.py` / `test_v240_p0_retirement_guard.py`:一个退役
事件一个守门文件,AST 扫描 import 而不是整文件 grep(grep 会把 docstring 里的历史
说明也判红,一个总是误报的守门等于没有守门)。

本文件锁四件事:

1. **退役包零残留** —— `neckline/` 与 `scripts/` 内不得再 import
   `neckline.sentinel` / `neckline.scan` / `neckline.selection` / `neckline.strategy` /
   `neckline.profile`,以及四个顶层退役模块。
2. **两块承重墙搬到位且没有兼容 shim** —— `data/realtime.py` 与 `dedup.py` 必须在新家
   可导入并带着原有 API;⛔ 旧路径不许还能 import 成功(留一个转发 shim 假装文件还在
   原位,等于退役没做完,下一个人照旧路径写代码,包就永远删不干净)。
3. **`sentinel_events` 表名留着** —— PROJECT_PLAN §3.2 定死:包没了、表名不改
   (改名 = 一次迁移风险换零产品价值)。
4. **K8 历史表只读留档** —— 裁定 6:表不删、不迁移、不回填。`init_schema()` 在临时库上
   可重复跑且逐表行数不变;⛔ 全仓不得出现针对这些表的 DROP TABLE。

⛔ **本文件不许放宽**:哪天真要复活某个包,先改这里、让改动是一次自觉行为。
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from typing import List, Set, Tuple

import pytest

from neckline.db import init_schema

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "neckline"
_SCRIPTS = _ROOT / "scripts"
_SCANNED = sorted(_PKG.rglob("*.py")) + sorted(_SCRIPTS.rglob("*.py"))

#: 整包退役(裁定 7 / §4.3)。`neckline.<name>` 及其任意子模块都不许被 import。
RETIRED_PACKAGES: Tuple[str, ...] = (
    "neckline.sentinel",
    "neckline.scan",
    "neckline.selection",
    "neckline.strategy",
    "neckline.profile",
)

#: 单文件退役(持仓整条线 + 自定义提醒 + 决策台账 + 用户动作 + 自然语言提醒解析)。
RETIRED_MODULES: Tuple[str, ...] = (
    "neckline.custom_alerts",
    "neckline.positions_entry",
    "neckline.decision_log",
    "neckline.user_actions",
    "neckline.llm.nl_alert",
)

#: 裁定 6 的只读留档表(表不删、不迁移、不回填)。这份清单是**验收单**,
#: 不是"新表清单" —— 它们全部已无应用层写入方。
LEGACY_READONLY_TABLES: Tuple[str, ...] = (
    "baskets", "basket_members", "basket_cards", "tier_history",
    "gate_evaluations", "out_candidates", "basket_dropped_handoff",
    "basket_stage_handoff", "reports", "industry_strength_daily",
    "positions", "position_plans", "entry_snapshots", "decision_log",
    "custom_alerts", "selection_packs", "strategy_versions",
    "limit_cluster_daily", "corr_matrix_daily", "leader_structure_daily",
    "sentinel_events",
)


def _imported_modules(path: Path) -> Set[str]:
    """该文件 import 到的**模块全名**集合(含 `from X import y` 的 `X` 与 `X.y`)。

    只看 import 语句,⛔ 不做整文件字符串匹配 —— docstring 里写「原 `scan/cluster.py`
    搬入」是**历史说明**,不是依赖。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover
        pytest.fail(f"{path} 语法错误")
    out: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:            # 相对 import:本仓全绝对,出现即异常
                out.add("." * node.level + (node.module or ""))
            elif node.module:
                out.add(node.module)
                out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


def _hits(prefixes: Tuple[str, ...]) -> List[str]:
    found = []
    for path in _SCANNED:
        for mod in sorted(_imported_modules(path)):
            for p in prefixes:
                if mod == p or mod.startswith(p + "."):
                    found.append(f"{path.relative_to(_ROOT)} → {mod}")
    return found


# ══════════════════════════════════════════════════════════════════════════
# 1. 退役包 / 退役模块零残留
# ══════════════════════════════════════════════════════════════════════════

def test_retired_packages_have_zero_import_sites():
    hits = _hits(RETIRED_PACKAGES)
    assert hits == [], "退役包仍被 import:\n" + "\n".join(hits)


def test_retired_single_file_modules_have_zero_import_sites():
    hits = _hits(RETIRED_MODULES)
    assert hits == [], "退役模块仍被 import:\n" + "\n".join(hits)


def test_retired_packages_are_physically_gone():
    for pkg in RETIRED_PACKAGES:
        d = _PKG / pkg.split(".", 1)[1]
        assert not d.exists(), f"{d} 还在磁盘上 —— 退役是物理删除,不是停用"
    for mod in RETIRED_MODULES:
        f = _ROOT / (mod.replace(".", "/") + ".py")
        assert not f.exists(), f"{f} 还在磁盘上"


def test_scan_covers_both_trees():
    """防止 glob 失效让本守门形同虚设。"""
    rel = {str(p.relative_to(_ROOT)) for p in _SCANNED}
    assert any(p.startswith("neckline/") for p in rel)
    assert any(p.startswith("scripts/") for p in rel)


# ══════════════════════════════════════════════════════════════════════════
# 2. 两块承重墙搬到位,且旧路径**不留 shim**
# ══════════════════════════════════════════════════════════════════════════

def test_load_bearing_modules_live_in_their_new_homes():
    from neckline import dedup
    from neckline.data import panel, realtime
    from neckline.facts import limitmap

    # 实时源:`Quote` / `DualQuote` / `to_symbol` 是 auction 与 review/parse 的入口。
    for name in ("Quote", "DualQuote", "to_symbol", "get_quotes", "get_quotes_dual"):
        assert hasattr(realtime, name), f"data/realtime.py 缺 {name}"
    # 防重台账:当日只跑一次的两个函数。
    for name in ("already_pushed", "record_pushed"):
        assert hasattr(dedup, name), f"dedup.py 缺 {name}"
    assert hasattr(panel, "build_research_panel")
    # 🔴 V2.5.0 S3 改口径:S1 断言的是搬家后的旧 API `refresh_limit_clusters`
    # (K8 口径:按 `stock_basic.industry` + 概念板块聚类、upsert 进
    # `limit_cluster_daily`)。S3 按**裁定 3** 把 limitmap 切到申万二级、按 K9 §3.0
    # 砍掉概念锚点、按 §5.3.1 改为**纯函数不落表**(涨停簇摘要进
    # `fact_packs.market_json`)。本条守的是「承重墙在不在新家」,故改断新 API;
    # 旧 API 的物理消失由 `test_facts_limitmap.py::test_concept_boards_are_gone_
    # from_the_module_entirely` 逐个点名。
    assert hasattr(limitmap, "compute")


@pytest.mark.parametrize("old_path", [
    "neckline.sentinel.quotes",
    "neckline.sentinel.dedup",
    "neckline.strategy.features",
    "neckline.scan.cluster",
])
def test_old_module_paths_are_not_importable(old_path):
    """⛔ **不许留兼容 shim**(PROJECT_PLAN §6 S1 / 施工纪律 7)。

    留一个转发模块假装文件还在原位,会让下一个人照旧路径继续写代码,
    退役就永远做不完 —— 旧路径必须**直接 ImportError**。"""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(old_path)


def test_to_symbol_still_prefers_the_suffix_over_the_prefix():
    """§12 坑 7:前缀启发式对指数会静默拿错标的(`000001.SH` 上证综指会被判成
    `sz000001` 平安银行)。搬家不许把这条退化掉。"""
    from neckline.data.realtime import to_symbol

    assert to_symbol("000001.SH") == "sh000001"
    assert to_symbol("000001.SZ") == "sz000001"


# ══════════════════════════════════════════════════════════════════════════
# 3. 包没了,表名留着(PROJECT_PLAN §3.2)
# ══════════════════════════════════════════════════════════════════════════

def test_dedup_still_writes_the_sentinel_events_table(tmp_path):
    """改名 = 一次迁移风险换零产品价值。表名钉死在这里。"""
    from datetime import date

    from neckline import dedup

    db = tmp_path / "n.db"
    assert dedup.already_pushed(date(2026, 8, 20), "auction", "", "tick", db_path=db) is False
    dedup.record_pushed(date(2026, 8, 20), "auction", "", "tick", db_path=db)
    assert dedup.already_pushed(date(2026, 8, 20), "auction", "", "tick", db_path=db) is True
    with sqlite3.connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM sentinel_events").fetchone()[0]
    assert n == 1


# ══════════════════════════════════════════════════════════════════════════
# 4. K8 历史表:只读留档(裁定 6)
# ══════════════════════════════════════════════════════════════════════════

def test_init_schema_is_repeatable_and_keeps_every_legacy_table(tmp_path):
    """S1 验收原文:「`init_schema()` 在临时库上可重复跑;旧表行数不变」。"""
    db = tmp_path / "n.db"
    init_schema(db)
    with sqlite3.connect(db) as conn:
        present = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [t for t in LEGACY_READONLY_TABLES if t not in present]
        assert missing == [], f"K8 只读留档表被删了:{missing}(裁定 6:表不删)"
        # 每张表塞一行不进去也无所谓 —— 这里要的是「重复跑不改行数」,
        # 故直接用 `sentinel_events` 做有行的样本,其余表验空表也不被重建。
        conn.execute(
            "INSERT INTO sentinel_events (trade_date, sentinel, ts_code, event_key, "
            "payload_json, pushed_at) VALUES ('20260820','legacy','600519.SH','x','{}','t')")
        conn.commit()
        before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in LEGACY_READONLY_TABLES}

    init_schema(db)          # 第二次
    init_schema(db)          # 第三次

    with sqlite3.connect(db) as conn:
        after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in LEGACY_READONLY_TABLES}
    assert after == before, f"重复 init_schema 改动了行数:{before} → {after}"
    assert before["sentinel_events"] == 1


def test_no_drop_table_against_the_legacy_archive():
    """⛔ 裁定 6:表不删。留档表只许在**建表重写**里被 DROP,不许被单纯删掉。

    SQLite 改约束只能靠「建临时表 → 拷行 → DROP 老表 → RENAME 临时表就位」这套重写
    (`db.py` 里 V2.4.2 那三处正是),故判据不是「有没有 DROP」,而是
    **DROP 之后有没有一句把同名表 RENAME 回来** —— 有 = 重写,没有 = 真删。"""
    offenders = []
    for path in _SCANNED:
        text = path.read_text(encoding="utf-8")
        for tbl in LEGACY_READONLY_TABLES:
            for stmt in (f"DROP TABLE {tbl}", f"DROP TABLE IF EXISTS {tbl}"):
                idx = text.find(stmt)
                while idx != -1:
                    tail = text[idx:idx + 4000]
                    if f"RENAME TO {tbl}" not in tail:
                        offenders.append(f"{path.relative_to(_ROOT)}: {stmt}(其后无 RENAME 回填)")
                    idx = text.find(stmt, idx + 1)
    assert offenders == [], "留档表被真删:\n" + "\n".join(offenders)
