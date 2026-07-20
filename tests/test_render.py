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
        assert "仅评分与形态标签" in md
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
