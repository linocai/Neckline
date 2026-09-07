from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from neckline.k10 import store
from neckline.k10.schema import initialize_schema, rollback_schema, schema_version
from neckline.k10.types import OpportunityPublicationInput
from neckline.k10.windows import SHANGHAI


NOW = "2026-09-08T09:00:00+08:00"


def _publication_input(*, candidate_id: str, company_code: str, event_id: str, opportunity_key: str,
                       category: str = "primary", rank: int = 1, historical=None) -> OpportunityPublicationInput:
    comparison = {
        "summary": "冻结的事件比较", "differences": {"role": category, "priorityReason": "公开事实",
        "gap": "公司差异", "rankChangeConditions": "新证据", "twoDayReason": "两日催化"},
        "evidenceRefs": [], "rank": rank,
        "classification": {"kind": "initial", "opportunityKey": opportunity_key, "reason": "首发",
        "newFacts": "公开披露", "changedJudgment": None, "twoDayReason": "两日窗口", "relatedOpportunityId": None},
    }
    if historical is not None:
        comparison.update(historical)
    return OpportunityPublicationInput(candidate_id=candidate_id, company_code=company_code, event_id=event_id,
        event_revision=1, opportunity_key=opportunity_key, catalyst_stage="approval", category=category,
        comparison=comparison, evidence_refs=(), source_marker="morning")


def _publication_seed(path, *, count: int = 1, historical=None, category: str = "primary", rank: int = 1):
    initialize_schema(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES('SSE',?,?)", [("20260907", 1), ("20260908", 1), ("20260909", 1)])
    store.create_scan(scan_id="scan-publication", window_kind="morning", cutoff_at=NOW, config_id=None, config_revision=None,
                      status="completed", coverage={}, created_at=NOW, completed_at=NOW, db_path=path)
    store.append_event_revision(event_id="event-publication", stable_key="event-publication", headline="正式披露", event_kind="news",
                                facts={}, source_refs=[], supersedes_revision=None, created_at=NOW, db_path=path)
    inputs = []
    for number in range(count):
        company_code = f"3000{number + 1:02d}.SZ"
        candidate_id = f"candidate-publication-{number + 1}"
        item = _publication_input(candidate_id=candidate_id, company_code=company_code, event_id="event-publication",
                                  opportunity_key=f"opportunity-{number + 1}", category=category, rank=rank,
                                  historical=historical)
        store.create_candidate(candidate_id=candidate_id, scan_id="scan-publication", event_id="event-publication", event_revision=1,
                               company_code=company_code, comparison=item.comparison, evidence=[], created_at=NOW, db_path=path)
        inputs.append(item)
    return inputs


def _seed_observed_window(path):
    initialize_schema(path)
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            INSERT INTO k10_company_windows VALUES('window-1','300001.SZ','batch-1','2026-09-05','2026-09-08','2026-09-09','2026-09-08T09:30:00+08:00','2026-09-09T15:00:00+08:00','primary',NULL,'2026-09-07T21:00:00+08:00');
            INSERT INTO k10_observations VALUES('obs-1','candidate-1','action-1','2026-09-07T21:00:00+08:00');
            INSERT INTO k10_tasks VALUES('task-initial','analysis','analysis:obs-1','k10-v1.4','2026-09-07T21:00:00+08:00','{}','completed','analysis_ready',1,NULL,'{}','{}',NULL,NULL,'2026-09-07T21:00:00+08:00','2026-09-07T21:01:00+08:00');
            INSERT INTO k10_company_window_observations VALUES('window-1','obs-1','candidate-1','task-initial','2026-09-07T21:00:00+08:00');
            INSERT INTO k10_analysis_revisions VALUES('pro-1','obs-1',1,'pro','2026-09-07T21:00:00+08:00','{"frozenEvidenceRefs":[{"documentId":"doc-1","revision":1}]}','{"fullText":"正方"}','completed','2026-09-07T21:01:00+08:00');
            INSERT INTO k10_analysis_revisions VALUES('con-1','obs-1',1,'con','2026-09-07T21:00:00+08:00','{"frozenEvidenceRefs":[{"documentId":"doc-1","revision":1}]}','{"fullText":"反方"}','completed','2026-09-07T21:01:00+08:00');
            INSERT INTO k10_source_documents VALUES('doc-1','official','external-1',NULL,'2026-09-07T20:00:00+08:00');
            INSERT INTO k10_source_document_versions VALUES('doc-1',1,'hash-1','2026-09-07T20:00:00+08:00','exact','2026-09-07T20:01:00+08:00','正文',NULL,'v1','{}','2026-09-07T20:01:00+08:00');
            INSERT INTO k10_source_documents VALUES('doc-other','official','external-other',NULL,'2026-09-07T20:00:00+08:00');
            INSERT INTO k10_source_document_versions VALUES('doc-other',1,'hash-other','2026-09-07T20:00:00+08:00','exact','2026-09-07T20:01:00+08:00','他股正文',NULL,'v1','{}','2026-09-07T20:01:00+08:00');
        """)


def test_v3_migrates_v2_market_rows_without_loss_and_allows_explicit_anomaly(tmp_path):
    path = tmp_path / "v2.sqlite"
    initialize_schema(path)
    rollback_schema(path, target_version=2)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT MAX(version) FROM k10_schema_migrations").fetchone() == (2,)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO k10_market_day_fact_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("300001.SZ", "2026-09-08", 1, "available", 1, 2, 1, 2, 1, 2, 1, 1, None, "{}", "[]", NOW, NOW))
    assert initialize_schema(path) == 3
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT availability,open,high,close FROM k10_market_day_fact_revisions").fetchone() == ("available", 1.0, 2.0, 2.0)
    assert store.append_market_day_fact(company_code="300001.SZ", trade_date="2026-09-08", availability="anomaly",
                                        open_price=1, high_price=2, low_price=1, close_price=2, pre_close=1, limit_up_price=2,
                                        close_limit_up=True, touched_limit_up=True, source_refs=(), obtained_at=NOW, created_at=NOW,
                                        metadata={"anomalyReason": "source_conflict"}, db_path=path) == 2


def test_morning_report_versions_preserve_all_five_sections_and_no_overwrite(tmp_path):
    path = tmp_path / "morning.sqlite"
    initialize_schema(path)
    store.create_scan(scan_id="scan-morning", window_kind="morning", cutoff_at=NOW, config_id=None, config_revision=None,
                      status="completed", coverage={"coverageStatus": "complete"}, created_at=NOW, completed_at=NOW, db_path=path)
    groups = {section: [] for section in ("major_contrary", "thesis_changed", "continuing_or_expiring", "new", "needs_review")}
    groups["needs_review"] = [{"itemId": "item-1", "status": "unavailable", "content": {"summary": "待核"}}]
    first = store.append_morning_report(report_id="report-1", scan_id="scan-morning", cutoff_at=NOW, generated_at=NOW,
                                        status="partial", coverage={"coverageStatus": "partial", "coverageGaps": ["source"]},
                                        groups=groups, created_at=NOW, db_path=path)
    assert first["revision"] == 1 and first["groups"]["needs_review"][0]["section"] == "needs_review"
    replay = store.append_morning_report(report_id="report-1", scan_id="scan-morning", cutoff_at=NOW, generated_at=NOW,
                                         status="partial", coverage={"coverageStatus": "partial", "coverageGaps": ["source"]},
                                         groups=groups, created_at=NOW, db_path=path)
    assert replay["revision"] == 1
    changed = {section: list(items) for section, items in groups.items()}
    changed["needs_review"] = [{"itemId": "item-1", "status": "completed", "content": {"summary": "不同内容"}}]
    with pytest.raises(store.K10Conflict, match="项目内容不同"):
        store.append_morning_report(report_id="report-1", scan_id="scan-morning", cutoff_at=NOW, generated_at=NOW,
                                    status="partial", coverage={"coverageStatus": "partial", "coverageGaps": ["source"]},
                                    groups=changed, created_at=NOW, db_path=path)
    second = store.append_morning_report(report_id="report-2", scan_id="scan-morning", cutoff_at=NOW,
                                         generated_at="2026-09-08T09:01:00+08:00", status="completed", coverage={"coverageStatus": "complete"},
                                         groups={section: [] for section in groups}, created_at="2026-09-08T09:01:00+08:00", db_path=path)
    assert second["revision"] == 2
    assert [item["revision"] for item in store.list_morning_reports(db_path=path)] == [2, 1]


def test_analysis_request_is_intent_idempotent_and_requires_complete_parent(tmp_path):
    path = tmp_path / "analysis.sqlite"
    _seed_observed_window(path)
    # Retired V2 morning rows are retained for audit but cannot become a fictitious parent.
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO k10_analysis_revisions VALUES(?,?,?,?,?,?,?,?,?)",
                     ("legacy-morning", "obs-1", 9, "morning", NOW, "{}", "{}", "completed", NOW))
    with pytest.raises(store.K10Conflict, match="未关联"):
        store.create_analysis_request(request_id="unrelated", task_id="unrelated-task", company_window_id="window-1",
                                      kind="evidence_update", question=None, source_refs=[{"documentId": "doc-other", "revision": 1}],
                                      idempotency_key="unrelated", input_cutoff_at="2026-09-08T09:05:00+08:00", task_input_version="k10-v1.4",
                                      task_payload={}, task_budget={}, created_at=NOW, db_path=path)
    first = store.create_analysis_request(request_id="request-1", task_id="task-followup-1", company_window_id="window-1",
                                          kind="evidence_update", question=None, source_refs=[{"documentId": "doc-1", "revision": 1}],
                                          idempotency_key="intent-1", input_cutoff_at="2026-09-08T09:05:00+08:00", task_input_version="k10-v1.4",
                                          task_payload={"configId": "cfg", "configRevision": 3}, task_budget={"costLimit": None}, created_at=NOW, db_path=path)
    assert first["replayed"] is False and first["parentRevision"] == 1 and first["globalRevision"] == first["targetRevision"] == 2
    assert [item["revision"] for item in store.list_analysis_chain(company_window_id="window-1", db_path=path)["items"]] == [1, 2]
    replay = store.create_analysis_request(request_id="ignored", task_id="ignored-task", company_window_id="window-1",
                                           kind="evidence_update", question=None, source_refs=[{"documentId": "doc-1", "revision": 1}],
                                           idempotency_key="intent-1", input_cutoff_at="2026-09-08T09:09:00+08:00", task_input_version="changed",
                                           task_payload={}, task_budget={}, created_at="2026-09-08T09:09:00+08:00", db_path=path)
    assert replay["replayed"] is True and replay["requestId"] == "request-1" and replay["inputCutoffAt"] == "2026-09-08T09:05:00+08:00"
    assert store.get_analysis_request_by_idempotency_key(idempotency_key="intent-1", db_path=path)["taskId"] == "task-followup-1"
    with pytest.raises(store.K10Conflict, match="尚未完整完成"):
        store.create_analysis_request(request_id="request-2", task_id="task-followup-2", company_window_id="window-1",
                                      kind="user_question", question="这会怎样？", source_refs=[], idempotency_key="intent-2",
                                      input_cutoff_at="2026-09-08T09:10:00+08:00", task_input_version="k10-v1.4", task_payload={},
                                      task_budget={}, created_at=NOW, db_path=path)


def test_publish_rejects_malformed_historical_context_before_any_batch_write(tmp_path):
    path = tmp_path / "invalid-history.sqlite"
    malformed = {"historicalCases": [{}], "historicalCoverage": {}}
    inputs = _publication_seed(path, historical=malformed)
    with pytest.raises(ValueError, match="历史案例覆盖面无效"):
        store.publish_opportunities(batch_id="batch-invalid-history", scan_id="scan-publication", publication_kind="morning",
                                    inputs=inputs, db_path=path, clock=lambda: datetime(2026, 9, 8, 9, 29, tzinfo=SHANGHAI))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_publication_batches").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM k10_publication_samples").fetchone() == (0,)


def test_publish_rejects_two_primaries_for_one_event_before_any_batch_write(tmp_path):
    path = tmp_path / "two-primary.sqlite"
    inputs = _publication_seed(path, count=2)
    with pytest.raises(ValueError, match="至多一个 primary"):
        store.publish_opportunities(batch_id="batch-two-primary", scan_id="scan-publication", publication_kind="morning",
                                    inputs=inputs, db_path=path, clock=lambda: datetime(2026, 9, 8, 9, 29, tzinfo=SHANGHAI))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_publication_batches").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM k10_company_windows").fetchone() == (0,)


def test_publish_allows_primary_global_ranks_from_separate_events(tmp_path):
    path = tmp_path / "global-primary-rank.sqlite"
    first = _publication_seed(path)
    second = _publication_input(candidate_id="candidate-event-two", company_code="300099.SZ", event_id="event-two",
                                opportunity_key="opportunity-event-two", category="primary", rank=2)
    store.append_event_revision(event_id="event-two", stable_key="event-two", headline="另一正式披露", event_kind="news",
                                facts={}, source_refs=[], supersedes_revision=None, created_at=NOW, db_path=path)
    store.create_candidate(candidate_id=second.candidate_id, scan_id="scan-publication", event_id="event-two", event_revision=1,
                           company_code=second.company_code, comparison=second.comparison, evidence=[], created_at=NOW, db_path=path)
    batch = store.publish_opportunities(batch_id="batch-global-primary-rank", scan_id="scan-publication", publication_kind="morning",
                                        inputs=[*first, second], db_path=path,
                                        clock=lambda: datetime(2026, 9, 8, 9, 29, tzinfo=SHANGHAI))
    assert batch.sample_count == 2
    assert [sample["rank"] for sample in store.list_publication_samples(batch_id=batch.batch_id, db_path=path)] == [1, 2]


@pytest.mark.parametrize("rank", [1, 2])
def test_publish_allows_tied_event_candidates_at_global_rank(rank, tmp_path):
    path = tmp_path / f"tied-rank-{rank}.sqlite"
    inputs = _publication_seed(path, count=2, category="tied", rank=rank)
    batch = store.publish_opportunities(batch_id=f"batch-tied-{rank}", scan_id="scan-publication", publication_kind="morning",
                                        inputs=inputs, db_path=path, clock=lambda: datetime(2026, 9, 8, 9, 29, tzinfo=SHANGHAI))
    samples = store.list_publication_samples(batch_id=batch.batch_id, db_path=path)
    assert len(samples) == 2 and {sample["category"] for sample in samples} == {"tied"}
    assert {sample["rank"] for sample in samples} == {rank}


def test_morning_targets_project_legacy_candidate_actions_when_no_snapshot(tmp_path):
    path = tmp_path / "legacy-action-target.sqlite"
    inputs = _publication_seed(path)
    store.publish_opportunities(batch_id="batch-target", scan_id="scan-publication", publication_kind="morning",
                                inputs=inputs, db_path=path, clock=lambda: datetime(2026, 9, 8, 9, 29, tzinfo=SHANGHAI))
    store.append_candidate_action(action_id="legacy-skip", candidate_id="candidate-publication-1", action="skip",
                                  idempotency_key="legacy-skip", reason="旧客户端明确略过",
                                  created_at="2026-09-08T09:20:00+08:00", db_path=path)
    targets = store.list_morning_report_targets(as_of=datetime(2026, 9, 8, 9, 25, tzinfo=SHANGHAI), scan_id="scan-publication", db_path=path)
    assert targets[0]["selectionState"] == "skipped"
