"""4A.5 问询台单测(plan 4A 验收:二值裁决 + 依据、初审通过写 `inquiry_pool`;
**「永不买」不变量**)。用合成市场三票(主板通过 / *ST 剔除 / 创业板剔除)跑**同码**
确定性纪律核对,不手搓一套规则。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

import pytest

from neckline.api import inquiry as inq
from neckline.api.schemas import VERDICT_PASS, VERDICT_REJECT
from neckline.api.stores import load_inquiry_pool
from neckline.llm.base import ChatMessage, LLMResult
from tests.conftest import seed_active_rule_v1, seed_synthetic_market


@dataclass
class StubProvider:
    """可注入的假 LLM provider:按预设 verdict 标签返回内容,不联网。"""
    name: str = "glm"
    default_model: str = "glm-5.2"
    reply_body: str = "结合搜索,题材催化尚在,未见明显利空。"
    tag: str = "初审通过"          # "初审通过" | "不符合" | ""(不给标签)
    ok: bool = True
    captured: List[ChatMessage] = field(default_factory=list)

    def chat(self, messages, *, enable_search=True, transport=None) -> LLMResult:
        self.captured = list(messages)
        content = self.reply_body + (f"\n裁决:{self.tag}" if self.tag else "")
        return LLMResult(ok=self.ok, content=content, provider=self.name, model=self.default_model)


@pytest.fixture
def market(api_env):
    """合成市场 + 现役 rule v1;返回 (settings, report_day)。"""
    dates = seed_synthetic_market(api_env)
    seed_active_rule_v1(api_env)
    return api_env, dates[-1]


# —— 确定性核对(同码,无 LLM)————————————————————————————————————————————

def test_deterministic_mainboard_passes(market):
    s, day = market
    det = inq.run_deterministic_checks("600001.SH", day, db_path=s.db_path, parquet_dir=s.parquet_dir)
    assert det.has_data is True
    assert det.disqualifiers == []
    assert det.passes_discipline is True
    assert det.board == "主板"


def test_deterministic_st_rejected(market):
    s, day = market
    det = inq.run_deterministic_checks("600002.SH", day, db_path=s.db_path, parquet_dir=s.parquet_dir)
    assert det.passes_discipline is False
    assert any("ST" in d for d in det.disqualifiers)


def test_deterministic_gem_rejected(market):
    s, day = market
    det = inq.run_deterministic_checks("300001.SZ", day, db_path=s.db_path, parquet_dir=s.parquet_dir)
    assert det.passes_discipline is False
    assert any("高弹" in d or "创业板" in d for d in det.disqualifiers)


def test_deterministic_unknown_code_no_data(market):
    s, day = market
    det = inq.run_deterministic_checks("999999.SZ", day, db_path=s.db_path, parquet_dir=s.parquet_dir)
    assert det.has_data is False
    assert det.passes_discipline is False


# —— run_inquiry:裁决 + 海选池写入 + 降级 —————————————————————————————————

def test_inquiry_pass_writes_pool_no_llm(market):
    s, day = market
    out = inq.run_inquiry("600001.SH", [], basis_date=day, pool_date=day,
                          db_path=s.db_path, parquet_dir=s.parquet_dir, provider=None)
    assert out["verdict"] == VERDICT_PASS
    assert out["degraded"] is True                    # 无 LLM → 降级占位
    pool = load_inquiry_pool(day, db_path=s.db_path)
    assert [p["ts_code"] for p in pool] == ["600001.SH"]


def test_inquiry_reject_does_not_write_pool(market):
    s, day = market
    out = inq.run_inquiry("600002.SH", [], basis_date=day, pool_date=day,
                          db_path=s.db_path, parquet_dir=s.parquet_dir, provider=None)
    assert out["verdict"] == VERDICT_REJECT
    assert load_inquiry_pool(day, db_path=s.db_path) == []


def test_inquiry_llm_veto_flips_to_reject(market):
    s, day = market
    prov = StubProvider(tag="不符合", reply_body="搜索发现重大商誉减值利空。")
    out = inq.run_inquiry("600001.SH", [{"role": "user", "content": "看看这票"}],
                          basis_date=day, pool_date=day, db_path=s.db_path,
                          parquet_dir=s.parquet_dir, provider=prov)
    assert out["verdict"] == VERDICT_REJECT           # 确定性通过但 LLM 显式否决 → 不符合
    assert load_inquiry_pool(day, db_path=s.db_path) == []
    # 注入了 system + 确定性上下文 + 用户消息
    roles = [m.role for m in prov.captured]
    assert roles[0] == "system" and "user" in roles


def test_inquiry_llm_pass_keeps_pass(market):
    s, day = market
    prov = StubProvider(tag="初审通过")
    out = inq.run_inquiry("600001.SH", [], basis_date=day, pool_date=day,
                          db_path=s.db_path, parquet_dir=s.parquet_dir, provider=prov)
    assert out["verdict"] == VERDICT_PASS
    assert out["degraded"] is False


def test_inquiry_hard_reject_never_calls_llm(market):
    """确定性硬性不符合 → 直接不符合,不劳 LLM(纪律不过不放行,即便 LLM 想翻案)。"""
    s, day = market
    prov = StubProvider(tag="初审通过")               # LLM 若被调会说"通过"
    out = inq.run_inquiry("600002.SH", [], basis_date=day, pool_date=day,
                          db_path=s.db_path, parquet_dir=s.parquet_dir, provider=prov)
    assert out["verdict"] == VERDICT_REJECT
    assert prov.captured == []                        # 根本没调用 LLM


# —— 「永不买」不变量(硬约束,§2.5)——————————————————————————————————————

@pytest.mark.parametrize("code", ["600001.SH", "600002.SH", "300001.SZ", "999999.SZ"])
def test_verdict_always_binary_never_buy(market, code):
    s, day = market
    # 即便 LLM 疯狂喊「现在就买买买」,裁决仍只可能是两个枚举值之一,绝不出现「买」路径
    prov = StubProvider(tag="", reply_body="现在就买!马上买入!强烈建议买买买!")
    out = inq.run_inquiry(code, [], basis_date=day, pool_date=day,
                          db_path=s.db_path, parquet_dir=s.parquet_dir, provider=prov)
    assert out["verdict"] in (VERDICT_PASS, VERDICT_REJECT)
    assert "买" not in out["verdict"]


def test_llm_no_tag_keeps_deterministic_pass(market):
    """LLM 未给裁决标签(格式异常)→ 保持确定性已判的初审通过(不因格式碎裂误杀已过纪律的票)。"""
    s, day = market
    prov = StubProvider(tag="", reply_body="一段没有结论标签的分析。")
    out = inq.run_inquiry("600001.SH", [], basis_date=day, pool_date=day,
                          db_path=s.db_path, parquet_dir=s.parquet_dir, provider=prov)
    assert out["verdict"] == VERDICT_PASS


def test_llm_call_failure_degrades(market):
    s, day = market
    prov = StubProvider(ok=False)
    out = inq.run_inquiry("600001.SH", [], basis_date=day, pool_date=day,
                          db_path=s.db_path, parquet_dir=s.parquet_dir, provider=prov)
    assert out["verdict"] == VERDICT_PASS and out["degraded"] is True


# —— 端点集成 ————————————————————————————————————————————————————————

def test_inquiry_endpoint(client, AUTH, market, monkeypatch):
    s, day = market
    import neckline.api.app as app_mod
    monkeypatch.setattr(app_mod, "_inquiry_basis_pool_date", lambda: (day, day))
    r = client.post("/api/v1/inquiry", headers=AUTH, json={"code": "600001.SH", "messages": []})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == VERDICT_PASS
    assert body["evidence"]                          # 有依据
    assert body["code"] == "600001.SH"


def test_inquiry_endpoint_st_rejected(client, AUTH, market, monkeypatch):
    s, day = market
    import neckline.api.app as app_mod
    monkeypatch.setattr(app_mod, "_inquiry_basis_pool_date", lambda: (day, day))
    body = client.post("/api/v1/inquiry", headers=AUTH, json={"code": "600002.SH", "messages": []}).json()
    assert body["verdict"] == VERDICT_REJECT
