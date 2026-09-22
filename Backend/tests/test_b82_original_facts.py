"""B82 P1: source facts retain their meaning through research finalization."""
from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
import json
from pathlib import Path
import socket
import sqlite3
from typing import Any

import httpx
import pytest

from neckline.k10 import pipeline, store as k10_store
from neckline.k10.cli import main as cli_main
from neckline.k10.discovery import DiscoverySliceYield, event_input_facts, event_system_metadata
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.research_runtime import _legacy_merged_understanding_events, research_context_digest, research_context_payload
from neckline.k10.v2_store import bind_strategy
from neckline.k10.windows import SHANGHAI, evening_cutoff
from neckline.k10.worker import run_once
from tests import v340_acceptance_fixture as base
from tests.test_v350_cli_api import DirectRoundTransport, explicit_bindings


_SOURCE_FACTS: dict[str, Any] = {
    # These are legal source facts.  They intentionally collide with runtime
    # bookkeeping names and must retain their exact source meanings.
    "stageKey": {"publisher": "source-stage", "phase": "试制"},
    "eventState": {"publisher": "source-state", "value": "条件生效"},
    "verification": {"publisher": "source-verification", "basis": ["原始公告"]},
    "eventComparison": {"publisher": "source-comparison", "summary": "来源自身的比较描述"},
    # A source may legally use the future reserved envelope name and even its
    # version string.  Only the writer-created *outer* envelope is metadata.
    "_necklineSystem": {
        "version": "k10-event-system-v1",
        "inputFacts": {"source": "not runtime metadata"},
        "stageKey": "source-owned-stage",
        "eventState": "source-owned-state",
    },
    "ordinarySourceFact": {"nested": ["保留", {"key": "value"}]},
}


class _OriginalFactsTransport(DirectRoundTransport):
    """Produce one event whose input facts use system-looking source keys."""

    def respond(self, request: httpx.Request) -> httpx.Response:
        payload = self._packet(request)
        if (payload.get("action") is None
                and isinstance(payload.get("documentId"), str)
                and isinstance(payload.get("revision"), int)):
            document_id = payload["documentId"]
            if self.document_numbers.get(document_id) != 0:
                raise AssertionError(f"unexpected source-understanding document: {document_id!r}")
            return self._source_event(document_id=document_id, revision=payload["revision"], source_number=0)
        return super().respond(request)

    def _source_event(self, *, document_id: str, revision: int, source_number: int) -> httpx.Response:
        reference = {"documentId": document_id, "revision": revision}
        self._record("understand", f"event-000/source-{source_number}")
        return self._ok({
            "events": [{
                "canonicalKey": "event-000",
                "stageKey": "runtime-stage",
                "eventState": "runtime-state",
                "headline": "原始事实与运行时元数据同名",
                "eventKind": "disclosure",
                "facts": _SOURCE_FACTS,
                "sourceRefs": [reference],
                "claims": [{
                    "text": f"来源 {source_number} 披露事件仍需条件化比较。",
                    "kind": "factual_assertion",
                    "novelty": "new_fact",
                    "speaker": "来源",
                    "subject": "项目",
                    "object": "事项",
                    "action": "披露",
                    "stageOrCondition": "条件生效",
                    "timeText": "本次来源",
                    "verificationStatus": "unverified",
                    "decisionImpact": "影响公司关联比较",
                    "sourceRef": reference,
                    "location": "paragraph:1",
                }],
            }],
            "needsFullText": False,
        })


class _MergedSourcesTransport(_OriginalFactsTransport):
    """Two independent documents describing the same canonical event."""

    def respond(self, request: httpx.Request) -> httpx.Response:
        payload = self._packet(request)
        if "inputCount" in payload:
            items = payload.get("items")
            assert isinstance(items, list) and len(items) == 2
            # The source-selection order is deliberately inverse to the
            # checkpoint reader's item-key ordering.  A recovery that silently
            # changes this order changes the frozen event context.
            selected = [1, 0]
            self.merged_selected_title_indexes = tuple(selected)
            self._record("titleGlobal", "reversed-document-order")
            return self._ok({
                "selectionComplete": True,
                "reviewedCount": len(items),
                "selected": [{"i": index, "selectedRank": rank, "reason": "同一事件的独立来源"}
                             for rank, index in enumerate(selected, start=1)],
                "merged": [],
            })
        if (payload.get("action") is None
                and isinstance(payload.get("documentId"), str)
                and isinstance(payload.get("revision"), int)):
            document_id = payload["documentId"]
            source_number = self.document_numbers.get(document_id)
            if source_number not in {0, 1}:
                raise AssertionError(f"unexpected merged-source document: {document_id!r}")
            return self._source_event(
                document_id=document_id, revision=payload["revision"], source_number=source_number,
            )
        return super().respond(request)


def _enqueue_evening(*, database: Path, day, config_id: str, config_revision: int,
                     execution_id: str, execution_revision: int) -> str:
    output = StringIO()
    with redirect_stdout(output):
        assert cli_main([
            "enqueue", "--db", str(database), "--kind", "evening", "--trading-day", day.isoformat(),
            "--config-id", config_id, "--config-revision", str(config_revision),
            "--execution-config-id", execution_id, "--execution-config-revision", str(execution_revision),
            "--bootstrap-cutoff", (evening_cutoff(day) - timedelta(hours=2)).isoformat(),
        ]) == 0
    task_id = output.getvalue().strip()
    assert task_id.startswith("task_")
    return task_id


def _latest_snapshot_binding(*, database: Path, task_id: str) -> tuple[str, str, int, dict[str, Any], dict[str, Any]]:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT snapshot_id,event_id,event_revision,snapshot_json FROM k10_research_snapshot_revisions "
            "WHERE task_id=? ORDER BY revision DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        assert row is not None
        event_row = connection.execute(
            "SELECT facts_json FROM k10_event_revisions WHERE event_id=? AND revision=?",
            (row[1], row[2]),
        ).fetchone()
    assert event_row is not None
    snapshot = json.loads(row[3])
    facts = json.loads(event_row[0])
    assert isinstance(snapshot, dict) and isinstance(facts, dict)
    return str(row[0]), str(row[1]), int(row[2]), snapshot, facts


def _append_legacy_v1_execution_profile(*, database: Path, config_id: str, config_revision: int,
                                        execution_id: str, execution_revision: int,
                                        created_at: datetime) -> tuple[int, int]:
    """Append the historic B81 policy rather than relabeling a B82 binding.

    The legacy receipt-only branch is gated by the *frozen task policy*, so a
    B82 task whose snapshot JSON is merely rewritten cannot exercise it.
    """
    current = k10_store.read_execution_config(
        config_id=execution_id, revision=execution_revision, db_path=database,
    )
    assert current is not None
    payload = json.loads(json.dumps(current["payload"], ensure_ascii=False))
    discovery = payload.get("discovery")
    assert isinstance(discovery, dict)
    discovery["investigationPromptContractRevision"] = "k10-investigation-v1"
    legacy_execution_revision = k10_store.append_execution_config(
        config_id=execution_id, payload=payload, created_at=created_at.isoformat(), db_path=database,
    )
    run_config = k10_store.read_run_config(
        config_id=config_id, revision=config_revision, db_path=database,
    )
    assert run_config is not None
    legacy_config = json.loads(json.dumps(run_config["payload"], ensure_ascii=False))
    legacy_snapshot_id = "k10-v2-b82-original-facts-legacy-v1"
    legacy_config["strategySnapshotId"] = legacy_snapshot_id
    legacy_config_revision = k10_store.append_run_config(
        config_id=config_id, payload=legacy_config, created_at=created_at.isoformat(), db_path=database,
    )
    # This isolated historical setup updates the explicit strategy binding
    # before the real CLI producer runs; it never patches a queued task.
    bind_strategy(
        db_path=database, snapshot_id=legacy_snapshot_id, config_id=config_id,
        config_revision=legacy_config_revision, execution_config_id=execution_id,
        execution_config_revision=legacy_execution_revision, created_at=created_at.isoformat(),
    )
    return legacy_config_revision, legacy_execution_revision


def _run_cross_slice(*, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                     tamper_source_field: str | None = None, source_count: int = 1,
                     tamper_source_manifest: str | None = None) -> dict[str, object]:
    if source_count not in {1, 2}:
        raise ValueError("original-facts fixture supports one or two sources")
    database = tmp_path / f"original-facts-{tamper_source_field or 'valid'}.sqlite"
    business_clock = [base.RUN_AT]
    config_id, config_revision, execution_id, execution_revision = base.seed_database(
        database, trading_day=base.DAY, fixture_now=business_clock[0],
    )
    monkeypatch.setattr(base, "TITLE_COUNT", source_count)
    monkeypatch.setattr(base, "DeterministicTransport",
                        _OriginalFactsTransport if source_count == 1 else _MergedSourcesTransport)
    transport, _ = base.install_offline_transports(
        monkeypatch, refusal_event=None, selected_event_count=source_count, fixture_run_at=business_clock[0],
    )
    monkeypatch.setattr(pipeline, "_now", lambda: business_clock[0])
    monkeypatch.setattr(socket.socket, "connect", base._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", base._deny_network)
    def resolve_provider(**_kwargs):
        # Each worker gets a fresh adapter instance.  Receipt/checkpoint reuse
        # must come from SQLite, never a shared in-process response cache.
        provider = MeteredProvider(
            ledger_db=database, ledger_task="original-facts", api_key="fixture", model="deepseek-v4-pro",
            name="fixture", api_url="https://fixture.invalid/v1/chat/completions", read_timeout=1,
            use_streaming=False,
        )
        provider.max_attempts = 1
        return ProviderResolution("configured", provider, "fixture", None)
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", resolve_provider)
    task_id = _enqueue_evening(
        database=database, day=base.DAY, config_id=config_id, config_revision=config_revision,
        execution_id=execution_id, execution_revision=execution_revision,
    )

    def handler(context):
        return pipeline.production_scan_handler(
            context, tushare_token="fixture-token", parquet_dir=tmp_path / "parquet",
            now=lambda: business_clock[0],
        )

    original_prioritize = pipeline._CheckpointedDiscoveryModel.prioritize
    yielded = {"value": False}

    def persist_priority_then_yield(self, *, candidates):
        # The snapshot, direct-round response, and ranking receipt are all
        # durable before the interrupt.  Resume therefore exercises only the
        # frozen finalizer and can prove no additional source/model call is
        # made when a persisted source manifest has been altered.
        result = original_prioritize(self, candidates=candidates)
        if not yielded["value"]:
            yielded["value"] = True
            raise DiscoverySliceYield()
        return result

    monkeypatch.setattr(pipeline._CheckpointedDiscoveryModel, "prioritize", persist_priority_then_yield)
    first = run_once(
        db_path=database, task_id=task_id, worker_id="b82-original-facts-first", lease_for=timedelta(minutes=5),
        handlers={"evening_scan": handler}, clock=lambda: datetime.now(SHANGHAI), require_b76_contract=True,
    )
    assert first is not None and first.status == "queued" and yielded["value"]
    snapshot_id, event_id, event_revision, snapshot, facts = _latest_snapshot_binding(
        database=database, task_id=task_id,
    )

    if tamper_source_field is not None:
        system = facts.get("_necklineSystem")
        assert isinstance(system, dict) and isinstance(system.get("inputFacts"), dict)
        input_facts = dict(system["inputFacts"])
        assert tamper_source_field in input_facts
        input_facts[tamper_source_field] = {"tampered": tamper_source_field}
        system["inputFacts"] = input_facts
        facts["_necklineSystem"] = system
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE k10_event_revisions SET facts_json=? WHERE event_id=? AND revision=?",
                (json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                 event_id, event_revision),
            )
    if tamper_source_manifest is not None:
        if source_count != 2 or tamper_source_manifest not in {"removed", "replaced"}:
            raise ValueError("source-manifest mutation requires the two-source fixture")
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT source_refs_json FROM k10_event_revisions WHERE event_id=? AND revision=?",
                (event_id, event_revision),
            ).fetchone()
            assert row is not None
            refs = json.loads(row[0])
            assert isinstance(refs, list) and len(refs) == 2
            if tamper_source_manifest == "removed":
                refs = refs[:1]
            else:
                refs[1] = {"documentId": "doc_foreign_source", "revision": 1}
            connection.execute(
                "UPDATE k10_event_revisions SET source_refs_json=? WHERE event_id=? AND revision=?",
                (json.dumps(refs, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                 event_id, event_revision),
            )

    with sqlite3.connect(database) as connection:
        retry = connection.execute(
            "SELECT not_before_at FROM k10_task_retry_schedules WHERE task_id=?", (task_id,),
        ).fetchone()
    assert retry is not None
    calls_before_resume = tuple(transport.calls)
    due = datetime.fromisoformat(str(retry[0]))
    second = run_once(
        db_path=database, task_id=task_id, worker_id="b82-original-facts-second", lease_for=timedelta(minutes=5),
        handlers={"evening_scan": handler}, clock=lambda: due + timedelta(seconds=1), require_b76_contract=True,
    )
    assert second is not None
    return {
        "database": database,
        "taskId": task_id,
        "first": first,
        "second": second,
        "snapshotId": snapshot_id,
        "snapshot": snapshot,
        "facts": facts,
        "callsBeforeResume": calls_before_resume,
        "transport": transport,
        "bindings": {
            "config_id": config_id,
            "config_revision": config_revision,
            "execution_id": execution_id,
            "execution_revision": execution_revision,
        },
    }


def _rewrite_as_b81_snapshot(*, database: Path, task_id: str,
                             break_understand: str | None = None,
                             historical_contract: bool = True) -> None:
    """Seed a precise historical B81 row before its read-only finalization.

    B81 had neither ``admissionContext`` nor the protected envelope.  This is
    a historical-data prerequisite only: task, scan, event/snapshot typed
    identities and all completed receipts are left untouched.  The next real
    worker may read the old row, but may not repair it or issue a fresh POST.
    """
    snapshot_id, event_id, event_revision, snapshot, facts = _latest_snapshot_binding(
        database=database, task_id=task_id,
    )
    with sqlite3.connect(database) as connection:
        if historical_contract:
            system = facts.get("_necklineSystem")
            assert isinstance(system, dict) and isinstance(system.get("inputFacts"), dict)
            legacy_facts = {
                **dict(system["inputFacts"]),
                "stageKey": system["stageKey"],
                "eventState": system["eventState"],
                "verification": system["verification"],
            }
            legacy_snapshot = dict(snapshot)
            legacy_snapshot.pop("admissionContext", None)
            # This row models the B81 round contract.  B82's v2 prompt
            # revision is intentionally ineligible for the narrow fallback.
            legacy_snapshot["promptContractRevision"] = "k10-research-3.5.0-b78"
            connection.execute(
                "UPDATE k10_event_revisions SET facts_json=? WHERE event_id=? AND revision=?",
                (json.dumps(legacy_facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                 event_id, event_revision),
            )
            connection.execute(
                "UPDATE k10_research_snapshot_revisions SET snapshot_json=? WHERE snapshot_id=? AND revision=?",
                (json.dumps(legacy_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                 snapshot_id, snapshot["revision"]),
            )
            connection.execute(
                "UPDATE k10_research_snapshot_revisions SET prompt_contract_revision=? WHERE snapshot_id=? AND revision=?",
                ("k10-research-3.5.0-b78", snapshot_id, snapshot["revision"]),
            )
        if break_understand is None:
            return
        row = connection.execute(
            "SELECT item_key,result_json FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND item_kind='document' AND stage='understand' AND status='completed'",
            (task_id,),
        ).fetchone()
        assert row is not None
        if break_understand == "derived_missing":
            # The derived row may be absent after an older worker interruption.
            # Its matching model checkpoint / exact response receipt remains a
            # durable completed source proof, so B81 may restore it locally.
            connection.execute(
                "DELETE FROM k10_execution_item_checkpoints "
                "WHERE task_id=? AND item_kind='document' AND stage='understand' AND item_key=?",
                (task_id, row[0]),
            )
        elif break_understand in {"unproven", "no_prior", "model_checkpoint_only"}:
            # Remove every exact source proof while leaving the settled
            # external-attempt identity in place.  Recovery must safely refuse
            # it instead of issuing an equivalent new model request.
            result = json.loads(row[1])
            events = result.get("events")
            assert isinstance(events, list) and len(events) == 1
            source_refs = events[0].get("sourceRefs")
            assert isinstance(source_refs, list) and len(source_refs) == 1
            exact_source_refs = json.dumps(
                source_refs, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            if break_understand == "model_checkpoint_only":
                # A completed model checkpoint remains an exact current-task
                # result.  The derived document row, raw receipt, and cache
                # may all be absent without licensing a replacement POST.
                connection.execute(
                    "DELETE FROM k10_execution_item_checkpoints "
                    "WHERE task_id=? AND item_kind='document' AND stage='understand' AND item_key=?",
                    (task_id, row[0]),
                )
            else:
                connection.execute(
                    "DELETE FROM k10_execution_item_checkpoints "
                    "WHERE task_id=? AND item_kind='document' AND stage IN ('understand', 'model:understand')",
                    (task_id,),
                )
            receipt_attempts = connection.execute(
                "SELECT attempt_id FROM k10_external_attempts "
                "WHERE task_id=? AND stage='fullText' AND item_key LIKE ? AND state='succeeded'",
                (task_id, f"understand:{row[0]}:%"),
            ).fetchall()
            assert len(receipt_attempts) == 1
            connection.execute(
                "DELETE FROM k10_model_response_receipts WHERE attempt_id=?",
                (receipt_attempts[0][0],),
            )
            # The source-fact cache is an equally valid, immutable proof when
            # its prompt/source/template/model key is present.  This fixture
            # must delete only the matching source's cache entry before it can
            # claim that no local proof remains.
            cached = connection.execute(
                "SELECT cache_key FROM k10_fact_cache WHERE source_refs_json=?",
                (exact_source_refs,),
            ).fetchall()
            assert len(cached) == 1
            connection.execute(
                "DELETE FROM k10_fact_cache WHERE cache_key=?", (cached[0][0],),
            )
            if break_understand == "no_prior":
                # Contrast control: a genuinely never-sent body has no paid
                # attempt to protect.  It remains admissible for its first
                # normal request even under a legacy task contract.
                connection.execute(
                    "DELETE FROM k10_external_attempts WHERE attempt_id=?",
                    (receipt_attempts[0][0],),
                )
        elif break_understand == "mismatched":
            payload = json.loads(row[1])
            assert isinstance(payload.get("events"), list) and payload["events"]
            payload["events"][0]["canonicalKey"] = "event-b81-fallback-mismatch"
            connection.execute(
                "UPDATE k10_execution_item_checkpoints SET result_json=? WHERE task_id=? AND item_key=?",
                (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                 task_id, row[0]),
            )
        else:
            raise AssertionError(f"unknown legacy understand failure: {break_understand!r}")


def _run_b81_fallback_finalization(*, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                   break_understand: str | None = None, source_count: int = 1,
                                   historical_contract: bool = True) -> dict[str, object]:
    """Reach finalization only after all current-task work is durably complete."""
    if source_count not in {1, 2}:
        raise ValueError("B81 fallback fixture supports one or two sources")
    database = tmp_path / f"b81-fallback-{break_understand or 'valid'}.sqlite"
    business_clock = [base.RUN_AT]
    config_id, config_revision, execution_id, execution_revision = base.seed_database(
        database, trading_day=base.DAY, fixture_now=business_clock[0],
    )
    if historical_contract:
        config_revision, execution_revision = _append_legacy_v1_execution_profile(
            database=database, config_id=config_id, config_revision=config_revision,
            execution_id=execution_id, execution_revision=execution_revision,
            created_at=business_clock[0],
        )
    monkeypatch.setattr(base, "TITLE_COUNT", source_count)
    monkeypatch.setattr(base, "DeterministicTransport",
                        _OriginalFactsTransport if source_count == 1 else _MergedSourcesTransport)
    transport, _ = base.install_offline_transports(
        monkeypatch, refusal_event=None, selected_event_count=source_count, fixture_run_at=business_clock[0],
    )
    monkeypatch.setattr(pipeline, "_now", lambda: business_clock[0])
    monkeypatch.setattr(socket.socket, "connect", base._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", base._deny_network)
    def resolve_provider(**_kwargs):
        provider = MeteredProvider(
            ledger_db=database, ledger_task="b81-fallback", api_key="fixture", model="deepseek-v4-pro",
            name="fixture", api_url="https://fixture.invalid/v1/chat/completions", read_timeout=1,
            use_streaming=False,
        )
        provider.max_attempts = 1
        return ProviderResolution("configured", provider, "fixture", None)
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", resolve_provider)
    task_id = _enqueue_evening(
        database=database, day=base.DAY, config_id=config_id, config_revision=config_revision,
        execution_id=execution_id, execution_revision=execution_revision,
    )

    def handler(context):
        return pipeline.production_scan_handler(
            context, tushare_token="fixture-token", parquet_dir=tmp_path / "parquet",
            now=lambda: business_clock[0],
        )

    # Yield after the priority receipt is durable.  This is inside the normal
    # discovery slice boundary, so the worker writes its resumable checkpoint
    # rather than treating the yield as a handler failure.
    original_prioritize = pipeline._CheckpointedDiscoveryModel.prioritize
    yielded = {"value": False}

    def persist_priority_then_yield(self, *, candidates):
        outcome = original_prioritize(self, candidates=candidates)
        if not yielded["value"]:
            yielded["value"] = True
            raise DiscoverySliceYield()
        return outcome

    monkeypatch.setattr(pipeline._CheckpointedDiscoveryModel, "prioritize", persist_priority_then_yield)
    first = run_once(
        db_path=database, task_id=task_id, worker_id="b82-b81-fallback-first", lease_for=timedelta(minutes=5),
        handlers={"evening_scan": handler}, clock=lambda: datetime.now(SHANGHAI), require_b76_contract=True,
    )
    assert first is not None and first.status == "queued" and yielded["value"]
    _rewrite_as_b81_snapshot(
        database=database, task_id=task_id, break_understand=break_understand,
        historical_contract=historical_contract,
    )
    if break_understand == "unproven":
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM k10_execution_item_checkpoints "
                "WHERE task_id=? AND item_kind='document' AND stage IN ('understand', 'model:understand')",
                (task_id,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM k10_model_response_receipts receipts "
                "JOIN k10_external_attempts attempts ON attempts.attempt_id=receipts.attempt_id "
                "WHERE attempts.task_id=? AND attempts.stage='fullText' AND attempts.item_key LIKE ?",
                (task_id, "understand:%"),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM k10_fact_cache",
            ).fetchone() == (0,)
            row = connection.execute(
                "SELECT prompt_contract_revision,snapshot_json FROM k10_research_snapshot_revisions "
                "WHERE task_id=? ORDER BY revision DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            assert row is not None
            if historical_contract:
                assert row[0] == "k10-research-3.5.0-b78"
                assert "admissionContext" not in json.loads(row[1])
            else:
                assert row[0] == "k10-investigation-v2"
                assert "admissionContext" in json.loads(row[1])
    calls_before_resume = tuple(transport.calls)
    with sqlite3.connect(database) as connection:
        retry = connection.execute(
            "SELECT not_before_at FROM k10_task_retry_schedules WHERE task_id=?", (task_id,),
        ).fetchone()
    assert retry is not None
    due = datetime.fromisoformat(str(retry[0]))
    second = run_once(
        db_path=database, task_id=task_id, worker_id="b82-b81-fallback-second", lease_for=timedelta(minutes=5),
        handlers={"evening_scan": handler}, clock=lambda: due + timedelta(seconds=1), require_b76_contract=True,
    )
    assert second is not None
    return {
        "database": database,
        "taskId": task_id,
        "first": first,
        "second": second,
        "callsBeforeResume": calls_before_resume,
        "transport": transport,
        "bindings": {
            "config_id": config_id,
            "config_revision": config_revision,
            "execution_id": execution_id,
            "execution_revision": execution_revision,
        },
    }


def test_b82_original_facts_survive_writer_and_cross_slice_finalization(tmp_path, monkeypatch):
    result = _run_cross_slice(tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert result["second"].status == "completed"
    facts = result["facts"]
    system = facts.get("_necklineSystem")
    assert isinstance(system, dict)
    input_facts = system.get("inputFacts")
    assert isinstance(input_facts, dict)
    for key, value in _SOURCE_FACTS.items():
        assert input_facts[key] == value
        if key != "_necklineSystem":
            assert facts[key] == value
    assert facts["_necklineSystem"] is system
    assert system["stageKey"] == "runtime-stage"
    assert system["eventState"] == "runtime-state"
    assert isinstance(system.get("verification"), dict)
    snapshot = result["snapshot"]
    admission = snapshot.get("admissionContext")
    assert isinstance(admission, dict)
    assert all(admission["facts"][key] == value for key, value in _SOURCE_FACTS.items())
    with base.actual_api(result["database"], **explicit_bindings(result["database"])) as client:
        report = client.get("/api/v1/k10/v2/reports/latest?window=evening").json()["report"]
    assert report["status"] == "completed" and report["availableAt"] and report["eveningCards"]


@pytest.mark.parametrize("field", tuple(_SOURCE_FACTS))
def test_b82_original_input_facts_tampering_blocks_final_publication(tmp_path, monkeypatch, field):
    result = _run_cross_slice(tmp_path=tmp_path, monkeypatch=monkeypatch, tamper_source_field=field)
    assert result["second"].status == "failed"
    with sqlite3.connect(result["database"]) as connection:
        row = connection.execute(
            "SELECT coverage_json FROM k10_scans WHERE status='failed' ORDER BY created_at DESC LIMIT 1",
        ).fetchone()
        assert row is not None
        coverage = json.loads(row[0])
        assert coverage["researchFailure"] == "research_snapshot_missing"
        assert connection.execute("SELECT COUNT(*) FROM k10_publication_samples").fetchone() == (0,)
        report = connection.execute(
            "SELECT available_at FROM k10_v2_report_runs WHERE scan_id=(SELECT scan_id FROM k10_scans "
            "WHERE status='failed' ORDER BY created_at DESC LIMIT 1)",
        ).fetchone()
    assert report is None or report[0] is None


def test_b82_same_event_two_sources_merge_into_one_frozen_research_input(tmp_path, monkeypatch):
    result = _run_cross_slice(tmp_path=tmp_path, monkeypatch=monkeypatch, source_count=2)
    assert result["second"].status == "completed"
    with sqlite3.connect(result["database"]) as connection:
        title_row = connection.execute(
            "SELECT result_json FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND stage='model:titleBatch' AND status='completed'",
            (result["taskId"],),
        ).fetchone()
        assert title_row is not None
        title_items = json.loads(title_row[0])["items"]
        selected_order = tuple(title_items[index]["documentId"] for index in result["transport"].merged_selected_title_indexes)
        assert selected_order == tuple(sorted(selected_order, reverse=True))
        row = connection.execute(
            "SELECT source_refs_json FROM k10_event_revisions WHERE event_id=? AND revision=?",
            (result["snapshot"]["eventId"], result["snapshot"]["eventRevision"]),
        ).fetchone()
        assert row is not None
        refs = json.loads(row[0])
        assert isinstance(refs, list) and len(refs) == 2
        assert connection.execute(
            "SELECT COUNT(DISTINCT snapshot_id) FROM k10_research_snapshot_revisions WHERE task_id=?",
            (result["taskId"],),
        ).fetchone() == (1,)
    with base.actual_api(result["database"], **explicit_bindings(result["database"])) as client:
        report = client.get("/api/v1/k10/v2/reports/latest?window=evening").json()["report"]
    assert report["status"] == "completed" and len(report["eveningCards"]) == 1


@pytest.mark.parametrize("mutation", ("removed", "replaced"))
def test_b82_same_event_source_manifest_change_rejects_frozen_snapshot(tmp_path, monkeypatch, mutation):
    result = _run_cross_slice(
        tmp_path=tmp_path, monkeypatch=monkeypatch, source_count=2,
        tamper_source_manifest=mutation,
    )
    assert result["second"].status == "failed"
    assert tuple(result["transport"].calls) == result["callsBeforeResume"]
    with sqlite3.connect(result["database"]) as connection:
        row = connection.execute(
            "SELECT coverage_json FROM k10_scans WHERE status='failed' ORDER BY created_at DESC LIMIT 1",
        ).fetchone()
        assert row is not None and json.loads(row[0])["researchFailure"] == "research_snapshot_missing"
        assert connection.execute("SELECT COUNT(*) FROM k10_publication_samples").fetchone() == (0,)


def test_b82_b81_snapshot_fallback_uses_completed_understand_without_new_post(tmp_path, monkeypatch):
    result = _run_b81_fallback_finalization(tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert result["second"].status == "completed"
    assert tuple(result["transport"].calls) == result["callsBeforeResume"]
    with base.actual_api(result["database"], **explicit_bindings(result["database"])) as client:
        report = client.get("/api/v1/k10/v2/reports/latest?window=evening").json()["report"]
    assert report["status"] == "completed" and report["availableAt"]


def test_b82_b81_source_owned_system_named_fact_remains_readable_through_api(tmp_path, monkeypatch):
    result = _run_b81_fallback_finalization(tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert result["second"].status == "completed"
    _, _, _, _, legacy_facts = _latest_snapshot_binding(database=result["database"], task_id=result["taskId"])
    # This is raw B81 business input, not a valid writer envelope despite the
    # deliberately identical field name and version string.
    assert event_system_metadata(legacy_facts) is None
    assert event_input_facts(legacy_facts)["_necklineSystem"] == _SOURCE_FACTS["_necklineSystem"]
    with base.actual_api(result["database"], **explicit_bindings(result["database"])) as client:
        report = client.get("/api/v1/k10/v2/reports/latest?window=evening").json()["report"]
        assert report is not None
        materials_response = client.get(f"/api/v1/k10/v2/reports/{report['reportId']}/materials")
        materials_response.raise_for_status()
        materials = materials_response.json()
        opportunities_response = client.get("/api/v1/k10/opportunities")
        opportunities_response.raise_for_status()
        opportunities = opportunities_response.json()["items"]
        assert len(opportunities) == 1
        detail_response = client.get(f"/api/v1/k10/opportunities/{opportunities[0]['opportunityId']}")
        detail_response.raise_for_status()
        detail = detail_response.json()
    assert detail["commonFacts"]
    raw_detail = detail["commonFacts"][0]["rawDetail"]
    assert raw_detail["sourceFacts"]["_necklineSystem"] == _SOURCE_FACTS["_necklineSystem"]
    assert len(materials["items"]) == 1
    material = materials["items"][0]
    assert material["sourceRefs"]
    for ref in material["sourceRefs"]:
        assert ref["documentId"] and ref["revision"] == 1 and ref["sourceKey"] == "tushare-major-news"
        assert ref["title"] and ref["url"]
    assert any(fact["sourceRefs"] for fact in material["facts"])


def test_b82_b81_derived_understand_checkpoint_rebuilds_from_exact_proof_without_post(tmp_path, monkeypatch):
    result = _run_b81_fallback_finalization(
        tmp_path=tmp_path, monkeypatch=monkeypatch, break_understand="derived_missing",
    )
    assert result["second"].status == "completed"
    assert tuple(result["transport"].calls) == result["callsBeforeResume"]
    with sqlite3.connect(result["database"]) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND item_kind='document' AND stage='understand' AND status='completed'",
            (result["taskId"],),
        ).fetchone() == (1,)


def test_b82_b81_legacy_fallback_preserves_two_source_merge_without_post(tmp_path, monkeypatch):
    result = _run_b81_fallback_finalization(tmp_path=tmp_path, monkeypatch=monkeypatch, source_count=2)
    assert result["second"].status == "completed"
    assert tuple(result["transport"].calls) == result["callsBeforeResume"]
    selected = k10_store.read_title_selection_manifest(task_id=result["taskId"], db_path=result["database"])
    selected_refs = selected.get("selectedRefs") if isinstance(selected, dict) else None
    assert isinstance(selected_refs, list) and len(selected_refs) == 2
    expected_refs = [(row["documentId"], row["revision"]) for row in selected_refs]
    assert expected_refs == list(reversed(sorted(expected_refs)))
    merged = _legacy_merged_understanding_events(task_id=result["taskId"], db_path=result["database"])
    assert len(merged) == 1
    candidate = merged[0]
    assert candidate.headline == "原始事实与运行时元数据同名"
    assert [(ref.document_id, ref.revision) for ref in candidate.source_refs] == expected_refs
    source_facts = candidate.facts["sourceFacts"]
    assert [
        (row["sourceRefs"][0]["documentId"], row["sourceRefs"][0]["revision"])
        for row in source_facts
    ] == expected_refs
    assert all(row["facts"]["ordinarySourceFact"] == _SOURCE_FACTS["ordinarySourceFact"] for row in source_facts)
    assert all(row["facts"]["_necklineSystem"] == _SOURCE_FACTS["_necklineSystem"] for row in source_facts)
    source_claims = candidate.facts["researchClaims"]
    assert [claim["sourceRef"]["documentId"] for claim in source_claims] == [row[0] for row in expected_refs]
    context = research_context_payload(
        event=candidate, cutoff_at=evening_cutoff(base.DAY), cutoff_inclusive=False,
    )
    with sqlite3.connect(result["database"]) as connection:
        row = connection.execute(
            "SELECT source_refs_json FROM k10_event_revisions "
            "WHERE event_id=(SELECT event_id FROM k10_research_snapshot_revisions WHERE task_id=? "
            "ORDER BY revision DESC LIMIT 1)",
            (result["taskId"],),
        ).fetchone()
        snapshot_row = connection.execute(
            "SELECT context_sha256 FROM k10_research_snapshot_revisions WHERE task_id=? "
            "ORDER BY revision DESC LIMIT 1",
            (result["taskId"],),
        ).fetchone()
        assert row is not None and json.loads(row[0]) == [
            {"documentId": document_id, "revision": revision} for document_id, revision in expected_refs
        ]
        assert snapshot_row is not None and snapshot_row[0] == research_context_digest(
            event=candidate, cutoff_at=evening_cutoff(base.DAY), cutoff_inclusive=False,
        )
    assert context["sourceRefs"] == [
        {"documentId": document_id, "revision": revision} for document_id, revision in expected_refs
    ]


@pytest.mark.parametrize("break_understand", ("unproven", "mismatched"))
def test_b82_b81_snapshot_fallback_requires_exact_completed_understand(tmp_path, monkeypatch, break_understand):
    result = _run_b81_fallback_finalization(
        tmp_path=tmp_path, monkeypatch=monkeypatch, break_understand=break_understand,
    )
    assert result["second"].status == "failed"
    assert tuple(result["transport"].calls) == result["callsBeforeResume"]
    with sqlite3.connect(result["database"]) as connection:
        row = connection.execute(
            "SELECT coverage_json FROM k10_scans WHERE status='failed' ORDER BY created_at DESC LIMIT 1",
        ).fetchone()
        assert row is not None
        coverage = json.loads(row[0])
        if break_understand == "unproven":
            # This fails while restoring the body proof, before a research
            # snapshot can be finalized.  Keep the actual, safe reason rather
            # than mislabeling it as a missing snapshot.
            assert coverage["executionState"] == "deep_read"
            delivery = coverage["delivery"]
            assert delivery["outcome"] == "failed"
            assert [(gap["stage"], gap["reasonCode"]) for gap in delivery["gaps"]] == [
                ("understand", "provider_response_receipt_unverifiable"),
            ]
            # The retry is not allowed to overwrite the original completed
            # article admission while explaining why a later legacy recovery
            # cannot be proven.
            assert connection.execute(
                "SELECT state,reason_code FROM k10_v2_article_admissions "
                "WHERE task_id=? ORDER BY document_id,revision",
                (result["taskId"],),
            ).fetchall() == [("completed", None)]
            assert connection.execute(
                "SELECT COUNT(*) FROM k10_external_attempts "
                "WHERE task_id=? AND stage='fullText' AND item_key LIKE ?",
                (result["taskId"], "understand:%"),
            ).fetchone() == (1,)
        else:
            # A readable but source-mismatched completed checkpoint is stopped
            # before finalization; it may not be converted into a new request.
            assert coverage["executionState"] == "deep_read"
        assert connection.execute("SELECT COUNT(*) FROM k10_publication_samples").fetchone() == (0,)


def test_b82_b81_body_with_no_prior_attempt_makes_its_first_normal_request(tmp_path, monkeypatch):
    """A legacy task may still contain a genuinely never-sent selected body."""
    result = _run_b81_fallback_finalization(
        tmp_path=tmp_path, monkeypatch=monkeypatch, break_understand="no_prior",
    )
    assert result["second"].status == "completed"
    resumed_calls = tuple(result["transport"].calls)[len(result["callsBeforeResume"]):]
    assert resumed_calls == (("understand", "event-000/source-0"),)
    with sqlite3.connect(result["database"]) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM k10_external_attempts "
            "WHERE task_id=? AND stage='fullText' AND item_key LIKE ?",
            (result["taskId"], "understand:%"),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM k10_model_response_receipts receipts "
            "JOIN k10_external_attempts attempts ON attempts.attempt_id=receipts.attempt_id "
            "WHERE attempts.task_id=? AND attempts.stage='fullText'",
            (result["taskId"],),
        ).fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM k10_publication_samples").fetchone() == (1,)


def test_b82_current_binding_rejects_unprovable_prior_body_without_second_post(tmp_path, monkeypatch):
    """B82 keeps its own snapshot/binding and cannot overwrite a prior body wire."""
    result = _run_b81_fallback_finalization(
        tmp_path=tmp_path, monkeypatch=monkeypatch, break_understand="unproven",
        historical_contract=False,
    )
    assert result["second"].status == "failed"
    assert tuple(result["transport"].calls) == result["callsBeforeResume"]
    with sqlite3.connect(result["database"]) as connection:
        row = connection.execute(
            "SELECT coverage_json FROM k10_scans WHERE status='failed' ORDER BY created_at DESC LIMIT 1",
        ).fetchone()
        assert row is not None
        coverage = json.loads(row[0])
        assert coverage["executionState"] == "deep_read"
        assert coverage["delivery"]["outcome"] == "failed"
        assert [(gap["stage"], gap["reasonCode"]) for gap in coverage["delivery"]["gaps"]] == [
            ("understand", "provider_response_receipt_unverifiable"),
        ]
        assert connection.execute(
            "SELECT state,reason_code FROM k10_v2_article_admissions "
            "WHERE task_id=? ORDER BY document_id,revision",
            (result["taskId"],),
        ).fetchall() == [("completed", None)]
        assert connection.execute(
            "SELECT COUNT(*) FROM k10_external_attempts "
            "WHERE task_id=? AND stage='fullText' AND item_key LIKE ?",
            (result["taskId"], "understand:%"),
        ).fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM k10_publication_samples").fetchone() == (0,)


def test_b82_current_binding_reuses_completed_model_checkpoint_without_raw_receipt(tmp_path, monkeypatch):
    """A current exact model checkpoint is sufficient local recovery proof."""
    result = _run_b81_fallback_finalization(
        tmp_path=tmp_path, monkeypatch=monkeypatch, break_understand="model_checkpoint_only",
        historical_contract=False,
    )
    assert result["second"].status == "completed"
    assert tuple(result["transport"].calls) == result["callsBeforeResume"]
    with sqlite3.connect(result["database"]) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND item_kind='document' AND stage='model:understand' AND status='completed'",
            (result["taskId"],),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM k10_external_attempts "
            "WHERE task_id=? AND stage='fullText' AND item_key LIKE ?",
            (result["taskId"], "understand:%"),
        ).fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM k10_publication_samples").fetchone() == (1,)
