"""B91 real morning scheduler admission under a saturated discovery sibling."""
from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
import json
from pathlib import Path
import sqlite3
from threading import Event, Lock, Thread, current_thread
import time
import traceback

from neckline.k10 import morning_runtime, pipeline, store
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from tests import v340_acceptance_fixture as base
from tests import test_v350_cli_api as cli_api
from tests.test_v350_cli_api import DirectRoundTransport, explicit_bindings, generate_acceptance


def test_b91_review_admission_stays_fair_while_discovery_is_busy(tmp_path, monkeypatch):
    """Frozen parent companies use their own share and advance past a slow peer.

    This goes through the public evening/morning CLI and worker.  The first
    review and the sibling discovery channel are both deliberately held.  The
    fourth company must nevertheless enter review before either hold releases,
    proving the frozen six-slot budget is split and the review queue replaces
    completed peers fairly instead of serializing behind its first company.
    """
    monkeypatch.setattr(cli_api, "EVENT_COUNT", 5)
    monkeypatch.setattr(cli_api, "TITLE_COUNT", 8)
    append_execution = store.append_execution_config

    def small_title_batches(**kwargs):
        payload = json.loads(json.dumps(kwargs["payload"]))
        payload["discovery"]["titleBatchSize"] = 1
        return append_execution(**(kwargs | {"payload": payload}))

    # Freeze enough genuine title batches before the public producer; never
    # repair a task binding merely to make its worker reach the tested stage.
    monkeypatch.setattr(store, "append_execution_config", small_title_batches)
    evening = generate_acceptance(tmp_path / "producer", monkeypatch, scenario="complete")
    database = evening.database
    bindings = explicit_bindings(database)
    parent_cards = evening.report["report"]["eveningCards"]
    assert len(parent_cards) == 4, "five deterministic events yield four companies"
    expected_companies = {card["companyCode"] for card in parent_cards}
    slow_company = parent_cards[0]["companyCode"]

    discovery_saturated, release_discovery = Event(), Event()
    review_saturated, release_fast_reviews = Event(), Event()
    slow_started, release_slow = Event(), Event()
    titles_saturated, release_titles = Event(), Event()
    state_lock = Lock()
    first_review_companies: list[str] = []
    observed_limits: dict[str, int] = {}
    discovery_errors: list[str] = []
    active_model_requests = 0
    peak_model_requests = 0
    active_discovery_research = 0
    active_title_requests = 0
    original_respond = DirectRoundTransport.respond
    original_execute_scan = pipeline.execute_scan
    original_run_reviews = pipeline._run_morning_reviews
    original_research_outcome = pipeline._research_outcome

    def capture_execute_scan(*args, **kwargs):
        if kwargs.get("kind") == "morning":
            observed_limits["discovery"] = kwargs.get("deep_read_concurrency_limit")
        try:
            return original_execute_scan(*args, **kwargs)
        except Exception as exc:
            discovery_errors.append(f"{type(exc).__name__}: {exc}")
            raise

    def capture_run_reviews(*args, **kwargs):
        observed_limits["reviews"] = kwargs.get("review_concurrency")
        return original_run_reviews(*args, **kwargs)

    def capture_research_outcome(**kwargs):
        try:
            return original_research_outcome(**kwargs)
        except Exception:
            discovery_errors.append(traceback.format_exc())
            raise

    def handle_respond(self, request):
        nonlocal active_discovery_research, active_title_requests
        wire = json.loads(request.content)
        message = wire["messages"][-1]["content"]
        packet = self._packet(request) if "<untrusted-evidence>" not in message else {}
        if "items" in packet and "inputCount" not in packet and not packet.get("action"):
            with state_lock:
                active_title_requests += 1
                if active_title_requests == 3:
                    titles_saturated.set()
            try:
                assert release_titles.wait(15), "test must release the discovery title batches"
                return original_respond(self, request)
            finally:
                with state_lock:
                    active_title_requests -= 1
        # The sibling runs five newly selected events.  Hold its first three
        # actual research wires, rather than a title-only request, so the
        # observed peak covers the frozen deep-read share itself.
        if packet.get("action") == "research_round":
            with state_lock:
                active_discovery_research += 1
                if active_discovery_research == 3:
                    discovery_saturated.set()
            try:
                assert release_discovery.wait(15), "test must release the discovery research wires"
            finally:
                with state_lock:
                    active_discovery_research -= 1
            return original_respond(self, request)
        if "<untrusted-evidence>" in message:
            try:
                evidence = json.loads(message.split("<untrusted-evidence>\n", 1)[1].split("\n</untrusted-evidence>", 1)[0])
                company = evidence["original"]["candidate"]["companyCode"]
                is_initial = not evidence["independentVerificationDocuments"]
                position = None
                if is_initial:
                    with state_lock:
                        if company not in first_review_companies:
                            first_review_companies.append(company)
                        position = first_review_companies.index(company) + 1
                        if len(first_review_companies) >= 3:
                            review_saturated.set()
                    if company == slow_company and not slow_started.is_set():
                        slow_started.set()
                        assert release_slow.wait(15), "test must release the slow review"
                    elif position is not None and position <= 3:
                        assert release_fast_reviews.wait(15), "test must release the other review slots"
                if is_initial:
                    return self._ok({
                        "action": "search", "question": "隔夜是否有独立反证？",
                        "query": f"离线晨报 {company} 独立核验",
                        "rationale": "复核冻结理由。",
                    })
                return self._ok({
                    "action": "conclude", "material": False, "reasonStatus": "current",
                    "observationStatus": "current", "summary": "独立资料未见改变冻结理由的事实。",
                    "materialContraryEvidence": [],
                })
            finally:
                pass
        if (current_thread().name.startswith("k10-morning-discovery")
                and "<untrusted-evidence>" not in message):
            packet = self._packet(request)
        return original_respond(self, request)

    def controlled_respond(self, request):
        nonlocal active_model_requests, peak_model_requests
        with state_lock:
            active_model_requests += 1
            peak_model_requests = max(peak_model_requests, active_model_requests)
        try:
            return handle_respond(self, request)
        finally:
            with state_lock:
                active_model_requests -= 1

    monkeypatch.setattr(DirectRoundTransport, "respond", controlled_respond)
    monkeypatch.setattr(DirectRoundTransport, "loopback_morning_discovery_zero", False)
    monkeypatch.setattr(pipeline, "execute_scan", capture_execute_scan)
    monkeypatch.setattr(pipeline, "_run_morning_reviews", capture_run_reviews)
    monkeypatch.setattr(pipeline, "_research_outcome", capture_research_outcome)
    morning_time = datetime(2026, 9, 9, 8, 35, tzinfo=base.SHANGHAI)
    monkeypatch.setattr(pipeline, "_now", lambda: morning_time)

    def resolver(**_kwargs):
        provider = MeteredProvider(
            ledger_db=database, ledger_task="discovery", api_key="fixture", model="deepseek-v4-pro", name="fixture",
            api_url="https://fixture.invalid/v1/chat/completions", read_timeout=1, use_streaming=False,
        )
        provider.max_attempts = 1
        return ProviderResolution("configured", provider, "fixture", None)

    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", resolver)
    monkeypatch.setattr(morning_runtime, "resolve_deepseek_v4_pro", resolver)
    stdout = StringIO()
    with base.redirect_stdout(stdout):
        assert base.cli_main([
            "enqueue", "--db", str(database), "--kind", "morning", "--trading-day", "2026-09-09",
            "--config-id", bindings["config_id"], "--config-revision", str(bindings["config_revision"]),
            "--execution-config-id", bindings["execution_id"],
            "--execution-config-revision", str(bindings["execution_revision"]),
        ]) == 0
    task_id = stdout.getvalue().strip()

    terminal: list[object] = []

    def worker() -> None:
        def handler(context):
            return pipeline.production_scan_handler(
                context, tushare_token="fixture-token", parquet_dir=tmp_path / "parquet", now=lambda: morning_time,
            )
        terminal.append(run_once(
            db_path=database, task_id=task_id, worker_id="b91-fairness", lease_for=timedelta(minutes=5),
            handlers={"morning_scan": handler}, clock=lambda: morning_time, require_b76_contract=True,
        ))

    thread = Thread(target=worker, name="b91-fairness-worker")
    thread.start()
    try:
        assert slow_started.wait(10), "first frozen parent review did not start"
        assert review_saturated.wait(10), "all review slots did not enter their first work item"
        assert titles_saturated.wait(10), {"errors": discovery_errors, "limits": observed_limits}
        with state_lock:
            assert active_title_requests == 3 and active_model_requests == 6
            assert peak_model_requests == 6
        release_titles.set()
        assert discovery_saturated.wait(10), {"errors": discovery_errors, "limits": observed_limits}
        with state_lock:
            assert peak_model_requests == 6
            assert active_discovery_research == 3
        release_fast_reviews.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with state_lock:
                started = set(first_review_companies)
            if len(started) >= 4:
                break
            time.sleep(0.02)
        with state_lock:
            started = set(first_review_companies)
        assert len(started) >= 4, {
            "started": sorted(started), "slow": slow_company, "limits": observed_limits,
        }
        assert started <= expected_companies
        assert observed_limits == {"discovery": 3, "reviews": 3}
        with state_lock:
            assert peak_model_requests <= 6
    finally:
        release_slow.set()
        release_titles.set()
        release_fast_reviews.set()
        release_discovery.set()
        thread.join(20)
    assert not thread.is_alive()
    assert terminal and terminal[0] is not None and terminal[0].status == "completed"

    with sqlite3.connect(database) as connection:
        report_row = connection.execute(
            "SELECT report_id,status FROM k10_v2_report_runs WHERE window_kind='morning' "
            "ORDER BY created_at DESC,report_id DESC LIMIT 1"
        ).fetchone()
        unresolved = connection.execute(
            "SELECT COUNT(*) FROM k10_external_attempts WHERE task_id=? AND state IN ('started','running','unknown')",
            (task_id,),
        ).fetchone()
    assert report_row is not None and report_row[1] == "completed"
    assert unresolved == (0,)
    with base.actual_api(database, **bindings) as client:
        report = client.get("/api/v1/k10/v2/reports/latest", params={"window": "morning"})
        assert report.status_code == 200
        payload = report.json()["report"]
        assert payload["delivery"]["outcome"] == "complete"
        assert {item["companyCode"] for item in payload["morningReview"]["items"]} == expected_companies
