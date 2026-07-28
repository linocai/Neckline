"""挂单未成交追踪出口单测(plan §五 v1.4-⑦-A,§七 P3-12)。

领域层的落库/推进逻辑已在 `tests/test_pending_track.py` 覆盖(offset 窗口 / 过期 /
retFromPlan / filled·cancelled 不追踪 / 幂等);本文件只测**端点**——`GET
/decisions/{id}/track` 把已有的 `decision_pending_track` 数据正确装配成
`{status, planPrice, rows:[...]}`,以及「没这条决策」(404)与「这条决策还没攒到
追踪数据」(200 空态)两种「空」必须能分开(§3.8「没有 vs 没看」)。
"""

from __future__ import annotations

import neckline.decision_log as dl_mod
from neckline.report.pending_track import DECISION_PENDING_TRACK_DAYS, track_pending_decisions
from tests.conftest import seed_synthetic_market


def _decision_body(**overrides):
    body = {
        "code": "600001.SH",
        "name": "示例甲",
        "whyBuy": "题材热+量能启动",
        "whyEntryPrice": "回调至10日均线企稳",
        "invalidation": "跌破10日均线",
        "thesisTags": ["THEME"],
        "playbookTag": "SWING_CHASE",
        "plannedPrice": 10.0,
        "plannedQty": 1000,
        "maxChasePct": 3.0,
    }
    body.update(overrides)
    return body


# —— 404:决策本身不存在 ——————————————————————————————————————————————————

def test_track_nonexistent_decision_404(client, AUTH):
    r = client.get("/api/v1/decisions/999999/track", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "not_found"


# —— 200 空态:决策存在,但还没攒到任何追踪快照(不是 404)——————————————————————

def test_track_empty_rows_when_not_yet_due(client, AUTH):
    did = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body()).json()["id"]
    r = client.get(f"/api/v1/decisions/{did}/track", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["planPrice"] == 10.0
    assert body["rows"] == []


# —— 往返:落过追踪数据后,端点如实装配 ——————————————————————————————————————

def test_track_roundtrip_with_rows(client, AUTH, api_env, monkeypatch):
    dates = seed_synthetic_market(api_env)
    created_day = dates[5]
    monkeypatch.setattr(dl_mod, "_now", lambda: f"{created_day.isoformat()}T09:00:00+00:00")
    did = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body(plannedPrice=10.0)).json()["id"]

    track_days = dates[6:6 + DECISION_PENDING_TRACK_DAYS]
    for td in track_days:
        n = track_pending_decisions(td, parquet_dir=api_env.parquet_dir, db_path=api_env.db_path)
        assert n == 1

    r = client.get(f"/api/v1/decisions/{did}/track", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["planPrice"] == 10.0
    assert body["status"] == "expired"   # 第 N 个交易日到期,同批转 expired(领域层既定行为)
    assert len(body["rows"]) == DECISION_PENDING_TRACK_DAYS
    # 按 tradeDate 升序、dOffset 从 1 递增,逐行不重不漏
    for i, row in enumerate(body["rows"], start=1):
        assert row["dOffset"] == i
        assert row["tradeDate"] == track_days[i - 1].strftime("%Y%m%d")
        assert isinstance(row["close"], float)
        assert row["retFromPlan"] == (row["close"] - 10.0) / 10.0
    # 升序保证
    assert [r["tradeDate"] for r in body["rows"]] == sorted(r["tradeDate"] for r in body["rows"])


def test_track_ret_from_plan_null_without_planned_price(client, AUTH, api_env, monkeypatch):
    dates = seed_synthetic_market(api_env)
    created_day = dates[5]
    monkeypatch.setattr(dl_mod, "_now", lambda: f"{created_day.isoformat()}T09:00:00+00:00")
    body = _decision_body()
    body.pop("plannedPrice")
    did = client.post("/api/v1/decisions", headers=AUTH, json=body).json()["id"]

    track_pending_decisions(dates[6], parquet_dir=api_env.parquet_dir, db_path=api_env.db_path)

    r = client.get(f"/api/v1/decisions/{did}/track", headers=AUTH)
    body = r.json()
    assert body["planPrice"] is None
    assert len(body["rows"]) == 1
    assert body["rows"][0]["retFromPlan"] is None
    assert body["rows"][0]["close"] is not None   # 收盘价仍如实记录,只是没基准价可比


def test_track_status_reflects_filled_after_link(client, AUTH, api_env, monkeypatch):
    """成交后 `status` 跟着变(该决策自然从 pending 追踪查询里消失,不再新增行,
    但端点仍能读到它此刻的真实状态——"追踪停在这里"与"这条决策不存在"是两回事)。"""
    dates = seed_synthetic_market(api_env)
    created_day = dates[5]
    monkeypatch.setattr(dl_mod, "_now", lambda: f"{created_day.isoformat()}T09:00:00+00:00")
    did = client.post("/api/v1/decisions", headers=AUTH, json=_decision_body()).json()["id"]
    client.post(f"/api/v1/decisions/{did}/link", headers=AUTH, json={"positionId": 1})

    track_pending_decisions(dates[6], parquet_dir=api_env.parquet_dir, db_path=api_env.db_path)

    r = client.get(f"/api/v1/decisions/{did}/track", headers=AUTH)
    body = r.json()
    assert body["status"] == "filled"
    assert body["rows"] == []   # filled 后不再进 pending 追踪查询,天然没有新增行
