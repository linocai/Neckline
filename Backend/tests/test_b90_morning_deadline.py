"""B90 morning deadline acceptance through the real CLI and worker boundary."""
from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
import json
from pathlib import Path
import sqlite3
from threading import Event, current_thread
import time

from neckline.k10 import morning_runtime, pipeline
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from tests import v340_acceptance_fixture as base
from tests.test_v350_cli_api import DirectRoundTransport, explicit_bindings, generate_acceptance


def test_b90_deadline_seals_only_discovery_and_publishes_completed_parent_reviews(tmp_path, monkeypatch):
    """A blocked discovery wire cannot turn completed frozen-parent reviews into a missed morning.

    This is deliberately an entry-point regression: the evening parent and the
    morning task are both CLI-enqueued, the worker owns the execution binding,
    and the assertions read the published report through FastAPI.  It never
    inserts a review, a report, or an unknown ledger row directly.
    """
    evening = generate_acceptance(tmp_path / "producer", monkeypatch, scenario="complete")
    database = evening.database
    bindings = explicit_bindings(database)
    deadline = datetime(2026, 9, 9, 9, 20, tzinfo=base.SHANGHAI)
    monotonic_started = time.monotonic()

    def clock() -> datetime:
        # Use the report's real immutable deadline while mapping a few wall
        # seconds into the final frozen envelope.  The production code sees a
        # normal business clock; only the isolated transport is slow.
        return deadline - timedelta(seconds=3) + timedelta(seconds=time.monotonic() - monotonic_started)

    discovery_wire_entered, release_discovery_wire = Event(), Event()
    original_respond = DirectRoundTransport.respond

    def blocking_discovery_respond(self, request):
        wire = json.loads(request.content)
        message = wire["messages"][-1]["content"]
        # Reviews have their own untrusted-evidence envelope and must retain a
        # usable model path while only the sibling discovery channel is held.
        if current_thread().name.startswith("k10-morning-discovery") and "<untrusted-evidence>" not in message:
            packet = self._packet(request)
            if packet.get("inputCount") is not None and not discovery_wire_entered.is_set():
                discovery_wire_entered.set()
                assert release_discovery_wire.wait(12), "test must release the late isolated discovery wire"
        return original_respond(self, request)

    monkeypatch.setattr(DirectRoundTransport, "respond", blocking_discovery_respond)
    # A short test envelope still follows the production finalization path.
    # Review transport is real, but its unrelated 90-second production
    # closeout budget would obscure this discovery-only deadline regression.
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

    # Both channels independently call the same declared source boundary; its
    # exact overnight timestamp makes discovery a real sibling workload.
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

    terminal = run_once(
        db_path=database, task_id=task_id, worker_id="b90-deadline", lease_for=timedelta(minutes=5),
        handlers={"morning_scan": morning_handler}, clock=clock, require_b76_contract=True,
    )
    assert terminal is not None and terminal.status == "completed"
    with sqlite3.connect(database) as _conn:
        assert _conn.execute("SELECT stage FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone() == ("report_partial",)
    assert discovery_wire_entered.is_set(), "the worker must have started real sibling discovery before its deadline"

    with sqlite3.connect(database) as conn:
        row = conn.execute(
            "SELECT report_id,scan_id,status FROM k10_v2_report_runs "
            "WHERE window_kind='morning' ORDER BY created_at DESC, report_id DESC LIMIT 1"
        ).fetchone()
        assert row is not None and row[2] == "partial"
        report_id, scan_id = str(row[0]), str(row[1])
        unresolved = conn.execute(
            "SELECT stage,state FROM k10_external_attempts WHERE task_id=? "
            "AND state IN ('started','unknown') ORDER BY attempt_id", (task_id,),
        ).fetchall()
        review_rows = conn.execute(
            "SELECT status FROM k10_morning_review_work_items WHERE scan_id=? ORDER BY work_item_id", (scan_id,),
        ).fetchall()
        report_count_before = conn.execute("SELECT COUNT(*) FROM k10_v2_report_runs").fetchone()[0]
        card_count_before = conn.execute("SELECT COUNT(*) FROM k10_v2_report_cards WHERE report_id=?", (report_id,)).fetchone()[0]
    assert unresolved and all(stage != "morning" for stage, _state in unresolved), unresolved
    assert review_rows and all(status == "completed" for (status,) in review_rows), review_rows

    with base.actual_api(database, **bindings) as client:
        response = client.get(f"/api/v1/k10/v2/reports/{report_id}")
        assert response.status_code == 200
        report = response.json()["report"]
    assert response.json()["schemaVersion"] == 10
    assert report["delivery"]["outcome"] == "partial"
    assert report["discovery"] == {
        "state": "partial", "outcome": "not_completed", "companyCount": 0,
        "reasonCodes": ["morning_discovery_deadline"],
    }
    review = report["morningReview"]
    assert review["state"] == "complete"
    assert review["items"] and all(item["status"] == "completed" for item in review["items"])
    assert any(gap["reasonCode"] == "morning_discovery_deadline" for gap in report["delivery"]["gaps"])

    # The original unknown attempt may settle after the response returns; it
    # cannot add cards, change the frozen report, or trigger a second publish.
    release_discovery_wire.set()
    time.sleep(0.35)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_v2_report_runs").fetchone()[0] == report_count_before
        assert conn.execute("SELECT COUNT(*) FROM k10_v2_report_cards WHERE report_id=?", (report_id,)).fetchone()[0] == card_count_before
