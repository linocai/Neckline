"""V2.5.0 S11 / S13 的**结构性**守门(PROJECT_PLAN §10 G7/G13/G18 + §5.9/§5.13,
外加**裁定 17**)。

| # | 断言 |
|---|---|
| G7 扩 | `review/**` 零 import `neckline.llm` / `neckline.search` —— 🔴 **架构 §六:这一层无 LLM 调用** |
| §5.9 | `cashflow` 四分类**仍然没有**「账户净变动」合计字段,也没有把四类相加的代码路径 |
| G13 扩 | **三条成绩线隔离**:`scorecard/**` 零 import `neckline.review`(反向单向);`review/**` 零 import `neckline.scorecard`;`review/**` ⛔ 不往任何 `k9_*` 表写 |
| §5.9 | `review/conclusions.py` **append-only**:`UPDATE`/`DELETE`/`REPLACE` 零命中 |
| 裁定 6 | `legacy_k8.py` **结构上只有 SELECT**;⛔ 不 import `neckline.db`(那条路会 `init_schema` 建表) |
| G18 | `neckline/**` 零 `import whynotme`(AGENTS.md:生产代码不许 import 研究实验室) |
| **裁定 17** | `scripts/daily_update.py` 里 `ths_*` 抓取段**已整段消失**;而**读侧 helper 与已抓 parquet 一个都没删** |
| §12 坑 1 | 装订**不在**每次进板块都会拉的聚合读里;⛔ 逐票 glob 的写法不存在 |

⚠ 本文件是**结构**判据;行为判据在 `test_review_bindery.py` / `test_review_conclusions.py`
/ `test_legacy_k8.py` / `test_export_snapshot.py`。
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path
from typing import List, Set

import pytest

from tests import guard_scan

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "neckline"
_REVIEW = _PKG / "review"
_SCORECARD = _PKG / "scorecard"
_REVIEW_FILES = sorted(_REVIEW.glob("*.py"))
_SCORECARD_FILES = sorted(_SCORECARD.glob("*.py"))
_ALL_PY = sorted(_PKG.rglob("*.py"))


def _code(path: Path) -> str:
    return guard_scan.code_without_docstrings(path)


def test_scanner_sees_the_files_it_claims_to_guard():
    """🔴 一个扫不到东西的闸门永远是绿的 —— 先断言它确实看见了该看的文件。"""
    assert {p.name for p in _REVIEW_FILES} == {
        "__init__.py", "bindery.py", "cashflow.py", "conclusions.py", "handoff.py",
        "material.py", "parse.py", "reconcile.py", "research_artifact.py", "store.py",
    }
    assert {p.name for p in _SCORECARD_FILES} == {
        "__init__.py", "coverage.py", "store.py"}
    assert len(_ALL_PY) > 50


# ══════════════════════════════════════════════════════════════════════════
# 🔴 S11 这一层无 LLM 调用(架构 §六 逐字)
# ══════════════════════════════════════════════════════════════════════════

_LLM_MODULES = ("neckline.llm", "neckline.search", "openai", "anthropic")


class TestReviewLayerHasNoLLM:
    @pytest.mark.parametrize("path", _REVIEW_FILES, ids=lambda p: p.name)
    def test_no_llm_import_anywhere_in_the_review_package(self, path: Path):
        for banned in _LLM_MODULES:
            hits = guard_scan.imports_any(path, banned)
            assert not hits, f"{path.name} import 了 {hits} —— 架构 §六:这一层无 LLM 调用"

    def test_no_llm_task_constant_is_referenced(self):
        """连**任务常量**都不许出现 —— 一个 `TASK_*` 引用就是在为接线做准备。"""
        for path in _REVIEW_FILES:
            src = _code(path)
            assert "TASK_" not in src, f"{path.name} 里出现了 LLM 任务常量"
            assert "get_provider" not in src, f"{path.name} 里出现了 get_provider"

    def test_the_scanner_would_catch_an_llm_import(self, tmp_path):
        """扫描器自检:给它一份**真的**import 了 llm 的文件,它必须报出来。"""
        bait = tmp_path / "bait.py"
        bait.write_text("from neckline.llm import factory\n", encoding="utf-8")
        assert guard_scan.imports_any(bait, "neckline.llm")


# ══════════════════════════════════════════════════════════════════════════
# §5.9 cashflow 四分类:⛔ 没有「账户净变动」合计字段
# ══════════════════════════════════════════════════════════════════════════

class TestCashflowHasNoAccountTotal:
    def test_the_summary_fields_are_exactly_the_four_categories_plus_bookkeeping(self):
        """🔴 蓝图 5.3 / §4.2:「账户金额增加不得直接视为策略收益」。
        字段集**逐字**冻结 —— 想加一个合计字段就得先改这行断言(一次自觉行为)。"""
        from neckline.review.cashflow import CashFlowSummary

        names = [f.name for f in dataclasses.fields(CashFlowSummary)]
        assert names == [
            "week",
            "transfer_in", "transfer_out",     # 转入转出(拆成两个非负数)
            "dividend", "tax", "other",        # 分红 / 税费 / 其他
            "other_event_count",
            "trading_pnl",                     # 交易盈亏(取 FIFO 净盈亏,不重算)
            "event_count",
        ]

    @pytest.mark.parametrize("banned", [
        "account_net", "accountNet", "net_change", "netChange",
        "total_net", "totalNet", "combined", "grand_total",
    ])
    def test_no_account_level_total_field_name_exists(self, banned):
        src = _code(_REVIEW / "cashflow.py")
        assert banned not in src, f"cashflow 里冒出了合计字段 {banned!r}"

    def test_to_dict_exposes_no_account_total_key(self):
        from neckline.review.cashflow import CashFlowSummary

        keys = set(CashFlowSummary(
            week="2026-W34", transfer_in=1.0, transfer_out=0.0, dividend=0.0,
            tax=0.0, other=0.0, other_event_count=0, trading_pnl=None,
            event_count=1).to_dict())
        assert keys == {
            "week", "transferIn", "transferOut", "transferNet", "dividend", "tax",
            "other", "otherEventCount", "tradingPnl", "eventCount", "note"}

    def test_no_code_path_adds_the_four_categories_together(self):
        """AST 判据:模块里不存在把 `dividend` / `tax` / `trading_pnl` 加到一起的表达式。
        ⚠ `transfer_in - transfer_out` 是**转入转出这一类内部**的净额,合法且必须留。"""
        tree = ast.parse((_REVIEW / "cashflow.py").read_text(encoding="utf-8"))
        cross = {"dividend", "tax", "trading_pnl", "other"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.BinOp) or not isinstance(node.op, (ast.Add, ast.Sub)):
                continue
            names = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
            names |= {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            assert len(names & cross) < 2, (
                f"cashflow 里出现了跨类相加/减的表达式(涉及 {sorted(names & cross)})"
                " —— 那正是「把银证转账当策略收益」的那条路")


# ══════════════════════════════════════════════════════════════════════════
# 🔴 三条成绩线互不进入对方的分子分母(架构 §五)
# ══════════════════════════════════════════════════════════════════════════

class TestThreeScorelinesAreIsolated:
    @pytest.mark.parametrize("path", _SCORECARD_FILES, ids=lambda p: p.name)
    def test_scorecard_never_imports_review(self, path: Path):
        """🔴 **这是最要紧的一条方向**:交割单里的成交⛔ 永远不进清单成绩或覆盖率的
        分子分母。`scorecard/**` 连 import 都不许有。"""
        hits = guard_scan.imports_any(path, "neckline.review")
        assert not hits, f"{path.name} import 了 {hits} —— 我的成绩不许流进另外两条线"

    @pytest.mark.parametrize("path", _SCORECARD_FILES, ids=lambda p: p.name)
    def test_scorecard_never_mentions_the_ledger(self, path: Path):
        src = _code(path)
        for word in ("round_trip", "roundTrip", "交割单", "realized_pnl", "cash_flow"):
            assert word not in src, f"{path.name} 里出现了交割单侧的概念 {word!r}"

    @pytest.mark.parametrize("path", _REVIEW_FILES, ids=lambda p: p.name)
    def test_review_never_imports_scorecard(self, path: Path):
        hits = guard_scan.imports_any(path, "neckline.scorecard")
        assert not hits, f"{path.name} import 了 {hits}"

    def test_coverage_cannot_even_receive_trades(self):
        """结构性:覆盖率的计算入口**收不下**交割单 —— 签名里没有那个位置。"""
        import inspect

        from neckline.scorecard import coverage

        params = set(inspect.signature(coverage.compute_day).parameters)
        assert params == {"pack", "listing", "dispositions"}

    @pytest.mark.parametrize("path", _REVIEW_FILES, ids=lambda p: p.name)
    def test_review_never_writes_to_any_k9_table(self, path: Path):
        """🔴 单向:装订**读** `k9_*` 当材料(架构 §六 明文要求),但 ⛔ 一个字都不往
        那边写。扫源码里的写 SQL 字面量。"""
        src = _code(path)
        for stmt in ("INSERT INTO k9_", "UPDATE k9_", "DELETE FROM k9_",
                     "INSERT OR REPLACE INTO k9_", "DROP TABLE k9_"):
            assert stmt not in src, f"{path.name} 里出现了写 k9_* 的 SQL:{stmt}"

    def test_the_binding_reads_k9_material_through_the_store_modules(self):
        """反面自检:装订**确实**读了报告 / 预案 / 清单(否则上面那条「只读不写」
        是在守一个空集)。"""
        src = _code(_REVIEW / "bindery.py")
        assert "load_k9_report_index" in src
        assert "load_latest_range" in src
        assert "load_listing_membership" in src


# ══════════════════════════════════════════════════════════════════════════
# §5.9 结论存档 append-only
# ══════════════════════════════════════════════════════════════════════════

class TestConclusionsAreAppendOnly:
    @pytest.mark.parametrize("stmt", ["UPDATE ", "DELETE FROM", "INSERT OR REPLACE",
                                      "DROP TABLE", "ALTER TABLE"])
    def test_no_mutating_sql_in_the_module(self, stmt):
        src = _code(_REVIEW / "conclusions.py")
        assert stmt not in src, f"conclusions.py 里出现了 {stmt!r} —— 结论只许追加新版本"

    def test_the_table_primary_key_includes_version(self):
        """主键含 `version` 就是那条纪律的牙齿:想改老版本只能 UPDATE,而应用层
        根本没有那条 SQL。"""
        ddl = (_PKG / "db.py").read_text(encoding="utf-8")
        block = ddl.split("CREATE TABLE IF NOT EXISTS review_conclusions")[1].split(");")[0]
        assert "PRIMARY KEY (week, version)" in block


# ══════════════════════════════════════════════════════════════════════════
# 裁定 6 · K8 只读追溯入口
# ══════════════════════════════════════════════════════════════════════════

class TestLegacyEntryIsReadOnly:
    @pytest.mark.parametrize("stmt", ["INSERT", "UPDATE", "DELETE", "REPLACE",
                                      "CREATE", "DROP", "ALTER"])
    def test_no_write_sql_anywhere_in_the_module(self, stmt):
        src = _code(_PKG / "legacy_k8.py")
        assert stmt not in src, (
            f"legacy_k8.py 里出现了 {stmt!r} —— 裁定 6:K8 表保留、**只读**、不迁移、不回填")

    def test_it_does_not_import_the_controlled_write_entrypoint(self):
        """⛔ 不 import `neckline.db`:那条路上的 `connection()` 顺手 `init_schema`,
        而只读入口绝不该给任何库建表。"""
        assert not guard_scan.imports_any(_PKG / "legacy_k8.py", "neckline.db")

    def test_it_opens_the_database_read_only(self):
        src = _code(_PKG / "legacy_k8.py")
        assert "mode=ro" in src

    def test_write_methods_on_the_route_are_405(self, client, AUTH):
        """写方法未注册 → FastAPI 自动 405(⛔ 不是 404:404 会让人以为路径写错了)。"""
        for call in (client.post, client.put, client.delete):
            assert call("/api/v1/legacy/k8/baskets", headers=AUTH).status_code == 405


# ══════════════════════════════════════════════════════════════════════════
# G18 · 生产代码零 import whynotme(AGENTS.md)
# ══════════════════════════════════════════════════════════════════════════

class TestProductionNeverImportsTheLaboratory:
    def test_no_module_under_neckline_imports_whynotme(self):
        bad: List[str] = []
        for path in _ALL_PY:
            if guard_scan.imports_any(path, "whynotme"):
                bad.append(str(path.relative_to(_ROOT)))
        assert not bad, f"生产代码 import 了研究实验室:{bad}"

    def test_the_word_does_not_appear_in_production_source(self):
        """比 import 判据更严一档:连路径拼接都不许有(⛔ 不写 whynotme 的任何目录)。"""
        bad: List[str] = []
        for path in _ALL_PY:
            if "whynotme" in _code(path):
                bad.append(str(path.relative_to(_ROOT)))
        assert not bad, f"生产源码里出现了 whynotme:{bad}"


# ══════════════════════════════════════════════════════════════════════════
# 🔴 裁定 17 · 停抓概念板块,**已抓的数据原地保留**
# ══════════════════════════════════════════════════════════════════════════

_DAILY_UPDATE = _ROOT / "scripts" / "daily_update.py"


class TestRuling17ConceptFetchIsGone:
    def test_the_daily_job_no_longer_fetches_ths_anything(self):
        """🔴 `ths_daily` 5 次/日 + `ths_index`/`ths_member` **395 次连续调用**/周
        ≈ 21,750 次/年,而三表在 S3 之后**零消费方**(K9 §3.0 明写不使用概念板块)。"""
        src = _code(_DAILY_UPDATE)
        for token in ("update_concept_boards", "update_ths_daily", "update_ths_snapshots",
                      "concept_data", "ths_daily", "ths_index", "ths_member"):
            assert token not in src, f"daily_update.py 里还留着 {token!r} —— 裁定 17 已停抓"

    def test_the_function_itself_is_gone(self):
        tree = ast.parse(_DAILY_UPDATE.read_text(encoding="utf-8"))
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        assert "update_concept_boards" not in names

    def test_no_tushare_ths_call_survives_on_any_scheduled_path(self):
        """定时器路径(`scripts/daily_update.py` 及它 import 的 `backfill`)上
        ⛔ 一个 `ts_ths_*` 调用都不许有。"""
        for path in (_DAILY_UPDATE, _ROOT / "scripts" / "backfill.py"):
            src = _code(path)
            for fn in ("ts_ths_index", "ts_ths_member", "ts_ths_daily"):
                assert fn not in src, f"{path.name} 还在调 {fn}"

    def test_the_read_side_helpers_are_kept_not_deleted(self):
        """⚠ 裁定 17 的另一半:**已抓的 parquet 原地保留**(将来解释层可拿概念当
        背景材料)。删掉读写 helper,保留下来的那 21 MB 就没人读得动了 ——
        所以它们**必须还在**。⛔ 这条断言反向锁住「顺手清理」的冲动。"""
        assert (_PKG / "data" / "concept_data.py").is_file()
        from neckline.data import concept_data

        for fn in ("load_ths_daily", "max_ths_daily_date", "concept_index_codes"):
            assert hasattr(concept_data, fn), f"读侧 helper {fn} 被删了(裁定 17 要求保留)"

    def test_nothing_in_the_tree_deletes_the_retained_parquet(self):
        """⛔ 全仓不许有删 `ths_*` parquet 的代码路径(裁定 17:原地保留不删)。"""
        bad: List[str] = []
        for path in _ALL_PY + sorted((_ROOT / "scripts").glob("*.py")):
            src = _code(path)
            if re.search(r"(unlink|remove|rmtree)\s*\(", src) and "ths_" in src:
                bad.append(str(path.relative_to(_ROOT)))
        assert not bad, f"这些文件里既提到 ths_ 又有删除调用,请人工复核:{bad}"


# ══════════════════════════════════════════════════════════════════════════
# §12 坑 1 · 装订的取数容量
# ══════════════════════════════════════════════════════════════════════════

class TestBinderyReadsAreBounded:
    def test_the_aggregate_overview_does_not_bind_materials(self):
        """⚠ 装订要读 parquet 行情,属于「点一下才算」的动作;
        ⛔ 别把它塞进每次进复盘板块都会拉的 `/review/overview`。"""
        src = _code(_PKG / "api" / "app.py")
        overview = src.split("def get_review_overview")[1].split("\ndef ")[0]
        assert "bind_week" not in overview
        assert "bindery" not in overview

    def test_bindery_never_calls_the_single_stock_reader_in_a_loop(self):
        """AST:`get_stock_history` 在 `bindery.py` 里零命中(单票读法一旦进循环,
        15 只票就是 30 次 glob、上万个 parquet footer)。"""
        src = _code(_REVIEW / "bindery.py")
        assert "get_stock_history" not in src
        assert "get_multi_stock_history" in src

    def test_bindery_never_uses_the_full_glob_readers(self):
        """§12 坑 1 逐字:⛔ 不许用 `get_market_slice` / `scan_table_range`。"""
        src = _code(_REVIEW / "bindery.py")
        for fn in ("get_market_slice", "scan_table_range"):
            assert fn not in src

    def test_the_window_has_an_explicit_capacity_ceiling(self):
        from neckline.review import bindery

        assert isinstance(bindery.MAX_WINDOW_SESSIONS, int)
        assert bindery.MAX_WINDOW_SESSIONS > 0

    def test_context_length_constants_are_required_keywords_at_the_call_site(self):
        """⚠ `pre_sessions` / `post_sessions` 是**上下文长度**不是策略参数,但调用方
        必须**显式说**自己要多长(同 S9 `build_inputs(sessions=)` 的姿势)。"""
        import inspect

        from neckline.review import bindery

        sig = inspect.signature(bindery.bind_week)
        for name in ("pre_sessions", "post_sessions"):
            assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
