"""4A.1 鉴权 + 应用骨架单测(plan 4A 验收:health 免鉴权 200 / 无 token 端点 401)。"""

from __future__ import annotations

import dataclasses

import pytest

PROTECTED_GET = [
    "/api/v1/settings", "/api/v1/settings/providers",
    "/api/v1/k10/scans/latest?window=evening", "/api/v1/k10/publications",
    "/api/v1/k10/company-windows", "/api/v1/k10/results",
]


def test_health_no_auth(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.parametrize("path", PROTECTED_GET)
def test_protected_get_requires_token(client, path):
    assert client.get(path).status_code == 401                      # 无 token
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401  # 错 token


def test_protected_post_requires_token(client):
    assert client.post("/api/v1/devices", json={"token": "x"}).status_code == 401
    assert client.post("/api/v1/settings/providers", json={"name": "x"}).status_code == 401
    assert client.post("/api/v1/k10/company-windows/window-1/selection").status_code == 401
    assert client.post("/api/v1/k10/operations/pause").status_code == 401


def test_protected_put_requires_token(client):
    assert client.put("/api/v1/settings/push", json={"report": True, "retreatBrake": True}).status_code == 401
    assert client.put("/api/v1/settings/providers/custom", json={}).status_code == 401
    assert client.post("/api/v1/k10/jobs/job-1/retry", json={}).status_code == 401


def test_protected_delete_requires_token(client):
    assert client.delete("/api/v1/settings/providers/custom").status_code == 401
    assert client.delete("/api/v1/settings/tavily").status_code == 401


def test_valid_token_passes(client, AUTH):
    assert client.get("/api/v1/settings", headers=AUTH).status_code == 200


def test_bearer_compare_is_exact(client, AUTH):
    tok = AUTH["Authorization"].split(" ", 1)[1]
    assert client.get("/api/v1/settings", headers={"Authorization": f"Bearer {tok}x"}).status_code == 401
    assert client.get("/api/v1/settings", headers={"Authorization": tok}).status_code == 401  # 缺 "Bearer "


def test_fail_fast_short_token(api_settings, monkeypatch):
    """startup fail-fast:API_TOKEN len<16 → require_api_token_ready 抛 RuntimeError。"""
    import neckline.api.deps as deps_mod

    short = dataclasses.replace(api_settings, api_token="short")
    monkeypatch.setattr(deps_mod, "settings", short)
    with pytest.raises(RuntimeError):
        deps_mod.require_api_token_ready()


def test_fail_fast_missing_token(api_settings, monkeypatch):
    import neckline.api.deps as deps_mod

    missing = dataclasses.replace(api_settings, api_token=None)
    monkeypatch.setattr(deps_mod, "settings", missing)
    with pytest.raises(RuntimeError):
        deps_mod.require_api_token_ready()


def test_fail_fast_ok_token(api_settings, monkeypatch):
    import neckline.api.deps as deps_mod

    monkeypatch.setattr(deps_mod, "settings", api_settings)
    deps_mod.require_api_token_ready()  # 不抛 = 通过
