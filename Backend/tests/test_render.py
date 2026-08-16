"""报告 markdown 渲染单测(plan 2.5)。纯函数、手工构造 dataclass 输入(不碰任何
I/O,数据管线接线由 `tests/test_pipeline.py` 覆盖)——断言每个小节在各种输入组合
(空/非空、通过/否决/未激活、含/不含搜索来源、超过 top_n_judged)下都能正确渲染、
不崩,并且不吹嘘 alpha(§2.3 硬性要求)。**v1.5-③ 起新增**:候选卡参考三件套四态
(ok/部分被拦/vetoed/unavailable/None)、执行提示行、持仓体检节。"""

from __future__ import annotations

from datetime import date

from neckline.llm.base import SearchHit
from neckline.llm.judge import VERDICT_INACTIVE, VERDICT_PASS, VERDICT_VETO, JudgeResult
from neckline.report.holding_k4_check import HoldingK4Hit, HoldingK4Item
from neckline.report.render import render_markdown
from neckline.report.sectors import SectorScore
from neckline.report.sentiment import SentimentDashboard
from neckline.sentinel.precall import (
    HARD_CAP_EXIT,
    HOLDING,
    PROFIT_EXEMPT,
    SUSPENDED_HOLD,
    TIME_EXIT_NEXT_DAY,
)

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


def _holding_item(**overrides) -> HoldingK4Item:
    base = dict(
        position_id=1, ts_code="600001.SH", name="示例甲", has_data=True, d_count=3,
        close=10.5, net_float=120.5, time_exit_state=HOLDING, max_hold_effective=5,
    )
    base.update(overrides)
    return HoldingK4Item(**base)


def _render(**overrides) -> str:
    base = dict(
        trade_date=D, strategy_version="v1", generated_at="2026-07-20T00:00:00+00:00",
        sentiment=_sentiment(), sectors=[],
    )
    base.update(overrides)
    return render_markdown(**base)


class TestSentimentSection:
    def test_report_date_is_title_and_trade_date_is_disclosed_as_data_cutoff(self):
        md = _render(report_date=date(2026, 3, 8))
        assert md.startswith("# Neckline 篮子日报 · 2026-03-08")
        assert "行情数据截至:2026-03-04" in md

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


class TestHoldingCheckSection:
    """持仓体检节(v1.5-③-C,需求 9「今日计划拆两块:持仓股 / 候选列表」的 markdown
    落地)。数据源 = pipeline 已算好的 `holding_k4_check`,本节只测渲染,不测判定
    逻辑本身(那是 `tests/test_holding_k4_check.py` 的职责)。"""

    def test_empty_holdings_shows_placeholder_not_omitted(self):
        """空持仓仍要有这一节(节在 = 体检跑过了),不能因为空列表整节消失。"""
        md = _render(holding_k4_check=[])
        assert "## ② 持仓体检" in md
        assert "今日无持仓" in md

    def test_omitted_holding_check_defaults_to_empty_not_crash(self):
        """`holding_k4_check` 参数省略(旧调用点/向后兼容)→ 按空处理,不崩。"""
        md = _render()
        assert "## ② 持仓体检" in md
        assert "今日无持仓" in md

    def test_holding_section_appears_before_market_context_sections(self):
        """「持仓管理优先于选新票」的顺序不变(客户端 v1.1-E.1 镜像)。⑭-A 五段结构
        落地后锚点换成 **③ 今日篮子**(② 持仓体检 → ③ 今日篮子),顺序纪律本身不许动;
        同时锁死「市场语境 ① 在持仓体检 ② 之前」这半句。"""
        item = _holding_item()
        md = _render(holding_k4_check=[item])
        assert md.index("## ① 情绪与市场语境") < md.index("## ② 持仓体检")
        assert md.index("## ② 持仓体检") < md.index("## ③ 今日篮子")

    def test_holding_item_shows_code_name_dcount_state_and_net_float(self):
        item = _holding_item(
            ts_code="600002.SH", name="示例乙", d_count=4, max_hold_effective=5,
            time_exit_state=HOLDING, net_float=-88.8,
        )
        md = _render(holding_k4_check=[item])
        assert "示例乙(600002.SH)" in md
        assert "D4" in md and "D5" in md
        assert "持有中" in md
        assert "-88.8 元" in md or "-88.8" in md

    def test_no_time_exit_clause_says_so_instead_of_a_fake_cap(self):
        """V2.2-⑤:`max_hold_effective is None`(章程无时间退出条款)→ **如实说没有**,
        ⛔ 不渲染成「有效上限 D None」,也不拿默认 5 顶上(§3.11-E 哨兵位同一种病)。"""
        item = _holding_item(d_count=9, max_hold_effective=None, time_exit_state=HOLDING)
        md = _render(holding_k4_check=[item])
        assert "D9" in md
        assert "本版章程无时间退出条款" in md
        assert "有效上限 D" not in md and "D None" not in md

    def test_net_float_unknown_shown_explicitly_not_blank(self):
        item = _holding_item(net_float=None)
        md = _render(holding_k4_check=[item])
        assert "净浮盈" in md
        assert "未知" in md

    def test_suspended_hold_state_labeled_explicitly(self):
        """时间退出态第五态 `suspended_hold`(v1.4-①-B,§七 P0-2)必须有专属文案,
        不与 `holding` 混淆——plan §五 v1.5-③-C 明写"时间退出态(含 suspended_hold)"。"""
        item = _holding_item(time_exit_state=SUSPENDED_HOLD)
        md = _render(holding_k4_check=[item])
        assert "判向挂起" in md

    def test_hard_cap_and_profit_exempt_states_have_distinct_labels(self):
        item1 = _holding_item(ts_code="600001.SH", time_exit_state=HARD_CAP_EXIT)
        item2 = _holding_item(ts_code="600002.SH", time_exit_state=PROFIT_EXEMPT)
        md1 = _render(holding_k4_check=[item1])
        md2 = _render(holding_k4_check=[item2])
        assert "浮盈硬上限" in md1
        assert "浮盈豁免" in md2
        assert "浮盈硬上限" not in md2 and "浮盈豁免" not in md1

    def test_time_exit_next_day_state_labeled(self):
        item = _holding_item(time_exit_state=TIME_EXIT_NEXT_DAY)
        md = _render(holding_k4_check=[item])
        assert "时间退出" in md

    def test_locked_date_shown_when_present(self):
        item = _holding_item(
            time_exit_state=TIME_EXIT_NEXT_DAY,
            time_exit_locked_state=TIME_EXIT_NEXT_DAY, time_exit_locked_date="20260728",
            time_exit_locked_net_float=-10.0,
        )
        md = _render(holding_k4_check=[item])
        assert "20260728" in md
        assert "定格" in md

    def test_no_locked_date_omits_locked_line(self):
        item = _holding_item(time_exit_locked_date=None)
        md = _render(holding_k4_check=[item])
        assert "定格" not in md

    def test_data_unavailable_shows_suspended_note_not_normal_check(self):
        """v1.4-①-B(§七 P0-2):当日无 EOD 行(停牌/数据缺口)→ 整份体检跳过,
        `dataUnavailable` 如实标注,不能装作正常体检过。"""
        item = _holding_item(has_data=False, d_count=6)
        md = _render(holding_k4_check=[item])
        assert "无 EOD 行情" in md or "停牌" in md
        assert "D6" in md   # d_count 照常累计展示
        assert "持有中" not in md   # 不应误显示成正常体检结果

    def test_k4_hits_split_into_strong_and_normal(self):
        item = _holding_item(hits=[
            HoldingK4Hit(code="A3_belowyear_limitup", label="年线下涨停(疑似诱多做局派发)",
                         level="strong", evidence="ev1", evidence_strength="price_volume"),
            HoldingK4Hit(code="B3_theme_persist_2_3", label="题材持续2-3天(认可题材=接盘侧;成分类参考)",
                         level="normal", evidence="ev2", evidence_strength="constituent"),
        ], has_strong=True)
        md = _render(holding_k4_check=[item])
        assert "强警示" in md and "年线下涨停" in md
        assert "普通警示" in md and "题材持续2-3天" in md

    def test_no_k4_hits_shows_no_hit_note(self):
        item = _holding_item(hits=[])
        md = _render(holding_k4_check=[item])
        assert "无命中" in md

    def test_multiple_holdings_each_rendered(self):
        item1 = _holding_item(ts_code="600001.SH", name="示例甲")
        item2 = _holding_item(ts_code="600002.SH", name="示例乙")
        md = _render(holding_k4_check=[item1, item2])
        assert "示例甲(600001.SH)" in md
        assert "示例乙(600002.SH)" in md


def _news_alerts(**overrides) -> "NewsAlertsReport":
    from neckline.report.news_alerts import NewsAlertsReport

    base = dict(trade_date=D)
    base.update(overrides)
    return NewsAlertsReport(**base)


class TestNewsAlertsSection:
    """消息面节(C4,plan §五 v1.3-③-C4)。核心断言:①「没扫到」与「扫了没有」
    两种空态渲染成不同文案(硬要求,不许静默当成"没有公告");②命中条目正确入表;
    ③ None(未生成)不崩。"""

    def test_none_report_shows_placeholder_not_crash(self):
        md = _render()
        assert "### 消息面" in md
        assert "未生成" in md

    def test_unscanned_source_shows_explicit_warning_not_silent_empty(self):
        from neckline.report.news_alerts import NewsAlertScanStatus, SOURCE_LLM_PREFIX, SOURCE_TUSHARE_HOLDERTRADE

        report = _news_alerts(scan_statuses=[
            NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=False, reason="TuShare stk_holdertrade 调用失败:token 缺失"),
            NewsAlertScanStatus(source=SOURCE_LLM_PREFIX, scanned=False, reason="未配置 LLM_PROVIDER/LLM_API_KEY"),
        ])
        md = _render(news_alerts=report)
        assert "本次未扫描" in md
        assert "token 缺失" in md
        assert "LLM_PROVIDER" in md

    def test_scanned_clean_shows_confirmed_no_alerts_not_same_as_unscanned(self):
        from neckline.report.news_alerts import NewsAlertScanStatus, SOURCE_LLM_PREFIX, SOURCE_TUSHARE_HOLDERTRADE

        report = _news_alerts(scan_statuses=[
            NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=True),
            NewsAlertScanStatus(source=SOURCE_LLM_PREFIX, scanned=True, codes_total=2),
        ])
        md = _render(news_alerts=report)
        # 逐源状态行的「未扫描」用 ⚠ + 粗体标出(见 render.py `**本次未扫描**`),
        # 与本测试下方"未命中条目"的兜底提示句(纯文本提及"本次未扫描"作为
        # 判断线索之一)刻意区分——后者不应被误判为"确实有源未扫描"。
        # ⚠ **断言锚在消息面这一小节内**(⑭-A 起整份报告的其它段落会正常出现 ⚠ ——
        # 如 ③ 今日篮子的「本段未取得」;对整份 md 断言"没有 ⚠"会把本测试变成一条
        # 与消息面无关的脆弱断言)。
        section = md.split("### 消息面")[1].split("## ③ 今日篮子")[0]
        assert "⚠" not in section
        assert "**本次未扫描**" not in section
        assert "已扫描" in section
        assert "确认无此类消息" in section or "未发现命中条目" in section

    def test_hit_items_rendered_in_table_with_category_label(self):
        from neckline.report.news_alerts import NewsAlertItem, NewsAlertScanStatus, SOURCE_TUSHARE_HOLDERTRADE

        report = _news_alerts(
            items=[NewsAlertItem(
                ts_code="600001.SH", name="示例甲", category="REDUCTION",
                summary="张三(高管)减持 50,000 股", source=SOURCE_TUSHARE_HOLDERTRADE,
            )],
            scan_statuses=[NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=True)],
        )
        md = _render(news_alerts=report)
        assert "600001.SH" in md
        assert "示例甲" in md
        assert "减持" in md   # 中文类别标签(枚举码 REDUCTION 已换算展示)
        assert "张三" in md

    def test_llm_budget_exhausted_shows_skipped_count_not_silent(self):
        """2026-07-26 必改 1:预算耗尽是"已扫描,但没扫完"——不是"没扫到"(scanned
        仍 True),文案要如实说清跳过了几只,不能悄悄消失。"""
        from neckline.report.news_alerts import NewsAlertScanStatus, SOURCE_LLM_PREFIX, SOURCE_TUSHARE_HOLDERTRADE

        report = _news_alerts(scan_statuses=[
            NewsAlertScanStatus(source=SOURCE_TUSHARE_HOLDERTRADE, scanned=True),
            NewsAlertScanStatus(
                source=SOURCE_LLM_PREFIX, scanned=True, codes_total=10, codes_skipped=6,
                reason="墙钟预算(300秒)耗尽,6 只标的未及扫描(持仓优先已扫完,被跳过的是排序靠后的自选标的,不代表确认无消息)。",
            ),
        ])
        md = _render(news_alerts=report)
        assert "**本次未扫描**" not in md   # 不是"未激活"那种整体没扫
        assert "预算" in md
        assert "6 只" in md
