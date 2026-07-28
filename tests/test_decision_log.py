"""预注册决策日志(八项 + v1.4-⑤-B 第⑨项)存取层单测(plan §五 v1.2-B 验收)。

覆盖:`created_at` 服务端生成(签名无此形参,物理上不可能被调用方覆盖)、八项
无 UPDATE 路径(revise 新增行 + 首版原地不变 + revision_of 落链根)、
scenario-outcome 只翻 matched 不动情景文本、index 越界报错、not-found 语义、
list 过滤;⑨`max_chase_pct`(领域层默认 `None`,不强制——「必须显式选择」是
`api/app.py` HTTP 契约层职责,不在本层,见 `tests/test_api_decisions.py`)往返 +
不被 link/cancel/scenario-outcome 触碰 + revise 携带。
"""

from __future__ import annotations

import pytest

from neckline.decision_log import (
    PLAYBOOK_TAG_CODES,
    STATUS_CANCELLED,
    STATUS_FILLED,
    STATUS_PENDING,
    THESIS_TAG_CODES,
    ScenarioIndexError,
    cancel_decision,
    create_decision,
    get_decision,
    link_decision,
    list_decisions,
    revise_decision,
    set_scenario_outcomes,
)

pytestmark = pytest.mark.usefixtures("isolated_env")


def _make(db_path, **overrides):
    kwargs = dict(
        ts_code="600001.SH", name="示例甲", why_buy="题材热+量能启动",
        why_entry_price="回调至10日线企稳", invalidation="跌破10日线",
        thesis_tags=["THEME", "CAPITAL_FLOW"], playbook_tag="SWING_CHASE",
        target_price=12.0, exit_low=9.0, exit_high=9.5,
        contingency_scenarios=[
            {"scenario": "次日高开", "trigger": "开盘涨幅>3%", "action": "HOLD"},
            {"scenario": "次日低开", "trigger": "开盘跌幅>2%", "action": "ABANDON"},
        ],
        planned_price=10.0, planned_qty=1000, db_path=db_path,
    )
    kwargs.update(overrides)
    return create_decision(**kwargs)


class TestCreateAndCreatedAt:
    def test_create_stamps_server_created_at(self, isolated_env):
        row = _make(isolated_env.db_path)
        assert row.id >= 1
        assert row.created_at   # 非空,服务端生成
        assert row.status == STATUS_PENDING
        assert row.position_id is None
        assert row.revision_of is None   # 首版

    def test_create_signature_has_no_created_at_param(self):
        """`created_at` 服务端生成的物理保证:函数签名本身没有这个形参,调用方
        (含 API 层透传)无法传入任何值来覆盖它——不是"传了会被忽略"的运行时判断,
        而是"根本没有地方传"。"""
        import inspect
        sig = inspect.signature(create_decision)
        assert "created_at" not in sig.parameters

    def test_eight_items_round_trip(self, isolated_env):
        row = _make(isolated_env.db_path)
        assert row.ts_code == "600001.SH"
        assert row.why_buy == "题材热+量能启动"
        assert row.why_entry_price == "回调至10日线企稳"
        assert row.target_price == 12.0
        assert row.exit_low == 9.0 and row.exit_high == 9.5
        assert row.thesis_tags == ["THEME", "CAPITAL_FLOW"]
        assert row.invalidation == "跌破10日线"
        assert len(row.contingency_scenarios) == 2
        assert row.contingency_scenarios[0] == {
            "scenario": "次日高开", "trigger": "开盘涨幅>3%", "action": "HOLD", "matched": False,
        }
        assert row.playbook_tag == "SWING_CHASE"
        assert row.planned_price == 10.0 and row.planned_qty == 1000

    def test_scenario_matched_defaults_false(self, isolated_env):
        row = _make(isolated_env.db_path)
        assert all(s["matched"] is False for s in row.contingency_scenarios)

    def test_enum_code_constants_match_plan(self):
        assert THESIS_TAG_CODES == ("THEME", "SENTIMENT_CYCLE", "CAPITAL_FLOW", "TECH_PATTERN", "NEWS")
        assert PLAYBOOK_TAG_CODES == ("SWING_CHASE", "BREATHING_TRIAL")


class TestNoUpdatePathForEightItems:
    """核心不变量:八项落库后不可编辑——本模块源码里,除 `create_decision`/
    `revise_decision` 的 INSERT 外,没有任何 UPDATE 语句触碰这 8 个字段。这里从
    行为角度断言:`link`/`cancel`/`scenario-outcome` 均不改动八项内容。"""

    def test_link_does_not_touch_eight_items(self, isolated_env):
        row = _make(isolated_env.db_path)
        link_decision(row.id, position_id=42, db_path=isolated_env.db_path)
        after = get_decision(row.id, db_path=isolated_env.db_path)
        assert after.why_buy == row.why_buy
        assert after.thesis_tags == row.thesis_tags
        assert after.contingency_scenarios == row.contingency_scenarios
        assert after.playbook_tag == row.playbook_tag
        # 只有关联字段变了
        assert after.status == STATUS_FILLED and after.position_id == 42

    def test_cancel_does_not_touch_eight_items(self, isolated_env):
        row = _make(isolated_env.db_path)
        cancel_decision(row.id, db_path=isolated_env.db_path)
        after = get_decision(row.id, db_path=isolated_env.db_path)
        assert after.why_entry_price == row.why_entry_price
        assert after.invalidation == row.invalidation
        assert after.status == STATUS_CANCELLED

    def test_link_missing_returns_false(self, isolated_env):
        assert link_decision(9999, position_id=1, db_path=isolated_env.db_path) is False

    def test_cancel_missing_returns_false(self, isolated_env):
        assert cancel_decision(9999, db_path=isolated_env.db_path) is False


class TestRevise:
    def test_revise_creates_new_row_original_untouched(self, isolated_env):
        base = _make(isolated_env.db_path)
        rev = revise_decision(
            base.id, why_buy="新理由:资金持续净流入", why_entry_price="新入场价理由",
            invalidation="新证伪条件", thesis_tags=["NEWS"], playbook_tag="BREATHING_TRIAL",
            contingency_scenarios=[], target_price=13.0, db_path=isolated_env.db_path,
        )
        assert rev.id != base.id
        assert rev.why_buy == "新理由:资金持续净流入"
        assert rev.target_price == 13.0
        assert rev.status == STATUS_PENDING

        # 首版原地不变(逐字段核对,非仅 id 不变)
        original = get_decision(base.id, db_path=isolated_env.db_path)
        assert original.why_buy == base.why_buy
        assert original.target_price == base.target_price
        assert original.thesis_tags == base.thesis_tags
        assert original.status == STATUS_PENDING   # revise 不改旧行状态

    def test_revise_carries_over_ts_code_and_name(self, isolated_env):
        base = _make(isolated_env.db_path, ts_code="600003.SH", name="示例丙")
        rev = revise_decision(
            base.id, why_buy="x", why_entry_price="x", invalidation="x",
            thesis_tags=[], playbook_tag="SWING_CHASE", db_path=isolated_env.db_path,
        )
        assert rev.ts_code == "600003.SH" and rev.name == "示例丙"

    def test_revision_of_points_to_root_not_immediate_parent(self, isolated_env):
        """链根语义:对修订行再次修订,新行的 `revision_of` 仍指向最初的首版 id,
        不是指向"上一版"(否则会形成需要递归遍历的链表)。"""
        base = _make(isolated_env.db_path)
        rev1 = revise_decision(
            base.id, why_buy="v2", why_entry_price="v2", invalidation="v2",
            thesis_tags=[], playbook_tag="SWING_CHASE", db_path=isolated_env.db_path,
        )
        assert rev1.revision_of == base.id

        rev2 = revise_decision(
            rev1.id, why_buy="v3", why_entry_price="v3", invalidation="v3",
            thesis_tags=[], playbook_tag="SWING_CHASE", db_path=isolated_env.db_path,
        )
        assert rev2.revision_of == base.id   # 不是 rev1.id

        rev3 = revise_decision(
            rev2.id, why_buy="v4", why_entry_price="v4", invalidation="v4",
            thesis_tags=[], playbook_tag="SWING_CHASE", db_path=isolated_env.db_path,
        )
        assert rev3.revision_of == base.id

        # 全部修订(rev1/rev2/rev3)都可用同一个 root id 一步查到,无需递归
        all_for_root = [d for d in list_decisions(db_path=isolated_env.db_path) if d.revision_of == base.id]
        assert {d.id for d in all_for_root} == {rev1.id, rev2.id, rev3.id}

    def test_revise_missing_returns_none(self, isolated_env):
        assert revise_decision(
            9999, why_buy="x", why_entry_price="x", invalidation="x",
            thesis_tags=[], playbook_tag="SWING_CHASE", db_path=isolated_env.db_path,
        ) is None


class TestScenarioOutcome:
    def test_only_flips_matched_not_scenario_text(self, isolated_env):
        row = _make(isolated_env.db_path)
        ok = set_scenario_outcomes(row.id, [{"index": 0, "matched": True}], db_path=isolated_env.db_path)
        assert ok is True
        after = get_decision(row.id, db_path=isolated_env.db_path)
        assert after.contingency_scenarios[0]["matched"] is True
        # scenario/trigger/action 逐字不变
        assert after.contingency_scenarios[0]["scenario"] == row.contingency_scenarios[0]["scenario"]
        assert after.contingency_scenarios[0]["trigger"] == row.contingency_scenarios[0]["trigger"]
        assert after.contingency_scenarios[0]["action"] == row.contingency_scenarios[0]["action"]
        # 未提及的第二项不受影响
        assert after.contingency_scenarios[1]["matched"] is False
        assert after.contingency_scenarios[1] == row.contingency_scenarios[1]
        # 八项其它内容也不受影响
        assert after.why_buy == row.why_buy

    def test_multiple_outcomes_in_one_call(self, isolated_env):
        row = _make(isolated_env.db_path)
        set_scenario_outcomes(
            row.id, [{"index": 0, "matched": True}, {"index": 1, "matched": True}],
            db_path=isolated_env.db_path,
        )
        after = get_decision(row.id, db_path=isolated_env.db_path)
        assert after.contingency_scenarios[0]["matched"] is True
        assert after.contingency_scenarios[1]["matched"] is True

    def test_index_out_of_range_raises_and_does_not_partially_apply(self, isolated_env):
        row = _make(isolated_env.db_path)
        with pytest.raises(ScenarioIndexError):
            set_scenario_outcomes(
                row.id, [{"index": 0, "matched": True}, {"index": 99, "matched": True}],
                db_path=isolated_env.db_path,
            )
        # 批内有一个越界,整批不生效(index 0 也未被落库)
        after = get_decision(row.id, db_path=isolated_env.db_path)
        assert after.contingency_scenarios[0]["matched"] is False

    def test_negative_index_raises(self, isolated_env):
        row = _make(isolated_env.db_path)
        with pytest.raises(ScenarioIndexError):
            set_scenario_outcomes(row.id, [{"index": -1, "matched": True}], db_path=isolated_env.db_path)

    def test_missing_decision_returns_false(self, isolated_env):
        assert set_scenario_outcomes(9999, [{"index": 0, "matched": True}], db_path=isolated_env.db_path) is False

    def test_empty_scenario_tree_any_index_out_of_range(self, isolated_env):
        row = _make(isolated_env.db_path, contingency_scenarios=[])
        with pytest.raises(ScenarioIndexError):
            set_scenario_outcomes(row.id, [{"index": 0, "matched": True}], db_path=isolated_env.db_path)


class TestMaxChasePct:
    """v1.4-⑤-B(需求 2 补充)⑨最高追价上限,领域层往返 + 不可编辑 + revise 携带。"""

    def test_defaults_to_none_when_not_passed(self, isolated_env):
        """领域层未强制——Python 直调方(CLI/其它调用点)不传时默认 `None`,不报错
        (「必须显式选择」是 HTTP 层职责,见 `test_api_decisions.py`)。"""
        row = _make(isolated_env.db_path)
        assert row.max_chase_pct is None

    def test_positive_and_negative_values_round_trip(self, isolated_env):
        row = _make(isolated_env.db_path, max_chase_pct=3.0)
        assert row.max_chase_pct == 3.0
        row2 = _make(isolated_env.db_path, ts_code="600002.SH", max_chase_pct=-1.5)
        assert row2.max_chase_pct == -1.5

    def test_link_cancel_do_not_touch_max_chase_pct(self, isolated_env):
        row = _make(isolated_env.db_path, max_chase_pct=2.0)
        link_decision(row.id, position_id=42, db_path=isolated_env.db_path)
        after = get_decision(row.id, db_path=isolated_env.db_path)
        assert after.max_chase_pct == 2.0

    def test_scenario_outcome_does_not_touch_max_chase_pct(self, isolated_env):
        row = _make(isolated_env.db_path, max_chase_pct=2.0)
        set_scenario_outcomes(row.id, [{"index": 0, "matched": True}], db_path=isolated_env.db_path)
        after = get_decision(row.id, db_path=isolated_env.db_path)
        assert after.max_chase_pct == 2.0

    def test_revise_carries_new_max_chase_pct_original_untouched(self, isolated_env):
        base = _make(isolated_env.db_path, max_chase_pct=3.0)
        rev = revise_decision(
            base.id, why_buy="x", why_entry_price="x", invalidation="x",
            thesis_tags=[], playbook_tag="SWING_CHASE", max_chase_pct=-0.5,
            db_path=isolated_env.db_path,
        )
        assert rev.max_chase_pct == -0.5
        original = get_decision(base.id, db_path=isolated_env.db_path)
        assert original.max_chase_pct == 3.0   # 首版原地不变

    def test_independent_of_planned_price(self, isolated_env):
        """⑨与 planned_price 语义分离:一个有值不影响另一个,任意组合都合法。"""
        row = _make(isolated_env.db_path, planned_price=None, max_chase_pct=1.0)
        assert row.planned_price is None and row.max_chase_pct == 1.0
        row2 = _make(isolated_env.db_path, ts_code="600002.SH", planned_price=9.0, max_chase_pct=None)
        assert row2.planned_price == 9.0 and row2.max_chase_pct is None


class TestListFilters:
    def test_list_all_default(self, isolated_env):
        _make(isolated_env.db_path, ts_code="600001.SH")
        _make(isolated_env.db_path, ts_code="600002.SH")
        assert len(list_decisions(db_path=isolated_env.db_path)) == 2

    def test_filter_by_status(self, isolated_env):
        a = _make(isolated_env.db_path, ts_code="600001.SH")
        _make(isolated_env.db_path, ts_code="600002.SH")
        cancel_decision(a.id, db_path=isolated_env.db_path)
        pending = list_decisions(status=STATUS_PENDING, db_path=isolated_env.db_path)
        assert [d.ts_code for d in pending] == ["600002.SH"]
        cancelled = list_decisions(status=STATUS_CANCELLED, db_path=isolated_env.db_path)
        assert [d.ts_code for d in cancelled] == ["600001.SH"]

    def test_filter_by_code(self, isolated_env):
        _make(isolated_env.db_path, ts_code="600001.SH")
        _make(isolated_env.db_path, ts_code="600002.SH")
        rows = list_decisions(ts_code="600002.SH", db_path=isolated_env.db_path)
        assert len(rows) == 1 and rows[0].ts_code == "600002.SH"

    def test_filter_by_date_range(self, isolated_env, monkeypatch):
        import neckline.decision_log as dl_mod

        monkeypatch.setattr(dl_mod, "_now", lambda: "2026-07-20T09:00:00+00:00")
        old = _make(isolated_env.db_path, ts_code="600001.SH")
        monkeypatch.setattr(dl_mod, "_now", lambda: "2026-07-25T09:00:00+00:00")
        new = _make(isolated_env.db_path, ts_code="600002.SH")

        in_range = list_decisions(date_from="20260722", date_to="20260726", db_path=isolated_env.db_path)
        assert [d.id for d in in_range] == [new.id]

        all_range = list_decisions(date_from="20260701", date_to="20260731", db_path=isolated_env.db_path)
        assert {d.id for d in all_range} == {old.id, new.id}

    def test_list_order_by_created_at(self, isolated_env):
        a = _make(isolated_env.db_path, ts_code="600001.SH")
        b = _make(isolated_env.db_path, ts_code="600002.SH")
        rows = list_decisions(db_path=isolated_env.db_path)
        assert [r.id for r in rows] == [a.id, b.id]
