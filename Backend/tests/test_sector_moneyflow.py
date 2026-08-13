"""板块资金流单测(plan §五 v1.3-③-C2)。锁死:① 2023-09 前无数据留空不臆造;
② 当日数据缺失(覆盖范围内但管线未拉到)同样留空、原因文案不同;③ 按板块成分
聚合 net_amount(万元)+ 排序;④ 板块池卫生线剔除(与 C1 同一份);⑤ 净流入/
净流出各自独立编号;⑥ 证据强度标注(evidenceStrength=constituent)透到字段;
⑦ 定位文案(拥挤情报、非选股信号)必须出现在 evidence_note。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.conftest import write_daily_fixture, write_flat_parquet

from neckline.report.sector_moneyflow import MONEYFLOW_COVERAGE_START, compute_sector_moneyflow

pytestmark = pytest.mark.usefixtures("isolated_env")


def _write_moneyflow(settings, d: date, rows: list) -> None:
    write_daily_fixture(settings, "moneyflow_dc", d, rows)


class TestCoverageBoundary:
    def test_before_coverage_start_is_unavailable_with_honest_reason(self, isolated_env):
        d = MONEYFLOW_COVERAGE_START - timedelta(days=5)
        report = compute_sector_moneyflow(d, parquet_dir=isolated_env.parquet_dir)
        assert report.available is False
        assert "2023-09-11" in report.unavailable_reason
        assert report.top_inflow == [] and report.top_outflow == []

    def test_missing_data_within_coverage_window_is_unavailable_different_reason(self, isolated_env):
        d = MONEYFLOW_COVERAGE_START + timedelta(days=10)
        report = compute_sector_moneyflow(d, parquet_dir=isolated_env.parquet_dir)
        assert report.available is False
        assert "覆盖" not in report.unavailable_reason or "管线" in report.unavailable_reason
        assert "2023-09-11" not in report.unavailable_reason  # 覆盖范围内,原因不该扯覆盖起始日


class TestAggregationAndRanking:
    def test_aggregates_by_board_sums_member_net_amount(self, isolated_env):
        d = date(2026, 3, 2)
        _write_moneyflow(isolated_env, d, [
            {"ts_code": "600001.SH", "net_amount": 1000.0},
            {"ts_code": "600002.SH", "net_amount": 500.0},
            {"ts_code": "600003.SH", "net_amount": -2000.0},
        ])
        write_flat_parquet(isolated_env, "ths_index.parquet", [
            {"ts_code": "AAA.TI", "name": "机器人概念"},
            {"ts_code": "BBB.TI", "name": "人工智能"},
        ])
        write_flat_parquet(isolated_env, "ths_member.parquet", [
            {"index_code": "AAA.TI", "con_code": "600001.SH", "con_name": "甲"},
            {"index_code": "AAA.TI", "con_code": "600002.SH", "con_name": "乙"},
            {"index_code": "BBB.TI", "con_code": "600003.SH", "con_name": "丙"},
        ])
        report = compute_sector_moneyflow(d, parquet_dir=isolated_env.parquet_dir)
        assert report.available is True
        by_name = {i.name: i for i in report.top_inflow}
        assert by_name["机器人概念"].net_inflow_wan == pytest.approx(1500.0)
        assert by_name["机器人概念"].member_count == 2
        # 净流出榜:人工智能(-2000)应排第一(最负)
        assert report.top_outflow[0].name == "人工智能"
        assert report.top_outflow[0].net_inflow_wan == pytest.approx(-2000.0)

    def test_inflow_and_outflow_independently_ranked_1_based(self, isolated_env):
        d = date(2026, 3, 2)
        _write_moneyflow(isolated_env, d, [
            {"ts_code": f"60000{i}.SH", "net_amount": float(100 - i * 50)} for i in range(4)
        ])
        write_flat_parquet(isolated_env, "ths_index.parquet", [
            {"ts_code": f"B{i}.TI", "name": f"板块{i}"} for i in range(4)
        ])
        write_flat_parquet(isolated_env, "ths_member.parquet", [
            {"index_code": f"B{i}.TI", "con_code": f"60000{i}.SH", "con_name": f"股{i}"} for i in range(4)
        ])
        report = compute_sector_moneyflow(d, parquet_dir=isolated_env.parquet_dir)
        assert [i.rank for i in report.top_inflow] == list(range(1, len(report.top_inflow) + 1))
        assert [i.rank for i in report.top_outflow] == list(range(1, len(report.top_outflow) + 1))
        # net_amount 序列 100,50,0,-50 → 板块0 净流入最多(rank1),板块3 净流出最多(rank1)
        assert report.top_inflow[0].name == "板块0"
        assert report.top_outflow[0].name == "板块3"

    def test_evidence_strength_and_positioning_note_present(self, isolated_env):
        d = date(2026, 3, 2)
        _write_moneyflow(isolated_env, d, [{"ts_code": "600001.SH", "net_amount": 100.0}])
        write_flat_parquet(isolated_env, "ths_index.parquet", [{"ts_code": "AAA.TI", "name": "机器人概念"}])
        write_flat_parquet(isolated_env, "ths_member.parquet", [
            {"index_code": "AAA.TI", "con_code": "600001.SH", "con_name": "甲"},
        ])
        report = compute_sector_moneyflow(d, parquet_dir=isolated_env.parquet_dir)
        assert report.top_inflow[0].evidence_strength == "constituent"
        assert "非选股信号" in report.evidence_note
        assert "K2" in report.evidence_note


class TestHygieneReuse:
    def test_denylisted_board_excluded_from_ranking_and_noted(self, isolated_env):
        d = date(2026, 3, 2)
        _write_moneyflow(isolated_env, d, [
            {"ts_code": "600001.SH", "net_amount": 999999.0},   # 巨额流入,但属于剔除板块
            {"ts_code": "600002.SH", "net_amount": 10.0},
        ])
        write_flat_parquet(isolated_env, "ths_index.parquet", [
            {"ts_code": "JUNK.TI", "name": "融资融券"},
            {"ts_code": "AAA.TI", "name": "机器人概念"},
        ])
        write_flat_parquet(isolated_env, "ths_member.parquet", [
            {"index_code": "JUNK.TI", "con_code": "600001.SH", "con_name": "甲"},
            {"index_code": "AAA.TI", "con_code": "600002.SH", "con_name": "乙"},
        ])
        report = compute_sector_moneyflow(d, parquet_dir=isolated_env.parquet_dir)
        names = {i.name for i in report.top_inflow} | {i.name for i in report.top_outflow}
        assert "融资融券" not in names
        assert "机器人概念" in names
        assert "融资融券" in report.excluded_boards_note


class TestNoBoardMatched:
    def test_no_member_overlap_with_moneyflow_yields_available_true_empty_lists(self, isolated_env):
        d = date(2026, 3, 2)
        _write_moneyflow(isolated_env, d, [{"ts_code": "999999.SH", "net_amount": 1.0}])
        write_flat_parquet(isolated_env, "ths_index.parquet", [{"ts_code": "AAA.TI", "name": "机器人概念"}])
        write_flat_parquet(isolated_env, "ths_member.parquet", [
            {"index_code": "AAA.TI", "con_code": "600001.SH", "con_name": "甲"},   # 不在 moneyflow_dc 里
        ])
        report = compute_sector_moneyflow(d, parquet_dir=isolated_env.parquet_dir)
        assert report.available is True
        assert report.top_inflow == [] and report.top_outflow == []
        assert report.unavailable_reason  # 有解释,不是悄无声息


class TestPublicDictSafety:
    def test_to_public_dict_is_json_safe(self, isolated_env):
        d = date(2026, 3, 2)
        _write_moneyflow(isolated_env, d, [{"ts_code": "600001.SH", "net_amount": 100.0}])
        write_flat_parquet(isolated_env, "ths_index.parquet", [{"ts_code": "AAA.TI", "name": "机器人概念"}])
        write_flat_parquet(isolated_env, "ths_member.parquet", [
            {"index_code": "AAA.TI", "con_code": "600001.SH", "con_name": "甲"},
        ])
        report = compute_sector_moneyflow(d, parquet_dir=isolated_env.parquet_dir)
        d_out = report.to_public_dict()
        assert d_out["tradeDate"] == "2026-03-02"
        assert d_out["available"] is True
        assert "topInflow" in d_out and "topOutflow" in d_out
        import json
        json.dumps(d_out)
