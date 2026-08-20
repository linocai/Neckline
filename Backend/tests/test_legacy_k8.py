"""S13 · K8 历史数据只读追溯入口的行为判据(裁定 6,PROJECT_PLAN §5.12 / §6.13)。

结构判据(模块里只有 SELECT / 写方法 405)在 `test_v250_s11_s13_guard.py`。

锁三件事:
  ① **四态分开说**:表不在(`available=False`)/ 表在但一行没有(从没跑过 K8)/
     有历史但不是那天(`found=False`)/ 有;
  ② **只读**:调完之后源库的行数、mtime、schema 一字不变,且**不会凭空造库**;
  ③ 返回的是 **K8 的语义**,⛔ 没有被翻译成 K9 的字段名。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from neckline import legacy_k8

_K9_FIELD_NAMES = ("pattern", "seatKind", "seat_kind", "firstResistance",
                   "first_resistance", "primaryPattern", "invalidation")


def _seed_k8(db: Path) -> None:
    """建 K8 的三张表并铺一天的篮子(⚠ 这是**历史留档**的形状,不是 K9 的)。"""
    from neckline.db import init_schema

    init_schema(db_path=db)
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS baskets (
          id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT NOT NULL,
          basket_key TEXT NOT NULL, name TEXT NOT NULL, driver TEXT NOT NULL,
          driver_kind TEXT NOT NULL, tier INTEGER NOT NULL, pack_version TEXT NOT NULL,
          engine_api_version INTEGER NOT NULL, charter_version TEXT NOT NULL,
          via TEXT NOT NULL DEFAULT 'auto', evidence_status TEXT NOT NULL DEFAULT 'ok',
          selection_run_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS basket_members (
          id INTEGER PRIMARY KEY AUTOINCREMENT, basket_id INTEGER NOT NULL,
          ts_code TEXT NOT NULL, role_llm TEXT NOT NULL, role_mech TEXT,
          role_conflict INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL,
          is_primary INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS basket_cards (
          id INTEGER PRIMARY KEY AUTOINCREMENT, basket_id INTEGER NOT NULL,
          version INTEGER NOT NULL, card_json TEXT NOT NULL, stop_pct REAL,
          take_profit_retrace REAL, charter_version TEXT, pack_version TEXT,
          engine_api_version INTEGER, created_at TEXT NOT NULL);
        """)
        conn.execute(
            "INSERT INTO baskets (id, trade_date, basket_key, name, driver, driver_kind, "
            "tier, pack_version, engine_api_version, charter_version, created_at) "
            "VALUES (1,'20260724','abc','储能','政策催化','policy',1,'C1',3,'K1','t')")
        conn.executemany(
            "INSERT INTO basket_members (basket_id, ts_code, role_llm, reason, created_at) "
            "VALUES (?,?,?,?,?)",
            [(1, "600001.SH", "leader", "龙头", "t"), (1, "600002.SH", "core", "中军", "t")])
        conn.executemany(
            "INSERT INTO basket_cards (basket_id, version, card_json, created_at) "
            "VALUES (?,?,?,?)",
            [(1, 1, json.dumps({"note": "D0 原判"}), "t"),
             (1, 2, json.dumps({"note": "D+1 追加"}), "t")])
        conn.commit()
    finally:
        conn.close()


class TestThreeStatesAreDistinct:
    def test_no_k8_tables_is_unavailable_not_empty(self, tmp_path):
        """⚠ 这是**外部库 / 指错路径**的情形 —— 全新 Neckline 库里 `baskets` 表是**在**的
        (裁定 6:表保留只读),那一态见 `test_empty_table_says_never_ran_k8_not_wrong_day`。"""
        db = tmp_path / "fresh.db"
        sqlite3.connect(str(db)).close()
        out = legacy_k8.load_baskets(date(2026, 7, 24), db_path=db)
        assert out["available"] is False and out["found"] is False
        assert "baskets" in out["reason"]

    def test_has_history_but_not_that_day_is_found_false(self, tmp_path):
        db = tmp_path / "k8.db"
        _seed_k8(db)
        out = legacy_k8.load_baskets(date(2026, 7, 23), db_path=db)
        assert out["available"] is True and out["found"] is False
        assert out["overview"]["basketCount"] == 1
        assert out["baskets"] == []
        assert "但不是这一天" in out["reason"]

    def test_empty_table_says_never_ran_k8_not_wrong_day(self, tmp_path):
        """🔴 第二态:表在、一行没有 = 这个库**从没跑过 K8**(全新 Neckline 库就是
        这样)。它与「跑过、只是不是这一天」⛔ 必须说不同的话 —— 两者要人做的
        下一步完全不同。"""
        from neckline.db import init_schema

        db = tmp_path / "fresh_schema.db"
        init_schema(db_path=db)
        out = legacy_k8.load_baskets(date(2026, 7, 24), db_path=db)
        assert out["available"] is True and out["found"] is False
        assert out["overview"]["basketCount"] == 0
        assert "从没跑过 K8" in out["reason"]

    def test_that_day_returns_baskets_members_and_latest_card(self, tmp_path):
        db = tmp_path / "k8.db"
        _seed_k8(db)
        out = legacy_k8.load_baskets(date(2026, 7, 24), db_path=db)
        assert out["available"] is True and out["found"] is True
        (b,) = out["baskets"]
        assert b["name"] == "储能" and b["tier"] == 1
        assert [m["tsCode"] for m in b["members"]] == ["600001.SH", "600002.SH"]
        assert b["latestCard"]["version"] == 2, "只给最新版卡"
        assert b["latestCard"]["card"] == {"note": "D+1 追加"}

    def test_no_date_returns_the_overview_only(self, tmp_path):
        db = tmp_path / "k8.db"
        _seed_k8(db)
        out = legacy_k8.load_baskets(None, db_path=db)
        assert out["overview"]["firstDate"] == out["overview"]["lastDate"] == "20260724"
        assert out["baskets"] == [] and out["date"] is None

    def test_a_missing_database_file_is_not_created(self, tmp_path):
        """🔴 `mode=ro` 连接:库不存在就报读不开,⛔ 不像可写连接那样凭空造一个空库
        (那会在 whynotme / 运维手滑指错路径时留下一个假的「Neckline 库」)。"""
        db = tmp_path / "does-not-exist.db"
        out = legacy_k8.load_baskets(None, db_path=db)
        assert out["available"] is False
        assert not db.exists()


class TestReadOnly:
    def test_reading_changes_nothing_in_the_source_database(self, tmp_path):
        db = tmp_path / "k8.db"
        _seed_k8(db)
        before_size = db.stat().st_size
        conn = sqlite3.connect(str(db))
        before = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("baskets", "basket_members", "basket_cards")
        }
        before_schema = sorted(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        conn.close()

        legacy_k8.load_baskets(date(2026, 7, 24), db_path=db)
        legacy_k8.load_baskets(None, db_path=db)

        conn = sqlite3.connect(str(db))
        after = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("baskets", "basket_members", "basket_cards")
        }
        after_schema = sorted(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        conn.close()
        assert after == before
        assert after_schema == before_schema, "⛔ 只读入口不许建表(它绝不调 init_schema)"
        assert db.stat().st_size == before_size


class TestK8SemanticsAreNotTranslated:
    def test_k9_field_names_never_appear_in_the_payload(self, tmp_path):
        """⚠ `tier` / `driver` / `roleLlm` 对 K9 **没有意义**,⛔ 不许被翻译成
        `pattern` / `seatKind` / `firstResistance` —— 那会让一份 K8 留档看起来
        像一份 K9 清单,进而被谁拿去算成绩。"""
        db = tmp_path / "k8.db"
        _seed_k8(db)
        blob = json.dumps(legacy_k8.load_baskets(date(2026, 7, 24), db_path=db),
                          ensure_ascii=False)
        for name in _K9_FIELD_NAMES:
            assert name not in blob, f"K8 留档里冒出了 K9 的字段名 {name!r}"

    def test_unparseable_card_json_is_returned_raw_not_swallowed(self, tmp_path):
        db = tmp_path / "k8.db"
        _seed_k8(db)
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("UPDATE basket_cards SET card_json='{broken' WHERE version=2")
            conn.commit()
        finally:
            conn.close()
        out = legacy_k8.load_baskets(date(2026, 7, 24), db_path=db)
        assert out["baskets"][0]["latestCard"]["card"] == "{broken", \
            "⛔ 解不出不许吞成 {} —— 追溯要看的正是那份原文"
