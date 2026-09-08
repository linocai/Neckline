from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import sqlite3

import httpx
import pytest

from neckline.k10 import store
from neckline.k10.schema import initialize_schema
from neckline.k10.verification import TavilyEvidenceGateway
from neckline.k10.verification_checkpoints import VerificationCheckpointError
from neckline.search.tavily import TavilySearchClient, TavilyExtractResponse
from tests.test_k10_verification import _bound_task, _event, _Search, NOW, COMPLETED_AT


QUESTION = {"questionId": "q-stage", "question": "公告表示资格入围还是已签订单？"}
PATH = {"questionId": "q-stage", "pathId": "path-announcement", "query": "项目 资格 公示 原始公告",
        "intent": "查明项目阶段", "targetSource": "采购公告原始发布方",
        "newPathReason": "现有报道只写入围，首次查看原始公告",
        "expectedInformationGain": "阶段和条件", "expectedJudgmentChange": "区分未来预期与已经签约"}


def _frozen(path, task_id, count, limit=40):
    refs = [{"documentId": f"frozen-{i}", "revision": 1} for i in range(count)]
    policy = store.read_title_triage_policy(policy_id=f"{task_id}-execution-policy", revision=1, db_path=path)
    store.freeze_title_triage_manifest(task_id=task_id, input_manifest_sha256=store._hash(refs),
        window_kind="morning" if limit == 40 else "evening", policy_id=policy["policyId"], policy_revision=1,
        policy_content_sha256=policy["contentSha256"], article_limit=limit, input_refs=refs,
        batch_count=1, title_status="frozen", created_at=NOW.isoformat(), db_path=path)
    for i, ref in enumerate(refs):
        store.record_title_triage_item(task_id=task_id, document_id=ref["documentId"], revision=1,
            batch_index=0, disposition="candidate", matter_key=ref["documentId"], merged_ref=None,
            selection_rank=i+1, audit_reason="新事实", created_at=NOW.isoformat(), db_path=path)
    store.freeze_title_selection_manifest(task_id=task_id, selection_manifest_sha256=store._hash(refs),
        selected_refs=refs, created_at=NOW.isoformat(), db_path=path)


class _SearchExtract(_Search):
    def __init__(self, path=None):
        super().__init__()
        self.extract_calls = 0
        self.path = path
        self.urls = []

    def extract(self, url):
        self.extract_calls += 1
        self.urls.append(url)
        if self.path:
            with sqlite3.connect(self.path) as conn:
                assert conn.execute("SELECT COUNT(*) FROM k10_article_admissions WHERE admission_kind='tavily_full_article'").fetchone()[0] == 1
        return TavilyExtractResponse(True, url, "原始公示：仅取得入围资格，尚未签约或形成订单。", 1, "extract-1")


def _gateway(path, client):
    initialize_schema(path)
    task = _bound_task(path)
    return TavilyEvidenceGateway(db_path=path, client=client, clock=lambda: COMPLETED_AT,
        task_id=task, network_max_attempts=2), task


def _request(document):
    return {"questionId": QUESTION["questionId"], "sourceRef": {"documentId": document.document_id, "revision": document.revision},
            "reasonExcerptInsufficient": "摘要缺少限制条件和否定句", "expectedJudgmentChange": "避免把资格写成订单"}


def test_questions_use_the_planned_query_and_restart_each_path_without_new_http(tmp_path):
    path = tmp_path / "questions.sqlite"
    client = _SearchExtract()
    gateway, task = _gateway(path, client)
    one = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW, question=QUESTION, query_path=PATH)
    assert one.coverage["pathId"] == PATH["pathId"]
    assert one.documents[0].metadata["query"] == PATH["query"]
    second_path = PATH | {"pathId": "path-denial", "query": "项目 入围 取消 更正", "intent": "排查更正和取消",
                          "newPathReason": "查否认及更正，不重复资格公告", "expectedInformationGain": "是否已撤回"}
    two = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW, question=QUESTION, query_path=second_path)
    assert client.calls == 2
    restarted = TavilyEvidenceGateway(db_path=path, client=client, clock=lambda: COMPLETED_AT,
        task_id=task, network_max_attempts=2)
    for query_path in (PATH, second_path):
        result = restarted.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW, question=QUESTION, query_path=query_path)
        assert result.coverage["requestState"] == "reused"
    assert client.calls == 2
    assert two.documents[0].metadata["investigationPath"]["intent"] == "排查更正和取消"


def test_invalid_or_mismatched_question_is_rejected_before_http(tmp_path):
    client = _SearchExtract()
    gateway, _ = _gateway(tmp_path / "bad-path.sqlite", client)
    for bad in (PATH | {"questionId": "different"}, PATH | {"newPathReason": ""}, PATH | {"query": "a"*401}):
        with pytest.raises(VerificationCheckpointError):
            gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW, question=QUESTION, query_path=bad)
    assert client.calls == 0


@pytest.mark.parametrize("limit", [40, 80])
def test_fulltext_does_not_take_frozen_slots_or_call_extract_at_limit(tmp_path, limit):
    path = tmp_path / "full.sqlite"
    client = _SearchExtract(path)
    gateway, task = _gateway(path, client)
    _frozen(path, task, limit, limit)
    document = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW,
        question=QUESTION, query_path=PATH).eligible_documents[0]
    result = gateway.fetch_fulltext(event=_event(), document=document, question=QUESTION,
        request=_request(document), cutoff_at=NOW)
    assert result.state == "pending" and result.coverage["reason"] == "article_limit_reached"
    assert result.coverage["admissionState"] == "rejected"
    assert client.extract_calls == 0
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_article_admissions").fetchone()[0] == limit


def test_fulltext_admitted_before_request_and_restart_reuses_real_body(tmp_path):
    path = tmp_path / "admitted.sqlite"
    client = _SearchExtract(path)
    gateway, task = _gateway(path, client)
    _frozen(path, task, 37)
    document = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW,
        question=QUESTION, query_path=PATH).eligible_documents[0]
    request = _request(document)
    result = gateway.fetch_fulltext(event=_event(), document=document, question=QUESTION, request=request, cutoff_at=NOW)
    assert result.state == "available"
    assert result.coverage["admissionState"] == "fulfilled"
    assert result.documents[0].original_text == "原始公示：仅取得入围资格，尚未签约或形成订单。"
    assert result.documents[0].revision > document.revision
    assert result.documents[0].metadata["contentVersionAtCutoff"] == "unconfirmed"
    assert result.documents[0].metadata["bodyObservedAt"] == COMPLETED_AT.isoformat(timespec="seconds")
    restored = TavilyEvidenceGateway(db_path=path, client=client, task_id=task, network_max_attempts=2)
    again = restored.fetch_fulltext(event=_event(), document=document, question=QUESTION, request=request, cutoff_at=NOW)
    assert again.coverage["requestState"] == "reused" and client.extract_calls == 1
    assert again.documents[0].original_text == result.documents[0].original_text
    assert store.external_attempt_summary(task_id=task, db_path=path)["actualUsage"]["searchCredits"] == 3
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_article_admissions").fetchone()[0] == 38
        assert conn.execute("SELECT state FROM k10_article_admissions WHERE admission_kind='tavily_full_article'").fetchone()[0] == "completed"


def test_fulltext_unavailable_keeps_slot_and_is_a_gap_not_refutation(tmp_path):
    path = tmp_path / "missing.sqlite"
    client = _SearchExtract()
    client.extract = lambda url: TavilyExtractResponse(False, url, credits=1, reason="tavily_fulltext_unavailable")
    gateway, task = _gateway(path, client)
    _frozen(path, task, 37)
    doc = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW, question=QUESTION, query_path=PATH).eligible_documents[0]
    result = gateway.fetch_fulltext(event=_event(), document=doc, question=QUESTION, request=_request(doc), cutoff_at=NOW)
    assert not result.documents and result.coverage["reason"] == "tavily_fulltext_unavailable"
    assert result.coverage["requestState"] == "completed"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT state FROM k10_article_admissions WHERE admission_kind='tavily_full_article'").fetchone()[0] == "missing_body"


def test_another_question_reuses_one_admitted_article_without_extract_or_another_slot(tmp_path):
    path = tmp_path / "shared-article.sqlite"
    client = _SearchExtract(path)
    gateway, task = _gateway(path, client)
    _frozen(path, task, 37)
    doc = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW, question=QUESTION, query_path=PATH).eligible_documents[0]
    first = gateway.fetch_fulltext(event=_event(), document=doc, question=QUESTION, request=_request(doc), cutoff_at=NOW)
    other_question = QUESTION | {"questionId": "q-entity", "question": "名单主体是否是子公司？"}
    other_request = _request(doc) | {"questionId": "q-entity", "reasonExcerptInsufficient": "需要名单全称"}
    second = gateway.fetch_fulltext(event=_event(), document=doc, question=other_question, request=other_request, cutoff_at=NOW)
    assert client.extract_calls == 1 and second.coverage["requestState"] == "reused"
    assert second.documents[0].evidence_ref == first.documents[0].evidence_ref
    body_document = first.documents[0]
    body_request = _request(body_document) | {"questionId": "q-entity", "reasonExcerptInsufficient": "需要名单全称"}
    again = gateway.fetch_fulltext(event=_event(), document=body_document, question=other_question,
                                  request=body_request, cutoff_at=NOW)
    assert again.documents[0].evidence_ref == first.documents[0].evidence_ref
    assert client.extract_calls == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_article_admissions").fetchone()[0] == 38


def test_restart_after_body_persisted_before_article_outcome_does_not_pay_again(tmp_path, monkeypatch):
    path = tmp_path / "interrupted.sqlite"
    client = _SearchExtract(path)
    gateway, task = _gateway(path, client)
    _frozen(path, task, 37)
    doc = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW, question=QUESTION, query_path=PATH).eligible_documents[0]
    original = store.record_article_outcome
    def interrupted(**kwargs):
        if kwargs["document_id"] == doc.document_id:
            raise RuntimeError("simulated process loss after saving body")
        return original(**kwargs)
    monkeypatch.setattr(store, "record_article_outcome", interrupted)
    with pytest.raises(RuntimeError):
        gateway.fetch_fulltext(event=_event(), document=doc, question=QUESTION, request=_request(doc), cutoff_at=NOW)
    monkeypatch.setattr(store, "record_article_outcome", original)
    restarted = TavilyEvidenceGateway(db_path=path, client=client, task_id=task, network_max_attempts=2)
    recovered = restarted.fetch_fulltext(event=_event(), document=doc, question=QUESTION, request=_request(doc), cutoff_at=NOW)
    assert recovered.documents[0].original_text and recovered.coverage["requestState"] == "reused"
    assert client.extract_calls == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_execution_item_checkpoints WHERE status!='completed'").fetchone()[0] == 0
        assert conn.execute("SELECT state FROM k10_article_admissions WHERE admission_kind='tavily_full_article'").fetchone()[0] == "completed"


def test_extract_transport_requests_whole_source_not_query_chunks_and_tracks_credits():
    seen = []
    def transport(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"results": [{"url": "https://example.invalid/evidence", "raw_content": "完整原文"}],
            "usage": {"credits": 1}, "request_id": "req-extract"})
    result = TavilySearchClient("fixture-not-a-secret", transport=httpx.MockTransport(transport)).extract("https://example.invalid/evidence")
    assert result.ok and result.raw_content == "完整原文" and result.credits == 1
    assert "query" not in seen[0] and "chunks_per_source" not in seen[0]
    assert seen[0]["include_usage"] and seen[0]["urls"] == ["https://example.invalid/evidence"]


def test_late_and_unknown_sources_remain_ineligible_but_are_archived(tmp_path):
    path = tmp_path / "late.sqlite"
    client = _SearchExtract()
    original_search = client.search
    def late(query):
        response = original_search(query)
        return replace(response, hits=(replace(response.hits[0], publish_date="2026-09-07T01:05:00+00:00"), response.hits[1]))
    client.search = late
    gateway, _ = _gateway(path, client)
    result = gateway.fetch(event=_event(), retrieved_at=NOW, cutoff_at=NOW, question=QUESTION, query_path=PATH)
    assert len(result.documents) == 2 and not result.eligible_documents
    assert result.documents[0].metadata["afterCutoff"] is True
    assert result.documents[1].published_at is None
