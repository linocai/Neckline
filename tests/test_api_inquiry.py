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
from neckline.api.stores import get_inquiry_log, load_inquiry_pool
from neckline.llm.base import ChatMessage, LLMResult, SearchHit
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
    captured_search_query: Any = None            # v1.3.4:问询台传下去的联网检索词
    search_hits: List[Any] = field(default_factory=list)   # v1.3.4:回给上层的命中(条数即可)

    def chat(self, messages, *, enable_search=True, search_query=None, transport=None) -> LLMResult:
        self.captured = list(messages)
        self.captured_search_query = search_query
        return LLMResult(ok=self.ok, content=self.reply_body, search_hits=list(self.search_hits),
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


# —— v1.4-②:K4 题材类(A2/B3)持续天数改读 industry_strength(不再是 board_age)———————

def test_k4_flags_uses_industry_persist_days(api_env, monkeypatch):
    """`_k4_flags` 的参数已从 `member_map`/`hot`(概念板块)改名为 `industry_of`/
    `industry_hot`(行业强度)——直接验证接线正确:喂一个 persist_days=5 的行业热表,
    A2(题材持续≥4天)命中文案应出现在 `k4_flags` 里。монkeypatch 价量面板为空,
    只验题材类(不依赖价量面板,见 holding_k4_check 模块头)。"""
    import polars as pl

    from neckline.report import holding_k4_check as hk

    monkeypatch.setattr(hk, "_build_holding_feature_panel", lambda codes, td, pd: pl.DataFrame())
    industry_of = {"600001.SH": "强势行业"}
    industry_hot = inq.industry_strength_lookup([
        inq.IndustryStrength(industry="强势行业", median_ret=0.05, member_count=10,
                             industry_rank=1, is_strength_day=True, persist_days=5),
    ])
    flags = inq._k4_flags(
        "600001.SH", __import__("datetime").date(2024, 1, 2),
        db_path=api_env.db_path, parquet_dir=api_env.parquet_dir,
        industry_of=industry_of, industry_hot=industry_hot,
    )
    assert len(flags) == 1 and "题材持续" in flags[0]


def test_k4_flags_no_industry_no_hit(api_env, monkeypatch):
    """票无 industry(不在 `industry_of` 里)→ `stock_persist_days` 恒 0,不误触 A2/B3。"""
    import polars as pl

    from neckline.report import holding_k4_check as hk

    monkeypatch.setattr(hk, "_build_holding_feature_panel", lambda codes, td, pd: pl.DataFrame())
    flags = inq._k4_flags(
        "600001.SH", __import__("datetime").date(2024, 1, 2),
        db_path=api_env.db_path, parquet_dir=api_env.parquet_dir,
        industry_of={}, industry_hot={},
    )
    assert flags == []


def test_run_deterministic_checks_wires_industry_scores_injection(market):
    """`run_deterministic_checks` 接受 `industry_scores` 注入(免联网/免真实全市场
    `daily` 扫描),题材类 K4 命中经此路径也能生效——同 `sector_scores` 既有注入姿势。"""
    s, day = market
    industry_scores = [
        inq.IndustryStrength(industry="电气设备", median_ret=0.05, member_count=10,
                             industry_rank=1, is_strength_day=True, persist_days=6),
    ]
    det = inq.run_deterministic_checks(
        "600001.SH", day, db_path=s.db_path, parquet_dir=s.parquet_dir,
        industry_scores=industry_scores,
    )
    assert det.has_data is True
    assert any("题材持续" in f for f in det.k4_flags)
    assert any("K4 安检命中" in e for e in det.evidence)


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
        """**契约只做加法,不做减法**:v1.3 那六个字段逐字段仍在(已装 macOS App 靠
        这个解码);v1.4-⑦-B 新增 `inquiryId` 是唯一允许的差异(旧客户端对未声明的
        多余字段直接忽略,不影响既有解码)。"""
        s, day = market
        import neckline.api.app as app_mod
        monkeypatch.setattr(app_mod, "_inquiry_basis_date", lambda: day)
        body = client.post("/api/v1/inquiry", headers=AUTH,
                           json={"code": "600001.SH", "messages": []}).json()
        assert set(body) == {"ok", "code", "reply", "verdict", "evidence", "degraded", "inquiryId"}
        assert isinstance(body["verdict"], str)
        assert isinstance(body["inquiryId"], int) and body["inquiryId"] >= 1

    def test_arbitrary_verdict_string_is_accepted_by_schema(self):
        """`verdict` 类型确已由 Literal 放宽成 str(否则新取值会 500)。"""
        from neckline.api.schemas import InquiryOut
        assert InquiryOut(code="x", reply="y", verdict="任意新取值").verdict == "任意新取值"


class TestSearchIdentityV134:
    """v1.3.4:问询台的联网搜索此前**搜错了东西**,两个原因各锁一组断言。

    生产真洞(2026-07-27 真 key 实证):供应商推导检索词时紧跟**最后一条 user 消息**,
    而问询台最后一条是用户的代词提问(「这只票…」)——身份信息躺在更早那条材料消息里
    也救不回来,搜回来的是泛泛板块新闻,模型只好退回训练数据答,用户看到的财报数据
    停在两年前。叠加 `det.name` 从来没被赋过值(材料首行恒「名称:未知」),连中文名
    这个中文财经检索最值钱的词都没有。"""

    def test_name_is_populated_from_stock_basic(self, market):
        """`det.name` 曾经声明了、被 `build_llm_context` 读了,却从没有任何一处赋值。"""
        s, day = market
        det = inq.run_deterministic_checks("600001.SH", day, db_path=s.db_path, parquet_dir=s.parquet_dir)
        assert det.name == "示例甲"
        assert "名称:示例甲" in inq.build_llm_context(det)
        assert "名称:未知" not in inq.build_llm_context(det)

    def test_name_populated_even_when_no_eod_data(self, market):
        """停牌/查无行情的票**更**要靠搜索说话,名字必须在 early return 之前就填好。"""
        s, day = market
        det = inq.run_deterministic_checks("300001.SZ", day, db_path=s.db_path, parquet_dir=s.parquet_dir)
        assert det.name == "示例丙"

    def test_search_query_carries_identity_not_just_the_pronoun(self, market):
        """核心回归:用户用代词提问时,检索词里必须仍有股票名+代码。"""
        s, day = market
        prov = StubProvider()
        inq.run_inquiry(
            "600001.SH", [{"role": "user", "content": "这只票你觉得后续走势会怎么样"}],
            basis_date=day, db_path=s.db_path, parquet_dir=s.parquet_dir, provider=prov,
        )
        q = prov.captured_search_query
        assert "示例甲" in q and "600001.SH" in q          # 身份(修复前这两样都不在检索词里)
        assert "后续走势" in q                              # 用户意图原样带上,检索词才贴题

    def test_search_query_carries_recency_hint_ahead_of_the_question(self, market):
        """v1.5.2(用户报障:603298 的回答把 2024 年研报当现行参照):检索词补当前年份 +
        「最新」。**紧跟主体、不放最末** —— 放最末会被 GLM 78 字截断连同长问句切掉。"""
        from datetime import date as _date

        s, day = market
        prov = StubProvider()
        long_q = "这只票最近的业绩和公告怎么样," + "顺便说说产业催化会不会兑现风险在哪" * 3
        inq.run_inquiry(
            "600001.SH", [{"role": "user", "content": long_q}],
            basis_date=day, db_path=s.db_path, parquet_dir=s.parquet_dir, provider=prov,
        )
        q = prov.captured_search_query
        hint = f"{_date.today().year} 最新"
        assert hint in q
        assert q.index(hint) < q.index("这只票")            # 主体之后、用户问句之前
        assert hint in q[:78]                                # 落在 GLM 截断窗口内

    def test_llm_context_first_line_is_the_current_date_anchor(self, market):
        """报障根因锁死:材料第一行必须告诉模型今天几号(此前一处都没有)。"""
        s, day = market
        det = inq.run_deterministic_checks("600001.SH", day, db_path=s.db_path, parquet_dir=s.parquet_dir)
        first = inq.build_llm_context(det).splitlines()[0]
        assert first.startswith("今天是 ") and "下一交易日" in first

    def test_system_prompt_carries_timeliness_rules(self):
        from neckline.llm.prompt_context import TIMELINESS_RULES
        assert TIMELINESS_RULES in inq.INQUIRY_SYSTEM_PROMPT

    def test_search_query_uses_last_user_turn_in_multi_turn(self, market):
        """多轮对话取**最后一句**——供应商推导检索词就是跟着它走的。"""
        s, day = market
        prov = StubProvider()
        inq.run_inquiry(
            "600001.SH",
            [{"role": "user", "content": "先聊聊基本面"},
             {"role": "assistant", "content": "好的……"},
             {"role": "user", "content": "那最近有没有利空公告"}],
            basis_date=day, db_path=s.db_path, parquet_dir=s.parquet_dir, provider=prov,
        )
        assert "利空公告" in prov.captured_search_query
        assert "先聊聊基本面" not in prov.captured_search_query

    def test_search_query_falls_back_to_code_when_name_unknown(self, market):
        """查无此票(stock_basic 没有)→ 退化成只带代码,不能拼出「(600009.SH)」这种空名括号。"""
        s, day = market
        prov = StubProvider()
        inq.run_inquiry(
            "600009.SH", [{"role": "user", "content": "怎么看"}],
            basis_date=day, db_path=s.db_path, parquet_dir=s.parquet_dir, provider=prov,
        )
        assert prov.captured_search_query.startswith("600009.SH")
        assert "()" not in prov.captured_search_query

    def test_zero_search_hits_is_surfaced_in_evidence(self, market):
        """0 命中必须让用户看见——否则「搜过没消息」和「一条都没搜到」在回答里长得一模一样。"""
        s, day = market
        prov = StubProvider(search_hits=[])
        out = inq.run_inquiry(
            "600001.SH", [{"role": "user", "content": "怎么看"}],
            basis_date=day, db_path=s.db_path, parquet_dir=s.parquet_dir, provider=prov,
        )
        assert any("命中 0 条" in e for e in out["evidence"])

    def test_hit_count_is_surfaced_in_evidence(self, market):
        s, day = market
        prov = StubProvider(search_hits=[object(), object(), object()])
        out = inq.run_inquiry(
            "600001.SH", [{"role": "user", "content": "怎么看"}],
            basis_date=day, db_path=s.db_path, parquet_dir=s.parquet_dir, provider=prov,
        )
        assert any("命中 3 条" in e for e in out["evidence"])

    def test_reply_stays_model_verbatim(self, market):
        """取证脚注只进 `evidence`,**不许掺进 `reply`**——reply 是模型原文(§v1.3.3 软护栏)。"""
        s, day = market
        prov = StubProvider(search_hits=[])
        out = inq.run_inquiry(
            "600001.SH", [{"role": "user", "content": "怎么看"}],
            basis_date=day, db_path=s.db_path, parquet_dir=s.parquet_dir, provider=prov,
        )
        assert out["reply"] == prov.reply_body


# —— v1.4-⑦-B:问询记录落库(P3-13,§七)——————————————————————————————————————
# `POST /inquiry` 此前只返回不持久化;`run_inquiry` 结尾旁路写 `inquiry_log`,失败
# 不影响本次回答。端点(`GET /inquiries` 列表 / `GET /inquiries/{id}` 详情)的装配
# /分页/过滤/404 测试见 `tests/test_api_inquiry_log.py`,本节只测 `run_inquiry` 本身
# 的落库行为。

class TestInquiryLogPersistence:
    def test_run_inquiry_writes_one_row_and_returns_id(self, market):
        s, day = market
        prov = StubProvider(reply_body="龙头效应还在,量能没走坏。")
        out = inq.run_inquiry(
            "600001.SH", [{"role": "user", "content": "怎么看走势"}],
            basis_date=day, db_path=s.db_path, parquet_dir=s.parquet_dir, provider=prov,
        )
        assert isinstance(out["inquiryId"], int) and out["inquiryId"] >= 1
        row = get_inquiry_log(out["inquiryId"], db_path=s.db_path)
        assert row is not None
        assert row["code"] == "600001.SH"
        assert row["question"] == "怎么看走势"
        assert row["answer"] == out["reply"]
        assert row["verdict"] == out["verdict"]
        assert row["evidence"] == out["evidence"]
        assert row["materials"]["board"] == "主板"
        assert row["positionId"] is None and row["decisionId"] is None   # 当前无写入方,见表头注释

    def test_no_coupling_with_inquiry_pool(self, market_wall_down):
        """问一次同时验证两件事:`inquiry_log` 落了一行,`inquiry_pool`
        (已退役历史队列表)分毫未动——两张表各自独立,不是"改了一个就顺手也写了
        另一个"(§七 P3-13 验收「与 inquiry_pool 无耦合」)。"""
        s, day = market_wall_down
        out = inq.run_inquiry("300001.SZ", [], basis_date=day, db_path=s.db_path,
                              parquet_dir=s.parquet_dir, provider=StubProvider())
        assert out["inquiryId"] is not None
        assert load_inquiry_pool(day, db_path=s.db_path) == []

    def test_question_is_last_user_message_in_multi_turn(self, market):
        s, day = market
        out = inq.run_inquiry(
            "600001.SH",
            [{"role": "user", "content": "先聊聊基本面"},
             {"role": "assistant", "content": "好的……"},
             {"role": "user", "content": "那最近有没有利空公告"}],
            basis_date=day, db_path=s.db_path, parquet_dir=s.parquet_dir, provider=StubProvider(),
        )
        row = get_inquiry_log(out["inquiryId"], db_path=s.db_path)
        assert row["question"] == "那最近有没有利空公告"

    def test_question_empty_string_when_no_messages(self, market):
        s, day = market
        out = inq.run_inquiry("600001.SH", [], basis_date=day, db_path=s.db_path,
                              parquet_dir=s.parquet_dir, provider=StubProvider())
        row = get_inquiry_log(out["inquiryId"], db_path=s.db_path)
        assert row["question"] == ""

    def test_search_hits_full_text_archived_not_just_count(self, market):
        """联网搜索命中全文落档(同 `llm_judgments.search_hits_json` 惯例),不是只存
        条数——供事后审计"当时搜到了什么"。"""
        s, day = market
        hits = [SearchHit(title="示例甲拿下大单", link="https://example.com/a", content="正文……")]
        prov = StubProvider(search_hits=hits)
        out = inq.run_inquiry("600001.SH", [{"role": "user", "content": "怎么看"}],
                              basis_date=day, db_path=s.db_path, parquet_dir=s.parquet_dir, provider=prov)
        row = get_inquiry_log(out["inquiryId"], db_path=s.db_path)
        assert len(row["searchHits"]) == 1
        assert row["searchHits"][0]["title"] == "示例甲拿下大单"
        assert row["searchHits"][0]["link"] == "https://example.com/a"

    def test_degraded_reply_also_gets_archived(self, market_wall_down):
        """缺 key 的降级回答同样是"实质回答",一并落档(不是只存成功案例)。"""
        s, day = market_wall_down
        out = inq.run_inquiry("300001.SZ", [], basis_date=day, db_path=s.db_path,
                              parquet_dir=s.parquet_dir, provider=None)
        assert out["degraded"] is True
        assert out["inquiryId"] is not None
        row = get_inquiry_log(out["inquiryId"], db_path=s.db_path)
        assert row["answer"] == out["reply"]

    def test_persistence_failure_degrades_gracefully_without_breaking_answer(self, market, monkeypatch):
        """落库是旁路——DB 写失败绝不能打断已经算好的回答;`degraded` 字段专指 LLM
        段,与"档案有没有落进去"是两件独立的事(不因落库失败被带偏)。"""
        s, day = market
        import neckline.api.inquiry as inq_mod

        def _boom(*a, **k):
            raise RuntimeError("磁盘写满(演练)")

        monkeypatch.setattr(inq_mod, "create_inquiry_log", _boom)
        prov = StubProvider(reply_body="不受影响,回答照常。")
        out = inq.run_inquiry("600001.SH", [], basis_date=day, db_path=s.db_path,
                              parquet_dir=s.parquet_dir, provider=prov)
        assert out["reply"] == "不受影响,回答照常。"
        assert out["inquiryId"] is None
        assert out["degraded"] is False


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
