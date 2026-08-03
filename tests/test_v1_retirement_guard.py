"""V2-⑬「V1 清理清单」的全仓守门(**十三项逐项一条断言**,plan §五 V2-⑬ 验收条款)。

本文件是 ⑬ 的**机器判据集中处**,三类断言:

  ① **模块已物理删除**:被删的 `.py`/`.swift` 文件确实不在树里,且 `neckline/` 全仓
     零 import(注释里提到旧名字是**允许的**——历史说明与语义留痕不该被守门误伤,
     故一律走 **AST import 扫描**,不是纯文本 grep)。
  ② **停写留档表零写入调用点**:AST 走遍 `neckline/**/*.py` 的 `execute*` 调用,
     禁止 `INSERT INTO / UPDATE / DELETE FROM <表>`(体例照抄 ⑩ 的
     `test_decision_log.py::test_decision_log_table_has_zero_write_call_sites_in_neckline_package`,
     `tests/` 自身的裸 SQL 夹具**不在扫描范围**——那是造历史行用的,合法)。
  ③ **该活下来的确实活着**:⑬ 明文点名"不许陪葬"的几件(`llm/json_block.py`、
     `llm/judge.py::judge_candidate`、`report/board_pool.py`、`report/exec_hint.py`
     纯计算、问询台主体 + `inquiry_log`),以及守门迁移后的新家。

⚠ **`report/board_pool.py` 不是「五常驻」模块**(Plan ⑬-1 落点表里那一行是笔误,已回报):
它是**板块池卫生线**(名称模式闸 + 成分数上限),V2 的 `scan/corr.py`/`scan/cluster.py`/
`scan/seeds.py` 与 `report/intel.py`/`report/sector_moneyflow.py` 五处在用 —— 删了会
把扫描层打断。真正的五常驻保底(`QUOTA_PER_PERMANENT_BOARD` / `_permanent_board_status`)
住在已删除的 `report/intel_candidates.py` 里,已随该模块一并消失(见下面 ⑬-13 一条)。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Set, Tuple

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "neckline"
_PY_FILES = sorted(_PKG.rglob("*.py"))
_EXEC_METHODS = {"execute", "executemany", "executescript"}


# ======================================================================
#  共用扫描器
# ======================================================================

def _imported_names(path: Path) -> Set[str]:
    """该文件 import 进来的模块全名集合(`import a.b` / `from a.b import c` 都算 `a.b`)。
    **只看 import 语句**,注释与字符串里的旧名字不计——历史说明不是残留。"""
    out: Set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


def _import_hits(needle: str) -> List[str]:
    return [
        str(p.relative_to(_ROOT))
        for p in _PY_FILES
        if any(m == needle or m.startswith(needle + ".") for m in _imported_names(p))
    ]


def _sql_literal(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else ""
                       for v in node.values)
    return None


def _write_sql_hits(table: str) -> List[Tuple[str, int, str]]:
    """全仓 `neckline/` 里针对该表的 INSERT/UPDATE/DELETE 调用点(宁可漏报不许误报:
    取不到字面量的动态 SQL 不算命中,同 `test_v2_schema_guard._sql_literal` 既定取向)。"""
    forbidden = (f"INSERT INTO {table}", f"UPDATE {table}", f"DELETE FROM {table}")
    hits: List[Tuple[str, int, str]] = []
    for path in _PY_FILES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else None)
            if name not in _EXEC_METHODS or not node.args:
                continue
            sql = _sql_literal(node.args[0])
            if sql is None:
                continue
            upper = " ".join(sql.upper().split())
            for f in forbidden:
                if f.upper() in upper:
                    hits.append((str(path.relative_to(_ROOT)), node.lineno, f))
    return hits


# ======================================================================
#  ⑬-1 / ⑬-13:20 只单票候选榜 + 五常驻板块保底
# ======================================================================

def test_13_1_candidate_board_and_permanent_quota_are_gone():
    assert not (_PKG / "report" / "intel_candidates.py").exists()
    assert not (_PKG / "report" / "candidates.py").exists()   # `Candidate` 数据类的家
    assert _import_hits("neckline.report.intel_candidates") == []
    assert _import_hits("neckline.report.candidates") == []
    # ⑬-13 五常驻保底:配置存取 + 两个端点全无
    import neckline.settings_store as ss

    for gone in ("get_intel_watch_boards", "set_intel_watch_boards", "DEFAULT_INTEL_WATCH_BOARDS"):
        assert not hasattr(ss, gone), gone
    from neckline.api.app import app

    paths = {r.path for r in app.routes}
    assert not any("intel-boards" in p for p in paths)


def test_13_1_board_pool_survives_because_it_is_not_the_permanent_quota_module():
    """反向守门(Plan 落点表笔误的防线):`report/board_pool.py` **必须还在**且仍被
    V2 扫描层消费 —— 它是板块池卫生线,不是五常驻。"""
    assert (_PKG / "report" / "board_pool.py").exists()
    consumers = {p for p in ("neckline/scan/corr.py", "neckline/scan/cluster.py",
                             "neckline/scan/seeds.py", "neckline/report/intel.py",
                             "neckline/report/sector_moneyflow.py")}
    actual = set(_import_hits("neckline.report.board_pool"))
    assert consumers <= actual, f"board_pool 的 V2 消费方缺失:{consumers - actual}"


# ======================================================================
#  ⑬-2:单票 LLM 审判(`llm_judgments` 停写留档,`judge_candidate` 本体保留)
# ======================================================================

def test_13_2_llm_judgments_table_has_zero_write_call_sites():
    assert _write_sql_hits("llm_judgments") == []


def test_13_2_judge_candidate_itself_survives_as_a_general_tool():
    """⑬-2 明文:`judge_candidate` **本体保留**为「通用 LLM 调用 + 降级链 + verdict
    解析」工具,含 `narrative_splitter` 依赖注入纪律。"""
    import inspect

    from neckline.llm.judge import judge_candidate

    params = inspect.signature(judge_candidate).parameters
    assert "system_prompt" in params
    assert "narrative_splitter" in params, "标签后挂内容时剥离叙述的依赖注入口不许删"


# ======================================================================
#  ⑬-3:单票参考件三件套(`json_block.py` 不陪葬)
# ======================================================================

def test_13_3_reference_plan_modules_are_gone_and_table_is_write_frozen():
    assert not (_PKG / "report" / "reference_plan.py").exists()
    assert not (_PKG / "report" / "reference_plan_store.py").exists()
    assert _import_hits("neckline.report.reference_plan") == []
    assert _import_hits("neckline.report.reference_plan_store") == []
    assert _write_sql_hits("reference_plans") == []


def test_13_3_json_block_survives_and_is_used_by_aggregate_and_card():
    """⑬-3 子条款(2026-08-02 登记):删 `reference_plan.py` 时**只删那层再导出**,
    通用围栏 JSON 解析件 `llm/json_block.py` 必须还在,且 ⑤ 聚合层与 ⑦ 卡生成在用。"""
    assert (_PKG / "llm" / "json_block.py").exists()
    from neckline.llm.json_block import split_narrative_and_reference_json  # noqa: F401

    users = set(_import_hits("neckline.llm.json_block"))
    for must in ("neckline/selection/aggregate.py", "neckline/selection/basket_card.py"):
        assert must in users, f"{must} 应仍在用 json_block(⑤/⑦ 的围栏解析)"


# ======================================================================
#  ⑬-4:exec_hint 展示位删除,纯计算保留
# ======================================================================

def test_13_4_exec_hint_display_gone_but_pure_computation_kept():
    import neckline.report.exec_hint as eh

    assert not hasattr(eh, "attach_exec_hints"), "展示位装配器应已删除"
    assert hasattr(eh, "exec_hints_for"), "四条计算须留一个纯计算出口(并入篮子剧本的输入)"
    for code in (eh.C1_STRONG_MARKET_ORDER, eh.C2_MILD_RED_LOW_VARIANCE,
                 eh.C3_LOW_LIMIT_SELF_AWARE, eh.C4_NO_PULLBACK_BIGRED_MECHANICAL):
        assert isinstance(code, str) and code


# ======================================================================
#  ⑬-5:决策日志强制表单(服务端 ⑩-C 已下线,⑬ 删客户端必填分支)
# ======================================================================

def test_13_5_decision_log_form_required_branches_gone_from_client():
    src = (_ROOT / "client" / "Neckline" / "App" / "AppModel.swift").read_text(encoding="utf-8")
    # `DecisionLogForm.isValid` 只剩「有 code」这一条(五项必填全下线)
    body = src.split("var isValid: Bool {", 1)[1].split("}", 1)[0]
    assert "code" in body
    for gone in ("whyBuy", "whyEntryPrice", "invalidation", "maxChaseChosen"):
        assert gone not in body, f"`{gone}` 不该再出现在提交校验里(⑬-5 强制表单退役)"


# ======================================================================
#  ⑬-6:老四件套残键(P3-27 兑现)
# ======================================================================

def test_13_6_legacy_four_piece_keys_gone_from_contract_and_client():
    from neckline.api.schemas import CandidateOut

    for gone in ("buyPoint", "stop", "target", "invalidation"):
        assert gone not in CandidateOut.model_fields, gone
    swift = (_ROOT / "client" / "Models.swift").read_text(encoding="utf-8")
    cand = swift.split("struct Candidate: Codable", 1)[1].split("\n}\n", 1)[0]
    for gone in ("var buyPoint", "var stop:", "var target:", "var invalidation:"):
        assert gone not in cand, gone


# ======================================================================
#  ⑬-7:盘前 9:26 判定对象换血成篮子成员
# ======================================================================

def test_13_7_precall_judges_basket_members_from_frozen_card_spec():
    import neckline.sentinel.precall as pc

    assert hasattr(pc, "MemberScript") and hasattr(pc, "load_member_scripts")
    mods = _imported_names(_PKG / "sentinel" / "precall.py")
    assert any(m.startswith("neckline.selection.basket_store") for m in mods), "判据源须来自 ⑦ 的冻结卡"
    assert not any(m.startswith("neckline.report.candidates") for m in mods)
    # 阈值未变(V1 原样),推送 kind 仍在
    assert pc.PRECALL_GAP_UP_INVALIDATE == 0.03
    from neckline.notify_kinds import KIND_PRECALL

    assert KIND_PRECALL == "precall"


def test_13_7_precall_does_not_bump_the_verification_ruleset():
    """⛔ 竞价开盘价不是收盘价 —— precall 只借冻结价位,**不许**往 ⑦-b 的条件集里加
    竞价语义(那会污染 ⑧ 的四态判定,且要 bump `VERIFICATION_RULESET_VERSION`)。"""
    from neckline.selection.verification_rules import VERIFICATION_RULESET_VERSION

    assert VERIFICATION_RULESET_VERSION == "verify_ruleset_v1"


# ======================================================================
#  ⑬-8:报告 markdown 候选节 / 参考件展示位
# ======================================================================

def test_13_8_report_render_has_no_candidate_section():
    import neckline.report.render as rd

    for gone in ("_render_candidates", "_render_reference_plan", "_exec_hint_line",
                 "_intel_rank_line", "_k4_flag_line"):
        assert not hasattr(rd, gone), gone
    # 该留的仍在(市场语境 / 持仓体检 / 情报 / 资金流 / 消息面)
    for kept in ("_render_sentiment", "_render_sectors", "_render_holding_check",
                 "_render_intel", "_render_sector_moneyflow", "_render_news_alerts"):
        assert hasattr(rd, kept), kept


# ======================================================================
#  ⑬-9:K1 entry mask 生产 import 清零
# ======================================================================

def test_13_9_build_entry_mask_has_zero_production_import_sites():
    """`strategy/momentum.py::build_entry_mask` 只准留回测 / 研究线调用。

    ⚠ **`MomentumConfig` 不在此列且必须保留** —— 它是 `strategy_versions.rule["config"]`
    的解析目标,熔断 / 周对账 / 持仓时间退出 / entry-suggestion 全靠它;删它等于动
    「纪律外壳」(V2 红线 1)。本条只扫 entry mask 本尊。"""
    hits = []
    for p in _PY_FILES:
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"), filename=str(p))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("strategy.momentum"):
                if any(a.name == "build_entry_mask" for a in node.names):
                    hits.append(f"{p.relative_to(_ROOT)}:{node.lineno}")
    assert hits == [], f"`build_entry_mask` 仍有生产 import:{hits}"


def test_13_9_research_and_backtest_line_still_reaches_the_entry_mask():
    """反向守门:回测 / 研究线**必须还能用** K1(⑬-9 原文「只留回测 / 研究线调用」)。"""
    from neckline.strategy.momentum import MomentumConfig, MomentumStrategy, build_entry_mask  # noqa: F401

    assert callable(build_entry_mask)


# ======================================================================
#  ⑬-10:问询台 forced 海选池通道
# ======================================================================

def test_13_10_inquiry_pool_channel_gone_but_historical_read_kept():
    from neckline.api import stores

    for gone in ("add_to_inquiry_pool", "load_pending_inquiry_codes", "mark_inquiry_pool_consumed"):
        assert not hasattr(stores, gone), gone
    assert hasattr(stores, "load_inquiry_pool"), "周复盘归因要读历史行,只读函数保留"
    assert _write_sql_hits("inquiry_pool") == []


def test_13_10_inquiry_desk_itself_survives():
    """⑬「明确不动」:问询台主体 + `inquiry_log`。"""
    from neckline.api.inquiry import run_deterministic_checks, run_inquiry  # noqa: F401
    from neckline.api.stores import create_inquiry_log, list_inquiry_logs  # noqa: F401
    from neckline.api.app import app

    paths = {r.path for r in app.routes}
    assert any(p.endswith("/inquiry") for p in paths)


# ======================================================================
#  ⑬-11:自选池 + 同花顺对账
# ======================================================================

def test_13_11_watchlist_chain_is_gone():
    assert not (_PKG / "watchlist.py").exists()
    assert not (_PKG / "report" / "watchlist_check.py").exists()
    assert not (_ROOT / "client" / "Neckline" / "Views" / "WatchlistView.swift").exists()
    assert _import_hits("neckline.watchlist") == []
    assert _import_hits("neckline.report.watchlist_check") == []
    assert _write_sql_hits("watchlist") == []
    from neckline.api.app import app

    assert not any("/watchlist" in r.path for r in app.routes)


def test_13_11_discipline_checks_moved_out_instead_of_dying_with_it():
    """守门迁移:纪律判定项是**问询台**(保留件)的判据源,随自选体检删除**搬家**
    到独立模块,不是陪葬。"""
    from neckline.api import inquiry as inq
    from neckline.report.discipline_checks import discipline_checks

    assert inq.discipline_checks is discipline_checks


# ======================================================================
#  ⑬-12:呼吸台账
# ======================================================================

def test_13_12_breathing_ledger_is_gone():
    assert not (_PKG / "breathing.py").exists()
    assert not (_ROOT / "client" / "Neckline" / "Views" / "BreathingLedgerView.swift").exists()
    assert _import_hits("neckline.breathing") == []
    assert _write_sql_hits("breathing_t_trades") == []
    from neckline.api.app import app

    assert not any("/breathing" in r.path for r in app.routes)


def test_13_12_breathing_trial_playbook_tag_survives():
    """反向守门:`BREATHING_TRIAL` 是 **decision_log 的打法标签枚举**,不是台账 ——
    历史决策日志的展示要用,⛔ 别一起删。"""
    from neckline.decision_log import PLAYBOOK_TAG_CODES

    assert "BREATHING_TRIAL" in PLAYBOOK_TAG_CODES


# ======================================================================
#  ⑬-N:信息卡保留改造(不是删除)
# ======================================================================

def test_13_N_info_card_kept_and_repointed_to_basket_members():
    import neckline.report.info_card as ic
    from neckline.api.app import app

    assert (_PKG / "report" / "info_card.py").exists()
    assert any("info-card" in r.path for r in app.routes), "信息卡端点必须保留(D1 已拍板不删)"
    # 改造:三块 + K7 标注件
    assert hasattr(ic, "build_basket_context") and hasattr(ic, "build_member_tags")
    from neckline.api.schemas import InfoCardOut

    for must in ("basket", "tags", "tagsAbsent"):
        assert must in InfoCardOut.model_fields, must
    # 批量摘要装配器(吃 `List[Candidate]`)随候选榜删除
    assert not hasattr(ic, "attach_info_card_summaries")


def test_13_N_K7_info_card_reads_the_single_tag_implementation():
    """⑬-N-K7:**读 `selection/member_tags.py` 同一份实现与同一份文案模板**,
    ⛔ 禁在信息卡侧重写判据(逐位相同的交叉断言在 `test_api_info_card.py`)。"""
    import ast as _ast

    path = _PKG / "report" / "info_card.py"
    assert "member_tags" in path.read_text(encoding="utf-8")
    # 只扫**代码**(剥掉 docstring 与注释),否则"这段文案来自 member_tags"这类说明会被误伤。
    tree = _ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Expr) and isinstance(node.value, _ast.Constant) \
                and isinstance(node.value.value, str):
            node.value.value = ""       # 抹掉 docstring
    code_text = _ast.unparse(tree)
    for banned in ("龙回头位", "双尾警示", "参考、非指令"):
        assert banned not in code_text, f"文案 `{banned}` 只能来自 member_tags 模板,不许在信息卡侧写第二份"


@pytest.mark.parametrize("path", [
    "neckline/sentinel/universe.py",
    "neckline/sentinel/engine.py",
    "neckline/sentinel/precall.py",
    "neckline/sentinel/invalidation.py",
])
def test_13_N_K7_tags_stay_out_of_the_sentinel_tree(path):
    """⑦-K7「四不」的既有防线在 ⑬ 之后仍成立(标注件禁入哨兵目录)——⑬-7 让 precall
    读了篮子卡,这条因此**更该复查一次**。"""
    from neckline.selection.member_tags import ALL_TAG_CODES

    src = (_ROOT / path).read_text(encoding="utf-8")
    for code in ALL_TAG_CODES:
        assert code not in src, f"{path} 出现标注码 {code}"


# ======================================================================
#  跨项:停写留档表在跑完一遍完整管线后「行数不增」(⑬ 验收条款)
# ======================================================================

_FROZEN_TABLES = ("watchlist", "breathing_t_trades", "inquiry_pool",
                  "llm_judgments", "reference_plans", "decision_log")


def test_frozen_tables_gain_no_rows_after_a_full_pipeline_run(isolated_env, monkeypatch):
    """⑬ 验收:「停写表在跑一遍完整管线后**行数不增**」。"""
    import sqlite3

    from tests.conftest import seed_active_rule_v1, seed_synthetic_market
    import neckline.report.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
    dates = seed_synthetic_market(isolated_env)
    seed_active_rule_v1(isolated_env)

    def counts() -> dict:
        conn = sqlite3.connect(str(isolated_env.db_path))
        try:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in _FROZEN_TABLES}
        finally:
            conn.close()

    before = counts()
    pipeline_mod.build_report(dates[-1], parquet_dir=isolated_env.parquet_dir,
                              db_path=isolated_env.db_path, save=True)
    assert counts() == before
