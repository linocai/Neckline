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


# —— v1.3-⑤ 选股域漂移清理:同码 + 裁决等价(plan §五 v1.3-⑤ 验收)—————————————

def test_disqualifiers_reuse_shared_discipline_checks(market):
    """`run_deterministic_checks` 与 `report.watchlist_check.discipline_checks` 是
    同一个函数(不是两份各自维护的阈值)——直接断言两者对象同一,防止未来有人在
    问询台悄悄另起一份。"""
    from neckline.report.watchlist_check import discipline_checks as wc_discipline_checks

    assert inq.discipline_checks is wc_discipline_checks


def test_matches_watchlist_check_same_code_same_day(market):
    """§v1.3-⑤ 验收:问询台确定性核对与报告 `watchlist_check` 对同一票、同一日
    同判(同码一致,disqualifiers 逐项集合相等,仅问询台额外展示「板块被排除」这
    一行不参与本比较)。"""
    from neckline.report.watchlist_check import build_watchlist_check
    from neckline.strategy import brain

    s, day = market
    active = brain.get_active(db_path=s.db_path)
    for code in ("600001.SH", "600002.SH", "300001.SZ"):
        det = inq.run_deterministic_checks(code, day, db_path=s.db_path, parquet_dir=s.parquet_dir)
        wc_items = build_watchlist_check(
            day, active.rule,
            [{"ts_code": code, "name": code, "pinned": False, "source": "manual"}],
            parquet_dir=s.parquet_dir, db_path=s.db_path,
        )
        assert len(wc_items) == 1
        wc = wc_items[0]
        assert det.passes_discipline == wc.green_light, code
        assert set(det.disqualifiers) == set(wc.disqualifiers), code


def test_verdict_equivalent_to_pre_cleanup_handrolled_logic(market):
    """§v1.3-⑤ 必须证明:清理前(问询台手写选股域五项 + P6 次新,**不含** P4/P5)
    与清理后(`discipline_checks` 同码)在 K1 现役(P4/P5=None)下对同一批票的
    `passes_discipline` 结果逐票相等——原因文案粒度允许变(五项→一条组合原因),
    通过/不通过的布尔裁决不允许变。下面的 `_old_disqualified` 是清理前
    `run_deterministic_checks` 原文手写逻辑的冻结快照(仅供本测试当历史 oracle,
    不是第二份生产逻辑,不会被维护/不会漂移)。"""
    from neckline.strategy import signals as S
    from neckline.strategy.features import build_research_panel

    s, day = market
    cfg = inq._cfg_from_active(s.db_path)
    panel = build_research_panel(day, day, with_forward=False, parquet_dir=s.parquet_dir)
    panel = S.add_ret_rank_column(panel)

    def _old_disqualified(code: str) -> bool:
        sub = panel.filter(panel["ts_code"] == code)
        row = sub.row(0, named=True)
        if row.get("is_st"):
            return True
        board_raw = row.get("board", "MAIN")
        if board_raw == "BSE":
            return True
        if cfg.forbid_high_elasticity and board_raw in ("GEM", "STAR") and board_raw != "BSE":
            return True
        close = row.get("close")
        if close is not None and close < 2.0:
            return True
        amt = row.get("amount_ma20")
        if amt is not None and amt < 20000:
            return True
        if row.get("ma20") is None:
            return True
        if cfg.forbid_new_days is not None:
            dsl = row.get("days_since_listing")
            if dsl is not None and dsl < cfg.forbid_new_days:
                return True
        return False

    for code in ("600001.SH", "600002.SH", "300001.SZ"):
        old_passes = not _old_disqualified(code)
        det = inq.run_deterministic_checks(code, day, db_path=s.db_path, parquet_dir=s.parquet_dir)
        assert det.passes_discipline == old_passes, (
            f"{code}: 清理前 passes={old_passes},清理后 passes={det.passes_discipline}"
        )


def test_forbid_green_bigdown_now_reaches_inquiry_previously_ignored(api_env):
    """§v1.3-⑤ 根因清理顺带修的真实缺口:清理前 `run_deterministic_checks` 完全
    没实现 P4(`forbid_green_bigdown`)/P5(`forbid_far_from_high`)核对——即便现役
    config 打开,问询台也会静默放行。`discipline_checks` 同码后,P4 现在必须能
    拦下来。合成市场报告日全部代码 `ret_1d≈-1%`(见 `seed_synthetic_market` 收官
    小幅回调),用阈值 -0.5% 的 config 强制命中。"""
    dates = seed_synthetic_market(api_env)
    seed_active_rule_v1(api_env, extra_config={"forbid_green_bigdown": -0.005})
    day = dates[-1]
    det = inq.run_deterministic_checks("600001.SH", day, db_path=api_env.db_path, parquet_dir=api_env.parquet_dir)
    assert det.passes_discipline is False
    assert any("绿盘大阴线" in d for d in det.disqualifiers)
