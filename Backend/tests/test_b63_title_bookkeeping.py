import copy
from datetime import timedelta

import pytest

from neckline.k10 import pipeline, store, title_runtime
from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
from neckline.k10.title_triage import (
    TitleDTO, TitleTriageResult, TitleTriageProtocolError, normalize_reconcile_result,
)
from neckline.k10.v2_store import read_report
from neckline.k10.worker import run_once
from tests import test_v310_pipeline_e2e as e2e
from tests.test_b60_pool_filtering import edit_responses


def test_production_reconcile_bookkeeping_preserves_selected_sources():
    # Exact counts, indices, duplicate/rank defect and merge topology from the
    # paid 2026-09-10 response. Source content and reasons are anonymized.
    chosen = [2108, 799, 1200, 1679, 2557, 1106, 1696, 1161, 1391,
              1297, 1158, 1309, 2144, 632, 1097, 2619, 542]
    links = [(1116, 632), (571, 831), (1347, 2108), (2307, 600),
             (1475, 1869), (2177, 2098), (1697, 932), (2781, 158), (2353, 1237)]
    participants = set(chosen) | {i for pair in links for i in pair}
    for i in range(2782):
        if len(participants) == 577:
            break
        participants.add(i)
    titles = [TitleDTO(f"doc-{i:04}", 1, "fixture", None, "fixture title") for i in range(2782)]
    batch = [TitleTriageResult(t.document_id, 1, "candidate" if i in participants else "no_value",
                              "matter", "new", "batch judgement") for i, t in enumerate(titles)]
    selected = [{"i": i, "selectedRank": rank, "reason": f"selected reason {i}"}
                for rank, i in enumerate(chosen, 1)]
    raw = {"selectionComplete": True, "reviewedCount": 2782,
           "selected": selected + [{"i": 1106, "selectedRank": 17, "reason": ""}],
           "merged": [{"i": i, "into": target, "reason": "same event"} for i, target in links]}
    before = copy.deepcopy(raw)
    value = normalize_reconcile_result(raw, titles, batch, len(titles))
    assert raw == before
    assert value["selected"] == selected
    assert [(row["i"], row["into"]) for row in value["merged"]] == [(1116, 632), (1347, 2108)]
    assert set(value["notSelected"]) == participants - set(chosen) - {1116, 1347}
    # An unused grouping hint never promotes a new source or silently merges
    # it into an unselected target. Both remain separate audited non-selections.
    assert {571, 831} <= set(value["notSelected"])


@pytest.mark.parametrize("complete,count", [(False, 2), (True, True), (True, 99)])
def test_bookkeeping_tolerance_does_not_invent_completed_review(complete, count):
    titles = [TitleDTO(f"d{i}", 1, "fixture", None, "title") for i in range(2)]
    batch = [TitleTriageResult(t.document_id, 1, "candidate", "m", "s", "r") for t in titles]
    with pytest.raises(TitleTriageProtocolError):
        normalize_reconcile_result({"selectionComplete": complete, "reviewedCount": count,
                                    "selected": [], "merged": []}, titles, batch, 2)


def test_official_recovery_reuses_duplicate_paid_title_result_and_publishes(tmp_path, monkeypatch):
    original = title_runtime.normalize_reconcile_result
    def duplicate(value):
        if "selectionComplete" in value and value["selected"]:
            value["selected"].append({**value["selected"][0], "reason": ""})
    def old_strict(*args, **kwargs):
        raise TitleTriageProtocolError("全局标题输出 refIndex 重复")
    edit_responses(monkeypatch, duplicate)
    monkeypatch.setattr(title_runtime, "normalize_reconcile_result", old_strict)
    db, task_id, task, calls, _ = e2e._run(tmp_path, monkeypatch, v2=True)
    assert task.status == "failed" and calls.count("titleGlobal") == 2
    monkeypatch.setattr(title_runtime, "normalize_reconcile_result", original)
    scan = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]["scanId"]
    assert recover_scan(db_path=db, scan_id=scan, execution_config_id="b39-execution", execution_config_revision=1,
                        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan, db_path=db), now=e2e.RUN_AT) == task_id
    resumed = e2e._http_transport(monkeypatch, v2=True)
    done = run_once(db_path=db, task_id=task_id, worker_id="b63", lease_for=timedelta(minutes=5),
                    handlers=pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path/"parquet"),
                    clock=lambda: e2e.RUN_AT)
    assert done.status == "completed" and "titleGlobal" not in resumed and "titleBatch" not in resumed
    assert read_report(db_path=db)["eveningCards"]
