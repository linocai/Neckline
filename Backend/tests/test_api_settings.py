"""Settings security, notification preferences and device registration."""
import json
import pytest
from neckline import notify_kinds, settings_store


def test_provider_credentials_roundtrip_without_echo_and_drive_v4_pro(client, AUTH, api_env):
    from pathlib import Path
    from neckline.k10.providers import resolve_deepseek_v4_pro
    configuration = json.loads((Path(__file__).parents[1] / "neckline/config/k10-v1.4.json").read_text())
    connection = {"name": "deepseek", "baseUrl": "https://api.deepseek.com",
                  "model": "deepseek-v4-pro", "apiKey": "synthetic-write-only-key"}
    response = client.post("/api/v1/settings/providers", headers=AUTH, json=connection)
    assert response.status_code == 201
    assert response.json()["keySet"] is True
    assert connection["apiKey"] not in response.text
    assert client.post("/api/v1/settings/providers", headers=AUTH, json=connection).status_code == 409
    listed = client.get("/api/v1/settings/providers", headers=AUTH)
    assert connection["apiKey"] not in listed.text
    assert resolve_deepseek_v4_pro(configuration=configuration, task="analysis", db_path=api_env.db_path).state == "configured"
    # A partial update does not clear a previously supplied key.
    assert client.put("/api/v1/settings/providers/deepseek", headers=AUTH, json={"notes": "synthetic"}).status_code == 200
    assert settings_store.get_provider_record("deepseek", db_path=api_env.db_path).api_key == connection["apiKey"]
    assert client.put("/api/v1/settings/providers/deepseek", headers=AUTH, json={"enabled": False}).status_code == 200
    assert resolve_deepseek_v4_pro(configuration=configuration, task="analysis", db_path=api_env.db_path).state == "not_configured"
    assert client.delete("/api/v1/settings/providers/deepseek", headers=AUTH).status_code == 200
    assert client.delete("/api/v1/settings/providers/deepseek", headers=AUTH).status_code == 404


def test_update_provider_not_found_404(client, AUTH):
    r = client.put("/api/v1/settings/providers/ghost", headers=AUTH, json={"model": "x"})
    assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"


def test_tavily_key_write_only_roundtrip_and_clear(client, AUTH, api_env):
    secret = "tvly-secret-never-return"
    r = client.put("/api/v1/settings/tavily", headers=AUTH, json={"apiKey": secret})
    assert r.status_code == 200 and r.json() == {"keySet": True}
    settings = client.get("/api/v1/settings", headers=AUTH)
    assert settings.json()["tavily"] == {"keySet": True}
    assert secret not in settings.text and secret not in r.text
    assert settings_store.get_tavily_api_key(db_path=api_env.db_path) == secret

    cleared = client.delete("/api/v1/settings/tavily", headers=AUTH)
    assert cleared.status_code == 200 and cleared.json() == {"keySet": False}
    assert settings_store.get_tavily_api_key(db_path=api_env.db_path) is None


def test_tavily_whitespace_key_rejected(client, AUTH):
    r = client.put("/api/v1/settings/tavily", headers=AUTH, json={"apiKey": "   "})
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "invalid_tavily_key"


def test_put_push_toggles(client, AUTH):
    """两类通知各自有独立开关。"""
    kinds = {k: True for k in notify_kinds.ALL_KINDS}
    kinds[notify_kinds.ALL_KINDS[0]] = False
    r = client.put("/api/v1/settings/push", headers=AUTH, json={"kinds": kinds})
    assert r.status_code == 200
    got = {k["kind"]: k["enabled"] for k in client.get("/api/v1/settings", headers=AUTH).json()["push"]["kinds"]}
    assert got[notify_kinds.ALL_KINDS[0]] is False
    assert got[notify_kinds.ALL_KINDS[1]] is True


def test_put_push_missing_kind_422(client, AUTH):
    """必须给全每一个 kind(承 V1「六字段必填、防漏传静默重置」的同一条纪律),
    缺 kind → 422 而非静默补默认。"""
    kinds = {notify_kinds.ALL_KINDS[0]: True}
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


def test_register_device(client, AUTH, api_env):
    from neckline.api.stores import list_device_tokens

    assert client.post("/api/v1/devices", headers=AUTH, json={"token": "devtok1", "platform": "ios"}).json()["ok"]
    client.post("/api/v1/devices", headers=AUTH, json={"token": "devtok1", "platform": "ios"})  # 幂等
    client.post("/api/v1/devices", headers=AUTH, json={"token": "devtok2"})
    assert set(list_device_tokens(db_path=api_env.db_path)) == {"devtok1", "devtok2"}


def test_create_provider_rejects_duplicate_name(api_env):
    db = api_env.db_path
    settings_store.create_provider("custom", "https://x", "m1", db_path=db)
    with pytest.raises(ValueError):
        settings_store.create_provider("custom", "https://y", "m2", db_path=db)


def test_update_provider_missing_name_returns_none(api_env):
    assert settings_store.update_provider("ghost", model="x", db_path=api_env.db_path) is None


def test_delete_provider_missing_name_returns_false(api_env):
    assert settings_store.delete_provider("ghost", db_path=api_env.db_path) is False


def test_key_never_logged(api_env, caplog):
    import logging
    with caplog.at_level(logging.DEBUG):
        settings_store.create_provider("custom", "https://x", "test-model", api_key="sk-topsecret-999", db_path=api_env.db_path)
        settings_store.update_provider("custom", api_key="sk-topsecret-999-v2", db_path=api_env.db_path)
        settings_store.set_tavily_api_key("tvly-topsecret-999", db_path=api_env.db_path)
        settings_store.list_providers(db_path=api_env.db_path)
        settings_store.get_app_settings(db_path=api_env.db_path)
    assert "sk-topsecret-999" not in caplog.text
    assert "tvly-topsecret-999" not in caplog.text
