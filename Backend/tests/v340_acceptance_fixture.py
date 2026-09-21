"""Deterministic, full-scale B76 acceptance harness.

This is intentionally a test-only provider/source boundary.  It drives the
same public CLI, worker, production handlers, metered provider and FastAPI
router that a release uses, while ``httpx.MockTransport`` and a socket gate
make an external request impossible.  Its synthetic replies prove software
contracts and scale only; they are never evidence of a live model's quality,
cost, or delivery reliability.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from io import StringIO
import json
from pathlib import Path
import socket
import sqlite3
from threading import Lock
from typing import Any, Mapping

import httpx
from fastapi.testclient import TestClient

from neckline.db import init_schema as initialize_common_schema
from neckline.k10 import pipeline, store
from neckline.k10.cli import main as cli_main
from neckline.k10 import metering
from neckline.k10.metering import MeteredProvider
from neckline.k10.notifications import initialize_notifications_schema
from neckline.k10.providers import ProviderResolution
from neckline.k10.schema import initialize_schema
from neckline.k10.sources import SourceCoverage, SourceDocumentInput, SourceFetchResult
from neckline.k10.universe import CompanyMetadata
from neckline.k10.verification import TavilyEvidenceGateway
from neckline.k10.windows import SHANGHAI, evening_cutoff
from neckline.k10.worker import run_once
from neckline.k10.v2_profiles import PROFILES_ID, UNIVERSE_ID, import_profiles
from neckline.k10.v2_store import bind_strategy
from neckline.search.tavily import TavilySearchClient


DAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=SHANGHAI)
RUN_AT = datetime(2026, 9, 8, 22, 0, tzinfo=SHANGHAI)
TITLE_COUNT = 1602
SELECTED_EVENT_COUNT = 84
TITLE_BATCH_SIZE = 64
_REAL_HTTPX_CLIENT = httpx.Client


@dataclass(frozen=True)
class FlowResult:
    db_path: Path
    task_id: str
    scan_id: str
    task_status: str
    task_stage: str | None
    calls: Mapping[str, int]
    transport_calls: tuple[tuple[str, str | None], ...]
    gateway_calls: tuple[str, ...]
    gateway_trace: tuple[str, ...]
    company_codes: tuple[str, ...]
    worker_passes: int
    continuation_count: int


def release_root() -> Path:
    return Path("/Users/linotsai/Lino/releases/Neckline/v3.4.0-b76-20260916")


def fixture_company_codes() -> tuple[str, ...]:
    """Use the actual immutable K10 pool, never invented membership."""
    source = Path("/Users/linotsai/Lino/whynotme/research/K10-v2初始股票池_20260909.json")
    payload = json.loads(source.read_text(encoding="utf-8"))
    codes = tuple(str(row["ts_code"]) for row in payload["stocks"])
    assert len(codes) == 1089
    assert len(set(codes)) == 1089
    return codes


def _event_number(value: object) -> int:
    if not isinstance(value, str) or not value.startswith("event-"):
        raise AssertionError(f"synthetic event identity missing: {value!r}")
    return int(value.rsplit("-", 1)[1])


class _FullScaleNews:
    coverage = SourceCoverage(
        "tushare-major-news", "market-wide", "deterministic-offline", "bounded",
        "publishedAt", "publishedAt", True,
    )

    def __init__(self, *, token: str, request_bound: int):
        assert token == "fixture-token"
        assert request_bound >= 1

    def fetch_incremental(self, request) -> SourceFetchResult:
        published = request.window.start_at + timedelta(minutes=1)
        fetched = published + timedelta(minutes=1)
        documents = tuple(
            SourceDocumentInput(
                external_id=f"acceptance-{index:04d}",
                canonical_url=f"https://fixture.invalid/acceptance/{index:04d}",
                original_text=f"离线验收正文 {index:04d}：公司事件需要基于公开资料核验。",
                excerpt=None,
                published_at=published,
                published_precision="exact",
                fetched_at=fetched,
                fetch_version="v340-acceptance",
                metadata={"title": f"离线验收标题 {index:04d}：新增经营事件"},
            )
            for index in range(TITLE_COUNT)
        )
        return SourceFetchResult(
            documents=documents, next_cursor="fixture-final", success_watermark=request.window.cutoff_at,
            pages_fetched=1, pages_expected=1, exhausted=True,
        )


class _Metadata:
    def lookup(self, *, company_code, as_of):
        return CompanyMetadata(company_code, "chinext", False, "801080.SI", as_of)


@dataclass
class _TavilyWire:
    """A network-denied wire transport for the real Tavily client and ledger."""

    queries: list[str] = field(default_factory=list)
    outcomes: list[Mapping[str, Any]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)

    def respond(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != "api.tavily.com" or request.url.path != "/search":
            raise AssertionError(f"unexpected offline Tavily request: {request.url!s}")
        payload = json.loads(request.content)
        query = payload.get("query")
        if not isinstance(query, str) or not query.startswith("离线验收 event-"):
            raise AssertionError(f"unexpected Tavily query: {query!r}")
        with self._lock:
            self.queries.append(query)
        number = _event_number(query.split(" ", 2)[1])
        return httpx.Response(200, json={
            "request_id": f"fixture-tavily-{number:03d}", "usage": {"credits": 1},
            "results": [{
                "url": f"https://evidence.fixture.invalid/{number:03d}",
                "title": f"离线独立来源 {number:03d}",
                "content": f"离线独立来源确认 event-{number:03d} 仍需正式公告核实。",
                "published_date": "2026-09-07T12:00:00+00:00",
            }],
        })


@dataclass
class DeterministicTransport:
    """Exact synthetic OpenAI-compatible transport used by full CLI flows."""

    company_codes: tuple[str, ...]
    refusal_event: int | None = None
    selected_event_count: int = SELECTED_EVENT_COUNT
    all_events_same_company: bool = False
    refusal_operation: str | None = None
    calls: list[tuple[str, str | None]] = field(default_factory=list)
    document_numbers: dict[str, int] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def _record(self, kind: str, event: str | None = None) -> None:
        with self._lock:
            self.calls.append((kind, event))

    @staticmethod
    def _number_from_title(title: object) -> int:
        if not isinstance(title, str):
            raise AssertionError("title fixture lost its title")
        return int(title.split("标题 ", 1)[1].split("：", 1)[0])

    def _title_number(self, item: Mapping[str, Any]) -> int:
        title = item.get("title")
        document_id = item.get("documentId")
        if not isinstance(document_id, str):
            raise AssertionError("title fixture lost its sealed document identity")
        number = self._number_from_title(title)
        with self._lock:
            self.document_numbers[document_id] = number
        return number

    def _company_for_event(self, event: object) -> str:
        number = _event_number(event)
        # event 0 and 1 deliberately share A; event 2 is independent B.  A
        # content refusal at event 1 must remove A before global ranking while
        # preserving B and every unrelated completed company.
        if self.all_events_same_company or number in {0, 1}:
            return self.company_codes[0]
        return self.company_codes[number - 1]

    @staticmethod
    def _packet(request: httpx.Request) -> Mapping[str, Any]:
        wire = json.loads(request.content)
        message = wire["messages"][-1]["content"]
        return json.loads(message.split("<untrusted-k10-evidence>\n", 1)[1].split("\n</untrusted-k10-evidence>", 1)[0])

    @staticmethod
    def _ok(result: Mapping[str, Any]) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
                                "finish_reason": "stop"}],
                  "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6}},
        )

    @staticmethod
    def _ref(packet: Mapping[str, Any]) -> Mapping[str, Any]:
        refs = packet["evidencePacket"]["allowedEvidenceRefs"]
        assert refs
        return refs[0]

    def respond(self, request: httpx.Request) -> httpx.Response:
        payload = self._packet(request)
        action = payload.get("action")
        packet = payload.get("evidencePacket") if isinstance(payload.get("evidencePacket"), Mapping) else {}
        event = packet.get("event") if isinstance(packet.get("event"), Mapping) else {}
        canonical_key = event.get("canonicalKey")
        if isinstance(action, str):
            assert action == "research_round", f"retired research action {action}"
            self._record("research:" + action, canonical_key if isinstance(canonical_key, str) else None)
            ref = self._ref(payload)
            company = self._company_for_event(canonical_key)
            if not packet.get("queryPaths"):
                claim_id = packet["claims"][0]["claimId"]
                return self._ok({
                    "action": action,
                    "conclusion": {"researchStatus": "continue_research", "eventDisposition": "待补查",
                        "companyMappings": [], "companyDispositions": [], "materialGaps": ["公司确认"],
                        "stopReason": "仍有必要事件问题", "resumeCondition": "补查资料返回"},
                    "questions": [{
                        "questionId": "q-1", "claimIds": [claim_id], "companyCodes": [company],
                        "question": "公开资料是否确认本次事件", "knownEvidence": [ref],
                        "missingEvidence": ["公司确认"], "supportCondition": "公司公告确认",
                        "refuteCondition": "公司公告否认", "decisionImpact": "影响事件比较",
                        "state": "open", "resumeCondition": "出现公司披露",
                    }],
                    "queryPaths": [{
                        "pathId": "path-1", "questionId": "q-1", "query": f"离线验收 {canonical_key} 公司公告",
                        "intent": "确认事件", "targetSource": "公司公告", "newPathReason": "尚未查询",
                        "expectedInformationGain": "确认主体", "expectedJudgmentChange": "影响比较",
                        "purposeKind": "event_fact", "targetRefs": [{"kind": "claim", "claimId": claim_id}],
                        "state": "planned", "resultSummary": None,
                    }],
                })
            if self.refusal_event is not None and _event_number(canonical_key) == self.refusal_event:
                return httpx.Response(400, json={"error": {
                    "code": "invalid_request_error", "message": "Content Exists Risk",
                }})
            return self._ok({
                "action": action,
                "conclusion": {
                    "researchStatus": "ready_for_comparison", "eventDisposition": "可比较",
                    "companyMappings": [{"companyCode": company, "affectedStage": "离线验收阶段",
                        "relationEvidence": [ref], "inference": {}, "uncertainty": "独立资料暂无新增"}],
                    "companyDispositions": [], "materialGaps": ["独立资料暂无新增"],
                    "stopReason": "必要补查已完成", "resumeCondition": "新的公司公告",
                },
                "comparison": {"summary": "离线验收的事件比较。", "evidenceRefs": [ref], "historicalAssessments": []},
                "companyAssessments": [{"companyCode": company, "role": "primary", "rank": 1,
                    "summary": "独立完成的公司比较", "priorityReason": "事件关联已完成",
                    "gap": "独立资料暂无新增", "rankChangeConditions": "后续公司公告",
                    "twoDayReason": "新事件窗口", "evidenceDisclosure": {
                        "verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown",
                        "originEvidenceRef": None, "unverifiedReasons": ["独立资料无新增"],
                        "conditionalAnalysis": "等待公司公开确认后复核。",
                    }}],
            })

        assert payload.get("operation") != "titleSelectionReview", "retired third title pass"

        if "inputCount" in payload:
            self._record("titleGlobal")
            return self._ok({
                "selectionComplete": True, "reviewedCount": len(payload["items"]),
                "selected": [{"i": row["i"], "selectedRank": self._number_from_title(row["title"]) + 1, "reason": "新增事件值得核验"}
                             for row in payload["items"] if self._number_from_title(row["title"]) < self.selected_event_count],
                "merged": [],
            })
        if "items" in payload:
            self._record("titleBatch")
            return self._ok({"items": [
                {"i": index, "status": "candidate", "matterKey": f"matter-{self._title_number(row)}", 
                 "stageKey": "new", "reason": "标题含新事件"}
                for index, row in enumerate(payload["items"])
            ]})
        if isinstance(payload.get("output"), Mapping) and "kind" in payload["output"]:
            self._record("classify", canonical_key if isinstance(canonical_key, str) else None)
            return self._ok({"kind": "initial", "relatedOpportunityId": None, "reason": "本次首次出现",
                             "newFacts": "本次离线来源出现新事件", "changedJudgment": None,
                             "twoDayReason": "固定窗口内可复核"})
        if "candidates" in payload and isinstance(payload.get("output"), Mapping) and "choices" in payload["output"]:
            self._record("prioritize")
            if self.refusal_operation == "prioritize":
                return httpx.Response(400, json={"error": {
                    "code": "invalid_request_error", "message": "Content Exists Risk",
                }})
            return self._ok({"choices": [{"canonicalKey": row["canonicalKey"], "companyCode": row["companyCode"]}
                                           for row in payload["candidates"]]})

        document_id = payload.get("documentId")
        if not isinstance(document_id, str):
            raise AssertionError(f"unexpected non-title model payload: {sorted(payload)}")
        with self._lock:
            number = self.document_numbers.get(document_id)
        if number is None:
            raise AssertionError(f"understand received a document never seen by title triage: {document_id}")
        assert 0 <= number < self.selected_event_count
        self._record("understand", f"event-{number:03d}")
        ref = {"documentId": document_id, "revision": payload["revision"]}
        return self._ok({
            "events": [{
                "canonicalKey": f"event-{number:03d}", "stageKey": "initial", "eventState": "rumor",
                "headline": f"离线验收事件 {number:03d}", "eventKind": "rumor", "facts": {}, "sourceRefs": [ref],
                "claims": [{
                    "claimId": f"claim-{number:03d}", "text": "供应商称事件仍待公司确认", "kind": "rumor",
                    "novelty": "new_fact", "speaker": "供应商", "subject": "项目", "object": "样品",
                    "action": "送样", "stageOrCondition": "待确认", "timeText": "本次来源",
                    "verificationStatus": "unverified", "decisionImpact": "影响比较", "sourceRef": ref,
                    "location": "paragraph:1",
                }],
            }], "needsFullText": False,
        })


def _deny_network(*_args, **_kwargs):
    raise AssertionError("B76 acceptance attempted an external socket connection")


def install_offline_transports(monkeypatch, *, refusal_event: int | None,
                               selected_event_count: int = SELECTED_EVENT_COUNT,
                               all_events_same_company: bool = False,
                               refusal_operation: str | None = None,
                               fixture_run_at: datetime = RUN_AT) -> tuple[DeterministicTransport, _TavilyWire]:
    """Install deterministic provider/source transports and an explicit socket kill-switch."""
    if isinstance(selected_event_count, bool) or not isinstance(selected_event_count, int) or not 0 <= selected_event_count <= TITLE_COUNT:
        raise ValueError("selected_event_count must be between zero and the frozen title count")
    transport = DeterministicTransport(
        fixture_company_codes(), refusal_event=refusal_event, selected_event_count=selected_event_count,
        all_events_same_company=all_events_same_company, refusal_operation=refusal_operation,
    )
    tavily_wire = _TavilyWire()
    tavily_client = TavilySearchClient("fixture-tavily-key", transport=httpx.MockTransport(tavily_wire.respond))
    monkeypatch.setattr(socket, "create_connection", _deny_network)
    def offline_http_client(**kwargs):
        # The model transport is injected globally because MeteredProvider does
        # not expose a client. The real Tavily client has its own explicit
        # MockTransport, which must retain its concrete HTTP/usage parser.
        if kwargs.get("transport") is not None:
            return _REAL_HTTPX_CLIENT(**kwargs)
        return _REAL_HTTPX_CLIENT(**{**kwargs, "transport": httpx.MockTransport(transport.respond)})
    monkeypatch.setattr(httpx, "Client", offline_http_client)
    monkeypatch.setattr(pipeline, "TuShareMajorNewsAdapter", _FullScaleNews)
    class RecordingTavilyEvidenceGateway(TavilyEvidenceGateway):
        def fetch(self, **kwargs):
            try:
                bundle = super().fetch(**kwargs)
            except Exception as exc:  # surface a sanitized fixture trace on a terminal aggregate failure
                with tavily_wire._lock:
                    tavily_wire.failures.append(f"{type(exc).__name__}: {exc}")
                raise
            with tavily_wire._lock:
                tavily_wire.outcomes.append(dict(bundle.coverage))
            return bundle

    monkeypatch.setattr(
        pipeline, "TavilyEvidenceGateway",
        lambda **kwargs: RecordingTavilyEvidenceGateway(**kwargs, client=tavily_client, clock=lambda: fixture_run_at),
    )
    original_research_outcome = pipeline._research_outcome

    def recording_research_outcome(**kwargs):
        try:
            return original_research_outcome(**kwargs)
        except Exception as exc:
            with tavily_wire._lock:
                tavily_wire.failures.append(f"research {type(exc).__name__}: {exc}")
            raise

    monkeypatch.setattr(pipeline, "_research_outcome", recording_research_outcome)
    monkeypatch.setattr(pipeline, "SqliteCompanyMetadataProvider", lambda **_kwargs: _Metadata())
    monkeypatch.setattr(pipeline, "_now", lambda: fixture_run_at)
    # The fixture endpoint deliberately has no production identity.  Declare
    # only its deterministic capacity so model-option validation cannot borrow
    # DeepSeek's live endpoint capability and no socket can be opened.
    monkeypatch.setitem(metering._MODEL_CAPABILITIES,
                        ("https://fixture.invalid/v1/chat/completions", "deepseek-v4-pro"), {
                            "contextTokens": 1_000_000, "maxOutputTokens": 384_000,
                            "counter": "v340-offline-acceptance-bound",
                        })
    return transport, tavily_wire


def _append_configuration(*, db_path: Path, fixture_now: datetime) -> tuple[str, int, str, int]:
    config_path = Path(__file__).parents[1] / "neckline" / "config" / "k10-v2.json"
    execution_path = Path(__file__).parents[1] / "neckline" / "config" / "k10-execution-v4.json"
    config_id = "v340-acceptance"
    config_revision = store.append_run_config(
        config_id=config_id, payload=json.loads(config_path.read_text(encoding="utf-8")),
        created_at=fixture_now.isoformat(), db_path=db_path,
    )
    execution_id = "v340-acceptance-execution"
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    policy = execution["discovery"]["titleTriagePolicy"]
    store.append_title_triage_policy(
        policy_id=policy["policyId"], content=policy["content"], approval_state="approved",
        created_at=fixture_now.isoformat(), approved_at=fixture_now.isoformat(), db_path=db_path,
    )
    execution_revision = store.append_execution_config(
        config_id=execution_id, payload=execution, created_at=fixture_now.isoformat(), db_path=db_path,
    )
    import_profiles(
        universe_file=Path("/Users/linotsai/Lino/whynotme/research/K10-v2初始股票池_20260909.json"),
        profiles_dir=Path("/Users/linotsai/Lino/whynotme/artifacts/output/k10-company-profiles-v2-20260909"),
        db_path=db_path, confirmed_target=db_path, universe_id=UNIVERSE_ID, profiles_id=PROFILES_ID,
        imported_at=fixture_now.isoformat(),
    )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT count(*) FROM k10_v2_universe_members").fetchone()[0] == 1089
    bind_strategy(
        db_path=db_path, snapshot_id="k10-v2-20260909", config_id=config_id, config_revision=config_revision,
        execution_config_id=execution_id, execution_config_revision=execution_revision, created_at=fixture_now.isoformat(),
    )
    return config_id, config_revision, execution_id, execution_revision


def seed_database(path: Path, *, trading_day: date = DAY, fixture_now: datetime = NOW) -> tuple[str, int, str, int]:
    # Mirror the explicit storage bootstrap for an owned, isolated DB.
    # K10's schema migration intentionally does not select a journal mode; the
    # common storage bootstrap owns that choice and persists WAL before the
    # concurrent CLI/worker acceptance path starts.
    initialize_common_schema(path)
    initialize_schema(path)
    initialize_notifications_schema(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        connection.execute("DELETE FROM trade_cal")
        connection.executemany(
            "INSERT INTO trade_cal(exchange,cal_date,is_open) VALUES ('SSE', ?, 1)",
            [(trading_day.strftime("%Y%m%d"),), ((trading_day + timedelta(days=1)).strftime("%Y%m%d"),),
             ((trading_day + timedelta(days=2)).strftime("%Y%m%d"),)],
        )
    store.set_run_control(state="open", reason_code="fixture_approved", changed_at=fixture_now.isoformat(),
                          changed_by="v340_acceptance", db_path=path)
    return _append_configuration(db_path=path, fixture_now=fixture_now)


def run_full_scale_flow(tmp_path: Path, monkeypatch, *, name: str, refusal_event: int | None = None,
                        selected_event_count: int = SELECTED_EVENT_COUNT,
                        all_events_same_company: bool = False,
                        refusal_operation: str | None = None,
                        expect_handler_failure: bool = False,
                        trading_day: date = DAY, fixture_now: datetime = NOW,
                        fixture_run_at: datetime = RUN_AT) -> FlowResult:
    """Execute one real B76 CLI → claim → production handler flow at release scale."""
    db_path = tmp_path / f"{name}.sqlite"
    config_id, config_revision, execution_id, execution_revision = seed_database(
        db_path, trading_day=trading_day, fixture_now=fixture_now,
    )
    transport, tavily_wire = install_offline_transports(
        monkeypatch, refusal_event=refusal_event, selected_event_count=selected_event_count,
        all_events_same_company=all_events_same_company, refusal_operation=refusal_operation,
        fixture_run_at=fixture_run_at,
    )
    stdout = StringIO()
    with redirect_stdout(stdout):
        assert cli_main([
            "enqueue", "--db", str(db_path), "--kind", "evening", "--trading-day", trading_day.isoformat(),
            "--config-id", config_id, "--config-revision", str(config_revision),
            "--execution-config-id", execution_id, "--execution-config-revision", str(execution_revision),
            "--bootstrap-cutoff", (evening_cutoff(trading_day) - timedelta(hours=2)).isoformat(),
        ]) == 0
    task_id = stdout.getvalue().strip()
    assert task_id.startswith("task_")
    provider = MeteredProvider(
        ledger_db=db_path, ledger_task="discovery", api_key="fixture", model="deepseek-v4-pro", name="fixture",
        api_url="https://fixture.invalid/v1/chat/completions", read_timeout=1, use_streaming=False,
    )
    provider.max_attempts = 1
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro",
                        lambda **_kwargs: ProviderResolution("configured", provider, "fixture", None))
    parquet_dir = tmp_path / "parquet"
    handlers = pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=parquet_dir)
    handler_faults: list[BaseException] = []

    def production_handler(context):
        # ``production_scan_handler`` binds ``now=_now`` at import time, so a
        # module monkeypatch alone leaves its business cutoff at the host wall
        # clock.  Inject the frozen source/report clock through the real public
        # handler while retaining the live worker clock below for lease checks.
        return pipeline.production_scan_handler(
            context, tushare_token="fixture-token", parquet_dir=parquet_dir,
            now=lambda: fixture_run_at,
        )

    def capture_handler_fault(context):
        try:
            return production_handler(context)
        except BaseException as exc:
            # The real worker correctly persists only a safe error.  Tests
            # retain the local exception long enough to report an actionable
            # regression without changing production behavior.
            handler_faults.append(exc)
            raise

    handlers["evening_scan"] = capture_handler_fault
    frozen_task = store.get_task(task_id=task_id, db_path=db_path)
    frozen_execution = store.task_execution_input(task_id=task_id, db_path=db_path)
    assert frozen_task is not None and frozen_execution is not None
    # A continuation reclaims the original queued task through the production
    # worker selector. It does not call CLI recovery, write task state, or
    # synthesize another execution binding. Eight passes safely bounds this
    # 84-event fixture while treating an unfinished slice as a real failure.
    max_worker_passes = 8
    worker_passes = continuation_count = 0
    execution_started_at = continuation_scan_id = None
    task = None
    while worker_passes < max_worker_passes:
        task = run_once(
            db_path=db_path, worker_id="v340-acceptance", lease_for=timedelta(minutes=5),
            handlers=handlers,
            # The frozen report/source boundary stays at RUN_AT. A worker lease
            # is a live coordination primitive, so its own clock stays current.
            clock=lambda: datetime.now(SHANGHAI), require_b76_contract=True,
        )
        worker_passes += 1
        assert task is not None and task.task_id == task_id
        current_execution = store.task_execution_input(task_id=task_id, db_path=db_path)
        current_task = store.get_task(task_id=task_id, db_path=db_path)
        assert current_execution is not None and current_task is not None
        assert current_task.payload == frozen_task.payload
        assert current_execution["inputVersion"] == frozen_execution["inputVersion"]
        assert current_execution["inputCutoffAt"] == frozen_execution["inputCutoffAt"]
        assert current_execution["executionProfile"] == frozen_execution["executionProfile"]
        checkpoint_after_pass = current_execution["checkpoint"]
        assert isinstance(checkpoint_after_pass, Mapping)
        started = checkpoint_after_pass.get("executionStartedAt")
        assert isinstance(started, str)
        if execution_started_at is None:
            execution_started_at = started
        else:
            assert started == execution_started_at
        if task.status != "queued":
            break
        scan_after_pass = checkpoint_after_pass.get("scanId")
        assert isinstance(scan_after_pass, str)
        if continuation_scan_id is None:
            continuation_scan_id = scan_after_pass
        else:
            assert scan_after_pass == continuation_scan_id
        # DISCOVERY_SLICE is an ordinary same-task continuation. A failed
        # provider retry, a different task, or a non-due return must surface as
        # an acceptance error instead of being silently treated as completion.
        with sqlite3.connect(db_path) as connection:
            retry = connection.execute(
                "SELECT retry_kind,safe_error_code,failure_attempt_count "
                "FROM k10_task_retry_schedules WHERE task_id=?",
                (task_id,),
            ).fetchone()
        assert retry == ("continuation", "DISCOVERY_SLICE", 0)
        continuation_count += 1
    else:
        raise AssertionError(f"full-scale task did not terminalize after {max_worker_passes} worker passes")
    assert task is not None and task.status in {"completed", "failed"}
    if handler_faults and not expect_handler_failure:
        if tavily_wire.failures or tavily_wire.outcomes:
            raise AssertionError(
                "real Tavily gateway trace before terminal worker failure: "
                f"failures={tavily_wire.failures[:3]!r} outcomes={tavily_wire.outcomes[:3]!r}"
            ) from handler_faults[0]
        raise handler_faults[0]
    execution = store.task_execution_input(task_id=task_id, db_path=db_path)
    checkpoint = execution["checkpoint"]
    scan_id = checkpoint.get("scanId") if isinstance(checkpoint, Mapping) else None
    if not isinstance(scan_id, str):
        # A terminal global failure can persist the real failed report before
        # worker checkpoint finalization.  Read its existing scan identity;
        # never repair the task state to make an acceptance fixture convenient.
        with sqlite3.connect(db_path) as connection:
            row = connection.execute("SELECT scan_id FROM k10_scans ORDER BY created_at DESC, scan_id DESC LIMIT 1").fetchone()
        assert row is not None
        scan_id = str(row[0])
    if continuation_scan_id is not None:
        assert scan_id == continuation_scan_id
    calls: dict[str, int] = {}
    for kind, _event in transport.calls:
        calls[kind] = calls.get(kind, 0) + 1
    return FlowResult(
        db_path=db_path, task_id=task_id, scan_id=scan_id, task_status=task.status,
        task_stage=checkpoint.get("stage") if isinstance(checkpoint, Mapping) else None,
        calls=calls, transport_calls=tuple(transport.calls), gateway_calls=tuple(tavily_wire.queries),
        gateway_trace=tuple(tavily_wire.failures),
        company_codes=transport.company_codes,
        worker_passes=worker_passes,
        continuation_count=continuation_count,
    )


def actual_api(path: Path, *, config_id: str, config_revision: int, execution_id: str, execution_revision: int):
    """Build the production router with explicit active configuration bindings."""
    from fastapi import FastAPI
    from neckline.api.k10 import create_router

    app = FastAPI()
    app.include_router(create_router(
        lambda: path,
        lambda: None,
        lambda: path.parent / "parquet",
        current_config_binding_provider=lambda: (config_id, config_revision, None),
        current_execution_config_binding_provider=lambda: (execution_id, execution_revision, None),
    ))
    return TestClient(app)


def active_bindings(path: Path) -> tuple[str, int, str, int]:
    with sqlite3.connect(path) as connection:
        config = connection.execute(
            "SELECT config_id, revision FROM k10_run_config_revisions ORDER BY created_at DESC, revision DESC LIMIT 1"
        ).fetchone()
        execution = connection.execute(
            "SELECT config_id, revision FROM k10_execution_config_revisions ORDER BY created_at DESC, revision DESC LIMIT 1"
        ).fetchone()
    assert config is not None and execution is not None
    return str(config[0]), int(config[1]), str(execution[0]), int(execution[1])
