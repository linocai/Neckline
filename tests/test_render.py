"""报告 markdown 渲染单测(plan 2.5)。纯函数、手工构造 dataclass 输入(不碰任何
I/O,数据管线接线由 `tests/test_pipeline.py` 覆盖)——断言每个小节在各种输入组合
(空/非空、通过/否决/未激活、含/不含搜索来源、超过 top_n_judged)下都能正确渲染、
不崩,并且不吹嘘 alpha(§2.3 硬性要求)。"""

from __future__ import annotations

from datetime import date

from neckline.llm.base import SearchHit
from neckline.llm.judge import VERDICT_INACTIVE, VERDICT_PASS, VERDICT_VETO, JudgeResult
from neckline.report.candidates import Candidate
from neckline.report.render import render_markdown
from neckline.report.sectors import SectorScore
from neckline.report.sentiment import SentimentDashboard
from neckline.report.watchlist_check import WatchlistCheckItem

D = date(2026, 3, 4)


def _sentiment(**overrides) -> SentimentDashboard:
    base = dict(
        trade_date=D, limit_up_count=40, limit_down_count=5, zaban_count=5, zaban_rate=0.10,
        max_consec_limit_up=3, prev_limit_up_premium_avg=0.01, prev_limit_up_sample=10,
        position_quota="满额", quota_reason="涨停40家/跌停5家/炸板率10%/最高连板3板(阈值未回测,实盘归因迭代中)",
    )
    base.update(overrides)
    return SentimentDashboard(**base)


def _sector(**overrides) -> SectorScore:
    base = dict(index_code="AAA.TI", name="人工智能", board_age=2, ret_20d=0.12, bonus=3.0, rank=1)
    base.update(overrides)
    return SectorScore(**base)


def _candidate(**overrides) -> Candidate:
    base = dict(
        ts_code="600001.SH", name="示例甲", close=10.5, score=95.0, rank=1, board="MAIN",
        pattern_tags=["浅回调贴前高"], hot_sectors=["人工智能"], sector_names=["人工智能"],
        entry_plan="回调低吸:现价10.5...", stop_loss="参考止损价约9.98元...",
        target="不设固定止盈线...", invalidation_text="次日低开...跌破VWAP...", invalidation_spec={}, raw={},
    )
    base.update(overrides)
    return Candidate(**base)


def _render(**overrides) -> str:
    base = dict(
        trade_date=D, strategy_version="v1", generated_at="2026-07-20T00:00:00+00:00",
        sentiment=_sentiment(), sectors=[], candidates=[], judged={}, top_n_judged=10,
    )
    base.update(overrides)
    return render_markdown(**base)


class TestSentimentSection:
    def test_contains_position_quota_and_metrics(self):
        md = _render()
        assert "情绪仪表盘" in md
        assert "满额" in md
        assert "涨停" in md

    def test_no_premium_data_renders_explicit_note_not_zero(self):
        md = _render(sentiment=_sentiment(prev_limit_up_premium_avg=None, prev_limit_up_sample=0))
        assert "无数据" in md


class TestSectorsSection:
    def test_empty_sectors_shows_placeholder_not_crash(self):
        md = _render(sectors=[])
        assert "无概念板块数据" in md

    def test_sector_table_rendered_with_name_and_momentum(self):
        md = _render(sectors=[_sector()])
        assert "人工智能" in md
        assert "12.0%" in md

    def test_soft_weight_disclaimer_present(self):
        md = _render(sectors=[_sector()])
        assert "不圈死" in md


class TestCandidatesSection:
    def test_no_candidates_shows_placeholder(self):
        md = _render(candidates=[])
        assert "无候选" in md

    def test_judged_pass_shows_badge_and_narrative(self):
        c = _candidate()
        jr = JudgeResult(ts_code="600001.SH", provider="glm", model="glm-5.2", verdict=VERDICT_PASS,
                          narrative="这是一段自由叙述的分析,没有分栏结构。", degraded=False)
        md = _render(candidates=[c], judged={"600001.SH": jr})
        assert "✅ 通过" in md
        assert "这是一段自由叙述的分析" in md
        assert "买点" in md and "止损" in md and "目标" in md and "证伪条件" in md

    def test_judged_veto_shows_veto_badge(self):
        c = _candidate(ts_code="600002.SH")
        jr = JudgeResult(ts_code="600002.SH", provider="glm", model="glm-5.2", verdict=VERDICT_VETO,
                          narrative="利空明显。", degraded=False)
        md = _render(candidates=[c], judged={"600002.SH": jr})
        assert "🚫 否决" in md

    def test_inactive_llm_shows_inactive_note(self):
        c = _candidate()
        jr = JudgeResult(ts_code="600001.SH", provider="none", model="", verdict=VERDICT_INACTIVE,
                          narrative="LLM 未激活(.env 未配置 LLM_PROVIDER/LLM_API_KEY)。",
                          degraded=True, degrade_reason="未配置 LLM_PROVIDER/LLM_API_KEY")
        md = _render(candidates=[c], judged={"600001.SH": jr})
        assert "未激活" in md

    def test_search_hits_rendered_as_sources(self):
        c = _candidate()
        hit = SearchHit(title="标题X", link="https://example.com/a", content="摘要")
        jr = JudgeResult(ts_code="600001.SH", provider="glm", model="glm-5.2", verdict=VERDICT_PASS,
                          narrative="正文。", degraded=False, search_hits=[hit])
        md = _render(candidates=[c], judged={"600001.SH": jr})
        assert "https://example.com/a" in md

    def test_scored_only_section_beyond_top_n_judged(self):
        c1 = _candidate(ts_code="600001.SH", rank=1)
        c2 = _candidate(ts_code="600002.SH", rank=2, name="示例乙")
        jr1 = JudgeResult(ts_code="600001.SH", provider="glm", model="glm-5.2", verdict=VERDICT_PASS, narrative="x", degraded=False)
        md = _render(candidates=[c1, c2], judged={"600001.SH": jr1}, top_n_judged=1)
        assert "仅情报排序与形态标签" in md   # v1.3-③-C3:候选后N只节改「情报排序」口径
        assert "600002.SH" in md
        assert "示例乙" in md

    def test_missing_judgment_for_top_candidate_shows_explicit_warning(self):
        c = _candidate()
        md = _render(candidates=[c], judged={})
        assert "未执行" in md

    def test_alpha_disclaimer_present(self):
        md = _render()
        assert "不是正 alpha" in md
        assert "不构成收益预测" in md


def _watch_item(**overrides) -> WatchlistCheckItem:
    base = dict(
        ts_code="600003.SH", name="示例丙", pinned=False, source="manual", has_data=True,
        close=8.0, board="MAIN", score=70.0, pattern_tags=["均线多头"],
        green_light=True, buy_point_triggered=True,
        entry_plan="回调低吸:现价8.0...", stop_loss="参考止损价约7.6元...",
        target="不设固定止盈线...", invalidation_text="次日低开...跌破VWAP...",
    )
    base.update(overrides)
    return WatchlistCheckItem(**base)


class TestWatchlistSection:
    """v1.1-C.3 自选体检节(独立于候选,标题明确分开)。"""

    def test_empty_watchlist_shows_placeholder(self):
        md = _render(watchlist_check=[])
        assert "自选体检" in md
        assert "自选池为空" in md

    def test_omitted_watchlist_defaults_to_empty_section_not_crash(self):
        """`watchlist_check` 参数省略(旧调用点)→ 不崩,按空处理(向后兼容)。"""
        md = _render()
        assert "自选体检" in md
        assert "自选池为空" in md

    def test_green_light_triggered_item_shows_four_piece(self):
        item = _watch_item()
        md = _render(watchlist_check=[item])
        assert "🟢 可动" in md
        assert "示例丙" in md and "600003.SH" in md
        assert "回调低吸:现价8.0" in md
        assert "参考止损价约7.6元" in md

    def test_red_light_item_shows_disqualifier_reason(self):
        item = _watch_item(green_light=False, buy_point_triggered=False,
                           disqualifiers=["ST/*ST(选股域清洗,禁买)"])
        md = _render(watchlist_check=[item])
        assert "🔴 禁买" in md
        assert "ST/*ST" in md

    def test_not_triggered_item_shows_no_buy_point_note(self):
        item = _watch_item(buy_point_triggered=False, entry_plan="今日未触发母战法买点(仅供关注,非现在买入建议)。")
        md = _render(watchlist_check=[item])
        assert "今日未触发母战法买点" in md

    def test_no_data_item_shows_reason_not_crash(self):
        from neckline.report.watchlist_check import NO_DATA_REASON
        item = _watch_item(has_data=False, close=0.0, score=None, disqualifiers=[NO_DATA_REASON])
        md = _render(watchlist_check=[item])
        assert NO_DATA_REASON in md

    def test_pinned_and_status_changed_badges(self):
        item = _watch_item(pinned=True, status_changed=True)
        md = _render(watchlist_check=[item])
        assert "📌 已点名" in md
        assert "🔔 状态变化" in md

    def test_llm_judgment_narrative_rendered(self):
        item = _watch_item(status_changed=True, llm_judgment={
            "verdict": "通过", "narrative": "一段自由叙述的分析,近期无明显利空。", "degraded": False,
        })
        md = _render(watchlist_check=[item])
        assert "一段自由叙述的分析" in md
        assert "✅ 通过" in md

    def test_no_llm_judgment_omits_llm_block(self):
        """未变化也未 pinned → 不跑 LLM,渲染层不应出现 LLM 审判段落。"""
        item = _watch_item(llm_judgment=None)
        md = _render(watchlist_check=[item])
        assert "LLM 审判" not in md

    def test_watchlist_section_does_not_mix_into_candidates_section(self):
        """自选体检独立一节,不与候选榜混排。"""
        cand = _candidate(ts_code="600001.SH")
        watch = _watch_item(ts_code="600003.SH")
        md = _render(candidates=[cand], watchlist_check=[watch])
        assert md.index("## 候选") < md.index("## 自选体检")
