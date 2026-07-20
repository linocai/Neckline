"""报告与 LLM 审判落库单测(plan 2.4/2.5)。锁死:① 存/读往返;② 幂等覆盖(同一
交易日/同一 (交易日,票) 重跑不留重复行);③ 搜索结果全文(SearchHit)正确序列化
存档;④ 查不存在的交易日返回 None/空列表,不崩。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from neckline.llm.base import SearchHit
from neckline.llm.judge import VERDICT_INACTIVE, VERDICT_PASS, VERDICT_VETO, JudgeResult
from neckline.report import store

D = date(2026, 3, 4)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "neckline_test.db"


class TestReportRoundtrip:
    def test_save_and_load_roundtrip(self, db):
        store.save_report(
            D,
            strategy_version="v1",
            sentiment={"position_quota": "满额", "limit_up_count": 40},
            sectors=[{"index_code": "AAA.TI", "name": "人工智能", "bonus": 3.0}],
            candidates=[{"ts_code": "600001.SH", "score": 95.0}],
            markdown="# 报告正文",
            db_path=db,
        )
        loaded = store.load_report(D, db_path=db)
        assert loaded is not None
        assert loaded["trade_date"] == "20260304"
        assert loaded["strategy_version"] == "v1"
        assert loaded["sentiment"]["position_quota"] == "满额"
        assert loaded["sectors"][0]["name"] == "人工智能"
        assert loaded["candidates"][0]["ts_code"] == "600001.SH"
        assert loaded["markdown"] == "# 报告正文"
        assert "generated_at" in loaded

    def test_missing_date_returns_none(self, db):
        assert store.load_report(date(2020, 1, 1), db_path=db) is None

    def test_overwrite_same_trade_date_is_idempotent(self, db):
        store.save_report(
            D, strategy_version="v1", sentiment={"a": 1}, sectors=[], candidates=[], markdown="old", db_path=db
        )
        store.save_report(
            D, strategy_version="v1", sentiment={"a": 2}, sectors=[], candidates=[], markdown="new", db_path=db
        )
        loaded = store.load_report(D, db_path=db)
        assert loaded["markdown"] == "new"
        assert loaded["sentiment"]["a"] == 2

        import sqlite3

        conn = sqlite3.connect(str(db))
        try:
            n = conn.execute("SELECT COUNT(*) FROM reports WHERE trade_date='20260304'").fetchone()[0]
        finally:
            conn.close()
        assert n == 1


class TestLLMJudgmentRoundtrip:
    def test_save_and_load_with_search_hits(self, db):
        result = JudgeResult(
            ts_code="600001.SH", provider="glm", model="glm-5.2", verdict=VERDICT_PASS,
            narrative="催化仍在持续。", degraded=False,
            search_hits=[SearchHit(title="标题", link="https://a.com", content="摘要", media="媒体A", publish_date="2026-07-18")],
        )
        store.save_llm_judgment(D, result, db_path=db)
        rows = store.load_llm_judgments(D, db_path=db)
        assert len(rows) == 1
        r = rows[0]
        assert r["ts_code"] == "600001.SH"
        assert r["verdict"] == VERDICT_PASS
        assert r["degraded"] is False
        assert r["search_hits"][0]["title"] == "标题"
        assert r["search_hits"][0]["link"] == "https://a.com"

    def test_degraded_judgment_stores_flag_and_reason(self, db):
        result = JudgeResult(
            ts_code="600002.SH", provider="none", model="", verdict=VERDICT_INACTIVE,
            narrative="LLM 未激活...", degraded=True, degrade_reason="未配置 LLM_PROVIDER/LLM_API_KEY",
        )
        store.save_llm_judgment(D, result, db_path=db)
        rows = store.load_llm_judgments(D, db_path=db)
        assert rows[0]["degraded"] is True
        assert rows[0]["degrade_reason"] == "未配置 LLM_PROVIDER/LLM_API_KEY"
        assert rows[0]["search_hits"] == []

    def test_multiple_candidates_same_day_ordered_by_insertion(self, db):
        for code in ["600001.SH", "600002.SH", "600003.SH"]:
            store.save_llm_judgment(
                D,
                JudgeResult(ts_code=code, provider="glm", model="glm-5.2", verdict=VERDICT_VETO, narrative="x", degraded=False),
                db_path=db,
            )
        rows = store.load_llm_judgments(D, db_path=db)
        assert [r["ts_code"] for r in rows] == ["600001.SH", "600002.SH", "600003.SH"]

    def test_overwrite_same_trade_date_and_code_is_idempotent(self, db):
        store.save_llm_judgment(
            D, JudgeResult(ts_code="600001.SH", provider="glm", model="glm-5.2", verdict=VERDICT_PASS, narrative="第一次", degraded=False),
            db_path=db,
        )
        store.save_llm_judgment(
            D, JudgeResult(ts_code="600001.SH", provider="glm", model="glm-5.2", verdict=VERDICT_VETO, narrative="重跑后改判", degraded=False),
            db_path=db,
        )
        rows = store.load_llm_judgments(D, db_path=db)
        assert len(rows) == 1
        assert rows[0]["verdict"] == VERDICT_VETO
        assert rows[0]["narrative"] == "重跑后改判"

    def test_missing_date_returns_empty_list(self, db):
        assert store.load_llm_judgments(date(2020, 1, 1), db_path=db) == []
