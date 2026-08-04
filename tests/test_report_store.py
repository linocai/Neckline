"""报告与 LLM 审判落库单测(plan 2.4/2.5)。锁死:① 存/读往返;② 幂等覆盖(同一
交易日/同一 (交易日,票) 重跑不留重复行);③ 搜索结果全文(SearchHit)正确序列化
存档;④ 查不存在的交易日返回 None/空列表,不崩;⑤ 🟡 Y-1(小审 2026-08-03)—— 重跑
历史日期不得销毁 V1 冻结的 `candidates_json`/`watchlist_json` 快照(见
`TestV1FrozenSnapshotSurvivesRerun`)。"""

from __future__ import annotations

import json
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


class TestV1FrozenSnapshotSurvivesRerun:
    """🟡 Y-1(小审 2026-08-03,`archive/REVIEW_REPORT_V2_小审_⑭⑮_20260803.md`)守门:
    `INSERT OR REPLACE`(整行先删后插)叠加 ⑬-11 起 `watchlist_json` 不进列清单 + ⑭
    起 `candidates` 恒传 `[]`,会让重跑历史日期的 `scripts/report.py` 把 V1 冻结的
    `candidates_json`/`watchlist_json` 快照永久清空(V2 已删候选管线与自选体检,两者
    都无法重算)。修法:`INSERT ... ON CONFLICT(trade_date) DO UPDATE SET <本次
    写入列>`——`watchlist_json` 天然不在列清单里,`candidates_json` 额外加 SQL `CASE`
    守卫(只在"本次写 `[]` 且历史已非 `[]`"时保留旧值)。本类锁死:① 两列逐字节不变;
    ② 守卫不误伤"调用方真的想写非空 candidates"的合法路径;③ markdown/sentiment 等
    可重算字段仍会被新一轮渲染正常覆盖(不是"整行冻结",只冻两列)。"""

    def test_v1_candidates_and_watchlist_survive_v2_rerun(self, db):
        import sqlite3

        from neckline.db import init_schema

        init_schema(db_path=db)   # 先建好含全部现役列(含 watchlist_json)的真实 DDL
        v1_candidates = json.dumps(
            [{"ts_code": "600001.SH", "score": 95.0, "reason": "V1 历史候选快照"}],
            ensure_ascii=False,
        )
        v1_watchlist = json.dumps(
            [{"ts_code": "600002.SH", "verdict": "hold", "reason": "V1 历史自选体检快照"}],
            ensure_ascii=False,
        )
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                "INSERT INTO reports (trade_date, generated_at, strategy_version, sentiment_json, "
                "sectors_json, candidates_json, markdown, watchlist_json) VALUES (?,?,?,?,?,?,?,?)",
                ("20260304", "2026-03-04T09:00:00+00:00", "v1.3.3", "{}", "[]",
                 v1_candidates, "# V1 历史报告", v1_watchlist),
            )
            conn.commit()
        finally:
            conn.close()

        # 模拟 V2 重跑当日报告(`scripts/report.py 20260304`):`build_report` 恒传 candidates=[]。
        store.save_report(
            D, strategy_version="v2.0.0", sentiment={"x": 1}, sectors=[],
            candidates=[], markdown="# V2 重新渲染的报告", db_path=db,
        )

        conn = sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT candidates_json, watchlist_json, markdown FROM reports WHERE trade_date='20260304'"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == v1_candidates, "V1 候选快照被重跑覆写,冻结留档纪律被击穿"
        assert row[1] == v1_watchlist, "V1 自选体检快照被重跑覆写,冻结留档纪律被击穿"
        # markdown 不是冻结快照(V2 仍在正常生成报告正文),重跑应正常拿到新内容。
        assert row[2] == "# V2 重新渲染的报告"

    def test_explicit_non_empty_candidates_still_overwrites(self, db):
        """守卫只挡"本次写 `[]` 且历史已非 `[]`"这一种模式;调用方若显式传入非空
        `candidates`(不排除未来的合法写路径),仍必须正常覆盖,不误伤合法写入。"""
        store.save_report(
            D, strategy_version="v1", sentiment={}, sectors=[],
            candidates=[{"ts_code": "A"}], markdown="# 1", db_path=db,
        )
        store.save_report(
            D, strategy_version="v1", sentiment={}, sectors=[],
            candidates=[{"ts_code": "B"}], markdown="# 2", db_path=db,
        )
        loaded = store.load_report(D, db_path=db)
        assert loaded["candidates"] == [{"ts_code": "B"}]

    def test_empty_stays_empty_when_history_already_empty(self, db):
        """历史值本来就是 `[]`(如 V2 上线后新造的行)时,守卫不应把它"锁死"成
        任何非预期状态——重跑仍正常落 `[]`,不是误报"检测到冻结值"。"""
        store.save_report(
            D, strategy_version="v2.0.0", sentiment={}, sectors=[], candidates=[], markdown="# 1", db_path=db,
        )
        store.save_report(
            D, strategy_version="v2.0.0", sentiment={}, sectors=[], candidates=[], markdown="# 2", db_path=db,
        )
        loaded = store.load_report(D, db_path=db)
        assert loaded["candidates"] == []
        assert loaded["markdown"] == "# 2"


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


class TestLLMJudgmentReadOnlyAfterRetirement:
    """**V2-⑬-2**:`llm_judgments` 停写留档 —— 写函数 `save_llm_judgment` /
    `delete_llm_judgments` 已物理删除,原 `TestLLMJudgmentRoundtrip`(6 例往返/删除
    用例)随之删除。`load_llm_judgments` 保留为历史行只读,这里锁死这两点。"""

    def test_write_functions_are_gone_read_stays(self):
        from neckline.report import store as st

        for gone in ("save_llm_judgment", "delete_llm_judgments"):
            assert not hasattr(st, gone), f"{gone} 应已随 ⑬-2 删除"
            assert gone not in st.__all__
        assert hasattr(st, "load_llm_judgments")

    def test_load_on_empty_table_returns_empty_list(self, tmp_path):
        from neckline.report import store as st

        assert st.load_llm_judgments(date(2026, 7, 20), db_path=tmp_path / "t.db") == []

