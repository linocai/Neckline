"""⑪-C 自然语言解析层单测(`neckline/llm/nl_alert.py`)。

⑪ 验收条款点名的两条在这里:
    · **NL 解析 golden 单测(六类条件各一,含一条组合条件)**;
    · **LLM 不可用 → 降级为手填结构化表单**(不静默失败)。
外加两条本项目的通用铁律:提示词必须带**日期锚**;LLM 产出必须过**白名单**才算数。
"""

from __future__ import annotations

import json

import pytest

from neckline import custom_alerts as ca
from neckline.llm import nl_alert as nl
from neckline.llm.base import LLMResult


class _StubProvider:
    """最小假 provider(不经 httpx),把 `chat()` 返回值设死,专注测解析逻辑本身
    (同 `tests/test_judge.py::_StubProvider` 体例)。"""

    name = "stub"
    model = "stub-model"

    def __init__(self, content: str = "", *, ok: bool = True, reason: str = "ok") -> None:
        self._result = LLMResult(ok=ok, content=content, reason=reason,
                                 provider=self.name, model=self.model)
        self.captured = []
        self.enable_search_seen = []

    def chat(self, messages, *, enable_search=True, transport=None, search_query=None):
        self.captured.append(messages)
        self.enable_search_seen.append(enable_search)
        return self._result


def _reply(payload: dict, narrative: str = "明白了,给你设一条提醒。") -> str:
    return narrative + "\n\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


# ══════════════════════════════════════════════════════════════════════════
# golden:六类条件各一 + 一条组合
# ══════════════════════════════════════════════════════════════════════════

GOLDEN = [
    ("价格", {"action": "create", "ts_code": "600519.SH",
              "conditions": [{"metric": "price", "op": "<=", "value": 15.0}]},
     "price"),
    ("涨跌幅", {"action": "create", "ts_code": "600519.SH",
                "conditions": [{"metric": "chg_pct", "op": "<=", "value": -0.05}]},
     "chg_pct"),
    ("相对成本", {"action": "create", "ts_code": "600519.SH",
                  "conditions": [{"metric": "vs_cost", "op": ">=", "value": 0.08}]},
     "vs_cost"),
    ("相对日内高点", {"action": "create", "ts_code": "600519.SH",
                      "conditions": [{"metric": "from_day_high", "op": "<=", "value": -0.03}]},
     "from_day_high"),
    ("量能", {"action": "create", "ts_code": "600519.SH",
              "conditions": [{"metric": "volume_ratio", "op": ">=", "value": 2.0}]},
     "volume_ratio"),
    ("大盘", {"action": "create", "ts_code": None,
              "conditions": [{"metric": "index_chg_pct", "op": "<=", "value": -0.02,
                              "ref": "000001.SH"}]},
     "index_chg_pct"),
    ("篮子", {"action": "create", "ts_code": "600519.SH",
              "conditions": [{"metric": "basket_weak_ratio", "op": ">=", "value": 0.5}]},
     "basket_weak_ratio"),
]


@pytest.mark.parametrize("label,payload,metric", GOLDEN, ids=[g[0] for g in GOLDEN])
def test_golden_single_condition(label, payload, metric):
    p = _StubProvider(_reply(payload))
    got = nl.parse_nl_alert("随便一句话", provider=p)
    assert got.ok, got.reason
    assert got.rule["conditions"][0]["metric"] == metric
    assert got.rule["schema_version"] == ca.RULE_SCHEMA_VERSION


def test_golden_combined_condition():
    """组合条件(蓝图 5.6 明写要支持):个股价格 **或** 大盘跌幅。"""
    p = _StubProvider(_reply({
        "action": "create", "ts_code": "600519.SH", "logic": "any",
        "conditions": [
            {"metric": "price", "op": "<=", "value": 15.0},
            {"metric": "index_chg_pct", "op": "<=", "value": -0.02, "ref": "000001.SH"},
        ],
        "active_from": "13:30", "persist": False, "max_fires": 1,
    }))
    got = nl.parse_nl_alert("今天 13:30 以后,跌到 15 或者大盘跌 2% 就叫我", provider=p)
    assert got.ok and got.rule["logic"] == "any" and len(got.rule["conditions"]) == 2
    assert got.active_from == "13:30" and got.persist is False


def test_golden_metrics_cover_the_whole_whitelist():
    """自检:golden 覆盖白名单里每一个 metric(加 metric 忘了加 golden 就挂)。"""
    covered = {m for _l, _p, m in GOLDEN}
    assert covered == set(ca.ALL_METRICS)


# ══════════════════════════════════════════════════════════════════════════
# 提示词铁律
# ══════════════════════════════════════════════════════════════════════════

def test_prompt_carries_date_anchor():
    """CLAUDE.md 2026-07-30 事故:喂 LLM 的上下文必须带「今天是哪天」——「今天
    13:30 以后」这种相对时间没有日期锚就会算错生效窗。"""
    msgs = nl.build_messages("跌到 15 通知我")
    user = msgs[-1].content
    assert "今天是" in user and "下一交易日" in user


def test_prompt_lists_whitelist_and_forbids_inventing_metrics():
    msgs = nl.build_messages("x")
    system = msgs[0].content
    for m in ca.ALL_METRICS:
        assert m in system
    assert "不许发明新指标" in system
    assert "只通知,不自动交易" in system or "不自动交易" in system


def test_search_is_disabled_for_parsing():
    """把一句口语翻成规则不需要联网 —— 开搜索只是拖慢交互 + 烧预算。"""
    p = _StubProvider(_reply(GOLDEN[0][1]))
    nl.parse_nl_alert("跌到 15 通知我", provider=p)
    assert p.enable_search_seen == [False]


def test_context_hint_is_used_when_model_omits_code():
    p = _StubProvider(_reply({"action": "create",
                              "conditions": [{"metric": "price", "op": "<=", "value": 15.0}]}))
    got = nl.parse_nl_alert("跌到 15", ts_code_hint="600519.SH", provider=p)
    assert got.ok and got.ts_code == "600519.SH"


# ══════════════════════════════════════════════════════════════════════════
# 降级 / 拒收
# ══════════════════════════════════════════════════════════════════════════

class TestDegradation:
    def test_no_provider_degrades_to_manual_form(self, isolated_env, monkeypatch):
        """⑪-C:LLM 不可用 → **降级为手填结构化表单**,不静默失败。"""
        monkeypatch.setattr("neckline.llm.factory.get_provider", lambda *a, **k: None)
        got = nl.parse_nl_alert("跌到 15 通知我", db_path=isolated_env.db_path)
        assert got.ok is False and got.degraded is True
        assert got.manual_form is not None
        names = {f["name"] for f in got.manual_form["fields"]}
        assert {"tsCode", "conditions", "persist", "maxFires"} <= names
        assert "不可用" in got.reason

    def test_provider_failure_degrades_too(self):
        p = _StubProvider("", ok=False, reason="读超时")
        got = nl.parse_nl_alert("跌到 15", provider=p)
        assert got.ok is False and got.degraded is True and "读超时" in got.reason

    def test_provider_exception_degrades_not_crashes(self):
        class _Boom:
            name, model = "boom", "m"

            def chat(self, *a, **k):
                raise RuntimeError("网络炸了")

        got = nl.parse_nl_alert("跌到 15", provider=_Boom())
        assert got.ok is False and got.degraded is True and got.manual_form is not None

    def test_manual_form_is_generated_from_the_whitelist(self):
        form = nl.manual_form_schema()
        conds = [f for f in form["fields"] if f["name"] == "conditions"][0]
        assert conds["item"]["metric"]["enum"] == list(ca.ALL_METRICS)
        assert conds["maxItems"] == ca.MAX_CONDITIONS


class TestRejects:
    def test_no_json_block_is_a_parse_failure_not_a_degradation(self):
        """模型答了但不给机器可读段 = **解析失败**,不是 LLM 不可用 —— 降级成手填会
        把「提示词/模型不配合」这个真问题盖住。"""
        p = _StubProvider("好的,我记住了。")
        got = nl.parse_nl_alert("跌到 15", provider=p)
        assert got.ok is False and got.degraded is False and got.manual_form is None

    def test_invented_metric_is_rejected(self):
        p = _StubProvider(_reply({"action": "create", "ts_code": "600519.SH",
                                  "conditions": [{"metric": "rsi", "op": "<=", "value": 30}]}))
        got = nl.parse_nl_alert("RSI 低于 30 提醒我", provider=p)
        assert got.ok is False and "不合法" in got.reason and got.rule is None

    def test_percentage_written_as_whole_number_is_rejected(self):
        p = _StubProvider(_reply({"action": "create", "ts_code": "600519.SH",
                                  "conditions": [{"metric": "chg_pct", "op": "<=", "value": -5}]}))
        got = nl.parse_nl_alert("跌 5% 通知我", provider=p)
        assert got.ok is False and got.rule is None      # ⛔ 不替它猜成 -0.05

    def test_empty_text(self):
        got = nl.parse_nl_alert("   ")
        assert got.ok is False


# ══════════════════════════════════════════════════════════════════════════
# 其它意图 + 确认卡
# ══════════════════════════════════════════════════════════════════════════

def test_query_action_needs_no_rule():
    p = _StubProvider(_reply({"action": "query"}))
    got = nl.parse_nl_alert("我现在都有什么提醒", provider=p)
    assert got.ok and got.action == nl.ACTION_QUERY and got.rule is None


def test_cancel_action_carries_target_id():
    p = _StubProvider(_reply({"action": "cancel", "target_alert_id": 7}))
    got = nl.parse_nl_alert("把茅台那条取消", provider=p)
    assert got.ok and got.action == nl.ACTION_CANCEL and got.target_alert_id == 7


def test_confirmation_card_from_parse_has_seven_items():
    p = _StubProvider(_reply(GOLDEN[0][1]))
    got = nl.parse_nl_alert("跌到 15 通知我", provider=p)
    card = nl.confirmation_card_for(got, name="贵州茅台")
    assert card is not None
    d = card.to_dict()
    assert all(d[k] for k in ("subject", "condition", "active_window", "notify_limit",
                              "expiry", "quote_delay_disclosure", "no_auto_trade"))


def test_no_card_for_query_intent():
    p = _StubProvider(_reply({"action": "query"}))
    assert nl.confirmation_card_for(nl.parse_nl_alert("有啥提醒", provider=p)) is None


def test_parse_layer_never_writes_to_db(isolated_env):
    """解析层只解析:跑一遍之后 `custom_alerts` 一行都不该多出来(落库要用户确认)。"""
    p = _StubProvider(_reply(GOLDEN[0][1]))
    nl.parse_nl_alert("跌到 15 通知我", provider=p, db_path=isolated_env.db_path)
    assert ca.list_alerts(db_path=isolated_env.db_path) == []
