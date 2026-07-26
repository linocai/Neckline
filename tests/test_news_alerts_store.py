"""消息面告警存取单测(plan §五 v1.3-③-C4)。锁死:① 存/读往返;② 幂等覆盖
(同一 (ts_code, trade_date, category) 重跑取最新一次摘要,不留重复行);③ 查
不存在的交易日返回空列表,不崩;④ 空 items 不写(不清空当日已有告警,见
`save_news_alerts` docstring)。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from neckline.report.news_alerts_store import load_news_alerts, save_news_alerts

D = date(2026, 7, 24)


@dataclass
class _FakeItem:
    ts_code: str
    category: str
    summary: str
    source: str


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "neckline_test.db"


class TestRoundtrip:
    def test_save_and_load(self, db):
        save_news_alerts(D, [
            _FakeItem(ts_code="600001.SH", category="REDUCTION", summary="张三减持", source="tushare_holdertrade"),
        ], db_path=db)
        rows = load_news_alerts(D, db_path=db)
        assert len(rows) == 1
        assert rows[0]["ts_code"] == "600001.SH"
        assert rows[0]["category"] == "REDUCTION"
        assert rows[0]["summary"] == "张三减持"
        assert rows[0]["source"] == "tushare_holdertrade"
        assert "created_at" in rows[0]

    def test_missing_date_returns_empty_list(self, db):
        assert load_news_alerts(date(2020, 1, 1), db_path=db) == []

    def test_multiple_categories_same_code_all_kept(self, db):
        save_news_alerts(D, [
            _FakeItem(ts_code="600001.SH", category="REDUCTION", summary="减持", source="tushare_holdertrade"),
            _FakeItem(ts_code="600001.SH", category="REGULATORY", summary="监管函", source="llm_glm"),
        ], db_path=db)
        rows = load_news_alerts(D, db_path=db)
        assert {r["category"] for r in rows} == {"REDUCTION", "REGULATORY"}

    def test_different_codes_same_category_all_kept(self, db):
        save_news_alerts(D, [
            _FakeItem(ts_code="600001.SH", category="BLOWUP", summary="甲暴雷", source="llm_glm"),
            _FakeItem(ts_code="600002.SH", category="BLOWUP", summary="乙暴雷", source="llm_glm"),
        ], db_path=db)
        rows = load_news_alerts(D, db_path=db)
        assert {r["ts_code"] for r in rows} == {"600001.SH", "600002.SH"}


class TestIdempotentOverwrite:
    def test_same_code_date_category_overwrites_not_duplicates(self, db):
        save_news_alerts(D, [
            _FakeItem(ts_code="600001.SH", category="REDUCTION", summary="第一次扫到", source="tushare_holdertrade"),
        ], db_path=db)
        save_news_alerts(D, [
            _FakeItem(ts_code="600001.SH", category="REDUCTION", summary="重跑后更新", source="tushare_holdertrade"),
        ], db_path=db)
        rows = load_news_alerts(D, db_path=db)
        assert len(rows) == 1
        assert rows[0]["summary"] == "重跑后更新"

    def test_different_trade_date_is_a_separate_row(self, db):
        save_news_alerts(D, [
            _FakeItem(ts_code="600001.SH", category="REDUCTION", summary="第一天", source="tushare_holdertrade"),
        ], db_path=db)
        save_news_alerts(date(2026, 7, 25), [
            _FakeItem(ts_code="600001.SH", category="REDUCTION", summary="第二天", source="tushare_holdertrade"),
        ], db_path=db)
        assert len(load_news_alerts(D, db_path=db)) == 1
        assert len(load_news_alerts(date(2026, 7, 25), db_path=db)) == 1


class TestEmptyItemsNoop:
    def test_empty_items_does_not_clear_existing_rows(self, db):
        save_news_alerts(D, [
            _FakeItem(ts_code="600001.SH", category="REDUCTION", summary="已扫到", source="tushare_holdertrade"),
        ], db_path=db)
        save_news_alerts(D, [], db_path=db)   # 本次扫描降级为空,不应清空此前已有告警
        rows = load_news_alerts(D, db_path=db)
        assert len(rows) == 1
        assert rows[0]["summary"] == "已扫到"
