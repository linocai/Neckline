"""决策日志端点单测(v2.0.0 起,PROJECT_PLAN §五 V2-⑩-C 决策日志强制表单退役)。

**v1.2-B~v1.4-⑤-B 的九项强制表单 + `link`/`cancel`/`revise`/`scenario-outcome`
四个写端点已全部下线**(不是跳过,是端点物理删除,`decision_log` 表停写留档)。
本文件覆盖:①`POST /decisions` 复用同一 URL 但已换血成蓝图 §2.2/§5.2「用户可选
补充」入口——全部字段可选(⑩-C「不传五必填 → 200 而非 400」的落点)、落
`user_actions` 而非 `decision_log`;②`GET /decisions` 只读归因(fixture 走裸 SQL,
不再有 create 端点可用);③硬约束:本节任何调用都不触发下单 / 不写 `decision_log`。
"""

from __future__ import annotations

from tests.conftest import insert_decision_log_row, set_decision_status

from neckline.decision_log import STATUS_CANCELLED, STATUS_PENDING


# —— POST /decisions:全部字段可选,不传五必填 → 200 而非 400 ——————————————————

def test_empty_submission_returns_200_with_empty_recorded(client, AUTH):
    """⑩-C 验收条款的直接落点:旧版这里传空表单会 400(缺 `maxChasePct` 键)或
    422(缺 `whyBuy`/`invalidation`/`playbookTag` 等必填字段)——v2.0.0 起完全没有
    "必填"这回事,空提交是合法的"这次没有可补充的内容"。"""
    r = client.post("/api/v1/decisions", headers=AUTH, json={})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "recorded": []}


def test_missing_body_fields_never_400(client, AUTH):
    """旧版 `whyBuy`/`whyEntryPrice`/`invalidation`/`playbookTag`/`maxChasePct`
    五项(任一缺失即 400/422)现在全部不存在于请求体形状里——传老格式的多余字段
    会被 pydantic 直接忽略,不会 400。"""
    r = client.post("/api/v1/decisions", headers=AUTH, json={
        "code": "600001.SH",
        "whyBuy": "老格式残留字段", "playbookTag": "SWING_CHASE",   # 多余字段,忽略
    })
    assert r.status_code == 200


def test_labels_recorded_as_user_action(client, AUTH, api_env):
    r = client.post("/api/v1/decisions", headers=AUTH, json={
        "code": "600001.SH", "labels": ["THEME_SHIFT", "NEWS_CATALYST"],
    })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "recorded": ["label"]}

    from neckline import user_actions
    rows = user_actions.list_actions(kind="label", db_path=api_env.db_path)
    assert len(rows) == 1
    assert rows[0]["ts_code"] == "600001.SH"
    assert rows[0]["payload"]["labels"] == ["THEME_SHIFT", "NEWS_CATALYST"]


def test_voice_note_recorded_as_user_action(client, AUTH, api_env):
    r = client.post("/api/v1/decisions", headers=AUTH, json={
        "code": "600001.SH", "voiceNote": "板块承接明显好转,先加一点观察",
    })
    assert r.status_code == 200
    assert r.json() == {"ok": True, "recorded": ["voice_note"]}

    from neckline import user_actions
    rows = user_actions.list_actions(kind="voice_note", db_path=api_env.db_path)
    assert len(rows) == 1
    assert rows[0]["payload"]["text"] == "板块承接明显好转,先加一点观察"


def test_both_labels_and_voice_note_in_one_call(client, AUTH, api_env):
    r = client.post("/api/v1/decisions", headers=AUTH, json={
        "code": "600001.SH", "labels": ["CORE_POSITION"], "voiceNote": "补一句",
    })
    assert r.status_code == 200
    assert set(r.json()["recorded"]) == {"label", "voice_note"}

    from neckline import user_actions
    assert len(user_actions.list_actions(kind="label", db_path=api_env.db_path)) == 1
    assert len(user_actions.list_actions(kind="voice_note", db_path=api_env.db_path)) == 1


def test_position_id_attaches_to_user_action(client, AUTH, api_env):
    r = client.post("/api/v1/decisions", headers=AUTH, json={
        "code": "600001.SH", "positionId": 7, "labels": ["WEAK_TO_STRONG"],
    })
    assert r.status_code == 200
    from neckline import user_actions
    rows = user_actions.list_actions(kind="label", db_path=api_env.db_path)
    assert rows[0]["position_id"] == 7


def test_invalid_label_code_422(client, AUTH):
    r = client.post("/api/v1/decisions", headers=AUTH, json={
        "code": "600001.SH", "labels": ["NOT_A_REAL_LABEL"],
    })
    assert r.status_code == 422


def test_all_seven_labels_accepted(client, AUTH):
    labels = [
        "THEME_SHIFT", "LEADER_REACTIVATE", "VOLUME_BREAKOUT", "WEAK_TO_STRONG",
        "CORE_POSITION", "NEWS_CATALYST", "PURE_TAPE_READING",
    ]
    r = client.post("/api/v1/decisions", headers=AUTH, json={"code": "600001.SH", "labels": labels})
    assert r.status_code == 200


# —— 硬约束:本节端点绝不写 decision_log、绝不下单 ——————————————————————————

def test_note_submission_does_not_write_decision_log(client, AUTH):
    """⑩-C 验收条款「decision_log 零新增行」的端到端体现:任何一种可选补充都
    不会让 `GET /decisions` 多出一行。"""
    client.post("/api/v1/decisions", headers=AUTH, json={
        "code": "600001.SH", "labels": ["THEME_SHIFT"], "voiceNote": "备注",
    })
    assert client.get("/api/v1/decisions", headers=AUTH).json()["items"] == []


def test_note_submission_does_not_open_a_position(client, AUTH):
    """§3.8 铁律:录入可选补充绝不触发任何下单动作——持仓台账仍为空。"""
    client.post("/api/v1/decisions", headers=AUTH, json={
        "code": "600001.SH", "labels": ["THEME_SHIFT"],
    })
    holdings = client.get("/api/v1/positions", headers=AUTH).json()["holdings"]
    assert holdings == []


# —— GET /decisions:只读归因,fixture 走裸 SQL(⑩-C 起没有 create 端点可借用)——————

def test_list_empty_by_default(client, AUTH):
    assert client.get("/api/v1/decisions", headers=AUTH).json()["items"] == []


def test_list_filters_by_status_and_code(client, AUTH, api_env):
    d1 = insert_decision_log_row(api_env.db_path, ts_code="600001.SH", why_buy="a", invalidation="x")
    d2 = insert_decision_log_row(api_env.db_path, ts_code="600002.SH", why_buy="b", invalidation="y")
    set_decision_status(api_env.db_path, d1.id, STATUS_CANCELLED)

    pending = client.get("/api/v1/decisions", headers=AUTH, params={"status": "pending"}).json()["items"]
    assert [d["id"] for d in pending] == [d2.id]

    by_code = client.get("/api/v1/decisions", headers=AUTH, params={"code": "600001.SH"}).json()["items"]
    assert [d["id"] for d in by_code] == [d1.id]

    all_items = client.get("/api/v1/decisions", headers=AUTH).json()["items"]
    assert len(all_items) == 2


def test_list_contract_shape_reads_historical_row(client, AUTH, api_env):
    """历史行(割接前的真实生产数据)必须仍能被 `GET /decisions` 如实装配——只读
    入口的存在意义就是这个。"""
    insert_decision_log_row(
        api_env.db_path, ts_code="600001.SH", name="示例甲", why_buy="题材热",
        why_entry_price="回调低吸", invalidation="跌破10日线",
        thesis_tags=["THEME", "CAPITAL_FLOW"], playbook_tag="SWING_CHASE",
        target_price=12.0, exit_low=9.0, exit_high=9.5,
        contingency_scenarios=[{"scenario": "s", "trigger": "t", "action": "HOLD", "matched": True}],
        planned_price=10.0, planned_qty=1000, max_chase_pct=3.0,
    )
    item = client.get("/api/v1/decisions", headers=AUTH).json()["items"][0]
    assert item["code"] == "600001.SH" and item["name"] == "示例甲"
    assert item["whyBuy"] == "题材热" and item["invalidation"] == "跌破10日线"
    assert item["thesisTags"] == ["THEME", "CAPITAL_FLOW"]
    assert item["playbookTag"] == "SWING_CHASE"
    assert item["maxChasePct"] == 3.0
    assert item["status"] == STATUS_PENDING
    assert item["contingencyScenarios"][0]["matched"] is True
