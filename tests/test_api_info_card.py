"""`GET /report/{date}/info-card/{code}` 端点单测(plan §五 v1.4-④-B)。

域计算逻辑(K 线/RS 线/行业分歧线/快照/消息面/龙虎榜/市场语境的具体正确性)已在
`tests/test_info_card.py` 逐项覆盖;本文件只测**端点层**职责——404 两个 reason、
`code` 归一化比对、`k4_flags`/`name` 原样从存档传给 `build_info_card`(不重算)、
响应体到 `InfoCardOut` 的往返不丢字段。用 monkeypatch 把 `build_info_card` 换成
可控替身,隔离端点逻辑与信息卡内部计算逻辑。
"""

from __future__ import annotations

from datetime import date

import pytest

import neckline.report.info_card as info_card_mod
from neckline.report import store as report_store


def _candidate(ts_code: str, name: str, *, k4_flags=None) -> dict:
    return {
        "ts_code": ts_code, "name": name, "close": 10.0, "score": 88.0, "rank": 1,
        "board": "MAIN", "pattern_tags": [], "hot_sectors": [], "sector_names": [],
        "entry_plan": "", "stop_loss": "", "target": "", "invalidation_text": "",
        "invalidation_spec": {}, "entry_spec": {},
        "k4_flags": k4_flags or [],
    }


def _seed_report(db, d: date, *, k4_flags=None):
    report_store.save_report(
        d, strategy_version="v1.4.0",
        sentiment={"trade_date": d.strftime("%Y%m%d")}, sectors=[],
        candidates=[_candidate("600001.SH", "示例甲", k4_flags=k4_flags)],
        markdown="# 报告", db_path=db,
    )


def _fake_card() -> info_card_mod.InfoCard:
    return info_card_mod.InfoCard(
        code="600001.SH", name="示例甲", trade_date="20260717",
        kline_available=True,
        kline=[info_card_mod.InfoCardKlineBar(
            trade_date="20260717", open=10.0, high=10.5, low=9.8, close=10.25, vol=100000.0,
            ma20=10.1, ma250=None,
        )],
        rs_available=True,
        rs_line=[info_card_mod.InfoCardIndexPoint(trade_date="20260717", value=102.5)],
        industry_divergence_available=False,
        industry="小众行业",
        industry_divergence_unavailable_reason="行业样本不足(小众行业当日成员数不足,分歧线缺省)",
        snapshot=info_card_mod.InfoCardSnapshot(vol_ratio5=1.1, turnover_rate=5.0),
        k4_flags=[info_card_mod.InfoCardK4Flag(
            code="A1_turnover_gt_10", label="换手率 >10%(过热放量,接盘区)", level="strong",
            section="hard_cut", evidence_strength="price_volume", evidence="换手>10%次日跌停3.37%",
        )],
        mild_band=True,
        news=info_card_mod.InfoCardNews(scanned=False, unavailable_reason="候选不在消息面扫描域(仅持仓+自选)"),
        top_list=info_card_mod.InfoCardTopList(on_list_today=False, lookback_days_covered=3),
        market=info_card_mod.InfoCardMarket(limit_up_count=42, limit_down_count=3, above_ma20=True),
    )


def test_info_card_bad_date_404(client, AUTH):
    r = client.get("/api/v1/report/abc/info-card/600001.SH", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "report_not_found"


def test_info_card_no_report_that_day_404(client, AUTH):
    r = client.get("/api/v1/report/20200101/info-card/600001.SH", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "report_not_found"


def test_info_card_code_not_in_report_404(client, AUTH, api_env):
    _seed_report(api_env.db_path, date(2026, 7, 17))
    r = client.get("/api/v1/report/20260717/info-card/999999.SH", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "code_not_in_report"


def test_info_card_happy_path_shapes_full_payload(client, AUTH, api_env, monkeypatch):
    _seed_report(api_env.db_path, date(2026, 7, 17), k4_flags=["A1_turnover_gt_10"])
    captured = {}

    def _fake_build_info_card(trade_date, code, *, k4_flags, name=None, **kwargs):
        captured["trade_date"] = trade_date
        captured["code"] = code
        captured["k4_flags"] = k4_flags
        captured["name"] = name
        return _fake_card()

    monkeypatch.setattr(info_card_mod, "build_info_card", _fake_build_info_card)

    r = client.get("/api/v1/report/20260717/info-card/600001.SH", headers=AUTH)
    assert r.status_code == 200
    body = r.json()

    # 端点把当日存档里的 k4_flags/name 原样传给 build_info_card(不重算)。
    assert captured["trade_date"] == date(2026, 7, 17)
    assert captured["code"] == "600001.SH"
    assert captured["k4_flags"] == ["A1_turnover_gt_10"]
    assert captured["name"] == "示例甲"

    # 响应体逐路到位(往返不丢字段)。
    assert body["code"] == "600001.SH" and body["name"] == "示例甲"
    assert body["klineAvailable"] is True
    assert body["kline"][0]["close"] == pytest.approx(10.25)
    assert body["rsAvailable"] is True
    assert body["rsBenchmark"] == "000001.SH"
    assert body["rsLine"][0]["value"] == pytest.approx(102.5)
    assert body["industryDivergenceAvailable"] is False
    assert "样本不足" in body["industryDivergenceUnavailableReason"]
    assert body["industryDivergenceNote"] == "行业线=行业成员中位数合成,非申万官方指数"
    assert body["snapshot"]["volRatio5"] == pytest.approx(1.1)
    assert body["k4Flags"][0]["code"] == "A1_turnover_gt_10"
    assert body["k4Flags"][0]["section"] == "hard_cut"
    assert body["mildBand"] is True
    assert body["news"]["scanned"] is False
    assert body["news"]["unavailableReason"] == "候选不在消息面扫描域(仅持仓+自选)"
    assert body["topList"]["lookbackDaysCovered"] == 3
    assert body["market"]["limitUpCount"] == 42
    assert body["market"]["aboveMa20"] is True


def test_info_card_bare_code_normalizes_to_ts_code(client, AUTH, api_env, monkeypatch):
    """`code` 路径参数支持裸 6 位(不带交易所后缀),经 `normalize_ts_code` 归一后
    与存档里的 `ts_code` 比对——同 `positions`/`decision_log` 写入通道的归一惯例。"""
    _seed_report(api_env.db_path, date(2026, 7, 17))
    monkeypatch.setattr(info_card_mod, "build_info_card", lambda *a, **kw: _fake_card())

    r = client.get("/api/v1/report/20260717/info-card/600001", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["code"] == "600001.SH"


def test_info_card_requires_auth(client):
    r = client.get("/api/v1/report/20260717/info-card/600001.SH")
    assert r.status_code == 401
