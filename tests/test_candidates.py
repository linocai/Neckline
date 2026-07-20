"""候选评分管线单测(plan 2.3)。核心用手工构造的【特征行】(与 test_momentum.py
同一套约定,绕开 build_research_panel 的整条 I/O 管线)直接测 `score_candidates`
的评分/四件套/标签逻辑——entry mask 仍是 `neckline.strategy.momentum.build_entry_mask`
本尊(同码,§2.6),不是重新实现。`build_candidates` 的完整 I/O 接线由
`tests/test_report_consistency.py`(2.6)一并验证,避免两处重复搭建合成行情夹具。
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from tests.conftest import insert_stock_basic

from neckline.report.candidates import (
    Candidate,
    entry_plan_text,
    invalidation_spec,
    invalidation_text,
    pattern_tags,
    score_candidates,
    stop_loss_text,
    target_text,
)
from neckline.report.sectors import SectorScore
from neckline.strategy.momentum import MomentumConfig

D = date(2024, 3, 4)


def _row(ts_code: str, **overrides) -> dict:
    base = {
        "ts_code": ts_code, "trade_date": D, "board": "MAIN", "close": 10.0,
        "amount_ma20": 50000.0, "ma20": 9.0, "is_st": False,
        "above_ma20_bullish": True, "vol_ratio_5": 1.2, "ret_1d": -0.01,
        "ma10": 9.5, "dist_from_high_20d": -0.02, "prev_close_max_20d": 10.5,
        "consec_limit_up_days": 0, "is_limit_up": False, "limitup_count_20d": 0,
        "turnover_rate": 5.0,
    }
    base.update(overrides)
    return base


def _panel(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows))


RULE_V1_CFG = MomentumConfig(
    strength="none", buypoint="pullback", forbid_high_elasticity=True,
    stop_pct=0.05, take_profit_retrace=0.05, max_hold_days=5,
)


class TestScoreCandidatesFiltersAndRanks:
    def test_st_and_gem_excluded_by_entry_mask(self):
        panel = _panel(
            _row("MAIN1"),
            _row("STX", is_st=True),
            _row("GEM1", board="GEM"),  # rule v1 主板 only(forbid_high_elasticity)
        )
        out = score_candidates(panel, RULE_V1_CFG)
        assert [c.ts_code for c in out] == ["MAIN1"]

    def test_ranked_by_dist_from_high_desc_when_no_sector_bonus(self):
        panel = _panel(
            _row("C_FAR", dist_from_high_20d=-0.20),
            _row("C_NEAR", dist_from_high_20d=-0.01),
            _row("C_MID", dist_from_high_20d=-0.10),
        )
        out = score_candidates(panel, RULE_V1_CFG)
        assert [c.ts_code for c in out] == ["C_NEAR", "C_MID", "C_FAR"]
        assert out[0].rank == 1 and out[-1].rank == 3
        # 基础分公式:(1+dist)*100
        assert out[0].score == pytest.approx(99.0)
        assert out[1].score == pytest.approx(90.0)
        assert out[2].score == pytest.approx(80.0)

    def test_top_n_truncates(self):
        rows = [_row(f"C{i}", dist_from_high_20d=-0.01 * i) for i in range(10)]
        out = score_candidates(_panel(*rows), RULE_V1_CFG, top_n=3)
        assert len(out) == 3
        assert [c.rank for c in out] == [1, 2, 3]

    def test_empty_when_nothing_passes_mask(self):
        panel = _panel(_row("ONLYST", is_st=True))
        assert score_candidates(panel, RULE_V1_CFG) == []

    def test_score_never_claims_alpha_in_docstring_contract(self):
        # 轻量契约测试:分数是有限浮点,不是 inf/nan(评分公式退化保护)
        out = score_candidates(_panel(_row("C0")), RULE_V1_CFG)
        assert out[0].score == out[0].score  # not nan
        assert abs(out[0].score) < 1e6


class TestSectorBonus:
    def test_hot_sector_membership_can_flip_ranking(self):
        panel = _panel(
            _row("HOT", dist_from_high_20d=-0.10),   # base=90,命中热板块 +10 -> 100
            _row("COLD", dist_from_high_20d=-0.01),  # base=99,无板块加分
        )
        sector_scores = [SectorScore(index_code="SEC.A", name="板块甲", board_age=2, ret_20d=0.1, bonus=10.0, rank=1)]
        member_map = {"HOT": ["SEC.A"]}
        out = score_candidates(panel, RULE_V1_CFG, sector_scores=sector_scores, member_map=member_map)
        assert [c.ts_code for c in out] == ["HOT", "COLD"]
        assert out[0].score == pytest.approx(100.0)
        assert len(out[0].hot_sectors) == 1
        assert "板块甲" in out[0].hot_sectors[0]
        assert "板块年龄2天" in out[0].hot_sectors[0]  # 审判信息源要求板块年龄本身可查,不只是布尔态
        assert "10.0%" in out[0].hot_sectors[0]  # 20日动量一并展示

    def test_multiple_hot_sectors_bonus_is_capped_not_summed(self):
        panel = _panel(_row("MULTI", dist_from_high_20d=-0.10))  # base=90
        sector_scores = [
            SectorScore(index_code="SEC.A", name="板块甲", board_age=2, ret_20d=0.1, bonus=5.0, rank=1),
            SectorScore(index_code="SEC.B", name="板块乙", board_age=2, ret_20d=0.2, bonus=10.0, rank=2),
        ]
        member_map = {"MULTI": ["SEC.A", "SEC.B"]}
        out = score_candidates(panel, RULE_V1_CFG, sector_scores=sector_scores, member_map=member_map)
        assert out[0].score == pytest.approx(100.0)  # 90 + max(5,10),不是 90+15

    def test_non_hot_sector_membership_gives_no_bonus(self):
        panel = _panel(_row("C0", dist_from_high_20d=-0.10))
        member_map = {"C0": ["SEC.NOT_HOT"]}
        out = score_candidates(panel, RULE_V1_CFG, sector_scores=[], member_map=member_map)
        assert out[0].score == pytest.approx(90.0)
        assert out[0].hot_sectors == []

    def test_sector_names_shown_even_when_not_hot(self):
        panel = _panel(_row("C0"))
        out = score_candidates(
            panel, RULE_V1_CFG, member_map={"C0": ["SEC.X"]}, index_names={"SEC.X": "板块X"}
        )
        assert out[0].sector_names == ["板块X"]
        assert out[0].hot_sectors == []  # 不在热榜,不加分,但仍展示归属


class TestGenericRankByFallback:
    def test_unknown_rank_by_column_falls_back_to_percentile_rank(self):
        cfg = MomentumConfig(strength="none", buypoint="pullback", rank_by="turnover_rate", rank_desc=True)
        panel = _panel(
            _row("LOW", turnover_rate=1.0),
            _row("HIGH", turnover_rate=20.0),
            _row("MID", turnover_rate=10.0),
        )
        out = score_candidates(panel, cfg)
        assert [c.ts_code for c in out] == ["HIGH", "MID", "LOW"]
        assert all(0.0 <= c.score <= 100.0 for c in out)


class TestFourPieceText:
    def test_pullback_entry_plan_mentions_ma10_and_distance(self):
        row = _row("C0", ma10=9.5, dist_from_high_20d=-0.03)
        cfg = MomentumConfig(buypoint="pullback")
        text = entry_plan_text(row, cfg)
        assert "回调低吸" in text
        assert "9.5" in text or "9.50" in text

    def test_breakout_entry_plan_mentions_platform_high(self):
        row = _row("C0", prev_close_max_20d=11.2, vol_ratio_5=2.0)
        cfg = MomentumConfig(buypoint="breakout")
        text = entry_plan_text(row, cfg)
        assert "突破" in text
        assert "11.2" in text

    def test_stop_loss_text_uses_configured_pct(self):
        text = stop_loss_text(9.5, MomentumConfig(stop_pct=0.05))
        assert "9.5" in text or "9.50" in text
        assert "-5%" in text or "5%" in text
        assert "只设" in text  # 纪律措辞:只设不许撤不许下调

    def test_stop_loss_text_none_safe_when_no_stop_configured(self):
        text = stop_loss_text(None, MomentumConfig(stop_pct=None))
        assert "未设固定止损" in text

    def test_target_text_no_fixed_take_profit_line(self):
        text = target_text(MomentumConfig(take_profit_retrace=None, max_hold_days=5))
        assert "不设固定止盈线" in text
        assert "5 个交易日" in text

    def test_target_text_includes_retrace_when_configured(self):
        text = target_text(MomentumConfig(take_profit_retrace=0.05, max_hold_days=5))
        assert "回落" in text and "5%" in text

    def test_invalidation_spec_and_text_consistent(self):
        spec = invalidation_spec()
        text = invalidation_text(spec)
        assert "VWAP" in text
        assert f"{spec['vol_ratio_low']:.1f}" in text
        assert f"{spec['vol_ratio_high']:.1f}" in text
        assert spec["low_open_pct"] < 0


class TestPatternTags:
    def test_consecutive_limit_up_tag(self):
        assert "连板3日" in pattern_tags(_row("C0", consec_limit_up_days=3))

    def test_single_day_limit_up_tag_when_not_consecutive(self):
        assert "今日涨停" in pattern_tags(_row("C0", is_limit_up=True, consec_limit_up_days=0))

    def test_shallow_pullback_tag(self):
        assert "浅回调贴前高" in pattern_tags(_row("C0", dist_from_high_20d=-0.02))

    def test_deep_pullback_tag(self):
        assert "深回调" in pattern_tags(_row("C0", dist_from_high_20d=-0.20))

    def test_shrink_and_expand_volume_tags(self):
        assert "缩量" in pattern_tags(_row("C0", vol_ratio_5=0.5))
        assert "放量" in pattern_tags(_row("C0", vol_ratio_5=2.0))

    def test_board_label_tag_for_non_main(self):
        assert "创业板" in pattern_tags(_row("C0", board="GEM"))
        assert "科创板" in pattern_tags(_row("C0", board="STAR"))
        assert "北交所" in pattern_tags(_row("C0", board="BSE"))

    def test_main_board_gets_no_board_tag(self):
        tags = pattern_tags(_row("C0", board="MAIN"))
        assert "主板" not in tags and "MAIN" not in tags


class TestStockNameResolution:
    def test_resolves_name_from_stock_basic(self, isolated_env):
        insert_stock_basic(isolated_env, [{"ts_code": "600001.SH", "name": "示例股份"}])
        panel = _panel(_row("600001.SH"))
        out = score_candidates(panel, RULE_V1_CFG, db_path=isolated_env.db_path)
        assert out[0].name == "示例股份"

    def test_falls_back_to_ts_code_when_no_stock_basic_row(self, isolated_env):
        panel = _panel(_row("999999.SH"))
        out = score_candidates(panel, RULE_V1_CFG, db_path=isolated_env.db_path)
        assert out[0].name == "999999.SH"


class TestPublicDict:
    def test_excludes_raw_feature_row(self):
        out = score_candidates(_panel(_row("C0")), RULE_V1_CFG)
        d = out[0].public_dict()
        assert "raw" not in d
        assert d["ts_code"] == "C0"
        assert d["score"] == out[0].score
        assert d["pattern_tags"] == out[0].pattern_tags
