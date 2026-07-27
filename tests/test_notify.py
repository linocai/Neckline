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


def test_push_whitelist_is_exactly_six():
    """推送白名单结构守护(§2.4 = 六类,各独立入口 + 独立 category):notify 模块只暴露这
    六个推送入口,不给第七类事件留路径。v1.3-② 验收②:白名单六类齐(v1.3-② 扩第六类 K4
    持仓派发警报,用户 2026-07-26 拍板独立开关 + 独立 category)。"""
    assert set(notify.__all__) == {
        "NotifyOutcome", "push_report_ready", "push_retreat_brake",
        "push_precall_summary", "push_d5_exit", "push_circuit_breaker", "push_holding_alert",
    }


def test_categories_are_six_distinct():
    """六类推送各自独立的 APNs category(互不复用,客户端据此挂各自 UNNotificationCategory)。"""
    cats = {
        apns.CATEGORY_REPORT, apns.CATEGORY_RETREAT, apns.CATEGORY_PRECALL,
        apns.CATEGORY_D5EXIT, apns.CATEGORY_CIRCUIT, apns.CATEGORY_HOLDING_ALERT,
    }
    assert cats == {"REPORT", "RETREAT", "PRECALL", "D5EXIT", "CIRCUIT", "HOLDINGALERT"}
    assert len(cats) == 6


def test_report_push_gated_off(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    set_push(report=False, retreat=True, precall=True, d5exit=True, circuit=True, holding_alert=True, db_path=db)
    out = notify.push_report_ready("2026-07-17", db_path=db, transport=_ok_transport)
    assert out.sent == 0 and out.skipped_reason == "push_report_off"


def test_report_push_sends_when_on(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    upsert_device("tok2", db_path=db)
    set_push(report=True, retreat=True, precall=True, d5exit=True, circuit=True, holding_alert=True, db_path=db)
    out = notify.push_report_ready("2026-07-17", db_path=db, transport=_ok_transport)
    assert out.sent == 2 and out.failed == 0


def test_retreat_push_gated_off(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    set_push(report=True, retreat=False, precall=True, d5exit=True, circuit=True, holding_alert=True, db_path=db)
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


def _set_switch(db, column: str, on: bool) -> None:
    """直接改 app_settings 的推送开关列(v1.1-G 的 4 字段设置端点尚未落地,A/B 测试
    用最小 SQL 切换 push_precall / push_d5exit,不提前引入 G 的写入面)。"""
    import sqlite3

    from neckline.settings_store import _ensure_row
    conn = sqlite3.connect(str(db))
    try:
        _ensure_row(conn)
        conn.execute(f"UPDATE app_settings SET {column}=? WHERE id=1", (1 if on else 0,))
        conn.commit()
    finally:
        conn.close()


def test_precall_summary_gated_off(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    _set_switch(db, "push_precall", False)
    out = notify.push_precall_summary(
        {"gap_up": 2, "low_open": 1, "position_low_open": 0, "auction": 1},
        db_path=db, transport=_ok_transport,
    )
    assert out.sent == 0 and out.skipped_reason == "push_precall_off"


def test_precall_summary_sends_when_on(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    upsert_device("tok2", db_path=db)
    out = notify.push_precall_summary(
        {"gap_up": 1, "low_open": 0, "position_low_open": 1, "auction": 0},
        db_path=db, transport=_ok_transport,
    )
    assert out.sent == 2 and out.failed == 0   # 默认开(列默认 1)


def test_precall_summary_circuit_locked_prefixes_note(api_env, apns_configured):
    """审计 🟡-4:熔断锁定 → 标题/正文前置「熔断中:今日只减不加」+ custom 带 circuitLocked。"""
    import json
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    seen = {}

    def _capture(url, headers, body):
        seen.update(json.loads(body.decode() if isinstance(body, bytes) else body))
        return apns.PushResult(ok=True, status=200, reason="ok")

    out = notify.push_precall_summary(
        {"gap_up": 0, "low_open": 0, "position_low_open": 0, "auction": 0},
        circuit_locked=True, db_path=db, transport=_capture,
    )
    assert out.sent == 1
    alert = seen["aps"]["alert"]
    assert "熔断中:今日只减不加" in alert["title"]
    assert "熔断中:今日只减不加" in alert["body"] and "只许减仓" in alert["body"]
    assert seen["circuitLocked"] is True


def test_precall_summary_unlocked_has_no_circuit_note(api_env, apns_configured):
    """阴性方向:未锁定 → 文案不带熔断句(不制造误导),custom 标 False。"""
    import json
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    seen = {}

    def _capture(url, headers, body):
        seen.update(json.loads(body.decode() if isinstance(body, bytes) else body))
        return apns.PushResult(ok=True, status=200, reason="ok")

    notify.push_precall_summary(
        {"gap_up": 1, "low_open": 0, "position_low_open": 0, "auction": 0},
        db_path=db, transport=_capture,
    )
    alert = seen["aps"]["alert"]
    assert "熔断" not in alert["title"] and "熔断" not in alert["body"]
    assert seen["circuitLocked"] is False


def test_circuit_locked_note_matches_precall_constant():
    """结构性:notify 的字面量措辞与 `sentinel/precall.CIRCUIT_LOCKED_PRECALL_NOTE` 一致
    (两处刻意不互相 import,靠本断言防漂移——同 `_KIND_TIME_EXIT` 惯例)。"""
    from neckline.sentinel.precall import CIRCUIT_LOCKED_PRECALL_NOTE
    assert notify._CIRCUIT_LOCKED_NOTE == CIRCUIT_LOCKED_PRECALL_NOTE


def test_d5_exit_gated_off(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    _set_switch(db, "push_d5exit", False)
    out = notify.push_d5_exit("贵州茅台", "600519.SH", 5, db_path=db, transport=_ok_transport)
    assert out.sent == 0 and out.skipped_reason == "push_d5exit_off"


def test_d5_exit_sends_when_on(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    out = notify.push_d5_exit("贵州茅台", "600519.SH", 5, db_path=db, transport=_ok_transport)
    assert out.sent == 1


def _capture_transport():
    """记录 push body(JSON)的假 transport,供两档文案断言。"""
    import json
    captured = {}

    def _t(url, headers, body):
        captured["payload"] = json.loads(body)
        return apns.PushResult(ok=True, status=200, reason="ok")

    return _t, captured


def test_d5_exit_two_tier_time_exit_body(api_env, apns_configured):
    """两档非浮盈单:文案标「净浮盈 ≤0」;custom 带 timeExitState。"""
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    t, cap = _capture_transport()
    out = notify.push_d5_exit("贵州茅台", "600519.SH", 5, kind="time_exit_next_day",
                              two_tier=True, db_path=db, transport=t)
    assert out.sent == 1
    aps = cap["payload"]["aps"]["alert"]
    assert "净浮盈 ≤0" in aps["body"]
    assert cap["payload"]["timeExitState"] == "time_exit_next_day"


def test_d5_exit_single_tier_no_netfloat_wording(api_env, apns_configured):
    """K1 单档(two_tier=False,默认):无条件时间退出,文案不标净浮盈(单档退出与浮亏浮盈无关)。"""
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    t, cap = _capture_transport()
    notify.push_d5_exit("贵州茅台", "600519.SH", 5, db_path=db, transport=t)
    body = cap["payload"]["aps"]["alert"]["body"]
    assert "净浮盈" not in body and "时间退出日" in body


def test_d5_exit_hard_cap_body(api_env, apns_configured):
    """浮盈硬上限单:文案标「已达浮盈硬上限 D15」。"""
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    t, cap = _capture_transport()
    notify.push_d5_exit("贵州茅台", "600519.SH", 15, kind="hard_cap_exit",
                        max_hold_effective=15, two_tier=True, db_path=db, transport=t)
    body = cap["payload"]["aps"]["alert"]["body"]
    assert "浮盈硬上限 D15" in body
    assert cap["payload"]["timeExitState"] == "hard_cap_exit"


def _fake_episode(note="连续 3 笔止损离场触发熔断(基于台账 3 笔已补录成交)。"):
    from neckline.sentinel.circuit import CircuitEpisode
    return CircuitEpisode(
        id=1, triggered_at="2026-07-22T08:00:00+00:00",
        trigger_reason="consecutive_stops", trigger_ref_date="20260722",
        basis={"position_ids": [1, 2, 3], "window": "2026-07-22", "note": note},
        created_at="2026-07-22T08:00:00+00:00",
    )


def test_circuit_push_gated_off(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    _set_switch(db, "push_circuit", False)
    out = notify.push_circuit_breaker(_fake_episode(), db_path=db, transport=_ok_transport)
    assert out.sent == 0 and out.skipped_reason == "push_circuit_off"


def test_circuit_push_sends_when_on(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    upsert_device("tok2", db_path=db)
    out = notify.push_circuit_breaker(_fake_episode(), db_path=db, transport=_ok_transport)
    assert out.sent == 2 and out.failed == 0   # 默认开(push_circuit 列默认 1)


def test_holding_alert_gated_off(api_env, apns_configured):
    """第六类:K4 持仓派发警报,push_holding_alert 关 → 跳过。"""
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    _set_switch(db, "push_holding_alert", False)
    out = notify.push_holding_alert("贵州茅台", "600519.SH", ["年线下涨停(疑似诱多做局派发)"],
                                    db_path=db, transport=_ok_transport)
    assert out.sent == 0 and out.skipped_reason == "push_holding_alert_off"


def test_holding_alert_sends_when_on(api_env, apns_configured):
    """第六类默认开(push_holding_alert 列默认 1);独立 category HOLDINGALERT + 文案含命中项。"""
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    upsert_device("tok2", db_path=db)
    t, cap = _capture_transport()
    out = notify.push_holding_alert("贵州茅台", "600519.SH",
                                    ["年线下涨停(疑似诱多做局派发)"], db_path=db, transport=t)
    assert out.sent == 2 and out.failed == 0
    assert cap["payload"]["aps"]["category"] == apns.CATEGORY_HOLDING_ALERT
    assert "年线下涨停" in cap["payload"]["aps"]["alert"]["body"]
    assert cap["payload"]["kind"] == "holding_alert"


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
