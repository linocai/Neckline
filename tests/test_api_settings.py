"""4A.5 设置端点 + settings_store 单测(plan 4A 验收:settings DB 存取,key 不回传明文)。
V2-② 起 LLM 部分改为 Provider 注册表自填制(plan §五 V2-②/§3.10-B),取代 V1 的
`PUT /settings/llm` 单供应商枚举——本文件的 Provider 相关用例已随之改写,其余
(push/intel-boards/设备注册)不变。**🔴 高危区:LLM key 服务端存取。**"""

from __future__ import annotations

import json

import pytest

from neckline import notify_kinds, settings_store
from neckline.llm.factory import get_provider
from neckline.llm.router import TASK_BASKET_REASON, TASK_DRIVER_SEARCH
from tests.conftest import write_flat_parquet


# —— 端点 ————————————————————————————————————————————————————————————

def test_settings_default(client, AUTH):
    r = client.get("/api/v1/settings", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["providers"] == []
    assert body["routes"] == {}
    # V2-⑪:push 从「六个具名布尔」换成「按 kind 的开关清单」(三级 × N kind,D5)。
    kinds = body["push"]["kinds"]
    assert [k["kind"] for k in kinds] == list(notify_kinds.ALL_KINDS)
    assert all(k["enabled"] is True for k in kinds)          # 全部默认开
    assert {k["level"] for k in kinds} <= set(notify_kinds.LEVELS)
    assert all(k["label"] for k in kinds)                    # 人读名服务端给,双端不各抄一份


def test_create_provider_key_not_leaked_and_runtime_effective(client, AUTH, api_env):
    r = client.post("/api/v1/settings/providers", headers=AUTH, json={
        "name": "glm", "baseUrl": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-5.2", "apiKey": "sk-secret-abc", "hasWebSearch": True, "searchEngine": "search_pro",
    })
    assert r.status_code == 201
    created = r.json()
    assert created["name"] == "glm" and created["keySet"] is True
    assert "sk-secret" not in json.dumps(created)

    body = client.get("/api/v1/settings", headers=AUTH).json()
    assert body["providers"] == [
        {"name": "glm", "model": "glm-5.2", "hasWebSearch": True, "keySet": True, "enabled": True}
    ]
    assert "sk-secret" not in json.dumps(body)
    assert "sk-secret" not in client.get("/api/v1/settings/providers", headers=AUTH).text

    # get_provider 现读 DB 生效(运行时,不重启);无显式路由时缺省 provider 为 None,
    # 但检索类任务(TASK_DRIVER_SEARCH)缺路由会挑这个 has_web_search=True 的启用行。
    p = get_provider(TASK_DRIVER_SEARCH, db_path=api_env.db_path)
    assert p is not None and p.name == "glm" and p.model == "glm-5.2"


def test_create_provider_duplicate_name_409(client, AUTH):
    body = {"name": "glm", "baseUrl": "https://x", "model": "m"}
    assert client.post("/api/v1/settings/providers", headers=AUTH, json=body).status_code == 201
    r = client.post("/api/v1/settings/providers", headers=AUTH, json=body)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "already_exists"


def test_create_provider_missing_required_field_422(client, AUTH):
    # schema 要求 name/baseUrl/model 均非空(min_length=1)
    assert client.post("/api/v1/settings/providers", headers=AUTH, json={"name": "", "baseUrl": "x", "model": "m"}).status_code == 422


def test_update_provider_partial_only_touches_named_fields(client, AUTH, api_env):
    client.post("/api/v1/settings/providers", headers=AUTH, json={
        "name": "deepseek", "baseUrl": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-chat", "apiKey": "k1", "hasWebSearch": False,
    })
    r = client.put("/api/v1/settings/providers/deepseek", headers=AUTH, json={"model": "deepseek-reasoner"})
    assert r.status_code == 200
    got = r.json()
    assert got["model"] == "deepseek-reasoner"
    assert got["baseUrl"] == "https://api.deepseek.com/chat/completions"  # 未传的字段不变
    assert got["keySet"] is True  # 没碰 apiKey,key 仍在

    # 显式传空串清空 apiKey(视为清除,同既有 `_clean()` 纪律)
    r2 = client.put("/api/v1/settings/providers/deepseek", headers=AUTH, json={"apiKey": ""})
    assert r2.status_code == 200 and r2.json()["keySet"] is False

    # 立"deepseek"为默认 provider 后,无 key 应让 get_provider() 整体判不可用
    client.put("/api/v1/settings/llm-routes", headers=AUTH, json={"routes": {}, "defaultProvider": "deepseek"})
    assert get_provider(db_path=api_env.db_path) is None


def test_update_provider_not_found_404(client, AUTH):
    r = client.put("/api/v1/settings/providers/ghost", headers=AUTH, json={"model": "x"})
    assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"


def test_delete_provider(client, AUTH):
    client.post("/api/v1/settings/providers", headers=AUTH, json={"name": "temp", "baseUrl": "https://x", "model": "m"})
    assert client.delete("/api/v1/settings/providers/temp", headers=AUTH).status_code == 200
    assert client.delete("/api/v1/settings/providers/temp", headers=AUTH).status_code == 404
    assert client.get("/api/v1/settings/providers", headers=AUTH).json()["items"] == []


def test_llm_routes_roundtrip_and_default_fallback(client, AUTH, api_env):
    client.post("/api/v1/settings/providers", headers=AUTH, json={
        "name": "deepseek", "baseUrl": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-chat", "apiKey": "k1",
    })
    r = client.put("/api/v1/settings/llm-routes", headers=AUTH,
                    json={"routes": {"basket_reason": "deepseek"}, "defaultProvider": "deepseek"})
    assert r.status_code == 200
    assert r.json() == {"routes": {"basket_reason": "deepseek"}, "defaultProvider": "deepseek"}
    assert client.get("/api/v1/settings/llm-routes", headers=AUTH).json() == r.json()

    p = get_provider(TASK_BASKET_REASON, db_path=api_env.db_path)
    assert p is not None and p.name == "deepseek"
    # 未在 routes 里的其它任务缺路由回退 defaultProvider(同一个 deepseek)
    p2 = get_provider("some_future_task", db_path=api_env.db_path)
    assert p2 is not None and p2.name == "deepseek"


def test_llm_routes_unknown_task_422(client, AUTH):
    r = client.put("/api/v1/settings/llm-routes", headers=AUTH,
                    json={"routes": {"not_a_real_task": "x"}, "defaultProvider": None})
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "invalid_task"


def test_put_push_toggles(client, AUTH):
    """V2-⑪:开关按 **kind** 配(D5)。关掉 `d5exit` 不连坐同为「重要不紧急」级的
    `holding_alert` —— 这正是不按 category 配的理由。"""
    kinds = {k: True for k in notify_kinds.ALL_KINDS}
    kinds[notify_kinds.KIND_REPORT_READY] = False
    kinds[notify_kinds.KIND_D5EXIT] = False
    r = client.put("/api/v1/settings/push", headers=AUTH, json={"kinds": kinds})
    assert r.status_code == 200
    got = {k["kind"]: k["enabled"] for k in client.get("/api/v1/settings", headers=AUTH).json()["push"]["kinds"]}
    assert got[notify_kinds.KIND_REPORT_READY] is False
    assert got[notify_kinds.KIND_D5EXIT] is False
    # 同级的其它 kind 一个都没被连坐
    assert got[notify_kinds.KIND_HOLDING_ALERT] is True
    assert got[notify_kinds.KIND_PRECALL] is True
    assert got[notify_kinds.KIND_MARKET_SHOCK] is True


def test_put_push_missing_kind_422(client, AUTH):
    """必须给全每一个 kind(承 V1「六字段必填、防漏传静默重置」的同一条纪律),
    缺 kind → 422 而非静默补默认。"""
    kinds = {k: True for k in notify_kinds.ALL_KINDS if k != notify_kinds.KIND_CUSTOM_ALERT}
    r = client.put("/api/v1/settings/push", headers=AUTH, json={"kinds": kinds})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "invalid_push_kinds"


def test_put_push_unknown_kind_422(client, AUTH):
    """未登记 kind → 422(白名单不开后门;新增 kind 须用户拍板)。"""
    kinds = {k: True for k in notify_kinds.ALL_KINDS}
    kinds["made_up_kind"] = True
    r = client.put("/api/v1/settings/push", headers=AUTH, json={"kinds": kinds})
    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "invalid_push_kinds"


def test_put_llm_routes_does_not_reset_push(client, AUTH):
    """`set_llm_routes` 只碰 llm_task_routes/llm_default_provider 两列,不连带重置
    push 开关(各 setter 只 UPDATE 自己的列,同 `_ensure_row` 两步式纪律)。"""
    all_off = {k: False for k in notify_kinds.ALL_KINDS}
    client.put("/api/v1/settings/push", headers=AUTH, json={"kinds": all_off})
    client.put("/api/v1/settings/llm-routes", headers=AUTH, json={"routes": {}, "defaultProvider": "x"})
    got = {k["kind"]: k["enabled"] for k in client.get("/api/v1/settings", headers=AUTH).json()["push"]["kinds"]}
    assert got == all_off


def test_register_device(client, AUTH, api_env):
    from neckline.api.stores import list_device_tokens

    assert client.post("/api/v1/devices", headers=AUTH, json={"token": "devtok1", "platform": "ios"}).json()["ok"]
    client.post("/api/v1/devices", headers=AUTH, json={"token": "devtok1", "platform": "ios"})  # 幂等
    client.post("/api/v1/devices", headers=AUTH, json={"token": "devtok2"})
    assert set(list_device_tokens(db_path=api_env.db_path)) == {"devtok1", "devtok2"}


# —— settings_store 直接单测(Provider 注册表存取语义 / 降级,V2-②)—————————————

def test_empty_key_treated_as_unset(api_env):
    db = api_env.db_path
    settings_store.create_provider("glm", "https://x", "glm-5.2", api_key="realkey", db_path=db)
    settings_store.set_llm_routes({}, "glm", db_path=db)  # 立"glm"为默认 provider,便于下面用 get_provider() 观测
    assert settings_store.get_provider_record("glm", db_path=db).api_key == "realkey"
    assert get_provider(db_path=db) is not None
    # 填空 key → 视为清除(降级),不留一个空 key 去乱调
    settings_store.update_provider("glm", api_key="   ", db_path=db)
    assert settings_store.get_provider_record("glm", db_path=db).api_key is None
    assert get_provider(db_path=db) is None


def test_create_provider_rejects_duplicate_name(api_env):
    db = api_env.db_path
    settings_store.create_provider("glm", "https://x", "m1", db_path=db)
    with pytest.raises(ValueError):
        settings_store.create_provider("glm", "https://y", "m2", db_path=db)


def test_update_provider_missing_name_returns_none(api_env):
    assert settings_store.update_provider("ghost", model="x", db_path=api_env.db_path) is None


def test_delete_provider_missing_name_returns_false(api_env):
    assert settings_store.delete_provider("ghost", db_path=api_env.db_path) is False


def test_set_llm_routes_rejects_unknown_task(api_env):
    with pytest.raises(ValueError):
        settings_store.set_llm_routes({"not_a_real_task": "x"}, None, db_path=api_env.db_path)


def test_get_llm_routes_default_empty(api_env):
    assert settings_store.get_llm_routes(db_path=api_env.db_path) == ({}, None)


def test_key_never_logged(api_env, caplog):
    import logging
    with caplog.at_level(logging.DEBUG):
        settings_store.create_provider("glm", "https://x", "glm-5.2", api_key="sk-topsecret-999", db_path=api_env.db_path)
        settings_store.update_provider("glm", api_key="sk-topsecret-999-v2", db_path=api_env.db_path)
        settings_store.list_providers(db_path=api_env.db_path)
        settings_store.get_app_settings(db_path=api_env.db_path)
    assert "sk-topsecret-999" not in caplog.text
