from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import json
import threading
import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
from neckline.k10.discovery import CandidateComparison, CompanyMappingDraft, DiscoveryDocument, EvidenceRef, EventComparison, EventDraft, Verification, prepare_document_for_analysis, run_discovery
from neckline.k10.pipeline import DeepSeekDiscoveryModel, PipelineError, _CheckpointedDiscoveryModel, _run_morning_reviews, execute_scan
from neckline.k10.metering import MeteredProvider, bind_provider_execution_spending
from neckline.k10.schema import initialize_schema as _initialize_schema
from neckline.k10.sources import SourceCoverage, SourceDocumentInput, SourceFetchResult
from neckline.k10.universe import CompanyMetadata
from neckline.k10.verification import VerificationEvidenceBundle
from neckline.k10.windows import SHANGHAI
from neckline.llm.base import ChatMessage, LLMProvider, LLMResult
from neckline.llm.openai_compat import OpenAICompatProvider
from neckline.k10.types import Task
from neckline.k10.worker import TaskContext, run_once
from tests.k10_v306_fixture import append_approved_execution_profile


DAY = date(2026, 9, 7)
CUTOFF = datetime(2026, 9, 7, 21, tzinfo=SHANGHAI)
CREATED = datetime(2026, 9, 7, 21, 5, tzinfo=SHANGHAI)


def initialize_schema(path):
    import sqlite3
    result = _initialize_schema(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        if not conn.execute("SELECT 1 FROM trade_cal LIMIT 1").fetchone():
            conn.executemany("INSERT INTO trade_cal VALUES('SSE', ?, ?)",
                [("20260904",1),("20260905",0),("20260906",0),("20260907",1),("20260908",1),
                 ("20260909",1),("20260910",1),("20260911",1),("20260912",0),("20260913",0),("20260914",1)])
    return result


@pytest.fixture(autouse=True)
def fixed_test_clock(monkeypatch):
    monkeypatch.setattr("neckline.k10.pipeline._now", lambda: CREATED)


def _configuration():
    import json
    from pathlib import Path
    configuration = json.loads((Path(__file__).parents[1] / "neckline/config/k10-v1.4.json").read_text())
    configuration["sourceAdapters"] = [{"key": "fixture-source", "lateArrivalReplaySeconds": 86400}]
    return configuration


def _deepseek(provider, **kwargs):
    model = DeepSeekDiscoveryModel(provider, **kwargs)
    model.set_execution_policy({
        "documentBatchSize": 2, "understandConcurrency": 2, "keyPassageMaxCharacters": 1000,
        "networkMaxAttempts": 1, "jsonRepairMaxAttempts": 0, "retryBackoffSeconds": [1], "taskSliceSeconds": 60,
        "completionDeadlineSeconds": 7200, "continuationDelaySeconds": 1,
        "modelOptions": {
            "understand": {"maxTokens": 1800, "thinking": {"type": "disabled"}},
            "verify": {"maxTokens": 1800, "thinking": {"type": "enabled"}, "reasoningEffort": "high"},
            "companyComparison": {"maxTokens": 1800, "thinking": {"type": "enabled"}, "reasoningEffort": "high"},
            "prioritize": {"maxTokens": 1800, "thinking": {"type": "enabled"}, "reasoningEffort": "high"},
        },
    })
    return model



class _Adapter:
    coverage = SourceCoverage("fixture-source", "market-wide", "fixture", "bounded", "publishedAt",
                              "publishedAt", True)

    def __init__(self):
        self.request = None

    def fetch_incremental(self, request):
        self.request = request
        published = request.window.start_at + timedelta(minutes=1)
        return SourceFetchResult(
            documents=(SourceDocumentInput("fixture-1", None, "公司的原始公告文本", None, published, "exact",
                                           published + timedelta(minutes=1), "fixture-v1", {"title": "公告"}),),
            next_cursor="next-1", success_watermark=request.window.cutoff_at, pages_fetched=1,
            pages_expected=1, exhausted=True,
        )


class _Model:
    def __init__(self):
        self.usage_records = [{"operation": "fixture", "totalTokens": 7}]

    def understand(self, *, document: DiscoveryDocument):
        return (EventDraft("event-fixture", "announcement", "confirmed", "公告", "disclosure",
                           {"document": document.document_id}, (document.evidence_ref,)),)

    def verify(self, event):
        return Verification("verified", "已核验", event.source_refs)

    def map_companies(self, *, event, verification):
        return (CompanyMappingDraft("300001.SZ", "supply", event.source_refs, {"basis": "公告"}, "fixture"),)

    def compare_event(self, *, event, verification, mappings):
        return EventComparison("事件共同事实", {mapping.company_code: CandidateComparison("具体比较", {"role": "primary", "priorityReason": "公司证据", "gap": "比较关系差异", "rankChangeConditions": "新披露", "twoDayReason": "新披露进入两日观察"}, mapping.relation_evidence, 1) for mapping in mappings}, event.source_refs)

    def classify_opportunity(self, *, event, verification, mapping, comparison, previous):
        exact = next((old for old in previous if old.get("canonicalKey") == event.canonical_key), None)
        return {"kind": "continuation" if exact else "initial", "relatedOpportunityId": exact["opportunityId"] if exact else None,
                "reason": "与已有事实比较", "newFacts": "本轮公司披露", "changedJudgment": None,
                "twoDayReason": "新披露进入两日观察"}

    def prioritize(self, *, candidates):
        return tuple(dict.fromkeys((item.event.canonical_key, item.mapping.company_code) for item in candidates))


class _FixtureVerificationGateway:
    """Explicit, independently traceable verification fixture; never contacts Tavily."""

    def fetch(self, *, event, retrieved_at, cutoff_at, cutoff_inclusive=False):
        document = DiscoveryDocument(f"tavily-{event.canonical_key}", 1,
                                     (cutoff_at - timedelta(minutes=1)).isoformat(), retrieved_at.isoformat(),
                                     "独立核验原文", None, {"provider": "fixture"})
        return VerificationEvidenceBundle("available", (document,), (document,), {"state": "available"})


class _VerifiedModel(_Model):
    """Fixture model that cites the explicit independent evidence supplied above."""

    def verify(self, event):
        return Verification("verified", "已核验", (EvidenceRef(f"tavily-{event.canonical_key}", 1),))


class _Metadata:
    def lookup(self, *, company_code, as_of):
        return CompanyMetadata(company_code, "chinext", False, "801080.SI", as_of)


def _watermark(path: Path):
    store.create_scan(scan_id="previous", window_kind="evening", cutoff_at="2026-09-04T21:00:00+08:00",
                      config_id=None, config_revision=None, status="completed", coverage={},
                      created_at="2026-09-04T21:01:00+08:00", completed_at="2026-09-04T21:01:00+08:00", db_path=path)
    store.append_source_watermark(watermark_id="previous-watermark", source_key="fixture-source", cursor_value="old-cursor",
                                  success_cutoff_at="2026-09-04T21:00:00+08:00", fetched_at="2026-09-04T21:01:00+08:00",
                                  scan_id="previous", created_at="2026-09-04T21:01:00+08:00", db_path=path)


def _append_targeted_verification_document(path: Path) -> str:
    document_id = "tavily-directed-fixture"
    store.append_document_version(
        document_id=document_id, source_key="tavily_verification", external_id="directed-fixture",
        canonical_url="https://example.invalid/verification", content_sha256="a" * 64,
        published_at=(CUTOFF - timedelta(hours=1)).isoformat(), published_precision="exact",
        fetched_at=(CUTOFF - timedelta(minutes=30)).isoformat(), original_text="定向核验资料，不能再次发现",
        excerpt=None, fetch_version="fixture", metadata={"provider": "fixture"},
        created_at=CREATED.isoformat(), db_path=path,
    )
    return document_id


def _frozen_config(path: Path) -> int:
    return store.append_run_config(config_id="fixture", payload=_configuration(), created_at=CREATED.isoformat(), db_path=path)


def _bound_v306_execution(path: Path, *, task_id: str, binding_kind: str = "scheduled", config_id: str = "execution"):
    """Bind the approved V3 title/deep-read profile to one isolated task."""
    profile_id, revision = append_approved_execution_profile(
        db_path=path, created_at=CREATED.isoformat(), config_id=config_id,
    )
    store.enqueue_task(task_id=task_id, kind="evening_scan", idempotency_key=task_id, input_version="fixture",
                       input_cutoff_at=CUTOFF.isoformat(), payload={}, budget={"maxAttempts": 1},
                       created_at=CREATED.isoformat(), db_path=path)
    return store.bind_task_execution(task_id=task_id, execution_config_id=profile_id,
                                     execution_config_revision=revision, binding_kind=binding_kind,
                                     bound_at=CREATED.isoformat(), db_path=path)


def _freeze_single_selected_article(path: Path, *, task_id: str, binding, document: DiscoveryDocument) -> None:
    """Create the V3 selection/admission boundary before direct deep-read testing."""
    ref = {"documentId": document.document_id, "revision": document.revision}
    approved = binding["payload"]["discovery"]["titleTriagePolicy"]
    store.freeze_title_triage_manifest(
        task_id=task_id, input_manifest_sha256=store._hash([ref]), window_kind="evening",
        policy_id=approved["policyId"], policy_revision=approved["revision"],
        policy_content_sha256=approved["contentSha256"], input_count=len([ref]), input_refs=[ref],
        batch_count=1, title_status="frozen", created_at=CREATED.isoformat(), db_path=path,
    )
    store.record_title_triage_item(task_id=task_id, document_id=document.document_id, revision=document.revision,
                                   batch_index=0, disposition="candidate", matter_key="full-document",
                                   merged_ref=None, selection_rank=1, audit_reason="离线全文回归",
                                   created_at=CREATED.isoformat(), db_path=path)
    store.freeze_title_selection_manifest(task_id=task_id, selection_manifest_sha256=store._hash([ref]),
                                          selected_refs=[ref], created_at=CREATED.isoformat(), db_path=path)


def _scan_id_for(path: Path, identity: str) -> str:
    # The production identity is opaque; recover the single fixture scan by its stable
    # frozen task identity rather than duplicating the pipeline hash contract here.
    scans = [item for item in store.list_scans(window_kind="evening", db_path=path) if item["scanId"] != "previous"]
    assert len(scans) == 1
    return scans[0]["scanId"]


def test_evening_scan_uses_prior_source_watermark_and_persists_fake_end_to_end(tmp_path):
    path = tmp_path / "pipeline.sqlite"
    initialize_schema(path)
    assert _frozen_config(path) == 1
    _watermark(path)
    adapter = _Adapter()

    result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                          adapter=adapter, model=_VerifiedModel(), metadata=_Metadata(), created_at=CREATED,
                          config_id="fixture", config_revision=1,
                          verification_gateway=_FixtureVerificationGateway())

    assert result.status == "completed"
    assert result.checkpoint["candidateCount"] == 1
    assert adapter.request.window.start_at == datetime(2026, 9, 4, 21, tzinfo=SHANGHAI)
    replay = store.get_scan(scan_id=result.checkpoint["scanId"], db_path=path)["coverage"]["sourceReplay"]
    assert replay == {"sourceKey": "fixture-source", "nominalStartAt": "2026-09-04T21:00:00+08:00",
                      "effectiveStartAt": "2026-09-04T21:00:00+08:00", "replayStartAt": "2026-09-06T21:00:00+08:00",
                      "cutoffAt": "2026-09-07T21:00:00+08:00", "replaySeconds": 86400, "requestState": "completed"}
    assert adapter.request.previous_cursor == "old-cursor"
    assert len(store.list_candidates(scan_id=result.checkpoint["scanId"], state="offered", db_path=path)) == 1
    assert store.get_scan(scan_id=result.checkpoint["scanId"], db_path=path)["status"] == "completed"
    assert datetime.fromisoformat(store.latest_source_watermark(source_key="fixture-source", db_path=path)["successCutoffAt"]) == CUTOFF.astimezone(timezone.utc)

    replay = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                          adapter=adapter, model=_Model(), metadata=_Metadata(), created_at=CREATED + timedelta(minutes=1),
                          config_id="fixture", config_revision=1, scan_identity="fixed-task")
    # A different identity is a distinct manually requested scan; the task identity itself is stable.
    fixed = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                         adapter=adapter, model=_Model(), metadata=_Metadata(), created_at=CREATED + timedelta(minutes=2),
                         config_id="fixture", config_revision=1, scan_identity="replay-task")
    again = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                         adapter=adapter, model=_Model(), metadata=_Metadata(), created_at=CREATED + timedelta(minutes=3),
                         config_id="fixture", config_revision=1, scan_identity="replay-task")
    assert replay.status == "completed"
    assert fixed.status == "completed" and again.stage == "scan_replayed"


def test_discovery_reads_only_the_active_market_wide_source_not_prior_tavily_evidence(tmp_path):
    path = tmp_path / "discovery-source-boundary.sqlite"
    initialize_schema(path); _watermark(path)
    _append_targeted_verification_document(path)

    class RecordingModel(_VerifiedModel):
        def __init__(self):
            super().__init__()
            self.seen = []

        def understand(self, *, document):
            self.seen.append(document.original_text)
            return super().understand(document=document)

    model = RecordingModel()
    result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                          adapter=_Adapter(), model=model, metadata=_Metadata(), created_at=CREATED,
                          verification_gateway=_FixtureVerificationGateway())
    assert result.status == "completed" and result.checkpoint["candidateCount"] == 1
    assert model.seen == ["公司的原始公告文本"]


def test_targeted_verification_without_market_wide_input_cannot_start_discovery(tmp_path):
    path = tmp_path / "targeted-only.sqlite"
    initialize_schema(path); _watermark(path)
    _append_targeted_verification_document(path)

    class EmptyAdapter(_Adapter):
        def fetch_incremental(self, request):
            self.request = request
            return SourceFetchResult(documents=(), next_cursor=None, success_watermark=request.window.cutoff_at,
                                     pages_fetched=1, pages_expected=1, exhausted=True)

    class RecordingModel(_VerifiedModel):
        def __init__(self):
            super().__init__()
            self.understand_calls = 0

        def understand(self, *, document):
            self.understand_calls += 1
            return super().understand(document=document)

    model = RecordingModel()
    result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                          adapter=EmptyAdapter(), model=model, metadata=_Metadata(), created_at=CREATED,
                          verification_gateway=_FixtureVerificationGateway())
    assert result.status == "completed" and result.checkpoint["candidateCount"] == 0
    assert model.understand_calls == 0


def test_legacy_frozen_targeted_input_is_refused_before_any_replay_or_publication(tmp_path):
    path = tmp_path / "legacy-frozen-targeted.sqlite"
    initialize_schema(path)
    document_id = _append_targeted_verification_document(path)
    identity = "legacy-frozen-input"
    import neckline.k10.pipeline as pipeline
    scan_id = pipeline._scan_id(kind="evening", cutoff_at=CUTOFF, identity=identity)
    original_coverage = {
        "inputSnapshotFrozen": True,
        "inputDocumentRefs": [{"documentId": document_id, "revision": 1}],
        # A completed scan without a batch normally enters thaw/publish recovery.  Its frozen
        # source boundary must be checked before that shortcut is allowed.
        "discoveryDraft": {},
    }
    store.create_scan(scan_id=scan_id, window_kind="evening", cutoff_at=CUTOFF.isoformat(), config_id=None,
                      config_revision=None, status="completed", coverage=original_coverage,
                      created_at=CREATED.isoformat(), completed_at=CREATED.isoformat(), db_path=path)

    result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                          adapter=_Adapter(), model=_VerifiedModel(), metadata=_Metadata(), created_at=CREATED,
                          scan_identity=identity, verification_gateway=_FixtureVerificationGateway())
    assert result.status == "failed" and result.stage == "source_boundary"
    assert "未授权来源" in result.error
    stored = store.get_scan(scan_id=scan_id, db_path=path)
    assert stored["status"] == "completed"
    assert stored["coverage"] == original_coverage


def test_running_frozen_targeted_input_finishes_failed_without_rewriting_evidence(tmp_path):
    path = tmp_path / "running-frozen-targeted.sqlite"
    initialize_schema(path)
    document_id = _append_targeted_verification_document(path)
    identity = "running-frozen-input"
    import neckline.k10.pipeline as pipeline
    scan_id = pipeline._scan_id(kind="evening", cutoff_at=CUTOFF, identity=identity)
    original_coverage = {
        "inputSnapshotFrozen": True,
        "inputDocumentRefs": [{"documentId": document_id, "revision": 1}],
        "state": "completed",
        "ingestionState": "completed",
        "sourceOutcomes": [{"sourceKey": "fixture-source", "state": "completed"}],
        "pipelineState": "discovery_persisted",
        "window": {"kind": "evening", "startAt": "2026-09-04T21:00:00+08:00",
                   "cutoffAt": CUTOFF.isoformat(), "startInclusive": False, "cutoffInclusive": True},
    }
    store.create_scan(scan_id=scan_id, window_kind="evening", cutoff_at=CUTOFF.isoformat(), config_id=None,
                      config_revision=None, status="running", coverage=original_coverage,
                      created_at=CREATED.isoformat(), completed_at=None, db_path=path)
    store.enqueue_task(task_id=identity, kind="evening_scan", idempotency_key="fixture-boundary",
                       input_version="fixture@1", input_cutoff_at=CUTOFF.isoformat(), payload={}, budget={"maxAttempts": 1},
                       created_at=CREATED.isoformat(), db_path=path)
    store.set_run_control(state="open", reason_code="offline_fixture", changed_at=CREATED.isoformat(),
                          changed_by="test", db_path=path)
    execution_id, execution_revision = append_approved_execution_profile(db_path=path, created_at=CREATED.isoformat())
    store.bind_task_execution(task_id=identity, execution_config_id=execution_id,
                              execution_config_revision=execution_revision, binding_kind="scheduled",
                              bound_at=CREATED.isoformat(), db_path=path)
    lease_calls = []

    def handler(_context):
        return execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                            adapter=_Adapter(), model=_VerifiedModel(), metadata=_Metadata(), created_at=CREATED,
                            scan_identity=identity, verification_gateway=_FixtureVerificationGateway(),
                            leaseguard=lambda: lease_calls.append(True))

    task = run_once(db_path=path, worker_id="boundary-worker", lease_for=timedelta(minutes=5),
                    handlers={"evening_scan": handler}, clock=lambda: CREATED)
    assert task is not None and task.status == "failed"
    assert lease_calls
    stored = store.get_scan(scan_id=scan_id, db_path=path)
    assert stored["status"] == "failed" and stored["completedAt"] is not None
    assert stored["coverage"]["inputDocumentRefs"] == original_coverage["inputDocumentRefs"]
    assert stored["coverage"]["sourceOutcomes"] == original_coverage["sourceOutcomes"]
    assert stored["coverage"]["pipelineState"] == "source_boundary"

    retry = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                         adapter=_Adapter(), model=_VerifiedModel(), metadata=_Metadata(), created_at=CREATED,
                         scan_identity=identity, verification_gateway=_FixtureVerificationGateway())
    assert retry.status == "failed" and retry.stage == "source_boundary"
    assert store.get_scan(scan_id=scan_id, db_path=path) == stored


def test_evening_scan_without_explicit_bootstrap_never_calls_source(tmp_path):
    path = tmp_path / "missing-watermark.sqlite"
    initialize_schema(path)
    adapter = _Adapter()
    result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                          adapter=adapter, model=_Model(), metadata=_Metadata(), created_at=CREATED)
    assert result.status == "not_configured"
    assert result.stage == "source_bootstrap"
    assert adapter.request is None


def test_evening_explicit_bootstrap_is_a_fetch_start_not_a_fake_success_watermark(tmp_path):
    path = tmp_path / "bootstrap.sqlite"
    initialize_schema(path)
    adapter = _Adapter()
    result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                          adapter=adapter, model=_Model(), metadata=_Metadata(), created_at=CREATED,
                          bootstrap_cutoff="2026-09-01T21:00:00+08:00")
    assert result.status == "completed"
    assert adapter.request.source_success_watermark == datetime(2026, 9, 1, 21, tzinfo=SHANGHAI)
    assert store.latest_source_watermark(source_key="fixture-source", db_path=path)["successCutoffAt"] != "2026-09-01T21:00:00+08:00"


def test_existing_success_watermark_takes_precedence_over_bootstrap(tmp_path):
    path = tmp_path / "watermark-wins.sqlite"
    initialize_schema(path)
    _watermark(path)
    adapter = _Adapter()

    result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                          adapter=adapter, model=_Model(), metadata=_Metadata(), created_at=CREATED,
                          bootstrap_cutoff="2026-09-01T21:00:00+08:00")

    assert result.status == "completed"
    assert adapter.request.source_success_watermark == datetime(2026, 9, 4, 21, tzinfo=SHANGHAI)
    assert adapter.request.window.start_at == datetime(2026, 9, 4, 21, tzinfo=SHANGHAI)


def test_failed_frozen_scan_reopens_without_changing_its_identity(tmp_path):
    path = tmp_path / "recover.sqlite"
    initialize_schema(path); _watermark(path)
    class FailsThenWorks(_Model):
        def __init__(self): super().__init__(); self.failed = False
        def understand(self, *, document):
            if not self.failed:
                self.failed = True
                raise RuntimeError("synthetic")
            return super().understand(document=document)
    model = FailsThenWorks()
    first = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path, adapter=_Adapter(),
                         model=model, metadata=_Metadata(), created_at=CREATED, scan_identity="recover-task")
    assert first.status == "completed" and first.stage == "partial_coverage"
    scan_id = next(item["scanId"] for item in store.list_scans(window_kind="evening", db_path=path) if item["scanId"].startswith("scan_"))
    assert store.get_scan(scan_id=scan_id, db_path=path)["status"] == "partial"
    recovered = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path, adapter=_Adapter(),
                             model=model, metadata=_Metadata(), created_at=CREATED + timedelta(minutes=1), scan_identity="recover-task")
    assert recovered.status == "completed" and recovered.stage == "scan_replayed" and recovered.checkpoint["scanId"] == scan_id


def test_recovery_reuses_frozen_window_and_snapshot_after_source_watermark_advances(tmp_path):
    path = tmp_path / "frozen-window.sqlite"
    initialize_schema(path); _watermark(path)

    class FailsOnce(_Model):
        def __init__(self): super().__init__(); self.calls = 0
        def understand(self, *, document):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("synthetic crash after ingestion")
            return super().understand(document=document)

    adapter, model = _Adapter(), FailsOnce()
    first_result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                                adapter=adapter, model=model, metadata=_Metadata(), created_at=CREATED, scan_identity="frozen")
    assert first_result.status == "completed" and first_result.stage == "partial_coverage"
    scan_id = _scan_id_for(path, "frozen")
    failed = store.get_scan(scan_id=scan_id, db_path=path)
    assert failed["coverage"]["inputSnapshotFrozen"] is True
    assert failed["coverage"]["inputDocumentRefs"]
    assert datetime.fromisoformat(store.latest_source_watermark(source_key="fixture-source", db_path=path)["successCutoffAt"]) == CUTOFF.astimezone(timezone.utc)

    recovered = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                             adapter=adapter, model=model, metadata=_Metadata(), created_at=CREATED + timedelta(minutes=1),
                             scan_identity="frozen")
    assert recovered.status == "completed" and recovered.stage == "scan_replayed"
    assert adapter.request.window.start_at == datetime(2026, 9, 4, 21, tzinfo=SHANGHAI)
    assert adapter.request.previous_cursor == "old-cursor"


def test_frozen_retry_keeps_only_the_original_market_wide_documents(tmp_path):
    path = tmp_path / "frozen-source-boundary.sqlite"
    initialize_schema(path); _watermark(path)
    _append_targeted_verification_document(path)

    class FailsOnce(_VerifiedModel):
        def __init__(self):
            super().__init__()
            self.failed = False
            self.seen = []

        def understand(self, *, document):
            self.seen.append(document.original_text)
            if not self.failed:
                self.failed = True
                raise RuntimeError("interrupt after frozen input")
            return super().understand(document=document)

    model = FailsOnce()
    first_result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                                adapter=_Adapter(), model=model, metadata=_Metadata(), created_at=CREATED,
                                scan_identity="frozen-source-boundary", verification_gateway=_FixtureVerificationGateway())
    assert first_result.status == "completed" and first_result.stage == "partial_coverage"
    scan_id = _scan_id_for(path, "frozen-source-boundary")
    frozen = store.get_scan(scan_id=scan_id, db_path=path)["coverage"]["inputDocumentRefs"]
    assert len(frozen) == 1
    assert store.load_document_versions(refs=frozen, db_path=path)[0]["sourceKey"] == "fixture-source"

    recovered = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                             adapter=_Adapter(), model=model, metadata=_Metadata(), created_at=CREATED + timedelta(minutes=1),
                             scan_identity="frozen-source-boundary", verification_gateway=_FixtureVerificationGateway())
    assert recovered.status == "completed" and recovered.stage == "scan_replayed"
    assert model.seen == ["公司的原始公告文本"]


def test_recovery_publishes_frozen_model_draft_without_second_model_run(tmp_path, monkeypatch):
    path = tmp_path / "frozen-draft.sqlite"
    initialize_schema(path); _watermark(path)
    model = _VerifiedModel()
    import neckline.k10.pipeline as pipeline
    original = pipeline.persist_discovery
    calls = {"persist": 0}
    def interrupted(*args, **kwargs):
        calls["persist"] += 1
        if calls["persist"] == 1:
            raise RuntimeError("synthetic process interruption")
        return original(*args, **kwargs)
    monkeypatch.setattr(pipeline, "persist_discovery", interrupted)
    import pytest
    with pytest.raises(RuntimeError):
        execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                     adapter=_Adapter(), model=model, metadata=_Metadata(), created_at=CREATED, scan_identity="draft",
                     verification_gateway=_FixtureVerificationGateway())
    scan_id = _scan_id_for(path, "draft")
    assert "discoveryDraft" in store.get_scan(scan_id=scan_id, db_path=path)["coverage"]
    calls_before = len(model.usage_records)
    completed = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                             adapter=_Adapter(), model=model, metadata=_Metadata(), created_at=CREATED + timedelta(minutes=1),
                             scan_identity="draft", verification_gateway=_FixtureVerificationGateway())
    assert completed.status == "completed"
    assert len(model.usage_records) == calls_before
    assert len(store.list_candidates(scan_id=scan_id, state="offered", db_path=path)) == 1


def test_source_failure_does_not_freeze_empty_input_before_successful_retry(tmp_path):
    path = tmp_path / "source-retry.sqlite"
    initialize_schema(path); _watermark(path)
    class FailsSourceOnce(_Adapter):
        def __init__(self): super().__init__(); self.failed = False
        def fetch_incremental(self, request):
            if not self.failed:
                self.failed = True
                raise RuntimeError("synthetic source outage")
            return super().fetch_incremental(request)
    adapter = FailsSourceOnce()
    first = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                         adapter=adapter, model=_Model(), metadata=_Metadata(), created_at=CREATED, scan_identity="source-retry")
    assert first.status == "failed"
    scan_id = _scan_id_for(path, "source-retry")
    assert store.get_scan(scan_id=scan_id, db_path=path)["coverage"]["inputSnapshotFrozen"] is False
    second = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                          adapter=adapter, model=_VerifiedModel(), metadata=_Metadata(), created_at=CREATED + timedelta(minutes=1),
                          scan_identity="source-retry", verification_gateway=_FixtureVerificationGateway())
    assert second.status == "completed"
    assert len(store.list_candidates(scan_id=scan_id, state="offered", db_path=path)) == 1


def test_crash_after_candidate_write_replays_same_event_revision_and_candidate(tmp_path, monkeypatch):
    path = tmp_path / "candidate-replay.sqlite"
    initialize_schema(path); _watermark(path)
    original = store.create_candidate
    calls = {"count": 0}
    def after_write(**kwargs):
        original(**kwargs)
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("synthetic crash after candidate write")
    monkeypatch.setattr(store, "create_candidate", after_write)
    import pytest
    with pytest.raises(RuntimeError):
        execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                     adapter=_Adapter(), model=_VerifiedModel(), metadata=_Metadata(), created_at=CREATED, scan_identity="candidate-replay",
                     verification_gateway=_FixtureVerificationGateway())
    scan_id = _scan_id_for(path, "candidate-replay")
    event_id = store.list_candidates(scan_id=scan_id, state="offered", db_path=path)[0]["eventId"]
    assert store.latest_event_revision(event_id=event_id, db_path=path).revision == 1
    completed = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                             adapter=_Adapter(), model=_VerifiedModel(), metadata=_Metadata(), created_at=CREATED + timedelta(minutes=1),
                             scan_identity="candidate-replay", verification_gateway=_FixtureVerificationGateway())
    assert completed.status == "completed"
    assert store.latest_event_revision(event_id=event_id, db_path=path).revision == 1
    assert len(store.list_candidates(scan_id=scan_id, state="offered", db_path=path)) == 1


def test_persisted_morning_match_requires_explicit_retry_for_terminal_child(tmp_path, monkeypatch):
    path = tmp_path / "morning-child-retry.sqlite"
    initialize_schema(path)
    _frozen_config(path)
    store.create_scan(scan_id="old", window_kind="evening", cutoff_at=CUTOFF.isoformat(), config_id="fixture", config_revision=1,
                      status="completed", coverage={}, created_at=CREATED.isoformat(), completed_at=CREATED.isoformat(), db_path=path)
    event = store.append_event_revision(event_id="event-child", stable_key="child", headline="公告", event_kind="fixture", facts={},
                                        source_refs=[], supersedes_revision=None, created_at=CREATED.isoformat(), db_path=path)
    from tests.test_k10_lifecycle import _input
    published = _input("candidate-child", "event-child", key="child", source_marker="evening")
    store.create_candidate(candidate_id="candidate-child", scan_id="old", event_id=event.event_id, event_revision=event.revision,
                           company_code="300001.SZ", comparison=published.comparison, evidence=[], created_at=CREATED.isoformat(), db_path=path)
    store.publish_opportunities(batch_id="old-batch", scan_id="old", publication_kind="evening", inputs=[published], clock=lambda: CREATED, db_path=path)
    parent = TaskContext(Task("parent", "morning_scan", "running", 1, "parent-worker", None, {}),
                         {"maxAttempts": 3}, {}, "fixture@1", CUTOFF.isoformat(), path, threading.Event())
    configuration = {"taskPolicies": {"morning": {"maxAttempts": 3, "costLimit": None}}}
    matches = [{"candidateId": "candidate-child", "eventId": "event-child", "morningEvidenceRefs": []}]
    import neckline.k10.pipeline as pipeline
    def finish(status):
        def runner(*, db_path, worker_id, lease_for, handlers, task_id, clock):
            now = CREATED.astimezone(timezone.utc)
            task = store.claim_task_by_id(task_id=task_id, worker_id=worker_id, now=now, lease_for=timedelta(minutes=10), db_path=db_path)
            if task is None:
                return None
            store.finish_task(task_id=task_id, worker_id=worker_id, status=status, stage="fixture", checkpoint={}, error_text=None,
                              finished_at=now, db_path=db_path)
            return store.get_task(task_id=task_id, db_path=db_path)
        return runner
    monkeypatch.setattr(pipeline, "run_once", finish("failed"))
    first, ids = _run_morning_reviews(parent=parent, matches=matches, configuration=configuration,
                                      config_id="fixture", config_revision=1, source_status="complete", now=CREATED)
    assert first == "partial" and store.get_task(task_id=ids[0], db_path=path).status == "failed"
    monkeypatch.setattr(pipeline, "run_once", finish("completed"))
    second, replay_ids = _run_morning_reviews(parent=parent, matches=matches, configuration=configuration,
                                               config_id="fixture", config_revision=1, source_status="complete", now=CREATED)
    terminal = store.get_task(task_id=ids[0], db_path=path)
    assert second == "partial" and replay_ids == ids
    assert terminal.status == "failed" and terminal.attempt_count == 1
    store.retry_task(task_id=ids[0], expected_attempt_count=terminal.attempt_count,
                     retried_at=CREATED.isoformat(), db_path=path)
    third, explicit_ids = _run_morning_reviews(parent=parent, matches=matches, configuration=configuration,
        config_id="fixture", config_revision=1, source_status="complete", now=CREATED)
    assert third == "completed" and explicit_ids == ids
    assert store.get_task(task_id=ids[0], db_path=path).status == "completed"


def test_morning_data_is_retained_without_auto_candidate_replacement(tmp_path):
    path = tmp_path / "morning.sqlite"
    initialize_schema(path)
    adapter = _Adapter()
    morning = datetime(2026, 9, 7, 9, tzinfo=SHANGHAI)
    result = execute_scan(kind="morning", cutoff_at=morning, configuration=_configuration(), db_path=path,
                          adapter=adapter, model=_VerifiedModel(), metadata=_Metadata(), created_at=CREATED,
                          verification_gateway=_FixtureVerificationGateway())
    assert result.status == "completed"
    assert result.stage == "discovery_completed"
    assert result.checkpoint["candidateCount"] == 1
    assert result.checkpoint["deferredCount"] == 0
    assert adapter.request.window.start_at == datetime(2026, 9, 4, 21, tzinfo=SHANGHAI)


class _Provider(LLMProvider):
    def __init__(self, *, wrap_compare: bool = False):
        self.calls = []
        outputs = (
            '{"events":[{"canonicalKey":"event","stageKey":"stage","eventState":"confirmed","headline":"公告","eventKind":"disclosure","facts":{},"sourceRefs":[{"documentId":"doc","revision":1}]}]}',
            '{"state":"verified","summary":"核验","sourceRefs":[{"documentId":"tavily-doc","revision":1}]}',
            '{"mappings":[{"companyCode":"300001.SZ","affectedStage":"supply","relationEvidence":[{"documentId":"tavily-doc","revision":1}],"inference":{},"uncertainty":"公开资料"}]}',
            '{"summary":"事件共同比较","sourceRefs":[{"documentId":"tavily-doc","revision":1}],"candidates":[{"companyCode":"300001.SZ","summary":"比较","role":"primary","rank":1,"priorityReason":"公司证据","gap":"比较差异","rankChangeConditions":"新披露","twoDayReason":"两日催化","sourceRefs":[{"documentId":"tavily-doc","revision":1}]}]}',
        )
        if wrap_compare:
            outputs = (*outputs[:3], json.dumps({"output": json.loads(outputs[3])}))
        self._outputs = iter(outputs)

    def chat(self, messages, *, enable_search=True, search_query=None, response_format=None, transport=None,
             model_options=None):
        self.calls.append((messages, enable_search, response_format, model_options))
        return LLMResult(ok=True, content=next(self._outputs), provider="fixture", model="deepseek-v4-pro",
                         prompt_tokens=3, completion_tokens=4, total_tokens=7, usage_unavailable=False)


def test_deepseek_discovery_model_uses_structured_json_and_preserves_usage():
    provider = _Provider()
    model = _deepseek(provider)
    model.set_scan_cutoff(CUTOFF)
    document = DiscoveryDocument("doc", 1, CUTOFF.isoformat(), CUTOFF.isoformat(), "不可信资料", None, {})
    event = model.understand(document=document)[0]
    independent = DiscoveryDocument("tavily-doc", 1, (CUTOFF - timedelta(hours=1)).isoformat(),
                                    CUTOFF.isoformat(), "独立核验的原文", None, {"provider": "tavily"})
    model.set_verification_documents(event=event, documents=(independent,))
    verification = model.verify(event)
    mapping = model.map_companies(event=event, verification=verification)[0]
    comparison = model.compare_event(event=event, verification=verification, mappings=(mapping,))

    assert comparison.summary == "事件共同比较"
    assert comparison.candidates["300001.SZ"].summary == "比较"
    assert verification.state == "verified"
    assert all(search is False and output == {"type": "json_object"} for _, search, output, _ in provider.calls)
    assert "不可信证据数据" in provider.calls[0][0][0].content
    def payload(call):
        content = call[0][1].content
        return json.loads(content.split("<untrusted-k10-evidence>\n", 1)[1].split("\n</untrusted-k10-evidence>", 1)[0])
    understand_input, mapping_input, comparison_input = payload(provider.calls[0]), payload(provider.calls[2]), payload(provider.calls[3])
    assert understand_input["publicationContext"] == {
        "publishedAt": CUTOFF.isoformat(), "fetchedAt": CUTOFF.isoformat(), "scanCutoffAt": CUTOFF.isoformat(),
    }
    assert understand_input["factsConvention"] == {"currentFacts": {}, "background": {}}
    assert "历史融资轮次" in provider.calls[0][0][1].content
    assert {item["documentId"] for item in mapping_input["evidence"]} == {"doc", "tavily-doc"}
    assert any(item["text"] == "独立核验的原文" for item in comparison_input["evidence"])
    assert mapping_input["verification"]["sourceRefs"] == [{"documentId": "tavily-doc", "revision": 1}]
    assert "document_id" not in provider.calls[2][0][1].content
    assert model.usage_records[-1]["totalTokens"] == 7


def test_deepseek_cannot_self_verify_or_cite_unfrozen_evidence():
    class Provider(LLMProvider):
        def __init__(self, evidence_ref):
            self.evidence_ref = evidence_ref

        def chat(self, messages, **kwargs):
            output = ('{"events":[{"canonicalKey":"event","stageKey":"stage","eventState":"confirmed",'
                      '"headline":"公告","eventKind":"disclosure","facts":{},'
                      '"sourceRefs":[{"documentId":"doc","revision":1}]}]}')
            if "重点核验" in messages[1].content:
                output = json.dumps({"state": "verified", "summary": "模型结论", "sourceRefs": [self.evidence_ref]})
            return LLMResult(ok=True, content=output, provider="fixture", model="deepseek-v4-pro",
                             prompt_tokens=1, completion_tokens=1, total_tokens=2, usage_unavailable=False)

    source_only = _deepseek(Provider({"documentId": "doc", "revision": 1}))
    event = source_only.understand(document=DiscoveryDocument("doc", 1, CUTOFF.isoformat(), CUTOFF.isoformat(), "原文", None, {}))[0]
    assert source_only.verify(event).state == "needs_review"

    unfrozen = _deepseek(Provider({"documentId": "invented", "revision": 9}))
    event = unfrozen.understand(document=DiscoveryDocument("doc", 1, CUTOFF.isoformat(), CUTOFF.isoformat(), "原文", None, {}))[0]
    with pytest.raises(PipelineError, match="未输入的冻结资料"):
        unfrozen.verify(event)


@pytest.mark.parametrize("state,refs", [("verified", None), ("contradicted", [])])
def test_deepseek_missing_verification_refs_is_a_reviewable_gap(state, refs):
    class Provider(LLMProvider):
        def chat(self, messages, **kwargs):
            if "重点核验" in messages[1].content:
                body = {"state": state, "summary": "模型声称已核验"}
                if refs is not None:
                    body["sourceRefs"] = refs
                content = json.dumps(body)
            else:
                content = ('{"events":[{"canonicalKey":"event","stageKey":"stage","eventState":"confirmed",'
                           '"headline":"公告","eventKind":"disclosure","facts":{},'
                           '"sourceRefs":[{"documentId":"doc","revision":1}]}]}')
            return LLMResult(ok=True, content=content, provider="fixture", model="deepseek-v4-pro",
                             prompt_tokens=1, completion_tokens=1, total_tokens=2, usage_unavailable=False)

    model = _deepseek(Provider())
    event = model.understand(document=DiscoveryDocument("doc", 1, CUTOFF.isoformat(), CUTOFF.isoformat(), "原文", None, {}))[0]
    verification = model.verify(event)
    assert verification.state == "needs_review"
    assert verification.evidence_refs == ()
    assert "缺少可追溯核验依据" in verification.summary


def test_deepseek_compare_accepts_only_the_actual_sole_output_wrapper():
    provider = _Provider(wrap_compare=True)
    model = _deepseek(provider)
    document = DiscoveryDocument("doc", 1, CUTOFF.isoformat(), CUTOFF.isoformat(), "原文", None, {})
    event = model.understand(document=document)[0]
    evidence = DiscoveryDocument("tavily-doc", 1, (CUTOFF - timedelta(hours=1)).isoformat(),
                                 CUTOFF.isoformat(), "独立原文", None, {})
    model.set_verification_documents(event=event, documents=(evidence,))
    verification = model.verify(event)
    mapping = model.map_companies(event=event, verification=verification)[0]
    assert model.compare_event(event=event, verification=verification, mappings=(mapping,)).summary == "事件共同比较"


def test_deepseek_mapping_discards_hk_counterparty_but_rejects_unknown_code_formats():
    class Provider(LLMProvider):
        def __init__(self, code):
            self.code = code
            self.calls = []

        def chat(self, messages, **kwargs):
            self.calls.append(messages)
            if "只提取本篇" in messages[1].content:
                content = ('{"events":[{"canonicalKey":"event","stageKey":"stage","eventState":"confirmed",'
                           '"headline":"公告","eventKind":"disclosure","facts":{},'
                           '"sourceRefs":[{"documentId":"doc","revision":1}]}]}')
            else:
                rows = [
                    {"companyCode": "300207.SZ", "affectedStage": "supply", "relationEvidence": [{"documentId": "doc", "revision": 1}], "inference": {}, "uncertainty": "公告"},
                    {"companyCode": self.code, "affectedStage": "counterparty", "relationEvidence": [{"documentId": "doc", "revision": 1}], "inference": {}, "uncertainty": "公告"},
                ]
                content = json.dumps({"mappings": rows})
            return LLMResult(ok=True, content=content, provider="fixture", model="deepseek-v4-pro",
                             prompt_tokens=1, completion_tokens=1, total_tokens=2, usage_unavailable=False)

    document = DiscoveryDocument("doc", 1, CUTOFF.isoformat(), CUTOFF.isoformat(), "原文", None, {})
    model = _deepseek(Provider("02015.HK"))
    event = model.understand(document=document)[0]
    mappings = model.map_companies(event=event, verification=Verification("needs_review", "待核", event.source_refs))
    assert [mapping.company_code for mapping in mappings] == ["300207.SZ"]
    assert "港/美股" in model.provider.calls[1][1].content

    malformed = _deepseek(Provider("L2015.HK"))
    event = malformed.understand(document=document)[0]
    with pytest.raises(PipelineError, match="TuShare ts_code"):
        malformed.map_companies(event=event, verification=Verification("needs_review", "待核", event.source_refs))


def test_execute_scan_uses_injected_verification_gateway(tmp_path):
    path = tmp_path / "injected-verification.sqlite"
    initialize_schema(path)
    _watermark(path)

    class Gateway:
        def __init__(self):
            self.calls = []

        def fetch(self, *, event, retrieved_at, cutoff_at, cutoff_inclusive=False):
            self.calls.append((event, retrieved_at, cutoff_at, cutoff_inclusive))
            evidence = DiscoveryDocument("tavily-doc", 1, (cutoff_at - timedelta(minutes=1)).isoformat(),
                                         retrieved_at.isoformat(), "独立资料", None, {"provider": "fixture"})
            return VerificationEvidenceBundle("available", (evidence,), (evidence,), {"provider": "fixture", "state": "available"})

    gateway = Gateway()
    result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                          adapter=_Adapter(), model=_Model(), metadata=_Metadata(), created_at=CREATED,
                          verification_gateway=gateway)
    assert result.status == "completed"
    assert len(gateway.calls) == 1
    assert gateway.calls[0][2] == CUTOFF
    assert gateway.calls[0][3] is False


def test_scan_continues_from_missing_verification_refs_to_other_verified_comparisons(tmp_path):
    path = tmp_path / "verification-gap-does-not-stop-scan.sqlite"
    initialize_schema(path)
    _watermark(path)

    class Adapter(_Adapter):
        def fetch_incremental(self, request):
            self.request = request
            published = request.window.start_at + timedelta(minutes=1)
            return SourceFetchResult(
                documents=(
                    SourceDocumentInput("fixture-1", None, "第一条公告", None, published, "exact", published, "fixture", {}),
                    SourceDocumentInput("fixture-2", None, "第二条公告", None, published, "exact", published, "fixture", {}),
                ), next_cursor=None, success_watermark=request.window.cutoff_at,
                pages_fetched=1, pages_expected=1, exhausted=True,
            )

    class Model(_Model):
        def __init__(self):
            super().__init__()
            self.verification_states = []

        def understand(self, *, document):
            key = "first" if document.original_text == "第一条公告" else "second"
            return (EventDraft(key, "stage", "confirmed", key, "disclosure",
                               {"document": document.document_id}, (document.evidence_ref,)),)

        def verify(self, event):
            if event.canonical_key == "first":
                return Verification("needs_review", "缺引用", ())
            return Verification("verified", "独立资料", (EvidenceRef("tavily-second", 1),))

        def map_companies(self, *, event, verification):
            self.verification_states.append(verification.state)
            code = "300001.SZ" if event.canonical_key == "first" else "300002.SZ"
            refs = event.source_refs if event.canonical_key == "first" else verification.evidence_refs
            return (CompanyMappingDraft(code, "supply", refs, {"basis": "公告"}, "fixture"),)

    class Gateway:
        def fetch(self, *, event, retrieved_at, cutoff_at, cutoff_inclusive=False):
            evidence = DiscoveryDocument(f"tavily-{event.canonical_key}", 1, (cutoff_at - timedelta(minutes=1)).isoformat(),
                                         retrieved_at.isoformat(), "独立核验原文", None, {})
            return VerificationEvidenceBundle("available", (evidence,), (evidence,), {"state": "available"})

    model = Model()
    result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                          adapter=Adapter(), model=model, metadata=_Metadata(), created_at=CREATED,
                          verification_gateway=Gateway())
    assert result.status == "completed"
    assert result.checkpoint["candidateCount"] == 1
    assert sorted(model.verification_states) == ["needs_review", "verified"]


def test_default_gateway_receives_only_explicit_metadata_resolver(tmp_path, monkeypatch):
    import neckline.k10.pipeline as pipeline
    path = tmp_path / "default-resolver.sqlite"
    initialize_schema(path)
    _watermark(path)
    built = []

    class Gateway:
        def __init__(self, *, db_path, metadata_resolver):
            built.append((db_path, metadata_resolver))

        def fetch(self, *, event, retrieved_at, cutoff_at, cutoff_inclusive=False):
            return VerificationEvidenceBundle("pending", (), (), {"state": "pending", "reason": "fixture"})

    monkeypatch.setattr(pipeline, "TavilyEvidenceGateway", Gateway)
    result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                          adapter=_Adapter(), model=_Model(), metadata=_Metadata(), created_at=CREATED)
    assert result.status == "completed"
    assert len(built) == 1
    resolver = built[0][1]
    assert resolver is not None
    assert resolver._max_requests == 16
    assert resolver._timeout == 12.0
    assert resolver._max_bytes == 262144
    assert "www.cninfo.com.cn" in resolver._hosts


def test_invalid_explicit_metadata_configuration_stops_before_source_fetch(tmp_path):
    path = tmp_path / "bad-metadata-config.sqlite"
    initialize_schema(path)
    _watermark(path)
    configuration = _configuration()
    configuration["evidenceMetadata"] = {"allowedHttpsHosts": [], "maxRequests": 0, "timeoutSeconds": 0, "maxBytes": 0}
    adapter = _Adapter()
    result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=configuration, db_path=path,
                          adapter=adapter, model=_Model(), metadata=_Metadata(), created_at=CREATED)
    assert result.status == "not_configured"
    assert result.stage == "configuration"
    assert "evidenceMetadata" in result.error
    assert adapter.request is None


def test_nonfinite_metadata_timeout_is_not_a_valid_engineering_limit(tmp_path):
    path = tmp_path / "nonfinite-metadata-timeout.sqlite"
    initialize_schema(path)
    _watermark(path)
    configuration = _configuration()
    configuration["evidenceMetadata"] = {**configuration["evidenceMetadata"], "timeoutSeconds": float("nan")}
    adapter = _Adapter()
    result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=configuration, db_path=path,
                          adapter=adapter, model=_Model(), metadata=_Metadata(), created_at=CREATED)
    assert result.status == "not_configured"
    assert "evidenceMetadata" in result.error
    assert adapter.request is None


def test_retry_keeps_the_original_opportunity_history_used_for_classification(tmp_path, monkeypatch):
    import neckline.k10.pipeline as pipeline
    path = tmp_path / "history-snapshot.sqlite"
    initialize_schema(path)
    _watermark(path)

    class Interrupted(_Model):
        def __init__(self):
            super().__init__()
            self.histories = []
            self.calls = 0

        def set_previous_opportunities(self, previous):
            self.histories.append(list(previous))

        def understand(self, *, document):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("interrupted before complete model draft")
            return super().understand(document=document)

    model = Interrupted()
    first_result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                                adapter=_Adapter(), model=model, metadata=_Metadata(), created_at=CREATED,
                                scan_identity="history-frozen")
    assert first_result.status == "completed" and first_result.stage == "partial_coverage"
    first = next(row for row in store.list_scans(window_kind="evening", db_path=path) if row["scanId"] != "previous")
    assert first["coverage"]["inputOpportunitySnapshot"] == []
    monkeypatch.setattr(pipeline, "_existing_opportunity_context", lambda **_: pytest.fail("retry must not read later history"))
    result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                          adapter=_Adapter(), model=model, metadata=_Metadata(), created_at=CREATED + timedelta(minutes=1),
                          scan_identity="history-frozen")
    assert result.status == "completed"
    assert model.histories == [[]]
    recovered = store.get_scan(scan_id=first["scanId"], db_path=path)
    assert recovered["coverage"]["inputVisibleAt"] == first["coverage"]["inputVisibleAt"]


def test_morning_review_universe_excludes_unpublished_and_expired_candidates(tmp_path):
    from neckline.k10.pipeline import _active_published_candidates, _existing_opportunity_context
    path = tmp_path / "active-formal.sqlite"
    initialize_schema(path)
    _watermark(path)
    execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                 adapter=_Adapter(), model=_VerifiedModel(), metadata=_Metadata(), created_at=CREATED,
                 verification_gateway=_FixtureVerificationGateway())
    published = store.list_candidates(scan_id=None, state=None, db_path=path)[0]
    store.create_candidate(candidate_id="draft-never-published", scan_id=published["scanId"],
                           event_id=published["eventId"], event_revision=published["eventRevision"],
                           company_code="300002.SZ", comparison={}, evidence=[],
                           created_at=CREATED.isoformat(), db_path=path)
    history = _existing_opportunity_context(db_path=path)
    active = _active_published_candidates(opportunities=history, as_of=CREATED, db_path=path)
    assert [row["candidateId"] for row in active] == [published["candidateId"]]
    expired = _active_published_candidates(opportunities=history,
        as_of=datetime(2026, 9, 9, 15, tzinfo=SHANGHAI), db_path=path)
    assert expired == []


def test_event_comparison_classifies_historical_case_only_from_frozen_source_quote():
    class Provider:
        def chat(self, *_args, **_kwargs):
            return LLMResult(ok=True, content=json.dumps({
                "summary": "整体比较", "sourceRefs": [{"documentId": "doc-current", "revision": 1}],
                "historicalAssessments": [{"caseId": "external:doc-history@1", "outcome": "success",
                    "summary": "来源明示成功", "sourceQuote": "该案例最终成功", "sourceRefs": [{"documentId": "doc-history", "revision": 1}]}],
                "candidates": [{"companyCode": "300001.SZ", "summary": "公司比较", "role": "primary", "rank": 1,
                    "priorityReason": "证据", "gap": "差异", "rankChangeConditions": "反证", "twoDayReason": "催化",
                    "sourceRefs": [{"documentId": "doc-current", "revision": 1}]}],
            }, ensure_ascii=False), provider="fixture", model="fixture")

    event = EventDraft("current", "order", "confirmed", "当前事件", "disclosure", {"mechanism": "材料"}, (EvidenceRef("doc-current", 1),))
    model = _deepseek(Provider(), historical_context_loader=lambda **_: {
        "historicalCases": [{"caseId": "external:doc-history@1", "outcome": "unclassified", "summary": "历史资料",
            "observedAt": "2026-08-01T09:00:00+08:00", "sourceRefs": [{"documentId": "doc-history", "revision": 1}],
            "marketFacts": [], "sourceBasedDescription": "该案例最终成功，且有公开复盘。"}],
        "historicalCoverage": {"state": "partial", "requestedOutcomes": ["success", "flat", "failure"],
            "presentOutcomes": [], "missingOutcomes": ["success", "flat", "failure"], "reason": "gap",
            "sourceRefs": [{"documentId": "doc-history", "revision": 1}]},
    })
    model.set_scan_cutoff(CUTOFF)
    model._documents[event.source_refs[0]] = DiscoveryDocument("doc-current", 1, CUTOFF.isoformat(), CUTOFF.isoformat(), "当前资料", None, {})
    comparison = model.compare_event(event=event, verification=Verification("needs_review", "待核", ()),
        mappings=(CompanyMappingDraft("300001.SZ", "order", event.source_refs, {}, "fixture"),))
    history = comparison.candidates["300001.SZ"].historical_cases
    assert history[0]["outcome"] == "success"
    assert history[0]["categoryEvidence"] == [{"documentId": "doc-history", "revision": 1, "quote": "该案例最终成功"}]


def test_task_bound_model_stage_retries_provider_failure_once_and_caches_the_validated_result(tmp_path):
    """The V3 retry allowance is consumed in the same run, not a later slice."""
    path = tmp_path / "stage-retry.sqlite"
    initialize_schema(path)
    binding = _bound_v306_execution(path, task_id="task-stage")

    class Base:
        def __init__(self): self.calls, self.usage_records = 0, []
        def verify(self, _event):
            self.calls += 1
            self.usage_records.append({"inputTokens": 3, "outputTokens": 2, "totalTokens": 5})
            if self.calls == 1:
                raise PipelineError("temporary provider outage", code="provider_transport")
            return Verification("verified", "独立资料确认", (EvidenceRef("doc-1", 1),))

    base = Base()
    model = _CheckpointedDiscoveryModel(base=base, task_id="task-stage", execution_profile=binding,
                                        cutoff_at=CUTOFF, db_path=path, leaseguard=None)
    event = EventDraft("event-1", "initial", "confirmed", "事件", "disclosure", {}, (EvidenceRef("doc-1", 1),))
    assert model.verify(event).state == "verified"
    assert base.calls == 2
    rows = store.completed_execution_items(task_id="task-stage", item_kind="event", stage="model:verify", db_path=path)
    assert len(rows) == 1 and rows[0]["networkAttemptCount"] == 2
    assert rows[0]["inputTokens"] == 6 and rows[0]["outputTokens"] == 4
    assert model.verify(event).state == "verified"
    assert base.calls == 2  # exact same frozen input hydrates cache, never calls provider again


def test_compare_role_failure_keeps_external_usage_and_does_not_repeat_same_input(tmp_path, monkeypatch):
    """A semantic compare failure remains a one-call, auditable terminal result."""
    path = tmp_path / "compare-role-invalid.sqlite"
    initialize_schema(path)
    binding = _bound_v306_execution(path, task_id="task-compare-invalid")
    store.set_run_control(state="open", reason_code="fixture", changed_at=CREATED.isoformat(),
                          changed_by="test", db_path=path)
    provider = MeteredProvider(ledger_db=path, ledger_task="discovery", api_key="fixture", model="deepseek-v4-pro",
                               name="fixture", api_url="https://api.deepseek.com/chat/completions")
    bind_provider_execution_spending(provider=provider, task_id="task-compare-invalid", execution_profile=binding)
    upstream_calls = 0

    def upstream(*_args, **_kwargs):
        nonlocal upstream_calls
        upstream_calls += 1
        return LLMResult(ok=True, content="{}", provider="fixture", model="deepseek-v4-pro",
                         prompt_tokens=7, completion_tokens=3, total_tokens=10, usage_unavailable=False)

    monkeypatch.setattr(OpenAICompatProvider, "chat", upstream)

    class Base:
        def __init__(self):
            self.provider, self.usage_records = provider, []

        def map_companies(self, *, event, verification):
            return (CompanyMappingDraft("300133.SZ", "supply", event.source_refs, {"basis": "fixture"}, "fixture"),)

        def compare_event(self, *, event, verification, mappings):
            result = self.provider.chat([ChatMessage(role="user", content="比较")], enable_search=False,
                                        model_options={"maxTokens": 128, "thinking": {"type": "disabled"}})
            assert result.ok
            self.usage_records.append({"inputTokens": 7, "outputTokens": 3, "totalTokens": 10})
            company = mappings[0]
            return EventComparison("共同事实", {
                company.company_code: CandidateComparison("比较", {
                    "role": "unsupported-role", "priorityReason": "资料", "gap": "差异",
                    "rankChangeConditions": "新资料", "twoDayReason": "两日催化",
                }, company.relation_evidence, 1),
            }, event.source_refs)

    document = DiscoveryDocument("compare-doc", 1, CUTOFF.isoformat(), CUTOFF.isoformat(), "冻结资料", None, {})
    event = EventDraft("compare-event", "initial", "confirmed", "比较事件", "disclosure", {}, (document.evidence_ref,))
    model = _CheckpointedDiscoveryModel(base=Base(), task_id="task-compare-invalid", execution_profile=binding,
                                        cutoff_at=CUTOFF, db_path=path, leaseguard=None)
    kwargs = {"documents": (document,), "configuration": _configuration(), "model": model,
              "verify": lambda item: Verification("verified", "核验", item.source_refs), "metadata": _Metadata(),
              "cutoff_at": CUTOFF, "understood_by_document": {document.evidence_ref: (event,)}}
    first = run_discovery(**kwargs)
    second = run_discovery(**kwargs)
    assert first.state == second.state == "partial"
    assert not first.candidates and not second.candidates
    assert [issue.code for issue in first.issues] == ["compare_company_role_invalid"]
    assert upstream_calls == 1
    assert store.external_attempt_summary(task_id="task-compare-invalid", db_path=path)["actualUsage"] == {
        "promptTokens": 7, "completionTokens": 3, "totalTokens": 10, "searchRequests": 0, "searchCredits": 0,
    }
    with store.read_connection(path) as conn:
        row = conn.execute(
            "SELECT status,network_attempt_count,safe_error_code,result_json FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND stage='model:compare'", ("task-compare-invalid",),
        ).fetchone()
    assert row == ("failed", 1, "compare_company_role_invalid", None)


@pytest.mark.parametrize("kind, expected", [
    ("root", "compare_output_root_invalid"),
    ("coverage", "compare_company_coverage_invalid"),
    ("ranking", "compare_company_ranking_invalid"),
    ("role", "compare_company_role_invalid"),
    ("prediction", "compare_uncalibrated_prediction"),
])
def test_compare_checkpoint_uses_stable_semantic_error_codes(tmp_path, kind, expected):
    path = tmp_path / f"compare-{kind}.sqlite"
    initialize_schema(path)
    binding = _bound_v306_execution(path, task_id=f"task-compare-{kind}")
    event = EventDraft("compare-event", "initial", "confirmed", "比较事件", "disclosure", {}, (EvidenceRef("doc-1", 1),))
    mapping = CompanyMappingDraft("300133.SZ", "supply", event.source_refs, {"basis": "fixture"}, "fixture")

    class Base:
        provider = None
        usage_records: list[dict] = []

        def compare_event(self, **_kwargs):
            if kind == "root":
                return object()
            candidates = {} if kind == "coverage" else {
                mapping.company_code: CandidateComparison(
                    "比较",
                    {"role": "unsupported" if kind == "role" else "primary", "priorityReason": "资料", "gap": "差异",
                     "rankChangeConditions": "新资料", "twoDayReason": "两日催化"},
                    event.source_refs, 2 if kind == "ranking" else 1,
                ),
            }
            return EventComparison("涨停概率70%" if kind == "prediction" else "共同事实", candidates, event.source_refs)

    model = _CheckpointedDiscoveryModel(base=Base(), task_id=f"task-compare-{kind}", execution_profile=binding,
                                        cutoff_at=CUTOFF, db_path=path, leaseguard=None)
    with pytest.raises(PipelineError) as raised:
        model.compare_event(event=event, verification=Verification("verified", "核验", event.source_refs), mappings=(mapping,))
    assert raised.value.code == expected


def test_deepseek_compare_labels_invalid_historical_evidence_without_persisting_response():
    class Provider(LLMProvider):
        def chat(self, *_args, **_kwargs):
            return LLMResult(ok=True, content=json.dumps({
                "summary": "共同事实", "sourceRefs": [{"documentId": "doc-history-check", "revision": 1}],
                "historicalAssessments": {"invalid": True},
                "candidates": [{"companyCode": "300133.SZ", "summary": "比较", "role": "primary", "rank": 1,
                                "priorityReason": "资料", "gap": "差异", "rankChangeConditions": "新资料",
                                "twoDayReason": "两日催化", "sourceRefs": [{"documentId": "doc-history-check", "revision": 1}]}],
            }), provider="fixture", model="deepseek-v4-pro")

    model = _deepseek(Provider())
    model.set_scan_cutoff(CUTOFF)
    document = DiscoveryDocument("doc-history-check", 1, CUTOFF.isoformat(), CUTOFF.isoformat(), "冻结资料", None, {})
    event = EventDraft("history-check", "initial", "confirmed", "历史校验", "disclosure", {}, (document.evidence_ref,))
    model._documents[document.evidence_ref] = document
    mapping = CompanyMappingDraft("300133.SZ", "supply", event.source_refs, {}, "fixture")
    with pytest.raises(PipelineError) as raised:
        model.compare_event(event=event, verification=Verification("needs_review", "待核", ()), mappings=(mapping,))
    assert raised.value.code == "compare_historical_evidence_invalid"


def test_task_bound_truncation_is_pending_without_repeating_identical_request(tmp_path):
    """More output capacity must be an explicit new profile/input, never a blind retry."""
    path = tmp_path / "stage-truncated.sqlite"
    initialize_schema(path)
    binding = _bound_v306_execution(path, task_id="task-truncated")

    class Base:
        def __init__(self): self.calls, self.usage_records = 0, []
        def verify(self, _event):
            self.calls += 1
            self.usage_records.append({"inputTokens": 9, "outputTokens": 8192, "totalTokens": 8201})
            raise PipelineError("output limit", code="response_truncated")

    event = EventDraft("event-1", "initial", "confirmed", "事件", "disclosure", {}, (EvidenceRef("doc-1", 1),))
    model = _CheckpointedDiscoveryModel(base=Base(), task_id="task-truncated", execution_profile=binding,
                                        cutoff_at=CUTOFF, db_path=path, leaseguard=None)
    with pytest.raises(PipelineError, match="模型阶段未完成") as raised:
        model.verify(event)
    assert raised.value.code == "response_truncated"
    # An unchanged item/input is a terminal pending error: a later slice reads
    # the safe ledger state and does not spend another identical 8192-token call.
    with pytest.raises(PipelineError) as retried:
        model.verify(event)
    assert retried.value.code == "response_truncated"
    assert model._base.calls == 1
    with store.read_connection(path) as conn:
        row = conn.execute("SELECT status,attempt_count,network_attempt_count,safe_error_code,input_tokens,output_tokens "
                           "FROM k10_execution_item_checkpoints WHERE task_id='task-truncated' AND stage='model:verify'").fetchone()
    assert row == ("failed", 1, 1, "response_truncated", 9, 8192)


def test_full_text_route_marks_the_final_document_coarse_checkpoint(tmp_path):
    """A selected V3 article is read in full once and resumed from one checkpoint."""
    path = tmp_path / "full-text-coarse.sqlite"
    initialize_schema(path)
    binding = _bound_v306_execution(path, task_id="task-full-text")

    class Provider:
        def __init__(self): self.calls, self.prompts = 0, []
        def chat(self, messages, **_kwargs):
            self.calls += 1
            self.prompts.append(messages[-1].content)
            payload = {"events": [{"canonicalKey": "full-event", "stageKey": "initial", "eventState": "confirmed",
                                    "headline": "全文事件", "eventKind": "disclosure", "facts": {},
                                    "sourceRefs": [{"documentId": "full-doc", "revision": 1}],
                                    "claims": [{"claimId": "full-claim", "text": "正文披露", "kind": "factual_assertion",
                                                "novelty": "new_fact", "speaker": None, "subject": "项目", "object": None,
                                                "action": "披露", "stageOrCondition": None, "timeText": None,
                                                "verificationStatus": "unverified", "decisionImpact": "影响阶段判断",
                                                "sourceRef": {"documentId": "full-doc", "revision": 1}, "location": "paragraph:1"}]}], "needsFullText": False}
            return LLMResult(ok=True, content=json.dumps(payload), provider="fixture", model="deepseek-v4-pro",
                             prompt_tokens=5, completion_tokens=4, total_tokens=9, usage_unavailable=False)

    provider = Provider()
    base = DeepSeekDiscoveryModel(provider)
    base.set_execution_policy(binding["payload"]["discovery"])
    base.set_scan_cutoff(CUTOFF)
    model = _CheckpointedDiscoveryModel(base=base, task_id="task-full-text", execution_profile=binding,
                                        cutoff_at=CUTOFF, db_path=path, leaseguard=None)
    document = DiscoveryDocument("full-doc", 1, CUTOFF.isoformat(), CUTOFF.isoformat(), "第一段足够长\n第二段仍是冻结全文", None, {})
    _freeze_single_selected_article(path, task_id="task-full-text", binding=binding, document=document)
    checkpoints: list[dict] = []
    run = run_discovery(documents=(document,), configuration=_configuration(), model=model,
                        verify=lambda event: Verification("needs_review", "独立资料待核", (), {"state": "pending"}),
                        metadata=_Metadata(), cutoff_at=CUTOFF, checkpoint=checkpoints.append)
    assert run.state == "partial" and provider.calls == 1
    assert "第一段足够长" in provider.prompts[0] and "第二段仍是冻结全文" in provider.prompts[0]
    final = next(item for item in checkpoints if item["state"] == "completed")
    assert final["fullTextUsed"] is True
    # A later slice reuses the one completed full-body understanding checkpoint.
    cached_base = DeepSeekDiscoveryModel(provider)
    cached_base.set_execution_policy(binding["payload"]["discovery"])
    cached_base.set_scan_cutoff(CUTOFF)
    cached = _CheckpointedDiscoveryModel(base=cached_base, task_id="task-full-text", execution_profile=binding,
                                         cutoff_at=CUTOFF, db_path=path, leaseguard=None)
    prepared = prepare_document_for_analysis(document)
    assert cached.understand(document=prepared)[0].canonical_key == "full-event"
    assert cached.full_text_used(document=prepared) is True and provider.calls == 1
    rows = store.completed_execution_items(task_id="task-full-text", item_kind="document", stage="model:understand", db_path=path)
    assert len(rows) == 1


def test_recovered_understanding_registers_prepared_source_before_event_verify(tmp_path):
    """A new slice must verify recovered drafts without rerunning understanding."""
    path = tmp_path / "recovered-event-context.sqlite"
    initialize_schema(path)
    binding = _bound_v306_execution(path, task_id="task-recovered-event", binding_kind="recovery")

    class Provider:
        def __init__(self): self.calls, self.prompts = 0, []
        def chat(self, messages, **_kwargs):
            self.calls += 1
            self.prompts.append(messages[-1].content)
            payload = ({"state": "verified", "summary": "独立资料已核", "sourceRefs": [{"documentId": "verify-doc", "revision": 1}]}
                       if self.calls == 1 else {"mappings": []})
            return LLMResult(ok=True, content=json.dumps(payload), provider="fixture", model="deepseek-v4-pro",
                             prompt_tokens=4, completion_tokens=3, total_tokens=7, usage_unavailable=False)

    provider = Provider()
    base = DeepSeekDiscoveryModel(provider)
    base.set_execution_policy(binding["payload"]["discovery"])
    base.set_scan_cutoff(CUTOFF)
    model = _CheckpointedDiscoveryModel(base=base, task_id="task-recovered-event", execution_profile=binding,
                                        cutoff_at=CUTOFF, db_path=path, leaseguard=None)
    document = DiscoveryDocument("recovered-doc", 1, CUTOFF.isoformat(), CUTOFF.isoformat(),
                                 "<article>已提纯事实</article>", None, {"sourceKey": "fixture-source", "title": "恢复资料"})
    event = EventDraft("recovered-event", "initial", "confirmed", "恢复事件", "disclosure", {"researchClaims": []},
                       (EvidenceRef("recovered-doc", 1),))
    verification_document = DiscoveryDocument("verify-doc", 1, CUTOFF.isoformat(), CUTOFF.isoformat(), "独立核验事实", None, {})
    def verify(recovered):
        model.set_verification_documents(event=recovered, documents=(verification_document,))
        reviewed = model.verify(recovered)
        return Verification(reviewed.state, reviewed.summary, reviewed.evidence_refs, {}, (verification_document,))
    run = run_discovery(documents=(document,), configuration=_configuration(), model=model,
                        verify=verify, metadata=_Metadata(), cutoff_at=CUTOFF,
                        understood_by_document={document.evidence_ref: (event,)})
    with store.read_connection(path) as conn:
        failures = conn.execute("SELECT safe_error_code FROM k10_execution_item_checkpoints WHERE task_id='task-recovered-event'").fetchall()
    assert run.state == "completed" and provider.calls == 2, (run.state, run.issues, provider.calls, failures)
    # B39 reuses the typed fact derivative and locator excerpt only. A resumed
    # verify request must not resend the original article body.
    assert "已提纯事实" not in provider.prompts[0]
    verify_rows = store.completed_execution_items(task_id="task-recovered-event", item_kind="event", stage="model:verify", db_path=path)
    assert len(verify_rows) == 1 and verify_rows[0]["networkAttemptCount"] == 1


def test_task_deadline_finalizes_running_scan_without_publication(tmp_path, monkeypatch):
    path = tmp_path / "deadline-terminal.sqlite"
    initialize_schema(path)
    _watermark(path)
    binding = _bound_v306_execution(path, task_id="task-deadline")

    class Model(_Model):
        def set_execution_policy(self, _policy): pass
        def set_scan_cutoff(self, _cutoff): pass
        def set_previous_opportunities(self, _previous): pass
        def titleBatch(self, *, payload):
            return {"items": [{"documentId": row["documentId"], "revision": row["revision"],
                               "status": "candidate", "matterKey": "deadline", "stageKey": "initial",
                               "reason": "离线截止时间回归"} for row in payload["items"]]}
        def titleReconcile(self, *, payload):
            if payload.get("operation") == "titleSelectionReview":
                return {"complete": True, "kept": [{"i": row["i"], "reason": "保留独立事实"}
                                                  for row in payload["items"]], "removed": []}
            return {"selected": [{"i": row["i"], "selectedRank": index + 1, "reason": "离线截止时间回归"}
                                 for index, row in enumerate(payload["items"])],
                    "merged": [], "notSelected": []}

    from neckline.k10.discovery import DiscoveryDeadlineExceeded
    monkeypatch.setattr("neckline.k10.pipeline.run_discovery", lambda **_: (_ for _ in ()).throw(DiscoveryDeadlineExceeded()))
    result = execute_scan(kind="evening", cutoff_at=CUTOFF, configuration=_configuration(), db_path=path,
                          adapter=_Adapter(), model=Model(), metadata=_Metadata(), created_at=CREATED,
                          scan_identity="deadline-terminal", verification_gateway=_FixtureVerificationGateway(),
                          task_id="task-deadline", execution_profile=binding,
                          execution_deadline_at=CREATED + timedelta(hours=2))
    assert result.status == "failed" and result.stage == "deadline"
    scan = store.get_scan(scan_id=result.checkpoint["scanId"], db_path=path)
    assert scan is not None and scan["status"] == "failed" and scan["coverage"]["executionState"] == "deadline"
    assert store.list_publication_batches(db_path=path) == []


@pytest.mark.parametrize("copies", [1, 2])
def test_cli_recovery_worker_handler_reuses_exact_frozen_refs_without_token_or_adapter(tmp_path, monkeypatch, copies):
    """The actual CLI recovery task reaches production handler without source collection."""
    path = tmp_path / "cli-recovery.sqlite"
    initialize_schema(path)
    store.set_run_control(state="open", reason_code="offline_fixture", changed_at=CREATED.isoformat(),
                          changed_by="test", db_path=path)
    config = _configuration()
    config["sourceAdapters"] = [{"key": "tushare-major-news", "lateArrivalReplaySeconds": 86400}]
    strategy_revision = store.append_run_config(config_id="strategy", payload=config, created_at=CREATED.isoformat(), db_path=path)
    _, execution_revision = append_approved_execution_profile(db_path=path, created_at=CREATED.isoformat(), config_id="execution")
    document = store.append_document_version(
        document_id="frozen-doc", source_key="tushare-major-news", external_id="frozen-doc", canonical_url=None,
        content_sha256=sha256(b"frozen").hexdigest(), published_at=(CUTOFF - timedelta(minutes=1)).isoformat(),
        published_precision="exact", fetched_at=CREATED.isoformat(), original_text="冻结资料正文", excerpt=None,
        fetch_version="fixture", metadata={"title": "冻结资料"}, created_at=CREATED.isoformat(), db_path=path,
    )
    scan_id = "scan_" + "a" * 32
    refs = [{"documentId": document.document_id, "revision": document.revision}]
    if copies == 2:
        duplicate = store.append_document_version(
            document_id="frozen-duplicate", source_key="tushare-major-news", external_id="frozen-duplicate", canonical_url=None,
            content_sha256=sha256(b"frozen").hexdigest(), published_at=(CUTOFF - timedelta(minutes=1)).isoformat(),
            published_precision="exact", fetched_at=CREATED.isoformat(), original_text="冻结资料正文", excerpt=None,
            fetch_version="fixture", metadata={"title": "冻结资料"}, created_at=CREATED.isoformat(), db_path=path)
        refs.append({"documentId": duplicate.document_id, "revision": duplicate.revision})
    store.create_scan(scan_id=scan_id, window_kind="evening", cutoff_at=CUTOFF.isoformat(), config_id="strategy",
                      config_revision=strategy_revision, status="failed", created_at=CREATED.isoformat(),
                      completed_at=CREATED.isoformat(), db_path=path,
                      coverage={"inputSnapshotFrozen": True, "inputDocumentRefs": refs, "ingestionState": "completed",
                                "state": "completed", "window": {"kind": "evening", "startAt": (CUTOFF - timedelta(days=1)).isoformat(),
                                "cutoffAt": CUTOFF.isoformat(), "startInclusive": False, "cutoffInclusive": True}})
    task_id = recover_scan(db_path=path, scan_id=scan_id, execution_config_id="execution", execution_config_revision=execution_revision,
                           confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan_id, db_path=path), now=CREATED)

    class Provider:
        def __init__(self):
            self.calls = 0
            self.stages = []

        def chat(self, messages, **_kwargs):
            self.calls += 1
            content = messages[-1].content
            payload = json.loads(content.split("<untrusted-k10-evidence>\n", 1)[1].split("\n</untrusted-k10-evidence>", 1)[0])
            if "policyContent" in payload:
                assert payload["knownSubjects"] == [{"companyCode": "300001.SZ", "headline": "已发布的独立机会"}]
                assert "PRIVATE_OLD_BODY_MUST_NOT_ENTER_TITLES" not in content
            if payload.get("operation") == "titleSelectionReview":
                self.stages.append("titleReview")
                response = {"complete": True, "kept": [{"i": row["i"], "reason": "标题事实独立，保留"}
                                                       for row in payload["items"]], "removed": []}
            elif "inputCount" in payload:
                self.stages.append("titleReconcile")
                response = {"selected": [{"i": 0, "selectedRank": 1, "reason": "离线正文回归"}],
                            "merged": [], "notSelected": []}
            elif "policyContent" in payload:
                self.stages.append("titleBatch")
                response = {"items": [{"documentId": row["documentId"], "revision": row["revision"],
                                       "status": "candidate", "matterKey": "frozen", "stageKey": "initial",
                                       "reason": "离线正文回归"} for row in payload["items"]]}
            else:
                self.stages.append("understand")
                response = {"events": [], "needsFullText": False}
            return LLMResult(ok=True, content=json.dumps(response), provider="fixture", model="deepseek-v4-pro",
                             prompt_tokens=7, completion_tokens=3, total_tokens=10, usage_unavailable=False)
    provider = Provider()
    monkeypatch.setattr(pipeline, "_existing_opportunity_context", lambda **_: [
        {"eventId": "watched-event", "companyCode": "300001.SZ", "headline": "已发布的独立机会", "state": "active",
         "facts": {"privatePriorBody": "PRIVATE_OLD_BODY_MUST_NOT_ENTER_TITLES"}}])
    monkeypatch.setattr(pipeline, "_active_published_candidates", lambda **_: [
        {"eventId": "watched-event", "companyCode": "300001.SZ"}])
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", lambda **_: type("Resolution", (), {"provider": provider, "error": None})())
    monkeypatch.setattr(pipeline, "TuShareMajorNewsAdapter", lambda **_: pytest.fail("recovery attempted source collection"))
    task = run_once(db_path=path, worker_id="recovery-worker", lease_for=timedelta(minutes=5), clock=lambda: CREATED,
                    handlers={"evening_scan": lambda context: pipeline.production_scan_handler(
                        context, tushare_token=None, parquet_dir=path.parent / "parquet", now=lambda: CREATED)}, task_id=task_id)
    assert task is not None and task.status == "completed"
    recovered = store.get_scan(scan_id=scan_id, db_path=path)
    assert recovered is not None and recovered["coverage"]["inputDocumentRefs"] == refs
    assert provider.stages == ["titleBatch", "titleReconcile", "titleReview", "understand"]
    manifest = store.read_title_selection_manifest(task_id=task_id, db_path=path)
    assert manifest is not None and manifest["selectedRefs"] == refs[:1]
    triage = store.read_title_triage_items(task_id=task_id, db_path=path)
    assert len(triage) == copies
    if copies == 2:
        duplicate = next(item for item in triage if item["documentId"] == "frozen-duplicate")
        assert duplicate["disposition"] == "exact_duplicate"
        assert duplicate["mergedRef"] == refs[0]
    bodies = store.completed_execution_items(task_id=task_id, item_kind="document", stage="model:understand", db_path=path)
    assert len(bodies) == 1
    assert store.task_execution_profile(task_id=task_id, db_path=path)["bindingKind"] == "recovery"


def test_cli_morning_worker_preserves_title_slice_and_resumes_before_report(tmp_path, monkeypatch):
    """A running morning scan must not be finalized as a report during a slice."""
    from neckline.k10.cli import enqueue_scan
    from neckline.k10 import title_runtime
    from neckline.k10.discovery import DiscoverySliceYield
    path = tmp_path / 'morning-title-slice.sqlite'
    initialize_schema(path)
    now = datetime(2026, 9, 8, 9, 5, tzinfo=SHANGHAI)
    monkeypatch.setattr(pipeline, '_now', lambda: now)
    store.set_run_control(state='open', reason_code='offline_fixture', changed_at=now.isoformat(), changed_by='test', db_path=path)
    config = _configuration()
    config['sourceAdapters'] = [{'key':'tushare-major-news','lateArrivalReplaySeconds':86400}]
    rev = store.append_run_config(config_id='morning', payload=config, created_at=now.isoformat(), db_path=path)
    eid, erev = append_approved_execution_profile(db_path=path,created_at=now.isoformat(),config_id='execution')
    task_id = enqueue_scan(db_path=path,kind='morning',trading_day=date(2026,9,8),config_id='morning',config_revision=rev,
        execution_config_id=eid,execution_config_revision=erev,now=now)
    adapter = _Adapter()
    adapter.coverage = SourceCoverage('tushare-major-news','market-wide','fixture','bounded','publishedAt','publishedAt',True)
    monkeypatch.setattr(pipeline,'TuShareMajorNewsAdapter',lambda **_:adapter)
    calls=[]
    class Provider:
        def chat(self,messages,**_kwargs):
            payload=json.loads(messages[-1].content.split('<untrusted-k10-evidence>\n',1)[1].split('\n</untrusted-k10-evidence>',1)[0])
            if payload.get('operation') == 'titleSelectionReview':
                calls.append('titleReview')
                result={'complete':True,'kept':[{'i':row['i'],'reason':'标题事实独立，保留'} for row in payload['items']], 'removed':[]}
            elif 'inputCount' in payload:
                calls.append('titleReconcile')
                result={'selected':[{'i':0,'selectedRank':1,'reason':'独立事项'}],'merged':[],'notSelected':[]}
            elif 'policyContent' in payload:
                calls.append('titleBatch')
                result={'items':[{'i':i,'status':'candidate','matterKey':'one','stageKey':'new','reason':'有实质新事实'} for i,_ in enumerate(payload['items'])]}
            else:
                calls.append('understand');result={'events':[],'needsFullText':False}
            return LLMResult(ok=True,content=json.dumps(result),provider='fixture',model='deepseek-v4-pro',
                prompt_tokens=7,completion_tokens=3,total_tokens=10,usage_unavailable=False)
    provider=Provider()
    monkeypatch.setattr(pipeline,'resolve_deepseek_v4_pro',lambda **_:type('Resolution',(),{'provider':provider,'error':None})())
    original=title_runtime.select_title_documents
    first=[True]
    def one_hold(**kwargs):
        selected=original(**kwargs)
        if first[0]:
            first[0]=False
            raise DiscoverySliceYield()
        return selected
    monkeypatch.setattr(title_runtime,'select_title_documents',one_hold)
    def handler(context):
        return pipeline.production_scan_handler(context,tushare_token='fixture',parquet_dir=tmp_path/'parquet',now=lambda:now)
    task=run_once(db_path=path,worker_id='morning-worker',lease_for=timedelta(minutes=5),clock=lambda:now,
        handlers={'morning_scan':handler},task_id=task_id)
    assert task is not None and task.status=='queued'
    assert calls==['titleBatch','titleReconcile','titleReview']
    now+=timedelta(seconds=2)
    task=run_once(db_path=path,worker_id='morning-worker',lease_for=timedelta(minutes=5),clock=lambda:now,
        handlers={'morning_scan':handler},task_id=task_id)
    assert task is not None and task.status=='completed'
    assert calls==['titleBatch','titleReconcile','titleReview','understand']
