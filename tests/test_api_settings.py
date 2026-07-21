"""4A.5 设置端点 + settings_store 单测(plan 4A 验收:settings DB 存取,key 不回传明文;
`PUT /settings/llm` 后 `get_provider()` 现读 DB 生效)。**🔴 高危区:LLM key 服务端存取。**"""

from __future__ import annotations

import json

import pytest

from neckline import settings_store
from neckline.llm.factory import get_provider


# —— 端点 ————————————————————————————————————————————————————————————

def test_settings_default(client, AUTH):
    r = client.get("/api/v1/settings", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["llmProvider"] is None
    assert body["llmKeySet"] is False
    assert body["push"] == {"report": True, "retreatBrake": True, "precall": True, "d5exit": True}


def test_put_llm_key_not_leaked_and_provider_runtime_effective(client, AUTH, api_env):
    r = client.put("/api/v1/settings/llm", headers=AUTH, json={"provider": "glm", "apiKey": "sk-secret-abc"})
    assert r.status_code == 200 and r.json()["ok"] is True

    body = client.get("/api/v1/settings", headers=AUTH).json()
    assert body["llmProvider"] == "glm"
    assert body["llmKeySet"] is True
    # 明文 key 绝不出现在响应里
    assert "sk-secret" not in json.dumps(body)

    # get_provider 现读 DB 覆盖生效(运行时,不重启)
    p = get_provider(db_path=api_env.db_path)
    assert p is not None and p.name == "glm"


def test_put_llm_switch_provider(client, AUTH, api_env):
    client.put("/api/v1/settings/llm", headers=AUTH, json={"provider": "glm", "apiKey": "k1"})
    client.put("/api/v1/settings/llm", headers=AUTH, json={"provider": "kimi", "apiKey": "k2"})
    assert client.get("/api/v1/settings", headers=AUTH).json()["llmProvider"] == "kimi"
    p = get_provider(db_path=api_env.db_path)
    assert p is not None and p.name == "kimi"


def test_put_llm_invalid_provider_422(client, AUTH):
    # schema Literal 拒收未知供应商(第一重「永不乱调」保险)
    assert client.put("/api/v1/settings/llm", headers=AUTH, json={"provider": "evil", "apiKey": "x"}).status_code == 422


def test_put_push_toggles(client, AUTH):
    """v1.1-G.1:契约扩至四字段(报告/退潮/盘前校准/D5 退出)。"""
    r = client.put("/api/v1/settings/push", headers=AUTH,
                   json={"report": False, "retreatBrake": True, "precall": False, "d5exit": True})
    assert r.status_code == 200
    push = client.get("/api/v1/settings", headers=AUTH).json()["push"]
    assert push == {"report": False, "retreatBrake": True, "precall": False, "d5exit": True}


def test_put_push_missing_field_422(client, AUTH):
    """四字段均必填(与 report/retreatBrake 同款无默认值风格),缺字段 → 422 而非静默补默认。"""
    r = client.put("/api/v1/settings/push", headers=AUTH, json={"report": True, "retreatBrake": True})
    assert r.status_code == 422


def test_put_llm_does_not_reset_push(client, AUTH):
    """set_llm 只碰 llm 列,不连带重置 push 开关(各 setter 只 UPDATE 自己的列)。"""
    client.put("/api/v1/settings/push", headers=AUTH,
              json={"report": False, "retreatBrake": False, "precall": False, "d5exit": False})
    client.put("/api/v1/settings/llm", headers=AUTH, json={"provider": "glm", "apiKey": "k"})
    push = client.get("/api/v1/settings", headers=AUTH).json()["push"]
    assert push == {"report": False, "retreatBrake": False, "precall": False, "d5exit": False}


def test_register_device(client, AUTH, api_env):
    from neckline.api.stores import list_device_tokens

    assert client.post("/api/v1/devices", headers=AUTH, json={"token": "devtok1", "platform": "ios"}).json()["ok"]
    client.post("/api/v1/devices", headers=AUTH, json={"token": "devtok1", "platform": "ios"})  # 幂等
    client.post("/api/v1/devices", headers=AUTH, json={"token": "devtok2"})
    assert set(list_device_tokens(db_path=api_env.db_path)) == {"devtok1", "devtok2"}


# —— settings_store 直接单测(存取语义 / 降级)————————————————————————————————

def test_empty_key_treated_as_unset(api_env):
    db = api_env.db_path
    settings_store.set_llm("glm", "realkey", db_path=db)
    assert settings_store.get_app_settings(db_path=db).llm_key_set is True
    # 填空 key → 视为清除(降级),不留一个空 key 去乱调
    settings_store.set_llm("glm", "   ", db_path=db)
    assert settings_store.get_app_settings(db_path=db).llm_key_set is False
    assert get_provider(db_path=db) is None


def test_resolve_llm_db_overrides_env(api_env):
    import dataclasses
    db = api_env.db_path
    env_settings = dataclasses.replace(api_env, llm_provider="kimi", llm_api_key="env-key")
    # DB 未设 → 用 .env 兜底
    assert settings_store.resolve_llm(default_settings=env_settings, db_path=db) == ("kimi", "env-key")
    # DB 设了 → DB 覆盖 .env
    settings_store.set_llm("glm", "db-key", db_path=db)
    assert settings_store.resolve_llm(default_settings=env_settings, db_path=db) == ("glm", "db-key")


def test_set_llm_rejects_unknown_provider(api_env):
    with pytest.raises(ValueError):
        settings_store.set_llm("bogus", "k", db_path=api_env.db_path)


def test_key_never_logged(api_env, caplog):
    import logging
    with caplog.at_level(logging.DEBUG):
        settings_store.set_llm("glm", "sk-topsecret-999", db_path=api_env.db_path)
        settings_store.resolve_llm(db_path=api_env.db_path)
        settings_store.get_app_settings(db_path=api_env.db_path)
    assert "sk-topsecret-999" not in caplog.text
