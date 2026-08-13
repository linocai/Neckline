"""预注册决策日志(plan §五 v1.2-B)**只读**存取层单测。

**v2.0.0 起(PROJECT_PLAN §五 V2-⑩-C 决策日志强制表单退役)**:`decision_log` 表
停写留档,`neckline.decision_log` 不再提供任何写函数——本文件不再测
`create_decision`/`revise_decision`/`link_decision`/`cancel_decision`/
`expire_decision`/`set_scenario_outcomes`(它们已被物理删除,不是跳过);历史
行 fixture 一律走 `tests.conftest.insert_decision_log_row`/`set_decision_status`
(裸 SQL,不经任何应用层写口)。覆盖:①`get_decision`/`list_decisions` 只读装配
+ 过滤(status/code/日期区间/position_id);②`created_at_cn_date` 时区换算;
③**退役守门**——模块不再暴露任何写函数、`decision_log` 表在 `neckline/` 全仓
零写入(AST 扫描,同 `tests/test_v2_schema_guard.py` 姿势)。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

from neckline.decision_log import (
    PLAYBOOK_TAG_CODES,
    STATUS_CANCELLED,
    STATUS_FILLED,
    STATUS_PENDING,
    THESIS_TAG_CODES,
    get_decision,
    list_decisions,
)
from tests.conftest import insert_decision_log_row, set_decision_status

pytestmark = pytest.mark.usefixtures("isolated_env")


def _make(db_path, **overrides):
    kwargs = dict(
        ts_code="600001.SH", name="示例甲", why_buy="题材热+量能启动",
        why_entry_price="回调至10日线企稳", invalidation="跌破10日线",
        thesis_tags=["THEME", "CAPITAL_FLOW"], playbook_tag="SWING_CHASE",
        target_price=12.0, exit_low=9.0, exit_high=9.5,
        contingency_scenarios=[
            {"scenario": "次日高开", "trigger": "开盘涨幅>3%", "action": "HOLD", "matched": False},
        ],
        planned_price=10.0, planned_qty=1000,
    )
    kwargs.update(overrides)
    return insert_decision_log_row(db_path, **kwargs)


class TestReadRoundTrip:
    def test_get_decision_round_trips_fixture_row(self, isolated_env):
        row = _make(isolated_env.db_path)
        got = get_decision(row.id, db_path=isolated_env.db_path)
        assert got is not None
        assert got.ts_code == "600001.SH"
        assert got.why_buy == "题材热+量能启动"
        assert got.thesis_tags == ["THEME", "CAPITAL_FLOW"]
        assert got.contingency_scenarios[0]["scenario"] == "次日高开"
        assert got.status == STATUS_PENDING
        assert got.position_id is None and got.revision_of is None

    def test_get_decision_missing_returns_none(self, isolated_env):
        assert get_decision(9999, db_path=isolated_env.db_path) is None

    def test_enum_code_constants_match_plan(self):
        """写入口退役,合法值集合本身不变(历史行仍用这套码)。"""
        assert THESIS_TAG_CODES == ("THEME", "SENTIMENT_CYCLE", "CAPITAL_FLOW", "TECH_PATTERN", "NEWS")
        assert PLAYBOOK_TAG_CODES == ("SWING_CHASE", "BREATHING_TRIAL")


class TestListFilters:
    def test_list_all_default(self, isolated_env):
        _make(isolated_env.db_path, ts_code="600001.SH")
        _make(isolated_env.db_path, ts_code="600002.SH")
        assert len(list_decisions(db_path=isolated_env.db_path)) == 2

    def test_filter_by_status(self, isolated_env):
        a = _make(isolated_env.db_path, ts_code="600001.SH")
        _make(isolated_env.db_path, ts_code="600002.SH")
        set_decision_status(isolated_env.db_path, a.id, STATUS_CANCELLED)
        pending = list_decisions(status=STATUS_PENDING, db_path=isolated_env.db_path)
        assert [d.ts_code for d in pending] == ["600002.SH"]
        cancelled = list_decisions(status=STATUS_CANCELLED, db_path=isolated_env.db_path)
        assert [d.ts_code for d in cancelled] == ["600001.SH"]

    def test_filter_by_code(self, isolated_env):
        _make(isolated_env.db_path, ts_code="600001.SH")
        _make(isolated_env.db_path, ts_code="600002.SH")
        rows = list_decisions(ts_code="600002.SH", db_path=isolated_env.db_path)
        assert len(rows) == 1 and rows[0].ts_code == "600002.SH"

    def test_filter_by_position_id(self, isolated_env):
        a = _make(isolated_env.db_path, ts_code="600001.SH")
        _make(isolated_env.db_path, ts_code="600002.SH")
        set_decision_status(isolated_env.db_path, a.id, STATUS_FILLED, position_id=42)
        rows = list_decisions(position_id=42, db_path=isolated_env.db_path)
        assert [d.id for d in rows] == [a.id]

    def test_filter_by_date_range(self, isolated_env):
        old = _make(isolated_env.db_path, ts_code="600001.SH", created_at="2026-07-20T09:00:00+00:00")
        new = _make(isolated_env.db_path, ts_code="600002.SH", created_at="2026-07-25T09:00:00+00:00")

        in_range = list_decisions(date_from="20260722", date_to="20260726", db_path=isolated_env.db_path)
        assert [d.id for d in in_range] == [new.id]

        all_range = list_decisions(date_from="20260701", date_to="20260731", db_path=isolated_env.db_path)
        assert {d.id for d in all_range} == {old.id, new.id}

    def test_list_order_by_created_at(self, isolated_env):
        a = _make(isolated_env.db_path, ts_code="600001.SH", created_at="2026-07-20T09:00:00+00:00")
        b = _make(isolated_env.db_path, ts_code="600002.SH", created_at="2026-07-21T09:00:00+00:00")
        rows = list_decisions(db_path=isolated_env.db_path)
        assert [r.id for r in rows] == [a.id, b.id]


class TestCreatedAtCnDate:
    def test_utc_late_night_rolls_to_next_beijing_day(self):
        from neckline.decision_log import created_at_cn_date

        # 北京时间 T+1 00:30 建的行,UTC 落库是 T 16:30 —— 必须读作 T+1(北京日),
        # 不是 T(UTC 日),否则历史回放会漏看这条决策(v1.4 review 契约线 🟡-2)。
        assert created_at_cn_date("2026-07-20T16:30:00+00:00") == "2026-07-21"

    def test_naive_string_treated_as_utc(self):
        from neckline.decision_log import created_at_cn_date

        assert created_at_cn_date("2026-07-20T16:30:00") == "2026-07-21"

    def test_unparseable_falls_back_to_first_ten_chars(self):
        from neckline.decision_log import created_at_cn_date

        assert created_at_cn_date("not-a-date-at-all") == "not-a-date"


# ══════════════════════════════════════════════════════════════════════════
# 退役守门(v2.0.0 ⑩-C):写函数物理删除 + 全仓零写入
# ══════════════════════════════════════════════════════════════════════════

class TestRetirementGuard:
    def test_module_exposes_no_write_functions(self):
        """`decision_log.py` 不再有任何创建/修改历史行的公开函数——这不是"暂时不
        导出",是物理删除(源码里根本不存在这些函数定义)。"""
        import neckline.decision_log as dl_mod

        retired_names = (
            "create_decision", "link_decision", "cancel_decision",
            "expire_decision", "revise_decision", "set_scenario_outcomes",
            "ScenarioIndexError",
        )
        present = [n for n in retired_names if hasattr(dl_mod, n)]
        assert not present, f"decision_log 模块仍暴露已退役的写函数/类型:{present}"

    def test_all_matches_actual_read_only_surface(self):
        import neckline.decision_log as dl_mod

        assert set(dl_mod.__all__) == {
            "STATUS_PENDING", "STATUS_FILLED", "STATUS_CANCELLED", "STATUS_EXPIRED",
            "THESIS_TAG_CODES", "PLAYBOOK_TAG_CODES", "SCENARIO_ACTION_CODES",
            "DecisionRow", "created_at_cn_date", "get_decision", "list_decisions",
        }


_NECKLINE_DIR = Path(__file__).resolve().parent.parent / "neckline"
_NECKLINE_PY_FILES = sorted(_NECKLINE_DIR.rglob("*.py"))
_EXEC_METHOD_NAMES = {"execute", "executemany", "executescript"}
# 允许出现在 `decision_log` 表 CREATE 语句本身(建表是幂等 DDL,不是"写入一行"这个
# 意义上的写)——用 SELECT/INSERT/UPDATE/DELETE 四个动词精确匹配,不整体禁字面量
# "decision_log"(那会连 db.py 的 CREATE TABLE 语句和大量说明性 docstring 一起拦下)。
_FORBIDDEN_DECISION_LOG_SQL = (
    "INSERT INTO decision_log",
    "UPDATE decision_log",
    "DELETE FROM decision_log",
)


def _sql_literal(node: ast.AST) -> Optional[str]:
    """从字符串常量或简单 f-string 尽力取出 SQL 文本(取不到就返回 None,宁可漏报
    不许误报——同 `test_v2_schema_guard.py::_sql_literal` 的既定取向)。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                parts.append("")
        return "".join(parts)
    return None


def _execute_sql_literals(path: Path) -> List[Tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        else:
            continue
        if name not in _EXEC_METHOD_NAMES or not node.args:
            continue
        sql = _sql_literal(node.args[0])
        if sql is not None:
            out.append((node.lineno, sql))
    return out


def test_decision_log_table_has_zero_write_call_sites_in_neckline_package():
    """⑩-C 验收条款「`decision_log` 零新增行(grep 守门)」的机器判据:`neckline/`
    全仓(不含 `tests/`,fixture 裸 SQL 允许)扫描不到任何真实 INSERT/UPDATE/DELETE
    调用点碰这张表。"""
    hits: List[Tuple[str, int, str]] = []
    for path in _NECKLINE_PY_FILES:
        for lineno, sql in _execute_sql_literals(path):
            upper = sql.upper()
            for forbidden in _FORBIDDEN_DECISION_LOG_SQL:
                if forbidden in upper:
                    hits.append((str(path.relative_to(_NECKLINE_DIR.parent)), lineno, forbidden))
    assert not hits, f"decision_log 表出现禁止的写入调用点(该表 v2.0.0 起停写留档):{hits}"
