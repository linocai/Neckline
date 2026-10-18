"""The rewritten API serves only V4, on a temporary morning database."""
from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from neckline.api import deps
from neckline.api.app import RELEASE_SET, VERSION, app
from neckline.morning import db

TOKEN = "a" * 16
WRONG = "b" * 16
READ_KEY = "c" * 24
LEGACY = "/api/" + "v1"


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "v4.db"
    db.init_schema(path)
    monkeypatch.setenv("NECKLINE_V4_DB_PATH", str(path))
    monkeypatch.setenv("NECKLINE_READ_KEY", READ_KEY)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(deps, "settings", dataclasses.replace(deps.settings, api_token=TOKEN))
    with TestClient(app) as test_client:
        yield test_client


def test_startup_refuses_a_missing_token(monkeypatch):
    monkeypatch.setattr(deps, "settings", dataclasses.replace(deps.settings, api_token=None))
    with pytest.raises(RuntimeError, match="API_TOKEN"):
        with TestClient(app):
            pass


def test_health_is_public_and_names_the_release(client):
    response = client.get("/api/v4/health")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == VERSION
    assert body["releaseSet"] == RELEASE_SET
    assert body["db"] == "ready"
    assert body["latestReport"] is None
    assert TOKEN not in response.text


@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/api/v4/reports/latest", None),
    ("GET", "/api/v4/reports", None),
    ("GET", "/api/v4/documents/x", None),
    ("POST", "/api/v4/devices", {"token": "ab" * 32, "platform": "ios"}),
])
def test_protected_routes_reject_missing_and_wrong_tokens(client, method, path, payload):
    kwargs = {} if payload is None else {"json": payload}
    missing = client.request(method, path, **kwargs)
    wrong = client.request(method, path, headers={"Authorization": f"Bearer {WRONG}"}, **kwargs)
    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_legacy_prefix_is_gone(client):
    assert client.get(LEGACY + "/health").status_code == 404
    assert client.post(LEGACY + "/devices").status_code == 404
    assert client.get(LEGACY + "/reports/latest").status_code == 404


def test_read_page_hides_a_wrong_key(client):
    assert client.get("/read/" + "d" * 24).status_code == 404
    assert client.get("/read/short").status_code == 404
