"""3.3.0 B69 regressions for bounded research input and paused scheduling."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
import sqlite3

import httpx
import pytest
from neckline.k10 import store
from neckline.k10.cli import main
from neckline.k10.discovery import DiscoveryDocument, EventDraft, EvidenceRef
from neckline.k10.metering import MeteredProvider, bind_provider_execution_spending, provider_spend_context
from neckline.k10.pipeline import DeepSeekDiscoveryModel
from neckline.k10.research_context import project_packet, read_context
from neckline.k10.research_store import _eligible_prior_source
from neckline.k10.schema import initialize_schema, read_connection
from neckline.k10.verification import TavilyEvidenceGateway
from neckline.llm.base import ChatMessage, LLMResult, SearchHit
from neckline.llm.openai_compat import OpenAICompatProvider
from neckline.search.tavily import TavilySearchResponse
from tests.k10_v306_fixture import append_approved_execution_profile
from tests.test_k10_cli import _db, _execution


class _RecordingProvider:
    def __init__(self) -> None:
        self.messages = []

    def chat(self, messages, **_kwargs):
        self.messages.append(messages)
        return LLMResult(ok=True, content='{"events":[],"needsFullText":false}', provider="fixture", model="fixture")


def test_prospectus_body_never_reaches_understand_model() -> None:
    provider = _RecordingProvider()
    model = DeepSeekDiscoveryModel(provider)
    body = "招股说明书\n发行人声明\n" + "客户、供应链和历史背景。" * 70_000
    assert len(body) > 807_670
    document = DiscoveryDocument("prospectus", 1, "2026-09-11T00:00:00+00:00", "2026-09-13T13:00:16+00:00",
                                 body, "招股书摘要", {"title": "首次公开发行股票并在创业板上市招股说明书"})

    assert model.understand(document=document) == ()
    assert provider.messages == []


def test_verification_search_and_cached_bundle_keep_prospectus_local_before_any_runtime_card(tmp_path) -> None:
    """Search/cached evidence uses the same P01 admission as initial understanding."""
    from neckline.k10.research_runtime import _Investigation
    from tests.test_k10_verification import _event, NOW

    class Search:
        calls = 0

        def search(self, query):
            self.calls += 1
            return TavilySearchResponse(True, query, credits=1, hits=(
                SearchHit(title="首次公开发行股票并在创业板上市招股说明书", link="https://example.test/prospectus",
                          content="发行人声明\n本招股说明书\n募集资金运用", publish_date="2026-09-06T01:00:00+00:00"),
                SearchHit(title="公司发布招股说明书受理进展新闻", link="https://example.test/news",
                          content="公司称招股说明书已获受理，募集资金用途仍待监管审核。", publish_date="2026-09-06T02:00:00+00:00"),
            ))

    db = tmp_path / "prospectus-search.sqlite"
    initialize_schema(db)
    search = Search()
    gateway = TavilyEvidenceGateway(db_path=db, client=search, clock=lambda: NOW)
    bundle = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert search.calls == 1
    assert len(bundle.documents) == len(bundle.eligible_documents) == 1
    assert bundle.documents[0].metadata["title"] == "公司发布招股说明书受理进展新闻"
    assert bundle.coverage["materialExclusions"] == {"prospectus_document": 1}
    with sqlite3.connect(db) as conn:
        rows = [json.loads(row[0]) for row in conn.execute("SELECT metadata_json FROM k10_source_document_versions")]
    prospectus_metadata = next(row for row in rows if row.get("title", "").endswith("招股说明书"))
    assert prospectus_metadata["materialAdmission"]["reason"] == "prospectus_document"

    # An older checkpoint could still name that stored source.  Its restored
    # bundle and the next runtime evidence card both exclude the raw snippet.
    prospectus_row = next(
        row for row in store.list_source_document_versions(cutoff_at=None, db_path=db)
        if (row.get("metadata") or {}).get("materialAdmission", {}).get("reason") == "prospectus_document"
    )
    cached = gateway._restore_checkpoint_bundle({
        "state": "available",
        "documentRefs": [{"documentId": prospectus_row["documentId"], "revision": prospectus_row["revision"]}],
        "eligibleDocumentRefs": [{"documentId": prospectus_row["documentId"], "revision": prospectus_row["revision"]}],
        "coverage": {"provider": "tavily", "state": "available"},
    })
    assert not cached.documents and not cached.eligible_documents
    runtime = object.__new__(_Investigation)
    excluded = DiscoveryDocument(prospectus_row["documentId"], prospectus_row["revision"], prospectus_row.get("publishedAt"),
                                 prospectus_row["fetchedAt"], prospectus_row.get("originalText"), prospectus_row.get("excerpt"),
                                 prospectus_row.get("metadata") or {})
    runtime.allowed, runtime.documents = {excluded.evidence_ref}, {excluded.evidence_ref: excluded}
    runtime.event, runtime.state = type("Event", (), {"source_refs": ()})(), {"claims": [], "stageResults": []}
    assert runtime._cards() == []


def test_long_single_line_context_returns_locator_not_whole_body() -> None:
    text = "逐字连续正文" * 100_000
    document = DiscoveryDocument("long", 1, "2026-09-11T00:00:00+00:00", "2026-09-13T13:00:16+00:00",
                                 text, None, {"title": "长篇公告"})
    value = read_context({"kind": "source", "purpose": "核对限定语", "sourceRef": {"documentId": "long", "revision": 1},
                          "location": "paragraph:1"}, state={"questions": []},
                         documents={document.evidence_ref: document}, binding=None, eligible_refs={("long", 1)})

    assert value["status"] == "found"
    assert len(json.dumps(value, ensure_ascii=False)) < 30_000
    assert value["value"]["needsLocator"] is True
    assert value["value"]["locatorHint"] == "line:1"
    assert value["value"]["unreadableLocatorCount"] == 1
    assert "text" not in value["value"]


def test_same_task_frozen_source_allows_collection_after_nominal_news_cutoff(tmp_path) -> None:
    db = tmp_path / "shared-source.sqlite"
    initialize_schema(db)
    published = "2026-09-11T12:00:00+00:00"
    fetched = "2026-09-13T13:00:16+00:00"
    cutoff = "2026-09-13T13:00:00+00:00"
    store.append_document_version(document_id="shared", source_key="fixture", external_id="shared",
                                  canonical_url="https://example.test/shared", content_sha256="a" * 64,
                                  published_at=published, published_precision="exact", fetched_at=fetched,
                                  original_text="同任务冻结正文", excerpt="摘要", fetch_version="fixture", metadata={},
                                  created_at=fetched, db_path=db)
    with read_connection(db) as conn:
        accepted, timing = _eligible_prior_source(conn, ref={"documentId": "shared", "revision": 1},
                                                   allowed={("shared", 1)}, cutoff=__import__("datetime").datetime.fromisoformat(cutoff),
                                                   same_task_frozen=True,
                                                   verification_cutoff=__import__("datetime").datetime.fromisoformat("2026-09-13T13:01:00+00:00"))
    assert accepted, timing


@pytest.mark.parametrize('protocol', ['k10-v2-context-3.3.0', 'k10-v2-context-3.3.0-b70'])
def test_b69_scoped_gateway_refuses_headline_fallback_before_http(tmp_path, protocol) -> None:
    from neckline.k10.verification_checkpoints import VerificationCheckpointError
    from tests.test_k10_verification import _event, NOW
    from tests.test_v310_tavily import _SearchExtract, _gateway

    client = _SearchExtract()
    gateway, _ = _gateway(tmp_path / "scoped-headline.sqlite", client)
    gateway.context_protocol = protocol
    with pytest.raises(VerificationCheckpointError, match="research_query_scope_invalid"):
        gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW)
    assert client.calls == 0


def _b69_scoped_query(question_id: str, *, claim_id: str, company_code: str) -> tuple[dict[str, object], dict[str, object]]:
    """Build the same self-authenticating scope shape a persisted B69 path carries."""
    question: dict[str, object] = {
        "questionId": question_id,
        "question": "本事件是否已形成可确认订单？",
        "claimIds": [claim_id],
        "companyCodes": [company_code],
        "knownEvidence": [],
        "missingEvidence": ["独立公告"],
        "supportCondition": "公告确认订单",
        "refuteCondition": "仅处于送样或资格阶段",
    }
    projection = {key: question.get(key) for key in (
        "questionId", "question", "claimIds", "companyCodes", "supportCondition", "refuteCondition", "missingEvidence",
    )}
    scope: dict[str, object] = {
        "version": "k10-v2-question-scope-1",
        "questionId": question_id,
        "claimIds": question["claimIds"],
        "companyCodes": question["companyCodes"],
        "questionSha256": sha256(json.dumps(projection, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":")).encode()).hexdigest(),
    }
    scope["scopeSha256"] = sha256(json.dumps(scope, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":")).encode()).hexdigest()
    path: dict[str, object] = {
        "questionId": question_id,
        "pathId": f"path-{question_id}",
        "query": "项目 送样 公告",
        "intent": "确认项目实际阶段",
        "targetSource": "公司原始公告",
        "newPathReason": "当前事件缺少独立确认",
        "expectedInformationGain": "订单或仅送样",
        "expectedJudgmentChange": "避免把送样当订单",
        "purposeKind": "event_fact",
        "targetRefs": [{"kind": "claim", "claimId": claim_id}],
        "questionScope": scope,
    }
    return question, path


def test_b69_scoped_events_share_one_unknown_physical_search_after_restart(tmp_path) -> None:
    """Separate P02 authorizations cannot bypass a same-wire unknown reservation."""
    from tests.test_k10_verification import NOW, _bound_task

    class UnknownSearch:
        def __init__(self) -> None:
            self.calls = 0

        def search(self, _query):
            self.calls += 1
            raise RuntimeError("response lost after dispatch")

    db = tmp_path / "scoped-shared-unknown.sqlite"
    initialize_schema(db)
    task_id = _bound_task(db)
    client = UnknownSearch()
    first = TavilyEvidenceGateway(db_path=db, client=client, task_id=task_id,
                                  clock=lambda: NOW, network_max_attempts=2)
    first.context_protocol = "k10-v2-context-3.3.0"
    first_question, first_path = _b69_scoped_query("q-first", claim_id="claim-first", company_code="300001.SZ")
    first_event = EventDraft(
        "first-event", "stage", "reported", "首个项目事件", "disclosure", {}, (EvidenceRef("origin", 1),),
    )
    initial = first.fetch(event=first_event, retrieved_at=NOW, cutoff_at=NOW,
                          question=first_question, query_path=first_path)
    assert initial.coverage["reason"] == "tavily_request_outcome_unknown" and client.calls == 1

    # A new provider instance and a separately valid event/question must find
    # the same physical request before it can open a replacement socket.
    restarted = TavilyEvidenceGateway(db_path=db, client=client, task_id=task_id,
                                      clock=lambda: NOW, network_max_attempts=2)
    restarted.context_protocol = "k10-v2-context-3.3.0"
    other_question, other_path = _b69_scoped_query("q-second", claim_id="claim-second", company_code="300002.SZ")
    other_event = EventDraft(
        "second-event", "stage", "reported", "第二个项目事件", "disclosure", {}, (EvidenceRef("origin", 1),),
    )
    blocked = restarted.fetch(event=other_event, retrieved_at=NOW, cutoff_at=NOW,
                              question=other_question, query_path=other_path)
    assert blocked.coverage["reason"] == "tavily_request_outcome_unknown"
    assert client.calls == 1
    assert store.external_attempt_summary(task_id=task_id, db_path=db)["unknown"] == 1


def test_same_task_receiver_refreshes_empty_shared_facts_after_peer_commit(tmp_path) -> None:
    """A concurrent receiver must refresh, not permanently cache its empty first read."""
    from dataclasses import replace
    from types import SimpleNamespace
    from datetime import datetime
    from tests.test_v310_research_storage import _seed, _claim, NOW, LATER
    from neckline.k10.discovery import EventDraft
    from neckline.k10.research_contracts import ResearchStageResult
    from neckline.k10.research_runtime import _Investigation
    from neckline.k10.research_store import create_research_snapshot, advance_research_snapshot, read_research_state

    db = tmp_path / "shared-refresh.sqlite"
    source_snapshot = _seed(db)
    store.append_event_revision(event_id="receiver-event", stable_key="receiver", headline="同源接收事件",
                                event_kind="disclosure", facts={}, source_refs=[{"documentId": "source-1", "revision": 1}],
                                supersedes_revision=None, created_at=NOW, db_path=db)
    receiver_snapshot = replace(source_snapshot, snapshot_id="receiver-snapshot", event_id="receiver-event")
    create_research_snapshot(snapshot=receiver_snapshot, db_path=db)
    event = EventDraft("receiver", "initial", "reported", "同源接收事件", "disclosure", {},
                       (EvidenceRef("source-1", 1),))
    runtime = object.__new__(_Investigation)
    runtime.task_id, runtime.db_path, runtime.identity = "task-1", db, "receiver-snapshot"
    runtime.event, runtime.state, runtime.documents, runtime.allowed = event, read_research_state(snapshot_id="receiver-snapshot", db_path=db), {}, set()
    runtime.clock, runtime.guard = lambda: datetime.fromisoformat(LATER), None
    runtime.model = SimpleNamespace(_company_profiles_binding=(db, "fixture"))

    runtime._freeze_prior_evidence()
    assert runtime.state["stageResults"][-1]["result"]["conclusion"]["runtimePriorEvidence"]["claims"] == []

    create_research_snapshot(snapshot=source_snapshot, db_path=db)
    advance_research_snapshot(snapshot_id="snapshot-1", expected_revision=1, research_status="continue_research",
        execution_status="ok", stage_result=ResearchStageResult("extract_claims", claims=(_claim(),)),
        input_sha256="1" * 64, updated_at=LATER, db_path=db)
    runtime._freeze_prior_evidence()
    refreshed = runtime.state["stageResults"][-1]["result"]["conclusion"]["runtimePriorEvidence"]
    assert [claim["text"] for claim in refreshed["claims"]] == ["供应商称项目进入送样"]
    assert len(runtime._cards()[0]["sourceStatements"]) == 1
    revision = runtime.snapshot.revision
    runtime.clock = lambda: datetime.fromisoformat("2026-09-08T13:16:00+00:00")
    runtime._freeze_prior_evidence()
    assert runtime.snapshot.revision == revision


def test_company_projection_keeps_only_summary_until_local_field_read() -> None:
    packet = {
        "companyScope": {"fixedPool": [{"companyCode": "300001.SZ"}], "candidateCompanyCodes": ["300001.SZ"],
                         "companyProfiles": [{"identity": {"ts_code": "300001.SZ", "name": "公司"}, "summary": "短业务摘要",
                                              "review_status": "local_draft_awaiting_user", "profileContentSha256": "a" * 64,
                                              "relationships": [{"entity": "客户", "details": "不应整包进入每个研究动作" * 4000}]}]},
        "claims": [], "questions": [], "evidenceUpdates": [], "allowedEvidenceRefs": [], "evidenceCards": [],
        "fulltextRequests": [], "queryPaths": [],
    }
    projected = project_packet("plan_gaps", packet)
    payload = json.dumps(projected, ensure_ascii=False)
    assert "不应整包进入每个研究动作" not in payload
    assert projected["companyScope"]["companyProfiles"][0]["summary"] == "短业务摘要"


def test_final_provider_guard_blocks_over_context_before_socket(tmp_path, monkeypatch) -> None:
    db = tmp_path / "metered.sqlite"
    initialize_schema(db)
    execution_id, execution_revision = append_approved_execution_profile(db_path=db, created_at="2026-09-13T13:00:00+00:00",
                                                                           config_id="execution")
    store.enqueue_task(task_id="task", kind="evening_scan", idempotency_key="task", input_version="fixture",
                       input_cutoff_at="2026-09-13T13:00:00+00:00", payload={}, budget={},
                       created_at="2026-09-13T13:00:00+00:00", db_path=db)
    store.bind_task_execution(task_id="task", execution_config_id=execution_id, execution_config_revision=execution_revision,
                              binding_kind="scheduled", bound_at="2026-09-13T13:00:00+00:00", db_path=db)
    store.set_run_control(state="open", reason_code="fixture", changed_at="2026-09-13T13:00:00+00:00", changed_by="test", db_path=db)
    provider = MeteredProvider(ledger_db=db, ledger_task="fixture", api_key="fixture", model="deepseek-flash", name="fixture",
                               api_url="https://api.deepseek.com/chat/completions")
    bind_provider_execution_spending(provider=provider, task_id="task", execution_profile=store.task_execution_profile(task_id="task", db_path=db))
    monkeypatch.setattr(OpenAICompatProvider, "chat", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("socket opened")))

    from neckline.llm.base import ChatMessage
    with provider_spend_context(provider=provider, task_id="task", stage="investigation", item_key="item", attempt=1):
        result = provider.chat([ChatMessage(role="user", content="超长资料" * 600_000)], enable_search=False,
                               response_format={"type": "json_object"}, model_options={"maxTokens": 32_768})
    assert result.error_code == "execution_context_exceeded"


def test_scheduled_closed_control_is_a_structured_success_without_task(tmp_path, capsys) -> None:
    db = tmp_path / "scheduled-closed.sqlite"
    revision = _db(db)
    execution_revision = _execution(db)
    store.set_run_control(state="closed", reason_code="tavily_plan_limit", changed_at="2026-09-13T13:00:00+00:00",
                          changed_by="test", db_path=db)

    assert main(["enqueue", "--db", str(db), "--kind", "evening", "--trading-day", "2026-09-07",
                 "--config-id", "fixture", "--config-revision", str(revision),
                 "--execution-config-id", "fixture-execution", "--execution-config-revision", str(execution_revision), "--scheduled"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "skipped", "reason": "tavily_plan_limit", "taskId": None}
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_tasks").fetchone()[0] == 0


def test_scheduled_pause_does_not_hide_calendar_config_or_missing_control_faults(tmp_path) -> None:
    """Only a real closed control with otherwise-valid scheduled input exits zero."""
    db = tmp_path / "scheduled-control-validation.sqlite"
    revision = _db(db)
    execution_revision = _execution(db)
    store.set_run_control(state="closed", reason_code="tavily_plan_limit", changed_at="2026-09-13T13:00:00+00:00",
                          changed_by="test", db_path=db)
    base = ["enqueue", "--db", str(db), "--kind", "evening", "--trading-day", "2026-09-07",
            "--config-id", "fixture", "--config-revision", str(revision), "--scheduled"]
    with pytest.raises(RuntimeError, match="执行配置"):
        main([*base, "--execution-config-id", "missing", "--execution-config-revision", "1"])
    with pytest.raises(RuntimeError, match="交易日历缺覆盖"):
        main(["enqueue", "--db", str(db), "--kind", "evening", "--trading-day", "2026-09-11",
              "--config-id", "fixture", "--config-revision", str(revision),
              "--execution-config-id", "fixture-execution", "--execution-config-revision", str(execution_revision), "--scheduled"])
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM k10_run_controls WHERE control_key='k10_discovery'")
    with pytest.raises(RuntimeError, match="运行已暂停"):
        main([*base, "--execution-config-id", "fixture-execution", "--execution-config-revision", str(execution_revision)])


def test_scheduled_close_between_read_and_enqueue_skips_without_new_task(tmp_path, capsys, monkeypatch) -> None:
    """The real CLI must recheck control in the insert transaction after an open read."""
    db = tmp_path / "scheduled-close-race.sqlite"
    revision = _db(db)
    execution_revision = _execution(db)
    original_enqueue = store.enqueue_task

    def close_before_atomic_enqueue(**kwargs):
        store.set_run_control(state="closed", reason_code="tavily_plan_limit", changed_at="2026-09-13T13:00:01+00:00",
                              changed_by="race", db_path=db)
        return original_enqueue(**kwargs)

    monkeypatch.setattr("neckline.k10.cli.store.enqueue_task", close_before_atomic_enqueue)
    assert main(["enqueue", "--db", str(db), "--kind", "evening", "--trading-day", "2026-09-07",
                 "--config-id", "fixture", "--config-revision", str(revision),
                 "--execution-config-id", "fixture-execution", "--execution-config-revision", str(execution_revision),
                 "--scheduled"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "skipped", "reason": "tavily_plan_limit", "taskId": None}
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_tasks").fetchone()[0] == 0


def _receipt_provider(tmp_path, *, task_id: str = "receipt-task"):
    db = tmp_path / "receipt.sqlite"
    # This is the explicit isolated test setup, never a read-path migration:
    # it lets the receipt regression prove the auxiliary usage ledger is not
    # appended again when a paid response is locally reused.
    from neckline.db import init_schema as init_shared_schema
    init_shared_schema(db)
    initialize_schema(db)
    config_id, revision = append_approved_execution_profile(
        db_path=db, created_at="2026-09-13T13:00:00+00:00", config_id="receipt-execution",
    )
    store.enqueue_task(task_id=task_id, kind="evening_scan", idempotency_key=task_id, input_version="fixture",
                       input_cutoff_at="2026-09-13T13:00:00+00:00", payload={}, budget={},
                       created_at="2026-09-13T13:00:00+00:00", db_path=db)
    store.bind_task_execution(task_id=task_id, execution_config_id=config_id, execution_config_revision=revision,
                              binding_kind="scheduled", bound_at="2026-09-13T13:00:00+00:00", db_path=db)
    store.set_run_control(state="open", reason_code="fixture", changed_at="2026-09-13T13:00:00+00:00",
                          changed_by="test", db_path=db)
    provider = MeteredProvider(ledger_db=db, ledger_task="discovery", api_key="fixture", model="deepseek-flash",
                               name="fixture", api_url="https://api.deepseek.com/chat/completions")
    bind_provider_execution_spending(provider=provider, task_id=task_id,
                                     execution_profile=store.task_execution_profile(task_id=task_id, db_path=db))
    return db, provider


def _restarted_receipt_provider(db, *, task_id: str = "receipt-task"):
    """Recreate only the worker-side provider around an existing task DB."""
    provider = MeteredProvider(ledger_db=db, ledger_task="discovery", api_key="fixture", model="deepseek-flash",
                               name="fixture", api_url="https://api.deepseek.com/chat/completions")
    bind_provider_execution_spending(provider=provider, task_id=task_id,
                                     execution_profile=store.task_execution_profile(task_id=task_id, db_path=db))
    return provider


def test_committed_model_receipt_reuses_exact_wire_without_duplicate_usage(tmp_path, monkeypatch) -> None:
    db, provider = _receipt_provider(tmp_path)
    upstream_calls, usage_calls = [], []

    def upstream(_request):
        upstream_calls.append(1)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "{\"events\":[]}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        })

    monkeypatch.setattr("neckline.k10.metering.usage.record", lambda **kwargs: usage_calls.append(kwargs))
    messages = [ChatMessage(role="user", content="same frozen evidence")]
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="doc-a", attempt=1):
        first = provider.chat(messages, enable_search=False, response_format={"type": "json_object"},
                              model_options={"maxTokens": 128, "temperature": 0.1},
                              transport=httpx.MockTransport(upstream))
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="doc-b", attempt=1):
        reused = provider.chat(messages, enable_search=False, response_format={"type": "json_object"},
                                model_options={"maxTokens": 128, "temperature": 0.1},
                                transport=httpx.MockTransport(upstream))
    assert first.ok and reused.ok and getattr(reused, "local_reuse", False) is True
    assert isinstance(getattr(reused, "reused_attempt_id", None), str)
    assert provider._thread_usage.last_model_receipt_attempt_id == reused.reused_attempt_id
    assert len(upstream_calls) == len(usage_calls) == 1
    assert store.external_attempt_summary(task_id="receipt-task", db_path=db)["succeeded"] == 1
    with read_connection(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_model_response_receipts").fetchone()[0] == 1
        assert "same frozen evidence" not in conn.execute("SELECT payload_json FROM k10_model_response_receipts").fetchone()[0]


def test_metered_request_fingerprint_is_the_actual_mocktransport_wire_and_cross_checkpoint_reuses_it(tmp_path) -> None:
    """The worker may die after the paid reply but before its business checkpoint."""
    db, provider = _receipt_provider(tmp_path)
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "{\"events\":[]}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        })

    messages = [ChatMessage(role="user", content="exact frozen evidence")]
    kwargs = {"enable_search": False, "response_format": {"type": "json_object"},
              "model_options": {"maxTokens": 128, "temperature": 0.2},
              "transport": httpx.MockTransport(respond)}
    expected = provider.initial_wire_payload(messages, enable_search=False,
                                             response_format={"type": "json_object"},
                                             model_options={"maxTokens": 128, "temperature": 0.2})
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="before-checkpoint", attempt=1):
        first = provider.chat(messages, **kwargs)
    # Deliberately do not write a discovery/checkpoint row here.  A later item
    # must read the receipt created at the provider-result boundary.
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="after-restart", attempt=1):
        reused = provider.chat(messages, **kwargs)
    assert first.ok and reused.ok and getattr(reused, "local_reuse", False) is True
    assert sent == [expected]
    assert sent[0]["temperature"] == 0.2 and sent[0]["max_tokens"] == 128
    request_hash = sha256(json.dumps({"endpoint": "https://api.deepseek.com/chat/completions", "body": expected},
                                     ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT input_sha256 FROM k10_external_attempts").fetchone()[0] == request_hash
        assert conn.execute("SELECT COUNT(*) FROM k10_model_response_receipts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM llm_usage_events").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("body", "error_code"),
    [
        ({"choices": [{"message": {"content": "{not json"}, "finish_reason": "stop"}],
          "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}, "response_json_invalid"),
        ({"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}],
          "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}, "response_truncated"),
    ],
)
def test_paid_protocol_rejections_preserve_raw_response_and_do_not_repost(tmp_path, body, error_code) -> None:
    db, provider = _receipt_provider(tmp_path)
    calls = []

    def respond(_request):
        calls.append(1)
        return httpx.Response(200, json=body)

    messages = [ChatMessage(role="user", content="same paid malformed reply")]
    kwargs = {"enable_search": False, "response_format": {"type": "json_object"},
              "model_options": {"maxTokens": 128}, "transport": httpx.MockTransport(respond)}
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="first", attempt=1):
        first = provider.chat(messages, **kwargs)
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="second", attempt=1):
        reused = provider.chat(messages, **kwargs)
    assert first.error_code == reused.error_code == error_code
    assert first.raw_responses == reused.raw_responses == [body]
    assert getattr(reused, "local_reuse", False) is True and calls == [1]
    # A full provider body is an external response even when local JSON
    # validation rejects it.  The immutable raw receipt drives the same local
    # rejection on replay without spending again.
    assert store.external_attempt_summary(task_id="receipt-task", db_path=db)["succeeded"] == 1
    with sqlite3.connect(db) as conn:
        payload = json.loads(conn.execute("SELECT payload_json FROM k10_model_response_receipts").fetchone()[0])
    assert payload["rawReceiptOnly"] is True and payload["rawResponses"] == [body]


@pytest.mark.parametrize(
    ("response", "error_code", "raw_body"),
    [
        (lambda: httpx.Response(200, content=b"not valid JSON"), "response_json_invalid", "not valid JSON"),
        (lambda: httpx.Response(200, json=[1, 2]), "response_structure_invalid", "[1,2]"),
    ],
)
def test_received_http_200_nonobject_or_nonjson_is_privately_reused_without_repost(
    tmp_path, response, error_code, raw_body,
) -> None:
    """A 200 response may be billable even when it cannot enter the JSON protocol."""
    db, provider = _receipt_provider(tmp_path)
    calls = []

    def respond(_request):
        calls.append(1)
        return response()

    messages = [ChatMessage(role="user", content="retain received malformed HTTP reply")]
    kwargs = {
        "enable_search": False,
        "response_format": {"type": "json_object"},
        "model_options": {"maxTokens": 128},
        "transport": httpx.MockTransport(respond),
    }
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="first", attempt=1):
        first = provider.chat(messages, **kwargs)
    restarted = _restarted_receipt_provider(db)
    with provider_spend_context(provider=restarted, task_id="receipt-task", stage="understand", item_key="after-restart", attempt=1):
        replay = restarted.chat(messages, **kwargs)

    assert first.error_code == replay.error_code == error_code
    assert getattr(replay, "local_reuse", False) is True and calls == [1]
    assert first.usage_unavailable and replay.usage_unavailable
    assert first.raw_responses == replay.raw_responses
    assert store.external_attempt_summary(task_id="receipt-task", db_path=db)["succeeded"] == 1
    with sqlite3.connect(db) as conn:
        payload = json.loads(conn.execute("SELECT payload_json FROM k10_model_response_receipts").fetchone()[0])
        usage = conn.execute(
            "SELECT prompt_tokens,completion_tokens,total_tokens,usage_unavailable FROM llm_usage_events"
        ).fetchone()
    envelope = payload["rawResponses"][0]["_k10_raw_http_response"]
    assert payload["rawReceiptOnly"] is True and payload["responseReceived"] is True
    assert envelope == {"statusCode": 200, "kind": ("json_decode_failure" if error_code == "response_json_invalid"
                                                       else "json_top_level_not_object"), "body": raw_body}
    # The provider did not provide usage.  Retain that unknown rather than
    # inventing a zero-token bill for this received response.
    assert usage == (None, None, None, 1)


@pytest.mark.parametrize("exception_type", [httpx.ReadTimeout, httpx.WriteError, httpx.DecodingError])
def test_dispatched_transport_uncertainty_stays_started_and_blocks_restart_repost(tmp_path, exception_type) -> None:
    """Actual httpx transport exceptions after post entry are never treated as free failures."""
    db, provider = _receipt_provider(tmp_path)
    calls = []

    def uncertain(request):
        calls.append(1)
        raise exception_type("connection outcome unknown", request=request)

    messages = [ChatMessage(role="user", content="do not charge twice after ambiguous transport")]
    kwargs = {
        "enable_search": False,
        "response_format": {"type": "json_object"},
        "model_options": {"maxTokens": 128},
        "transport": httpx.MockTransport(uncertain),
    }
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="first", attempt=1):
        first = provider.chat(messages, **kwargs)
    restarted = _restarted_receipt_provider(db)
    with provider_spend_context(provider=restarted, task_id="receipt-task", stage="understand", item_key="after-restart", attempt=1):
        replay = restarted.chat(messages, **kwargs)

    assert first.error_code == replay.error_code == "provider_request_outcome_unknown"
    assert first.usage_unavailable and replay.usage_unavailable and calls == [1]
    assert store.external_attempt_summary(task_id="receipt-task", db_path=db)["started"] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_model_response_receipts").fetchone()[0] == 0
        usage = conn.execute(
            "SELECT prompt_tokens,completion_tokens,total_tokens,usage_unavailable FROM llm_usage_events"
        ).fetchone()
    assert usage == (None, None, None, 1)


def test_received_body_persistence_failure_keeps_unknown_and_never_settles_synthetic_result(tmp_path, monkeypatch) -> None:
    """A raw-body commit failure must not overwrite the paid outcome with a synthetic error."""
    db, provider = _receipt_provider(tmp_path)
    calls, usage_calls = [], []
    body = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}
    transport = httpx.MockTransport(lambda _request: (calls.append(1) or httpx.Response(200, json=body)))
    monkeypatch.setattr("neckline.k10.store.settle_model_response_attempt",
                        lambda **_kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("injected receipt failure")))
    monkeypatch.setattr("neckline.k10.metering.usage.record", lambda **kwargs: usage_calls.append(kwargs))
    kwargs = {"enable_search": False, "response_format": {"type": "json_object"},
              "model_options": {"maxTokens": 128}, "transport": transport}
    messages = [ChatMessage(role="user", content="raw body must remain unknown if commit fails")]
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="first", attempt=1):
        failed = provider.chat(messages, **kwargs)
    assert failed.error_code == "provider_response_persist_failed"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT state FROM k10_external_attempts").fetchone()[0] == "started"
        assert conn.execute("SELECT COUNT(*) FROM k10_model_response_receipts").fetchone()[0] == 0
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="second", attempt=1):
        blocked = provider.chat(messages, **kwargs)
    assert blocked.error_code == "provider_request_outcome_unknown" and calls == [1]
    assert len(usage_calls) == 1 and usage_calls[0]["result"].raw_responses == [body]


def test_receipt_contract_change_revalidates_exact_raw_wire_without_reposting(tmp_path, monkeypatch) -> None:
    db, provider = _receipt_provider(tmp_path)
    calls = []
    transport = httpx.MockTransport(lambda _request: (calls.append(1) or httpx.Response(200, json={
        "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })))
    messages = [ChatMessage(role="user", content="contract-sensitive reply")]
    kwargs = {"enable_search": False, "model_options": {"maxTokens": 128}, "transport": transport}
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="v1", attempt=1):
        assert provider.chat(messages, **kwargs).ok
    revalidations = []
    original_revalidate = OpenAICompatProvider.revalidate_received_response

    def revalidate_current_parser(self, *args, **kwargs):
        revalidations.append((args, kwargs))
        return original_revalidate(self, *args, **kwargs)

    monkeypatch.setattr(OpenAICompatProvider, "revalidate_received_response", revalidate_current_parser)
    monkeypatch.setattr("neckline.k10.metering._RECEIPT_CONTRACT_VERSION", "k10-model-response-receipt-v2")
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="v2", attempt=1):
        replay = provider.chat(messages, **kwargs)
    assert replay.ok and getattr(replay, "local_reuse", False) is True
    assert calls == [1] and len(revalidations) == 1
    # The immutable receipt retains its original parser contract for audit; a
    # new local contract receives only raw body revalidation, never an old
    # semantic success nor a fresh provider authorization.
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT receipt_version FROM k10_model_response_receipts").fetchone()[0] == "k10-model-response-receipt-v1"
    assert store.external_attempt_summary(task_id="receipt-task", db_path=db)["succeeded"] == 1


def test_contract_changed_semantic_receipt_without_raw_body_blocks_repost(tmp_path, monkeypatch) -> None:
    """An old semantic-only receipt cannot become current-parser success or a new wire."""
    db, provider = _receipt_provider(tmp_path)
    calls = []
    monkeypatch.setattr(
        OpenAICompatProvider,
        "chat",
        lambda *_args, **_kwargs: (calls.append(1) or LLMResult(
            ok=True, content="{}", provider="fixture", model="deepseek-flash", usage_unavailable=True,
        )),
    )
    messages = [ChatMessage(role="user", content="semantic-only historical receipt")]
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="v1", attempt=1):
        assert provider.chat(messages, enable_search=False, model_options={"maxTokens": 128}).ok
    monkeypatch.setattr("neckline.k10.metering._RECEIPT_CONTRACT_VERSION", "k10-model-response-receipt-v2")
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="v2", attempt=1):
        blocked = provider.chat(messages, enable_search=False, model_options={"maxTokens": 128})
    assert blocked.error_code == "provider_response_receipt_unreplayable" and calls == [1]


def test_deepseek_flash_custom_endpoint_cannot_claim_official_capacity(tmp_path) -> None:
    _db_path, provider = _receipt_provider(tmp_path)
    provider.api_url = "https://proxy.example/chat/completions"
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="custom", attempt=1):
        blocked = provider.chat([ChatMessage(role="user", content="evidence")], enable_search=False,
                                model_options={"maxTokens": 128},
                                transport=httpx.MockTransport(lambda _request: (_ for _ in ()).throw(AssertionError("socket opened"))))
    assert blocked.error_code == "execution_model_capability_missing"


def test_unknown_model_or_endpoint_cannot_bypass_capacity_guard(tmp_path) -> None:
    _db_path, provider = _receipt_provider(tmp_path)
    provider.model = "uninspected-provider-model"
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="unknown", attempt=1):
        blocked = provider.chat([ChatMessage(role="user", content="evidence")], enable_search=False,
                                model_options={"maxTokens": 128},
                                transport=httpx.MockTransport(lambda _request: (_ for _ in ()).throw(AssertionError("socket opened"))))
    assert blocked.error_code == "execution_model_capability_missing"


def test_model_receipt_requires_exact_effective_options_and_unknown_does_not_retry(tmp_path, monkeypatch) -> None:
    db, provider = _receipt_provider(tmp_path)
    calls = []
    monkeypatch.setattr(OpenAICompatProvider, "chat", lambda *_args, **_kwargs: (calls.append(1) or LLMResult(
        ok=True, content="{}", provider="deepseek", model="deepseek-flash", usage_unavailable=True)))
    messages = [ChatMessage(role="user", content="evidence")]
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="one", attempt=1):
        assert provider.chat(messages, enable_search=False, model_options={"maxTokens": 128, "temperature": 0.1}).ok
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="two", attempt=1):
        assert provider.chat(messages, enable_search=False, model_options={"maxTokens": 129, "temperature": 0.1}).ok
    assert len(calls) == 2

    unknown_db, unknown_provider = _receipt_provider(tmp_path, task_id="unknown-task")
    monkeypatch.setattr(OpenAICompatProvider, "chat", lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("transport")))
    with provider_spend_context(provider=unknown_provider, task_id="unknown-task", stage="understand", item_key="one", attempt=1):
        try:
            unknown_provider.chat(messages, enable_search=False, model_options={"maxTokens": 128})
        except ConnectionError:
            pass
        else:
            raise AssertionError("transport exception must preserve an unknown external attempt")
    monkeypatch.setattr(OpenAICompatProvider, "chat", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not retry")))
    with provider_spend_context(provider=unknown_provider, task_id="unknown-task", stage="understand", item_key="two", attempt=1):
        blocked = unknown_provider.chat(messages, enable_search=False, model_options={"maxTokens": 128})
    assert blocked.error_code == "provider_request_outcome_unknown"
    assert store.external_attempt_summary(task_id="unknown-task", db_path=unknown_db)["started"] == 1


def test_manual_enqueue_remains_rejected_when_closed(tmp_path) -> None:
    db = tmp_path / "manual-closed.sqlite"
    revision = _db(db)
    execution_revision = _execution(db)
    store.set_run_control(state="closed", reason_code="tavily_plan_limit", changed_at="2026-09-13T13:00:00+00:00",
                          changed_by="test", db_path=db)
    try:
        main(["enqueue", "--db", str(db), "--kind", "evening", "--trading-day", "2026-09-07",
              "--config-id", "fixture", "--config-revision", str(revision),
              "--execution-config-id", "fixture-execution", "--execution-config-revision", str(execution_revision)])
    except RuntimeError as exc:
        assert "暂停" in str(exc)
    else:
        raise AssertionError("manual enqueue must remain rejected while run control is closed")


def test_schema9_empty_receipt_rolls_back_to_schema8_and_forwards_without_rewriting_rows(tmp_path) -> None:
    from neckline.k10 import schema
    db, _provider = _receipt_provider(tmp_path)
    with read_connection(db) as conn:
        before = conn.execute("SELECT group_concat(task_id || ':' || input_version, '|') FROM k10_tasks ORDER BY task_id").fetchone()[0]
        assert conn.execute("SELECT COUNT(*) FROM k10_model_response_receipts").fetchone()[0] == 0
    store.set_run_control(state="closed", reason_code="rollback_rehearsal", changed_at="2026-09-13T13:01:00+00:00",
                          changed_by="test", db_path=db)
    assert schema.schema_version(db) == schema.SCHEMA_VERSION == 9
    assert schema.rollback_schema(db, target_version=8) == 8
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT MAX(version) FROM k10_schema_migrations").fetchone()[0] == 8
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='k10_model_response_receipts'").fetchone() is None
        assert conn.execute("SELECT group_concat(task_id || ':' || input_version, '|') FROM k10_tasks ORDER BY task_id").fetchone()[0] == before
    assert schema.initialize_schema(db) == 9


def test_schema9_refuses_to_drop_unresolved_or_private_paid_receipts(tmp_path, monkeypatch) -> None:
    from neckline.k10 import schema
    db, provider = _receipt_provider(tmp_path)
    messages = [ChatMessage(role="user", content="evidence")]
    monkeypatch.setattr(OpenAICompatProvider, "chat", lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("transport")))
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="one", attempt=1):
        try:
            provider.chat(messages, enable_search=False, model_options={"maxTokens": 128})
        except ConnectionError:
            pass
    store.set_run_control(state="closed", reason_code="rollback_rehearsal", changed_at="2026-09-13T13:01:00+00:00",
                          changed_by="test", db_path=db)
    try:
        schema.rollback_schema(db, target_version=8)
    except schema.K10SchemaError as exc:
        assert "未决" in str(exc)
    else:
        raise AssertionError("unknown external outcome must block Schema 9 rollback")


def test_schema9_nonempty_receipts_export_is_private_and_rollback_refuses(tmp_path, monkeypatch) -> None:
    from neckline.k10 import schema
    db, provider = _receipt_provider(tmp_path)
    monkeypatch.setattr(OpenAICompatProvider, "chat", lambda *_args, **_kwargs: LLMResult(
        ok=True, content="{}", provider="deepseek", model="deepseek-flash", prompt_tokens=2,
        completion_tokens=1, total_tokens=3, usage_unavailable=False, finish_reason="stop"))
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="one", attempt=1):
        assert provider.chat([ChatMessage(role="user", content="evidence")], enable_search=False,
                             model_options={"maxTokens": 128}).ok
    store.set_run_control(state="closed", reason_code="rollback_rehearsal", changed_at="2026-09-13T13:01:00+00:00",
                          changed_by="test", db_path=db)
    export = tmp_path / "private-receipts.json"
    details = schema.export_model_response_receipts(db, export_path=export)
    assert details["receiptCount"] == 1 and export.stat().st_mode & 0o777 == 0o600
    try:
        schema.rollback_schema(db, target_version=8, receipt_export_path=export)
    except schema.K10SchemaError as exc:
        assert "回执" in str(exc) and schema.schema_version(db) == 9
    else:
        raise AssertionError("nonempty paid receipts must block automatic Schema 9 rollback")


def test_schema9_migration_failure_rolls_back_table_and_version(tmp_path, monkeypatch) -> None:
    from neckline.k10 import schema
    db, _provider = _receipt_provider(tmp_path)
    store.set_run_control(state="closed", reason_code="rollback_rehearsal", changed_at="2026-09-13T13:01:00+00:00",
                          changed_by="test", db_path=db)
    assert schema.rollback_schema(db, target_version=8) == 8
    original = schema._apply_v9

    def interrupted(conn):
        original(conn)
        raise RuntimeError("injected migration interruption")

    monkeypatch.setattr(schema, "_apply_v9", interrupted)
    try:
        schema.initialize_schema(db)
    except RuntimeError as exc:
        assert "interruption" in str(exc)
    else:
        raise AssertionError("injected Schema 9 migration failure must surface")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT MAX(version) FROM k10_schema_migrations").fetchone()[0] == 8
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='k10_model_response_receipts'").fetchone() is None


def test_structured_locator_preserves_original_offsets_and_never_slices_large_line() -> None:
    from neckline.k10.research_material import document_outline, read_locator
    text = "标题\n| 项目 | 限定 |\n| A | 不适用 |\n尾注：不构成承诺。"
    doc = DiscoveryDocument("structured", 1, "2026-09-11T00:00:00+00:00", "2026-09-13T13:00:16+00:00",
                            text, None, {"title": "公告"})
    outline = document_outline(doc)
    table = next(item for item in outline["locators"] if item["kind"] == "table")
    value = read_locator(doc, table["locator"])
    assert value is not None and value["text"] == "| 项目 | 限定 |\n| A | 不适用 |\n尾注：不构成承诺。"
    assert text[value["startOffset"]:value["endOffset"]] == value["text"]

    one_line = DiscoveryDocument("unreadable", 1, "2026-09-11T00:00:00+00:00", "2026-09-13T13:00:16+00:00",
                                 "否认条款" * 20_000, None, {"title": "公告"})
    blocked = read_locator(one_line, "line:1")
    assert blocked is not None and blocked["status"] == "not_safely_readable"
    assert blocked["needsStructuredSource"] is True and "text" not in blocked


def test_ipo_news_is_not_prospectus_and_large_excerpt_has_no_bypass() -> None:
    from neckline.k10.research_material import admit_material
    news = DiscoveryDocument("ipo-news", 1, "2026-09-11T00:00:00+00:00", "2026-09-13T13:00:16+00:00",
                             "公司称招股说明书已获受理，募集资金用途仍待监管审核。", None,
                             {"title": "公司发布招股说明书受理进展新闻"})
    assert admit_material(news).state == "admit"
    large_excerpt = DiscoveryDocument("excerpt", 1, "2026-09-11T00:00:00+00:00", "2026-09-13T13:00:16+00:00",
                                      None, "脚注否认信息" * 3_000, {"title": "公告"})
    value = read_context({"kind": "source", "purpose": "核对脚注", "sourceRef": {"documentId": "excerpt", "revision": 1},
                          "location": "excerpt"}, state={"questions": []}, documents={large_excerpt.evidence_ref: large_excerpt},
                         binding=None, eligible_refs={large_excerpt.evidence_ref})
    assert value["status"] == "found" and value["value"]["needsStructuredSource"] is True
    assert "text" not in value["value"]


def test_unrequested_tool_call_is_a_single_paid_raw_receipt_and_never_starts_tool_round(tmp_path) -> None:
    """K10 has no native tools, so a tool-call response cannot open a second wire."""
    db, provider = _receipt_provider(tmp_path)
    calls = []
    body = {
        "choices": [{"message": {"content": None, "tool_calls": [{"id": "unexpected", "type": "function", "function": {"name": "search", "arguments": "{}"}}]},
                     "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }
    transport = httpx.MockTransport(lambda _request: (calls.append(1) or httpx.Response(200, json=body)))
    messages = [ChatMessage(role="user", content="K10 plain text protocol")]
    kwargs = {"enable_search": False, "response_format": {"type": "json_object"},
              "model_options": {"maxTokens": 128}, "transport": transport}
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="first", attempt=1):
        first = provider.chat(messages, **kwargs)
    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="second", attempt=1):
        replay = provider.chat(messages, **kwargs)
    assert first.error_code == replay.error_code == "provider_response_receipt_unreplayable"
    assert first.raw_responses == replay.raw_responses == [body]
    assert getattr(replay, "local_reuse", False) is True and calls == [1]
    assert store.external_attempt_summary(task_id="receipt-task", db_path=db)["succeeded"] == 1
