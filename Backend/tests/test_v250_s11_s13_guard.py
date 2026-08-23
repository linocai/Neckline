"""复盘、成绩线、研究边界与已删除概念链的结构守门。"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from tests import guard_scan

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "neckline"
REVIEW = PKG / "review"
SCORECARD = PKG / "scorecard"
REVIEW_FILES = sorted(REVIEW.glob("*.py"))
SCORECARD_FILES = sorted(SCORECARD.glob("*.py"))


def _code(path: Path) -> str:
    return guard_scan.code_without_docstrings(path)


def test_scanner_sees_only_current_modules():
    assert {path.name for path in REVIEW_FILES} == {
        "__init__.py", "bindery.py", "cashflow.py", "conclusions.py", "material.py",
        "parse.py", "reconcile.py", "store.py",
    }
    assert {path.name for path in SCORECARD_FILES} == {
        "__init__.py", "coverage.py", "listing.py", "store.py",
    }


@pytest.mark.parametrize("path", REVIEW_FILES, ids=lambda p: p.name)
def test_review_layer_has_no_llm(path):
    for banned in ("neckline.llm", "neckline.search", "openai", "anthropic"):
        assert not guard_scan.imports_any(path, banned)


def test_cashflow_has_no_account_level_total():
    from neckline.review.cashflow import CashFlowSummary

    assert [field.name for field in dataclasses.fields(CashFlowSummary)] == [
        "week", "transfer_in", "transfer_out", "dividend", "tax", "other",
        "other_event_count", "trading_pnl", "event_count",
    ]
    src = _code(REVIEW / "cashflow.py")
    for banned in ("account_net", "net_change", "grand_total", "combined"):
        assert banned not in src


@pytest.mark.parametrize("path", SCORECARD_FILES, ids=lambda p: p.name)
def test_scorecards_never_import_review_or_trades(path):
    assert not guard_scan.imports_any(path, "neckline.review")
    src = _code(path)
    for word in ("round_trip", "交割单", "realized_pnl", "cash_flow"):
        assert word not in src


@pytest.mark.parametrize("path", REVIEW_FILES, ids=lambda p: p.name)
def test_review_never_imports_or_writes_scorecards(path):
    assert not guard_scan.imports_any(path, "neckline.scorecard")
    src = _code(path)
    for stmt in ("INSERT INTO k9_", "UPDATE k9_", "DELETE FROM k9_", "DROP TABLE k9_"):
        assert stmt not in src


def test_conclusions_remain_append_only():
    src = _code(REVIEW / "conclusions.py")
    for stmt in ("UPDATE ", "DELETE FROM", "INSERT OR REPLACE", "DROP TABLE", "ALTER TABLE"):
        assert stmt not in src
    ddl = (PKG / "db.py").read_text(encoding="utf-8")
    block = ddl.split("CREATE TABLE IF NOT EXISTS review_conclusions")[1].split(");")[0]
    assert "PRIMARY KEY (week, version)" in block


def test_production_never_imports_research_laboratory():
    bad = [str(path.relative_to(ROOT)) for path in PKG.rglob("*.py")
           if guard_scan.imports_any(path, "whynotme")]
    assert not bad


def test_concept_chain_is_physically_deleted():
    assert not (PKG / "data" / "concept_data.py").exists()
    assert not (ROOT / "scripts" / "backfill_concept.py").exists()
    source = "\n".join(_code(path) for path in [
        ROOT / "scripts" / "daily_update.py", PKG / "data" / "tushare_client.py",
    ])
    for token in ("update_concept_boards", "ts_ths_index", "ts_ths_member", "ts_ths_daily"):
        assert token not in source


def test_bindery_reads_are_bounded_and_not_in_overview():
    app = _code(PKG / "api" / "app.py")
    overview = app.split("def get_review_overview")[1].split("\ndef ")[0]
    assert "bind_week" not in overview and "bindery" not in overview
    bindery = _code(REVIEW / "bindery.py")
    assert "get_multi_stock_history" in bindery
    assert "get_stock_history" not in bindery
    for banned in ("get_market_slice", "scan_table_range"):
        assert banned not in bindery
