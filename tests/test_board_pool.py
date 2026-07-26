"""板块池卫生线单测(plan §五 v1.3-③-C3-①,C1/C2 复用)。锁死:① 名称模式剔除;
② 成分数上限剔除;③ 双闸互斥归因(先名称后成分数,各自不重复计入);④ 误伤豁免
(重组蛋白);⑤ 剔除审计文案可读;⑥ `count_members`/`invert_member_map` 互为逆运算。
"""

from __future__ import annotations

from neckline.report.board_pool import (
    MAX_CONSTITUENTS,
    apply_hygiene,
    count_members,
    invert_member_map,
)


class TestApplyHygiene:
    def test_name_pattern_excludes_eligibility_boards(self):
        index_names = {
            "A.TI": "融资融券", "B.TI": "深股通", "C.TI": "机器人概念", "D.TI": "国企改革",
        }
        counts = {"A.TI": 3000, "B.TI": 1800, "C.TI": 1200, "D.TI": 1400}
        result = apply_hygiene(index_names, counts)
        assert result.kept == frozenset({"C.TI"})
        gates = {e.index_code: e.gate for e in result.excluded}
        assert gates == {"A.TI": "name_pattern", "B.TI": "name_pattern", "D.TI": "name_pattern"}

    def test_constituent_cap_excludes_large_unnamed_boards(self):
        index_names = {"A.TI": "某新宽基标签", "B.TI": "机器人概念"}
        counts = {"A.TI": MAX_CONSTITUENTS + 1, "B.TI": MAX_CONSTITUENTS - 1}
        result = apply_hygiene(index_names, counts)
        assert result.kept == frozenset({"B.TI"})
        assert result.excluded[0].index_code == "A.TI"
        assert result.excluded[0].gate == "constituent_cap"

    def test_boundary_equal_to_cap_is_kept_not_excluded(self):
        index_names = {"A.TI": "机器人概念"}
        counts = {"A.TI": MAX_CONSTITUENTS}
        result = apply_hygiene(index_names, counts)
        assert result.kept == frozenset({"A.TI"})
        assert result.excluded == []

    def test_name_pattern_takes_precedence_mutually_exclusive_attribution(self):
        """一个板块若同时命中名称模式与成分数上限,只归因一次(名称模式优先判)。"""
        index_names = {"A.TI": "融资融券"}
        counts = {"A.TI": MAX_CONSTITUENTS + 500}
        result = apply_hygiene(index_names, counts)
        assert len(result.excluded) == 1
        assert result.excluded[0].gate == "name_pattern"

    def test_false_positive_allowlist_rescues_recombinant_protein(self):
        """"重组"关键词朴素子串匹配会误伤"重组蛋白"(生物医药主题,与公司重组
        无关)——已加精确名称豁免,见 board_pool.py docstring 真实数据核对记录。"""
        index_names = {"A.TI": "重组蛋白", "B.TI": "股权转让(并购重组)"}
        counts = {"A.TI": 60, "B.TI": 459}
        result = apply_hygiene(index_names, counts)
        assert result.kept == frozenset({"A.TI"})
        assert result.excluded[0].index_code == "B.TI"

    def test_missing_member_count_defaults_to_zero_not_excluded_by_cap(self):
        index_names = {"A.TI": "机器人概念"}
        result = apply_hygiene(index_names, {})   # 无成分数据 → 视为 0,不触发成分数闸
        assert result.kept == frozenset({"A.TI"})

    def test_empty_universe_returns_empty_result(self):
        result = apply_hygiene({}, {})
        assert result.kept == frozenset()
        assert result.excluded == []

    def test_audit_lines_grouped_by_gate_and_sorted_by_member_count_desc(self):
        index_names = {"A.TI": "融资融券", "B.TI": "深股通"}
        counts = {"A.TI": 3800, "B.TI": 1800}
        result = apply_hygiene(index_names, counts)
        lines = result.audit_lines()
        assert len(lines) == 1   # 两个都命中同一闸(name_pattern),合并一行
        assert "融资融券(3800只)" in lines[0]
        assert "深股通(1800只)" in lines[0]
        # 成分数降序:融资融券在深股通之前
        assert lines[0].index("融资融券") < lines[0].index("深股通")

    def test_audit_lines_empty_when_nothing_excluded(self):
        result = apply_hygiene({"A.TI": "机器人概念"}, {"A.TI": 1200})
        assert result.audit_lines() == []


class TestCountMembersAndInvert:
    def test_count_members_tallies_per_board(self):
        member_map = {
            "600001.SH": ["A.TI", "B.TI"],
            "600002.SH": ["A.TI"],
            "300001.SZ": ["A.TI", "B.TI", "C.TI"],
        }
        counts = count_members(member_map)
        assert counts == {"A.TI": 3, "B.TI": 2, "C.TI": 1}

    def test_invert_member_map_is_inverse_of_load_member_map_shape(self):
        member_map = {
            "600001.SH": ["A.TI", "B.TI"],
            "600002.SH": ["A.TI"],
        }
        inv = invert_member_map(member_map)
        assert set(inv["A.TI"]) == {"600001.SH", "600002.SH"}
        assert inv["B.TI"] == ["600001.SH"]

    def test_empty_member_map_yields_empty_dicts(self):
        assert count_members({}) == {}
        assert invert_member_map({}) == {}
