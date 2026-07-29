"""报告 markdown 渲染单测(plan 2.5)。纯函数、手工构造 dataclass 输入(不碰任何
I/O,数据管线接线由 `tests/test_pipeline.py` 覆盖)——断言每个小节在各种输入组合
(空/非空、通过/否决/未激活、含/不含搜索来源、超过 top_n_judged)下都能正确渲染、
不崩,并且不吹嘘 alpha(§2.3 硬性要求)。**v1.5-③ 起新增**:候选卡参考三件套四态
(ok/部分被拦/vetoed/unavailable/None)、执行提示行、持仓体检节。"""

from __future__ import annotations

from datetime import date

from neckline.llm.base import SearchHit
from neckline.llm.judge import VERDICT_INACTIVE, VERDICT_PASS, VERDICT_VETO, JudgeResult
from neckline.report.candidates import Candidate
from neckline.report.holding_k4_check import HoldingK4Hit, HoldingK4Item
from neckline.report.render import render_markdown
from neckline.report.sectors import SectorScore
from neckline.report.sentiment import SentimentDashboard
from neckline.report.watchlist_check import WatchlistCheckItem
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


def _candidate(**overrides) -> Candidate:
    # v1.5-③-B:老四件套字段刻意仍塞假文本(而非留空默认)——用来在候选卡渲染断言
    # 里反向证明"即便 Candidate 对象带着这些字段〔如从老报告快照 `Candidate(**d)`
    # 重建〕,候选详情也绝不会展示它们",比默认空串更能守住"候选卡老四件套已退役"
    # 这条回归线。
    base = dict(
        ts_code="600001.SH", name="示例甲", close=10.5, score=95.0, rank=1, board="MAIN",
        pattern_tags=["浅回调贴前高"], hot_sectors=["人工智能"], sector_names=["人工智能"],
        entry_plan="回调低吸:现价10.5...", stop_loss="参考止损价约9.98元...",
        target="不设固定止盈线...", invalidation_text="次日低开...跌破VWAP...", invalidation_spec={}, raw={},
    )
    base.update(overrides)
    return Candidate(**base)


def _reference_plan(**overrides) -> dict:
    """`ReferencePlan.to_public_dict()` 形状(camelCase,`Candidate.reference_plan`
    的落库快照口径)——覆盖 status=ok 的最小合法示例,测试按需 override。"""
    base = dict(
        status="ok",
        buy={"low": 12.34, "high": 12.98, "stopPrice": 11.72, "why": "贴近支撑位"},
        buyUnavailableReason=None,
        exit={"low": 15.10, "high": 15.80, "why": "前高压力位"},
        exitUnavailableReason=None,
        script="若集合竞价大幅低开则放弃,温和低开则观望,符合预期则按区间执行。",
        vetoReason=None,
        unavailableReason=None,
        disclaimer="参考,非指令 —— 买卖与终选在你,系统不代下单;纪律以章程为准。",
        degraded=False,
    )
    base.update(overrides)
    return base


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

    def test_all_candidates_get_full_detail_regardless_of_top_n_judged(self):
        """v1.5-③-A(需求 9,20只全覆盖后):「前N只详情/后N只仅表格」两段结构退役,
        合并成一段——即便 `top_n_judged` 小于候选数(旧参数、生产已恒等于候选总数,
        但函数仍要向后兼容接受任意值),排名靠后、没有 `judged` 条目的候选也照样
        进详情区(有小标题 + 现价/形态标签行),不再被降格成"仅表格"。"""
        c1 = _candidate(ts_code="600001.SH", rank=1)
        c2 = _candidate(ts_code="600002.SH", rank=2, name="示例乙")
        jr1 = JudgeResult(ts_code="600001.SH", provider="glm", model="glm-5.2", verdict=VERDICT_PASS, narrative="x", degraded=False)
        md = _render(candidates=[c1, c2], judged={"600001.SH": jr1}, top_n_judged=1)
        assert "仅情报排序与形态标签" not in md   # 旧「后N只」小节标题已退役
        assert "### 前 " not in md and "### 后 " not in md  # 旧「前N只/后N只」小标题已退役
        assert "候选详情(2 只)" in md   # 合并成一段,规格覆盖全部候选
        # 候选2(未被 judged 收录、非 judge_skipped)仍进详情区,有独立小标题。
        assert "2. 示例乙" in md
        assert "现价:10.50 元" in md
        # 未被 judged 收录也非预算跳过 → 走"未执行"异常态文案(既有行为不变)。
        assert "**LLM 审判:未执行**" in md

    def test_overview_table_covers_all_candidates_not_only_tail(self):
        """v1.5-③-A:「后N只速览表」改「全部N只速览表」——排名第一的候选(此前只在
        详情区、不进紧凑表)现在也要出现在速览表里。"""
        c1 = _candidate(ts_code="600001.SH", rank=1)
        c2 = _candidate(ts_code="600002.SH", rank=2, name="示例乙")
        md = _render(candidates=[c1, c2], judged={}, top_n_judged=2)
        assert "速览表(全部 2 只)" in md
        table = md.split("速览表")[1]
        assert "600001.SH" in table and "600002.SH" in table

    def test_missing_judgment_for_top_candidate_shows_explicit_warning(self):
        c = _candidate()
        md = _render(candidates=[c], judged={})
        assert "未执行" in md

    def test_judge_skipped_shows_budget_exhausted_note_not_generic_error(self):
        """v1.5-②-B:预算耗尽跳过是**如实标注**,不是「未执行」那种异常状态——两条
        文案必须能分开(`test_missing_judgment_for_top_candidate_shows_explicit_warning`
        锁死真异常分支仍显示"未执行")。"""
        c = _candidate(judge_skipped=True)
        md = _render(candidates=[c], judged={})
        assert "预算耗尽未发起" in md
        assert "未执行" not in md

    def test_alpha_disclaimer_present(self):
        md = _render()
        assert "不是正 alpha" in md
        assert "不构成收益预测" in md


class TestExecHintLine:
    """执行提示(v1.4-⑤-A 既有计算,v1.5-③-A 起首次渲染进 markdown)。"""

    def test_exec_hints_rendered_with_label(self):
        c = _candidate(exec_hints=[
            {"code": "C1_strong_market_order", "text": "强票用市价/小δ立即介入不回踩", "source": "db"},
            {"code": "C4_no_pullback_bigred_mechanical", "text": "回调大红机械层不做", "source": "fallback"},
        ])
        md = _render(candidates=[c])
        assert "执行提示" in md
        assert "强票用市价/小δ立即介入不回踩" in md
        assert "回调大红机械层不做" in md

    def test_no_exec_hints_omits_line_not_crash(self):
        c = _candidate(exec_hints=[])
        md = _render(candidates=[c])
        assert "执行提示" not in md


class TestReferencePlanSection:
    """v1.5-③-A(需求 9)候选卡参考三件套渲染——取代退役的老四件套。覆盖四态
    (ok / 部分被拦 / vetoed / unavailable)+ None 的两种成因(老报告快照 / 预算
    跳过),并锁死语义红线(参考离场区间不得被称为止盈线)。"""

    def test_ok_state_shows_buy_exit_script_and_disclaimer(self):
        c = _candidate(reference_plan=_reference_plan())
        md = _render(candidates=[c])
        assert "参考买入区间(参考,非指令)" in md
        assert "12.34~12.98" in md and "11.72" in md   # 止损参考价一笔带过,不单列
        assert "参考离场区间(参考,非止盈线)" in md
        assert "15.10~15.80" in md
        assert "回落止盈 8% 兜底" in md
        assert "明早证伪剧本(参考,非指令)" in md
        assert "若集合竞价大幅低开则放弃" in md
        assert "参考,非指令" in md   # disclaimer 原样透出

    def test_buy_clamped_out_of_limit_shows_reason_not_blank_or_zero(self):
        """①-C 夹逼:买入区间越界被拦时**不画空区间、不写 0**,如实给出被拦原因;
        离场区间不受此影响,照常显示(①-C「只夹逼买入区间」)。"""
        c = _candidate(reference_plan=_reference_plan(
            buy=None, buyUnavailableReason="生成的买入参考区间超出明日涨跌停范围,已拦截",
        ))
        md = _render(candidates=[c])
        assert "超出明日涨跌停范围,已拦截" in md
        assert "0.00~0.00" not in md
        assert "参考离场区间(参考,非止盈线)" in md   # 离场区间不受买入夹逼牵连
        assert "15.10~15.80" in md

    def test_vetoed_state_hides_three_piece_shows_veto_reason(self):
        c = _candidate(reference_plan=_reference_plan(
            status="vetoed", buy=None, buyUnavailableReason="本次未生成买入参考区间",
            exit=None, exitUnavailableReason="本次未生成离场参考区间",
            script=None, vetoReason="股东大幅减持,催化站不住",
        ))
        md = _render(candidates=[c])
        assert "LLM 判风险大" in md
        assert "股东大幅减持,催化站不住" in md
        assert "参考买入区间(参考,非指令)" not in md
        assert "12.34" not in md

    def test_vetoed_without_explicit_reason_points_to_narrative(self):
        """`vetoReason` 缺失(模型给了 null)→ 指向下方审判叙述,不硬凑/不截断
        narrative 假装是理由(①-B 明文)。"""
        c = _candidate(reference_plan=_reference_plan(
            status="vetoed", buy=None, buyUnavailableReason="x", exit=None,
            exitUnavailableReason="x", script=None, vetoReason=None,
        ))
        md = _render(candidates=[c])
        assert "见下方 LLM 审判叙述" in md

    def test_unavailable_state_is_not_confused_with_no_reference(self):
        """`status="unavailable"`(生成过、没看清楚)与 `reference_plan is None`
        (压根没这个概念)必须是两种不同文案(§3.8"没有"vs"没看")。"""
        c = _candidate(reference_plan=_reference_plan(
            status="unavailable", buy=None, buyUnavailableReason=None,
            exit=None, exitUnavailableReason=None, script=None,
            unavailableReason="三件套 JSON 解析失败(围栏缺失或格式不合法)",
        ))
        md = _render(candidates=[c])
        assert "本次未生成" in md
        assert "三件套 JSON 解析失败" in md
        assert "不代表确认无参考" in md

    def test_none_reference_plan_old_snapshot_not_crash(self):
        """`Candidate.reference_plan` 是 `None`(老报告快照,本字段上线前生成)→
        如实说"未生成",不冒充"已确认无参考"。"""
        c = _candidate(reference_plan=None, judge_skipped=False)
        md = _render(candidates=[c])
        assert "本报告未生成参考三件套" in md
        assert "老报告快照" in md

    def test_none_reference_plan_judge_skipped_gives_budget_reason(self):
        """`reference_plan is None` 且 `judge_skipped=True`(v1.5-②-B 预算耗尽未
        发起)→ 换一句更具体的理由,不与"老报告快照"那句混为一谈。"""
        c = _candidate(reference_plan=None, judge_skipped=True)
        md = _render(candidates=[c], judged={})
        assert "预算耗尽未发起审判" in md
        assert "老报告快照" not in md

    def test_exit_region_never_called_take_profit_line(self):
        """语义红线(§五 v1.5「⛔ 语义红线」第三条):离场参考区间**尤其不许**被表述
        成止盈线——回落止盈 8% 是纪律、是被动兜底,离场参考是主动参考,两者并存
        不互相取代。"""
        c = _candidate(reference_plan=_reference_plan())
        md = _render(candidates=[c])
        assert "非止盈线" in md
        # 全文里"止盈线"字样只应以"非止盈线"这个否定表述出现,不应再有裸的
        # "止盈线"描述参考离场区间("止盈线"是"非止盈线"的子串,故两个计数相等
        # 等价于"每次出现都带着这个'非'字前缀")。
        assert md.count("止盈线") == md.count("非止盈线")


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


class TestHoldingCheckSection:
    """持仓体检节(v1.5-③-C,需求 9「今日计划拆两块:持仓股 / 候选列表」的 markdown
    落地)。数据源 = pipeline 已算好的 `holding_k4_check`,本节只测渲染,不测判定
    逻辑本身(那是 `tests/test_holding_k4_check.py` 的职责)。"""

    def test_empty_holdings_shows_placeholder_not_omitted(self):
        """空持仓仍要有这一节(节在 = 体检跑过了),不能因为空列表整节消失。"""
        md = _render(holding_k4_check=[])
        assert "## 持仓体检" in md
        assert "今日无持仓" in md

    def test_omitted_holding_check_defaults_to_empty_not_crash(self):
        """`holding_k4_check` 参数省略(旧调用点/向后兼容)→ 按空处理,不崩。"""
        md = _render()
        assert "## 持仓体检" in md
        assert "今日无持仓" in md

    def test_holding_section_appears_before_candidates_section(self):
        """排在候选**之前**(镜像客户端 v1.1-E.1「持仓管理优先于选新票」同一顺序)。"""
        cand = _candidate(ts_code="600001.SH")
        item = _holding_item()
        md = _render(candidates=[cand], holding_k4_check=[item])
        assert md.index("## 持仓体检") < md.index("## 候选")

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
        assert "## 消息面" in md
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
        assert "⚠" not in md
        assert "**本次未扫描**" not in md
        assert "已扫描" in md
        assert "确认无此类消息" in md or "未发现命中条目" in md

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
