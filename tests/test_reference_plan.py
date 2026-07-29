"""候选参考件三件套单测(plan §五 v1.5-①,需求 9)。覆盖 ①验收全部九项:
    ① 通过态出三件套 + disclaimer;② 否决态三件全 null + vetoReason(+"票仍在 20
    只里"归 test_pipeline.py,本文件覆盖 build_reference_plan 层);③ 未激活/解析
    失败态 status=unavailable 且叙述与 verdict 未丢;④ 夹逼四态各一单测;⑤ 离场
    区间不受涨跌停夹逼;⑥ stop_price 随现役 stop_pct 变而变;⑦ reference_plans
    幂等落库 + save=False 不写库(store 落库姿势本身,pipeline 的 save=False 场景见
    test_pipeline.py);⑧ ①-G 三条守门单测之一(sentinel 目录 grep 零命中;另两条
    分别在 test_intel_candidates.py / test_notify.py);⑨ pytest 零回归(跑全量套件
    验证,不在本文件内)。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from neckline.calendar import next_trading_day
from neckline.data.board import Board
from neckline.data.limit_derived import compute_intraday_limit_prices
from neckline.llm.base import LLMResult
from neckline.llm.judge import VERDICT_INACTIVE, VERDICT_PASS, VERDICT_VETO, JudgeResult, judge_candidate
from neckline.llm.providers.glm import GLMProvider
from neckline.report import reference_plan as rp
from neckline.report import reference_plan_store as rps
from neckline.report.candidates import Candidate, invalidation_spec
from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, seed_active_rule_v1

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _candidate(**overrides) -> Candidate:
    base = dict(
        ts_code="600001.SH", name="示例甲", close=12.00, score=95.0, rank=1, board="MAIN",
        pattern_tags=[], hot_sectors=[], sector_names=[],
        entry_plan="", stop_loss="", target="", invalidation_text="",
        invalidation_spec=invalidation_spec(), k4_flags=[], raw={},
    )
    base.update(overrides)
    return Candidate(**base)


class _StubProvider:
    """镜像 test_judge.py 的最小假 provider,专注测 judge.py 自己的
    调用/解析/降级逻辑是否被 `reference_plan.py` 正确复用(不经 httpx)。"""

    name = "stub"
    model = "stub-model"

    def __init__(self, result: LLMResult) -> None:
        self._result = result
        self.calls = 0
        self.captured_messages = []

    def chat(self, messages, *, enable_search=True, transport=None):
        self.calls += 1
        self.captured_messages = list(messages)
        return self._result


def _seed_env(settings, *, stop_pct: float = 0.05, n_days: int = 10, start: date = date(2024, 3, 1)):
    """铺一份能算涨跌停 + 读现役 stop_pct 的最小环境(600001.SH 主板非ST老股)。
    返回交易日列表(升序);调用方通常用 `dates[-2]` 当"报告日",`dates[-1]` 即
    `next_trading_day(报告日)`,落在 `insert_trade_cal` 的 DB 覆盖窗口内。"""
    dates = business_days(start, n_days)
    insert_trade_cal(settings, dates)
    insert_stock_basic(settings, [
        {"ts_code": "600001.SH", "name": "示例甲", "market": "主板", "list_date": date(2020, 1, 1)},
    ])
    seed_active_rule_v1(settings, extra_config={"stop_pct": stop_pct})
    return dates


# ————————————————————————————————————————————————————————————————
# ① `judge_candidate` 新增 `context_block` 参数(向后兼容扩展,不改既有默认行为)
# ————————————————————————————————————————————————————————————————

class TestJudgeCandidateContextBlockParam:
    def test_default_call_unaffected_uses_build_context_block(self):
        stub = _StubProvider(LLMResult(ok=True, content="分析。\n结论:通过", provider="glm", model="glm-5.2"))
        judge_candidate(_candidate(), provider=stub)
        assert "示例甲" in stub.captured_messages[1].content   # build_context_block 默认行为不变

    def test_context_block_overrides_default_when_given(self):
        stub = _StubProvider(LLMResult(ok=True, content="分析。\n结论:通过", provider="glm", model="glm-5.2"))
        judge_candidate(_candidate(), provider=stub, context_block="自定义富上下文XYZ")
        assert stub.captured_messages[1].content == "自定义富上下文XYZ"

    def test_none_provider_still_short_circuits_without_using_context_block(self):
        r = judge_candidate(_candidate(), provider=None, context_block="不该被用到的上下文")
        assert r.verdict == VERDICT_INACTIVE
        assert r.degraded is True


# ————————————————————————————————————————————————————————————————
# ② narrative 尾部三件套 json 解析(①-B)
# ————————————————————————————————————————————————————————————————

class TestSplitNarrativeAndReferenceJson:
    def test_fenced_json_extracted_and_narrative_cleaned(self):
        narrative = (
            "这是一段自由叙述,讲了催化逻辑是否站得住。\n\n"
            "```json\n"
            + json.dumps({
                "buy": {"low": 12.0, "high": 12.5, "why": "w"},
                "exit": {"low": 13.0, "high": 13.5, "why": "w2"},
                "script": "s", "veto_reason": None,
            }, ensure_ascii=False)
            + "\n```"
        )
        cleaned, parsed = rp.split_narrative_and_reference_json(narrative)
        assert "催化逻辑是否站得住" in cleaned
        assert "```" not in cleaned and "buy" not in cleaned
        assert parsed["buy"]["low"] == 12.0
        assert parsed["script"] == "s"

    def test_bare_trailing_json_tolerated_without_fence(self):
        narrative = '自由叙述,风险较大。\n{"buy": null, "exit": null, "script": null, "veto_reason": "利空明显"}'
        cleaned, parsed = rp.split_narrative_and_reference_json(narrative)
        assert cleaned == "自由叙述,风险较大。"
        assert parsed["veto_reason"] == "利空明显"

    def test_takes_last_fence_when_multiple_present(self):
        narrative = (
            "```json\n" + json.dumps({"buy": None, "exit": None, "script": None, "veto_reason": "旧"}) + "\n```\n"
            "补充说明后我改变判断。\n"
            "```json\n" + json.dumps({"buy": None, "exit": None, "script": None, "veto_reason": "新"}) + "\n```"
        )
        _, parsed = rp.split_narrative_and_reference_json(narrative)
        assert parsed["veto_reason"] == "新"

    def test_malformed_fence_json_returns_none_but_still_cleans_narrative(self):
        narrative = "自由叙述正文。\n\n```json\n{not valid json at all}\n```"
        cleaned, parsed = rp.split_narrative_and_reference_json(narrative)
        assert parsed is None
        assert "```" not in cleaned
        assert cleaned == "自由叙述正文。"

    def test_no_json_anywhere_returns_none_and_original_text_untouched(self):
        narrative = (
            "LLM 未激活(.env 未配置 LLM_PROVIDER/LLM_API_KEY),本候选未经过 LLM 审判,"
            "仅供参考,不构成否决或通过。"
        )
        cleaned, parsed = rp.split_narrative_and_reference_json(narrative)
        assert parsed is None
        assert cleaned == narrative

    def test_nested_braces_inside_fence_do_not_truncate_parse(self):
        """buy/exit 都是嵌套对象——非贪婪 `.*?` 若按花括号计数会在第一个内层 `}`
        处截断,必须验证围栏匹配是靠 ``` 定界、不受内部嵌套花括号影响。"""
        payload = {
            "buy": {"low": 1.0, "high": 2.0, "why": "w"},
            "exit": {"low": 3.0, "high": 4.0, "why": "w2"},
            "script": "s", "veto_reason": None,
        }
        narrative = "正文。\n\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        _, parsed = rp.split_narrative_and_reference_json(narrative)
        assert parsed == payload


# ————————————————————————————————————————————————————————————————
# ③ 买入夹逼四态 + 离场格式校验(①-C,④验收⑤验收)
# ————————————————————————————————————————————————————————————————

class TestClampBuy:
    def test_absent_when_buy_key_missing(self):
        assert rp._clamp_buy(None, 13.2, 10.8) == (None, None, rp.BUY_CLAMP_ABSENT, None)

    def test_absent_when_buy_is_empty_object(self):
        assert rp._clamp_buy({}, 13.2, 10.8) == (None, None, rp.BUY_CLAMP_ABSENT, None)

    def test_malformed_when_low_greater_than_high(self):
        _, _, clamp, _ = rp._clamp_buy({"low": 13.0, "high": 12.0}, 13.2, 10.8)
        assert clamp == rp.BUY_CLAMP_REJECTED_MALFORMED

    def test_malformed_when_one_number_missing(self):
        _, _, clamp, _ = rp._clamp_buy({"low": 12.0}, 13.2, 10.8)
        assert clamp == rp.BUY_CLAMP_REJECTED_MALFORMED

    def test_malformed_when_non_numeric(self):
        _, _, clamp, _ = rp._clamp_buy({"low": "十二", "high": 12.5}, 13.2, 10.8)
        assert clamp == rp.BUY_CLAMP_REJECTED_MALFORMED

    def test_malformed_when_zero_or_negative(self):
        _, _, clamp, _ = rp._clamp_buy({"low": -1.0, "high": 12.5}, 13.2, 10.8)
        assert clamp == rp.BUY_CLAMP_REJECTED_MALFORMED

    def test_no_limit_when_limit_prices_unresolvable(self):
        _, _, clamp, _ = rp._clamp_buy({"low": 12.0, "high": 12.5}, None, None)
        assert clamp == rp.BUY_CLAMP_REJECTED_NO_LIMIT

    def test_out_of_limit_when_above_limit_up(self):
        _, _, clamp, _ = rp._clamp_buy({"low": 12.0, "high": 13.5}, 13.2, 10.8)
        assert clamp == rp.BUY_CLAMP_REJECTED_OUT_OF_LIMIT

    def test_out_of_limit_when_below_limit_down(self):
        _, _, clamp, _ = rp._clamp_buy({"low": 10.0, "high": 12.0}, 13.2, 10.8)
        assert clamp == rp.BUY_CLAMP_REJECTED_OUT_OF_LIMIT

    def test_ok_within_bounds_rounds_to_two_decimals_and_keeps_why(self):
        low, high, clamp, why = rp._clamp_buy({"low": 12.341, "high": 12.987, "why": "贴近支撑"}, 13.2, 10.8)
        assert (low, high, clamp, why) == (12.34, 12.99, rp.BUY_CLAMP_OK, "贴近支撑")

    def test_absent_takes_priority_over_no_limit(self):
        """"没给"与"算不出涨跌停"是两件独立的事——LLM 压根没给 buy 时,即便涨跌停
        也算不出,仍应报 absent 而不是 rejected_no_limit(①-C 判定优先级①)。"""
        _, _, clamp, _ = rp._clamp_buy(None, None, None)
        assert clamp == rp.BUY_CLAMP_ABSENT


class TestClampExit:
    def test_absent(self):
        assert rp._clamp_exit(None)[2] == rp.EXIT_CLAMP_ABSENT

    def test_malformed(self):
        assert rp._clamp_exit({"low": 15.0, "high": 14.0})[2] == rp.EXIT_CLAMP_REJECTED_MALFORMED

    def test_ok_even_far_above_any_notional_limit_up(self):
        """离场区间不受涨跌停夹逼(①-C/④验收⑤):数值远高于"明日涨跌停"也不拦
        ——只做 0<low<=high 的格式校验。"""
        low, high, clamp, why = rp._clamp_exit({"low": 15.10, "high": 99.99, "why": "压力位"})
        assert (low, high, clamp, why) == (15.10, 99.99, rp.EXIT_CLAMP_OK, "压力位")


# ————————————————————————————————————————————————————————————————
# 明日涨跌停价 / 现役止损比例解析(①-C 锚点)
# ————————————————————————————————————————————————————————————————

class TestResolveNextDayLimitPrices:
    def test_resolves_and_matches_compute_intraday_limit_prices_directly(self, isolated_env):
        dates = _seed_env(isolated_env)
        cand = _candidate(close=10.0)
        up, down, reason = rp._resolve_next_day_limit_prices(cand, dates[-2], isolated_env.db_path)
        expected_up, expected_down = compute_intraday_limit_prices(
            10.0, Board.MAIN, False, next_trading_day(dates[-2])
        )
        assert (up, down) == (expected_up, expected_down)
        assert reason is None

    def test_no_meta_returns_reason_not_a_crash(self, isolated_env):
        _seed_env(isolated_env)
        cand = _candidate(ts_code="999999.SH", close=10.0)
        up, down, reason = rp._resolve_next_day_limit_prices(cand, date(2024, 3, 4), isolated_env.db_path)
        assert up is None and down is None
        assert reason and "元数据" in reason

    def test_non_positive_close_returns_reason(self, isolated_env):
        _seed_env(isolated_env)
        cand = _candidate(close=0.0)
        up, down, reason = rp._resolve_next_day_limit_prices(cand, date(2024, 3, 4), isolated_env.db_path)
        assert up is None and down is None
        assert reason


class TestResolveStopPct:
    def test_reads_from_active_config_not_hardcoded(self, isolated_env):
        _seed_env(isolated_env, stop_pct=0.07)
        assert rp._resolve_stop_pct(isolated_env.db_path) == pytest.approx(0.07)

    def test_none_when_no_active_strategy_version(self, isolated_env):
        assert rp._resolve_stop_pct(isolated_env.db_path) is None


# ————————————————————————————————————————————————————————————————
# ④ `build_reference_plan` 三态(①-D)+ ⑥ stop_price 随 stop_pct 变化
# ————————————————————————————————————————————————————————————————

class TestBuildReferencePlanStates:
    def test_degraded_judge_result_is_unavailable(self, isolated_env):
        dates = _seed_env(isolated_env)
        jr = JudgeResult(
            ts_code="600001.SH", provider="none", model="", verdict=VERDICT_INACTIVE,
            narrative="LLM 未激活(.env 未配置 LLM_PROVIDER/LLM_API_KEY)……",
            degraded=True, degrade_reason="未配置 LLM_PROVIDER/LLM_API_KEY",
        )
        plan = rp.build_reference_plan(
            _candidate(close=12.0), dates[-2], judge_result=jr, parsed_json=None, db_path=isolated_env.db_path,
        )
        assert plan.status == rp.STATUS_UNAVAILABLE
        assert plan.degraded is True
        assert plan.degrade_reason == "未配置 LLM_PROVIDER/LLM_API_KEY"
        assert plan.buy_clamp == rp.BUY_CLAMP_ABSENT and plan.buy_low is None
        assert plan.exit_clamp == rp.EXIT_CLAMP_ABSENT
        assert plan.script_text is None
        assert plan.veto_reason is None

    def test_veto_discards_json_numbers_even_if_model_gave_them(self, isolated_env):
        """否决时三件套一律置空——即便模型给了数字也丢弃(①-B 明文要求)。"""
        dates = _seed_env(isolated_env)
        jr = JudgeResult(
            ts_code="600001.SH", provider="glm", model="glm-5.2", verdict=VERDICT_VETO,
            narrative="有明显利空,公告显示业绩不及预期。", degraded=False,
        )
        parsed = {
            "buy": {"low": 12.0, "high": 12.5, "why": "w"},
            "exit": {"low": 13.0, "high": 14.0, "why": "w2"},
            "script": "不该出现的剧本", "veto_reason": "业绩暴雷",
        }
        plan = rp.build_reference_plan(
            _candidate(close=12.0), dates[-2], judge_result=jr, parsed_json=parsed, db_path=isolated_env.db_path,
        )
        assert plan.status == rp.STATUS_VETOED
        assert plan.buy_low is None and plan.buy_high is None
        assert plan.exit_low is None and plan.exit_high is None
        assert plan.script_text is None
        assert plan.veto_reason == "业绩暴雷"

    def test_veto_reason_falls_back_to_none_when_missing_not_fabricated(self, isolated_env):
        dates = _seed_env(isolated_env)
        jr = JudgeResult(ts_code="600001.SH", provider="glm", model="glm-5.2", verdict=VERDICT_VETO,
                          narrative="有硬伤。", degraded=False)
        plan = rp.build_reference_plan(
            _candidate(close=12.0), dates[-2], judge_result=jr, parsed_json=None, db_path=isolated_env.db_path,
        )
        assert plan.status == rp.STATUS_VETOED
        assert plan.veto_reason is None   # 不硬凑、不截断 narrative 假装是理由

    def test_pass_with_unparsable_json_is_unavailable_narrative_and_verdict_not_lost(self, isolated_env):
        dates = _seed_env(isolated_env)
        jr = JudgeResult(ts_code="600001.SH", provider="glm", model="glm-5.2", verdict=VERDICT_PASS,
                          narrative="正常审判叙述,未按格式给出三件套。", degraded=False)
        plan = rp.build_reference_plan(
            _candidate(close=12.0), dates[-2], judge_result=jr, parsed_json=None, db_path=isolated_env.db_path,
        )
        assert plan.status == rp.STATUS_UNAVAILABLE
        assert plan.degraded is True
        assert "解析失败" in plan.degrade_reason
        # 叙述与 verdict 本身不属于 ReferencePlan,而是留在 JudgeResult 上——③验收原话
        # 「叙述与 verdict 未丢」验证的是 judge_result 本体未被本函数篡改。
        assert jr.narrative == "正常审判叙述,未按格式给出三件套。"
        assert jr.verdict == VERDICT_PASS

    def test_pass_with_valid_json_is_ok(self, isolated_env):
        dates = _seed_env(isolated_env, stop_pct=0.05)
        up, down = compute_intraday_limit_prices(12.0, Board.MAIN, False, next_trading_day(dates[-2]))
        buy_low, buy_high = round(down + 0.2, 2), round(down + 0.5, 2)
        jr = JudgeResult(ts_code="600001.SH", provider="glm", model="glm-5.2", verdict=VERDICT_PASS,
                          narrative="正常叙述。", degraded=False)
        parsed = {
            "buy": {"low": buy_low, "high": buy_high, "why": "靠近支撑位"},
            "exit": {"low": 15.0, "high": 15.8, "why": "前高压力位"},
            "script": "若低开则观望", "veto_reason": None,
        }
        plan = rp.build_reference_plan(
            _candidate(close=12.0), dates[-2], judge_result=jr, parsed_json=parsed, db_path=isolated_env.db_path,
        )
        assert plan.status == rp.STATUS_OK
        assert plan.buy_clamp == rp.BUY_CLAMP_OK
        assert plan.buy_low == buy_low and plan.buy_high == buy_high
        assert plan.exit_clamp == rp.EXIT_CLAMP_OK
        assert plan.script_text == "若低开则观望"
        assert plan.stop_price == round(12.0 * (1 - 0.05), 2)
        d = plan.to_public_dict()
        assert d["disclaimer"] == rp.REFERENCE_DISCLAIMER
        assert d["buy"]["stopPrice"] == plan.stop_price
        assert d["status"] == "ok"

    def test_exit_not_clamped_by_limit_prices_even_far_above_limit_up(self, isolated_env):
        """④验收⑤:离场区间不受涨跌停夹逼——压力位高于明日涨停仍正常显示。"""
        dates = _seed_env(isolated_env, stop_pct=0.05)
        up, _ = compute_intraday_limit_prices(12.0, Board.MAIN, False, next_trading_day(dates[-2]))
        jr = JudgeResult(ts_code="600001.SH", provider="glm", model="glm-5.2", verdict=VERDICT_PASS,
                          narrative="x", degraded=False)
        parsed = {
            "buy": None,   # 本次没给买入区间,独立于离场区间是否越过涨跌停
            "exit": {"low": up + 5.0, "high": up + 10.0, "why": "远期压力位"},
            "script": None, "veto_reason": None,
        }
        plan = rp.build_reference_plan(
            _candidate(close=12.0), dates[-2], judge_result=jr, parsed_json=parsed, db_path=isolated_env.db_path,
        )
        assert plan.status == rp.STATUS_OK   # 至少一件有效(离场)即 ok
        assert plan.buy_clamp == rp.BUY_CLAMP_ABSENT
        assert plan.exit_clamp == rp.EXIT_CLAMP_OK
        assert plan.exit_low == round(up + 5.0, 2)
        assert plan.exit_high == round(up + 10.0, 2)

    @pytest.mark.parametrize("stop_pct", [0.05, 0.08])
    def test_stop_price_varies_with_active_stop_pct_not_hardcoded(self, isolated_env, stop_pct):
        """⑥验收:改测试库 config → stop_price 输出跟着变,单测锁死不硬编 0.05。"""
        dates = _seed_env(isolated_env, stop_pct=stop_pct)
        jr = JudgeResult(ts_code="600001.SH", provider="glm", model="glm-5.2", verdict=VERDICT_VETO,
                          narrative="x", degraded=False)
        plan = rp.build_reference_plan(
            _candidate(close=12.0), dates[-2], judge_result=jr, parsed_json=None, db_path=isolated_env.db_path,
        )
        assert plan.stop_pct == pytest.approx(stop_pct)
        assert plan.stop_price == round(12.0 * (1 - stop_pct), 2)


# ————————————————————————————————————————————————————————————————
# 一站式编排 `judge_and_build_reference_plan`(MockTransport 端到端 + 降级路径)
# ————————————————————————————————————————————————————————————————

class TestJudgeAndBuildReferencePlanOrchestration:
    def test_no_provider_never_computes_context_returns_unavailable(self, isolated_env, monkeypatch):
        dates = _seed_env(isolated_env)
        called = {"n": 0}

        def _boom(*a, **kw):
            called["n"] += 1
            raise AssertionError("provider=None 时不该计算上下文,做无用功")

        monkeypatch.setattr(rp, "build_reference_context_block", _boom)
        result, plan = rp.judge_and_build_reference_plan(
            _candidate(close=12.0), dates[-2], provider=None, db_path=isolated_env.db_path,
        )
        assert called["n"] == 0
        assert result.verdict == VERDICT_INACTIVE
        assert plan is not None and plan.status == rp.STATUS_UNAVAILABLE

    def test_full_pass_flow_with_real_provider_mock_transport(self, isolated_env):
        """用真 GLMProvider + MockTransport 走一遍完整链路,证明
        reference_plan.py 与 llm 层实际接得上(同 test_judge.py 的
        TestEndToEndWithRealProviderMockTransport 姿势)。"""
        dates = _seed_env(isolated_env, stop_pct=0.05)
        down_up = compute_intraday_limit_prices(12.0, Board.MAIN, False, next_trading_day(dates[-2]))
        up, down = down_up
        buy_low, buy_high = round(down + 0.2, 2), round(down + 0.5, 2)

        def handler(request: httpx.Request) -> httpx.Response:
            content = (
                "催化逻辑分析正文,搜索到产业催化消息。\n\n结论:通过\n\n```json\n"
                + json.dumps({
                    "buy": {"low": buy_low, "high": buy_high, "why": "靠近支撑"},
                    "exit": {"low": 15.0, "high": 15.8, "why": "前高压力位"},
                    "script": "若集合竞价大幅低开则放弃,温和低开且量能正常则观望",
                    "veto_reason": None,
                }, ensure_ascii=False)
                + "\n```"
            )
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            })

        provider = GLMProvider(api_key="sk-xxx")
        result, plan = rp.judge_and_build_reference_plan(
            _candidate(close=12.0), dates[-2], provider=provider, transport=httpx.MockTransport(handler),
            db_path=isolated_env.db_path,
        )
        assert result.verdict == VERDICT_PASS
        assert result.degraded is False
        assert "```" not in result.narrative and "buy" not in result.narrative
        assert "催化逻辑分析正文" in result.narrative
        assert plan is not None
        assert plan.status == rp.STATUS_OK
        assert plan.buy_clamp == rp.BUY_CLAMP_OK
        assert plan.buy_low == buy_low and plan.buy_high == buy_high
        assert plan.exit_clamp == rp.EXIT_CLAMP_OK
        assert plan.script_text.startswith("若集合竞价大幅低开")

    def test_full_veto_flow_candidate_would_stay_listed_upstream(self, isolated_env):
        """否决态端到端:三件套全空 + vetoReason,narrative/verdict 完整保留(供
        pipeline.py 层继续把候选留在 20 只里——本测试只验证 reference_plan.py 这一
        层的产出,候选去留断言见 test_pipeline.py)。"""
        dates = _seed_env(isolated_env)

        def handler(request: httpx.Request) -> httpx.Response:
            content = (
                "该股近期有一则减持公告,催化站不住。\n\n结论:否决\n\n```json\n"
                + json.dumps({"buy": None, "exit": None, "script": None, "veto_reason": "股东大幅减持"})
                + "\n```"
            )
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            })

        provider = GLMProvider(api_key="sk-xxx")
        result, plan = rp.judge_and_build_reference_plan(
            _candidate(close=12.0), dates[-2], provider=provider, transport=httpx.MockTransport(handler),
            db_path=isolated_env.db_path,
        )
        assert result.verdict == VERDICT_VETO
        assert "减持公告" in result.narrative
        assert plan.status == rp.STATUS_VETOED
        assert plan.veto_reason == "股东大幅减持"
        assert plan.to_public_dict()["buy"] is None
        assert plan.to_public_dict()["exit"] is None

    def test_context_build_exception_falls_back_but_judges_exactly_once(self, isolated_env, monkeypatch):
        """上下文装配异常不得阻断审判本身——退回默认上下文继续,且只发起一次 LLM
        调用(不因装配失败而发起第二次朴素审判浪费预算)。"""
        dates = _seed_env(isolated_env)
        monkeypatch.setattr(rp, "build_reference_context_block", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "分析。\n结论:通过"}}],
            })

        provider = GLMProvider(api_key="sk-xxx")
        result, plan = rp.judge_and_build_reference_plan(
            _candidate(close=12.0), dates[-2], provider=provider, transport=httpx.MockTransport(handler),
            db_path=isolated_env.db_path,
        )
        assert calls["n"] == 1
        assert result.verdict == VERDICT_PASS
        assert plan is not None
        assert plan.status == rp.STATUS_UNAVAILABLE   # 退回的默认上下文没能拿到三件套 json

    def test_plan_assembly_exception_keeps_judge_result_intact(self, isolated_env, monkeypatch):
        """三件套装配异常不得影响已产出的审判结论(JudgeResult 原样返回,plan=None)。"""
        dates = _seed_env(isolated_env)
        monkeypatch.setattr(rp, "build_reference_plan", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "id": "x", "model": "glm-5.2",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "分析文字正文。\n结论:通过"}}],
            })

        provider = GLMProvider(api_key="sk-xxx")
        result, plan = rp.judge_and_build_reference_plan(
            _candidate(close=12.0), dates[-2], provider=provider, transport=httpx.MockTransport(handler),
            db_path=isolated_env.db_path,
        )
        assert plan is None
        assert result.verdict == VERDICT_PASS
        assert "分析文字正文" in result.narrative


# ————————————————————————————————————————————————————————————————
# ⑦ `reference_plans` 落库幂等 + 不写库路径
# ————————————————————————————————————————————————————————————————

def _sample_plan(**overrides) -> "rp.ReferencePlan":
    base = dict(
        ts_code="600001.SH", status=rp.STATUS_OK, verdict=VERDICT_PASS, close=12.0,
        limit_up=13.2, limit_down=10.8, buy_low=12.1, buy_high=12.4, buy_clamp=rp.BUY_CLAMP_OK, buy_why="w",
        stop_price=11.4, stop_pct=0.05, exit_low=15.0, exit_high=15.8, exit_clamp=rp.EXIT_CLAMP_OK, exit_why="w2",
        script_text="s", veto_reason=None, provider="glm", model="glm-5.2", degraded=False, degrade_reason="",
    )
    base.update(overrides)
    return rp.ReferencePlan(**base)


class TestReferencePlanStore:
    def test_idempotent_rerun_same_day_no_duplicate_rows(self, isolated_env):
        plan = _sample_plan()
        rps.save_reference_plans(date(2024, 3, 6), [plan], db_path=isolated_env.db_path)
        first = [{k: v for k, v in r.items() if k != "created_at"} for r in rps.load_reference_plans(date(2024, 3, 6), db_path=isolated_env.db_path)]
        assert len(first) == 1

        rps.save_reference_plans(date(2024, 3, 6), [plan], db_path=isolated_env.db_path)
        second = [{k: v for k, v in r.items() if k != "created_at"} for r in rps.load_reference_plans(date(2024, 3, 6), db_path=isolated_env.db_path)]
        assert len(second) == 1
        assert first == second   # 逐位相同(created_at 之外)

    def test_empty_list_is_noop(self, isolated_env):
        rps.save_reference_plans(date(2024, 3, 6), [], db_path=isolated_env.db_path)
        assert rps.load_reference_plans(date(2024, 3, 6), db_path=isolated_env.db_path) == []

    def test_load_single_reference_plan_by_code(self, isolated_env):
        plan = _sample_plan(ts_code="600002.SH")
        rps.save_reference_plans(date(2024, 3, 6), [plan], db_path=isolated_env.db_path)
        row = rps.load_reference_plan(date(2024, 3, 6), "600002.SH", db_path=isolated_env.db_path)
        assert row is not None
        assert row["status"] == "ok"
        assert row["buy_low"] == pytest.approx(12.1)
        assert row["degraded"] is False

    def test_load_missing_returns_none_not_crash(self, isolated_env):
        assert rps.load_reference_plan(date(2024, 3, 6), "999999.SH", db_path=isolated_env.db_path) is None


# ————————————————————————————————————————————————————————————————
# ⑧ ①-G 守门单测(其一):sentinel 全目录不出现 reference_plan / referencePlan
#    (另两条——排序键白名单 / 推送白名单六类——分别在 test_intel_candidates.py /
#    test_notify.py,§2.0 第〇原则「参考件不触发任何机器动作」的机器判据)
# ————————————————————————————————————————————————————————————————

_SENTINEL_DIR = _PROJECT_ROOT / "neckline" / "sentinel"
_BANNED_NAMES = ["reference_plan", "referencePlan"]


@pytest.mark.parametrize("path", sorted(_SENTINEL_DIR.glob("*.py")), ids=lambda p: p.name)
def test_sentinel_never_references_reference_plan(path: Path):
    """§2.0 第一条「参考件不触发任何机器动作」的结构性防复发:哨兵永远只读
    `entry_spec`/`invalidation_spec`/现役 config,不许出现在参考件字段名(连注释里
    点名都不行——照 `_SORT_KEY_INPUTS` 白名单单测体例,判据要机器可查)。"""
    text = path.read_text(encoding="utf-8")
    for name in _BANNED_NAMES:
        assert name not in text, f"{path.name} 出现了被禁的参考件字段名 {name}(§2.0:哨兵不得消费参考件)"
