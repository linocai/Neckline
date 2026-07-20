"""4B.5 APNs 推送编排单测(plan §2.4 拍板:**只推两类** + 受开关控制 + 遍历 devices)。"""

from __future__ import annotations

import dataclasses

import pytest

from neckline.api import notify
from neckline.api.stores import upsert_device
from neckline.config import Settings
from neckline.push import apns
from neckline.settings_store import set_push


@pytest.fixture
def apns_configured(tmp_path, monkeypatch):
    """把 apns.settings 换成「APNs 配置齐全」的隔离 Settings(has_apns_config=True + 可读
    的临时 EC .p8,故 get_jwt 能出真 token)。真发走注入 transport,不连 Apple。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode()
    p8 = tmp_path / "AuthKey.p8"
    p8.write_text(priv, encoding="utf-8")
    s = dataclasses.replace(
        Settings(tushare_token=None, llm_provider=None, llm_api_key=None),
        apns_key_id="K", apns_team_id="T", apns_bundle_id="top.linotsai.neckline", apns_key_path=str(p8),
    )
    monkeypatch.setattr(apns, "settings", s)
    apns.reset_jwt_cache()
    yield s
    apns.reset_jwt_cache()


def _ok_transport(url, headers, body):
    return apns.PushResult(ok=True, status=200, reason="ok")


def test_only_two_push_entrypoints():
    """「只推两类」的结构保证:notify 模块只暴露两个推送入口,不给第三类事件留路径。"""
    assert set(notify.__all__) == {"NotifyOutcome", "push_report_ready", "push_retreat_brake"}


def test_report_push_gated_off(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    set_push(report=False, retreat=True, db_path=db)
    out = notify.push_report_ready("2026-07-17", db_path=db, transport=_ok_transport)
    assert out.sent == 0 and out.skipped_reason == "push_report_off"


def test_report_push_sends_when_on(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    upsert_device("tok2", db_path=db)
    set_push(report=True, retreat=True, db_path=db)
    out = notify.push_report_ready("2026-07-17", db_path=db, transport=_ok_transport)
    assert out.sent == 2 and out.failed == 0


def test_retreat_push_gated_off(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    set_push(report=True, retreat=False, db_path=db)
    out = notify.push_retreat_brake("炸板率飙升", db_path=db, transport=_ok_transport)
    assert out.sent == 0 and out.skipped_reason == "push_retreat_off"


def test_retreat_push_sends_when_on(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    out = notify.push_retreat_brake("炸板率飙升", db_path=db, transport=_ok_transport)
    assert out.sent == 1


def test_no_devices_skips(api_env, apns_configured):
    out = notify.push_report_ready("2026-07-17", db_path=api_env.db_path, transport=_ok_transport)
    assert out.sent == 0 and out.skipped_reason == "no_devices"


def test_no_apns_config_skips(api_env):
    # api_env 的 apns.settings 无配置 → has_apns_config False → 跳过
    upsert_device("tok1", db_path=api_env.db_path)
    out = notify.push_report_ready("2026-07-17", db_path=api_env.db_path, transport=_ok_transport)
    assert out.skipped_reason == "no_apns_config"


def test_partial_failure_counts(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("good", db_path=db)
    upsert_device("bad", db_path=db)

    def flaky(url, headers, body):
        if url.endswith("bad"):
            return apns.PushResult(ok=False, status=410, reason="Unregistered")
        return apns.PushResult(ok=True, status=200, reason="ok")

    out = notify.push_report_ready("2026-07-17", db_path=db, transport=flaky)
    assert out.sent == 1 and out.failed == 1          # 单设备失败不拖累其它
