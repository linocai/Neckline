"""4B.5 APNs 层单测(plan 4B 验收:APNs 层单测过,真推留 4E)。JWT ES256 用临时 EC
P-256 key 验签(不依赖真 .p8);send_push 注入假 transport(不真连 Apple)。"""

from __future__ import annotations

import dataclasses

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from neckline.config import Settings
from neckline.push import apns


@pytest.fixture
def ec_key_pem():
    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, pub


def _apns_settings(tmp_path, key_pem, **over) -> Settings:
    p8 = tmp_path / "AuthKey.p8"
    p8.write_text(key_pem, encoding="utf-8")
    kw = dict(
        apns_key_id="Q963AP3VY8", apns_team_id="HX73DFL88G",
        apns_bundle_id="top.linotsai.neckline", apns_key_path=str(p8), apns_use_sandbox=True,
    )
    kw.update(over)  # 覆盖(避免与显式 kwarg 重复传参)
    return dataclasses.replace(Settings(tushare_token=None, llm_provider=None, llm_api_key=None), **kw)


# —— JWT 签名 ————————————————————————————————————————————————————————

def test_build_jwt_es256_verifiable(ec_key_pem):
    priv, pub = ec_key_pem
    token = apns.build_jwt(key_pem=priv, key_id="Q963AP3VY8", team_id="HX73DFL88G", iat=1000)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256" and header["kid"] == "Q963AP3VY8"
    payload = jwt.decode(token, pub, algorithms=["ES256"])
    assert payload["iss"] == "HX73DFL88G" and payload["iat"] == 1000


def test_get_jwt_none_without_config(monkeypatch):
    monkeypatch.setattr(apns, "settings", Settings(tushare_token=None, llm_provider=None, llm_api_key=None))
    apns.reset_jwt_cache()
    assert apns.get_jwt() is None


def test_get_jwt_with_config(tmp_path, ec_key_pem, monkeypatch):
    priv, pub = ec_key_pem
    monkeypatch.setattr(apns, "settings", _apns_settings(tmp_path, priv))
    apns.reset_jwt_cache()
    token = apns.get_jwt(now=2000)
    assert token is not None
    payload = jwt.decode(token, pub, algorithms=["ES256"])
    assert payload["iss"] == "HX73DFL88G"
    # 缓存:同 kid、未过期 → 复用同一 token
    assert apns.get_jwt(now=2100) == token
    # 过期(>50min)→ 重签
    assert apns.get_jwt(now=2000 + 51 * 60) != token
    apns.reset_jwt_cache()


def test_get_jwt_missing_p8_file(tmp_path, ec_key_pem, monkeypatch):
    priv, _ = ec_key_pem
    s = _apns_settings(tmp_path, priv, apns_key_path=str(tmp_path / "nonexistent.p8"))
    monkeypatch.setattr(apns, "settings", s)
    apns.reset_jwt_cache()
    assert apns.get_jwt() is None                     # 读 .p8 失败 → None(不抛)


# —— payload / send_push ——————————————————————————————————————————————

def test_build_payload():
    p = apns.build_payload("标题", "正文", category=apns.CATEGORY_IMMEDIATE, custom={"kind": "retreat"})
    assert p["aps"]["alert"] == {"title": "标题", "body": "正文"}
    assert p["aps"]["category"] == "NKIMMEDIATE"    # V2-⑪:三级 category
    assert p["kind"] == "retreat"


def test_send_push_success_injected_transport(tmp_path, ec_key_pem, monkeypatch):
    priv, _ = ec_key_pem
    monkeypatch.setattr(apns, "settings", _apns_settings(tmp_path, priv))
    apns.reset_jwt_cache()
    captured = {}

    def fake_transport(url, headers, body):
        captured["url"] = url
        captured["topic"] = headers["apns-topic"]
        captured["auth"] = headers["authorization"]
        return apns.PushResult(ok=True, status=200, reason="ok", apns_id="abc")

    res = apns.send_push("devtoken123", "标题", "正文", category=apns.CATEGORY_DIGEST, transport=fake_transport)
    assert res.ok and res.status == 200
    assert captured["url"].endswith("/3/device/devtoken123")
    assert captured["topic"] == "top.linotsai.neckline"      # topic = 新 Bundle ID
    assert captured["auth"].startswith("bearer ")
    apns.reset_jwt_cache()


def test_send_push_sandbox_gateway(tmp_path, ec_key_pem, monkeypatch):
    priv, _ = ec_key_pem
    monkeypatch.setattr(apns, "settings", _apns_settings(tmp_path, priv, apns_use_sandbox=True))
    apns.reset_jwt_cache()
    captured = {}

    def fake_transport(url, headers, body):
        captured["url"] = url
        return apns.PushResult(ok=True, status=200, reason="ok")

    apns.send_push("t", "a", "b", transport=fake_transport)
    assert "sandbox" in captured["url"]
    apns.reset_jwt_cache()


def test_send_push_no_config_graceful(monkeypatch):
    monkeypatch.setattr(apns, "settings", Settings(tushare_token=None, llm_provider=None, llm_api_key=None))
    apns.reset_jwt_cache()
    res = apns.send_push("t", "a", "b")               # 无凭证 + 无注入 jwt → ok=False,不抛
    assert res.ok is False and "JWT" in res.reason


def test_send_push_failure_reason(tmp_path, ec_key_pem, monkeypatch):
    priv, _ = ec_key_pem
    monkeypatch.setattr(apns, "settings", _apns_settings(tmp_path, priv))
    apns.reset_jwt_cache()

    def fake_transport(url, headers, body):
        return apns.PushResult(ok=False, status=410, reason="Unregistered")

    res = apns.send_push("stale", "a", "b", transport=fake_transport)
    assert res.ok is False and res.status == 410 and res.reason == "Unregistered"
    apns.reset_jwt_cache()
