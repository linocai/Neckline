"""问询历史列表 / 详情端点单测(plan §五 v1.4-⑦-B,§七 P3-13)。

领域层持久化行为(question 抽取 / materials 快照 / search_hits 全文归档 / 与
`inquiry_pool` 无耦合 / 落库失败优雅降级)已在 `tests/test_api_inquiry.py::
TestInquiryLogPersistence` 覆盖;本文件只测 `GET /inquiries`(列表)与
`GET /inquiries/{id}`(详情)两个端点本身的装配、排序、分页、过滤与 404。
"""

from __future__ import annotations

import neckline.api.app as app_mod
from neckline.db import init_schema
from tests.conftest import seed_active_rule_v1, seed_synthetic_market


def test_inquiry_log_table_creation_is_idempotent(api_env):
    """新表走 `CREATE TABLE IF NOT EXISTS`(见 `neckline.db._SCHEMA`),重跑
    `init_schema` 不炸——同「新表/新列幂等迁移」项目铁律,新库/老库同一条路径。"""
    init_schema(db_path=api_env.db_path)
    init_schema(db_path=api_env.db_path)   # 重跑不炸,即幂等


def _seed(api_env, monkeypatch):
    """铺合成市场 + 现役章程,把问询台的「当日」钉在报告日(同
    `test_api_inquiry.py::market` fixture 的既定姿势),返回报告日。"""
    dates = seed_synthetic_market(api_env)
    seed_active_rule_v1(api_env)
    day = dates[-1]
    monkeypatch.setattr(app_mod, "_inquiry_basis_date", lambda: day)
    return day


# —— 404:记录本身不存在 ——————————————————————————————————————————————————

def test_detail_nonexistent_404(client, AUTH):
    r = client.get("/api/v1/inquiries/999999", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "not_found"


def test_list_empty_by_default(client, AUTH):
    assert client.get("/api/v1/inquiries", headers=AUTH).json()["items"] == []


# —— POST /inquiry 响应携带 inquiryId ——————————————————————————————————————

def test_post_inquiry_response_carries_inquiry_id(client, AUTH, api_env, monkeypatch):
    _seed(api_env, monkeypatch)
    r = client.post("/api/v1/inquiry", headers=AUTH, json={"code": "600001.SH", "messages": []})
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["inquiryId"], int) and body["inquiryId"] >= 1


# —— 列表 / 详情往返 ————————————————————————————————————————————————————————

def test_list_and_detail_roundtrip(client, AUTH, api_env, monkeypatch):
    _seed(api_env, monkeypatch)
    posted = client.post(
        "/api/v1/inquiry", headers=AUTH,
        json={"code": "600001.SH", "messages": [{"role": "user", "content": "怎么看"}]},
    ).json()
    iid = posted["inquiryId"]

    items = client.get("/api/v1/inquiries", headers=AUTH).json()["items"]
    assert len(items) == 1
    row = items[0]
    assert row["id"] == iid
    assert row["code"] == "600001.SH"
    assert row["question"] == "怎么看"
    assert row["answer"] == posted["reply"]
    assert row["verdict"] == posted["verdict"]
    assert row["evidence"] == posted["evidence"]
    assert row["positionId"] is None and row["decisionId"] is None

    detail = client.get(f"/api/v1/inquiries/{iid}", headers=AUTH).json()
    assert detail == row   # 列表行与详情行同一份 `_row_to_inquiry_log` 装配,逐字段相等


def test_list_most_recent_first(client, AUTH, api_env, monkeypatch):
    _seed(api_env, monkeypatch)
    r1 = client.post("/api/v1/inquiry", headers=AUTH, json={"code": "600001.SH", "messages": []})
    r2 = client.post("/api/v1/inquiry", headers=AUTH, json={"code": "600002.SH", "messages": []})
    items = client.get("/api/v1/inquiries", headers=AUTH).json()["items"]
    assert [i["id"] for i in items] == [r2.json()["inquiryId"], r1.json()["inquiryId"]]


def test_list_ts_code_filter(client, AUTH, api_env, monkeypatch):
    _seed(api_env, monkeypatch)
    client.post("/api/v1/inquiry", headers=AUTH, json={"code": "600001.SH", "messages": []})
    client.post("/api/v1/inquiry", headers=AUTH, json={"code": "600002.SH", "messages": []})

    items = client.get("/api/v1/inquiries", headers=AUTH, params={"tsCode": "600001.SH"}).json()["items"]
    assert len(items) == 1 and items[0]["code"] == "600001.SH"

    # 裸 6 位查询同样归一命中(写入通道已归一存 ts_code,同 decision_log 惯例)。
    bare = client.get("/api/v1/inquiries", headers=AUTH, params={"tsCode": "600001"}).json()["items"]
    assert len(bare) == 1 and bare[0]["code"] == "600001.SH"


def test_list_pagination_limit_offset(client, AUTH, api_env, monkeypatch):
    _seed(api_env, monkeypatch)
    for _ in range(3):
        client.post("/api/v1/inquiry", headers=AUTH, json={"code": "600001.SH", "messages": []})

    page1 = client.get("/api/v1/inquiries", headers=AUTH, params={"limit": 2, "offset": 0}).json()["items"]
    page2 = client.get("/api/v1/inquiries", headers=AUTH, params={"limit": 2, "offset": 2}).json()["items"]
    assert len(page1) == 2 and len(page2) == 1
    assert {i["id"] for i in page1}.isdisjoint({i["id"] for i in page2})

    all_items = client.get("/api/v1/inquiries", headers=AUTH).json()["items"]
    assert len(all_items) == 3
