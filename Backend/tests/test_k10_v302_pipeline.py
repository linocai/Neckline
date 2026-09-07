from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from neckline.k10 import pipeline
from neckline.k10.types import Task
from neckline.k10.worker import TaskContext


NOW = datetime.fromisoformat("2026-09-08T09:00:00+08:00")
REF = {"documentId": "doc-1", "revision": 1}
REF_A = {"documentId": "delta-a", "revision": 1}
REF_B = {"documentId": "old-b", "revision": 2}
INDEPENDENT = {"documentId": "verify-a", "revision": 3}
WITHDRAWAL = {"documentId": "withdrawal", "revision": 4}


def _target(*, opportunity_id: str, state: str, d1: str = "2026-09-08", d2: str = "2026-09-09",
            rank: int = 1, is_new: bool = False, refs=None, lifecycle=None):
    return {"opportunityId": opportunity_id, "companyWindowId": f"window-{opportunity_id}",
            "candidateId": f"candidate-{opportunity_id}", "companyCode": "300001.SZ",
            "d1TradeDate": d1, "d2TradeDate": d2, "selectionState": "unhandled",
            "state": state, "sourceRefs": refs if refs is not None else [REF], "displayRank": rank,
            "availableAt": "2026-09-07T15:10:00+08:00", "isNew": is_new,
            "lifecycle": lifecycle if lifecycle is not None else []}


def test_morning_target_projection_uses_target_rank_scoped_refs_and_one_expiry(monkeypatch, tmp_path):
    targets = [
        _target(opportunity_id="active", state="active", rank=3, is_new=True, refs=[REF_A]),
        _target(opportunity_id="withdrawn", state="withdrawn", rank=1, refs=[REF_B], lifecycle=[
            {"kind": "withdrawn", "sourceRefs": [WITHDRAWAL]},
        ]),
        _target(opportunity_id="expired", state="expired", d1="2026-09-04", d2="2026-09-07", rank=2),
        _target(opportunity_id="old", state="expired", d1="2026-09-01", d2="2026-09-02", rank=4),
    ]
    monkeypatch.setattr(pipeline.store, "list_morning_report_targets", lambda **_: targets)
    monkeypatch.setattr(pipeline.store, "get_candidate", lambda *, candidate_id, **_: {"eventId": f"event-{candidate_id}"})
    monkeypatch.setattr(pipeline, "prev_trading_day", lambda *_args, **_kwargs: date(2026, 9, 7))

    actual = pipeline._morning_target_items(
        scan_id="scan-current", cutoff_at=NOW, db_path=tmp_path, morning_refs=[{"documentId": "broadcast", "revision": 1}],
        review_matches=[{"candidateId": "candidate-active", "morningEvidenceRefs": [REF_A],
                         "independentVerificationRefs": [INDEPENDENT]}],
    )

    assert [item["opportunityId"] for item in actual] == ["active", "withdrawn", "expired"]
    assert [item["displayRank"] for item in actual] == [3, 1, 2]
    assert actual[0]["isNew"] is True and actual[0]["reviewMatched"] is True
    assert actual[0]["morningEvidenceRefs"] == [REF_A]
    assert actual[0]["independentVerificationRefs"] == [INDEPENDENT]
    assert actual[1]["morningEvidenceRefs"] == [REF_B]
    assert actual[1]["independentVerificationRefs"] == [WITHDRAWAL]
    assert actual[2]["justExpired"] is True


def test_morning_review_match_keeps_independent_sources_separate_from_event_sources():
    event = SimpleNamespace(canonical_key="event-key", source_refs=(SimpleNamespace(document_id="event", revision=1),))
    item = SimpleNamespace(event=event, mapping=SimpleNamespace(company_code="300001.SZ"),
                           verification=SimpleNamespace(evidence_refs=(
                               SimpleNamespace(document_id="event", revision=1),
                               SimpleNamespace(document_id="verification", revision=2),
                           )))
    run = SimpleNamespace(candidates=(item,), deferred=(), metadata_pending=(), excluded=(), updates=())
    actual = pipeline._morning_review_matches(
        run=run, existing=[{"candidateId": "candidate-1", "companyCode": "300001.SZ", "eventId": "other"}],
    )
    assert actual == [{"candidateId": "candidate-1", "eventId": pipeline._event_id("event-key"),
                       "morningEvidenceRefs": [{"documentId": "event", "revision": 1}],
                       "independentVerificationRefs": [{"documentId": "verification", "revision": 2}]}]


def test_source_failure_still_appends_all_formal_targets_as_needs_review(monkeypatch, tmp_path):
    targets = [_target(opportunity_id="kept", state="active"), _target(opportunity_id="withdrawn", state="withdrawn")]
    monkeypatch.setattr(pipeline, "_morning_target_items", lambda **_: [
        {**item, "lifecycle": item["state"], "morningEvidenceRefs": [REF],
         "independentVerificationRefs": [REF], "reviewMatched": False, "justExpired": False}
        for item in targets
    ])
    captured = {}
    monkeypatch.setattr(pipeline.store, "append_morning_report", lambda **kwargs: captured.update(kwargs) or {
        "reportId": kwargs["report_id"], "revision": 1,
    })
    parent = TaskContext(Task("parent", "morning_scan", "running", 1, "worker", None, {}),
                         {}, {}, "cfg@1", NOW.isoformat(), Path(tmp_path / "temp.sqlite"), Event())
    report, child_ids, state = pipeline._assemble_morning_report(
        parent=parent, scan_id="scan-morning", cutoff_at=NOW,
        configuration={"taskPolicies": {"morning": {"maxAttempts": 1, "costLimit": None}}},
        config_id="cfg", config_revision=1, source_status="unavailable", morning_refs=[], generated_at=NOW,
    )
    assert report["revision"] == 1 and child_ids == [] and state == "completed"
    assert captured["status"] == "partial"
    assert captured["coverage"]["coverageStatus"] == "partial"
    assert captured["coverage"]["needsReviewCount"] == 2
    assert captured["coverage"]["failedItemCount"] == 2
    assert len(captured["groups"]["needs_review"]) == 2
    assert not captured["groups"]["major_contrary"]
