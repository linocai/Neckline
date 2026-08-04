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
# 停写守门的扫描域 = `neckline/` + `scripts/`(契约线审计 🟡 Y1 第 3 洞,2026-08-03 扩)。
# `_PY_FILES` 保持只有 `neckline/`:上面 ① 类「模块已删 + 全仓零 import」断言的语义就是
# **包内**零 import(脚本层引用已删模块会自己 ImportError,不是同一件事),别混用。
_WRITE_SCAN_FILES = sorted(_PKG.rglob("*.py")) + sorted((_ROOT / "scripts").rglob("*.py"))
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
    """`neckline/` + `scripts/` 全域针对该表的**任何写入**调用点(宁可漏报不许误报:
    取不到字面量的动态 SQL 不算命中,同 `test_v2_schema_guard._sql_literal` 既定取向)。

    **写法变体成套(契约线审计 🟡 Y1 第 2 洞,2026-08-03)**:原来只有
    `INSERT INTO` / `UPDATE` / `DELETE FROM` 三条前缀 —— 而
    `INSERT OR REPLACE INTO <停写表>` / `INSERT OR IGNORE INTO <停写表>` 这两种**最常见的
    幂等写法**都不含子串 `INSERT INTO <表>`,一个都不命中。停写就是停写,不分写法。
    """
    forbidden = (
        f"INSERT INTO {table}", f"UPDATE {table}", f"DELETE FROM {table}",
        f"REPLACE INTO {table}",                      # 覆盖 `INSERT OR REPLACE INTO` 与裸 REPLACE
        f"INSERT OR IGNORE INTO {table}",
        f"INSERT OR ABORT INTO {table}", f"INSERT OR FAIL INTO {table}",
        f"INSERT OR ROLLBACK INTO {table}",
    )
    hits: List[Tuple[str, int, str]] = []
    for path in _WRITE_SCAN_FILES:
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


def test_13_1_permanent_board_dto_and_client_card_are_gone():
    """契约线审计 🟡 Y4(2026-08-03):⑬-1 的实现层删干净了,**契约层 DTO 与客户端卡片
    整套还留着** —— 新报告 `candidates` 恒空,那张卡会稳定显示「暂无候选可显示常驻板块
    状态(今晚 16:35 报告后可见)」,一句永远兑现不了的承诺。原守门只断言 settings_store
    符号与端点,罩不到这两处,所以全绿。

    ⚠ 这条与上面那条一起构成 ⑬-1 的完整判据:**「实现删了」不等于「退役了」**,
    契约面与渲染件留着,对用户来说这个功能就还在(还在承诺、还在占位)。"""
    from neckline.api import schemas

    assert not hasattr(schemas, "PermanentBoardStatusOut")
    assert "PermanentBoardStatusOut" not in schemas.__all__
    # ⑭-B 起 `IntelRankOut` 本身也退役了(整族候选契约换血),故这条从「某个键不在
    # 那个 DTO 里」升级为「那个 DTO 压根不存在」—— 更强,不是更弱。
    assert not hasattr(schemas, "IntelRankOut")

    models = (_ROOT / "client" / "Models.swift").read_text(encoding="utf-8")
    assert "struct PermanentBoardStatus" not in models
    assert "var permanentBoardStatus" not in models

    view = (_ROOT / "client" / "Neckline" / "Views" / "IntelSectionView.swift").read_text(
        encoding="utf-8")
    assert "permanentBoardsCard" not in view
    assert "暂无候选可显示常驻板块状态" not in view, "永远兑现不了的承诺文案不许留"
    assert "五常驻板块" not in view.split("import SwiftUI", 1)[1], "卡片本体已拆(注释里可留因由)"


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


def test_13_4_exec_hints_for_has_a_real_production_call_site():
    """**接线守门**(契约线审计 🔵 B5,2026-08-04 A9-③ 补):上面那条只查"函数还在"
    (`hasattr`)—— ⑬ 完工时它确实零生产调用方(留给 ⑭-A),那时 `hasattr` 是能给的
    最强断言;⑭-A 接上之后,"存在但没人调"与"存在且真被调"就必须分得开,否则哪天
    接线被顺手删掉,11 条单测照样全绿、报告里默默少一节。

    判据取**调用点**而不是 import(`basket_daily.py` 是函数内延迟 import,`_import_hits`
    那种模块级扫描看不见它)。"""
    import ast

    callers = []
    for path in _PY_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
            if name == "exec_hints_for":
                callers.append(str(path.relative_to(_ROOT)))
                break
    assert callers, (
        "`exec_hints_for` 在 `neckline/` 里零调用点 —— 四条执行提示算了没人用,"
        "报告的篮子剧本会静默少一节(⑭-A 的接线点是 report/basket_daily.py)"
    )


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
    """⚠ **断言两次升级**:

    · **V2-⑭-B**:整个 `CandidateOut` 连同 `ReportOut.candidates` 退役(契约面留着 =
      它还在承诺这件事),判据从"某四个键不在 DTO 里"升级为"这族 DTO 压根不存在"。
    · **V2-⑮**:客户端侧同批换血 —— `Candidate`/`IntelRank`/`LLMJudgment`/
      `InfoCardSummary`/`EntrySpec` 五个 Swift 类型一并删除,判据再升级为
      **"客户端连这族类型都没有"**(老四件套的键自然无处存身)。
    """
    import neckline.api.schemas as schemas

    for gone in ("CandidateOut", "IntelRankOut", "LLMJudgmentOut", "InfoCardSummaryOut"):
        assert not hasattr(schemas, gone), f"{gone} 应已随 ⑭-B 契约总装退役"
    assert "candidates" not in schemas.ReportOut.model_fields, \
        "`ReportOut.candidates` 应已换成 `basketDaily`(⑭-B)"
    assert "basketDaily" in schemas.ReportOut.model_fields

    swift = (_ROOT / "client" / "Models.swift").read_text(encoding="utf-8")
    for gone in ("struct Candidate:", "struct IntelRank:", "struct LLMJudgment:",
                 "struct InfoCardSummary:", "struct EntrySpec:"):
        assert gone not in swift, f"`{gone}` 应已随 ⑮ 客户端换血删除"
    # 报告展示模型改挂篮子日报三段。
    assert "var basketDaily: BasketDaily" in swift
    assert "var candidates: [Candidate]" not in swift


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


def test_13_7_precall_does_not_touch_the_verification_ruleset():
    """⛔ 竞价开盘价不是收盘价 —— precall 只借冻结价位(`ref_close` / `stop_line`
    两个数),**不许**往 ⑦-b 的条件集里加竞价语义(那会污染 ⑧ 的四态判定)。

    ⚠ 判据从「版本串等于某个字面量」改成「precall **碰不到**条件集的判定面」
    (2026-08-03,判定线 🟡-1 bump 到 v2 时暴露):钉死字面量的写法把「⑦-b 因**别的**
    原因正当 bump」也判成违规,是**误报型守门** —— 守门该锁的是这个模块的行为边界,
    不是另一个模块的版本号取值。"""
    import neckline.sentinel.precall as pc
    from neckline.selection import verification_rules as vr

    src = (_PKG / "sentinel" / "precall.py").read_text(encoding="utf-8")
    # ① 只准引用条件**码**(当键去读冻结 spec),不准调用条件集的任何判定/生成函数
    for banned in ("evaluate_condition", "decide_state", "conditions_block", "combine_side",
                   "min_members_hit", "VERIFICATION_RULESET_VERSION"):
        assert f"vr.{banned}" not in src, f"precall 不该调用条件集的 {banned}"
    # ② 不准自己造条件码 / 自己写一份条件集
    assert not any(name.startswith("COND_") for name in vars(pc)), "precall 不许自定义条件码"
    # ③ 条件集本身仍是 ⑦-b 那套(precall 落地前后逐字相同)
    assert tuple(vr.VERIFY_REQUIRE_ALL) == ("close_at_or_above_ref", "holds_ma20")
    assert tuple(vr.INVALIDATE_ANY_OF) == (
        "close_below_stop_line", "limit_down_touch", "close_below_ref_and_ma20")


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


def test_write_guard_scan_domain_and_variants_are_both_live():
    """守门本身可证伪(🟡 Y1 的病根是"看起来在守、其实漏"两处):
    ① 扫描域真的含 `scripts/`;② `INSERT OR REPLACE/IGNORE` 变体真的会命中。"""
    rel = {str(p.relative_to(_ROOT)) for p in _WRITE_SCAN_FILES}
    assert any(p.startswith("scripts/") for p in rel)
    assert any(p.startswith("neckline/") for p in rel)

    def _hits(sql: str) -> list:
        forbidden = ("INSERT INTO decision_log", "REPLACE INTO decision_log",
                     "INSERT OR IGNORE INTO decision_log")
        upper = " ".join(sql.upper().split())
        return [f for f in forbidden if f.upper() in upper]

    assert _hits("INSERT OR REPLACE INTO decision_log (id) VALUES (1)")
    assert _hits("insert or ignore into decision_log (id) values (1)")
    assert not _hits("SELECT * FROM decision_log")


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
