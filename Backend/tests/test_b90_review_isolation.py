"""Morning conclusions remain scoped to their frozen reasons and paid work units."""
from datetime import datetime, timedelta
from io import StringIO
import json
import sqlite3
from threading import Event
import time

import httpx
import pytest

from neckline.k10 import morning_runtime, pipeline, store
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from tests import v340_acceptance_fixture as base
from tests.test_v350_cli_api import DirectRoundTransport, explicit_bindings, generate_acceptance


@pytest.mark.parametrize("scenario", ("nonfirst_reason", "unknown_reason", "one_unknown", "review_deadline", "search_unknown"))
def test_real_parent_keeps_sibling_results_and_targets_the_actual_reason(tmp_path, monkeypatch, scenario):
    search_calls = []
    search_target = []
    original_search = base._TavilyWire.respond

    def search_wire(self, request):
        query = json.loads(request.content).get("query", "")
        if query.startswith("离线晨报 "):
            search_calls.append(query)
            if scenario == "search_unknown" and query in search_target:
                raise httpx.ReadTimeout("isolated search charge unknown", request=request)
        return original_search(self, request)

    monkeypatch.setattr(base._TavilyWire, "respond", search_wire)
    # Only the bounded-wait case needs a short isolated transport budget.
    # Freeze it before either public producer; never repair a queued task.
    if scenario == "review_deadline":
        append = store.append_run_config

        def shorter_transport(**kwargs):
            payload = json.loads(json.dumps(kwargs["payload"]))
            payload["taskPolicies"]["morning"]["timeoutSeconds"] = 1
            return append(**(kwargs | {"payload": payload}))

        monkeypatch.setattr(store, "append_run_config", shorter_transport)

    evening = generate_acceptance(tmp_path / "producer", monkeypatch, scenario="complete")
    database = evening.database
    binding = explicit_bindings(database)
    parent = evening.report["report"]
    parent_cards = parent["eveningCards"]
    multi = next(card for card in parent_cards if len(card["catalysts"]) == 2)
    search_target.append(f"离线晨报 {multi['companyCode']} 独立核验")
    other = next(card for card in parent_cards if card["companyCode"] != multi["companyCode"])
    expected_reason_ids = {card["companyCode"]: {c["opportunityId"] for c in card["catalysts"]} for card in parent_cards}
    target_reason = multi["catalysts"][1]["opportunityId"]
    before = {item["opportunityId"]: item["state"] for item in store.list_opportunities(as_of=datetime(2026, 9, 9, 9, 20, tzinfo=base.SHANGHAI), db_path=database)}
    with sqlite3.connect(database) as conn:
        windows = conn.execute("SELECT company_window_id,d1_trade_date,d2_trade_date FROM k10_company_windows ORDER BY company_window_id").fetchall()
        target_candidate = conn.execute("SELECT candidate_id FROM k10_publication_samples WHERE opportunity_id=?", (target_reason,)).fetchone()[0]

    deadline = datetime(2026, 9, 9, 9, 20, tzinfo=base.SHANGHAI)
    begun = time.monotonic()

    def clock():
        if scenario == "review_deadline":
            return deadline - timedelta(seconds=10) + timedelta(seconds=time.monotonic() - begun)
        return datetime(2026, 9, 9, 8, 35, tzinfo=base.SHANGHAI)

    wire_calls = []
    release = Event()
    original_respond = DirectRoundTransport.respond

    def review_wire(self, request):
        wire = json.loads(request.content)
        message = wire["messages"][-1]["content"]
        if "<untrusted-evidence>" not in message:
            return original_respond(self, request)
        evidence = json.loads(message.split("<untrusted-evidence>\n", 1)[1].split("\n</untrusted-evidence>", 1)[0])
        company = evidence["original"]["candidate"]["companyCode"]
        reasons = evidence["parentReasons"]
        assert {reason["opportunityId"] for reason in reasons} == expected_reason_ids[company]
        independent = evidence["independentVerificationDocuments"]
        wire_calls.append((company, "conclude" if independent else "search"))
        if not independent:
            return self._ok({"action": "search", "question": "昨晚理由是否出现独立反证？",
                "query": f"离线晨报 {company} 独立核验", "rationale": "核对冻结名单中该公司的全部原理由。"})
        if scenario == "one_unknown" and company == multi["companyCode"]:
            raise httpx.ReadTimeout("isolated provider reply unknown", request=request)
        if scenario == "review_deadline" and company == other["companyCode"]:
            # The production response deadline interrupts this actual model
            # boundary. No ledger row or work-item result is inserted by hand.
            assert release.wait(20), "production review deadline failed to bound its wire"
        if scenario in {"nonfirst_reason", "unknown_reason"} and company == multi["companyCode"]:
            ref = {key: independent[0][key] for key in ("documentId", "revision")}
            return self._ok({"action": "conclude", "material": True,
                "reasonStatus": "invalidated", "observationStatus": "unavailable",
                "affectedOpportunityIds": [target_reason if scenario == "nonfirst_reason" else "outside-frozen-parent"],
                "summary": "独立公告仅否认昨晚第二条理由，第一条理由不受影响。",
                "materialContraryEvidence": [{**ref, "claim": "公司明确否认第二条理由中的关系。"}]})
        return self._ok({"action": "conclude", "material": False,
            "reasonStatus": "current", "observationStatus": "current",
            "summary": "已检查全部原理由，独立资料没有显示需要改变判断的隔夜事实。",
            "materialContraryEvidence": []})

    monkeypatch.setattr(DirectRoundTransport, "respond", review_wire)
    monkeypatch.setattr(DirectRoundTransport, "loopback_morning_discovery_zero", True)
    monkeypatch.setattr(pipeline, "_now", clock)
    if scenario == "review_deadline":
        monkeypatch.setattr(pipeline, "_morning_finalization_reserve", lambda **_kwargs: timedelta(seconds=1))

    def resolver(**_kwargs):
        provider = MeteredProvider(ledger_db=database, ledger_task="discovery", api_key="fixture",
            model="deepseek-v4-pro", name="fixture", api_url="https://fixture.invalid/v1/chat/completions",
            read_timeout=1, use_streaming=False)
        provider.max_attempts = 1
        return ProviderResolution("configured", provider, "fixture", None)

    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", resolver)
    monkeypatch.setattr(morning_runtime, "resolve_deepseek_v4_pro", resolver)
    output = StringIO()
    with base.redirect_stdout(output):
        assert base.cli_main(["enqueue", "--db", str(database), "--kind", "morning",
            "--trading-day", "2026-09-09", "--config-id", binding["config_id"],
            "--config-revision", str(binding["config_revision"]),
            "--execution-config-id", binding["execution_id"],
            "--execution-config-revision", str(binding["execution_revision"])]) == 0
    task_id = output.getvalue().strip()
    assert task_id.startswith("task_")

    def handler(context):
        return pipeline.production_scan_handler(context, tushare_token="fixture-token",
            parquet_dir=tmp_path / "parquet", now=clock)

    sealed_before_release = None
    report_count_before_release = 1
    try:
        terminal = run_once(db_path=database, task_id=task_id, worker_id="b90-review-isolation",
            lease_for=timedelta(minutes=5), handlers={"morning_scan": handler}, clock=clock,
            require_b76_contract=True)
        if scenario == "review_deadline":
            # The parent must first seal an honest partial while the provider
            # request is still unsettled.  A later exact settlement is
            # accounting only and cannot change that immutable report.
            with sqlite3.connect(database) as conn:
                unresolved_before_release = conn.execute(
                    "SELECT stage,state FROM k10_external_attempts WHERE task_id=? "
                    "AND state IN ('started','running','unknown')", (task_id,),
                ).fetchall()
                report_count_before_release = conn.execute(
                    "SELECT count(*) FROM k10_v2_report_runs WHERE window_kind='morning'"
                ).fetchone()[0]
            with base.actual_api(database, **binding) as client:
                sealed_before_release = client.get("/api/v1/k10/v2/reports/latest?window=morning").json()
            assert unresolved_before_release and all(
                stage == "morning" and state in {"started", "unknown"}
                for stage, state in unresolved_before_release
            )
            assert sealed_before_release["report"]["delivery"]["outcome"] == "partial"
    finally:
        release.set()
    assert terminal is not None and terminal.status == "completed"
    with base.actual_api(database, **binding) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=morning")
        assert response.status_code == 200
        report = response.json()["report"]
        assert report["availableAt"] and datetime.fromisoformat(report["availableAt"]) <= deadline
        assert report["discovery"]["state"] == "complete"
        assert report["discovery"]["outcome"] == "no_recommendation"
        review = report["morningReview"]
        assert review["targetCompanyCount"] == 2 and review["targetReasonCount"] == 3
        items = {item["companyCode"]: item for item in review["items"]}
        assert {key: set(item["opportunityIds"]) for key, item in items.items()} == expected_reason_ids
        if scenario == "nonfirst_reason":
            assert all(item["status"] == "completed" for item in items.values())
            assert {item["opportunityId"] for item in report["lifecycleUpdates"]} == {target_reason}
        else:
            failed_company = other["companyCode"] if scenario == "review_deadline" else multi["companyCode"]
            good_company = multi["companyCode"] if scenario == "review_deadline" else other["companyCode"]
            assert report["delivery"]["outcome"] == "partial"
            assert items[failed_company]["status"] != "completed"
            assert items[failed_company]["unreviewedOpportunityIds"]
            assert items[good_company]["status"] == "completed"
            assert items[good_company]["unreviewedOpportunityIds"] == []
            assert report["lifecycleUpdates"] == []
        before_read = json.dumps(response.json(), sort_keys=True)
        assert json.dumps(client.get(f"/api/v1/k10/v2/reports/{report['reportId']}").json(), sort_keys=True) == before_read
        if sealed_before_release is not None:
            assert before_read == json.dumps(sealed_before_release, sort_keys=True)
    after = {item["opportunityId"]: item["state"] for item in store.list_opportunities(as_of=datetime(2026, 9, 9, 9, 20, tzinfo=base.SHANGHAI), db_path=database)}
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT company_window_id,d1_trade_date,d2_trade_date FROM k10_company_windows ORDER BY company_window_id").fetchall() == windows
        unresolved = conn.execute("SELECT stage,state FROM k10_external_attempts WHERE task_id=? AND state IN ('started','running','unknown')", (task_id,)).fetchall()
        assert conn.execute("SELECT count(*) FROM k10_v2_report_runs WHERE window_kind='morning'").fetchone() == (report_count_before_release,)
        assert conn.execute("SELECT count(*) FROM k10_tasks WHERE kind='morning_review'").fetchone() == (0,)
        if scenario == "nonfirst_reason":
            changed = conn.execute("SELECT opportunity_id,content_json FROM k10_opportunity_lifecycle_events WHERE kind='withdrawal'").fetchall()
            assert len(changed) == 1 and changed[0][0] == target_reason
            assert json.loads(changed[0][1])["candidateId"] == target_candidate
    if scenario == "nonfirst_reason":
        assert after[target_reason] == "withdrawn"
        assert {key: value for key, value in after.items() if key != target_reason} == {key: value for key, value in before.items() if key != target_reason}
        assert not unresolved
    else:
        assert after == before
        if scenario == "unknown_reason":
            assert not unresolved
        elif scenario == "review_deadline":
            # The late body may settle its original ledger attempt after the
            # immutable report is published; it must never cause another wire
            # or publication.
            assert not unresolved or all(stage == "morning" and state in {"started", "unknown"}
                                         for stage, state in unresolved)
        else:
            expected_stage = "search" if scenario == "search_unknown" else "morning"
            assert unresolved and all(stage == expected_stage and state in {"started", "unknown"} for stage, state in unresolved)
    # Exactly one paid search decision and one conclusion attempt per company;
    # a timeout is disclosed, never replaced with a new paid request.
    expected_calls = 3 if scenario == "search_unknown" else 4
    assert len(wire_calls) == expected_calls and len(set(wire_calls)) == expected_calls
    assert len(search_calls) == 2 and len(set(search_calls)) == 2
