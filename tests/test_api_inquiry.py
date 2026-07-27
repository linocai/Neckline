"""问询台单测(§2.5,**v1.3.3 = 自由分析师**)。

用合成市场三票(主板 / *ST / 创业板)跑**同码**确定性材料装配,不手搓一套规则。

v1.3.3 锁死的新语义(旧的「二值裁决 + 纪律不过不放行 + 初审通过写海选池」全部退役):
    ① **任何票都拿得到实质回答**——含创业板(拆墙后)、含 *ST(硬线只警告不拦)、
       含查无数据的代码;LLM 段对所有票都跑,不再有"纪律不过直接终止"的短路。
    ② **纪律命中项 = 警告标注**,进 `risk_flags` / `evidence` / LLM 上下文,**不拦人**。
    ③ **不再写 `inquiry_pool`**(海选池自动写入退役,改一键加自选);表与消费侧保留不动。
    ④ **软护栏只在 prompt 层**:不做枚举强校验、不做输出后处理——LLM 原文原样透出。
    ⑤ 契约不破:`InquiryOut` 字段集合一个不增不减,只 `verdict` 类型放宽。
    ⑥ 缺 key / LLM 异常仍优雅降级,且降级回答仍是**实质材料**而非一句"未激活"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List

import pytest

from neckline.api import inquiry as inq
from neckline.api.schemas import VERDICT_ANALYZED, VERDICT_ANALYZED_WARN
from neckline.api.stores import load_inquiry_pool
from neckline.llm.base import ChatMessage, LLMResult
from tests.conftest import seed_active_rule_v1, seed_synthetic_market

_ALL_VERDICTS = (VERDICT_ANALYZED, VERDICT_ANALYZED_WARN)


@dataclass
class StubProvider:
    """可注入的假 LLM provider:原样返回预设正文,不联网。"""
    name: str = "glm"
    default_model: str = "glm-5.2"
    reply_body: str = "结合搜索,题材催化尚在,未见明显利空。走势上还在 20 日线附近拉锯。"
    ok: bool = True
    captured: List[ChatMessage] = field(default_factory=list)

    def chat(self, messages, *, enable_search=True, transport=None) -> LLMResult:
        self.captured = list(messages)
        return LLMResult(ok=self.ok, content=self.reply_body,
                         provider=self.name, model=self.default_model)


@pytest.fixture
def market(api_env):
    """合成市场 + 现役 rule v1(K1 血缘:墙**还在**);返回 (settings, report_day)。"""
    dates = seed_synthetic_market(api_env)
    seed_active_rule_v1(api_env)
    return api_env, dates[-1]


@pytest.fixture
def market_wall_down(api_env):
    """拆墙形态:现役 config `forbid_high_elasticity=False`(= 生产 v1.3.3)。"""
    dates = seed_synthetic_market(api_env)
    seed_active_rule_v1(api_env, extra_config={"forbid_high_elasticity": False})
    return api_env, dates[-1]


def _sys_and_user(prov: StubProvider) -> tuple:
    return prov.captured[0].content, prov.captured[1].content


# —— 确定性材料装配(同码,无 LLM)————————————————————————————————————————

def test_deterministic_mainboard_clean(market):
    s, day = market
    det = inq.run_deterministic_checks("600001.SH", day, db_path=s.db_path, parquet_dir=s.parquet_dir)
    assert det.has_data is True
    assert det.risk_flags == []
    assert det.board == "主板"
    assert det.score is not None                    # 评分现在无条件给(不再只在买点触发时算)


def test_deterministic_st_is_warning_not_block(market):
    """*ST 仍出现在 `risk_flags`(真硬线,要提示),但它只是提示。"""
    s, day = market
    det = inq.run_deterministic_checks("600002.SH", day, db_path=s.db_path, parquet_dir=s.parquet_dir)
    assert det.has_data is True
    assert any("ST" in f for f in det.risk_flags)
    assert any("风险提示" in e for e in det.evidence)


def test_deterministic_gem_flagged_when_wall_up(market):
    """墙还在(K1 血缘)时,创业板仍进 `risk_flags` —— 但已经是**警告**而非拒绝。"""
    s, day = market
    det = inq.run_deterministic_checks("300001.SZ", day, db_path=s.db_path, parquet_dir=s.parquet_dir)
    assert any("高弹" in f or "创业板" in f for f in det.risk_flags)


def test_deterministic_gem_clean_after_wall_down(market_wall_down):
    """**拆墙后**(现役 `forbid_high_elasticity=False`)创业板一条 flag 都不该有。"""
    s, day = market_wall_down
    det = inq.run_deterministic_checks("300001.SZ", day, db_path=s.db_path, parquet_dir=s.parquet_dir)
    assert det.risk_flags == []
    assert det.board == "创业板"
    assert any("未命中系统硬线" in e for e in det.evidence)


def test_deterministic_bare_code_is_normalized(market_wall_down):
    """裸 6 位照样能核(写入通道归一同一批修复;面板是 TuShare 口径)。"""
    s, day = market_wall_down
    det = inq.run_deterministic_checks("300001", day, db_path=s.db_path, parquet_dir=s.parquet_dir)
    assert det.code == "300001.SZ" and det.has_data is True


def test_deterministic_unknown_code_no_data_but_still_analyzable(market):
    s, day = market
    det = inq.run_deterministic_checks("999999.SZ", day, db_path=s.db_path, parquet_dir=s.parquet_dir)
    assert det.has_data is False
    assert any("停牌" in f or "代码有误" in f for f in det.risk_flags)   # 降级成提示,不是拒绝


# —— 核心回归:创业板票能拿到实质回答,不被拦 ————————————————————————————

class TestGemGetsSubstantiveAnswer:
    """用户实测的原始故障:创业板票(生产是 300759 康龙化成)被「不予放行」一句话打发。
    合成市场里等价的是 `300001.SZ`。"""

    def test_gem_llm_is_called_and_answer_passed_through(self, market_wall_down):
        s, day = market_wall_down
        prov = StubProvider(reply_body="创业板这只,位置还在年线上方,量能是关键。")
        out = inq.run_inquiry("300001.SZ", [{"role": "user", "content": "帮我看看走势"}],
                              basis_date=day, db_path=s.db_path,
                              parquet_dir=s.parquet_dir, provider=prov)
        assert prov.captured, "拆墙后创业板必须走到 LLM,不该被短路掉"
        assert out["reply"] == "创业板这只,位置还在年线上方,量能是关键。"   # 原文原样透出
        assert out["verdict"] == VERDICT_ANALYZED
        assert "不予放行" not in out["reply"] and "不符合" not in out["reply"]

    def test_gem_llm_called_even_when_wall_still_up(self, market):
        """**即便章程还没拆墙**,问询台代码层也不再拦人——LLM 照跑、回答照给,
        高弹只作为警告出现在上下文里。这条锁死"代码层拆栏杆"与"config 层拆墙"是
        两件独立的事,任一单独生效都不该让用户被一句话打发。"""
        s, day = market
        prov = StubProvider()
        out = inq.run_inquiry("300001.SZ", [], basis_date=day, db_path=s.db_path,
                              parquet_dir=s.parquet_dir, provider=prov)
        assert prov.captured, "墙还在时也必须走 LLM(硬栏杆已拆)"
        assert out["reply"] == prov.reply_body
        assert out["verdict"] == VERDICT_ANALYZED_WARN          # 有警告标注
        _sys, user_ctx = _sys_and_user(prov)
        assert "提示,非禁令" in user_ctx                        # 警告以"提示"身份注入

    def test_st_also_gets_llm_answer_with_warning(self, market):
        """真硬线(*ST)同样不拦:LLM 照跑,警告进上下文并要求在回答里说明。"""
        s, day = market
        prov = StubProvider()
        out = inq.run_inquiry("600002.SH", [], basis_date=day, db_path=s.db_path,
                              parquet_dir=s.parquet_dir, provider=prov)
        assert prov.captured
        assert out["verdict"] == VERDICT_ANALYZED_WARN
        _sys, user_ctx = _sys_and_user(prov)
        assert "ST" in user_ctx
        assert any("风险提示" in e for e in out["evidence"])

    def test_no_data_code_still_gets_llm_answer(self, market):
        s, day = market
        prov = StubProvider()
        out = inq.run_inquiry("999999.SZ", [], basis_date=day, db_path=s.db_path,
                              parquet_dir=s.parquet_dir, provider=prov)
        assert prov.captured
        _sys, user_ctx = _sys_and_user(prov)
        assert "没有取到该票当日 EOD 行情" in user_ctx


# —— 硬栏杆与二值裁决确已退役 ————————————————————————————————————————

class TestGatesRetired:
    def test_no_verdict_constants_left(self):
        """旧的二值裁决枚举必须从 schemas 消失(不是留个别名装样子)。"""
        import neckline.api.schemas as sch
        assert not hasattr(sch, "VERDICT_PASS")
        assert not hasattr(sch, "VERDICT_REJECT")

    def test_no_verdict_tag_parsing(self):
        """裁决标签解析器 `_parse_llm_verdict` 已删(不再从自由文本抽结论)。"""
        assert not hasattr(inq, "_parse_llm_verdict")

    @pytest.mark.parametrize("code", ["600001.SH", "600002.SH", "300001.SZ", "999999.SZ"])
    def test_verdict_is_descriptive_never_a_judgement(self, market, code):
        """`verdict` 只可能是两个**描述性标注**之一,且既不含「买」也不含「不符合」。"""
        s, day = market
        prov = StubProvider()
        out = inq.run_inquiry(code, [], basis_date=day, db_path=s.db_path,
                              parquet_dir=s.parquet_dir, provider=prov)
        assert out["verdict"] in _ALL_VERDICTS
        assert "买" not in out["verdict"] and "不符合" not in out["verdict"]

    def test_llm_raw_text_is_not_post_processed(self, market):
        """软护栏刻意**不做**输出后处理:即便模型喊「现在就买」,后端也不改写、不拦截
        (真正的护栏是 §3.8「系统永不下单」+ prompt 层约束,不是 grep「买」字)。
        本测试保护的是"不要哪天有人顺手加回强校验"这个设计决定。"""
        s, day = market
        prov = StubProvider(reply_body="现在就买!马上买入!")
        out = inq.run_inquiry("600001.SH", [], basis_date=day, db_path=s.db_path,
                              parquet_dir=s.parquet_dir, provider=prov)
        assert out["reply"] == "现在就买!马上买入!"       # 原文,一字未动

    def test_system_prompt_carries_soft_guardrail(self):
        p = inq.INQUIRY_SYSTEM_PROMPT
        assert "不下买卖指令" in p                        # 软护栏在
        assert "分析归你,扣扳机归用户" in p               # 理由也在(§2.5 拍板原文)
        assert "自由叙述" in p and "禁止" in p             # §2.7 风格约束
        assert "不要因此拒绝分析这只票" in p               # 明确禁止"以纪律为由拒答"
        assert "审判员" in p                              # 定位反差写清楚了


# —— 海选池自动写入退役 ————————————————————————————————————————————

class TestInquiryPoolRetired:
    @pytest.mark.parametrize("code", ["600001.SH", "300001.SZ"])
    def test_never_writes_pool(self, market_wall_down, code):
        s, day = market_wall_down
        inq.run_inquiry(code, [], basis_date=day, db_path=s.db_path,
                        parquet_dir=s.parquet_dir, provider=StubProvider())
        assert load_inquiry_pool(day, db_path=s.db_path) == []

    def test_pool_consumption_side_still_intact(self, market_wall_down):
        """表与消费侧**保留不动**(向后兼容):手工入池的历史行仍能被正常读到/消费,
        「同日补跑幂等 + 次日不再重复消费」的既有语义一字未变。"""
        from datetime import timedelta

        from neckline.api.stores import (
            add_to_inquiry_pool, load_pending_inquiry_codes, mark_inquiry_pool_consumed,
        )
        s, day = market_wall_down
        add_to_inquiry_pool(day, "600001.SH", db_path=s.db_path)
        assert [p["ts_code"] for p in load_pending_inquiry_codes(day, db_path=s.db_path)] \
            == ["600001.SH"]
        mark_inquiry_pool_consumed(day, db_path=s.db_path)
        # 同日补跑仍取得到(幂等分支);次日的报告不再重复消费。
        assert [p["ts_code"] for p in load_pending_inquiry_codes(day, db_path=s.db_path)] \
            == ["600001.SH"]
        assert load_pending_inquiry_codes(day + timedelta(days=1), db_path=s.db_path) == []

    def test_run_inquiry_has_no_pool_date_param(self):
        import inspect
        assert "pool_date" not in inspect.signature(inq.run_inquiry).parameters


# —— 降级(缺 key / LLM 异常)————————————————————————————————————————

class TestDegradation:
    def test_no_provider_still_substantive(self, market_wall_down):
        """缺 key:仍给实质材料(板块/收盘/硬线/评分),不是一句"未激活"。"""
        s, day = market_wall_down
        out = inq.run_inquiry("300001.SZ", [], basis_date=day, db_path=s.db_path,
                              parquet_dir=s.parquet_dir, provider=None)
        assert out["degraded"] is True
        assert out["verdict"] == VERDICT_ANALYZED
        assert "创业板" in out["reply"]
        assert "未激活" in out["reply"]                   # 诚实标注消息面缺席
        assert len(out["reply"]) > 40                     # 不是一句话打发
        assert out["evidence"]

    def test_llm_failure_degrades_without_crash(self, market):
        s, day = market
        out = inq.run_inquiry("600001.SH", [], basis_date=day, db_path=s.db_path,
                              parquet_dir=s.parquet_dir, provider=StubProvider(ok=False))
        assert out["degraded"] is True and out["reply"]

    def test_llm_raises_degrades_without_crash(self, market):
        class Boom:
            name, default_model = "glm", "glm-5.2"

            def chat(self, *a, **k):
                raise RuntimeError("网络炸了")

        s, day = market
        out = inq.run_inquiry("600001.SH", [], basis_date=day, db_path=s.db_path,
                              parquet_dir=s.parquet_dir, provider=Boom())
        assert out["degraded"] is True and out["reply"]

    def test_no_active_brain_version_does_not_crash(self, api_env):
        """大脑无现役版本(异常库)→ 不崩,给一条说明性 evidence。"""
        seed_synthetic_market(api_env)
        out = inq.run_inquiry("600001.SH", [], basis_date=__import__("datetime").date(2026, 7, 27),
                              db_path=api_env.db_path, parquet_dir=api_env.parquet_dir, provider=None)
        assert out["reply"] and out["verdict"] in _ALL_VERDICTS


# —— 端点集成 + 契约 ————————————————————————————————————————————————

class TestEndpointAndContract:
    def test_endpoint_gem_returns_200_with_answer(self, client, AUTH, market_wall_down, monkeypatch):
        s, day = market_wall_down
        import neckline.api.app as app_mod
        monkeypatch.setattr(app_mod, "_inquiry_basis_date", lambda: day)
        r = client.post("/api/v1/inquiry", headers=AUTH, json={"code": "300001.SZ", "messages": []})
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == "300001.SZ"
        assert body["reply"] and body["evidence"]
        assert body["verdict"] == VERDICT_ANALYZED

    def test_endpoint_st_returns_200_not_blocked(self, client, AUTH, market, monkeypatch):
        s, day = market
        import neckline.api.app as app_mod
        monkeypatch.setattr(app_mod, "_inquiry_basis_date", lambda: day)
        body = client.post("/api/v1/inquiry", headers=AUTH,
                           json={"code": "600002.SH", "messages": []}).json()
        assert body["verdict"] == VERDICT_ANALYZED_WARN
        assert body["reply"]

    def test_response_field_set_unchanged(self, client, AUTH, market, monkeypatch):
        """**契约不破**:字段集合与 v1.3 逐字段相同(已装 macOS App 靠这个解码)。"""
        s, day = market
        import neckline.api.app as app_mod
        monkeypatch.setattr(app_mod, "_inquiry_basis_date", lambda: day)
        body = client.post("/api/v1/inquiry", headers=AUTH,
                           json={"code": "600001.SH", "messages": []}).json()
        assert set(body) == {"ok", "code", "reply", "verdict", "evidence", "degraded"}
        assert isinstance(body["verdict"], str)

    def test_arbitrary_verdict_string_is_accepted_by_schema(self):
        """`verdict` 类型确已由 Literal 放宽成 str(否则新取值会 500)。"""
        from neckline.api.schemas import InquiryOut
        assert InquiryOut(code="x", reply="y", verdict="任意新取值").verdict == "任意新取值"


# —— 同码不重写(v1.3-⑤ 既有验收,v1.3.3 继续守)——————————————————————————

def test_discipline_checks_is_the_shared_function(market):
    """`run_deterministic_checks` 与 `report.watchlist_check.discipline_checks` 是同一个
    函数对象(不是两份各自维护的阈值)——防止未来有人在问询台悄悄另起一份。"""
    from neckline.report.watchlist_check import discipline_checks as wc_discipline_checks

    assert inq.discipline_checks is wc_discipline_checks


def test_same_flags_as_watchlist_check_same_code_same_day(market):
    """同码一致:问询台 `risk_flags` 与自选体检 `disqualifiers` 逐项集合相等——
    **两处消费方式不同**(体检=红灯、问询台=警告),但判定必须同源同值。"""
    from neckline.report.watchlist_check import build_watchlist_check
    from neckline.strategy import brain

    s, day = market
    active = brain.get_active(db_path=s.db_path)
    for code in ("600001.SH", "600002.SH", "300001.SZ"):
        det = inq.run_deterministic_checks(code, day, db_path=s.db_path, parquet_dir=s.parquet_dir)
        wc = build_watchlist_check(
            day, active.rule,
            [{"ts_code": code, "name": code, "pinned": False, "source": "manual"}],
            parquet_dir=s.parquet_dir, db_path=s.db_path,
        )[0]
        assert set(det.risk_flags) == set(wc.disqualifiers), code


def test_configurable_forbid_filters_still_reach_inquiry(api_env):
    """现役 config 可配的禁买过滤(P4)仍能在问询台产出**警告**(不是被静默忽略,
    也不是被拿去拦人)——v1.3-⑤ 修的那个真实缺口不许回潮。"""
    dates = seed_synthetic_market(api_env)
    seed_active_rule_v1(api_env, extra_config={"forbid_green_bigdown": -0.005})
    det = inq.run_deterministic_checks("600001.SH", dates[-1],
                                       db_path=api_env.db_path, parquet_dir=api_env.parquet_dir)
    assert any("绿盘大阴线" in f for f in det.risk_flags)
