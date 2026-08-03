"""LLM 提示词共享件单测(v1.5.2:当前日期锚 / 时效纪律 / 检索词时效引导)。

**这组测试锁的是一次真实报障**(2026-07-30,用户带截图):问询 603298 时,回答把
**2024 年研报的目标价**当成现行参照。生产 `inquiry_log` 实证联网是通的(search_hits
2489/8565 字节、含 2025-06 命中),但三处提示词**一处都没告诉模型今天几号** —— 模型
没有"现在"的概念。故本文件的断言全部围绕一件事:**三条 LLM 链路都必须拿到日期锚 +
时效纪律,且只有一份实现**。
"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.llm import prompt_context as pc


class TestDateAnchorLine:
    def test_states_today_with_chinese_weekday(self):
        line = pc.date_anchor_line(today=date(2026, 7, 30))     # 周四
        assert "今天是 2026年7月30日(周四)" in line
        assert line.endswith("。") or "。" in line

    @pytest.mark.parametrize("d,wd", [
        (date(2026, 7, 27), "周一"), (date(2026, 7, 31), "周五"),
        (date(2026, 8, 1), "周六"), (date(2026, 8, 2), "周日"),
    ])
    def test_weekday_mapping(self, d, wd):
        assert wd in pc.date_anchor_line(today=d)

    def test_mentions_next_trading_day(self, isolated_env):
        line = pc.date_anchor_line(today=date(2026, 7, 30))
        assert "下一交易日是 2026年7月31日(周五)" in line   # 07-31 是交易日(周五)

    def test_weekend_next_trading_day_is_monday_not_tomorrow(self, isolated_env):
        """周五生成的东西,"下一交易日"不是自然日的明天——这正是「明早」要点名的原因。"""
        assert "2026年8月3日(周一)" in pc.date_anchor_line(today=date(2026, 7, 31))

    def test_backfill_names_both_dates_and_does_not_lie(self, isolated_env):
        """补跑历史日:如实说今天几号 **且** 点明基准交易日,不假装今天是那天。"""
        line = pc.date_anchor_line(date(2026, 7, 27), today=date(2026, 7, 30))
        assert "今天是 2026年7月30日" in line
        assert "基准交易日是 2026年7月27日" in line and "补跑" in line

    def test_future_base_date_is_not_called_a_backfill(self, isolated_env):
        """基准日在今天之后(极少见)→ 措辞必须反过来说"那天还没到",不能套"补跑历史日"。"""
        line = pc.date_anchor_line(date(2026, 7, 31), today=date(2026, 7, 30))
        assert "尚未到来" in line and "补跑" not in line

    def test_same_day_does_not_emit_backfill_clause(self, isolated_env):
        line = pc.date_anchor_line(date(2026, 7, 30), today=date(2026, 7, 30))
        assert "基准交易日" not in line and "补跑" not in line

    def test_name_tomorrow_pins_the_morning_to_next_trading_day(self, isolated_env):
        """参考件的「明早剧本」必须点名是哪天早上(周五生成时"明早"=下周一)。"""
        line = pc.date_anchor_line(date(2026, 7, 31), today=date(2026, 7, 31), name_tomorrow=True)
        assert "明早 / 次日开盘" in line and "2026年8月3日(周一)" in line

    def test_calendar_failure_degrades_to_a_note_not_an_exception(self, monkeypatch):
        """日历算不出 → 锚少一项,**绝不抛异常**(日期锚是提示词装饰,不该掀翻 LLM 调用)。"""
        monkeypatch.setattr(pc, "next_trading_day",
                            lambda _d: (_ for _ in ()).throw(RuntimeError("boom")))
        line = pc.date_anchor_line(today=date(2026, 7, 30))
        assert "今天是 2026年7月30日" in line
        assert "算不出" in line


class TestRecencyHint:
    def test_year_is_dynamic_not_hardcoded(self):
        assert pc.recency_hint(date(2026, 7, 30)) == "2026 最新"
        assert pc.recency_hint(date(2027, 1, 1)) == "2027 最新"

    def test_hint_goes_right_after_subject_not_at_the_tail(self):
        """**刻意不放最末**:GLM `max_search_query_chars=78` 截尾会把末尾的时效词连同
        用户长问句一起切掉 = 等于没加。"""
        q = pc.search_subject_with_recency("康龙化成(300759.SZ)", "这只票最近的业绩怎么样",
                                           today=date(2026, 7, 30))
        assert q.index("2026 最新") < q.index("这只票")
        assert q.startswith("康龙化成(300759.SZ) 2026 最新")

    def test_hint_survives_provider_truncation_with_a_long_question(self):
        """把 GLM 的 78 字截断真跑一遍:时效词必须还在截断窗口内。"""
        from neckline.llm.providers.glm import GLMProvider
        long_q = "我看到有人说这票有新的产业催化," + "想知道后续会不会兑现以及风险在哪" * 4
        q = pc.search_subject_with_recency("康龙化成(300759.SZ)", long_q, today=date(2026, 7, 30))
        assert len(q) > GLMProvider.max_search_query_chars      # 前提:确实会被截
        assert "2026 最新" in q[: GLMProvider.max_search_query_chars]

    def test_empty_tail_and_empty_subject_are_safe(self):
        assert pc.search_subject_with_recency("600001.SH", today=date(2026, 7, 30)) == "600001.SH 2026 最新"
        assert pc.search_subject_with_recency("", "", today=date(2026, 7, 30)) == "2026 最新"


class TestTimelinessRulesAreInEveryPrompt:
    """时效纪律**只有一份实现**,三条链路(问询台 / 审判 / 参考件)的 system prompt
    逐个必须带上它——任何一处漏了,那条链路就会重犯"把旧研报当现行"的错。"""

    def test_rules_text_covers_the_three_demands(self):
        r = pc.TIMELINESS_RULES
        assert "带上该材料的日期" in r
        assert "超过半年" in r and "时效有限" in r
        assert "只能作为历史参照" in r and "不得" in r

    @pytest.mark.parametrize("prompt_ref", [
        "neckline.api.inquiry:INQUIRY_SYSTEM_PROMPT",
        "neckline.llm.judge:JUDGE_SYSTEM_PROMPT",
        "neckline.report.reference_plan:REFERENCE_PLAN_SYSTEM_PROMPT",
    ])
    def test_prompt_embeds_the_shared_rules_verbatim(self, prompt_ref):
        import importlib
        mod_name, attr = prompt_ref.split(":")
        prompt = getattr(importlib.import_module(mod_name), attr)
        assert pc.TIMELINESS_RULES in prompt, f"{prompt_ref} 缺时效纪律"

    def test_no_second_copy_of_the_rules_in_the_tree(self):
        """防"抄一份":全仓只允许 `prompt_context.py` 里出现这段规则的定义。"""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "neckline"
        needle = "只能作为历史参照"
        hits = [p.name for p in root.rglob("*.py") if needle in p.read_text(encoding="utf-8")]
        assert hits == ["prompt_context.py"], f"时效纪律被抄了第二份:{hits}"
