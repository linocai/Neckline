"""两类现行 APNs 推送的编排、开关与日期契约。"""

from __future__ import annotations

import dataclasses
import json

import pytest

from neckline import notify_kinds
from neckline.api import notify
from neckline.api.stores import upsert_device
from neckline.config import Settings
from neckline.push import apns
from neckline.settings_store import get_push_kinds, set_push_kinds


@pytest.fixture
def apns_configured(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    p8 = tmp_path / "AuthKey.p8"
    p8.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    configured = dataclasses.replace(
        Settings(tushare_token=None),
        apns_key_id="K", apns_team_id="T", apns_bundle_id="top.linotsai.neckline",
        apns_key_path=str(p8))
    monkeypatch.setattr(apns, "settings", configured)
    apns.reset_jwt_cache()
    yield configured
    apns.reset_jwt_cache()


def _ok_transport(url, headers, body):
    return apns.PushResult(ok=True, status=200, reason="ok")


def _set_kind(db, kind: str, enabled: bool) -> None:
    values = get_push_kinds(db_path=db)
    values[kind] = enabled
    set_push_kinds(values, db_path=db)


def test_public_entrypoints_are_exactly_current():
    assert set(notify.__all__) == {
        "NotifyOutcome", "push_event", "push_report_ready",
        "push_checklist_summary", "push_previous_report_not_run",
    }


def test_unknown_kind_is_refused(api_env, apns_configured):
    upsert_device("tok", db_path=api_env.db_path)
    with pytest.raises(ValueError):
        notify.push_event("retreat", "t", "b", db_path=api_env.db_path,
                          transport=_ok_transport)


def test_report_respects_kind_switch(api_env, apns_configured):
    upsert_device("tok", db_path=api_env.db_path)
    _set_kind(api_env.db_path, notify_kinds.KIND_REPORT_READY, False)
    out = notify.push_report_ready("2026-08-16", db_path=api_env.db_path,
                                   transport=_ok_transport)
    assert out.sent == 0 and out.skipped_reason == "kind_off:report_ready"


def test_sunday_report_keeps_publication_and_market_dates_separate(api_env, apns_configured):
    upsert_device("tok", db_path=api_env.db_path)
    captured = {}

    def transport(url, headers, body):
        captured.update(json.loads(body))
        return apns.PushResult(ok=True, status=200, reason="ok")

    out = notify.push_report_ready(
        "2026-08-16", data_date_disp="2026-08-14", db_path=api_env.db_path,
        transport=transport)
    assert out.sent == 1
    assert captured["reportDate"] == "2026-08-16"
    assert captured["tradeDate"] == "2026-08-14"
    assert "行情截至 2026-08-14" in captured["aps"]["alert"]["body"]


def test_checklist_summary_uses_precall(api_env, apns_configured):
    upsert_device("tok", db_path=api_env.db_path)
    captured = {}

    def transport(url, headers, body):
        captured.update(json.loads(body))
        return apns.PushResult(ok=True, status=200, reason="ok")

    out = notify.push_checklist_summary(
        {"rejected": 1, "pendingOpen": 4, "noQuote": 0, "noPlaybook": 0,
         "dataQuality": "ok"}, db_path=api_env.db_path, transport=transport)
    assert out.sent == 1 and out.kind == notify_kinds.KIND_PRECALL
    body = captured["aps"]["alert"]["body"]
    assert "1 只已触发放弃" in body and "4 只待开盘后观察" in body


def test_previous_report_not_run_is_explicit(api_env, apns_configured):
    upsert_device("tok", db_path=api_env.db_path)
    captured = {}

    def transport(url, headers, body):
        captured.update(json.loads(body))
        return apns.PushResult(ok=True, status=200, reason="ok")

    out = notify.push_previous_report_not_run(
        "20260821", db_path=api_env.db_path, transport=transport)
    assert out.sent == 1 and captured["reportState"] == "not_run"
    assert "没有完整跑成" in captured["aps"]["alert"]["body"]


def test_no_device_and_no_config_are_safe_empty(api_env, apns_configured, monkeypatch):
    out = notify.push_report_ready("2026-08-16", db_path=api_env.db_path,
                                   transport=_ok_transport)
    assert out.skipped_reason == "no_devices"
    upsert_device("tok", db_path=api_env.db_path)
    monkeypatch.setattr(apns, "settings",
                        Settings(tushare_token=None))
    out = notify.push_report_ready("2026-08-16", db_path=api_env.db_path,
                                   transport=_ok_transport)
    assert out.skipped_reason == "no_apns_config"


def test_only_permanently_invalid_apns_tokens_are_removed(api_env, apns_configured):
    from neckline.api.stores import list_device_tokens

    upsert_device("permanent", db_path=api_env.db_path)
    upsert_device("transient", db_path=api_env.db_path)

    def transport(url, headers, body):
        token = url.rsplit("/", 1)[-1]
        if token == "permanent":
            return apns.PushResult(ok=False, status=410, reason="Unregistered")
        return apns.PushResult(ok=False, status=503, reason="ServiceUnavailable")

    notify.push_report_ready("2026-08-16", db_path=api_env.db_path, transport=transport)
    assert list_device_tokens(db_path=api_env.db_path) == ["transient"]


def test_partial_retry_targets_only_the_failed_device(api_env, apns_configured):
    from neckline.dedup import device_delivery_key

    upsert_device("accepted", db_path=api_env.db_path)
    upsert_device("retry", db_path=api_env.db_path)
    calls: list[str] = []

    def first_transport(url, headers, body):
        token = url.rsplit("/", 1)[-1]
        calls.append(token)
        if token == "retry":
            return apns.PushResult(ok=False, status=503, reason="ServiceUnavailable")
        assert len(headers["apns-collapse-id"]) == 64
        return apns.PushResult(ok=True, status=200, reason="ok")

    first = notify.push_checklist_summary(
        {"rejected": 0, "pendingOpen": 1}, db_path=api_env.db_path,
        transport=first_transport, delivery_id="20260821:auction:checklist_tick",
    )
    assert first.sent == 1 and first.failed == 1 and not first.delivery_complete
    assert first.delivered_device_keys == (device_delivery_key("accepted"),)

    second = notify.push_checklist_summary(
        {"rejected": 0, "pendingOpen": 1}, db_path=api_env.db_path,
        transport=first_transport, skip_device_keys=first.delivered_device_keys,
        delivery_id="20260821:auction:checklist_tick",
    )
    assert second.sent == 0 and second.failed == 1
    assert calls.count("accepted") == 1 and calls.count("retry") == 2
