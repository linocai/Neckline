"""K8 退役采用物理删除，Git 承担历史追溯。"""

from __future__ import annotations

from pathlib import Path

from neckline.db import _RETIRED_TABLES

ROOT = Path(__file__).resolve().parent.parent


def test_k8_runtime_modules_and_scripts_are_physically_gone():
    paths = (
        ROOT / "neckline" / "legacy_k8.py",
        ROOT / "neckline" / "data" / "concept_data.py",
        ROOT / "neckline" / "review" / "handoff.py",
        ROOT / "neckline" / "review" / "research_artifact.py",
        ROOT / "scripts" / "backfill_concept.py",
        ROOT / "scripts" / "oneoff",
        ROOT / "packs",
    )
    for path in paths:
        assert not path.exists() or (path.is_dir() and not any(path.iterdir())), path


def test_retired_tables_cover_the_old_k8_surface():
    required = {
        "baskets", "basket_members", "basket_cards", "gate_evaluations",
        "strategy_versions", "reports", "positions", "inquiry_pool",
        "selection_packs", "auction_reports", "auction_verdicts",
        "selection_runs", "selection_directions", "selection_direction_events",
        "selection_llm_calls", "selection_search_calls",
    }
    assert required <= set(_RETIRED_TABLES)


def test_current_sources_do_not_import_deleted_modules():
    needles = ("legacy_k8", "concept_data", "review.handoff", "research_artifact")
    bad = []
    for path in list((ROOT / "neckline").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if any(f"import {name}" in text or f"from {name}" in text for name in needles):
            bad.append(str(path.relative_to(ROOT)))
    assert not bad
