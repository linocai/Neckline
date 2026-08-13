"""⑪-C 临时提醒端点契约单测(`/alerts` CRUD + `/alerts/parse`)。

契约面的四条硬要求:
    · **落库路径只有一条**(`POST /alerts`);`/alerts/parse` 不写库;
    · 相同提醒 → 409 `duplicate_alert`;规则不合白名单 → 422 `invalid_rule`;
    · 删除 = 取消(状态改成 `cancelled`,**行还在**);
    · `/alerts/parse` **恒 200**:LLM 不可用回 `degraded=true` + 手填表单,不静默失败。
"""

from __future__ import annotations

import json

from neckline import custom_alerts as ca
from neckline.llm.base import LLMResult

CODE = "600519.SH"


def _price_cond(v=15.0):
    return {"metric": "price", "op": "<=", "value": v}


def _create(client, AUTH, **over):
    body = {"tsCode": CODE, "nlText": "跌到 15 通知我", "conditions": [_price_cond()],
            "logic": "all"}
    body.update(over)
    return client.post("/api/v1/alerts", headers=AUTH, json=body)


class TestCrud:
    def test_create_and_list(self, client, AUTH):
        r = _create(client, AUTH)
        assert r.status_code == 201, r.text
        got = r.json()
        assert got["tsCode"] == CODE and got["status"] == "active"
        assert got["maxFires"] == 1 and got["firedCount"] == 0
        assert "现价 ≤ 15.00 元" in got["condition"]

        items = client.get("/api/v1/alerts", headers=AUTH).json()["items"]
        assert [i["id"] for i in items] == [got["id"]]

    def test_duplicate_is_409(self, client, AUTH):
        _create(client, AUTH)
        r = _create(client, AUTH, nlText="换个说法但规则一样")
        assert r.status_code == 409
        assert r.json()["detail"]["reason"] == "duplicate_alert"

    def test_invalid_rule_is_422_with_readable_message(self, client, AUTH):
        r = _create(client, AUTH, conditions=[{"metric": "rsi", "op": "<=", "value": 30}])
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["reason"] == "invalid_rule" and detail["message"]

    def test_market_level_alert_rejects_stock_metric(self, client, AUTH):
        r = _create(client, AUTH, tsCode=None, conditions=[_price_cond()])
        assert r.status_code == 422 and r.json()["detail"]["reason"] == "invalid_rule"

    def test_market_level_alert_with_index_condition_ok(self, client, AUTH):
        r = _create(client, AUTH, tsCode=None, conditions=[
            {"metric": "index_chg_pct", "op": "<=", "value": -0.02, "ref": "000001.SH"}])
        assert r.status_code == 201 and r.json()["tsCode"] is None

    def test_update_partial(self, client, AUTH):
        aid = _create(client, AUTH).json()["id"]
        r = client.put(f"/api/v1/alerts/{aid}", headers=AUTH, json={"maxFires": 3})
        assert r.status_code == 200 and r.json()["maxFires"] == 3
        assert "现价 ≤ 15.00 元" in r.json()["condition"]     # 规则没被顺手改掉

    def test_update_missing_is_404(self, client, AUTH):
        r = client.put("/api/v1/alerts/999", headers=AUTH, json={"maxFires": 2})
        assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"

    def test_delete_is_cancel_not_physical_delete(self, client, AUTH, api_env):
        aid = _create(client, AUTH).json()["id"]
        assert client.delete(f"/api/v1/alerts/{aid}", headers=AUTH).status_code == 200
        row = ca.get_alert(aid, db_path=api_env.db_path)
        assert row is not None and row.status == "cancelled"      # 台账留痕

    def test_delete_missing_is_404(self, client, AUTH):
        r = client.delete("/api/v1/alerts/999", headers=AUTH)
        assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"

    def test_status_filter(self, client, AUTH):
        """⚠ **V2-⑭-B 契约修正(契约线 🔵 B7)**:查询参数名由 Python 形参名
        `status_filter` 改为契约名 `status`(形参仍叫 `status_filter`,因为 `status`
        会遮住模块级 `fastapi.status`)。"""
        aid = _create(client, AUTH).json()["id"]
        client.delete(f"/api/v1/alerts/{aid}", headers=AUTH)
        assert client.get("/api/v1/alerts?status=active", headers=AUTH).json()["items"] == []
        got = client.get("/api/v1/alerts?status=cancelled", headers=AUTH).json()["items"]
        assert [i["id"] for i in got] == [aid]

    def test_status_filter_query_key_is_camel_contract_name_not_python_param(self, client, AUTH):
        """防复发:形参名不许再漏成契约键。老键 `status_filter` 现在只是个被忽略的
        未知查询参数(FastAPI 不报错),故断言它**不再起过滤作用**。"""
        aid = _create(client, AUTH).json()["id"]
        client.delete(f"/api/v1/alerts/{aid}", headers=AUTH)
        got = client.get("/api/v1/alerts?status_filter=active", headers=AUTH).json()["items"]
        assert [i["id"] for i in got] == [aid], "老参数名不该再被识别成过滤条件"

    def test_create_records_a_user_action(self, client, AUTH, api_env):
        """⑩-D 五类用户行为之一(`alert`):建提醒是用户行为,落 append-only 台账。"""
        from neckline import user_actions
        _create(client, AUTH)
        rows = user_actions.list_actions(kind="alert", db_path=api_env.db_path)
        assert len(rows) == 1 and rows[0]["ts_code"] == CODE

    def test_requires_token(self, client):
        assert client.get("/api/v1/alerts").status_code in (401, 403)


class TestParse:
    def _stub_provider(self, monkeypatch, content, ok=True, reason="ok"):
        class _P:
            name, model = "stub", "stub-model"

            def chat(self, messages, *, enable_search=True, transport=None, search_query=None):
                return LLMResult(ok=ok, content=content, reason=reason,
                                 provider="stub", model="stub-model")

        monkeypatch.setattr("neckline.llm.factory.get_provider", lambda *a, **k: _P())

    def test_parse_returns_confirmation_card_and_draft(self, client, AUTH, monkeypatch):
        payload = {"action": "create", "ts_code": CODE, "logic": "all",
                   "conditions": [_price_cond()], "active_from": "13:30"}
        self._stub_provider(monkeypatch, "好的。\n\n```json\n" + json.dumps(payload) + "\n```")
        r = client.post("/api/v1/alerts/parse", headers=AUTH,
                        json={"text": "今天 13:30 以后跌到 15 通知我"})
        assert r.status_code == 200
        got = r.json()
        assert got["ok"] is True and got["action"] == "create"
        card = got["confirmationCard"]
        # 七项俱全,后两项是必选披露
        for k in ("subject", "condition", "activeWindow", "notifyLimit", "expiry",
                  "quoteDelayDisclosure", "noAutoTrade"):
            assert card[k], f"确认卡缺项:{k}"
        assert "延迟" in card["quoteDelayDisclosure"]
        assert "不自动交易" in card["noAutoTrade"]
        # draft 可原样回传给 POST /alerts
        assert got["draft"]["tsCode"] == CODE and got["draft"]["activeFrom"] == "13:30"

    def test_parse_does_not_persist(self, client, AUTH, api_env, monkeypatch):
        payload = {"action": "create", "ts_code": CODE, "conditions": [_price_cond()]}
        self._stub_provider(monkeypatch, "```json\n" + json.dumps(payload) + "\n```")
        client.post("/api/v1/alerts/parse", headers=AUTH, json={"text": "跌到 15"})
        assert ca.list_alerts(db_path=api_env.db_path) == []      # 用户确认后才落库

    def test_parse_then_create_roundtrip(self, client, AUTH):
        """确认卡 → 用户点确认 → 用 draft 直接 POST /alerts,一条链走通。"""
        import json as _json

        payload = {"action": "create", "ts_code": CODE, "conditions": [_price_cond()]}

        class _P:
            name, model = "stub", "m"

            def chat(self, *a, **k):
                return LLMResult(ok=True, content="```json\n" + _json.dumps(payload) + "\n```",
                                 provider="stub", model="m")

        import neckline.llm.factory as factory
        old = factory.get_provider
        factory.get_provider = lambda *a, **k: _P()
        try:
            draft = client.post("/api/v1/alerts/parse", headers=AUTH,
                                json={"text": "跌到 15"}).json()["draft"]
        finally:
            factory.get_provider = old
        r = client.post("/api/v1/alerts", headers=AUTH, json=draft)
        assert r.status_code == 201 and r.json()["tsCode"] == CODE

    def test_parse_degrades_to_manual_form_when_llm_unavailable(self, client, AUTH, monkeypatch):
        """⑪-C:**200 + degraded + 手填表单**,不是 5xx、也不是静默失败。"""
        monkeypatch.setattr("neckline.llm.factory.get_provider", lambda *a, **k: None)
        r = client.post("/api/v1/alerts/parse", headers=AUTH, json={"text": "跌到 15 通知我"})
        assert r.status_code == 200
        got = r.json()
        assert got["ok"] is False and got["degraded"] is True
        assert got["manualForm"] is not None and got["confirmationCard"] is None
        assert got["reason"]

    def test_parse_query_intent_returns_matches(self, client, AUTH, monkeypatch):
        _create(client, AUTH)
        self._stub_provider(monkeypatch, '```json\n{"action": "query"}\n```')
        got = client.post("/api/v1/alerts/parse", headers=AUTH, json={"text": "我有哪些提醒"}).json()
        assert got["action"] == "query" and len(got["matches"]) == 1

    def test_parse_invalid_rule_is_200_with_reason(self, client, AUTH, monkeypatch):
        self._stub_provider(
            monkeypatch,
            '```json\n{"action":"create","ts_code":"600519.SH",'
            '"conditions":[{"metric":"rsi","op":"<=","value":30}]}\n```')
        got = client.post("/api/v1/alerts/parse", headers=AUTH, json={"text": "RSI 低于 30"}).json()
        assert got["ok"] is False and got["degraded"] is False and "不合法" in got["reason"]
