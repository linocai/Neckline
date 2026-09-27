"""Mixed unknown dependencies retain independent morning results through real producers."""
from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
import json
import sqlite3
import httpx
import pytest
from threading import Event, current_thread
import time

from neckline.k10 import morning_runtime, pipeline
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from tests import v340_acceptance_fixture as base
from tests.test_v350_cli_api import DirectRoundTransport, explicit_bindings, generate_acceptance


def generate_mixed_unknown_acceptance(tmp_path, monkeypatch, *, unknown_kind="model"):
    """A blocked discovery wire cannot turn completed frozen-parent reviews into a missed morning.

    This is deliberately an entry-point regression: the evening parent and the
    morning task are both CLI-enqueued, the worker owns the execution binding,
    and the assertions read the published report through FastAPI.  It never
    inserts a review, a report, or an unknown ledger row directly.
    """
    search_wires = []
    unknown_companies = []
    original_search = base._TavilyWire.respond

    def search_response(self, request):
        search_wires.append(request.content)
        query = json.loads(request.content).get("query", "")
        if unknown_kind == "search" and unknown_companies and query.startswith("离线晨报 " + unknown_companies[0] + " "):
            raise httpx.ReadTimeout("mixed-unknown review search", request=request)
        if unknown_kind == "extract" and unknown_companies and request.url.path == "/extract":
            number = int(unknown_companies[0].split(".")[0][-3:])
            if json.loads(request.content).get("urls") == [f"https://evidence.fixture.invalid/{number:03d}"]:
                raise httpx.ReadTimeout("mixed-unknown review extract", request=request)
        return original_search(self, request)

    monkeypatch.setattr(base._TavilyWire, "respond", search_response)
    evening = generate_acceptance(tmp_path / "producer", monkeypatch, scenario="complete")
    database = evening.database
    unknown_company = evening.report["report"]["eveningCards"][0]["companyCode"]
    bindings = explicit_bindings(database)
    unknown_companies.append(unknown_company)
    deadline = datetime(2026, 9, 9, 9, 20, tzinfo=base.SHANGHAI)
    monotonic_started = time.monotonic()

    def clock() -> datetime:
        # Use the report's real immutable deadline while mapping a few wall
        # seconds into the final frozen envelope.  The production code sees a
        # normal business clock; only the isolated transport is slow.
        return deadline - timedelta(seconds=3) + timedelta(seconds=time.monotonic() - monotonic_started)

    discovery_wire_entered, release_discovery_wire = Event(), Event()
    original_respond = DirectRoundTransport.respond
    model_wires = []
    discovery_wire_finished = Event()

    def blocking_discovery_respond(self, request):
        wire = json.loads(request.content)
        model_wires.append(request.content)
        message = wire["messages"][-1]["content"]
        if "<untrusted-evidence>" in message:
            evidence = json.loads(message.split("<untrusted-evidence>\n", 1)[1].split("\n</untrusted-evidence>", 1)[0])
            company = evidence["original"]["candidate"]["companyCode"]
            independent = evidence["independentVerificationDocuments"]
            if not independent:
                return self._ok({"action": "search", "question": "昨晚理由是否出现独立反证？", "query": f"离线晨报 {company} 独立核验", "rationale": "核对冻结名单的原理由。"})
            if unknown_kind == "model" and company == unknown_company:
                raise httpx.ReadTimeout("review mixed-unknown isolated reproduction", request=request)
            original_ids = {item["documentId"] for item in independent if item.get("originalText")}
            excerpt = next((item for item in independent if item["documentId"] not in original_ids), None)
            if excerpt is not None:
                return self._ok({"action": "extract", "documentId": excerpt["documentId"], "revision": excerpt["revision"],
                                 "rationale": "搜索只给出摘录，读取独立来源原文后再判断。"})
            return self._ok({"action": "conclude", "material": False, "reasonStatus": "current", "observationStatus": "current", "summary": "独立资料未改变原判断。", "materialContraryEvidence": []})
        # Reviews have their own untrusted-evidence envelope and must retain a
        # usable model path while only the sibling discovery channel is held.
        if current_thread().name.startswith("k10-morning-discovery") and "<untrusted-evidence>" not in message:
            packet = self._packet(request)
            if packet.get("inputCount") is not None and not discovery_wire_entered.is_set():
                discovery_wire_entered.set()
                assert release_discovery_wire.wait(12), "test must release the late isolated discovery wire"
        response = original_respond(self, request)
        if discovery_wire_entered.is_set():
            discovery_wire_finished.set()
        return response

    monkeypatch.setattr(DirectRoundTransport, "respond", blocking_discovery_respond)
    # A short test envelope still follows the production finalization path.
    # Review transport is real, but its unrelated 90-second production
    # closeout budget would obscure this mixed-unknown publication regression.
    monkeypatch.setattr(pipeline, "_morning_finalization_reserve", lambda **_kwargs: timedelta(milliseconds=500))
    original_review_handler = morning_runtime.morning_review_handler

    def direct_review_handler(context, *, clock=clock, closeout_reserve=None, response_deadline_at=None,
                              response_fence=None, independent_evidence_fetch=None):
        # Preserve the new B90 same-work-item evidence callback while the
        # test deliberately removes only the unrelated finalization reserve.
        return original_review_handler(context, clock=clock,
                                       response_fence=response_fence,
                                       independent_evidence_fetch=independent_evidence_fetch)

    monkeypatch.setattr(morning_runtime, "morning_review_handler", direct_review_handler)

    def resolve_fixture_provider(**_kwargs):
        provider = MeteredProvider(
            ledger_db=database, ledger_task="discovery", api_key="fixture", model="deepseek-v4-pro", name="fixture",
            api_url="https://fixture.invalid/v1/chat/completions", read_timeout=1, use_streaming=False,
        )
        provider.max_attempts = 1
        return ProviderResolution("configured", provider, "fixture", None)

    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", resolve_fixture_provider)
    monkeypatch.setattr(morning_runtime, "resolve_deepseek_v4_pro", resolve_fixture_provider)
    monkeypatch.setattr(pipeline, "_now", clock)

    class OvernightNews:
        coverage = base._FullScaleNews.coverage

        def __init__(self, *, token: str, request_bound: int):
            assert token == "fixture-token" and request_bound >= 1

        def fetch_incremental(self, request):
            published = request.window.cutoff_at - timedelta(minutes=20)
            document = base.SourceDocumentInput(
                external_id="deadline-overnight-0000",
                canonical_url="https://fixture.invalid/deadline/overnight-0000",
                original_text="离线隔夜正文：需要核验的新增经营事件。", excerpt=None,
                published_at=published, published_precision="exact", fetched_at=published + timedelta(minutes=1),
                fetch_version="b90-deadline", metadata={"title": "离线验收标题 0000：隔夜新增经营事件"},
            )
            return base.SourceFetchResult(
                documents=(document,), next_cursor="deadline-final", success_watermark=request.window.cutoff_at,
                pages_fetched=1, pages_expected=1, exhausted=True,
            )

    # Its exact overnight timestamp makes discovery a real sibling workload;
    # company reviews reuse durable shared material without another collection.
    monkeypatch.setattr(pipeline, "TuShareMajorNewsAdapter", OvernightNews)

    morning_day = base.DAY + timedelta(days=1)
    output = StringIO()
    with base.redirect_stdout(output):
        assert base.cli_main([
            "enqueue", "--db", str(database), "--kind", "morning", "--trading-day", morning_day.isoformat(),
            "--config-id", bindings["config_id"], "--config-revision", str(bindings["config_revision"]),
            "--execution-config-id", bindings["execution_id"],
            "--execution-config-revision", str(bindings["execution_revision"]),
        ]) == 0
    task_id = output.getvalue().strip()
    assert task_id.startswith("task_")

    parquet_dir = tmp_path / "parquet"

    def morning_handler(context):
        return pipeline.production_scan_handler(
            context, tushare_token="fixture-token", parquet_dir=parquet_dir, now=clock,
        )

    try:
        terminal = run_once(
            db_path=database, task_id=task_id, worker_id="b91-mixed-unknown", lease_for=timedelta(minutes=5),
            handlers={"morning_scan": morning_handler}, clock=clock, require_b76_contract=True,
        )
        assert terminal is not None and terminal.status == "completed"
        assert discovery_wire_entered.is_set()
        with sqlite3.connect(database) as conn:
            task = conn.execute("SELECT status,stage,checkpoint_json FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()
            unsettled = conn.execute("SELECT stage,item_key,state FROM k10_external_attempts WHERE task_id=? AND state IN ('started','unknown')", (task_id,)).fetchall()
            report_row = conn.execute("SELECT report_id,scan_id,status FROM k10_v2_report_runs WHERE window_kind='morning'").fetchone()
            review_rows = conn.execute("SELECT work_item_id,status FROM k10_morning_review_work_items WHERE scan_id=?", (report_row[1],)).fetchall()
            report_count = conn.execute("SELECT COUNT(*) FROM k10_v2_report_runs").fetchone()[0]
        assert task[:2] == ("completed", "report_partial")
        assert report_row[2] == "partial"
        checkpoint = json.loads(task[2])
        review_stage = "morning" if unknown_kind == "model" else "search"
        assert any(stage == review_stage for stage, _, _ in unsettled), unsettled
        assert any(stage not in {"morning", "search"} for stage, _, _ in unsettled), unsettled
        assert sorted(status for _, status in review_rows) == ["completed", "failed"]
        with base.actual_api(database, **bindings) as client:
            response = client.get(f"/api/v1/k10/v2/reports/{report_row[0]}")
            assert response.status_code == 200
            body = response.json()
        report = body["report"]
        assert body["schemaVersion"] == 10
        assert report["delivery"]["outcome"] == "partial"
        assert report["availableAt"] and datetime.fromisoformat(report["availableAt"]) <= deadline
        assert report["discovery"]["outcome"] == "not_completed"
        items = report["morningReview"]["items"]
        good = next(item for item in items if item["companyCode"] != unknown_company)
        bad = next(item for item in items if item["companyCode"] == unknown_company)
        assert good["status"] == "completed" and good["analysisText"]
        assert good["outcome"] == "no_material_change"
        assert not good["unreviewedOpportunityIds"]
        with sqlite3.connect(database) as conn:
            good_stored = json.loads(conn.execute(
                "SELECT report_item_json FROM k10_morning_review_work_items WHERE work_item_id=?",
                (good["reviewId"],),
            ).fetchone()[0])
        independent_refs = good_stored["content"]["independentVerificationRefs"]
        public_keys = {(ref["documentId"], ref["revision"]) for ref in good["sourceRefs"]}
        with base.actual_api(database, **bindings) as client:
            originals = [ref for ref in independent_refs if
                client.get(f"/api/v1/k10/documents/{ref['documentId']}",
                           params={"revision": ref["revision"], "offset": 0, "limit": 6000}).json().get("contentKind") == "original"]
        assert originals and all((ref["documentId"], ref["revision"]) in public_keys for ref in originals)
        assert bad["status"] != "completed" and bad["unreviewedOpportunityIds"]
        gaps = report["delivery"]["gaps"]
        assert any(gap["reasonCode"] == "morning_discovery_deadline" for gap in gaps)
        assert any(gap["stage"] == "morning_review" and gap["unitId"] == bad["reviewId"] for gap in gaps)
        # Negative cases reuse the real producer-owned identities and pending
        # wire rows. No row or binding is fabricated to manufacture a pass.
        classify = pipeline._b90_isolated_morning_unsettled_dependencies
        kwargs = dict(task_id=task_id, db_path=database,
                      review_work_item_ids=[identity for identity, _ in review_rows],
                      delivery=report["delivery"], scan_id=report_row[1], discovery_deadline_declared=True)
        assert classify(**kwargs) == (True, [bad["reviewId"]])
        for stage in ("discovery", "morning_review"):
            missing = dict(report["delivery"], gaps=[gap for gap in gaps if gap["stage"] != stage])
            assert classify(**(kwargs | {"delivery": missing})) is None
        assert classify(**(kwargs | {"review_work_item_ids": [good["reviewId"]]})) is None
        assert classify(**(kwargs | {"discovery_deadline_declared": False})) is None
        assert classify(**(kwargs | {"scan_id": "not-the-produced-scan"})) is None
        before = json.dumps(body, sort_keys=True)
        wires_before = (len(model_wires), len(search_wires))
    finally:
        release_discovery_wire.set()
    assert discovery_wire_finished.wait(2), "the isolated late wire must finish before artifact cleanup"
    time.sleep(0.35)
    with base.actual_api(database, **bindings) as client:
        after = client.get(f"/api/v1/k10/v2/reports/{report_row[0]}").json()
    assert json.dumps(after, sort_keys=True) == before, "late settlement cannot mutate published identity/content"
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_v2_report_runs").fetchone()[0] == report_count
        pending = conn.execute("SELECT stage,item_key,state FROM k10_external_attempts WHERE task_id=? AND state IN ('started','unknown')", (task_id,)).fetchall()
    assert pending and all(stage == review_stage for stage, _, _ in pending)
    assert run_once(db_path=database, task_id=task_id, worker_id="b91-do-not-replay",
                    lease_for=timedelta(minutes=5), handlers={"morning_scan": morning_handler},
                    clock=clock, require_b76_contract=True) is None
    assert (len(model_wires), len(search_wires)) == wires_before, "a completed partial must not rebill either unknown dependency"
    return {"database": database, "bindings": bindings, "taskId": task_id,
            "report": body, "completedCompany": good, "failedCompany": bad,
            "pendingAttempts": pending, "checkpoint": checkpoint}


@pytest.mark.parametrize("unknown_kind", ["model", "search", "extract"])
def test_mixed_unknown_keeps_formal_partial_and_requires_each_disclosed_dependency(tmp_path, monkeypatch, unknown_kind):
    generate_mixed_unknown_acceptance(tmp_path, monkeypatch, unknown_kind=unknown_kind)
