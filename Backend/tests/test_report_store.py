"""报告落库单测(V2.5.0 S7,PROJECT_PLAN §5.10)。

本文件锁三件事:

| # | 断言 |
|---|---|
| ① | `k9_reports` 存 / 读往返;同 `trade_date` **幂等重写**;🔴 **双日期两列都落对** |
| ② | 🔴 `listing_size` 的 **NULL ≠ 0**(「今天没跑成」vs「今天没有」,裁定 5) |
| ③ | 🔴 旧 `reports` 表的**写路径已物理删除**(冻结只读留档),历史行仍读得回来 |

⚠ **S7 删掉了 `save_report()`**:那是 K8 时代 `reports` 表的写函数,V2.5.0 之后既没有
生产调用方,也不该再长出一个 —— 留着一个「谁都能调回去」的写路径,等于让「只读留档」
这条纪律靠自觉维持。原本围绕它的 ~15 条往返 / 幂等 / V1 快照保护用例随之删除
(⛔ 不留 skip 掉的僵尸测试),换成 ③ 这一条「写函数确实不在了」。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from neckline.db import init_schema
from neckline.report import store

TRADE_DATE = date(2026, 8, 14)     # 周五
REPORT_DATE = date(2026, 8, 16)    # 周日 —— 双日期契约的那个场景


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "neckline_test.db"


def _save(db: Path, **overrides) -> None:
    payload = dict(
        trade_date=TRADE_DATE, report_date=REPORT_DATE, state="has_list",
        headline="今天有这些 · 12 只(严格 9 / 放宽 3)", gaps=[],
        markdown="# 今天有这些", structured={"listing": []}, strategy="K9",
        params_package_version="k9-params-1", pack_id="abc", pack_version="fp-2",
        listing_size=12, strict_count=9, relaxed_count=3, db_path=db,
    )
    payload.update(overrides)
    store.save_k9_report(**payload)


# ══════════════════════════════════════════════════════════════════════════
# ① 往返 + 幂等 + 双日期
# ══════════════════════════════════════════════════════════════════════════

class TestK9ReportRoundtrip:
    def test_both_dates_survive_the_round_trip(self, db):
        """🔴 双日期契约(LRN-20260816-001):周日报告的 `report_date` 是周日、
        `trade_date` 是紧邻的上一周五 —— 两列各存各的,⛔ 不许互相顶替。"""
        _save(db)
        got = store.load_k9_report(TRADE_DATE, db_path=db)
        assert got["trade_date"] == "20260814"
        assert got["report_date"] == "20260816"
        assert got["state"] == "has_list"
        assert got["structured"] == {"listing": []}

    def test_missing_date_returns_none(self, db):
        assert store.load_k9_report(date(2026, 8, 13), db_path=db) is None

    def test_rerunning_the_same_trade_date_overwrites_in_place(self, db):
        _save(db)
        _save(db, listing_size=5, strict_count=5, relaxed_count=0,
              headline="今天有这些 · 5 只(严格 5 / 放宽 0)")
        got = store.load_k9_report(TRADE_DATE, db_path=db)
        assert got["listing_size"] == 5
        with sqlite3.connect(db) as conn:
            n = conn.execute(f"SELECT COUNT(*) FROM {store.K9_TABLE}").fetchone()[0]
        assert n == 1, "同一交易日重跑⛔ 不许留重复行"

    def test_latest_report_is_the_newest_trade_date(self, db):
        _save(db)
        _save(db, trade_date=date(2026, 8, 17), report_date=date(2026, 8, 17))
        assert store.latest_k9_report(db_path=db)["trade_date"] == "20260817"


# ══════════════════════════════════════════════════════════════════════════
# ② NULL ≠ 0(裁定 5)
# ══════════════════════════════════════════════════════════════════════════

class TestNullIsNotZero:
    def test_not_run_stores_null_listing_size(self, db):
        """「今天没跑成」= 清单**根本没算出来** → NULL。"""
        _save(db, state="not_run", listing_size=None, strict_count=None,
              relaxed_count=None, params_package_version=None, pack_id=None,
              pack_version=None, headline="今天没跑成 · 参数未配置",
              gaps=["参数未配置(未提供 --k9-params 路径)"])
        got = store.load_k9_report(TRADE_DATE, db_path=db)
        assert got["listing_size"] is None
        assert got["params_package_version"] is None
        assert got["gaps"] == ["参数未配置(未提供 --k9-params 路径)"]

    def test_empty_stores_zero_not_null(self, db):
        """「今天没有」= 跑通了、结果为空 → **0**,可以被信任。"""
        _save(db, state="empty", listing_size=0, strict_count=0, relaxed_count=0,
              headline="今天没有")
        got = store.load_k9_report(TRADE_DATE, db_path=db)
        assert got["listing_size"] == 0
        assert got["state"] == "empty"

    def test_the_two_are_distinguishable_after_a_round_trip(self, db):
        _save(db, state="empty", listing_size=0)
        empty = store.load_k9_report(TRADE_DATE, db_path=db)
        _save(db, state="not_run", listing_size=None)
        not_run = store.load_k9_report(TRADE_DATE, db_path=db)
        assert empty["listing_size"] == 0 and not_run["listing_size"] is None


# ══════════════════════════════════════════════════════════════════════════
# ③ 旧 `reports` 表:写路径已删,历史行只读
# ══════════════════════════════════════════════════════════════════════════

class TestLegacyReportsTableIsReadOnly:
    def test_the_write_function_is_gone(self):
        """裁定 6 / §5.10:旧 `reports` 表**冻结只读**。⛔ 写路径不许再存在。"""
        assert not hasattr(store, "save_report"), (
            "`save_report` 是 K8 `reports` 表的写路径,S7 已物理删除 —— "
            "留着它等于让「只读留档」靠自觉维持")
        assert "save_report" not in store.__all__

    def test_historic_rows_are_still_readable(self, db, tmp_path):
        init_schema(db_path=db)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO reports (trade_date, report_date, generated_at, "
                "strategy_version, sentiment_json, sectors_json, candidates_json, markdown) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("20260724", "20260724", "2026-07-24T08:00:00+00:00", "v2.4.2",
                 json.dumps({"mood": "warm"}), "[]", json.dumps([{"ts_code": "600001.SH"}]),
                 "# 旧报告"),
            )
        got = store.load_report(date(2026, 7, 24), db_path=db)
        assert got["markdown"] == "# 旧报告"
        assert got["candidates"] == [{"ts_code": "600001.SH"}]
        assert store.latest_report_date(db_path=db) == "20260724"

    def test_reading_a_never_written_legacy_row_returns_none(self, db):
        assert store.load_report(date(2026, 7, 25), db_path=db) is None


class TestLLMJudgmentReadOnlyAfterRetirement:
    """`llm_judgments` 停写留档 —— 写函数早已物理删除,只读入口保留。"""

    def test_write_functions_are_gone_read_stays(self):
        for gone in ("save_llm_judgment", "delete_llm_judgments"):
            assert not hasattr(store, gone)
            assert gone not in store.__all__
        assert hasattr(store, "load_llm_judgments")

    def test_load_on_empty_table_returns_empty_list(self, db):
        assert store.load_llm_judgments(date(2026, 7, 20), db_path=db) == []
