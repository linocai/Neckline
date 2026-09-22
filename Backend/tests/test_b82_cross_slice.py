"""B82 R3: research execution identity follows retained source units."""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, time, timedelta
from io import StringIO
import json
from pathlib import Path
import socket
import sqlite3
from typing import Any, Literal

import httpx
import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import main as cli_main
from neckline.k10.discovery import DiscoverySliceYield
from neckline.k10.investigation import InvestigationError
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.research_contracts import ResearchSnapshot
from neckline.k10.sources import SourceDocumentInput, SourceFetchResult
from neckline.k10.windows import SHANGHAI, evening_cutoff
from neckline.k10.worker import run_once
from tests import v340_acceptance_fixture as base
from tests.test_v350_cli_api import DirectRoundTransport, explicit_bindings


_EVENT_KEY = "shared-execution-unit"
_TITLE_COUNT = 2


class _ExecutionUnitNews:
    """Two fresh source versions per scan, retaining one public event lifecycle."""

    coverage = base._FullScaleNews.coverage

    def __init__(self, *, token: str, request_bound: int) -> None:
        assert token == "fixture-token"
        assert request_bound >= 1

    def fetch_incremental(self, request) -> SourceFetchResult:
        published = request.window.start_at + timedelta(minutes=1)
        fetched = published + timedelta(minutes=1)
        run_marker = request.window.cutoff_at.strftime("%Y%m%d")
        documents = tuple(
            SourceDocumentInput(
                external_id=f"execution-unit-{run_marker}-{index}",
                canonical_url=f"https://fixture.invalid/execution-unit/{run_marker}/{index}",
                original_text=(
                    f"离线执行单元正文 {run_marker}-{index}："
                    "同一事项的公告与否认必须保留各自来源。"
                ),
                excerpt=None,
                published_at=published,
                published_precision="exact",
                fetched_at=fetched,
                fetch_version="b82-execution-unit",
                metadata={"title": f"执行单元标题 {index:04d}：同一事项的独立来源"},
            )
            for index in range(_TITLE_COUNT)
        )
        return SourceFetchResult(
            documents=documents,
            next_cursor=f"execution-unit-{run_marker}-final",
            success_watermark=request.window.cutoff_at,
            pages_fetched=1,
            pages_expected=1,
            exhausted=True,
        )


class _ExecutionUnitTransport(DirectRoundTransport):
    """Produce announcement and denial as independent research inputs."""

    def respond(self, request: httpx.Request) -> httpx.Response:
        payload = self._packet(request)
        action = payload.get("action")
        if action == "research_round":
            packet = payload["evidencePacket"]
            event = packet["event"]
            state = event["eventState"]
            if state not in {"announcement", "denial"}:
                raise AssertionError(f"unexpected execution-unit state: {state!r}")
            self._record("research:research_round", f"{_EVENT_KEY}:{state}")
            reference = self._ref(payload)
            company = self.company_codes[0 if state == "announcement" else 1]
            return self._ok({
                "action": action,
                "conclusion": {
                    "researchStatus": "ready_for_comparison",
                    "eventDisposition": "可比较",
                    "companyMappings": [{
                        "companyCode": company,
                        "affectedStage": "执行单元来源核验",
                        "relationEvidence": [reference],
                        "inference": {"relation": f"{state} 的独立来源关联"},
                        "uncertainty": "离线验证不替代真实公司披露",
                    }],
                    "companyDispositions": [],
                    "materialGaps": ["独立来源仍待生产环境复核"],
                    "stopReason": "该来源已完成必要比较",
                    "resumeCondition": "新的公司公告",
                },
                "comparison": {
                    "summary": f"{state} 来源的独立事件比较。",
                    "evidenceRefs": [reference],
                    "historicalAssessments": [],
                },
                "companyAssessments": [{
                    "companyCode": company,
                    "role": "primary",
                    "rank": 1,
                    "summary": f"{state} 来源对应的公司比较",
                    "priorityReason": "独立来源需要保留",
                    "gap": "尚未取得额外公司披露",
                    "rankChangeConditions": "公司公开确认",
                    "twoDayReason": "新事件固定窗口",
                    "evidenceDisclosure": {
                        "verificationStatus": "unverified",
                        "isRumor": True,
                        "originStatus": "unknown",
                        "originEvidenceRef": None,
                        "unverifiedReasons": ["离线来源不构成正式确认"],
                        "conditionalAnalysis": "等待公司公开确认后复核。",
                    },
                }],
            })
        if (isinstance(payload.get("documentId"), str)
                and isinstance(payload.get("revision"), int)):
            document_id = payload["documentId"]
            number = self.document_numbers.get(document_id)
            if number not in {0, 1}:
                raise AssertionError(f"understand request lacks frozen title identity: {document_id!r}")
            state = "announcement" if number == 0 else "denial"
            reference = {"documentId": document_id, "revision": payload["revision"]}
            self._record("understand", f"{_EVENT_KEY}:{state}")
            return self._ok({
                "events": [{
                    "canonicalKey": _EVENT_KEY,
                    "stageKey": "reported",
                    "eventState": state,
                    "headline": f"同一事项的{state}来源",
                    "eventKind": "disclosure",
                    "facts": {"sourceState": state},
                    "sourceRefs": [reference],
                    "claims": [{
                        "text": f"{state} 来源披露同一事项的独立事实。",
                        "kind": "factual_assertion",
                        "novelty": "new_fact",
                        "speaker": "来源",
                        "subject": "项目",
                        "object": "事项",
                        "action": "披露",
                        "stageOrCondition": state,
                        "timeText": "本次来源",
                        "verificationStatus": "unverified",
                        "decisionImpact": "影响该来源的公司关联比较",
                        "sourceRef": reference,
                        "location": "paragraph:1",
                    }],
                }],
                "needsFullText": False,
            })
        return super().respond(request)


def _install_unit_fixture(monkeypatch: pytest.MonkeyPatch, *, database: Path,
                          business_clock: list[datetime]) -> _ExecutionUnitTransport:
    monkeypatch.setattr(base, "TITLE_COUNT", _TITLE_COUNT)
    monkeypatch.setattr(base, "DeterministicTransport", _ExecutionUnitTransport)
    transport, _ = base.install_offline_transports(
        monkeypatch,
        refusal_event=None,
        selected_event_count=_TITLE_COUNT,
        fixture_run_at=business_clock[0],
    )
    monkeypatch.setattr(pipeline, "TuShareMajorNewsAdapter", _ExecutionUnitNews)
    monkeypatch.setattr(pipeline, "_now", lambda: business_clock[0])
    monkeypatch.setattr(socket.socket, "connect", base._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", base._deny_network)
    provider = MeteredProvider(
        ledger_db=database,
        ledger_task="discovery",
        api_key="fixture",
        model="deepseek-v4-pro",
        name="fixture",
        api_url="https://fixture.invalid/v1/chat/completions",
        read_timeout=1,
        use_streaming=False,
    )
    monkeypatch.setattr(
        pipeline,
        "resolve_deepseek_v4_pro",
        lambda **_kwargs: ProviderResolution("configured", provider, "fixture", None),
    )
    assert isinstance(transport, _ExecutionUnitTransport)
    return transport


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


def _handler(*, parquet_dir: Path, business_clock: list[datetime]):
    def run(context):
        return pipeline.production_scan_handler(
            context,
            tushare_token="fixture-token",
            parquet_dir=parquet_dir,
            now=lambda: business_clock[0],
        )
    return run


def _run_worker(*, database: Path, task_id: str, worker_id: str, handler) -> object:
    task = run_once(
        db_path=database,
        task_id=task_id,
        worker_id=worker_id,
        lease_for=timedelta(minutes=5),
        handlers={"evening_scan": handler},
        clock=lambda: datetime.now(SHANGHAI),
        require_b76_contract=True,
    )
    assert task is not None
    return task


def _latest_snapshots(*, database: Path, task_id: str) -> list[dict[str, Any]]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT revision,snapshot_id,task_id,event_id,event_revision,research_status,execution_status "
            "FROM k10_research_snapshot_revisions snapshot "
            "WHERE task_id=? AND revision=(SELECT MAX(inner_snapshot.revision) "
            "FROM k10_research_snapshot_revisions inner_snapshot "
            "WHERE inner_snapshot.snapshot_id=snapshot.snapshot_id) "
            "ORDER BY snapshot_id",
            (task_id,),
        ).fetchall()
    return [
        {
            "revision": int(revision), "snapshotId": str(snapshot_id), "taskId": str(saved_task_id),
            "eventId": str(event_id), "eventRevision": int(event_revision),
            "researchStatus": str(research_status), "executionStatus": str(execution_status),
        }
        for revision, snapshot_id, saved_task_id, event_id, event_revision, research_status, execution_status in rows
    ]


def _scan_for_task(*, database: Path, task_id: str) -> tuple[str, str, dict[str, Any]]:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT scan.scan_id,scan.status,scan.coverage_json FROM k10_scans scan "
            "JOIN k10_scan_execution_bindings binding ON binding.scan_id=scan.scan_id "
            "WHERE binding.task_id=?",
            (task_id,),
        ).fetchone()
    assert row is not None
    return str(row[0]), str(row[1]), json.loads(row[2])


def _replace_running_snapshot_with_foreign(*, database: Path, task_id: str, foreign_snapshot_id: str) -> str:
    scan_id, status, coverage = _scan_for_task(database=database, task_id=task_id)
    assert status == "running"
    snapshot_ids = coverage.get("researchSnapshotIds")
    assert isinstance(snapshot_ids, list) and snapshot_ids
    local_snapshot_id = snapshot_ids[0]
    assert isinstance(local_snapshot_id, str) and local_snapshot_id != foreign_snapshot_id
    coverage["researchSnapshotIds"] = [
        foreign_snapshot_id if snapshot_id == local_snapshot_id else snapshot_id
        for snapshot_id in snapshot_ids
    ]
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE k10_scans SET coverage_json=? WHERE scan_id=? AND status='running'",
            (json.dumps(coverage, ensure_ascii=False, sort_keys=True, separators=(",", ":")), scan_id),
        )
    return local_snapshot_id


def test_b82_execution_unit_keeps_same_event_announcement_and_denial_independent(tmp_path, monkeypatch):
    """Same public event ID has two source-bound execution inputs and materials."""
    database = tmp_path / "execution-units.sqlite"
    day = base.DAY
    business_clock = [evening_cutoff(day) + timedelta(hours=1)]
    config_id, config_revision, execution_id, execution_revision = base.seed_database(
        database, trading_day=day, fixture_now=business_clock[0],
    )
    execution_revision, _ = _cross_serial_execution_binding(
        database=database, execution_id=execution_id, execution_revision=execution_revision,
        configuration_id=config_id, configuration_revision=config_revision, created_at=business_clock[0],
    )
    transport = _install_unit_fixture(monkeypatch, database=database, business_clock=business_clock)
    task_id = _enqueue_evening(
        database=database, day=day, config_id=config_id, config_revision=config_revision,
        execution_id=execution_id, execution_revision=execution_revision,
    )
    task = _run_worker(
        database=database, task_id=task_id, worker_id="b82-unit-success",
        handler=_handler(parquet_dir=tmp_path / "parquet", business_clock=business_clock),
    )
    assert task.status == "completed"
    assert {event for name, event in transport.calls if name == "research:research_round"} == {
        f"{_EVENT_KEY}:announcement", f"{_EVENT_KEY}:denial",
    }
    snapshots = _latest_snapshots(database=database, task_id=task_id)
    assert len(snapshots) == 2
    assert {item["eventId"] for item in snapshots} == {pipeline._event_id(_EVENT_KEY)}
    assert len({item["snapshotId"] for item in snapshots}) == 2
    assert {item["executionStatus"] for item in snapshots} == {"ok"}

    with base.actual_api(database, **explicit_bindings(database)) as client:
        latest = client.get("/api/v1/k10/v2/reports/latest?window=evening")
        latest.raise_for_status()
        report = latest.json()["report"]
        materials = client.get(f"/api/v1/k10/v2/reports/{report['reportId']}/materials")
        materials.raise_for_status()
    assert report["status"] == "completed" and report["availableAt"]
    assert report["delivery"]["outcome"] == "complete"
    assert report["delivery"]["counts"]["eventInput"] == 2
    assert report["delivery"]["counts"]["eventProcessed"] == 2
    assert report["eveningCards"]
    material_rows = materials.json()["items"]
    assert len(material_rows) == 2
    assert len({row["materialId"] for row in material_rows}) == 2
    assert {row["eventId"] for row in material_rows} == {pipeline._event_id(_EVENT_KEY)}
    assert len({(row["sourceRefs"][0]["documentId"], row["sourceRefs"][0]["revision"])
                for row in material_rows}) == 2


def test_b82_execution_unit_rejects_foreign_task_snapshot_for_same_public_event(tmp_path, monkeypatch):
    """A prior task's matching public event never satisfies this task's receipt binding."""
    database = tmp_path / "execution-units-foreign.sqlite"
    first_day = base.DAY
    second_day = first_day + timedelta(days=1)
    business_clock = [evening_cutoff(first_day) + timedelta(hours=1)]
    config_id, config_revision, execution_id, execution_revision = base.seed_database(
        database, trading_day=first_day, fixture_now=business_clock[0],
    )
    execution_revision, _ = _cross_serial_execution_binding(
        database=database, execution_id=execution_id, execution_revision=execution_revision,
        configuration_id=config_id, configuration_revision=config_revision, created_at=business_clock[0],
    )
    _install_unit_fixture(monkeypatch, database=database, business_clock=business_clock)
    first_task_id = _enqueue_evening(
        database=database, day=first_day, config_id=config_id, config_revision=config_revision,
        execution_id=execution_id, execution_revision=execution_revision,
    )
    first = _run_worker(
        database=database, task_id=first_task_id, worker_id="b82-unit-first-task",
        handler=_handler(parquet_dir=tmp_path / "first-parquet", business_clock=business_clock),
    )
    assert first.status == "completed"
    foreign_snapshot = _latest_snapshots(database=database, task_id=first_task_id)[0]

    business_clock[0] = evening_cutoff(second_day) + timedelta(hours=1)
    second_task_id = _enqueue_evening(
        database=database, day=second_day, config_id=config_id, config_revision=config_revision,
        execution_id=execution_id, execution_revision=execution_revision,
    )
    original_advance = pipeline._CheckpointedDiscoveryModel.advance_research_round
    yielded = False

    def yield_after_second_task_snapshot(self, *, snapshot, evidence_packet):
        nonlocal yielded
        result = original_advance(self, snapshot=snapshot, evidence_packet=evidence_packet)
        if self._task_id == second_task_id and not yielded:
            yielded = True
            raise DiscoverySliceYield()
        return result

    monkeypatch.setattr(
        pipeline._CheckpointedDiscoveryModel,
        "advance_research_round",
        yield_after_second_task_snapshot,
    )
    handler = _handler(parquet_dir=tmp_path / "second-parquet", business_clock=business_clock)
    paused = _run_worker(
        database=database, task_id=second_task_id, worker_id="b82-unit-second-paused", handler=handler,
    )
    assert paused.status == "queued" and yielded
    own_snapshot = _latest_snapshots(database=database, task_id=second_task_id)
    assert len(own_snapshot) == 1
    assert {item["eventId"] for item in own_snapshot} == {foreign_snapshot["eventId"]}
    replaced_snapshot_id = _replace_running_snapshot_with_foreign(
        database=database, task_id=second_task_id, foreign_snapshot_id=foreign_snapshot["snapshotId"],
    )
    assert replaced_snapshot_id in {item["snapshotId"] for item in own_snapshot}

    with sqlite3.connect(database) as connection:
        due = datetime.fromisoformat(connection.execute(
            "SELECT not_before_at FROM k10_task_retry_schedules WHERE task_id=?", (second_task_id,),
        ).fetchone()[0])
    rejected = run_once(
        db_path=database,
        task_id=second_task_id,
        worker_id="b82-unit-second-rejected",
        lease_for=timedelta(minutes=5),
        handlers={"evening_scan": handler},
        clock=lambda: due + timedelta(seconds=1),
        require_b76_contract=True,
    )
    assert rejected is not None and rejected.status == "failed"
    scan_id, status, coverage = _scan_for_task(database=database, task_id=second_task_id)
    assert status == "failed" and coverage["researchFailure"] == "research_snapshot_missing"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM k10_publication_samples WHERE batch_id=?", ("publication_" + scan_id,),
        ).fetchone() == (0,)
        report = connection.execute(
            "SELECT available_at FROM k10_v2_report_runs WHERE scan_id=?", (scan_id,)).fetchone()
    assert report is None or report[0] is None


# These three regressions remain the R3/R6 morning-closeout contract.  They
# intentionally keep the original 39-event fixture separate from the smaller
# execution-unit identity cases above.
_CROSS_SLICE_FIRST_EVENTS = 29
_CROSS_SLICE_TOTAL_EVENTS = 39


def _cross_latest_snapshots(*, database: Path, task_id: str) -> dict[str, dict[str, object]]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT event.stable_key,snapshot.snapshot_id,snapshot.event_id,snapshot.event_revision,"
            "snapshot.research_status,snapshot.execution_status "
            "FROM k10_research_snapshot_revisions snapshot "
            "JOIN (SELECT snapshot_id,max(revision) AS revision "
            "      FROM k10_research_snapshot_revisions WHERE task_id=? GROUP BY snapshot_id) latest "
            "  ON latest.snapshot_id=snapshot.snapshot_id AND latest.revision=snapshot.revision "
            "JOIN k10_events event ON event.event_id=snapshot.event_id "
            "WHERE snapshot.task_id=? ORDER BY event.stable_key",
            (task_id, task_id),
        ).fetchall()
    return {
        str(stable_key): {
            "snapshotId": str(snapshot_id), "eventId": str(event_id),
            "eventRevision": int(event_revision), "researchStatus": str(research_status),
            "executionStatus": str(execution_status),
        }
        for stable_key, snapshot_id, event_id, event_revision, research_status, execution_status in rows
    }


def _cross_serial_execution_binding(*, database: Path, execution_id: str, execution_revision: int,
                                    configuration_id: str, configuration_revision: int,
                                    created_at: datetime) -> tuple[int, dict[str, object]]:
    """Make fixture research admission serial so the 29th unit is deterministic."""
    payload = dict(store.read_execution_config(
        config_id=execution_id, revision=execution_revision, db_path=database,
    )["payload"])
    payload["discovery"] = {**payload["discovery"], "deepReadConcurrency": 1}
    revision = store.append_execution_config(
        config_id=execution_id, payload=payload, created_at=created_at.isoformat(), db_path=database,
    )
    execution = store.read_execution_config(config_id=execution_id, revision=revision, db_path=database)
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT content_json FROM k10_v2_strategy_snapshots WHERE snapshot_id=?", ("k10-v2-20260909",),
        ).fetchone()
        assert row is not None
        content = json.loads(row[0])
        content.update({
            "configId": configuration_id, "configRevision": configuration_revision,
            "executionConfigId": execution_id, "executionConfigRevision": revision,
            "executionSha256": execution["contentSha256"],
        })
        connection.execute(
            "UPDATE k10_v2_strategy_snapshots "
            "SET execution_config_id=?,execution_config_revision=?,content_json=? WHERE snapshot_id=?",
            (execution_id, revision,
             json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
             "k10-v2-20260909"),
        )
    return revision, execution


def _corrupt_cross_snapshot_binding(*, database: Path, snapshots: dict[str, dict[str, object]],
                                    mode: Literal["missing", "wrong_bound", "facts_tampered"]) -> None:
    """Make a narrow isolated negative control after production created snapshots."""
    first = snapshots["event-000"]
    if mode == "missing":
        with sqlite3.connect(database) as connection:
            row = connection.execute("SELECT coverage_json FROM k10_scans WHERE status='running'").fetchone()
            assert row is not None
            coverage = json.loads(row[0])
            ids = list(coverage["researchSnapshotIds"])
            ids.append("research_missing_b82_control")
            coverage["researchSnapshotIds"] = ids
            connection.execute(
                "UPDATE k10_scans SET coverage_json=? WHERE status='running'",
                (json.dumps(coverage, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
            )
        return

    if mode == "facts_tampered":
        # Keep every public/snapshot identity field equal.  Only the persisted
        # event facts change after admission, so finalization must prove the
        # snapshot's context digest still binds the exact frozen facts.
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT facts_json FROM k10_event_revisions WHERE event_id=? AND revision=?",
                (first["eventId"], first["eventRevision"]),
            ).fetchone()
            assert row is not None
            facts = json.loads(row[0])
            facts["b82TamperedAfterAdmission"] = True
            connection.execute(
                "UPDATE k10_event_revisions SET facts_json=? WHERE event_id=? AND revision=?",
                (json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                 first["eventId"], first["eventRevision"]),
            )
        return

    # The foreign event revision is structurally valid.  Only its immutable
    # binding differs, which proves finalization compares exact event identity
    # rather than a snapshot count.
    with sqlite3.connect(database) as connection:
        source = connection.execute(
            "SELECT revision,headline,event_kind,facts_json,source_refs_json,created_at "
            "FROM k10_event_revisions WHERE event_id=? AND revision=?",
            (first["eventId"], first["eventRevision"]),
        ).fetchone()
    assert source is not None
    wrong = store.append_event_revision(
        event_id="event_b82_cross_wrong_binding", stable_key="event-b82-cross-wrong-binding",
        headline=str(source[1]), event_kind=str(source[2]), facts=json.loads(source[3]),
        source_refs=json.loads(source[4]), supersedes_revision=None,
        created_at=str(source[5]), db_path=database,
    )
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT revision,snapshot_json FROM k10_research_snapshot_revisions WHERE snapshot_id=?",
            (first["snapshotId"],),
        ).fetchall()
        assert rows
        for revision, raw in rows:
            snapshot = ResearchSnapshot.from_dict(json.loads(raw))
            wrong_snapshot = replace(snapshot, event_id=wrong.event_id, event_revision=wrong.revision)
            connection.execute(
                "UPDATE k10_research_snapshot_revisions SET event_id=?,event_revision=?,snapshot_json=? "
                "WHERE snapshot_id=? AND revision=?",
                (wrong.event_id, wrong.revision,
                 json.dumps(wrong_snapshot.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                 first["snapshotId"], revision),
            )


def _run_cross_slice_morning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                             corruption: Literal["missing", "wrong_bound", "facts_tampered"] | None = None) -> dict[str, object]:
    day = base.DAY + timedelta(days=1)
    cutoff = datetime.combine(day, time(8, 30), tzinfo=SHANGHAI)
    deadline = cutoff.replace(hour=9, minute=20)
    database = tmp_path / f"cross-slice-{corruption or 'valid'}.sqlite"
    config_id, config_revision, execution_id, execution_revision = base.seed_database(
        database, trading_day=day - timedelta(days=1), fixture_now=cutoff - timedelta(hours=1),
    )
    execution_revision, execution = _cross_serial_execution_binding(
        database=database, execution_id=execution_id, execution_revision=execution_revision,
        configuration_id=config_id, configuration_revision=config_revision,
        created_at=cutoff - timedelta(minutes=2),
    )
    configuration = store.read_run_config(config_id=config_id, revision=config_revision, db_path=database)["payload"]
    finalization_at = deadline - pipeline._morning_finalization_reserve(
        configuration=configuration, execution_profile=execution,
    )
    research_closeout_at = finalization_at - pipeline._morning_finalization_reserve(
        configuration=configuration, execution_profile=execution,
    )
    business_clock = [research_closeout_at - timedelta(seconds=1)]

    monkeypatch.setattr(base, "TITLE_COUNT", _CROSS_SLICE_TOTAL_EVENTS)
    monkeypatch.setattr(base, "DeterministicTransport", DirectRoundTransport)
    transport, _ = base.install_offline_transports(
        monkeypatch, refusal_event=None, selected_event_count=_CROSS_SLICE_TOTAL_EVENTS,
        fixture_run_at=business_clock[0],
    )
    monkeypatch.setattr(pipeline, "_now", lambda: business_clock[0])
    monkeypatch.setattr(socket.socket, "connect", base._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", base._deny_network)
    provider = MeteredProvider(
        ledger_db=database, ledger_task="discovery", api_key="fixture", model="deepseek-v4-pro",
        name="fixture", api_url="https://fixture.invalid/v1/chat/completions", read_timeout=1,
        use_streaming=False,
    )
    monkeypatch.setattr(
        pipeline, "resolve_deepseek_v4_pro",
        lambda **_kwargs: ProviderResolution("configured", provider, "fixture", None),
    )
    original_advance = pipeline._CheckpointedDiscoveryModel.advance_research_round
    controls = {"failed": False, "yielded": False}

    def settle_twenty_nine_then_yield(self, *, snapshot, evidence_packet):
        result = original_advance(self, snapshot=snapshot, evidence_packet=evidence_packet)
        event = evidence_packet.get("event") if isinstance(evidence_packet, dict) else None
        key = event.get("canonicalKey") if isinstance(event, dict) else None
        if key == "event-001" and not controls["failed"]:
            controls["failed"] = True
            raise InvestigationError("fixture event-local failure", code="fixture_event_failure")
        if key == "event-028" and not controls["yielded"]:
            controls["yielded"] = True
            raise DiscoverySliceYield()
        return result

    monkeypatch.setattr(
        pipeline._CheckpointedDiscoveryModel, "advance_research_round", settle_twenty_nine_then_yield,
    )
    output = StringIO()
    with redirect_stdout(output):
        assert cli_main([
            "enqueue", "--db", str(database), "--kind", "morning", "--trading-day", day.isoformat(),
            "--config-id", config_id, "--config-revision", str(config_revision),
            "--execution-config-id", execution_id, "--execution-config-revision", str(execution_revision),
        ]) == 0
    task_id = output.getvalue().strip()
    assert task_id.startswith("task_")

    def handler(context):
        return pipeline.production_scan_handler(
            replace(context, clock=lambda: business_clock[0]), tushare_token="fixture-token",
            parquet_dir=tmp_path / "cross-slice-parquet", now=lambda: business_clock[0],
        )

    first = run_once(
        db_path=database, task_id=task_id, worker_id="b82-cross-slice-first", lease_for=timedelta(minutes=5),
        handlers={"morning_scan": handler}, clock=lambda: datetime.now(SHANGHAI), require_b76_contract=True,
    )
    assert first is not None and first.status == "queued"
    assert controls == {"failed": True, "yielded": True}
    snapshots = _cross_latest_snapshots(database=database, task_id=task_id)
    assert set(snapshots) == {f"event-{index:03d}" for index in range(_CROSS_SLICE_FIRST_EVENTS)}
    assert snapshots["event-000"]["executionStatus"] == "ok"
    assert snapshots["event-001"]["executionStatus"] == "failed"
    assert snapshots["event-028"]["executionStatus"] == "ok"
    assert snapshots["event-028"]["researchStatus"] == "continue_research"
    research_calls_before_resume = sum(name == "research:research_round" for name, _ in transport.calls)

    if corruption is not None:
        _corrupt_cross_snapshot_binding(database=database, snapshots=snapshots, mode=corruption)
    business_clock[0] = research_closeout_at
    with sqlite3.connect(database) as connection:
        due = datetime.fromisoformat(connection.execute(
            "SELECT not_before_at FROM k10_task_retry_schedules WHERE task_id=?", (task_id,),
        ).fetchone()[0])
    second = run_once(
        db_path=database, task_id=task_id, worker_id="b82-cross-slice-second", lease_for=timedelta(minutes=5),
        handlers={"morning_scan": handler}, clock=lambda: due + timedelta(seconds=1), require_b76_contract=True,
    )
    assert second is not None
    return {
        "database": database, "taskId": task_id, "first": first, "second": second,
        "transport": transport, "researchCallsBeforeResume": research_calls_before_resume,
    }


def test_b82_cross_slice_restores_all_admitted_snapshot_identities_before_morning_closeout(tmp_path, monkeypatch):
    result = _run_cross_slice_morning(tmp_path, monkeypatch)
    assert result["second"].status == "completed"
    transport = result["transport"]
    assert sum(name == "research:research_round" for name, _ in transport.calls) == result["researchCallsBeforeResume"]
    database, task_id = result["database"], result["taskId"]
    snapshots = _cross_latest_snapshots(database=database, task_id=task_id)
    assert set(snapshots) == {f"event-{index:03d}" for index in range(_CROSS_SLICE_FIRST_EVENTS)}
    assert snapshots["event-001"]["executionStatus"] == "failed"
    assert snapshots["event-028"]["executionStatus"] == "ok"
    with sqlite3.connect(database) as connection:
        event_failures = connection.execute(
            "SELECT item_key,status,safe_error_code FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND item_kind='event' ORDER BY item_key", (task_id,),
        ).fetchall()
        assert [row[0] for row in event_failures if row[2] == "morning_closeout_reserve"] == [
            f"event-{index:03d}" for index in range(_CROSS_SLICE_FIRST_EVENTS, _CROSS_SLICE_TOTAL_EVENTS)
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM k10_external_attempts WHERE state IN ('started','running','unknown')",
        ).fetchone() == (0,)
    with base.actual_api(database, **explicit_bindings(database)) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=morning")
        response.raise_for_status()
        report = response.json()["report"]
    assert report["status"] == "partial" and report["availableAt"]
    assert report["addedCards"]
    assert {gap["reasonCode"] for gap in report["delivery"]["gaps"]} >= {"morning_closeout_reserve"}
    assert sum(gap["reasonCode"] == "morning_closeout_reserve" for gap in report["delivery"]["gaps"]) == 10


@pytest.mark.parametrize("corruption", ("missing", "wrong_bound", "facts_tampered"))
def test_b82_cross_slice_missing_or_wrong_snapshot_cannot_publish(tmp_path, monkeypatch, corruption):
    result = _run_cross_slice_morning(tmp_path, monkeypatch, corruption=corruption)
    assert result["second"].status == "failed"
    database, task_id = result["database"], result["taskId"]
    with sqlite3.connect(database) as connection:
        scan = connection.execute("SELECT coverage_json FROM k10_scans WHERE status='failed'").fetchone()
        assert scan is not None and json.loads(scan[0])["researchFailure"] == "research_snapshot_missing"
        assert connection.execute("SELECT COUNT(*) FROM k10_publication_samples").fetchone() == (0,)
        available = connection.execute(
            "SELECT available_at FROM k10_v2_report_runs WHERE scan_id=(SELECT scan_id FROM k10_scans WHERE status='failed')",
        ).fetchone()
        assert available is None or available[0] is None
        assert connection.execute(
            "SELECT COUNT(*) FROM k10_external_attempts WHERE task_id=? AND state IN ('started','running','unknown')",
            (task_id,),
        ).fetchone() == (0,)
