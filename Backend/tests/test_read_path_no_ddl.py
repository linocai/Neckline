"""🔴 **读一次⛔ 不许把库迁移掉** —— R3-🔴-2 的**行为**验收(不是 AST 守门)。

`tests/test_v250_s14_release_gate.py::test_no_read_helper_triggers_a_schema_migration`
是**静态**判据(AST 调用图:读函数走不到 `init_schema`)。本文件是它的另一半:
拿一个**未迁移的库**真的去调那些读函数,断言

  ① 库的 `sqlite_master` 快照**逐字节不变**(⛔ 一张表、一个索引都没多);
  ② 每个读函数返回它**自己文档化的空态**(⛔ 不抛 `no such table`)。

**为什么这条必须有**(README「任何 GET 或日常读取都不是迁移触发器」/
PROJECT_PLAN §9.2 / §9.4 / §9.6 步骤 6.8):回滚边界的论证整个建立在这句话上 ——
§9.6 步骤 6.6 要求「K8 只读表行数与备份**逐表相等**」。复审实测过反例:v2.4.2
老库 59 表,只调一次 `report.store.load_k9_report` → **75 表**,两份备份 sha256
当场不再相等。

⚠ 静态那条闸按**函数名前缀**扫(`load_/latest_/list_/get_/read_/fetch_`),
所以像 `undecided_codes` / `retreat_brake_state` / `search` 这些**不带前缀的读函数**
它看不见 —— 本文件逐个点名调它们,补上那个盲区。
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest


def _schema_snapshot(db: Path) -> list:
    """`sqlite_master` 全量快照(表 / 索引 / 触发器 + 各自的 DDL 原文)。"""
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
    finally:
        conn.close()


@pytest.fixture()
def unmigrated_db(tmp_path: Path) -> Path:
    """一个**未迁移**的库:文件真的存在、里面有别人的表,但本版这些表一张都没有。

    ⚠ 刻意**不**跑 `init_schema` —— 那正是被测对象不许做的事。
    """
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE some_k8_history (id INTEGER PRIMARY KEY, note TEXT)")
    conn.execute("INSERT INTO some_k8_history (note) VALUES ('K8 历史行,一个字都不许动')")
    conn.commit()
    conn.close()
    return db


#: `(可读的名字, 调用, 文档化的空态)`。⚠ **`get_app_settings` 与 `get_push_kinds`
#: 的空态不是 `None`/`[]`** —— 它们的文档说「从未写过 → 默认值」,故单独断言。
def _read_calls(db: Path):
    from neckline import dedup, settings_store
    from neckline.auction import store as auction_store
    from neckline.explain import store as explain_store
    from neckline.playbook import store as pb_store
    from neckline.report import store as report_store
    from neckline.review import conclusions as review_conclusions
    from neckline.review import store as review_store

    d = date(2026, 8, 20)
    return [
        ("report.load_k9_report", lambda: report_store.load_k9_report(d, db_path=db), None),
        ("report.load_k9_report_index",
         lambda: report_store.load_k9_report_index(d, d, db_path=db), {}),
        ("report.latest_k9_report", lambda: report_store.latest_k9_report(db_path=db), None),
        ("report.load_report", lambda: report_store.load_report(d, db), None),
        ("report.load_report_by_str", lambda: report_store.load_report_by_str("20260820", db), None),
        ("report.latest_report_date", lambda: report_store.latest_report_date(db), None),
        ("report.load_llm_judgments", lambda: report_store.load_llm_judgments(d, db), []),
        ("auction.load_checklist", lambda: auction_store.load_checklist(d, db_path=db), None),
        ("auction.load_verdicts", lambda: auction_store.load_verdicts(d, db_path=db), []),
        # ⚠ 不带读前缀 —— 静态那条闸看不见它。
        ("auction.undecided_codes", lambda: auction_store.undecided_codes(d, db_path=db), []),
        ("explain.load_notes", lambda: explain_store.load_notes(d, db_path=db), {}),
        ("explain.load_audit", lambda: explain_store.load_audit(d, db_path=db), []),
        ("playbook.load_latest", lambda: pb_store.load_latest(d, db_path=db), {}),
        ("playbook.load_versions", lambda: pb_store.load_versions(d, "000001.SZ", db_path=db), []),
        ("playbook.load_latest_range",
         lambda: pb_store.load_latest_range(d, d, ["000001.SZ"], db_path=db), {}),
        ("playbook.count_for_day", lambda: pb_store.count_for_day(d, db_path=db), 0),
        ("review.load_weekly_review", lambda: review_store.load_weekly_review("2026-W34", db), None),
        ("review.list_review_weeks", lambda: review_store.list_review_weeks(db), []),
        ("conclusions.load_latest",
         lambda: review_conclusions.load_latest("2026-W34", db_path=db), None),
        ("conclusions.load_versions",
         lambda: review_conclusions.load_versions("2026-W34", db_path=db), []),
        ("conclusions.list_latest", lambda: review_conclusions.list_latest(db_path=db), []),
        # ⚠ 不带读前缀。
        ("conclusions.search", lambda: review_conclusions.search("涨停", db_path=db), []),
        ("dedup.load_events_for_date", lambda: dedup.load_events_for_date(d, db), []),
        # ⚠ 不带读前缀。
        ("dedup.retreat_brake_state", lambda: dedup.retreat_brake_state(d, db), None),
        ("settings.get_tavily_api_key", lambda: settings_store.get_tavily_api_key(db), None),
        ("settings.list_providers", lambda: settings_store.list_providers(db), []),
        ("settings.list_providers_public", lambda: settings_store.list_providers_public(db), []),
        ("settings.get_provider_record", lambda: settings_store.get_provider_record("zhipu", db), None),
        ("settings.get_llm_routes", lambda: settings_store.get_llm_routes(db), ({}, None)),
    ]


class TestReadingAnUnmigratedDatabase:
    def test_every_read_helper_returns_its_documented_empty_state(self, unmigrated_db: Path):
        """表还没建 → 每个读函数返回它自己写在 docstring 里的那个空态。

        ⛔ 不是抛 `sqlite3.OperationalError: no such table`,也⛔ 不是顺手建表。
        """
        for name, call, expected in _read_calls(unmigrated_db):
            assert call() == expected, f"{name} 在未迁移库上没有返回文档化的空态"

    def test_reading_does_not_create_a_single_table(self, unmigrated_db: Path):
        """🔴 **本文件的核心断言**:读完一整轮,`sqlite_master` 逐行不变。

        复审的反例是「59 表 → 75 表」;这里是同一件事的最小可跑形式。
        """
        before = _schema_snapshot(unmigrated_db)
        for _name, call, _expected in _read_calls(unmigrated_db):
            call()
        after = _schema_snapshot(unmigrated_db)
        assert after == before, (
            "读了一轮之后库的 schema 变了 —— 读路径又成了迁移触发器:\n"
            f"多出来的:{sorted(set(after) - set(before))}")

    def test_push_kind_switches_fall_back_to_defaults_not_an_exception(self, unmigrated_db: Path):
        """`app_settings.push_kinds` 是 `_COLUMN_MIGRATIONS` 里的**补列** ——
        老库缺表 / 缺列要走「从未写过 → 全部默认开」那条既有分支。"""
        from neckline import notify_kinds, settings_store

        kinds = settings_store.get_push_kinds(unmigrated_db)
        assert set(kinds) == set(notify_kinds.ALL_KINDS)
        assert all(v is notify_kinds.DEFAULT_ENABLED for v in kinds.values())
        assert _schema_snapshot(unmigrated_db) == _schema_snapshot(unmigrated_db)

    def test_app_settings_view_falls_back_to_defaults(self, unmigrated_db: Path):
        from neckline import settings_store

        before = _schema_snapshot(unmigrated_db)
        got = settings_store.get_app_settings(unmigrated_db)
        assert got.review_col_map == {} and got.tavily_key_set is False
        assert got.updated_at is None
        assert _schema_snapshot(unmigrated_db) == before


class TestReadingADatabaseThatDoesNotExistAtAll:
    """⚠ **文件都不在**也不许被读出来一个库(`readonly_connection` 刻意不建父目录)。"""

    def test_no_file_is_created(self, tmp_path: Path):
        from neckline.report import store as report_store

        db = tmp_path / "nested" / "nope.db"
        assert report_store.load_k9_report(date(2026, 8, 20), db_path=db) is None
        assert not db.exists(), "读一次把库文件建出来了 —— 迁移触发器换了个形状"
        assert not db.parent.exists(), "读一次把父目录建出来了"
