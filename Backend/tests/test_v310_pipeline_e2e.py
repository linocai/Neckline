"""B39 production-boundary regressions using only deterministic transports."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.api.k10 import create_router
from neckline.k10 import pipeline, store
from neckline.k10.cli import enqueue_scan, frozen_scan_input_sha256, recover_scan, main as cli_main
from neckline.k10.metering import MeteredProvider
from neckline.k10.notifications import initialize_notifications_schema
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


class _FreshSharedNews(_News):
    """A frozen 21:00:16 source used by two related events in one CLI task."""
    def fetch_incremental(self, request):
        fetched = request.window.start_at + timedelta(seconds=16)
        return SourceFetchResult(
            documents=(SourceDocumentInput("news-1", None, "供应商称两个项目均进入送样核实阶段。", None,
                                           request.window.start_at + timedelta(seconds=1), "exact", fetched,
                                           "fixture-shared-v1", {"title": "供应商称项目送样传闻"}),),
            next_cursor="fixture", success_watermark=request.window.cutoff_at,
            pages_fetched=1, pages_expected=1, exhausted=True,
        )


class _ProspectusNews(_News):
    def fetch_incremental(self, request):
        published = request.window.start_at + timedelta(minutes=1)
        return SourceFetchResult(
            documents=(SourceDocumentInput("prospectus-1", None,
                "招股说明书\n发行人声明\n重大事项提示\n募集资金运用\n本文件不构成投资承诺。", None,
                published, "exact", published + timedelta(seconds=16), "fixture-prospectus-v1",
                {"title": "甲公司首次公开发行股票并在创业板上市招股说明书（注册稿）"}),),
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
        self.search_routes: list[dict] = []

    def fetch(self, **_kwargs):
        path = _kwargs.get("query_path")
        self.search_paths.append(path.path_id)
        self.search_routes.append(path.to_dict())
        return VerificationEvidenceBundle("available", (), (), {"state": "available", "requestState": "completed"})

    def fetch_fulltext(self, **_kwargs):
        return VerificationEvidenceBundle("available", (), (), {"state": "available", "requestState": "completed"})


def _http_transport(monkeypatch, *, malformed_action: str | None = None,
                    malformed_close_round: int | None = None,
                    close_status: str = "ready_for_comparison", initial_query_round: int = 0, initial_close_round: int = 0,
                    title_response: str = "object", body_impact: str | None = None, truncate_action: str | None = None,
                    action_shape: str | None = None, evidence_location: str | None = None, pending_ranking: str | None = None, finalization_truncate: str | None = None, v2: bool = False, provider_status: int | None = None, outside_pool: bool = False, failure_action: str | None = None, request_observer=None, require_title_hint=True, invalid_query_target: bool = False, shared_same_source_events: bool = False, initial_source_routes: bool = False):
    calls: list[str] = []
    query_round = initial_query_round
    close_round = initial_close_round

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal query_round, close_round
        if request_observer is not None:
            request_observer(request)
        wire = json.loads(request.content)
        message = wire["messages"][-1]["content"]
        payload = json.loads(message.split("<untrusted-k10-evidence>\n", 1)[1].split("\n</untrusted-k10-evidence>", 1)[0])
        action = payload.get("action")
        if provider_status and 'http_'+str(provider_status) not in calls and ((failure_action is None and not calls) or action == failure_action):
            calls.append('http_'+str(provider_status))
            return httpx.Response(provider_status,headers={'Retry-After':'0'},json={'error':{'message':'synthetic failure'}})

        if v2 and action:
            scope = payload['evidencePacket']['companyScope']
            if payload['evidencePacket'].get('contextProtocol'):
                assert 'fixedPool' not in scope
                assert scope['profileSnapshotId'] == 'k10-v2-profiles-20260909'
            else:
                assert len(scope['fixedPool']) == 1089
            if require_title_hint:
                assert '300002.SZ' in scope['candidateCompanyCodes']
                assert scope['companyProfiles']
            if 'fixedPool' in scope:
                assert set(scope['candidateCompanyCodes']) <= {row['companyCode'] for row in scope['fixedPool']}
            assert all(row['review_status']=='local_draft_awaiting_user' for row in scope['companyProfiles'])
            assert all('evidence' not in row and 'notes' not in row for row in scope['companyProfiles'])
        if action:
            calls.append("research:" + action)
            if (malformed_action == action
                    and (action not in {"close_research", "assess_and_decide"} or malformed_close_round is None
                         or close_round + 1 == malformed_close_round)):
                result = {"action": action, "claims": "wrong-shape"}
            elif action == "research_round":
                # Current producer tasks receive a direct result. Historical
                # action fixtures below remain for isolated legacy decoders;
                # do not manufacture old stage calls on the current wire.
                ref = payload["evidencePacket"]["allowedEvidenceRefs"][0]
                mappings = ([{"companyCode": code, "affectedStage": "送样", "relationEvidence": [ref],
                              "inference": {}, "uncertainty": "传闻待核"}
                             for code in ("300001.SZ", "300002.SZ", "300003.SZ")]
                            if close_status == "ready_for_comparison" or pending_ranking else [])
                unverified = {"verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown",
                    "originEvidenceRef": None, "unverifiedReasons": ["独立来源未确认"], "conditionalAnalysis": "仅在公司确认时重估。"}
                ordinary = {"verificationStatus": "partially_supported", "isRumor": False, "originStatus": "unknown",
                    "originEvidenceRef": None, "unverifiedReasons": [], "conditionalAnalysis": None}
                rows = [{"companyCode": code, "role": role, "rank": rank, "summary": role + " assessment",
                         "priorityReason": "关系路径", "gap": "待确认", "rankChangeConditions": "公司公告",
                         "twoDayReason": "新增传闻", "evidenceDisclosure": disclosure}
                        for code, role, rank, disclosure in (
                            ("300001.SZ", "pending" if pending_ranking == "pending" else "primary",
                             None if pending_ranking == "pending" else 1, unverified),
                            ("300002.SZ", "pending", None, ordinary), ("300003.SZ", "excluded", None, ordinary))] if mappings else []
                result = {"action": action, "conclusion": {
                    "researchStatus": "pending_verification" if pending_ranking else close_status,
                    "companyMappings": mappings, "materialGaps": ["传闻未核"],
                    "stopReason": "现有资料足以比较，保留条件化披露", "resumeCondition": "公司确认"},
                    "comparison": {"summary": "供应商称送样仍未获独立确认。", "evidenceRefs": [ref],
                                   "historicalAssessments": []} if mappings else None,
                    "companyAssessments": rows}
            elif action == "plan_gaps":
                ref = payload["evidencePacket"]["allowedEvidenceRefs"][0]
                claim_id = payload["evidencePacket"]["claims"][0]["claimId"]
                result = {"action": action, "questions": [{"questionId": "q-1", "claimIds": [claim_id],
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
                          "purposeKind": "event_fact", "targetRefs": [{"kind": "claim", "claimId": payload["evidencePacket"]["claims"][0]["claimId"]}],
                          "state": "planned", "resultSummary": None}]}
                if invalid_query_target:
                    result["queryPaths"][0]["targetRefs"] = [{"kind": "claim", "claimId": "not-this-question"}]
            elif action == "plan_research":
                # An envelope correction repeats this plan, not the next path.
                if not (action_shape == "wrong_repair" and calls.count("research:" + action) == 2):
                    query_round += 1
                ref = payload["evidencePacket"]["allowedEvidenceRefs"][0]
                claim_id = payload["evidencePacket"]["claims"][0]["claimId"]
                result = {"action": action, "questions": [{"questionId": "q-1", "claimIds": [claim_id],
                          "companyCodes": ["300001.SZ"], "question": "送样是否获公司确认", "knownEvidence": [ref],
                          "missingEvidence": ["公司确认"], "supportCondition": "公司确认", "refuteCondition": "公司否认",
                          "decisionImpact": "影响主推", "state": "open", "resumeCondition": "出现公司公告"}],
                          "queryPaths": [{"pathId": f"path-{query_round}", "questionId": "q-1",
                          "query": "项目 送样 公告", "intent": "确认送样", "targetSource": "公司公告",
                          "newPathReason": "没有已尝试路径", "expectedInformationGain": "确认主体",
                          "expectedJudgmentChange": "改变比较", "purposeKind": "event_fact",
                          "targetRefs": [{"kind": "claim", "claimId": claim_id}], "state": "planned", "resultSummary": None}]}
                if initial_source_routes:
                    result["queryPaths"].append({"pathId": f"path-{query_round}-industry", "questionId": "q-1",
                        "query": "site:industry.example 项目 送样", "intent": "确认送样",
                        "targetSource": "行业媒体", "newPathReason": "首批独立来源",
                        "expectedInformationGain": "确认主体", "expectedJudgmentChange": "改变比较",
                        "purposeKind": "event_fact", "targetRefs": [{"kind": "claim", "claimId": claim_id}],
                        "state": "planned", "resultSummary": None})
                    # Same-source wording is not another first-plan route;
                    # the decoder must retain only the two genuinely distinct
                    # declarations before either search is consumed.
                    result["queryPaths"].append({"pathId": f"path-{query_round}-announcement-reword", "questionId": "q-1",
                        "query": "项目最新送样披露", "intent": "确认送样",
                        "targetSource": "公司公告", "newPathReason": "同来源措辞改写",
                        "expectedInformationGain": "确认主体", "expectedJudgmentChange": "改变比较",
                        "purposeKind": "event_fact", "targetRefs": [{"kind": "claim", "claimId": claim_id}],
                        "state": "planned", "resultSummary": None})
                if invalid_query_target:
                    result["queryPaths"][0]["targetRefs"] = [{"kind": "claim", "claimId": "not-this-question"}]
            elif action == "assess_evidence":
                result = {"action": action, "evidenceUpdates": [], "fulltextRequests": []}
                if evidence_location is not None:
                    count = calls.count("research:assess_evidence")
                    if count == 2 and evidence_location == "repair":
                        assert "evidenceUpdates[].location" in message and "non_empty_string" in message
                    result["evidenceUpdates"] = [{"claimId": payload["evidencePacket"]["claims"][0]["claimId"],
                        "sourceRef": payload["evidencePacket"]["allowedEvidenceRefs"][0],
                        "relation": "irrelevant", "location": "excerpt" if count > 1 and evidence_location == "repair" else "", "applicability": {}}]
            elif action == "close_research":
                close_round += 1
                ref = payload["evidencePacket"]["allowedEvidenceRefs"][0]
                mappings = ([{"companyCode": code, "affectedStage": "送样", "relationEvidence": [ref], "inference": {}, "uncertainty": "传闻待核"}
                             for code in ("300001.SZ", "300002.SZ", "300003.SZ")]
                            if pending_ranking or (close_status == "ready_for_comparison" and close_round > 1) else [])
                result_status = "continue_research" if close_status == "ready_for_comparison" and close_round == 1 else close_status
                if pending_ranking:
                    result_status = "pending_verification"
                result = {"action": action, "conclusion": {"researchStatus": result_status,
                          "eventDisposition": "可比较" if mappings else "待核",
                          "companyMappings": mappings, "companyDispositions": [], "materialGaps": ["传闻未核"],
                          "stopReason": "无新增搜索结果，保留条件化披露", "resumeCondition": "公司确认"}}
            elif action == "assess_and_decide":
                # A length repair reuses the unfinished same semantic decision;
                # it is not a fresh post-search decision.  Keeping the fixture
                # state stable here verifies that the repair does not silently
                # consume the next route/closure slot.
                concise_repair = (action == truncate_action
                                  and calls.count("research:" + action) == 2
                                  and "上次达到输出长度限制" in message)
                if evidence_location == "repair" and calls.count("research:" + action) == 2:
                    assert "evidenceUpdates[].location" in message and "non_empty_string" in message
                    concise_repair = True
                if not concise_repair:
                    close_round += 1
                ref = payload["evidencePacket"]["allowedEvidenceRefs"][0]
                claim_id = payload["evidencePacket"]["claims"][0]["claimId"]
                continue_research = close_status == "ready_for_comparison" and close_round == 1 and not payload["evidencePacket"].get("pathsExhausted")
                if continue_research:
                    if not concise_repair:
                        query_round += 1
                    paths = [{"pathId": f"path-{query_round}", "questionId": "q-1",
                        "query": "项目 送样 投资者关系", "intent": "核对项目进展", "targetSource": "投资者关系记录",
                        "newPathReason": "首个路径无结果", "expectedInformationGain": "确认主体",
                        "expectedJudgmentChange": "改变比较", "purposeKind": "event_fact",
                        "targetRefs": [{"kind": "claim", "claimId": claim_id}], "state": "planned", "resultSummary": None}]
                else:
                    paths = []
                mappings = ([{"companyCode": code, "affectedStage": "送样", "relationEvidence": [ref], "inference": {}, "uncertainty": "传闻待核"}
                             for code in ("300001.SZ", "300002.SZ", "300003.SZ")]
                            if pending_ranking or (close_status == "ready_for_comparison" and not continue_research) else [])
                result_status = "continue_research" if continue_research else close_status
                if pending_ranking:
                    result_status = "pending_verification"
                result = {"action": action, "claims": [], "questions": [], "evidenceUpdates": [], "fulltextRequests": [],
                          "queryPaths": paths, "conclusion": {"researchStatus": result_status,
                          "eventDisposition": "可比较" if mappings else "待核", "companyMappings": mappings,
                          "companyDispositions": [], "materialGaps": ["传闻未核"],
                          "stopReason": "无新增搜索结果，保留条件化披露", "resumeCondition": "公司确认"}}
                if evidence_location is not None:
                    count = calls.count("research:" + action)
                    result["evidenceUpdates"] = [{"claimId": claim_id, "sourceRef": ref,
                        "relation": "irrelevant", "location": "excerpt" if count > 1 and evidence_location == "repair" else "", "applicability": {}}]
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
                if pending_ranking and (pending_ranking == "pending" or calls.count("research:compare_companies") > 1):
                    if pending_ranking == "repair":
                        assert "companyAssessments[].role" in message and "pending|excluded" in message
                    rows[0] = {**rows[0], "role":"pending", "rank":None}
                result = {"action": action,
                          "conclusion": {"summary": "供应商称送样仍未获独立确认。", "evidenceRefs": [ref],
                                         "historicalAssessments": []},
                          "companyAssessments": rows}
            else:
                raise AssertionError(action)
        elif payload.get("operation") == "titleSelectionReview":
            calls.append("titleReview")
            result = {"complete": True, "kept": [{"i": row["i"], "reason": "有实质新增"} for row in payload["items"]], "removed": []}
        elif "inputCount" in payload:
            calls.append("titleGlobal")
            result = {"selectionComplete": True, "reviewedCount": len(payload["items"]),
                      "selected": [{"i": row["i"], "selectedRank": index + 1, "reason": "入选"}
                                   for index, row in enumerate(payload["items"])], "merged": []}
        elif "items" in payload:
            calls.append("titleBatch")
            result = {"items": [{"i": index, "status": "candidate", "matterKey": "project", "stageKey": "new", "reason": "项目送样", **({"companyCodes":["300002.SZ"]} if v2 else {})}
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
            if shared_same_source_events:
                first = result["events"][0]
                second_claim = {**first["claims"][0], "claimId": "article-claim-2", "text": "供应商称第二项目送样待确认",
                                "subject": "第二项目", "action": "送样"}
                result["events"].append({**first, "canonicalKey": "project-delivery-b", "headline": "第二项目送样传闻",
                                         "facts": {"sharedFixture": True}, "claims": [second_claim]})
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
        if action in {"plan_gaps", "plan_research"} and action_shape:
            assert f'本次根字段 action 必须严格为 "{action}"' in message.split("<untrusted-k10-evidence>", 1)[0]
            if action_shape == "omitted":
                result.pop("action")
            elif action_shape == "wrapped":
                result = {"outputContract": result}
            elif action_shape == "wrong_repair":
                if calls.count("research:" + action) == 1:
                    result["action"] = "plan_queries"
                else:
                    assert '"field": "action"' in message and f'"allowed": ["{action}"]' in message
        truncated = (action == truncate_action and calls.count("research:" + str(action)) == 1) or (finalization_truncate is not None and calls[-1] == finalization_truncate and calls.count(finalization_truncate) == 1)
        if action == truncate_action and calls.count("research:" + str(action)) == 2:
            assert "上次达到输出长度限制" in message
            assert "增量变化" in message
        if v2 and action:
            result = json.loads(json.dumps(result).replace("300003.SZ","300005.SZ").replace("300002.SZ","300004.SZ").replace("300001.SZ","300002.SZ"))
        if outside_pool and action in {"plan_gaps", "plan_research"}:
            result["questions"][0]["companyCodes"] = ["600000.SH"]
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(result)}, "finish_reason": "length" if truncated else "stop"}],
                                         "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6}})

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _HTTPX_CLIENT(**{**kwargs, "transport": transport}))
    return calls


def _run(tmp_path, monkeypatch, *, malformed_action: str | None = None,
         malformed_close_round: int | None = None,
         close_status: str = "ready_for_comparison", title_response: str = "object", body_impact: str | None = None,
         truncate_action: str | None = None, action_shape: str | None = None, evidence_location: str | None = None, pending_ranking: str | None = None, finalization_truncate: str | None = None, v2: bool = False, provider_status: int | None = None, outside_pool: bool = False, failure_action: str | None = None, provider_setup=None, request_observer=None, require_title_hint=True, cli_entry: bool = False, invalid_query_target: bool = False, shared_same_source_events: bool = False, news_adapter=None, initial_source_routes: bool = False):
    monkeypatch.setattr(pipeline, "_now", lambda: RUN_AT)
    db_path = tmp_path / "b39-e2e.sqlite"
    initialize_schema(db_path)
    initialize_notifications_schema(db_path)
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE trade_cal(exchange TEXT, cal_date TEXT, is_open INTEGER)")
        conn.executemany("INSERT INTO trade_cal VALUES ('SSE', ?, 1)", [
            (DAY.strftime("%Y%m%d"),),
            ((DAY + timedelta(days=1)).strftime("%Y%m%d"),),
            ((DAY + timedelta(days=2)).strftime("%Y%m%d"),),
        ])
    store.set_run_control(state="open", reason_code="fixture", changed_at=NOW.isoformat(), changed_by="test", db_path=db_path)
    config_id, config_revision = "b39", store.append_run_config(config_id="b39", payload=(json.loads((Path(__file__).parents[1]/"neckline/config/k10-v2.json").read_text()) if v2 else _config()), created_at=NOW.isoformat(), db_path=db_path)
    execution_id, execution_revision = append_approved_execution_profile(db_path=db_path, created_at=NOW.isoformat(), config_id="b39-execution")
    if v2:
        from neckline.k10.v2_profiles import import_profiles, UNIVERSE_ID, PROFILES_ID
        from neckline.k10.v2_store import bind_strategy
        source=Path('/Users/linotsai/Lino/whynotme')
        import_profiles(universe_file=source/'research/K10-v2初始股票池_20260909.json',profiles_dir=source/'artifacts/output/k10-company-profiles-v2-20260909',
            db_path=db_path,confirmed_target=db_path,universe_id=UNIVERSE_ID,profiles_id=PROFILES_ID,imported_at=NOW.isoformat())
        bind_strategy(db_path=db_path,snapshot_id='k10-v2-20260909',config_id=config_id,config_revision=config_revision,
            execution_config_id=execution_id,execution_config_revision=execution_revision,created_at=NOW.isoformat())
    if cli_entry:
        output = StringIO()
        with redirect_stdout(output):
            assert cli_main([
                "enqueue", "--db", str(db_path), "--kind", "evening", "--trading-day", DAY.isoformat(),
                "--config-id", config_id, "--config-revision", str(config_revision),
                "--execution-config-id", execution_id, "--execution-config-revision", str(execution_revision),
                "--bootstrap-cutoff", (evening_cutoff(DAY) - timedelta(hours=2)).isoformat(),
            ]) == 0
        # The production CLI intentionally prints the task ID as a single
        # plain line on successful enqueue (scheduled pause is structured
        # JSON).  This test derives the worker identity from that real output.
        task_id = output.getvalue().strip()
        assert task_id.startswith("task_")
    else:
        task_id = enqueue_scan(db_path=db_path, kind="evening", trading_day=DAY, config_id=config_id, config_revision=config_revision,
                               execution_config_id=execution_id, execution_config_revision=execution_revision, now=NOW,
                               bootstrap_cutoff=(evening_cutoff(DAY) - timedelta(hours=2)).isoformat())
    calls = _http_transport(monkeypatch, malformed_action=malformed_action,
                            malformed_close_round=malformed_close_round, close_status=close_status,
                            title_response=title_response, body_impact=body_impact, truncate_action=truncate_action,
                            action_shape=action_shape, evidence_location=evidence_location, pending_ranking=pending_ranking, finalization_truncate=finalization_truncate, v2=v2, provider_status=provider_status, outside_pool=outside_pool, failure_action=failure_action, request_observer=request_observer, require_title_hint=require_title_hint, invalid_query_target=invalid_query_target, shared_same_source_events=shared_same_source_events, initial_source_routes=initial_source_routes)
    provider = MeteredProvider(ledger_db=db_path, ledger_task="discovery", api_key="fixture", model="deepseek-flash",
                               name="fixture", api_url="https://api.deepseek.com/chat/completions", read_timeout=1, use_streaming=False)
    provider.max_attempts = 1
    if provider_setup is None:
        monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution("configured", provider, "fixture", None))
    else:
        provider_setup(db_path)
    monkeypatch.setattr(pipeline, "TuShareMajorNewsAdapter", news_adapter or _News)
    gateway = _Gateway()
    monkeypatch.setattr(pipeline, "TavilyEvidenceGateway", lambda **_: gateway)
    monkeypatch.setattr(pipeline, "SqliteCompanyMetadataProvider", lambda **_: _Metadata())
    task = run_once(db_path=db_path, worker_id="fixture", lease_for=timedelta(minutes=5),
                    handlers=pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path / "parquet",
                                                          now=lambda: RUN_AT), clock=lambda: RUN_AT)
    return db_path, task_id, task, calls, gateway


def test_cli_worker_real_deepseek_transport_publishes_unverified_primary_and_keeps_full_assessments(tmp_path, monkeypatch):
    db_path, task_id, task, calls, gateway = _run(tmp_path, monkeypatch)
    assert task is not None and task.status == "completed"
    assert {"titleBatch", "titleGlobal", "understand", "research:research_round"} <= set(calls)
    assert calls.count("research:research_round") == 1
    assert not ({"titleReview", "research:plan_research", "research:assess_and_decide",
                 "research:compare_companies"} & set(calls))
    assert gateway.search_paths == []
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


def _direct_query_fixture(monkeypatch, *, invalid=False, empty=False):
    from neckline.k10.discovery import DiscoveryDocument
    from tests.test_b60_pool_filtering import edit_responses
    from tests.test_b72_query_path_resilience import path
    edited = []
    def edit(value):
        if value.get('action') != 'research_round' or edited: return
        edited.append(True)
        ref = value['comparison']['evidenceRefs'][0]
        question = {'questionId':'q-1','claimIds':['article-claim-1'],'companyCodes':['300002.SZ'],
            'question':'当前送样是否已获确认','knownEvidence':[ref],'missingEvidence':['公司确认'],
            'supportCondition':'公司确认','refuteCondition':'公司否认','decisionImpact':'影响比较',
            'state':'open','resumeCondition':'新公告'}
        route = path('necessary-query','q-1',[{'kind':'claim',
            'claimId':'unknown-claim' if invalid else 'article-claim-1'}])
        route['purposeKind'] = 'event_fact'
        value.clear(); value.update(action='research_round',questions=[question],queryPaths=[route],
            conclusion={'researchStatus':'continue_research','companyMappings':[],
                'stopReason':'需确认来源事实','resumeCondition':'新资料'})
    edit_responses(monkeypatch,edit)
    scopes = []
    original = _Gateway.fetch
    def fetch(self, **kwargs):
        route = kwargs['query_path']; scopes.append(route.to_dict())
        bundle = original(self, **kwargs)
        if empty: return bundle
        doc = DiscoveryDocument('scope-check',1,'2026-09-08T12:00:00Z','2026-09-08T12:30:00Z',
            '公司公告：项目仍在送样阶段，尚未确认订单。','项目仍在送样阶段，尚未确认订单。',{'title':'项目进度'})
        return VerificationEvidenceBundle('available',(doc,),(doc,),{'state':'available','requestState':'completed'})
    monkeypatch.setattr(_Gateway,'fetch',fetch)
    return scopes


def test_real_cli_binds_new_query_to_question_scope_before_one_search(tmp_path, monkeypatch):
    scopes = _direct_query_fixture(monkeypatch)
    db_path, task_id, task, calls, gateway = _run(tmp_path, monkeypatch, v2=True, cli_entry=True)
    assert task is not None and task.status == "completed"
    assert_search_routes(gateway, [('necessary-query', 'Company notice', 'q-1')])
    assert calls.count('research:research_round') == 2
    assert len(scopes) == 1
    row = scopes[0]
    assert row['purposeKind'] == 'event_fact'
    assert row['targetRefs'] == [{'kind':'claim','claimId':'article-claim-1'}]
    assert row['questionScope']['questionId'] == row['questionId']
    assert row['questionScope']['scopeSha256']
    with sqlite3.connect(db_path) as conn:
        receipts = [json.loads(row[0]) for row in conn.execute('SELECT tool_evidence_json FROM k10_research_round_results')]
    assert any(item.get('questionScope') == row['questionScope'] for receipt in receipts for item in receipt)


def test_real_cli_rejects_out_of_question_query_before_tavily(tmp_path, monkeypatch):
    scopes = _direct_query_fixture(monkeypatch, invalid=True)
    db_path, task_id, task, calls, gateway = _run(tmp_path, monkeypatch, v2=True, cli_entry=True)
    assert task is not None and task.status == 'completed'
    assert calls.count('research:research_round') == 1
    assert scopes == gateway.search_paths == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_external_attempts WHERE stage='search'").fetchone() == (0,)
        rows = conn.execute(
            'SELECT r.result_json,r.tool_evidence_json,s.execution_status FROM k10_research_round_results r '
            'JOIN k10_research_snapshot_revisions s USING(snapshot_id,revision)'
        ).fetchall()
        assert len(rows) == 1
        failed_round = json.loads(rows[0][0])
        assert failed_round['safeErrorCode'] == 'investigation_path_scope_invalid'
        assert failed_round['comparison'] is None and failed_round['companyAssessments'] == []
        assert json.loads(rows[0][1]) == [] and rows[0][2] == 'failed'
        # Keep the exact paid reply; a local semantic refusal never manufactures a retry.
        assert conn.execute("SELECT status,network_attempt_count FROM k10_execution_item_checkpoints WHERE stage='model:investigation_research_round'").fetchall() == [('completed',1)]
    from neckline.k10.v2_store import read_report
    report = read_report(db_path=db_path)
    assert report['status'] == 'partial' and report['availableAt'] and not report['eveningCards']
    assert any(gap['reasonCode'] == 'investigation_path_scope_invalid' for gap in report['delivery']['gaps'])


def test_real_cli_second_same_task_event_receives_fresh_shared_fact_on_wire(tmp_path, monkeypatch):
    packets = []
    def observe(request):
        wire = json.loads(request.content)
        message = wire["messages"][-1]["content"]
        payload = json.loads(message.split("<untrusted-k10-evidence>\n", 1)[1].split("\n</untrusted-k10-evidence>", 1)[0])
        if payload.get("action") == "research_round":
            packets.append(payload["evidencePacket"])
    db_path, task_id, task, _, _ = _run(
        tmp_path, monkeypatch, v2=True, cli_entry=True, news_adapter=_FreshSharedNews,
        shared_same_source_events=True, request_observer=observe, close_status="pending_verification")
    assert task is not None and task.status == "completed"
    assert len(packets) >= 2
    receiving = next(packet for packet in packets
                     if packet["event"]["canonicalKey"] == "project-delivery-b")
    shared = receiving["reusableSourceEvidence"]["claims"]
    assert shared and shared[0]["text"] == "供应商称项目送样"
    assert datetime.fromisoformat(shared[0]["sourceTiming"]["fetchedAt"]).astimezone(SHANGHAI).strftime("%H:%M:%S") == "21:00:16"
    with sqlite3.connect(db_path) as conn:
        fetched = conn.execute("SELECT fetched_at FROM k10_source_document_versions WHERE document_id LIKE 'doc_%'").fetchone()[0]
    assert datetime.fromisoformat(fetched).astimezone(SHANGHAI).strftime("%H:%M:%S") == "21:00:16"
    assert store.task_execution_input(task_id=task_id, db_path=db_path)["checkpoint"]["scanId"]


def test_real_cli_selected_prospectus_checkpoints_exclusion_without_model_body_read(tmp_path, monkeypatch):
    db_path, task_id, task, calls, _ = _run(
        tmp_path, monkeypatch, cli_entry=True, news_adapter=_ProspectusNews)
    assert task is not None and task.status == "completed"
    assert {"titleBatch", "titleGlobal"} <= set(calls)
    assert "understand" not in calls and not any(call.startswith("research:") for call in calls)
    with sqlite3.connect(db_path) as conn:
        admissions = conn.execute(
            "SELECT admission_kind,state,reason_code FROM k10_v2_article_admissions WHERE task_id=?", (task_id,)).fetchall()
    assert admissions == [("selected", "completed", None)]
    understand_rows = store.completed_execution_items(task_id=task_id, item_kind="document", stage="understand", db_path=db_path)
    assert understand_rows and understand_rows[0]["result"]["materialAdmission"]["reason"] == "prospectus_document"
    assert understand_rows[0]["result"]["events"] == [] and understand_rows[0]["result"]["fullTextUsed"] is False


def test_cli_worker_malformed_research_output_fails_snapshot_and_never_publishes(tmp_path, monkeypatch):
    db_path, task_id, task, _, _ = _run(tmp_path, monkeypatch, malformed_action="research_round", v2=True, cli_entry=True)
    assert task is not None and task.status == "completed"
    execution = store.task_execution_input(task_id=task_id, db_path=db_path)
    assert store.list_candidates(scan_id=execution["checkpoint"].get("scanId", "missing"), state="offered", db_path=db_path) == []
    from neckline.k10.research_store import read_research_snapshot
    scan = store.get_scan(scan_id=execution["checkpoint"]["scanId"], db_path=db_path)
    snapshot_ids = scan["coverage"]["researchSnapshotIds"]
    assert len(snapshot_ids) == 1
    snapshot = read_research_snapshot(snapshot_id=snapshot_ids[0], db_path=db_path)
    assert snapshot is not None
    assert snapshot.execution_status == "failed"
    # A settled event-local model failure is disclosed once. A published
    # partial task cannot be reopened to renew its model correction budget.
    assert task.status == "completed"
    from neckline.k10.v2_store import read_report
    report = read_report(db_path=db_path)
    assert report['status'] == 'partial' and report['coverageGaps'] and not report['eveningCards']
    with pytest.raises(RuntimeError, match='只有当前 failed 或 not_configured'):
        recover_scan(db_path=db_path, scan_id=scan['scanId'], execution_config_id='b39-execution',
            execution_config_revision=1, confirmed_input_sha256=frozen_scan_input_sha256(
                scan_id=scan['scanId'], db_path=db_path), now=RUN_AT)


def test_cli_worker_completed_empty_search_becomes_pending_without_execution_failure(tmp_path, monkeypatch):
    _direct_query_fixture(monkeypatch, empty=True)
    db_path, task_id, task, calls, gateway = _run(tmp_path, monkeypatch, v2=True, cli_entry=True)
    assert task is not None and task.status == "completed"
    execution = store.task_execution_input(task_id=task_id, db_path=db_path)
    assert store.list_candidates(scan_id=execution["checkpoint"]["scanId"], state="offered", db_path=db_path) == []
    assert calls.count("research:research_round") == 1 and "classify" not in calls
    assert_search_routes(gateway, [('necessary-query', 'Company notice', 'q-1')])
    from neckline.k10.research_store import read_research_snapshot
    scan = store.get_scan(scan_id=execution["checkpoint"]["scanId"], db_path=db_path)
    snapshot = read_research_snapshot(snapshot_id=scan["coverage"]["researchSnapshotIds"][0], db_path=db_path)
    assert snapshot is not None
    assert (snapshot.research_status, snapshot.execution_status) == ("pending_verification", "ok")


def test_real_cli_worker_replays_committed_plan_receipt_after_checkpoint_interruption(tmp_path, monkeypatch):
    """A B76 worker recovery must use the exact paid reply, never send another POST."""
    original = store.record_execution_checkpoint
    interrupted = False

    def interrupt_after_receipt(**kwargs):
        nonlocal interrupted
        if (kwargs.get("stage") == "model:investigation_research_round"
                and kwargs.get("status") == "completed" and not interrupted):
            interrupted = True
            raise sqlite3.OperationalError("fixture interruption after committed model receipt")
        return original(**kwargs)

    monkeypatch.setattr(store, "record_execution_checkpoint", interrupt_after_receipt)
    db_path, task_id, first, first_calls, _ = _run(tmp_path, monkeypatch, v2=True, cli_entry=True)
    assert interrupted is True
    assert first is not None and first.status == "failed"
    with sqlite3.connect(db_path) as conn:
        before = conn.execute(
            "SELECT a.attempt_id,r.payload_sha256 FROM k10_external_attempts a "
            "JOIN k10_model_response_receipts r ON r.attempt_id=a.attempt_id "
            "WHERE a.task_id=? AND a.stage='investigation'", (task_id,)
        ).fetchall()
        assert len(before) == 1
    monkeypatch.setattr(store, "record_execution_checkpoint", original)

    # The production recovery entry keeps the CLI-created task and frozen
    # binding.  It is the only path that can resume a failed research snapshot.
    execution = store.task_execution_input(task_id=task_id, db_path=db_path)
    scan = store.get_scan(scan_id=execution["checkpoint"]["scanId"], db_path=db_path)
    assert scan is not None
    recovered_id = recover_scan(
        db_path=db_path, scan_id=scan["scanId"], execution_config_id="b39-execution",
        execution_config_revision=1,
        confirmed_input_sha256=frozen_scan_input_sha256(scan_id=scan["scanId"], db_path=db_path),
        now=RUN_AT,
    )
    assert recovered_id == task_id
    resumed_calls = _http_transport(monkeypatch)
    second = run_once(
        db_path=db_path, worker_id="receipt-recovery", lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path / "parquet",
                                              now=lambda: RUN_AT),
        clock=lambda: RUN_AT,
    )
    assert second is not None and second.status == "completed"
    assert "research:research_round" not in resumed_calls
    with sqlite3.connect(db_path) as conn:
        after = conn.execute(
            "SELECT a.attempt_id,r.payload_sha256 FROM k10_external_attempts a "
            "JOIN k10_model_response_receipts r ON r.attempt_id=a.attempt_id "
            "WHERE a.task_id=? AND a.stage='investigation' ORDER BY a.rowid", (task_id,)
        ).fetchall()
        checkpoint = conn.execute(
            "SELECT status,attempt_count,network_attempt_count FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND stage='model:investigation_research_round'", (task_id,)
        ).fetchone()
    assert after[0] == before[0]
    assert checkpoint == ("completed", 1, 1)


# Settled malformed-event recovery is intentionally retired: the partial report
# is final. The malformed-result test above enforces this, and the preceding
# paid-receipt interruption test preserves same-task/frozen-input recovery.


def assert_search_routes(gateway, expected):
    """Assert actual paid-boundary semantics; execution IDs are system-generated."""
    assert [(row['query'], row['targetSource'], row['questionId']) for row in gateway.search_routes] == expected
    assert gateway.search_paths == [row['pathId'] for row in gateway.search_routes]
    assert len(set(gateway.search_paths)) == len(expected)
    assert all(row['questionScope']['questionId'] == row['questionId']
               and row['questionScope']['scopeSha256'] and row['targetRefs']
               for row in gateway.search_routes)
