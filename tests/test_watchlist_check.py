"""自选体检单测(plan §五 v1.1-C.3)。用手工构造的【特征行】(与 test_candidates.py
同一套约定)直接测 `score_watchlist` 的评分/红绿灯/买点触发/四件套逻辑——评分公式
与买点触发均直接复用 `report.candidates`/`strategy.momentum` 本尊(同码,§2.6),
不是重新实现一份;`TestScoreSameAsCandidates` 是 C 验收标准「自选体检评分与候选
评分同码一致」的直接证据。`apply_llm_review` 覆盖「状态变化」diff 定义与 LLM
控成本(只审 changed∪pinned)。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List

import polars as pl
import pytest

from neckline.llm.base import ChatMessage, LLMResult
from neckline.report.candidates import pattern_tags as candidates_pattern_tags
from neckline.report.candidates import score_candidates
from neckline.report.sectors import SectorScore
from neckline.report.watchlist_check import (
    WatchlistCheckItem,
    apply_llm_review,
    score_watchlist,
)
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


def _watch(ts_code: str, **overrides) -> Dict[str, Any]:
    base: Dict[str, Any] = {"ts_code": ts_code, "name": ts_code, "pinned": False, "source": "manual"}
    base.update(overrides)
    return base


RULE_V1_CFG = MomentumConfig(
    strength="none", buypoint="pullback", forbid_high_elasticity=True,
    stop_pct=0.05, take_profit_retrace=0.05, max_hold_days=5,
)


class TestScoreWatchlistBasic:
    def test_green_light_when_passes_discipline(self):
        panel = _panel(_row("MAIN1"))
        out = score_watchlist(panel, RULE_V1_CFG, [_watch("MAIN1")])
        assert len(out) == 1
        item = out[0]
        assert item.has_data is True
        assert item.green_light is True
        assert item.disqualifiers == []

    def test_red_light_when_st(self):
        panel = _panel(_row("STX", is_st=True))
        out = score_watchlist(panel, RULE_V1_CFG, [_watch("STX")])
        item = out[0]
        assert item.green_light is False
        assert any("选股域" in d for d in item.disqualifiers)

    def test_red_light_when_high_elasticity_board(self):
        panel = _panel(_row("GEM1", board="GEM"))
        out = score_watchlist(panel, RULE_V1_CFG, [_watch("GEM1")])
        item = out[0]
        assert item.green_light is False
        assert any("高弹题材" in d for d in item.disqualifiers)

    def test_green_light_when_high_elasticity_disabled_in_config(self):
        """红绿灯读现役 config——`forbid_high_elasticity=False` 时创业板不再触发
        这条禁买(与 `build_entry_mask` 的 if 分支同步,不硬编)。"""
        cfg = MomentumConfig(strength="none", buypoint="pullback", forbid_high_elasticity=False)
        panel = _panel(_row("GEM1", board="GEM"))
        out = score_watchlist(panel, cfg, [_watch("GEM1")])
        assert out[0].green_light is True

    def test_buy_point_triggered_true_when_entry_mask_passes(self):
        # RULE_V1_CFG.buypoint="pullback":ret_1d<=0 且 close>=ma10 → _row 默认值满足
        panel = _panel(_row("MAIN1"))
        out = score_watchlist(panel, RULE_V1_CFG, [_watch("MAIN1")])
        item = out[0]
        assert item.buy_point_triggered is True
        assert "回调低吸" in item.entry_plan
        assert item.entry_spec["ma10"] == pytest.approx(9.5)
        assert item.invalidation_spec["vol_ratio_high"] == pytest.approx(3.0)
        assert item.target and item.stop_loss

    def test_buy_point_not_triggered_gives_placeholder_text(self):
        # ret_1d > 0(未回调)→ pullback 不触发
        panel = _panel(_row("MAIN1", ret_1d=0.03))
        out = score_watchlist(panel, RULE_V1_CFG, [_watch("MAIN1")])
        item = out[0]
        assert item.buy_point_triggered is False
        assert "未触发" in item.entry_plan
        assert item.entry_spec == {}
        assert item.invalidation_spec == {}

    def test_no_data_when_code_missing_from_panel(self):
        panel = _panel(_row("MAIN1"))
        out = score_watchlist(panel, RULE_V1_CFG, [_watch("ZZZ999.SZ")])
        item = out[0]
        assert item.has_data is False
        assert item.green_light is False
        assert item.disqualifiers

    def test_empty_panel_all_no_data(self):
        out = score_watchlist(pl.DataFrame(), RULE_V1_CFG, [_watch("MAIN1"), _watch("MAIN2")])
        assert len(out) == 2
        assert all(not it.has_data for it in out)

    def test_empty_watchlist_items_returns_empty_list(self):
        panel = _panel(_row("MAIN1"))
        assert score_watchlist(panel, RULE_V1_CFG, []) == []

    def test_pinned_and_source_passed_through(self):
        panel = _panel(_row("MAIN1"))
        out = score_watchlist(panel, RULE_V1_CFG, [_watch("MAIN1", pinned=True, source="inquiry")])
        assert out[0].pinned is True and out[0].source == "inquiry"

    def test_pattern_tags_reused_from_candidates_module(self):
        row = _row("MAIN1", consec_limit_up_days=2)
        panel = _panel(row)
        out = score_watchlist(panel, RULE_V1_CFG, [_watch("MAIN1")])
        assert out[0].pattern_tags == candidates_pattern_tags(row)

    def test_multiple_watchlist_items_each_scored_independently(self):
        panel = _panel(_row("A", dist_from_high_20d=-0.01), _row("STX", is_st=True))
        out = score_watchlist(panel, RULE_V1_CFG, [_watch("A"), _watch("STX")])
        by_code = {it.ts_code: it for it in out}
        assert by_code["A"].green_light is True
        assert by_code["STX"].green_light is False


class TestScoreSameAsCandidates:
    """C 验收标准:「自选体检评分与候选评分同码一致(同 test_report_consistency
    姿势)」——同一行喂两条管线,分数应完全一致(均是 `_base_score_expr` + 板块
    加分 round(...,1) 后的结果)。"""

    def test_score_matches_candidates_score_for_same_row(self):
        row = _row("MAIN1", dist_from_high_20d=-0.05)
        panel = _panel(row)
        cand_out = score_candidates(panel, RULE_V1_CFG)
        watch_out = score_watchlist(panel, RULE_V1_CFG, [_watch("MAIN1")])
        assert cand_out[0].score == watch_out[0].score

    def test_score_matches_with_sector_bonus(self):
        row = _row("MAIN1")
        panel = _panel(row)
        sector = SectorScore(index_code="883300.TI", name="示例概念", board_age=2, ret_20d=0.05, bonus=3.0, rank=1)
        member_map = {"MAIN1": ["883300.TI"]}
        cand_out = score_candidates(
            panel, RULE_V1_CFG, sector_scores=[sector], member_map=member_map,
        )
        watch_out = score_watchlist(
            panel, RULE_V1_CFG, [_watch("MAIN1")], sector_scores=[sector], member_map=member_map,
        )
        assert cand_out[0].score == watch_out[0].score
        assert watch_out[0].hot_sectors   # 板块加分命中同步反映在展示文案里

    def test_watchlist_item_not_passing_mask_still_scored_unlike_candidates(self):
        """候选评分会把不过 mask 的票直接剔除(不出现在输出里);自选体检**不受
        mask 约束**——即便今日不满足买点/强势,也照样给出评分/红绿灯(自选是
        用户主理的池,不是"入围候选"的资格判定)。"""
        panel = _panel(_row("MAIN1", ret_1d=0.05))   # 未回调,pullback 不触发
        cand_out = score_candidates(panel, RULE_V1_CFG)
        assert cand_out == []   # 候选评分:不过 mask → 不出现
        watch_out = score_watchlist(panel, RULE_V1_CFG, [_watch("MAIN1")])
        assert len(watch_out) == 1 and watch_out[0].has_data is True   # 体检:仍给出评估


class TestStatusChangedDiff:
    """状态变化定义(任务原文钉死,单测锁死):无上一份快照(首次出现)/ 红绿灯
    翻转 / 买点触发翻转 / 形态标签集合变化 → 变化。"""

    def _item(self, **overrides) -> WatchlistCheckItem:
        base = dict(
            ts_code="MAIN1", name="示例", pinned=False, source="manual", has_data=True,
            green_light=True, buy_point_triggered=False, pattern_tags=["均线多头"],
        )
        base.update(overrides)
        return WatchlistCheckItem(**base)

    def test_no_previous_snapshot_is_changed(self):
        item = self._item()
        apply_llm_review([item], {}, provider=None)
        assert item.status_changed is True

    def test_green_light_flip_is_changed(self):
        item = self._item(green_light=False)
        prev = {"MAIN1": {"green_light": True, "buy_point_triggered": False, "pattern_tags": ["均线多头"]}}
        apply_llm_review([item], prev, provider=None)
        assert item.status_changed is True

    def test_buy_point_flip_is_changed(self):
        item = self._item(buy_point_triggered=True)
        prev = {"MAIN1": {"green_light": True, "buy_point_triggered": False, "pattern_tags": ["均线多头"]}}
        apply_llm_review([item], prev, provider=None)
        assert item.status_changed is True

    def test_buy_point_flip_the_other_direction_is_also_changed(self):
        """任务原文点名的方向是"首次触发"(False→True);这里对称验证
        True→False(不再满足买点条件)也算变化。"""
        item = self._item(buy_point_triggered=False)
        prev = {"MAIN1": {"green_light": True, "buy_point_triggered": True, "pattern_tags": ["均线多头"]}}
        apply_llm_review([item], prev, provider=None)
        assert item.status_changed is True

    def test_pattern_tags_change_is_changed(self):
        item = self._item(pattern_tags=["放量"])
        prev = {"MAIN1": {"green_light": True, "buy_point_triggered": False, "pattern_tags": ["均线多头"]}}
        apply_llm_review([item], prev, provider=None)
        assert item.status_changed is True

    def test_pattern_tags_order_insensitive(self):
        item = self._item(pattern_tags=["A", "B"])
        prev = {"MAIN1": {"green_light": True, "buy_point_triggered": False, "pattern_tags": ["B", "A"]}}
        apply_llm_review([item], prev, provider=None)
        assert item.status_changed is False

    def test_identical_snapshot_not_changed(self):
        item = self._item()
        prev = {"MAIN1": {"green_light": True, "buy_point_triggered": False, "pattern_tags": ["均线多头"]}}
        apply_llm_review([item], prev, provider=None)
        assert item.status_changed is False

    def test_score_change_alone_does_not_count_as_status_changed(self):
        """评分每日随行情连续变动,不算"状态"——只有其它三项翻转才算变化。"""
        item = self._item(score=50.0)
        prev = {
            "MAIN1": {"green_light": True, "buy_point_triggered": False, "pattern_tags": ["均线多头"], "score": 90.0},
        }
        apply_llm_review([item], prev, provider=None)
        assert item.status_changed is False


@dataclass
class _CountingProvider:
    """可注入的假 LLM provider:记录每次调用,免联网(同 test_api_inquiry.py 的
    `StubProvider` 姿势)。"""
    calls: List[str] = field(default_factory=list)
    tag: str = "通过"
    ok: bool = True

    def chat(self, messages: List[ChatMessage], *, enable_search: bool = True, transport=None) -> LLMResult:
        self.calls.append(messages[-1].content)
        return LLMResult(ok=self.ok, content=f"分析内容。\n结论:{self.tag}", provider="glm", model="glm-5.2")


class TestApplyLlmReviewCostControl:
    """LLM 控成本(任务拍板):体检只对「changed ∪ pinned」跑 LLM,其余确定性
    输出、不耗 LLM。复用 `llm.judge.judge_candidate`(降级链继承)。"""

    def _item(self, ts_code: str = "MAIN1", **overrides) -> WatchlistCheckItem:
        base = dict(
            ts_code=ts_code, name="示例", pinned=False, source="manual", has_data=True,
            close=10.0, board="MAIN", pattern_tags=[], sector_names=[], hot_sectors=[],
            entry_plan="计划", stop_loss="止损", green_light=True, buy_point_triggered=False,
        )
        base.update(overrides)
        return WatchlistCheckItem(**base)

    def test_changed_item_is_judged(self):
        item = self._item(green_light=False)   # 无 prev → 首次出现视为变化
        prov = _CountingProvider()
        apply_llm_review([item], {}, provider=prov)
        assert item.status_changed is True
        assert item.llm_judgment is not None
        assert item.llm_judgment["verdict"] == "通过"
        assert len(prov.calls) == 1

    def test_unchanged_and_unpinned_item_not_judged(self):
        item = self._item()
        prev = {"MAIN1": {"green_light": True, "buy_point_triggered": False, "pattern_tags": []}}
        prov = _CountingProvider()
        apply_llm_review([item], prev, provider=prov)
        assert item.status_changed is False
        assert item.llm_judgment is None
        assert prov.calls == []

    def test_unchanged_but_pinned_item_is_still_judged(self):
        item = self._item(pinned=True)
        prev = {"MAIN1": {"green_light": True, "buy_point_triggered": False, "pattern_tags": []}}
        prov = _CountingProvider()
        apply_llm_review([item], prev, provider=prov)
        assert item.status_changed is False      # 状态没变
        assert item.llm_judgment is not None      # 但因为 pinned 仍审
        assert len(prov.calls) == 1

    def test_no_data_item_never_judged_even_if_pinned(self):
        item = self._item(has_data=False, pinned=True, green_light=False,
                          disqualifiers=["当日行情面板查无该票"])
        prov = _CountingProvider()
        apply_llm_review([item], {}, provider=prov)
        assert item.llm_judgment is None
        assert prov.calls == []

    def test_mixed_batch_only_changed_or_pinned_get_judged(self):
        unchanged = self._item(ts_code="A")
        changed = self._item(ts_code="B", green_light=False)
        pinned = self._item(ts_code="C", pinned=True)
        prev = {
            "A": {"green_light": True, "buy_point_triggered": False, "pattern_tags": []},
            "B": {"green_light": True, "buy_point_triggered": False, "pattern_tags": []},
            "C": {"green_light": True, "buy_point_triggered": False, "pattern_tags": []},
        }
        prov = _CountingProvider()
        apply_llm_review([unchanged, changed, pinned], prev, provider=prov)
        assert unchanged.llm_judgment is None
        assert changed.llm_judgment is not None
        assert pinned.llm_judgment is not None
        assert len(prov.calls) == 2

    def test_provider_none_degrades_gracefully(self):
        item = self._item(pinned=True)
        apply_llm_review([item], {}, provider=None)
        assert item.llm_judgment is not None
        assert item.llm_judgment["degraded"] is True
        assert item.llm_judgment["verdict"] == "未激活"
