"""挂单未成交追踪出口单测(plan §五 v1.4-⑦-A,§七 P3-12)。

领域层的落库/推进逻辑已在 `tests/test_pending_track.py` 覆盖(offset 窗口 /
retFromPlan / filled·cancelled 不追踪 / 幂等);本文件只测**端点**——`GET
/decisions/{id}/track` 把已有的 `decision_pending_track` 数据正确装配成
`{status, planPrice, rows:[...]}`,以及「没这条决策」(404)与「这条决策还没攒到
追踪数据」(200 空态)两种「空」必须能分开(§3.8「没有 vs 没看」)。

**v2.0.0(⑩-C)起**:`decision_log` 停写留档,fixture 改走
`tests.conftest.insert_decision_log_row`/`set_decision_status`(裸 SQL),不再
经由已下线的 `POST /decisions`(create)/`POST /decisions/{id}/link` 端点。
"""

from __future__ import annotations

from tests.conftest import insert_decision_log_row, seed_synthetic_market, set_decision_status

from neckline.decision_log import STATUS_FILLED
from neckline.report.pending_track import DECISION_PENDING_TRACK_DAYS, track_pending_decisions


# —— 404:决策本身不存在 ——————————————————————————————————————————————————

def test_track_nonexistent_decision_404(client, AUTH):
    r = client.get("/api/v1/decisions/999999/track", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "not_found"


# —— 200 空态:决策存在,但还没攒到任何追踪快照(不是 404)——————————————————————

def test_track_empty_rows_when_not_yet_due(client, AUTH, api_env):
    d = insert_decision_log_row(api_env.db_path, ts_code="600001.SH", planned_price=10.0, planned_qty=1000)
    r = client.get(f"/api/v1/decisions/{d.id}/track", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["planPrice"] == 10.0
    assert body["rows"] == []


# —— 往返:落过追踪数据后,端点如实装配 ——————————————————————————————————————

def test_track_roundtrip_with_rows(client, AUTH, api_env):
    dates = seed_synthetic_market(api_env)
    created_day = dates[5]
    d = insert_decision_log_row(
        api_env.db_path, ts_code="600001.SH", planned_price=10.0, planned_qty=1000,
        created_at=f"{created_day.isoformat()}T09:00:00+00:00",
    )

    track_days = dates[6:6 + DECISION_PENDING_TRACK_DAYS]
    for td in track_days:
        n = track_pending_decisions(td, parquet_dir=api_env.parquet_dir, db_path=api_env.db_path)
        assert n == 1

    r = client.get(f"/api/v1/decisions/{d.id}/track", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["planPrice"] == 10.0
    # v2.0.0:窗口到点不再翻 status(decision_log 停写留档),仍是 pending。
    assert body["status"] == "pending"
    assert len(body["rows"]) == DECISION_PENDING_TRACK_DAYS
    # 按 tradeDate 升序、dOffset 从 1 递增,逐行不重不漏
    for i, row in enumerate(body["rows"], start=1):
        assert row["dOffset"] == i
        assert row["tradeDate"] == track_days[i - 1].strftime("%Y%m%d")
        assert isinstance(row["close"], float)
        assert row["retFromPlan"] == (row["close"] - 10.0) / 10.0
    # 升序保证
    assert [r["tradeDate"] for r in body["rows"]] == sorted(r["tradeDate"] for r in body["rows"])


def test_track_ret_from_plan_null_without_planned_price(client, AUTH, api_env):
    dates = seed_synthetic_market(api_env)
    created_day = dates[5]
    d = insert_decision_log_row(
        api_env.db_path, ts_code="600001.SH", planned_price=None,
        created_at=f"{created_day.isoformat()}T09:00:00+00:00",
    )

    track_pending_decisions(dates[6], parquet_dir=api_env.parquet_dir, db_path=api_env.db_path)

    r = client.get(f"/api/v1/decisions/{d.id}/track", headers=AUTH)
    body = r.json()
    assert body["planPrice"] is None
    assert len(body["rows"]) == 1
    assert body["rows"][0]["retFromPlan"] is None
    assert body["rows"][0]["close"] is not None   # 收盘价仍如实记录,只是没基准价可比


def test_track_status_reflects_filled(client, AUTH, api_env):
    """`status` 如实反映历史行当前状态(即便 v2.0.0 起不会再有新的状态流转)——
    "追踪停在这里"与"这条决策不存在"是两回事。"""
    dates = seed_synthetic_market(api_env)
    created_day = dates[5]
    d = insert_decision_log_row(
        api_env.db_path, ts_code="600001.SH",
        created_at=f"{created_day.isoformat()}T09:00:00+00:00",
    )
    set_decision_status(api_env.db_path, d.id, STATUS_FILLED, position_id=1)

    track_pending_decisions(dates[6], parquet_dir=api_env.parquet_dir, db_path=api_env.db_path)

    r = client.get(f"/api/v1/decisions/{d.id}/track", headers=AUTH)
    body = r.json()
    assert body["status"] == "filled"
    assert body["rows"] == []   # filled 不进 pending 追踪查询,天然没有新增行
