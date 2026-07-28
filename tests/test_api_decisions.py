"""v1.2-B 预注册决策日志端点单测(plan §五 v1.2-B 验收 + 客户端契约清单逐字段核对,
v1.4-⑤-B 补第⑨项「最高追价上限」)。

覆盖:①`createdAt` 服务端生成(客户端传入被忽略);②①-⑥+⑦情景文本+⑧无 UPDATE
路径(revise 新增行、首版行原地不变、`revisionOf` 指链根);③`scenario-outcome`
只翻 `matched`、不动情景文本;④`thesisTags`/`playbookTag`/情景树 `action` 非法码
422、合法码往返;⑤情景树 JSON 往返;⑥link 置 filled + positionId、cancel 置
cancelled、id 不存在 404;⑦list 过滤;⑧契约形状逐字段核对;⑨`maxChasePct`(v1.4-⑤-B)
填数/显式 null/缺键 400 三态,create 与 revise 两端点均覆盖,且与 `plannedPrice`
互不干扰。
"""

from __future__ import annotations


def _decision_body(**overrides):
    body = {
        "code": "600001.SH",
        "name": "示例甲",
        "whyBuy": "题材热+量能启动,板块龙头效应明显",
        "whyEntryPrice": "回调至10日均线企稳,缩量企稳信号",
        "targetPrice": 12.0,
        "exitLow": 9.0,
        "exitHigh": 9.5,
        "thesisTags": ["THEME", "CAPITAL_FLOW"],
        "invalidation": "跌破10日均线且缩量转放量下杀",
        "contingencyScenarios": [
            {"scenario": "次日高开超预期", "trigger": "开盘涨幅>3%", "action": "HOLD"},
            {"scenario": "次日低开破位", "trigger": "开盘跌幅>2%", "action": "ABANDON"},
        ],
        "playbookTag": "SWING_CHASE",
        "plannedPrice": 10.0,
        "plannedQty": 1000,
        # v1.4-⑤-B:⑨最高追价上限,必须显式传(见下方 test_missing_max_chase_pct_400)。
        "maxChasePct": 3.0,
    }
    body.update(overrides)
    return body


# —— 创建 + createdAt 服务端生成 ——————————————————————————————————————————

def test_create_decision_roundtrip_contract_shape(client, AUTH):
    r = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body())
    assert r.status_code == 200
    body = r.json()
    assert body["id"] >= 1
    assert body["code"] == "600001.SH" and body["name"] == "示例甲"
    assert body["createdAt"]   # 非空
    assert body["whyBuy"] == "题材热+量能启动,板块龙头效应明显"
    assert body["whyEntryPrice"] == "回调至10日均线企稳,缩量企稳信号"
    assert body["targetPrice"] == 12.0
    assert body["exitLow"] == 9.0 and body["exitHigh"] == 9.5
    assert body["thesisTags"] == ["THEME", "CAPITAL_FLOW"]
    assert body["invalidation"] == "跌破10日均线且缩量转放量下杀"
    assert len(body["contingencyScenarios"]) == 2
    assert body["contingencyScenarios"][0] == {
        "scenario": "次日高开超预期", "trigger": "开盘涨幅>3%", "action": "HOLD", "matched": False,
    }
    assert body["playbookTag"] == "SWING_CHASE"
    assert body["plannedPrice"] == 10.0 and body["plannedQty"] == 1000
    assert body["maxChasePct"] == 3.0
    assert body["status"] == "pending"
    assert body["positionId"] is None
    assert body["revisionOf"] is None


def test_create_decision_ignores_client_supplied_created_at(client, AUTH):
    """服务端生成 createdAt——客户端传入的同名字段被忽略(`DecisionCreateIn`
    根本没有该字段,不会解析进请求体;这里额外传一个荒谬的值验证不会泄漏进去)。"""
    body = _decision_body()
    body["createdAt"] = "1999-01-01T00:00:00Z"
    r = client.post("/api/v1/decisions", headers=AUTH, json=body)
    assert r.status_code == 200
    assert r.json()["createdAt"] != "1999-01-01T00:00:00Z"
    assert r.json()["createdAt"].startswith("20")   # 真实当下年份戳


def test_create_decision_optional_fields_default_null(client, AUTH):
    body = _decision_body()
    for k in ("targetPrice", "exitLow", "exitHigh", "plannedPrice", "plannedQty", "name"):
        body.pop(k, None)
    r = client.post("/api/v1/decisions", headers=AUTH, json=body)
    assert r.status_code == 200
    out = r.json()
    assert out["targetPrice"] is None and out["exitLow"] is None and out["exitHigh"] is None
    assert out["plannedPrice"] is None and out["plannedQty"] is None
    assert out["name"] == "600001.SH"   # 缺省回退到 code(同 positions 惯例)


# —— ⑨ maxChasePct(v1.4-⑤-B):填数 / 显式 null / 缺键 400 三态 ——————————————————

def test_create_decision_max_chase_pct_negative_value_roundtrips(client, AUTH):
    """允许负值(=只在低开时买)——不是只能填正数。"""
    r = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body(maxChasePct=-1.5))
    assert r.status_code == 200
    assert r.json()["maxChasePct"] == -1.5


def test_create_decision_max_chase_pct_explicit_null_is_legal(client, AUTH):
    """显式传 `null` = 主动选择"不设上限",合法、非 400。"""
    r = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body(maxChasePct=None))
    assert r.status_code == 200
    assert r.json()["maxChasePct"] is None


def test_create_decision_missing_max_chase_pct_400(client, AUTH):
    """省略该 JSON 键(与"显式传 null"不同)→ 400 `reason=max_chase_required`。"""
    body = _decision_body()
    body.pop("maxChasePct")
    r = client.post("/api/v1/decisions", headers=AUTH, json=body)
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "max_chase_required"


def test_create_decision_max_chase_pct_independent_of_planned_price(client, AUTH):
    """`maxChasePct`(追价上限)与 `plannedPrice`(计划挂单价)相互独立——改一个不
    影响另一个,且可以只设其中一个(plannedPrice 缺省、maxChasePct 显式 null 仍合法)。"""
    body = _decision_body(maxChasePct=2.5)
    body.pop("plannedPrice")
    r = client.post("/api/v1/decisions", headers=AUTH, json=body)
    assert r.status_code == 200
    out = r.json()
    assert out["maxChasePct"] == 2.5
    assert out["plannedPrice"] is None


def test_revise_missing_max_chase_pct_400(client, AUTH):
    """revise 端点同样要求 `maxChasePct` 显式传(修订=重新预注册九项)。"""
    did = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body()).json()["id"]
    body = _decision_body()
    body.pop("code"); body.pop("name"); body.pop("maxChasePct")
    r = client.post(f"/api/v1/decisions/{did}/revise", headers=AUTH, json=body)
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "max_chase_required"


def test_revise_max_chase_pct_roundtrips(client, AUTH):
    did = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body(maxChasePct=3.0)).json()["id"]
    body = _decision_body(maxChasePct=-0.5)
    body.pop("code"); body.pop("name")
    r = client.post(f"/api/v1/decisions/{did}/revise", headers=AUTH, json=body)
    assert r.status_code == 200
    assert r.json()["maxChasePct"] == -0.5
    # 首版原地不变(仍是修订前的 3.0)。
    original = next(d for d in client.get("/api/v1/decisions", headers=AUTH).json()["items"] if d["id"] == did)
    assert original["maxChasePct"] == 3.0


# —— 非法枚举码 422 / 合法码往返 ————————————————————————————————————————————

def test_invalid_thesis_tag_code_422(client, AUTH):
    body = _decision_body(thesisTags=["NOT_A_REAL_TAG"])
    r = client.post("/api/v1/decisions", headers=AUTH, json=body)
    assert r.status_code == 422


def test_invalid_playbook_tag_code_422(client, AUTH):
    body = _decision_body(playbookTag="NOT_A_REAL_PLAYBOOK")
    r = client.post("/api/v1/decisions", headers=AUTH, json=body)
    assert r.status_code == 422


def test_invalid_scenario_action_code_422(client, AUTH):
    body = _decision_body(contingencyScenarios=[
        {"scenario": "x", "trigger": "y", "action": "SELL_EVERYTHING"},
    ])
    r = client.post("/api/v1/decisions", headers=AUTH, json=body)
    assert r.status_code == 422


def test_all_valid_thesis_tags_and_playbook_tags_accepted(client, AUTH):
    body = _decision_body(
        thesisTags=["THEME", "SENTIMENT_CYCLE", "CAPITAL_FLOW", "TECH_PATTERN", "NEWS"],
        playbookTag="BREATHING_TRIAL",
    )
    r = client.post("/api/v1/decisions", headers=AUTH, json=body)
    assert r.status_code == 200
    out = r.json()
    assert set(out["thesisTags"]) == {"THEME", "SENTIMENT_CYCLE", "CAPITAL_FLOW", "TECH_PATTERN", "NEWS"}
    assert out["playbookTag"] == "BREATHING_TRIAL"


def test_all_valid_scenario_actions_accepted(client, AUTH):
    body = _decision_body(contingencyScenarios=[
        {"scenario": "a", "trigger": "ta", "action": "BUY"},
        {"scenario": "b", "trigger": "tb", "action": "HOLD"},
        {"scenario": "c", "trigger": "tc", "action": "REDUCE"},
        {"scenario": "d", "trigger": "td", "action": "ABANDON"},
    ])
    r = client.post("/api/v1/decisions", headers=AUTH, json=body)
    assert r.status_code == 200
    actions = [s["action"] for s in r.json()["contingencyScenarios"]]
    assert actions == ["BUY", "HOLD", "REDUCE", "ABANDON"]


# —— link / cancel ————————————————————————————————————————————————————————

def test_link_sets_filled_and_position_id(client, AUTH):
    did = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body()).json()["id"]
    r = client.post(f"/api/v1/decisions/{did}/link", headers=AUTH, json={"positionId": 77})
    assert r.status_code == 200 and r.json()["ok"] is True

    items = client.get("/api/v1/decisions", headers=AUTH).json()["items"]
    linked = next(d for d in items if d["id"] == did)
    assert linked["status"] == "filled" and linked["positionId"] == 77
    # 八项内容未被 link 触碰
    assert linked["whyBuy"] == _decision_body()["whyBuy"]


def test_link_nonexistent_404(client, AUTH):
    r = client.post("/api/v1/decisions/999999/link", headers=AUTH, json={"positionId": 1})
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "not_found"


def test_cancel_sets_cancelled(client, AUTH):
    did = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body()).json()["id"]
    r = client.post(f"/api/v1/decisions/{did}/cancel", headers=AUTH)
    assert r.status_code == 200 and r.json()["ok"] is True
    items = client.get("/api/v1/decisions", headers=AUTH).json()["items"]
    cancelled = next(d for d in items if d["id"] == did)
    assert cancelled["status"] == "cancelled"


def test_cancel_nonexistent_404(client, AUTH):
    r = client.post("/api/v1/decisions/999999/cancel", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "not_found"


# —— revise:新增行、首版原地不变、revisionOf 指链根 ——————————————————————————

def test_revise_creates_new_row_leaves_original_untouched(client, AUTH):
    did = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body()).json()["id"]
    revise_body = _decision_body(
        whyBuy="修订后的理由:资金持续净流入超预期", targetPrice=13.0,
    )
    # revise 请求体不含 code/name(修订不能换股票)
    revise_body.pop("code")
    revise_body.pop("name")
    r = client.post(f"/api/v1/decisions/{did}/revise", headers=AUTH, json=revise_body)
    assert r.status_code == 200
    revised = r.json()
    assert revised["id"] != did
    assert revised["whyBuy"] == "修订后的理由:资金持续净流入超预期"
    assert revised["targetPrice"] == 13.0
    assert revised["revisionOf"] == did
    assert revised["status"] == "pending"
    assert revised["code"] == "600001.SH"   # 继承自原行

    items = {d["id"]: d for d in client.get("/api/v1/decisions", headers=AUTH).json()["items"]}
    original = items[did]
    assert original["whyBuy"] == _decision_body()["whyBuy"]   # 首版原地不变
    assert original["targetPrice"] == _decision_body()["targetPrice"]
    assert original["revisionOf"] is None


def test_revise_chain_points_to_root(client, AUTH):
    """对修订行再次修订,revisionOf 仍指向最初首版,不是上一版(链根语义)。"""
    did = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body()).json()["id"]
    rev1_body = _decision_body()
    rev1_body.pop("code"); rev1_body.pop("name")
    rev1_body["whyBuy"] = "v2"
    rev1 = client.post(f"/api/v1/decisions/{did}/revise", headers=AUTH, json=rev1_body).json()
    assert rev1["revisionOf"] == did

    rev2_body = dict(rev1_body)
    rev2_body["whyBuy"] = "v3"
    rev2 = client.post(f"/api/v1/decisions/{rev1['id']}/revise", headers=AUTH, json=rev2_body).json()
    assert rev2["revisionOf"] == did   # 不是 rev1["id"]


def test_revise_nonexistent_404(client, AUTH):
    body = _decision_body()
    body.pop("code"); body.pop("name")
    r = client.post("/api/v1/decisions/999999/revise", headers=AUTH, json=body)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "not_found"


def test_revise_invalid_enum_code_422(client, AUTH):
    did = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body()).json()["id"]
    body = _decision_body(playbookTag="BOGUS")
    body.pop("code"); body.pop("name")
    r = client.post(f"/api/v1/decisions/{did}/revise", headers=AUTH, json=body)
    assert r.status_code == 422


# —— scenario-outcome:只翻 matched,不动情景文本 ——————————————————————————

def test_scenario_outcome_only_flips_matched(client, AUTH):
    did = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body()).json()["id"]
    r = client.post(
        f"/api/v1/decisions/{did}/scenario-outcome", headers=AUTH,
        json={"outcomes": [{"index": 0, "matched": True}]},
    )
    assert r.status_code == 200 and r.json()["ok"] is True

    items = {d["id"]: d for d in client.get("/api/v1/decisions", headers=AUTH).json()["items"]}
    scenarios = items[did]["contingencyScenarios"]
    assert scenarios[0]["matched"] is True
    # scenario/trigger/action 逐字不变
    original_scenarios = _decision_body()["contingencyScenarios"]
    assert scenarios[0]["scenario"] == original_scenarios[0]["scenario"]
    assert scenarios[0]["trigger"] == original_scenarios[0]["trigger"]
    assert scenarios[0]["action"] == original_scenarios[0]["action"]
    # 未提及的第二项不受影响
    assert scenarios[1]["matched"] is False
    assert scenarios[1]["scenario"] == original_scenarios[1]["scenario"]
    # 八项其余内容不受影响
    assert items[did]["whyBuy"] == _decision_body()["whyBuy"]
    assert items[did]["playbookTag"] == _decision_body()["playbookTag"]


def test_scenario_outcome_nonexistent_decision_404(client, AUTH):
    r = client.post(
        "/api/v1/decisions/999999/scenario-outcome", headers=AUTH,
        json={"outcomes": [{"index": 0, "matched": True}]},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "not_found"


def test_scenario_outcome_index_out_of_range_422(client, AUTH):
    did = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body()).json()["id"]
    r = client.post(
        f"/api/v1/decisions/{did}/scenario-outcome", headers=AUTH,
        json={"outcomes": [{"index": 99, "matched": True}]},
    )
    assert r.status_code == 422


# —— list 过滤 ————————————————————————————————————————————————————————————

def test_list_filters_by_status_and_code(client, AUTH):
    d1 = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body(code="600001.SH")).json()
    d2 = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body(code="600002.SH")).json()
    client.post(f"/api/v1/decisions/{d1['id']}/cancel", headers=AUTH)

    pending = client.get("/api/v1/decisions", headers=AUTH, params={"status": "pending"}).json()["items"]
    assert [d["id"] for d in pending] == [d2["id"]]

    by_code = client.get("/api/v1/decisions", headers=AUTH, params={"code": "600001.SH"}).json()["items"]
    assert [d["id"] for d in by_code] == [d1["id"]]

    all_items = client.get("/api/v1/decisions", headers=AUTH).json()["items"]
    assert len(all_items) == 2


def test_list_empty_by_default(client, AUTH):
    assert client.get("/api/v1/decisions", headers=AUTH).json()["items"] == []


# —— 审计件、非下单件(硬约束验证)———————————————————————————————————————————

def test_create_decision_does_not_open_a_position(client, AUTH):
    """录入决策日志绝不触发任何下单动作(§3.8 铁律)——创建后持仓台账仍为空。"""
    client.post("/api/v1/decisions", headers=AUTH, json=_decision_body())
    holdings = client.get("/api/v1/positions", headers=AUTH).json()["holdings"]
    assert holdings == []
