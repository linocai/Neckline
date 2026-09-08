from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from neckline.k10 import store
from neckline.k10.schema import initialize_schema, schema_version
from neckline.k10.types import OpportunityPublicationInput
from neckline.k10.windows import SHANGHAI


def _calendar(path: Path, *days: tuple[str, int]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES ('SSE',?,?)", days)


def _candidate(path: Path, *, suffix: str, scan_id: str, company: str = "300001.SZ") -> tuple[str, str]:
    store.create_scan(scan_id=scan_id, window_kind="evening", cutoff_at="2026-09-07T21:00:00+08:00",
                      config_id=None, config_revision=None, status="completed", coverage={},
                      created_at="2026-09-07T21:01:00+08:00", completed_at="2026-09-07T21:01:00+08:00", db_path=path)
    event_id=f"event-{suffix}"
    event=store.append_event_revision(event_id=event_id, stable_key=f"stable-{suffix}", headline="公告", event_kind="news",
                                      facts={}, source_refs=[], supersedes_revision=None, created_at="2026-09-07T21:01:00+08:00", db_path=path)
    candidate_id=f"candidate-{suffix}"
    store.create_candidate(candidate_id=candidate_id, scan_id=scan_id, event_id=event.event_id, event_revision=event.revision,
                           company_code=company, comparison={"rank": 1}, evidence=[], created_at="2026-09-07T21:01:00+08:00", db_path=path)
    return candidate_id,event_id


def _input(candidate_id: str, event_id: str, *, key: str, company: str = "300001.SZ", stage: str = "approval", source_marker: str = "morning") -> OpportunityPublicationInput:
    return OpportunityPublicationInput(candidate_id=candidate_id, company_code=company, event_id=event_id,
        event_revision=1, opportunity_key=key, catalyst_stage=stage, category="primary", comparison={
            "summary": "完整比较", "differences": {"role": "primary", "priorityReason": "资料", "gap": "差异", "rankChangeConditions": "反证条件", "twoDayReason": "新事实"},
            "evidenceRefs": [], "rank": 1, "classification": {"kind": "independent", "opportunityKey": key, "reason": "新催化", "newFacts": "资料", "changedJudgment": "新判断", "twoDayReason": "新事实", "relatedOpportunityId": None}},
        evidence_refs=(), source_marker=source_marker)


def test_schema_v1_migrates_to_v2_without_partial_tables(tmp_path):
    path=tmp_path/"v1.sqlite"
    initialize_schema(path)
    assert schema_version(path) == 5
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='k10_opportunities'").fetchone()
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='k10_plan_revisions'").fetchone() is None


def test_existing_v1_schema_forwards_to_v2_in_one_controlled_transaction(tmp_path):
    from neckline.k10 import schema
    path=tmp_path/"old-v1.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE k10_schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        schema._apply_v1(conn)
        conn.execute("INSERT INTO k10_schema_migrations VALUES(1,'2026-09-01T00:00:00+00:00')")
    assert initialize_schema(path) == 5
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT MAX(version) FROM k10_schema_migrations").fetchone() == (5,)
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='k10_opportunities'").fetchone()
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='k10_plan_revisions'").fetchone() is None


def test_publish_is_atomic_shares_same_company_window_and_freezes_selection(tmp_path):
    path=tmp_path/"publish.sqlite"; initialize_schema(path)
    _calendar(path, ("20260904",1),("20260905",0),("20260906",0),("20260907",1),("20260908",1))
    c1,e1=_candidate(path,suffix="one",scan_id="scan-one")
    c2,e2=_candidate(path,suffix="two",scan_id="scan-one")
    now=lambda: datetime(2026,9,7,9,29,tzinfo=SHANGHAI)
    batch=store.publish_opportunities(batch_id="batch-one",scan_id="scan-one",publication_kind="morning",
        inputs=(_input(c1,e1,key="300001.SZ/catalyst-a"),_input(c2,e2,key="300001.SZ/catalyst-b",stage="order")),db_path=path,clock=now)
    assert batch.sample_count == 2
    samples=store.list_publication_samples(batch_id="batch-one",db_path=path)
    assert len({item["companyWindowId"] for item in samples}) == 1
    window=store.list_company_windows(db_path=path)[0]
    assert window["d1TradeDate"] == "2026-09-07" and window["d2TradeDate"] == "2026-09-08"
    store.observe_candidate(action_id="action-one", observation_id="ob-one", task_id="analysis-one", outbox_id="outbox-one",
                            candidate_id=c1,idempotency_key="keep-one",task_input_version="config@1",task_input_cutoff_at="2026-09-07T09:00:00+08:00",
                            task_payload={},task_budget={"maxAttempts":1},created_at="2026-09-07T09:29:00+08:00",db_path=path)
    frozen=store.freeze_company_window_selection(company_window_id=window["companyWindowId"], frozen_at="2026-09-07T09:30:00+08:00",db_path=path)
    assert frozen["state"] == "selected"
    assert store.freeze_company_window_selection(company_window_id=window["companyWindowId"], frozen_at="2026-09-07T11:00:00+08:00",db_path=path) == frozen
    with pytest.raises(store.K10Conflict):
        store.publish_opportunities(batch_id="batch-two",scan_id="scan-one",publication_kind="morning",inputs=(_input(c1,e1,key="new"),),db_path=path,clock=now)


def test_later_overlapping_opportunity_is_fixed_overlap_and_withdrawal_keeps_window(tmp_path):
    path=tmp_path/"overlap.sqlite"; initialize_schema(path)
    _calendar(path,("20260907",1),("20260908",1),("20260909",1),("20260910",1))
    old,e_old=_candidate(path,suffix="old",scan_id="scan-old")
    first=store.publish_opportunities(batch_id="batch-old",scan_id="scan-old",publication_kind="morning",inputs=(_input(old,e_old,key="old"),),db_path=path,clock=lambda:datetime(2026,9,8,9,29,tzinfo=SHANGHAI))
    new,e_new=_candidate(path,suffix="new",scan_id="scan-new")
    store.publish_opportunities(batch_id="batch-new",scan_id="scan-new",publication_kind="morning",inputs=(_input(new,e_new,key="new"),),db_path=path,clock=lambda:datetime(2026,9,9,9,29,tzinfo=SHANGHAI))
    windows=store.list_company_windows(db_path=path)
    assert [item["sampleClass"] for item in windows] == ["overlap","primary"]
    opportunity=store.list_opportunities(batch_id="batch-new",db_path=path)[0]
    store.withdraw_opportunity(opportunity_id=opportunity["opportunityId"],reason="反证",source_refs=(),withdrawn_at="2026-09-09T10:00:00+08:00",db_path=path)
    withdrawn=store.get_opportunity(opportunity_id=opportunity["opportunityId"],db_path=path)
    assert withdrawn["state"] == "withdrawn" and withdrawn["d2TradeDate"] == "2026-09-10"


def test_market_day_facts_are_company_day_revisioned(tmp_path):
    path=tmp_path/"facts.sqlite"; initialize_schema(path)
    first=store.append_market_day_fact(company_code="300001.SZ",trade_date="2026-09-08",availability="data_gap",open_price=None,high_price=None,low_price=None,close_price=None,pre_close=None,limit_up_price=None,close_limit_up=None,touched_limit_up=None,source_refs=(),obtained_at="2026-09-08T16:00:00+08:00",created_at="2026-09-08T16:00:00+08:00",db_path=path)
    second=store.append_market_day_fact(company_code="300001.SZ",trade_date="2026-09-08",availability="available",open_price=10,high_price=11,low_price=9,close_price=11,pre_close=10,limit_up_price=11,close_limit_up=True,touched_limit_up=True,source_refs=({"source":"daily"},),obtained_at="2026-09-09T16:00:00+08:00",created_at="2026-09-09T16:00:00+08:00",db_path=path,adj_factor=1.25,metadata={"priceSource":"daily"})
    assert (first,second)==(1,2)
    facts=store.list_market_day_facts(company_code="300001.SZ",db_path=path)
    assert [item["availability"] for item in facts] == ["data_gap","available"]
    assert facts[-1]["factId"] == store.market_day_fact_id(company_code="300001.SZ",trade_date="2026-09-08")
    assert facts[-1]["adjFactor"] == 1.25 and facts[-1]["metadata"] == {"priceSource":"daily"}
    assert [(item["revision"], item["availability"]) for item in store.latest_market_day_facts(company_code="300001.SZ",db_path=path)] == [(2,"available")]


def test_open_boundary_is_late_and_selection_ignores_actions_at_or_after_open(tmp_path):
    path=tmp_path/"boundary.sqlite"; initialize_schema(path)
    _calendar(path,("20260906",1),("20260907",1),("20260908",1),("20260909",1))
    candidate,event=_candidate(path,suffix="boundary",scan_id="scan-boundary")
    store.publish_opportunities(batch_id="batch-boundary",scan_id="scan-boundary",publication_kind="morning",
        inputs=(_input(candidate,event,key="boundary"),),db_path=path,clock=lambda:datetime(2026,9,7,9,30,tzinfo=SHANGHAI))
    window=store.list_company_windows(db_path=path)[0]
    assert window["d1TradeDate"] == "2026-09-08"  # exact 09:30 is late
    # A later action cannot enter the already-fixed D1 selection cohort. This test manually
    # freezes an evening publication's next-day selection boundary.
    late_candidate,late_event=_candidate(path,suffix="evening",scan_id="scan-evening")
    store.publish_opportunities(batch_id="batch-evening",scan_id="scan-evening",publication_kind="evening",
        inputs=(_input(late_candidate,late_event,key="evening",source_marker="evening"),),db_path=path,clock=lambda:datetime(2026,9,7,21,0,tzinfo=SHANGHAI))
    evening_window=next(item for item in store.list_company_windows(db_path=path) if item["firstBatchId"]=="batch-evening")
    store.observe_candidate(action_id="before-open", observation_id="ob-boundary", task_id="task-boundary", outbox_id="outbox-boundary", candidate_id=late_candidate, idempotency_key="before-open", task_input_version="config@1", task_input_cutoff_at="2026-09-08T09:00:00+08:00", task_payload={}, task_budget={"maxAttempts": 1}, created_at="2026-09-08T09:29:59+08:00", db_path=path)
    store.append_candidate_action(action_id="at-open",candidate_id=late_candidate,action="skip",idempotency_key="at-open",reason=None,created_at="2026-09-08T09:30:00+08:00",db_path=path)
    frozen=store.freeze_company_window_selection(company_window_id=evening_window["companyWindowId"],frozen_at="2026-09-08T09:31:00+08:00",db_path=path)
    assert frozen["state"] == "selected" and frozen["actionIds"] == ["before-open"]


def test_publication_contract_allows_null_change_for_independent_but_requires_real_material_parent(tmp_path):
    path=tmp_path/"classification.sqlite"; initialize_schema(path)
    _calendar(path,("20260907",1),("20260908",1),("20260909",1),("20260910",1))
    old,old_event=_candidate(path,suffix="old",scan_id="scan-old")
    old_batch=store.publish_opportunities(batch_id="batch-old",scan_id="scan-old",publication_kind="morning",inputs=(_input(old,old_event,key="old"),),db_path=path,clock=lambda:datetime(2026,9,8,9,29,tzinfo=SHANGHAI))
    assert old_batch.sample_count == 1
    candidate,event=_candidate(path,suffix="independent",scan_id="scan-independent")
    independent=_input(candidate,event,key="independent")
    comparison=dict(independent.comparison); classification=dict(comparison["classification"]); classification["changedJudgment"]=None; comparison["classification"]=classification
    store.publish_opportunities(batch_id="batch-independent",scan_id="scan-independent",publication_kind="morning",inputs=(replace(independent,comparison=comparison),),db_path=path,clock=lambda:datetime(2026,9,9,9,29,tzinfo=SHANGHAI))
    material_candidate,material_event=_candidate(path,suffix="material",scan_id="scan-material")
    material=_input(material_candidate,material_event,key="material")
    comparison=dict(material.comparison); classification=dict(comparison["classification"]); classification.update({"kind":"material_stage","relatedOpportunityId":"not-an-opportunity","changedJudgment":"关键判断改变"}); comparison["classification"]=classification
    with pytest.raises(store.K10Conflict, match="既有机会"):
        store.publish_opportunities(batch_id="batch-material",scan_id="scan-material",publication_kind="morning",inputs=(replace(material,comparison=comparison,related_opportunity_id="not-an-opportunity"),),db_path=path,clock=lambda:datetime(2026,9,10,9,29,tzinfo=SHANGHAI))


def test_publication_replay_never_resamples_clock_and_boundary_rollback_is_invisible(tmp_path):
    path=tmp_path/"replay.sqlite"; initialize_schema(path)
    _calendar(path,("20260906",1),("20260907",1),("20260908",1),("20260909",1))
    candidate,event=_candidate(path,suffix="replay",scan_id="scan-replay")
    item=_input(candidate,event,key="replay")
    store.publish_opportunities(batch_id="batch-replay",scan_id="scan-replay",publication_kind="morning",inputs=(item,),db_path=path,clock=lambda:datetime(2026,9,7,9,29,tzinfo=SHANGHAI))
    replay=store.publish_opportunities(batch_id="batch-replay",scan_id="scan-replay",publication_kind="morning",inputs=(item,),db_path=path,clock=lambda:(_ for _ in ()).throw(AssertionError("replay sampled clock")))
    assert replay.available_at == "2026-09-07T01:29:00+00:00"
    boundary_candidate,boundary_event=_candidate(path,suffix="rollback",scan_id="scan-rollback")
    calls=iter((datetime(2026,9,7,9,29,tzinfo=SHANGHAI),datetime(2026,9,7,9,30,tzinfo=SHANGHAI)))
    with pytest.raises(store.K10Conflict, match="跨越固定 D1"):
        store.publish_opportunities(batch_id="batch-rollback",scan_id="scan-rollback",publication_kind="morning",inputs=(_input(boundary_candidate,boundary_event,key="rollback"),),db_path=path,clock=lambda:next(calls))
    assert store.get_publication_batch(batch_id="batch-rollback",db_path=path) is None
    assert store.list_opportunities(batch_id="batch-rollback",db_path=path) == []


def test_evening_company_limit_and_latest_evaluation_revision(tmp_path):
    path=tmp_path/"limit.sqlite"; initialize_schema(path)
    _calendar(path,("20260906",1),("20260907",1),("20260908",1),("20260909",1))
    values=[]
    for index in range(31):
        company=f"30{index:04d}.SZ"
        candidate,event=_candidate(path,suffix=f"limit-{index}",scan_id="scan-limit",company=company)
        values.append(replace(_input(candidate,event,key=f"limit-{index}",source_marker="evening"),company_code=company))
    with pytest.raises(store.K10Conflict, match="最多 30"):
        store.publish_opportunities(batch_id="batch-limit",scan_id="scan-limit",publication_kind="evening",inputs=tuple(values),db_path=path,clock=lambda:datetime(2026,9,7,21,0,tzinfo=SHANGHAI))
    candidate,event=_candidate(path,suffix="evaluation",scan_id="scan-evaluation")
    store.publish_opportunities(batch_id="batch-evaluation",scan_id="scan-evaluation",publication_kind="morning",inputs=(_input(candidate,event,key="evaluation"),),db_path=path,clock=lambda:datetime(2026,9,7,9,29,tzinfo=SHANGHAI))
    window=store.list_company_windows(db_path=path)[0]
    assert store.append_company_window_evaluation(company_window_id=window["companyWindowId"],state="pending",fact_refs=(),result={"complete":False},evaluated_at="2026-09-07T10:00:00+08:00",created_at="2026-09-07T10:00:00+08:00",db_path=path) == 1
    assert store.append_company_window_evaluation(company_window_id=window["companyWindowId"],state="incomplete",fact_refs=({"date":"2026-09-08"},),result={"complete":False,"gap":"missing"},evaluated_at="2026-09-08T16:00:00+08:00",created_at="2026-09-08T16:00:00+08:00",db_path=path) == 2
    latest=store.list_company_window_evaluations(company_window_id=window["companyWindowId"],db_path=path)
    assert [(item["revision"],item["state"]) for item in latest] == [(2,"incomplete")]


def test_window_action_uses_one_representative_and_one_shared_analysis_chain(tmp_path):
    path=tmp_path/"window-action.sqlite"; initialize_schema(path)
    _calendar(path,("20260906",1),("20260907",1),("20260908",1),("20260909",1))
    first,first_event=_candidate(path,suffix="first",scan_id="scan-window")
    second,second_event=_candidate(path,suffix="second",scan_id="scan-window")
    store.publish_opportunities(batch_id="batch-window",scan_id="scan-window",publication_kind="morning",inputs=(
        _input(first,first_event,key="window-a"), _input(second,second_event,key="window-b"),
    ),db_path=path,clock=lambda:datetime(2026,9,7,9,29,tzinfo=SHANGHAI))
    window=store.list_company_windows(db_path=path)[0]
    created=store.observe_company_window(action_id="keep-window",observation_id="ob-window",task_id="analysis-window",outbox_id="outbox-window",company_window_id=window["companyWindowId"],idempotency_key="keep-window-request",task_input_version="config@1",task_input_cutoff_at="2026-09-07T09:29:00+08:00",task_payload={"kind":"full"},task_budget={"maxAttempts":1},created_at="2026-09-07T09:10:00+08:00",db_path=path)
    assert created.created and not created.replayed and created.candidate_id == first
    replay=store.observe_company_window(action_id="ignored",observation_id="ignored",task_id="ignored",outbox_id="ignored",company_window_id=window["companyWindowId"],idempotency_key="keep-window-request",task_input_version="different",task_input_cutoff_at="different",task_payload={},task_budget={},created_at="2026-09-07T09:11:00+08:00",db_path=path)
    assert replay.observation_id == "ob-window" and replay.task_id == "analysis-window" and replay.replayed
    store.append_company_window_action(action_id="skip-window",company_window_id=window["companyWindowId"],action="skip",idempotency_key="skip-window-request",reason="明确略过",created_at="2026-09-07T09:20:00+08:00",db_path=path)
    realtime=store.get_company_window_selection(company_window_id=window["companyWindowId"],db_path=path)
    assert realtime == {"companyWindowId":window["companyWindowId"],"currentState":"skipped","lastActionId":"skip-window","lastActionAt":"2026-09-07T09:20:00+08:00","postFreeze":False,"representativeCandidateId":first,"observationId":"ob-window","taskId":"analysis-window","snapshotState":None,"snapshotActionIds":[],"frozenAt":None}
    frozen=store.freeze_company_window_selection(company_window_id=window["companyWindowId"],frozen_at="2026-09-07T09:30:00+08:00",db_path=path)
    assert frozen["state"] == "skipped" and frozen["actionIds"] == ["skip-window"]
    assert store.get_company_window_selection(company_window_id=window["companyWindowId"],db_path=path)["snapshotState"] == "skipped"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_company_window_observations").fetchone() == (1,)
        assert conn.execute("SELECT COUNT(*) FROM k10_tasks WHERE kind='analysis'").fetchone() == (1,)
    from neckline.k10.schema import rollback_schema
    assert rollback_schema(path) == 0
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'k10_%'").fetchall() == []


def test_d2_past_is_read_as_expired_without_get_writing_lifecycle(tmp_path):
    path=tmp_path/"expired-projection.sqlite"; initialize_schema(path)
    _calendar(path,("20260906",1),("20260907",1),("20260908",1),("20260909",1))
    candidate,event=_candidate(path,suffix="expired",scan_id="scan-expired")
    store.publish_opportunities(batch_id="batch-expired",scan_id="scan-expired",publication_kind="morning",inputs=(_input(candidate,event,key="expired"),),db_path=path,clock=lambda:datetime(2026,9,7,9,29,tzinfo=SHANGHAI))
    opportunity=store.list_opportunities(batch_id="batch-expired",as_of=datetime(2026,9,7,12,tzinfo=SHANGHAI),db_path=path)[0]
    assert opportunity["state"] == "active"
    expired=store.get_opportunity(opportunity_id=opportunity["opportunityId"],as_of=datetime(2026,9,8,15,tzinfo=SHANGHAI),db_path=path)
    assert expired and expired["state"] == "expired"
    events=store.list_opportunity_lifecycle_events(opportunity_id=opportunity["opportunityId"],db_path=path)
    assert len(events) == 1 and events[0]["kind"] == "published"


def test_thirty_company_batch_freezes_five_kept_and_twenty_five_unhandled(tmp_path):
    path=tmp_path/"thirty-company-selection.sqlite"; initialize_schema(path)
    _calendar(path,("20260906",1),("20260907",1),("20260908",1),("20260909",1))
    inputs=[]
    for number in range(30):
        company=f"30{number:04d}.SZ"
        candidate,event=_candidate(path,suffix=f"thirty-{number}",scan_id="scan-thirty",company=company)
        inputs.append(_input(candidate,event,key=f"thirty-{number}",company=company))
    store.publish_opportunities(batch_id="batch-thirty",scan_id="scan-thirty",publication_kind="morning",inputs=tuple(inputs),db_path=path,clock=lambda:datetime(2026,9,7,9,29,tzinfo=SHANGHAI))
    windows=store.list_company_windows(db_path=path)
    assert len(windows) == 30 and {item["sampleClass"] for item in windows} == {"primary"}
    for index, window in enumerate(windows[:5]):
        store.observe_company_window(action_id=f"keep-{index}",observation_id=f"ob-keep-{index}",task_id=f"task-keep-{index}",outbox_id=f"outbox-keep-{index}",company_window_id=window["companyWindowId"],idempotency_key=f"keep-request-{index}",task_input_version="config@1",task_input_cutoff_at="2026-09-07T09:29:00+08:00",task_payload={},task_budget={"maxAttempts":1},created_at=f"2026-09-07T09:2{index}:00+08:00",db_path=path)
    frozen=[store.freeze_company_window_selection(company_window_id=item["companyWindowId"],frozen_at="2026-09-07T09:30:00+08:00",db_path=path)["state"] for item in windows]
    assert frozen.count("selected") == 5
    assert frozen.count("skipped") == 0
    assert frozen.count("unhandled") == 25


def test_explicit_skip_is_distinct_from_no_action_and_post_open_change_cannot_rewrite_snapshot(tmp_path):
    path=tmp_path/"selection-groups.sqlite"; initialize_schema(path)
    _calendar(path,("20260906",1),("20260907",1),("20260908",1),("20260909",1))
    skipped,event_skipped=_candidate(path,suffix="explicit-skip",scan_id="scan-groups",company="300010.SZ")
    untouched,event_untouched=_candidate(path,suffix="untouched",scan_id="scan-groups",company="300011.SZ")
    store.publish_opportunities(batch_id="batch-groups",scan_id="scan-groups",publication_kind="morning",inputs=(
        _input(skipped,event_skipped,key="explicit-skip",company="300010.SZ"),
        _input(untouched,event_untouched,key="untouched",company="300011.SZ"),
    ),db_path=path,clock=lambda:datetime(2026,9,7,9,29,tzinfo=SHANGHAI))
    windows={item["companyCode"]:item for item in store.list_company_windows(db_path=path)}
    store.append_company_window_action(action_id="skip-explicit",company_window_id=windows["300010.SZ"]["companyWindowId"],action="skip",idempotency_key="skip-explicit",reason="明确略过",created_at="2026-09-07T09:20:00+08:00",db_path=path)
    skipped_snapshot=store.freeze_company_window_selection(company_window_id=windows["300010.SZ"]["companyWindowId"],frozen_at="2026-09-07T09:30:00+08:00",db_path=path)
    untouched_snapshot=store.freeze_company_window_selection(company_window_id=windows["300011.SZ"]["companyWindowId"],frozen_at="2026-09-07T09:30:00+08:00",db_path=path)
    assert skipped_snapshot["state"] == "skipped" and untouched_snapshot["state"] == "unhandled"
    store.append_company_window_action(action_id="post-open-restore",company_window_id=windows["300010.SZ"]["companyWindowId"],action="restore",idempotency_key="post-open-restore",reason="开盘后取消关注",created_at="2026-09-07T10:00:00+08:00",db_path=path)
    current=store.get_company_window_selection(company_window_id=windows["300010.SZ"]["companyWindowId"],db_path=path)
    assert current["currentState"] == "unhandled" and current["lastActionAt"] == "2026-09-07T10:00:00+08:00" and current["postFreeze"] is True
    assert store.freeze_company_window_selection(company_window_id=windows["300010.SZ"]["companyWindowId"],frozen_at="2026-09-07T11:00:00+08:00",db_path=path) == skipped_snapshot


def test_evening_and_preopen_morning_share_window_but_open_boundary_is_delayed(tmp_path):
    path=tmp_path/"availability-window.sqlite"; initialize_schema(path)
    _calendar(path,("20260906",1),("20260907",1),("20260908",1),("20260909",1),("20260910",1))
    evening,event_evening=_candidate(path,suffix="evening-window",scan_id="scan-evening-window",company="300020.SZ")
    morning,event_morning=_candidate(path,suffix="morning-window",scan_id="scan-morning-window",company="300021.SZ")
    delayed_evening,event_delayed_evening=_candidate(path,suffix="delayed-evening-window",scan_id="scan-delayed-evening-window",company="300022.SZ")
    late,event_late=_candidate(path,suffix="late-window",scan_id="scan-late-window",company="300023.SZ")
    store.publish_opportunities(batch_id="batch-evening-window",scan_id="scan-evening-window",publication_kind="evening",inputs=(_input(evening,event_evening,key="evening-window",company="300020.SZ",source_marker="evening"),),db_path=path,clock=lambda:datetime(2026,9,7,21,0,tzinfo=SHANGHAI))
    store.publish_opportunities(batch_id="batch-morning-window",scan_id="scan-morning-window",publication_kind="morning",inputs=(_input(morning,event_morning,key="morning-window",company="300021.SZ"),),db_path=path,clock=lambda:datetime(2026,9,8,9,29,tzinfo=SHANGHAI))
    # The frozen evening cutoff expects Tuesday D1. Actual visibility before the open still
    # gets Tuesday, even though the source is an evening scan; exactly 09:30 moves to Wednesday.
    store.publish_opportunities(batch_id="batch-delayed-evening-window",scan_id="scan-delayed-evening-window",publication_kind="evening",inputs=(_input(delayed_evening,event_delayed_evening,key="delayed-evening-window",company="300022.SZ",source_marker="evening"),),db_path=path,clock=lambda:datetime(2026,9,8,9,29,tzinfo=SHANGHAI))
    store.publish_opportunities(batch_id="batch-late-window",scan_id="scan-late-window",publication_kind="evening",inputs=(_input(late,event_late,key="late-window",company="300023.SZ",source_marker="evening"),),db_path=path,clock=lambda:datetime(2026,9,8,9,30,tzinfo=SHANGHAI))
    by_company={item["companyCode"]:item for item in store.list_company_windows(db_path=path)}
    assert (by_company["300020.SZ"]["d1TradeDate"],by_company["300020.SZ"]["d2TradeDate"]) == ("2026-09-08","2026-09-09")
    assert (by_company["300021.SZ"]["d1TradeDate"],by_company["300021.SZ"]["d2TradeDate"]) == ("2026-09-08","2026-09-09")
    assert (by_company["300022.SZ"]["d1TradeDate"],by_company["300022.SZ"]["d2TradeDate"]) == ("2026-09-08","2026-09-09")
    assert (by_company["300023.SZ"]["d1TradeDate"],by_company["300023.SZ"]["d2TradeDate"]) == ("2026-09-09","2026-09-10")
    normal=store.list_opportunities(batch_id="batch-evening-window",db_path=path)[0]
    preopen=store.list_opportunities(batch_id="batch-delayed-evening-window",db_path=path)[0]
    delayed=store.list_opportunities(batch_id="batch-late-window",db_path=path)[0]
    assert (normal["sourceMarker"],normal["latePublication"]) == ("evening",False)
    assert (preopen["sourceMarker"],preopen["latePublication"]) == ("evening",False)
    assert (delayed["sourceMarker"],delayed["latePublication"]) == ("evening",True)


def test_overlap_chain_remains_overlap_after_unfollow_and_withdrawal(tmp_path):
    path=tmp_path/"overlap-chain.sqlite"; initialize_schema(path)
    _calendar(path,("20260906",1),("20260907",1),("20260908",1),("20260909",1),("20260910",1),("20260911",1))
    first,event_first=_candidate(path,suffix="tue-wed",scan_id="scan-tue-wed")
    second,event_second=_candidate(path,suffix="wed-thu",scan_id="scan-wed-thu")
    third,event_third=_candidate(path,suffix="thu-fri",scan_id="scan-thu-fri")
    store.publish_opportunities(batch_id="batch-tue-wed",scan_id="scan-tue-wed",publication_kind="evening",inputs=(_input(first,event_first,key="tue-wed",source_marker="evening"),),db_path=path,clock=lambda:datetime(2026,9,7,21,0,tzinfo=SHANGHAI))
    store.publish_opportunities(batch_id="batch-wed-thu",scan_id="scan-wed-thu",publication_kind="evening",inputs=(_input(second,event_second,key="wed-thu",source_marker="evening"),),db_path=path,clock=lambda:datetime(2026,9,8,21,0,tzinfo=SHANGHAI))
    store.publish_opportunities(batch_id="batch-thu-fri",scan_id="scan-thu-fri",publication_kind="evening",inputs=(_input(third,event_third,key="thu-fri",source_marker="evening"),),db_path=path,clock=lambda:datetime(2026,9,9,21,0,tzinfo=SHANGHAI))
    windows=store.list_company_windows(db_path=path)
    assert [(item["d1TradeDate"],item["d2TradeDate"],item["sampleClass"]) for item in windows] == [("2026-09-10","2026-09-11","overlap"),("2026-09-09","2026-09-10","overlap"),("2026-09-08","2026-09-09","primary")]
    assert windows[0]["overlapsWindowId"] == windows[1]["companyWindowId"]
    assert windows[1]["overlapsWindowId"] == windows[2]["companyWindowId"]
    assert windows[2]["overlapsWindowId"] is None
    store.observe_company_window(action_id="follow-first",observation_id="ob-follow-first",task_id="task-follow-first",outbox_id="outbox-follow-first",company_window_id=windows[0]["companyWindowId"],idempotency_key="follow-first",task_input_version="config@1",task_input_cutoff_at="2026-09-08T09:00:00+08:00",task_payload={},task_budget={"maxAttempts":1},created_at="2026-09-08T09:00:00+08:00",db_path=path)
    store.append_company_window_action(action_id="unfollow-first",company_window_id=windows[0]["companyWindowId"],action="restore",idempotency_key="unfollow-first",reason="取消关注",created_at="2026-09-08T09:01:00+08:00",db_path=path)
    first_opportunity=store.list_opportunities(batch_id="batch-tue-wed",db_path=path)[0]
    store.withdraw_opportunity(opportunity_id=first_opportunity["opportunityId"],reason="重大反证",source_refs=(),withdrawn_at="2026-09-08T10:00:00+08:00",db_path=path)
    persisted=store.list_company_windows(db_path=path)
    assert [(item["sampleClass"],item["overlapsWindowId"]) for item in persisted] == [("overlap",windows[1]["companyWindowId"]),("overlap",windows[2]["companyWindowId"]),("primary",None)]
