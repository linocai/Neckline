"""B39 production-boundary regressions using only deterministic transports."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sqlite3

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.api.k10 import create_router
from neckline.k10 import pipeline, store
from neckline.k10.cli import enqueue_scan, frozen_scan_input_sha256, recover_scan
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.schema import initialize_schema
from neckline.k10.sources import SourceCoverage, SourceDocumentInput, SourceFetchResult
from neckline.k10.universe import CompanyMetadata
from neckline.k10.verification import VerificationEvidenceBundle
from neckline.k10.windows import SHANGHAI, evening_cutoff
from neckline.k10.worker import run_once
from tests.k10_v306_fixture import append_approved_execution_profile


DAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=SHANGHAI)
RUN_AT = datetime(2026, 9, 8, 22, 0, tzinfo=SHANGHAI)
_HTTPX_CLIENT = httpx.Client


def _config() -> dict:
    return json.loads((Path(__file__).parents[1] / "neckline/config/k10-v1.4.json").read_text())


def _api(path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(lambda: path, lambda: None, lambda: path.parent / "parquet"))
    return TestClient(app)


class _News:
    coverage = SourceCoverage("tushare-major-news", "market-wide", "fixture", "bounded", "publishedAt", "publishedAt", True)

    def __init__(self, *, token: str, request_bound: int):
        assert token == "fixture-token" and request_bound > 0

    def fetch_incremental(self, request):
        published = request.window.start_at + timedelta(minutes=1)
        return SourceFetchResult(
            documents=(SourceDocumentInput("news-1", None, "供应商称创业板公司可能进入新项目送样阶段。", None,
                                           published, "exact", published + timedelta(minutes=1), "fixture-v1",
                                           {"title": "供应商称项目送样传闻"}),),
            next_cursor="fixture", success_watermark=request.window.cutoff_at,
            pages_fetched=1, pages_expected=1, exhausted=True,
        )


class _Metadata:
    def lookup(self, *, company_code, as_of):
        return CompanyMetadata(company_code, "chinext", False, "801080.SI", as_of)


class _Gateway:
    """A completed empty search is evidence coverage, not an execution error."""
    def __init__(self):
        self.search_paths: list[str] = []

    def fetch(self, **_kwargs):
        path = _kwargs.get("query_path")
        self.search_paths.append(path.path_id)
        return VerificationEvidenceBundle("available", (), (), {"state": "available", "requestState": "completed"})

    def fetch_fulltext(self, **_kwargs):
        return VerificationEvidenceBundle("available", (), (), {"state": "available", "requestState": "completed"})


def _http_transport(monkeypatch, *, malformed_action: str | None = None,
                    malformed_close_round: int | None = None,
                    close_status: str = "ready_for_comparison", initial_query_round: int = 0,
                    title_response: str = "object", body_impact: str | None = None):
    calls: list[str] = []
    query_round = initial_query_round
    close_round = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal query_round, close_round
        wire = json.loads(request.content)
        message = wire["messages"][-1]["content"]
        payload = json.loads(message.split("<untrusted-k10-evidence>\n", 1)[1].split("\n</untrusted-k10-evidence>", 1)[0])
        action = payload.get("action")
        if action:
            calls.append("research:" + action)
            if (malformed_action == action
                    and (action != "close_research" or malformed_close_round is None
                         or close_round + 1 == malformed_close_round)):
                result = {"action": action, "claims": "wrong-shape"}
            elif action == "plan_gaps":
                ref = payload["evidencePacket"]["allowedEvidenceRefs"][0]
                result = {"action": action, "questions": [{"questionId": "q-1", "claimIds": ["article-claim-1"],
                          "companyCodes": ["300001.SZ"], "question": "送样是否获公司确认", "knownEvidence": [ref],
                          "missingEvidence": ["公司确认"], "supportCondition": "公司确认", "refuteCondition": "公司否认",
                          "decisionImpact": "影响主推", "state": "open", "resumeCondition": "出现公司公告"}]}
            elif action == "plan_queries":
                query_round += 1
                result = {"action": action, "queryPaths": [{"pathId": f"path-{query_round}", "questionId": "q-1",
                          "query": "项目 送样 公告" if query_round == 1 else "项目 送样 投资者关系",
                          "intent": "确认送样" if query_round == 1 else "核对项目进展",
                          "targetSource": "公司公告" if query_round == 1 else "投资者关系记录",
                          "newPathReason": "首个路径无结果" if query_round > 1 else "没有已尝试路径",
                          "expectedInformationGain": "确认主体", "expectedJudgmentChange": "改变比较",
                          "state": "planned", "resultSummary": None}]}
            elif action == "assess_evidence":
                result = {"action": action, "evidenceUpdates": [], "fulltextRequests": []}
            elif action == "close_research":
                close_round += 1
                ref = payload["evidencePacket"]["allowedEvidenceRefs"][0]
                mappings = ([{"companyCode": code, "affectedStage": "送样", "relationEvidence": [ref], "inference": {}, "uncertainty": "传闻待核"}
                             for code in ("300001.SZ", "300002.SZ", "300003.SZ")]
                            if close_status == "ready_for_comparison" and close_round > 1 else [])
                result_status = "continue_research" if close_status == "ready_for_comparison" and close_round == 1 else close_status
                result = {"action": action, "conclusion": {"researchStatus": result_status,
                          "eventDisposition": "可比较" if mappings else "待核",
                          "companyMappings": mappings, "companyDispositions": [], "materialGaps": ["传闻未核"],
                          "stopReason": "无新增搜索结果，保留条件化披露", "resumeCondition": "公司确认"}}
            elif action == "compare_companies":
                unverified = {"verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown",
                              "originEvidenceRef": None, "unverifiedReasons": ["独立来源未确认"], "conditionalAnalysis": "仅在公司确认时重估。"}
                ordinary = {"verificationStatus": "partially_supported", "isRumor": False, "originStatus": "unknown",
                            "originEvidenceRef": None, "unverifiedReasons": [], "conditionalAnalysis": None}
                rows = []
                for code, role, rank, disclosure in (("300001.SZ", "primary", 1, unverified),
                                                      ("300002.SZ", "pending", None, ordinary),
                                                      ("300003.SZ", "excluded", None, ordinary)):
                    rows.append({"companyCode": code, "role": role, "rank": rank, "summary": role + " assessment",
                                 "priorityReason": "关系路径", "gap": "待确认", "rankChangeConditions": "公司公告", "twoDayReason": "新增传闻",
                                 "evidenceDisclosure": disclosure})
                ref = payload["evidencePacket"]["allowedEvidenceRefs"][0]
                result = {"action": action,
                          "conclusion": {"summary": "供应商称送样仍未获独立确认。", "evidenceRefs": [ref],
                                         "historicalAssessments": []},
                          "companyAssessments": rows}
            else:
                raise AssertionError(action)
        elif payload.get("operation") == "titleSelectionReview":
            calls.append("titleReview")
            result = {"complete": True, "kept": [{"i": row["i"], "reason": "有实质新增"} for row in payload["items"]], "removed": []}
        elif "articleLimit" in payload:
            calls.append("titleGlobal")
            result = {"selectionComplete": True, "reviewedCount": len(payload["items"]),
                      "selected": [{"i": row["i"], "selectedRank": index + 1, "reason": "入选"}
                                   for index, row in enumerate(payload["items"])], "merged": []}
        elif "items" in payload:
            calls.append("titleBatch")
            result = {"items": [{"i": index, "status": "candidate", "matterKey": "project", "stageKey": "new", "reason": "项目送样"}
                                 for index, _ in enumerate(payload["items"])]}
            if title_response == "array":
                result = result["items"]
            elif title_response == "invalid":
                return httpx.Response(200, json={"choices": [{"message": {"content": "[broken"}, "finish_reason": "stop"}],
                                                "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6}})
        elif isinstance(payload.get("output"), dict) and "kind" in payload["output"]:
            calls.append("classify")
            result = {"kind": "initial", "relatedOpportunityId": None, "reason": "新传闻首次出现",
                      "newFacts": "供应商首次称送样", "changedJudgment": "形成新关注", "twoDayReason": "待公司确认"}
        elif "candidates" in payload and isinstance(payload.get("output"), dict) and "choices" in payload["output"]:
            calls.append("prioritize")
            result = {"choices": [{"canonicalKey": row["canonicalKey"], "companyCode": row["companyCode"]}
                                  for row in payload["candidates"]]}
        else:
            calls.append("understand")
            source_ref = {"documentId": payload["documentId"], "revision": payload["revision"]}
            result = {"events": [{"canonicalKey": "project-delivery", "stageKey": "initial", "eventState": "rumor",
                                    "headline": "项目送样传闻", "eventKind": "rumor", "facts": {},
                                    "sourceRefs": [source_ref],
                                    "claims": [{"claimId": "article-claim-1", "text": "供应商称项目送样", "kind": "rumor",
                                                "novelty": "new_fact", "speaker": "供应商", "subject": "项目", "object": "样品",
                                                "action": "送样", "stageOrCondition": "待确认", "timeText": "本次消息",
                                                "verificationStatus": "unverified", "decisionImpact": "影响比较",
                                                "sourceRef": source_ref, "location": "paragraph:1"}]}],
                      "needsFullText": False}
            if body_impact is not None:
                if body_impact == "repair_facts":
                    if "上次输出未通过校验" not in message:
                        result["events"][0]["facts"] = None
                    else:
                        assert '"field": "events[].facts"' in message
                        assert '"expected": "object"' in message
                elif body_impact == "repair":
                    if "上次输出未通过校验" not in message:
                        result["events"][0]["claims"][0]["decisionImpact"] = ""
                    else:
                        assert '"field": "decisionImpact"' in message
                        assert '"expected": "non_empty_string"' in message
                else:
                    result["events"][0]["claims"][0]["decisionImpact"] = body_impact
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(result)}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6}})

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _HTTPX_CLIENT(**{**kwargs, "transport": transport}))
    return calls


def _run(tmp_path, monkeypatch, *, malformed_action: str | None = None,
         malformed_close_round: int | None = None,
         close_status: str = "ready_for_comparison", title_response: str = "object", body_impact: str | None = None):
    db_path = tmp_path / "b39-e2e.sqlite"
    initialize_schema(db_path)
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES ('SSE', ?, 1)", [
            (DAY.strftime("%Y%m%d"),),
            ((DAY + timedelta(days=1)).strftime("%Y%m%d"),),
            ((DAY + timedelta(days=2)).strftime("%Y%m%d"),),
        ])
    store.set_run_control(state="open", reason_code="fixture", changed_at=NOW.isoformat(), changed_by="test", db_path=db_path)
    config_id, config_revision = "b39", store.append_run_config(config_id="b39", payload=_config(), created_at=NOW.isoformat(), db_path=db_path)
    execution_id, execution_revision = append_approved_execution_profile(db_path=db_path, created_at=NOW.isoformat(), config_id="b39-execution")
    task_id = enqueue_scan(db_path=db_path, kind="evening", trading_day=DAY, config_id=config_id, config_revision=config_revision,
                           execution_config_id=execution_id, execution_config_revision=execution_revision, now=NOW,
                           bootstrap_cutoff=(evening_cutoff(DAY) - timedelta(hours=2)).isoformat())
    calls = _http_transport(monkeypatch, malformed_action=malformed_action,
                            malformed_close_round=malformed_close_round, close_status=close_status,
                            title_response=title_response, body_impact=body_impact)
    provider = MeteredProvider(ledger_db=db_path, ledger_task="discovery", api_key="fixture", model="deepseek-v4-pro",
                               name="fixture", api_url="https://api.deepseek.com/chat/completions", read_timeout=1, use_streaming=False)
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution("configured", provider, "fixture", None))
    monkeypatch.setattr(pipeline, "TuShareMajorNewsAdapter", _News)
    gateway = _Gateway()
    monkeypatch.setattr(pipeline, "TavilyEvidenceGateway", lambda **_: gateway)
    monkeypatch.setattr(pipeline, "SqliteCompanyMetadataProvider", lambda **_: _Metadata())
    task = run_once(db_path=db_path, worker_id="fixture", lease_for=timedelta(minutes=5),
                    handlers=pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path / "parquet"), clock=lambda: RUN_AT)
    return db_path, task_id, task, calls, gateway


def test_cli_worker_real_deepseek_transport_publishes_unverified_primary_and_keeps_full_assessments(tmp_path, monkeypatch):
    db_path, task_id, task, calls, gateway = _run(tmp_path, monkeypatch)
    assert task is not None and task.status == "completed"
    assert {"titleBatch", "titleGlobal", "titleReview", "understand", "research:compare_companies"} <= set(calls)
    assert calls.count("research:plan_queries") == 2
    assert calls.count("research:assess_evidence") == 2
    assert calls.count("research:close_research") >= 2
    assert gateway.search_paths == ["path-1", "path-2"]
    checkpoint = store.task_execution_input(task_id=task_id, db_path=db_path)["checkpoint"]
    candidates = store.list_candidates(scan_id=checkpoint["scanId"], state="offered", db_path=db_path)
    assert [candidate["companyCode"] for candidate in candidates] == ["300001.SZ"]
    with _api(db_path) as client:
        opportunities = client.get("/api/v1/k10/opportunities")
        assert opportunities.status_code == 200
        opportunity = opportunities.json()["items"][0]
        detail = client.get(f"/api/v1/k10/opportunities/{opportunity['opportunityId']}")
        assessments = client.get(f"/api/v1/k10/scans/{checkpoint['scanId']}/assessments")
    assert detail.status_code == 200 and assessments.status_code == 200
    assert detail.json()["samples"][0]["comparison"]["evidenceDisclosure"]["verificationStatus"] == "unverified"
    assert detail.json()["samples"][0]["comparison"]["evidenceDisclosure"]["isRumor"] is True
    assert {(item["companyCode"], item["role"]) for item in assessments.json()["items"]} == {
        ("300001.SZ", "primary"), ("300002.SZ", "pending"), ("300003.SZ", "excluded"),
    }
    assert (opportunity["d1TradeDate"], opportunity["d2TradeDate"]) == ("2026-09-09", "2026-09-10")


def test_cli_worker_malformed_research_output_fails_snapshot_and_never_publishes(tmp_path, monkeypatch):
    db_path, task_id, task, _, _ = _run(tmp_path, monkeypatch, malformed_action="plan_gaps")
    assert task is not None and task.status == "failed"
    execution = store.task_execution_input(task_id=task_id, db_path=db_path)
    assert store.list_candidates(scan_id=execution["checkpoint"].get("scanId", "missing"), state="offered", db_path=db_path) == []
    from neckline.k10.research_store import read_research_snapshot
    scan = store.get_scan(scan_id=execution["checkpoint"]["scanId"], db_path=db_path)
    snapshot_ids = scan["coverage"]["researchSnapshotIds"]
    assert len(snapshot_ids) == 1
    snapshot = read_research_snapshot(snapshot_id=snapshot_ids[0], db_path=db_path)
    assert snapshot is not None
    assert snapshot.execution_status == "failed"
    # The same frozen source input can now take the one controlled recovery
    # route. A B39 semantic failure must not be stranded as a partial scan
    # merely because a worker terminal guard noticed it after publication.
    task_id_recovery = recover_scan(
        db_path=db_path, scan_id=scan["scanId"], execution_config_id="b39-execution", execution_config_revision=1,
        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan["scanId"], db_path=db_path), now=RUN_AT,
    )
    recovery = store.get_task(task_id=task_id_recovery, db_path=db_path)
    assert task_id_recovery == task_id
    assert recovery is not None and recovery.status == "queued"
    recovery_checkpoint = store.task_execution_input(task_id=task_id, db_path=db_path)["checkpoint"]
    assert recovery_checkpoint["recoveryAuthorized"]["frozenInputSha256"] == frozen_scan_input_sha256(
        scan_id=scan["scanId"], db_path=db_path)


def test_cli_worker_completed_empty_search_becomes_pending_without_execution_failure(tmp_path, monkeypatch):
    db_path, task_id, task, calls, _ = _run(tmp_path, monkeypatch, close_status="pending_verification")
    assert task is not None and task.status == "completed"
    execution = store.task_execution_input(task_id=task_id, db_path=db_path)
    assert store.list_candidates(scan_id=execution["checkpoint"]["scanId"], state="offered", db_path=db_path) == []
    assert "research:close_research" in calls and "classify" not in calls
    from neckline.k10.research_store import read_research_snapshot
    scan = store.get_scan(scan_id=execution["checkpoint"]["scanId"], db_path=db_path)
    snapshot = read_research_snapshot(snapshot_id=scan["coverage"]["researchSnapshotIds"][0], db_path=db_path)
    assert snapshot is not None
    assert (snapshot.research_status, snapshot.execution_status) == ("pending_verification", "ok")


def test_cli_worker_authorized_same_task_recovery_reuses_frozen_work_and_retries_only_failed_research_stage(tmp_path, monkeypatch):
    db_path, task_id, first, first_calls, gateway = _run(tmp_path, monkeypatch, malformed_action="close_research")
    assert first is not None and first.status == "failed"
    execution = store.task_execution_input(task_id=task_id, db_path=db_path)
    scan = store.get_scan(scan_id=execution["checkpoint"]["scanId"], db_path=db_path)
    assert scan is not None
    with sqlite3.connect(db_path) as conn:
        before_admissions = conn.execute("SELECT COUNT(*) FROM k10_article_admissions WHERE task_id=?", (task_id,)).fetchone()[0]
        before_fulltext = conn.execute(
            "SELECT COUNT(*) FROM k10_external_attempts WHERE task_id=? AND stage='fullText'", (task_id,)
        ).fetchone()[0]
    assert before_admissions == before_fulltext == 1
    assert gateway.search_paths == ["path-1"]

    recovered_id = recover_scan(
        db_path=db_path, scan_id=scan["scanId"], execution_config_id="b39-execution", execution_config_revision=1,
        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan["scanId"], db_path=db_path), now=RUN_AT,
    )
    assert recovered_id == task_id
    # The resumed deterministic model only sees work that was not durable before
    # the rejected close. Its query counter continues from the persisted path.
    resumed_calls = _http_transport(monkeypatch, initial_query_round=1)
    second = run_once(
        db_path=db_path, worker_id="recovery", lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path / "parquet"),
        clock=lambda: RUN_AT,
    )
    assert second is not None and second.status == "completed"
    assert not ({"titleBatch", "titleGlobal", "titleReview", "understand", "research:plan_gaps"} & set(resumed_calls))
    assert resumed_calls[0] == "research:close_research"  # explicit retry of the known failed action
    assert gateway.search_paths == ["path-1", "path-2"]
    with sqlite3.connect(db_path) as conn:
        after_admissions = conn.execute("SELECT COUNT(*) FROM k10_article_admissions WHERE task_id=?", (task_id,)).fetchone()[0]
        after_fulltext = conn.execute(
            "SELECT COUNT(*) FROM k10_external_attempts WHERE task_id=? AND stage='fullText'", (task_id,)
        ).fetchone()[0]
    assert after_admissions == before_admissions
    assert after_fulltext == before_fulltext
