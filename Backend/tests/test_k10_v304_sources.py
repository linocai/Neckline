"""V3.0.4 source-interruption regressions at the real task boundary."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from neckline.k10 import store
from neckline.k10.cli import enqueue_scan
from neckline.k10.pipeline import execute_scan as _execute_scan
from neckline.k10.sources import SourceCoverage, SourceDocumentInput, SourceFetchResult
from neckline.k10.windows import SHANGHAI
from neckline.k10.worker import run_once
from tests.test_k10_pipeline import (
    _FixtureVerificationGateway,
    _Metadata,
    _VerifiedModel,
    _configuration,
    initialize_schema,
)
from tests.k10_v306_fixture import append_approved_execution_profile


def execute_scan(**kwargs):
    # These historical scenarios freeze visibility separately from acquisition;
    # today's wall clock must not replace their publication time.
    kwargs.setdefault('publication_clock', lambda: kwargs['completed_at'])
    return _execute_scan(**kwargs)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=SHANGHAI)


class _Adapter:
    coverage = SourceCoverage("fixture-source", "fixture", "fixture", "single", "publishedAt", "publishedAt", True)

    def __init__(self, documents):
        self.documents = tuple(documents)
        self.calls = 0

    def fetch_incremental(self, request):
        self.calls += 1
        return SourceFetchResult(
            documents=self.documents,
            next_cursor=None,
            success_watermark=request.window.cutoff_at,
            pages_fetched=1,
            pages_expected=1,
            exhausted=True,
        )


def _document(external_id: str, published_at: datetime) -> SourceDocumentInput:
    return SourceDocumentInput(
        external_id=external_id,
        canonical_url=f"https://example.test/{external_id}",
        original_text=f"300001.SZ {external_id} 已披露实质订单及两日催化",
        excerpt=None,
        published_at=published_at,
        published_precision="exact",
        fetched_at=published_at + timedelta(minutes=1),
        fetch_version="fixture",
        metadata={},
    )


def _config() -> dict:
    configuration = _configuration()
    configuration["sourceAdapters"] = [{"key": "fixture-source", "lateArrivalReplaySeconds": 86400}]
    return configuration


def _enable_fixture_runtime(path, at: datetime) -> tuple[str, int]:
    store.set_run_control(state="open", reason_code="fixture_v304_sources", changed_at=at.isoformat(), changed_by="test", db_path=path)
    return append_approved_execution_profile(db_path=path, created_at=at.isoformat(), config_id="v304-source-execution")


def test_v304_cli_worker_recovers_document_committed_before_input_freeze(tmp_path, monkeypatch):
    """An owner may die after the atomic document+scan marker commit, never losing that input."""
    path = tmp_path / "source-interrupt.sqlite"
    initialize_schema(path)
    configuration = _config()
    started = _at(7, 21, 1)
    execution_id, execution_revision = _enable_fixture_runtime(path, started)
    revision = store.append_run_config(config_id="fixture", payload=configuration, created_at=started.isoformat(), db_path=path)
    task_id = enqueue_scan(
        db_path=path, kind="evening", trading_day=date(2026, 9, 7), config_id="fixture", config_revision=revision,
        now=started, bootstrap_cutoff=_at(4, 21).isoformat(), execution_config_id=execution_id,
        execution_config_revision=execution_revision,
    )
    adapter = _Adapter([_document("fresh", _at(7, 20))])
    model = _VerifiedModel()
    context_box = {}
    original_append = store.append_document_version

    def interrupt_after_atomic_commit(**kwargs):
        value = original_append(**kwargs)
        if kwargs.get("scan_id") and not context_box.get("interrupted"):
            context_box["interrupted"] = True
            context_box["context"].lease_lost.set()
        return value

    monkeypatch.setattr(store, "append_document_version", interrupt_after_atomic_commit)

    def handler(context):
        context_box["context"] = context
        frozen = store.read_run_config(config_id="fixture", revision=revision, db_path=path)
        return execute_scan(
            kind="evening", cutoff_at=datetime.fromisoformat(context.input_cutoff_at), configuration=frozen["payload"],
            db_path=path, adapter=adapter, model=model, metadata=_Metadata(), created_at=started,
            completed_at=started, config_id="fixture", config_revision=revision,
            bootstrap_cutoff=context.task.payload["sourceBootstrapCutoff"], scan_identity=context.task.task_id,
            verification_gateway=_FixtureVerificationGateway(), leaseguard=context.require_lease,
        )

    with pytest.raises(store.K10Conflict, match="租约"):
        run_once(db_path=path, worker_id="first-owner", lease_for=timedelta(minutes=5),
                 handlers={"evening_scan": handler}, clock=lambda: started, task_id=task_id)

    scan = store.list_scans(window_kind="evening", db_path=path)[0]
    accepted = scan["coverage"]["sourceAcceptedDocumentRefs"]
    assert accepted == [{"documentId": accepted[0]["documentId"], "revision": 1, "isNew": True}]
    assert scan["coverage"].get("inputSnapshotFrozen") is not True
    assert store.load_document_versions(refs=accepted, db_path=path)

    recovered_at = started + timedelta(minutes=6)
    recovered = run_once(db_path=path, worker_id="recovery-owner", lease_for=timedelta(minutes=5),
                         handlers={"evening_scan": handler}, clock=lambda: recovered_at, task_id=task_id)
    assert recovered is not None and recovered.status == "completed"
    repaired = store.get_scan(scan_id=scan["scanId"], db_path=path)
    assert repaired["coverage"]["inputSnapshotFrozen"] is True
    assert repaired["coverage"]["inputDocumentRefs"] == [
        {"documentId": accepted[0]["documentId"], "revision": 1}
    ]
    assert len(store.list_candidates(scan_id=scan["scanId"], state="offered", db_path=path)) == 1


def test_v304_replayed_old_document_never_reenters_discovery_with_fresh_document(tmp_path):
    path = tmp_path / "old-and-fresh.sqlite"
    initialize_schema(path)
    _enable_fixture_runtime(path, _at(7, 21))
    configuration = _config()
    old = _document("old", _at(7, 20))
    fresh = _document("fresh", _at(8, 20))
    first_model = _VerifiedModel()
    first = execute_scan(
        kind="evening", cutoff_at=_at(7, 21), configuration=configuration, db_path=path,
        adapter=_Adapter([old]), model=first_model, metadata=_Metadata(), created_at=_at(7, 21, 1),
        completed_at=_at(7, 21, 1), bootstrap_cutoff=_at(4, 21).isoformat(), scan_identity="first",
        verification_gateway=_FixtureVerificationGateway(),
    )
    assert first.status == "completed"

    class _RecordingModel(_VerifiedModel):
        def __init__(self):
            super().__init__()
            self.read = []

        def understand(self, *, document):
            self.read.append((document.document_id, document.revision))
            return super().understand(document=document)

    second_model = _RecordingModel()
    second = execute_scan(
        kind="evening", cutoff_at=_at(8, 21), configuration=configuration, db_path=path,
        adapter=_Adapter([old, fresh]), model=second_model, metadata=_Metadata(), created_at=_at(8, 21, 1),
        completed_at=_at(8, 21, 1), scan_identity="second", verification_gateway=_FixtureVerificationGateway(),
    )
    assert second.status == "completed"
    second_scan = store.get_scan(scan_id=second.checkpoint["scanId"], db_path=path)
    accepted = {item["documentId"]: item["isNew"] for item in second_scan["coverage"]["sourceAcceptedDocumentRefs"]}
    assert set(accepted.values()) == {False, True}
    assert len(second_model.read) == 1
    assert second_model.read[0][0] == next(document_id for document_id, is_new in accepted.items() if is_new)


def test_v304_interrupted_old_and_fresh_replay_recovers_only_fresh_once(tmp_path, monkeypatch):
    """A replay can return old+fresh again after loss without waking the old opportunity."""
    path = tmp_path / "interrupted-old-and-fresh.sqlite"
    initialize_schema(path)
    execution_id, execution_revision = _enable_fixture_runtime(path, _at(8, 21, 1))
    configuration = _config()
    old = _document("old", _at(7, 20))
    fresh = _document("fresh", _at(8, 20))
    first = execute_scan(
        kind="evening", cutoff_at=_at(7, 21), configuration=configuration, db_path=path,
        adapter=_Adapter([old]), model=_VerifiedModel(), metadata=_Metadata(), created_at=_at(7, 21, 1),
        completed_at=_at(7, 21, 1), bootstrap_cutoff=_at(4, 21).isoformat(), scan_identity="old-consumed",
        verification_gateway=_FixtureVerificationGateway(),
    )
    assert first.status == "completed"
    revision = store.append_run_config(config_id="fixture", payload=configuration, created_at=_at(8, 20).isoformat(), db_path=path)
    task_id = enqueue_scan(db_path=path, kind="evening", trading_day=date(2026, 9, 8), config_id="fixture",
                           config_revision=revision, now=_at(8, 21, 1), execution_config_id=execution_id,
                           execution_config_revision=execution_revision)
    adapter = _Adapter([old, fresh])

    class _RecordingModel(_VerifiedModel):
        def __init__(self):
            super().__init__()
            self.read = []

        def understand(self, *, document):
            self.read.append((document.document_id, document.revision))
            return super().understand(document=document)

    model = _RecordingModel()
    context_box = {}
    original_append = store.append_document_version

    def interrupt_after_fresh_commit(**kwargs):
        value = original_append(**kwargs)
        if kwargs.get("scan_id") and kwargs.get("external_id") == "fresh" and not context_box.get("interrupted"):
            context_box["interrupted"] = True
            context_box["context"].lease_lost.set()
        return value

    monkeypatch.setattr(store, "append_document_version", interrupt_after_fresh_commit)

    def handler(context):
        context_box["context"] = context
        frozen = store.read_run_config(config_id="fixture", revision=revision, db_path=path)
        now = _at(8, 21, 1)
        return execute_scan(
            kind="evening", cutoff_at=datetime.fromisoformat(context.input_cutoff_at), configuration=frozen["payload"],
            db_path=path, adapter=adapter, model=model, metadata=_Metadata(), created_at=now, completed_at=now,
            config_id="fixture", config_revision=revision, scan_identity=context.task.task_id,
            verification_gateway=_FixtureVerificationGateway(), leaseguard=context.require_lease,
        )

    with pytest.raises(store.K10Conflict, match="租约"):
        run_once(db_path=path, worker_id="first-owner", lease_for=timedelta(minutes=5),
                 handlers={"evening_scan": handler}, clock=lambda: _at(8, 21, 1), task_id=task_id)
    interrupted_scan = next(item for item in store.list_scans(window_kind="evening", db_path=path)
                            if item["status"] == "running")
    accepted = {item["documentId"]: item["isNew"] for item in interrupted_scan["coverage"]["sourceAcceptedDocumentRefs"]}
    assert set(accepted.values()) == {False, True}

    recovered = run_once(db_path=path, worker_id="recovery-owner", lease_for=timedelta(minutes=5),
                         handlers={"evening_scan": handler}, clock=lambda: _at(8, 21, 7), task_id=task_id)
    assert recovered is not None and recovered.status == "completed"
    assert len(model.read) == 1
    assert model.read[0][0] == next(document_id for document_id, is_new in accepted.items() if is_new)


@pytest.mark.parametrize("scan_state", ["running", "completed", "partial"])
def test_v304_legacy_unpublished_draft_fails_clearly_without_recomputing(tmp_path, monkeypatch, scan_state):
    """Old durable global ranks must not become fake event ranks on either retry branch."""
    import json
    import sqlite3
    import neckline.k10.pipeline as pipeline

    path = tmp_path / "legacy-draft.sqlite"
    initialize_schema(path)
    _enable_fixture_runtime(path, _at(7, 21))
    configuration = _config()
    model = _VerifiedModel()
    adapter = _Adapter([_document("fresh", _at(7, 20))])
    arguments = dict(kind="evening", cutoff_at=_at(7, 21), configuration=configuration, db_path=path,
        adapter=adapter, model=model, metadata=_Metadata(), created_at=_at(7, 21, 1),
        completed_at=_at(7, 21, 1), bootstrap_cutoff=_at(4, 21).isoformat(), scan_identity="legacy-draft",
        verification_gateway=_FixtureVerificationGateway())
    publish = pipeline._publish_scan
    monkeypatch.setattr(pipeline, "_publish_scan", lambda **_: (_ for _ in ()).throw(RuntimeError("interrupted before publication")))
    with pytest.raises(RuntimeError, match="interrupted"):
        execute_scan(**arguments)
    scan = store.list_scans(window_kind="evening", db_path=path)[0]
    coverage = scan["coverage"]
    draft = coverage["discoveryDraft"]
    for row in draft["candidates"]:
        row.pop("displayRank", None)
        row["comparison"].pop("eventRank", None)
        row["comparison"].pop("rankNamespace", None)
        row["comparison"]["rank"] = 2
    # An existing database from the previous release may contain this durable shape.
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE k10_scans SET status=?, coverage_json=? WHERE scan_id=?",
                     (scan_state, json.dumps(coverage), scan["scanId"]))
    monkeypatch.setattr(pipeline, "_publish_scan", publish)
    usage_before = len(model.usage_records)
    result = execute_scan(**arguments)
    assert result.status == "failed" and result.stage == "legacy_frozen_rank"
    assert "缺少事件内排序" in result.error
    assert len(model.usage_records) == usage_before
    assert store.get_publication_batch(batch_id="publication_" + scan["scanId"], db_path=path) is None
    saved = store.get_scan(scan_id=scan["scanId"], db_path=path)
    assert saved["coverage"]["discoveryDraft"] == draft
    if scan_state == "running":
        assert saved["status"] == "failed"
        assert saved["coverage"]["pipelineState"] == "legacy_frozen_rank"
