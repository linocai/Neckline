"""消息面告警存取单测(plan §五 v1.3-③-C4)。锁死:① 存/读往返;② 幂等覆盖
(同一 (ts_code, trade_date, category, event_key) 重跑取最新一次摘要,不留重复
行);③ 查不存在的交易日返回空列表,不崩;④ 空 items 不写(不清空当日已有告警,
见 `save_news_alerts` docstring);⑤ `load_seen_event_keys`(2026-07-26 必改 2,
事件级跨日去重的读取端)。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import pytest

from neckline.report.news_alerts_store import load_news_alerts, load_seen_event_keys, save_news_alerts

D = date(2026, 7, 24)


@dataclass
class _FakeItem:
    ts_code: str
    category: str
    summary: str
    source: str
    event_date: Optional[str] = None
    event_key: str = ""


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

    def test_same_code_date_category_different_event_key_is_a_separate_row(self, db):
        """2026-07-26 必改后 UNIQUE 约束加了 event_key——同一票同一天同一类别,
        但事件键不同(不同持股人各自的减持)必须各自留一行,不能互相覆盖丢信息。"""
        save_news_alerts(D, [
            _FakeItem(ts_code="600001.SH", category="REDUCTION", summary="张三减持",
                      source="tushare_holdertrade", event_date="20260724", event_key="张三|1|1"),
            _FakeItem(ts_code="600001.SH", category="REDUCTION", summary="李四减持",
                      source="tushare_holdertrade", event_date="20260724", event_key="李四|2|2"),
        ], db_path=db)
        rows = load_news_alerts(D, db_path=db)
        assert len(rows) == 2
        assert {r["summary"] for r in rows} == {"张三减持", "李四减持"}


class TestEmptyItemsNoop:
    def test_empty_items_does_not_clear_existing_rows(self, db):
        save_news_alerts(D, [
            _FakeItem(ts_code="600001.SH", category="REDUCTION", summary="已扫到", source="tushare_holdertrade"),
        ], db_path=db)
        save_news_alerts(D, [], db_path=db)   # 本次扫描降级为空,不应清空此前已有告警
        rows = load_news_alerts(D, db_path=db)
        assert len(rows) == 1
        assert rows[0]["summary"] == "已扫到"


class TestLoadSeenEventKeys:
    """2026-07-26 必改 2:事件级跨日去重的读取端——`_scan_reduction` 靠这个查询
    判断"这个事件是不是已经在更早的报告里报过了"。"""

    def test_saved_event_is_found_by_category(self, db):
        save_news_alerts(D, [
            _FakeItem(ts_code="600001.SH", category="REDUCTION", summary="张三减持",
                      source="tushare_holdertrade", event_date="20260724", event_key="张三|1.0000|0.1000"),
        ], db_path=db)
        seen = load_seen_event_keys("REDUCTION", db_path=db)
        assert ("600001.SH", "20260724", "张三|1.0000|0.1000") in seen

    def test_different_category_not_confused(self, db):
        save_news_alerts(D, [
            _FakeItem(ts_code="600001.SH", category="REGULATORY", summary="监管函",
                      source="llm_glm", event_date="20260724", event_key="somehow-set"),
        ], db_path=db)
        assert load_seen_event_keys("REDUCTION", db_path=db) == set()
        assert ("600001.SH", "20260724", "somehow-set") in load_seen_event_keys("REGULATORY", db_path=db)

    def test_llm_sourced_rows_excluded_no_event_date(self, db):
        """LLM 来源 event_date 恒 NULL、event_key 恒空串——不应出现在"已见过的
        事件"集合里(否则会把两个不同的 LLM 命中误判成同一事件而错误吞掉)。"""
        save_news_alerts(D, [
            _FakeItem(ts_code="600001.SH", category="REGULATORY", summary="监管函",
                      source="llm_glm", event_date=None, event_key=""),
        ], db_path=db)
        assert load_seen_event_keys("REGULATORY", db_path=db) == set()

    def test_no_rows_returns_empty_set_not_crash(self, db):
        assert load_seen_event_keys("REDUCTION", db_path=db) == set()

    def test_spans_multiple_trade_dates(self, db):
        """"跨日"去重的字面含义——查询不按 trade_date 过滤,历史上任意一天记录
        过的事件都算"已见过"。"""
        save_news_alerts(date(2026, 7, 1), [
            _FakeItem(ts_code="600001.SH", category="REDUCTION", summary="很早以前扫到",
                      source="tushare_holdertrade", event_date="20260701", event_key="张三|1.0000|0.1000"),
        ], db_path=db)
        seen = load_seen_event_keys("REDUCTION", db_path=db)
        assert ("600001.SH", "20260701", "张三|1.0000|0.1000") in seen
