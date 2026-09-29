"""3.6.2: frozen-source normalization, local failure truth and cheap continuation."""
from __future__ import annotations

import copy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
from threading import Event, Lock, Thread
from types import SimpleNamespace
import time
import tracemalloc

import pytest

from neckline.k10 import pipeline, research_runtime, store
from neckline.k10.discovery import DiscoveryDocument, DiscoverySliceYield, EvidenceRef
from neckline.k10.model_execution import execute_model_operation
from neckline.k10.research_material import source_material_for_understand, read_locator
from neckline.k10.schema import initialize_schema
from neckline.llm.base import LLMResult
from tests import v340_acceptance_fixture as base
from tests.test_b82_claim_identity import _claim
from tests.test_v350_cli_api import DirectRoundTransport, read_actual_api, explicit_bindings
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from tests.k10_v306_fixture import append_approved_execution_profile
from neckline.k10.notifications import dispatch_task_notifications, NotificationRetryPolicy


REF = EvidenceRef("frozen-body", 1)


def body_reply():
    return {"needsFullText": False, "events": [{
        "canonicalKey": "reported-fact", "stageKey": "reported", "eventState": "reported",
        "headline": "公司披露项目送样", "eventKind": "company", "facts": {"currentFacts": "项目送样"},
        "claims": [_claim(source_ref=None)],
    }]}


def test_frozen_body_source_can_supply_only_omitted_duplicate_references():
    raw = body_reply()
    before = copy.deepcopy(raw)
    events, _ = pipeline.DeepSeekDiscoveryModel._decode_understand(
        raw, require_claims=True, full_text=True, frozen_source_ref=REF)
    assert raw == before
    assert events[0].source_refs == (REF,)
    assert events[0].facts["researchClaims"][0]["sourceRef"] == {"documentId": REF.document_id, "revision": 1}
    with pytest.raises(pipeline.PipelineError):
        pipeline.DeepSeekDiscoveryModel._decode_understand(raw, require_claims=True, full_text=True)
    raw["events"][0]["sourceRefs"] = [{"documentId": "wrong", "revision": 1}]
    with pytest.raises(pipeline.PipelineError):
        pipeline.DeepSeekDiscoveryModel._decode_understand(
            raw, require_claims=True, full_text=True, frozen_source_ref=REF)


def test_missing_novelty_is_precise_local_contract_gap_not_a_business_default():
    raw = body_reply()
    del raw["events"][0]["claims"][0]["novelty"]
    before = copy.deepcopy(raw)
    with pytest.raises(pipeline.PipelineError) as failed:
        pipeline.DeepSeekDiscoveryModel._decode_understand(
            raw, require_claims=True, full_text=True, frozen_source_ref=REF)
    assert failed.value.code == "understand_json_contract_invalid"
    assert failed.value.__cause__.field_name == "novelty"
    assert raw == before


@pytest.mark.parametrize("bad_refs", [None, [], [{"documentId": "frozen-body", "revision": 2}],
    [{"documentId": "frozen-body", "revision": 1}, {"documentId": "other-body", "revision": 1}]])
def test_frozen_body_source_never_overwrites_explicit_invalid_reference(bad_refs):
    raw = body_reply()
    raw["events"][0]["sourceRefs"] = bad_refs
    before = copy.deepcopy(raw)
    with pytest.raises(pipeline.PipelineError):
        pipeline.DeepSeekDiscoveryModel._decode_understand(
            raw, require_claims=True, full_text=True, frozen_source_ref=REF)
    assert raw == before


def test_source_context_requires_actual_body_or_read_fragment(monkeypatch):
    model = object.__new__(pipeline.DeepSeekDiscoveryModel)
    model._thread_usage = SimpleNamespace()
    monkeypatch.setattr(model, "_uses_investigation_contract", lambda: True)
    document = DiscoveryDocument(REF.document_id, REF.revision, None, "2026-09-29T00:00:00+00:00",
                                 "项目送样", None, {})
    raw = body_reply()
    location = raw["events"][0]["claims"][0]["location"]
    material = {"textMode": "excerpt", "text": "目录预览", "readResults": []}
    with pytest.raises(pipeline.PipelineError):
        model._validate_material_reply(raw, document, material)
    material["text"] = "项目送样"
    material["readResults"] = [{"locator": location, "text": "项目送样"}]
    result = model._validate_material_reply(raw, document, material)
    assert result["events"][0]["sourceRefs"] == [{"documentId": REF.document_id, "revision": REF.revision}]
    material["readResults"] = [{"locator": "unread-claim-location", "text": "项目送样"}]
    with pytest.raises(pipeline.PipelineError) as failed:
        model._validate_material_reply(raw, document, material)
    assert failed.value.code == "understand_reference_invalid"


def test_reasoning_only_response_is_never_reported_as_network_exhaustion(tmp_path):
    db = tmp_path / "response-empty.sqlite"
    initialize_schema(db)
    stamp = "2026-09-29T00:35:00+00:00"
    store.set_run_control(state="open", reason_code="isolated_test", changed_at=stamp,
                          changed_by="v362_test", db_path=db)
    store.enqueue_task(task_id="model-empty", kind="evening_scan", idempotency_key="model-empty",
        input_version="isolated", input_cutoff_at=stamp, payload={}, budget={}, created_at=stamp, db_path=db)
    config_id, revision = append_approved_execution_profile(db_path=db, created_at=stamp, config_id="empty-profile")
    store.bind_task_execution(task_id="model-empty", execution_config_id=config_id,
        execution_config_revision=revision, binding_kind="scheduled", bound_at=stamp, db_path=db)
    calls = []
    def received_empty():
        calls.append(1)
        return LLMResult(ok=False, content="", error_code="response_empty", reason="缺少最终答案",
                         prompt_tokens=5840, completion_tokens=899, total_tokens=6739)
    kwargs = dict(task_id="model-empty", operation="investigation_research_round", item_key="round-1",
        input_sha256="a" * 64, policy={"networkMaxAttempts": 2, "jsonRepairMaxAttempts": 1},
        operation_call=received_empty, validate=lambda value: value, db_path=db)
    for _ in range(3):
        result = execute_model_operation(**kwargs)
    assert result.safe_error_code == "response_empty"
    assert len(calls) == 1, "a settled empty response is not a transport retry or a new repair budget"
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT safe_error_code,network_attempt_count FROM k10_execution_item_checkpoints").fetchone()
    assert row == ("response_empty", 1)


@pytest.mark.parametrize("known_scope", [True, False])
def test_real_cli_keeps_valid_peer_when_novelty_missing_and_recovers_omitted_source(tmp_path, monkeypatch, known_scope):
    class BrokenBodyTransport(DirectRoundTransport):
        def respond(self, request):
            response = super().respond(request)
            packet = self._packet(request)
            if not known_scope and "items" in packet and "inputCount" not in packet:
                result = json.loads(response.json()["choices"][0]["message"]["content"])
                for row in result.get("items", []):
                    if self._number_from_title(packet["items"][row["i"]]["title"]) == 1:
                        row["companyCodes"] = []
                return self._ok(result)
            if isinstance(packet.get("documentId"), str) and "output" in packet:
                result = json.loads(response.json()["choices"][0]["message"]["content"])
                if "events" in result:
                    for event in result["events"]:
                        event.pop("sourceRefs", None)
                        for claim in event["claims"]:
                            claim.pop("sourceRef", None)
                            if event["canonicalKey"] == "event-001":
                                claim.pop("novelty", None)
                    return self._ok(result)
            return response
    monkeypatch.setattr(base, "TITLE_COUNT", 8)
    monkeypatch.setattr(base, "DeterministicTransport", BrokenBodyTransport)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name="v362-body", selected_event_count=3)
    assert flow.task_status == "completed"
    assert flow.calls["understand"] == 4  # one existing repair for missing novelty
    envelope, materials, _, _ = read_actual_api(flow.db_path)
    report = envelope["report"]
    assert report["delivery"]["outcome"] == "partial"
    assert report["delivery"]["rankingScope"] == ("completed_subset" if known_scope else "none")
    assert report["discovery"]["outcome"] == "not_completed"
    assert materials["items"], "unaffected readable source facts must remain materials"
    with sqlite3.connect(flow.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_external_attempts WHERE state IN ('started','running','unknown')").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM k10_research_snapshot_revisions WHERE revision=1").fetchone() == (2,)
    if not known_scope:
        with sqlite3.connect(flow.db_path) as conn:
            notification_id = conn.execute("SELECT notification_id FROM k10_task_notifications WHERE task_id=?",
                                           (flow.task_id,)).fetchone()[0]
        assert dispatch_task_notifications(db_path=flow.db_path, list_device_tokens=lambda: [],
            delete_device=lambda _token: False, sender=lambda **_kwargs: pytest.fail("zero devices must not send"),
            worker_id="v362-no-devices", now=datetime.now(timezone.utc),
            retry_policy=NotificationRetryPolicy(timedelta(seconds=1), timedelta(seconds=60)),
            notification_id=notification_id) == 1
        envelope, _, _, _ = read_actual_api(flow.db_path)
        assert envelope["report"]["notificationEvidence"] == {"state": "no_registered_devices",
            "acceptedDeviceCount": 0, "registeredDeviceCount": 0, "deviceDisplayState": "unverified"}
        export = os.environ.get("NK_V362_API_DIR")
        if export:
            directory = Path(export)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "partial.json").write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n")
            (directory / "partial-materials.json").write_text(json.dumps(materials, ensure_ascii=False, indent=2) + "\n")


def test_continuation_profiles_and_reuses_terminal_outcomes_without_context_rebuild(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "TITLE_COUNT", 12)
    monkeypatch.setattr(base, "DeterministicTransport", DirectRoundTransport)
    original = pipeline._research_outcome
    original_scope = research_runtime._Investigation._company_scope
    original_context = research_runtime._Investigation._b78_comparison_context
    counts = {"scope": 0, "context": 0, "submitted": 0, "yielded": 0}
    first_outcomes = {}
    lock = Lock()
    def scope(self):
        with lock:
            counts["scope"] += 1
        return original_scope(self)
    def context(self, *args, **kwargs):
        with lock:
            counts["context"] += 1
        return original_context(self, *args, **kwargs)
    def slice_once(**kwargs):
        outcome = original(**kwargs)
        with lock:
            counts["submitted"] += 1
            key = kwargs["event"].canonical_key
            if key in first_outcomes:
                assert asdict(outcome) == first_outcomes[key], "cached facts, references and comparison must be identical"
            else:
                first_outcomes[key] = asdict(outcome)
            if not counts["yielded"]:
                counts["yielded"] = 1
                raise DiscoverySliceYield()
        return outcome
    monkeypatch.setattr(research_runtime._Investigation, "_company_scope", scope)
    monkeypatch.setattr(research_runtime._Investigation, "_b78_comparison_context", context)
    monkeypatch.setattr(pipeline, "_research_outcome", slice_once)
    tracemalloc.start()
    started = time.perf_counter()
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name="v362-continuation", selected_event_count=8)
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print("v362-profile", json.dumps({**counts, "elapsedSeconds": round(elapsed, 3), "peakBytes": peak,
        "researchWires": flow.calls.get("research:research_round", 0)}))
    assert flow.task_status == "completed" and flow.continuation_count >= 1
    assert counts["submitted"] > 8
    assert counts["scope"] == 8, "terminal research must avoid repeated company/history/context construction"
    assert flow.calls["research:research_round"] == 8
    report, _, _, _ = read_actual_api(flow.db_path)
    assert report["report"]["delivery"]["outcome"] == "complete"
    from neckline.k10.research_store import read_terminal_outcome
    with sqlite3.connect(flow.db_path) as conn:
        snapshot_id, context_sha, raw = conn.execute(
            "SELECT snapshot_id,context_sha256,json_extract(snapshot_json,'$.terminalOutcome') "
            "FROM k10_research_snapshot_revisions WHERE revision=2 LIMIT 1").fetchone()
        terminal = json.loads(raw)
    args = dict(snapshot_id=snapshot_id, task_id=flow.task_id, context_sha256=context_sha,
                profile_sha256=terminal["profileSha256"], db_path=flow.db_path)
    assert read_terminal_outcome(**args) == terminal["outcome"]
    for field in ("task_id", "context_sha256", "profile_sha256"):
        assert read_terminal_outcome(**(args | {field: "mismatched"})) is None
    with sqlite3.connect(flow.db_path) as conn:
        conn.execute("UPDATE k10_research_snapshot_revisions SET execution_status='failed' "
                     "WHERE snapshot_id=? AND revision=2", (snapshot_id,))
    assert read_terminal_outcome(**args) is None


def test_summary_write_interruption_reuses_durable_round_without_another_wire(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "TITLE_COUNT", 4)
    monkeypatch.setattr(base, "DeterministicTransport", DirectRoundTransport)
    original = research_runtime.persist_terminal_outcome
    interrupted = []

    def save_summary(**kwargs):
        if not interrupted:
            with sqlite3.connect(kwargs["db_path"]) as conn:
                assert conn.execute("SELECT COUNT(*) FROM k10_research_round_results WHERE snapshot_id=?",
                                    (kwargs["snapshot_id"],)).fetchone()[0] > 0
            interrupted.append(kwargs["snapshot_id"])
            raise DiscoverySliceYield()
        return original(**kwargs)

    monkeypatch.setattr(research_runtime, "persist_terminal_outcome", save_summary)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name="v362-summary-interrupted", selected_event_count=2)
    assert interrupted and flow.task_status == "completed" and flow.continuation_count >= 1
    assert flow.calls["research:research_round"] == 2
    report, materials, _, _ = read_actual_api(flow.db_path)
    assert report["report"]["delivery"]["outcome"] == "complete" and materials["items"]
    with sqlite3.connect(flow.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_external_attempts WHERE state IN ('started','running','unknown')").fetchone() == (0,)


def test_real_morning_empty_frozen_reviews_lend_six_slots_without_new_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "TITLE_COUNT", 10)
    monkeypatch.setattr(base, "DeterministicTransport", DirectRoundTransport)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name="v362-empty-parent", selected_event_count=0)
    database = flow.db_path
    binding = explicit_bindings(database)
    saturated, release = Event(), Event()
    lock = Lock()
    active = peak = 0
    scan_faults = []
    original_execute = pipeline.execute_scan
    def capture_execute(*args, **kwargs):
        try:
            return original_execute(*args, **kwargs)
        except Exception as exc:
            import traceback
            scan_faults.append(traceback.format_exc())
            raise
    monkeypatch.setattr(pipeline, "execute_scan", capture_execute)
    original = DirectRoundTransport.respond
    def respond(self, request):
        nonlocal active, peak
        self.selected_event_count = 8
        if self._packet(request).get("action") != "research_round":
            return original(self, request)
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 6:
                saturated.set()
        try:
            assert release.wait(15)
            return original(self, request)
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(DirectRoundTransport, "respond", respond)
    def resolve(**_kwargs):
        provider = MeteredProvider(ledger_db=database, ledger_task="discovery", api_key="fixture",
            model="deepseek-v4-pro", name="fixture", api_url="https://fixture.invalid/v1/chat/completions",
            read_timeout=1, use_streaming=False)
        provider.max_attempts = 1
        return ProviderResolution("configured", provider, "fixture", None)
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", resolve)
    at = datetime(2026, 9, 9, 8, 35, tzinfo=base.SHANGHAI)
    monkeypatch.setattr(pipeline, "_now", lambda: at)
    output = StringIO()
    with base.redirect_stdout(output):
        assert base.cli_main(["enqueue", "--db", str(database), "--kind", "morning", "--trading-day", "2026-09-09",
            "--config-id", binding["config_id"], "--config-revision", str(binding["config_revision"]),
            "--execution-config-id", binding["execution_id"],
            "--execution-config-revision", str(binding["execution_revision"])]) == 0
    task_id = output.getvalue().strip()
    results, faults = [], []
    def worker():
        try:
            results.append(run_once(db_path=database, task_id=task_id, worker_id="v362-six-slots",
                lease_for=timedelta(minutes=5), clock=lambda: at, require_b76_contract=True,
                handlers={"morning_scan": lambda context: pipeline.production_scan_handler(
                    context, tushare_token="fixture-token", parquet_dir=tmp_path / "morning-parquet", now=lambda: at)}))
        except Exception as exc:
            faults.append(exc)
    thread = Thread(target=worker)
    thread.start()
    try:
        assert saturated.wait(10), {"peak": peak, "faults": faults, "scanFaults": scan_faults}
        assert peak == 6
    finally:
        release.set()
        thread.join(20)
    assert not thread.is_alive() and not faults
    assert results[0].status == "completed" and peak <= 6
    with base.actual_api(database, **binding) as client:
        report = client.get("/api/v1/k10/v2/reports/latest?window=morning").json()["report"]
    assert report["morningReview"]["targetCompanyCount"] == 0
    assert report["delivery"]["outcome"] == "complete"


@pytest.mark.parametrize("omit_event_source", [False, True])
def test_structural_read_restores_only_frozen_source_fields(omit_event_source, monkeypatch):
    text = "公司披露项目送样。\n\n" + "本次报道保留公司新进展。\n\n" * 1000
    document = DiscoveryDocument(REF.document_id, REF.revision, None,
        "2026-09-29T00:00:00+00:00", text, None, {"title": "项目送样报道"})
    material = source_material_for_understand(document, max_characters=len(text))
    assert material["textMode"] == "structural_outline" and material["text"] == ""
    locator = material["sourceIndex"]["locators"][0]["locator"]
    part = read_locator(document, locator, max_characters=len(text) * 2)
    assert part["text"].strip()
    material = {**material, "textMode": "structural_read", "text": "", "readResults": [part]}
    model = object.__new__(pipeline.DeepSeekDiscoveryModel)
    model._thread_usage = SimpleNamespace()
    monkeypatch.setattr(model, "_uses_investigation_contract", lambda: True)
    raw = body_reply()
    if not omit_event_source:
        raw["events"][0]["sourceRefs"] = [{"documentId": REF.document_id, "revision": REF.revision}]
    raw["events"][0]["claims"][0]["location"] = locator
    before = copy.deepcopy(raw)
    result = model._validate_material_reply(raw, document, material)
    assert raw == before
    assert result["events"][0]["sourceRefs"] == [{"documentId": REF.document_id, "revision": REF.revision}]
    assert result["events"][0]["facts"]["researchClaims"][0]["sourceRef"] == {"documentId": REF.document_id, "revision": REF.revision}
    # A read of one locator must not authorize an unread or empty locator.
    for reads in [[], [{**part, "text": "  "}], [{**part, "locator": "unread-locator"}],
                  [{**part, "text": ""}, {**part, "locator": "other-read-locator"}]]:
        with pytest.raises(pipeline.PipelineError):
            model._validate_material_reply(raw, document, {**material, "readResults": reads})
    invalid = copy.deepcopy(raw)
    invalid["events"][0]["sourceRefs"] = [{"documentId": "wrong-source", "revision": REF.revision}]
    with pytest.raises(pipeline.PipelineError):
        model._validate_material_reply(invalid, document, material)


@pytest.mark.parametrize("omit_event_source", [False, True])
def test_real_cli_structural_reads_reach_report_without_spurious_repair(tmp_path, monkeypatch, omit_event_source):
    original_news = base._FullScaleNews
    class LongNews(original_news):
        def fetch_incremental(self, request):
            result = super().fetch_incremental(request)
            rows = list(result.documents)
            rows[0] = replace(rows[0], original_text="公司披露项目送样。\n\n" + "本次报道保留公司新进展。\n\n" * 1000)
            return replace(result, documents=tuple(rows))
    observed_reads = []
    class LongBodyTransport(DirectRoundTransport):
        def respond(self, request):
            packet = self._packet(request)
            material = packet.get("sourceMaterial")
            if isinstance(material, dict) and material["textMode"] != "full_text":
                assert packet["text"] == "", "retain the production structural-read shape"
                if not material.get("readResults"):
                    self._record("sourceRead")
                    return self._ok({"sourceRead": {"location": material["sourceIndex"]["locators"][0]["locator"]}})
                observed_reads.append(material["readResults"])
                result = json.loads(super().respond(request).json()["choices"][0]["message"]["content"])
                for event in result.get("events", []):
                    if omit_event_source:
                        event.pop("sourceRefs", None)
                    for claim in event["claims"]:
                        claim.pop("sourceRef", None)
                        claim["location"] = material["readResults"][0]["locator"]
                return self._ok(result)
            return super().respond(request)
    monkeypatch.setattr(base, "TITLE_COUNT", 3)
    monkeypatch.setattr(base, "_FullScaleNews", LongNews)
    monkeypatch.setattr(base, "DeterministicTransport", LongBodyTransport)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name="structural-read-omission", selected_event_count=3)
    assert flow.task_status == "completed"
    assert len(observed_reads) == 1, "valid read output must not trigger JSON repair"
    assert observed_reads[0][0]["text"].strip()
    envelope, materials, _, _ = read_actual_api(flow.db_path)
    report = envelope["report"]
    assert report["delivery"]["outcome"] == "complete"
    assert not [gap for gap in report["delivery"]["gaps"] if gap["stage"] == "understand"]
    assert len(materials["items"]) == 3
    with sqlite3.connect(flow.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_external_attempts WHERE state IN ('started','running','unknown')").fetchone() == (0,)
