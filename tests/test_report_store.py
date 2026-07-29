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


class TestWatchlistJsonRoundtrip:
    """v1.1-C.3 自选体检快照持久化(`reports.watchlist_json`)。"""

    def test_watchlist_saved_and_loaded(self, db):
        store.save_report(
            D, strategy_version="v1", sentiment={}, sectors=[], candidates=[], markdown="# t",
            watchlist=[{"ts_code": "600001.SH", "green_light": True, "buy_point_triggered": False}],
            db_path=db,
        )
        loaded = store.load_report(D, db_path=db)
        assert loaded["watchlist"] == [{"ts_code": "600001.SH", "green_light": True, "buy_point_triggered": False}]

    def test_watchlist_defaults_to_empty_list_when_omitted(self, db):
        """旧调用点(未传 `watchlist`)/自选池为空 → 落 `'[]'`,读回来是空列表,
        不是 None(前向兼容,客户端不必对 null 特判)。"""
        store.save_report(D, strategy_version="v1", sentiment={}, sectors=[], candidates=[], markdown="# t", db_path=db)
        assert store.load_report(D, db_path=db)["watchlist"] == []

    def test_load_report_by_str_also_returns_watchlist(self, db):
        store.save_report(
            D, strategy_version="v1", sentiment={}, sectors=[], candidates=[], markdown="# t",
            watchlist=[{"ts_code": "600002.SH"}], db_path=db,
        )
        loaded = store.load_report_by_str("20260304", db_path=db)
        assert loaded["watchlist"] == [{"ts_code": "600002.SH"}]

    def test_old_report_row_without_column_defaults_to_empty_list(self, db):
        """模拟老库(建 `watchlist_json` 列之前生成的报告行)——`_migrate_columns`
        幂等补列取默认 `'[]'`,读回来不炸、不是 None。"""
        import sqlite3

        from neckline.db import init_schema

        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE reports (
                trade_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL, strategy_version TEXT NOT NULL,
                sentiment_json TEXT NOT NULL, sectors_json TEXT NOT NULL, candidates_json TEXT NOT NULL,
                markdown TEXT NOT NULL
            );
            INSERT INTO reports VALUES ('20260304','t','v1','{}','[]','[]','# old');
        """)
        conn.commit()
        conn.close()

        init_schema(db_path=db)   # 触发 _migrate_columns 幂等补列
        loaded = store.load_report(D, db_path=db)
        assert loaded["watchlist"] == []


class TestIntelAndSectorMoneyflowJsonRoundtrip:
    """v1.3-③ C1/C2(`reports.intel_json`/`reports.sector_moneyflow_json`)。均为
    **单个对象**快照(非数组),同 `watchlist_json` 前向兼容先例。"""

    def test_saved_and_loaded_roundtrip(self, db):
        store.save_report(
            D, strategy_version="v1", sentiment={}, sectors=[], candidates=[], markdown="# t",
            intel={"tradeDate": "2026-03-04", "gainers": [{"code": "600001.SH"}]},
            sector_moneyflow={"available": True, "topInflow": [{"code": "AAA.TI"}]},
            db_path=db,
        )
        loaded = store.load_report(D, db_path=db)
        assert loaded["intel"]["gainers"][0]["code"] == "600001.SH"
        assert loaded["sector_moneyflow"]["available"] is True
        assert loaded["sector_moneyflow"]["topInflow"][0]["code"] == "AAA.TI"

    def test_defaults_to_empty_dict_when_omitted(self, db):
        """旧调用点(未传 `intel`/`sector_moneyflow`)→ 落 `'{}'`,读回来是空字典,
        不是 None(前向兼容,客户端不必对 null 特判)。"""
        store.save_report(D, strategy_version="v1", sentiment={}, sectors=[], candidates=[], markdown="# t", db_path=db)
        loaded = store.load_report(D, db_path=db)
        assert loaded["intel"] == {}
        assert loaded["sector_moneyflow"] == {}

    def test_load_report_by_str_also_returns_intel_and_sector_moneyflow(self, db):
        store.save_report(
            D, strategy_version="v1", sentiment={}, sectors=[], candidates=[], markdown="# t",
            intel={"gainers": []}, sector_moneyflow={"available": False}, db_path=db,
        )
        loaded = store.load_report_by_str("20260304", db_path=db)
        assert loaded["intel"] == {"gainers": []}
        assert loaded["sector_moneyflow"] == {"available": False}

    def test_old_report_row_without_columns_defaults_to_empty_dict(self, db):
        """模拟老库(建 `intel_json`/`sector_moneyflow_json` 列之前生成的报告行)——
        `_migrate_columns` 幂等补列取默认值 '{}',读回来不炸、不是 None。"""
        import sqlite3

        from neckline.db import init_schema

        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE reports (
                trade_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL, strategy_version TEXT NOT NULL,
                sentiment_json TEXT NOT NULL, sectors_json TEXT NOT NULL, candidates_json TEXT NOT NULL,
                markdown TEXT NOT NULL, watchlist_json TEXT NOT NULL DEFAULT '[]'
            );
            INSERT INTO reports VALUES ('20260304','t','v1','{}','[]','[]','# old','[]');
        """)
        conn.commit()
        conn.close()

        init_schema(db_path=db)   # 触发 _migrate_columns 幂等补列
        loaded = store.load_report(D, db_path=db)
        assert loaded["intel"] == {}
        assert loaded["sector_moneyflow"] == {}


class TestNewsAlertsScanJsonRoundtrip:
    """v1.3-③-C4(`reports.news_alerts_scan_json`)——只落**扫描状态**元信息(命中
    条目落独立 `news_alerts` 表,见 `test_news_alerts_store.py`),数组快照,同
    `watchlist_json` 前向兼容先例。"""

    def test_saved_and_loaded_roundtrip(self, db):
        store.save_report(
            D, strategy_version="v1", sentiment={}, sectors=[], candidates=[], markdown="# t",
            news_alerts_scan=[
                {"source": "tushare_holdertrade", "scanned": True, "reason": "", "codesTotal": 0, "codesFailed": 0},
                {"source": "llm", "scanned": False, "reason": "未配置 LLM_PROVIDER/LLM_API_KEY", "codesTotal": 2, "codesFailed": 0},
            ],
            db_path=db,
        )
        loaded = store.load_report(D, db_path=db)
        assert len(loaded["news_alerts_scan"]) == 2
        assert loaded["news_alerts_scan"][0]["scanned"] is True
        assert loaded["news_alerts_scan"][1]["scanned"] is False
        assert "LLM_PROVIDER" in loaded["news_alerts_scan"][1]["reason"]

    def test_defaults_to_empty_list_when_omitted(self, db):
        store.save_report(D, strategy_version="v1", sentiment={}, sectors=[], candidates=[], markdown="# t", db_path=db)
        assert store.load_report(D, db_path=db)["news_alerts_scan"] == []

    def test_load_report_by_str_also_returns_news_alerts_scan(self, db):
        store.save_report(
            D, strategy_version="v1", sentiment={}, sectors=[], candidates=[], markdown="# t",
            news_alerts_scan=[{"source": "tushare_holdertrade", "scanned": True}],
            db_path=db,
        )
        loaded = store.load_report_by_str("20260304", db_path=db)
        assert loaded["news_alerts_scan"] == [{"source": "tushare_holdertrade", "scanned": True}]

    def test_old_report_row_without_column_defaults_to_empty_list(self, db):
        """模拟老库(建 `news_alerts_scan_json` 列之前生成的报告行)——`_migrate_columns`
        幂等补列取默认 `'[]'`,读回来不炸、不是 None。"""
        import sqlite3

        from neckline.db import init_schema

        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE reports (
                trade_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL, strategy_version TEXT NOT NULL,
                sentiment_json TEXT NOT NULL, sectors_json TEXT NOT NULL, candidates_json TEXT NOT NULL,
                markdown TEXT NOT NULL, watchlist_json TEXT NOT NULL DEFAULT '[]',
                intel_json TEXT NOT NULL DEFAULT '{}', sector_moneyflow_json TEXT NOT NULL DEFAULT '{}'
            );
            INSERT INTO reports VALUES ('20260304','t','v1','{}','[]','[]','# old','[]','{}','{}');
        """)
        conn.commit()
        conn.close()

        init_schema(db_path=db)   # 触发 _migrate_columns 幂等补列
        loaded = store.load_report(D, db_path=db)
        assert loaded["news_alerts_scan"] == []


class TestLoadWatchlistSnapshotBefore:
    """`load_watchlist_snapshot_before`(供 `watchlist_check.apply_llm_review` 的
    「状态变化」diff 用):严格早于目标日,不把即将被本次覆盖的同日旧值当基准。"""

    def test_returns_most_recent_prior_report_snapshot(self, db):
        store.save_report(
            date(2026, 3, 1), strategy_version="v1", sentiment={}, sectors=[], candidates=[], markdown="# t",
            watchlist=[{"ts_code": "600001.SH", "green_light": False}], db_path=db,
        )
        store.save_report(
            date(2026, 3, 3), strategy_version="v1", sentiment={}, sectors=[], candidates=[], markdown="# t",
            watchlist=[{"ts_code": "600001.SH", "green_light": True}], db_path=db,
        )
        snap = store.load_watchlist_snapshot_before(date(2026, 3, 4), db_path=db)
        assert snap["600001.SH"]["green_light"] is True   # 取最近一份(3-3),不是更早的 3-1

    def test_excludes_same_day_report_not_yet_saved_or_being_regenerated(self, db):
        """同日补跑场景:即使 `D` 当天已经存在一份报告(即将被本次重跑覆盖),
        查 `D` 的"上一份"也不应把 `D` 自己当基准(`<` 严格早于)。"""
        store.save_report(
            date(2026, 3, 3), strategy_version="v1", sentiment={}, sectors=[], candidates=[], markdown="# t",
            watchlist=[{"ts_code": "600001.SH", "green_light": False}], db_path=db,
        )
        store.save_report(
            D, strategy_version="v1", sentiment={}, sectors=[], candidates=[], markdown="# t",
            watchlist=[{"ts_code": "600001.SH", "green_light": True}], db_path=db,
        )
        snap = store.load_watchlist_snapshot_before(D, db_path=db)
        assert snap["600001.SH"]["green_light"] is False   # 3-3 的旧值,不是 D 自己

    def test_no_prior_report_returns_empty_dict(self, db):
        assert store.load_watchlist_snapshot_before(D, db_path=db) == {}

    def test_prior_report_with_empty_watchlist_returns_empty_dict(self, db):
        store.save_report(
            date(2026, 3, 1), strategy_version="v1", sentiment={}, sectors=[], candidates=[], markdown="# t",
            db_path=db,
        )
        assert store.load_watchlist_snapshot_before(D, db_path=db) == {}


class TestLLMJudgmentRoundtrip:
    def test_save_and_load_with_search_hits(self, db):
        result = JudgeResult(
            ts_code="600001.SH", provider="glm", model="glm-5.2", verdict=VERDICT_PASS,
            narrative="催化仍在持续。", degraded=False,
            search_hits=[SearchHit(title="标题", link="https://a.com", content="摘要", media="媒体A", publish_date="2026-07-18")],
            search_engine="search_pro",
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
        assert r["search_engine"] == "search_pro"

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
        assert rows[0]["search_engine"] is None   # v1.5-④-A3:未记录,不臆造

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
