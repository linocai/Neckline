"""APNs 推送编排单测。**V2-⑪ 起结构 = 三级 category × N kind**(plan §五 V2-⑪-B / D5)
—— 原「白名单六类、每类一个 category」的守门断言随之换成:
    ① `notify.__all__` 只暴露「一条扇出路径 + 措辞层函数」;
    ② 每个措辞函数的 kind 都落在 `notify_kinds.ALL_KINDS` 里;
    ③ category 恒为三个之一;
    ④ 开关按 **kind** 配(关掉一个不连坐同级的其它 kind)。
V1 时代的行为断言(文案、两档 D5、熔断前缀、部分失败计数)**逐条保留**,只把开关的
设法从「改 push_* 列」换成「改 push_kinds」。"""

from __future__ import annotations

import dataclasses

import pytest

from neckline.api import notify
from neckline.api.stores import upsert_device
from neckline.config import Settings
from neckline.push import apns
from neckline import notify_kinds
from neckline.settings_store import get_push_kinds, set_push_kinds


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


def test_push_entrypoints_are_exactly_the_declared_set():
    """扇出路径守门(V2-⑪ 取代 V1「六类」断言,2026-08-03 再加一个持仓三事件旁路
    入口;**V2.3.3-④ 再加一个竞价确认汇总**):`notify.__all__` = 一条 `push_event`
    + 十个措辞函数,**不给第十二个入口留位置**。加入口 = 改这条断言 = 过一次人眼。

    ⚠ `push_auction_summary` 是**措辞层**新增,**kind 复用 `KIND_PRECALL`、零新 kind**
    (§五 〇-5 用户拍板)—— 所以 `test_notify_kinds.py` 的 `ALL_KINDS` 精确集合**不动**。
    """
    assert set(notify.__all__) == {
        "NotifyOutcome", "push_event",
        "push_report_ready", "push_retreat_brake", "push_precall_summary",
        "push_d5_exit", "push_consecutive_stops_notice", "push_holding_alert",
        "push_attention_alert", "push_custom_alert",
        "push_holding_risk_alert",
        "push_auction_summary",
    }


def test_categories_are_exactly_three():
    """三级 = 三个 APNs category(D5),**不多不少**;`push/apns.py` 只是别名,
    与唯一源 `notify_kinds` 逐字节相同。"""
    cats = {apns.CATEGORY_IMMEDIATE, apns.CATEGORY_IMPORTANT, apns.CATEGORY_DIGEST}
    assert cats == {"NKIMMEDIATE", "NKIMPORTANT", "NKDIGEST"}
    assert len(cats) == 3
    assert apns.CATEGORY_IMMEDIATE == notify_kinds.CATEGORY_IMMEDIATE
    assert apns.CATEGORY_IMPORTANT == notify_kinds.CATEGORY_IMPORTANT
    assert apns.CATEGORY_DIGEST == notify_kinds.CATEGORY_DIGEST
    # V1 的六个 category 常量已随本块删除(D2=A 路,契约一次性换血)。
    for gone in ("CATEGORY_REPORT", "CATEGORY_RETREAT", "CATEGORY_PRECALL",
                 "CATEGORY_D5EXIT", "CATEGORY_CIRCUIT", "CATEGORY_HOLDING_ALERT"):
        assert not hasattr(apns, gone), f"{gone} 应已删除"


def test_push_event_rejects_unregistered_kind(api_env, apns_configured):
    """白名单不开后门:未登记 kind → 直接抛,**不静默变成一条真推送**。"""
    upsert_device("tok1", db_path=api_env.db_path)
    with pytest.raises(ValueError):
        notify.push_event("totally_new_kind", "t", "b",
                          db_path=api_env.db_path, transport=_ok_transport)


def test_report_push_gated_off(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    _set_kind(db, notify_kinds.KIND_REPORT_READY, False)
    out = notify.push_report_ready("2026-07-17", db_path=db, transport=_ok_transport)
    assert out.sent == 0 and out.skipped_reason == "kind_off:report_ready"


def test_report_push_sends_when_on(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    upsert_device("tok2", db_path=db)
    out = notify.push_report_ready("2026-07-17", db_path=db, transport=_ok_transport)
    assert out.sent == 2 and out.failed == 0        # 全部 kind 默认开


# 🔴 **V2.4.0 P0(施工纪律 4:写明被谁取代)**:原两条用例是
#   · `test_retreat_push_gated_off` —— 开关关掉 → `skipped_reason == "kind_off:retreat"`;
#   · `test_retreat_push_sends_when_on` —— 开关开着 → `sent == 1`。
# 后者**被 P0.1 表「退潮 APNs / Bark / 系统推送 = 删」取代**:`KIND_RETREAT` 已进
# `notify_kinds.RETIRED_KINDS`,`push_event` 在**开关闸之前**就拒发 —— 开着也发不出去。
# 两条合并成下面这一条,正反两面都断言(开 / 关都必须是 `kind_retired:`,
# ⛔ 不许退化成"关着所以没发"这种看起来也绿的假证据)。


@pytest.mark.parametrize("enabled", [True, False])
def test_retired_retreat_kind_is_refused_regardless_of_switch(api_env, apns_configured, enabled):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    _set_kind(db, notify_kinds.KIND_RETREAT, enabled)
    out = notify.push_retreat_brake("炸板率飙升", db_path=db, transport=_ok_transport)
    assert out.sent == 0
    assert out.skipped_reason == "kind_retired:retreat"


def test_non_retired_kinds_are_unaffected_by_the_retirement_gate(api_env, apns_configured):
    """退役闸**只拦 `RETIRED_KINDS`** —— 别的 kind 一行行为不变(防误伤)。"""
    upsert_device("tok1", db_path=api_env.db_path)
    out = notify.push_report_ready("2026-07-17", db_path=api_env.db_path, transport=_ok_transport)
    assert out.sent == 1 and out.skipped_reason == ""


def test_no_devices_skips(api_env, apns_configured):
    out = notify.push_report_ready("2026-07-17", db_path=api_env.db_path, transport=_ok_transport)
    assert out.sent == 0 and out.skipped_reason == "no_devices"


def test_no_apns_config_skips(api_env):
    # api_env 的 apns.settings 无配置 → has_apns_config False → 跳过
    upsert_device("tok1", db_path=api_env.db_path)
    out = notify.push_report_ready("2026-07-17", db_path=api_env.db_path, transport=_ok_transport)
    assert out.skipped_reason == "no_apns_config"


def _set_kind(db, kind: str, on: bool) -> None:
    """只翻一个 kind 的开关,其余保持现状(全量写,但基于当前值改一位——正是
    `set_push_kinds` 要求给全的用意:不会漏传静默重置别的 kind)。"""
    kinds = get_push_kinds(db_path=db)
    kinds[kind] = on
    set_push_kinds(kinds, db_path=db)


def test_precall_summary_gated_off(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    _set_kind(db, notify_kinds.KIND_PRECALL, False)
    out = notify.push_precall_summary(
        {"gap_up": 2, "low_open": 1, "position_low_open": 0, "auction": 1},
        db_path=db, transport=_ok_transport,
    )
    assert out.sent == 0 and out.skipped_reason == "kind_off:precall"


def test_precall_summary_sends_when_on(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    upsert_device("tok2", db_path=db)
    out = notify.push_precall_summary(
        {"gap_up": 1, "low_open": 0, "position_low_open": 1, "auction": 0},
        db_path=db, transport=_ok_transport,
    )
    assert out.sent == 2 and out.failed == 0   # 默认开(列默认 1)


def test_precall_summary_has_no_lock_prefix_anymore(api_env, apns_configured):
    """**V2.2-⑤-B(裁定 #8)**:原「熔断锁定 → 标题/正文前置『今日只减不加』+ custom 带
    锁定位」三件**全删**。汇总推送从此只有一种形态:一句集合竞价校准。

    ⚠ 连带如实登记:那句「次日只减不加」此前靠 9:26 汇总的**必发豁免**送达,**豁免一并
    取消**(§八 第 19 项已当面告知用户:以后平静的清晨就是真的没推送)。"""
    import json
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    seen = {}

    def _capture(url, headers, body):
        seen.update(json.loads(body.decode() if isinstance(body, bytes) else body))
        return apns.PushResult(ok=True, status=200, reason="ok")

    out = notify.push_precall_summary(
        {"gap_up": 1, "low_open": 0, "position_low_open": 0, "auction": 0},
        db_path=db, transport=_capture,
    )
    assert out.sent == 1
    alert = seen["aps"]["alert"]
    assert alert["title"] == "盘前校准提醒"
    for banned in ("熔断", "只减不加", "只许减仓"):
        assert banned not in alert["title"] and banned not in alert["body"]
    assert "circuitLocked" not in seen      # custom 载荷里那一位也没了


def test_d5_exit_gated_off(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    _set_kind(db, notify_kinds.KIND_D5EXIT, False)
    out = notify.push_d5_exit("贵州茅台", "600519.SH", 5, db_path=db, transport=_ok_transport)
    assert out.sent == 0 and out.skipped_reason == "kind_off:d5exit"


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


def test_consecutive_stops_push_gated_off(api_env, apns_configured):
    """⑤-B 第 6 项:**kind 刻意仍是 `circuit`**(新增 kind 要用户拍板,改文案不触发那条
    纪律),开关 `push_circuit` 原样保留 —— 用户关掉就不推。"""
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    _set_kind(db, notify_kinds.KIND_CIRCUIT, False)
    out = notify.push_consecutive_stops_notice(3, ts_code="600519.SH", db_path=db,
                                               transport=_ok_transport)
    assert out.sent == 0 and out.skipped_reason == "kind_off:circuit"


def test_consecutive_stops_push_sends_when_on(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    upsert_device("tok2", db_path=db)
    out = notify.push_consecutive_stops_notice(3, ts_code="600519.SH", db_path=db,
                                               transport=_ok_transport)
    assert out.sent == 2 and out.failed == 0   # 默认开(push_circuit 列默认 1)


def test_holding_alert_gated_off(api_env, apns_configured):
    """第六类:K4 持仓派发警报,push_holding_alert 关 → 跳过。"""
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    _set_kind(db, notify_kinds.KIND_HOLDING_ALERT, False)
    out = notify.push_holding_alert("贵州茅台", "600519.SH", ["年线下涨停(疑似诱多做局派发)"],
                                    db_path=db, transport=_ok_transport)
    assert out.sent == 0 and out.skipped_reason == "kind_off:holding_alert"


def test_holding_alert_sends_when_on(api_env, apns_configured):
    """第六类默认开(push_holding_alert 列默认 1);独立 category HOLDINGALERT + 文案含命中项。"""
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    upsert_device("tok2", db_path=db)
    t, cap = _capture_transport()
    out = notify.push_holding_alert("贵州茅台", "600519.SH",
                                    ["年线下涨停(疑似诱多做局派发)"], db_path=db, transport=t)
    assert out.sent == 2 and out.failed == 0
    assert cap["payload"]["aps"]["category"] == apns.CATEGORY_IMPORTANT
    assert "年线下涨停" in cap["payload"]["aps"]["alert"]["body"]
    assert cap["payload"]["kind"] == "holding_alert"


def test_holding_risk_alert_rejects_unregistered_kind(api_env, apns_configured):
    """白名单不开后门:只接受持仓三事件 kind,别的串直接抛。"""
    upsert_device("tok1", db_path=api_env.db_path)
    with pytest.raises(ValueError):
        notify.push_holding_risk_alert(
            "holding_alert", "t", "b", db_path=api_env.db_path, transport=_ok_transport,
        )


def test_stop_approach_gated_off(api_env, apns_configured):
    """2026-08-03 用户拍板:止损逼近/触发,关掉该 kind → 跳过。"""
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    _set_kind(db, notify_kinds.KIND_STOP_APPROACH, False)
    out = notify.push_holding_risk_alert(
        notify_kinds.KIND_STOP_APPROACH, "持仓提醒:600001.SH",
        "现价9.60已跌破止损线9.50(-5%),若券商条件单未成交请立即人工确认(系统不代下单/撤单)",
        code="600001.SH", db_path=db, transport=_ok_transport,
    )
    assert out.sent == 0 and out.skipped_reason == "kind_off:stop_approach"


def test_stop_approach_sends_when_on(api_env, apns_configured):
    """默认开(2026-08-03 用户拍板即默认开,非"缺键取默认开"那条通用规则)。"""
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    t, cap = _capture_transport()
    out = notify.push_holding_risk_alert(
        notify_kinds.KIND_STOP_APPROACH, "持仓提醒:600001.SH",
        "现价9.60已跌破止损线9.50(-5%),若券商条件单未成交请立即人工确认(系统不代下单/撤单)",
        code="600001.SH", db_path=db, transport=t,
    )
    assert out.sent == 1
    assert cap["payload"]["kind"] == "stop_approach"
    assert cap["payload"]["aps"]["category"] == apns.CATEGORY_IMMEDIATE   # 三条均立即级
    body = cap["payload"]["aps"]["alert"]["body"]
    assert "已跌破止损线" in body                # 事实原样保留,不二次措辞
    assert "点开 APP 核对" in body                # ⑪-B 三句式补的第三句


def test_sector_dive_gated_off(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    _set_kind(db, notify_kinds.KIND_SECTOR_DIVE, False)
    out = notify.push_holding_risk_alert(
        notify_kinds.KIND_SECTOR_DIVE, "持仓提醒:600001.SH",
        "所属板块内可比个股(关注池样本3只)平均跌幅-4.0%,疑似板块跳水",
        code="600001.SH", db_path=db, transport=_ok_transport,
    )
    assert out.sent == 0 and out.skipped_reason == "kind_off:sector_dive"


def test_sector_dive_sends_when_on(api_env, apns_configured):
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    t, cap = _capture_transport()
    out = notify.push_holding_risk_alert(
        notify_kinds.KIND_SECTOR_DIVE, "持仓提醒:600001.SH",
        "所属板块内可比个股(关注池样本3只)平均跌幅-4.0%,疑似板块跳水",
        code="600001.SH", db_path=db, transport=t,
    )
    assert out.sent == 1
    assert cap["payload"]["kind"] == "sector_dive"
    assert "疑似板块跳水" in cap["payload"]["aps"]["alert"]["body"]


def test_take_profit_gated_off(api_env, apns_configured):
    """`take_profit` 的触发源是离场参考区间触达(见 engine.py 旁路 E),**不是**
    回落止盈——本测只管 kind 开关本身,措辞由调用方(engine.py)负责。"""
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    _set_kind(db, notify_kinds.KIND_TAKE_PROFIT, False)
    out = notify.push_holding_risk_alert(
        notify_kinds.KIND_TAKE_PROFIT, "离场参考提醒:600001.SH",
        "现价13.00已触达来源篮子的离场参考区间[13.00, 15.00](仅供参考,不是止盈信号,是否离场由您判断)",
        code="600001.SH", db_path=db, transport=_ok_transport,
    )
    assert out.sent == 0 and out.skipped_reason == "kind_off:take_profit"


def test_take_profit_sends_when_on_and_payload_has_kind(api_env, apns_configured):
    """端到端:事件文案 → `push_holding_risk_alert` → `push_event` → APNs payload
    含 `kind`(2026-08-03 定向任务书验收点「事件→push_event→payload 含 kind」)。"""
    db = api_env.db_path
    upsert_device("tok1", db_path=db)
    t, cap = _capture_transport()
    out = notify.push_holding_risk_alert(
        notify_kinds.KIND_TAKE_PROFIT, "离场参考提醒:600001.SH",
        "现价13.00已触达来源篮子的离场参考区间[13.00, 15.00](仅供参考,不是止盈信号,是否离场由您判断)",
        code="600001.SH", db_path=db, transport=t,
    )
    assert out.sent == 1 and out.kind == "take_profit" and out.level == "immediate"
    assert cap["payload"]["kind"] == "take_profit"
    assert cap["payload"]["level"] == "immediate"
    assert cap["payload"]["code"] == "600001.SH"
    body = cap["payload"]["aps"]["alert"]["body"]
    # 语义红线:不建议卖出,只中性陈述"触达"。
    assert "建议" not in body and "该卖" not in body
    assert "触达" in body


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
