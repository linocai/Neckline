"""B93 review inputs and question tools, without running a report."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3

from neckline.db import init_schema
from neckline.k10 import morning_runtime, pipeline, v2_store
from neckline.k10 import collection_runtime
from neckline.k10.discovery import DiscoveryDocument
from neckline.k10.ingestion import SqliteIngestionWriter
from neckline.k10.morning import build_morning_report_item, MorningReportError
from neckline.k10.schema import initialize_schema
from neckline.k10.sources import SourceDocumentInput
from neckline.k10.verification import VerificationEvidenceBundle
from neckline.k10.providers import ProviderResolution


PARENT = "2026-09-25T21:00:00+08:00"
MORNING = "2026-09-26T08:30:00+08:00"


def _original(document_id: str, *, published: str, body: str | None,
              precision: str = "exact", revision: int = 1) -> dict:
    return {"documentId": document_id, "revision": revision,
            "publishedAt": published, "publishedPrecision": precision,
            "originalText": body, "fetchedAt": MORNING}


def test_local_original_including_older_overlooked_fact_can_support_invalidation():
    original = {"documentId": "parent", "revision": 1}
    contexts = [{"reason": {"sourceRefs": [original]},
                 "reasonDocuments": [{"contentSha256": "original-hash", "originalText": "原推荐内容"}]}]
    docs = [
        {**_original("parent", published=MORNING, body="原推荐内容"), "contentSha256": "original-hash"},
        _original("recollected-old", published="2026-09-25T18:00:00+08:00", body="旧事重采"),
        _original("excerpt-only", published="2026-09-26T08:00:00+08:00", body=None),
        _original("unknown-time", published="时间不明", body="无法确定发生时间", precision="unknown"),
        _original("new-contrary", published="2026-09-26T08:00:00+08:00", body="上游终止供货的原文"),
        {**_original("duplicate-reprint", published=MORNING, body="原推荐内容"),
         "contentSha256": "original-hash"},
        {**_original("duplicate-cross-source", published=MORNING, body="原推荐内容"),
         "contentSha256": "different-metadata-hash"},
    ]
    valid = morning_runtime._review_local_original_refs(documents=docs, parent_reason_contexts=contexts)
    assert valid == {("recollected-old", 1), ("unknown-time", 1), ("new-contrary", 1)}
    item = build_morning_report_item(
        item_id="item", opportunity_id="op", company_window_id="window", display_rank=1,
        selection_state="unhandled", lifecycle="published", source_status="complete",
        reason_status="invalidated", material=True, is_new=False, summary="上游终止供货",
        coverage={"status": "complete"}, source_refs=[{"documentId": "new-contrary", "revision": 1}],
        independent_verification_refs=[], local_contrary_refs=[{"documentId": "new-contrary", "revision": 1}],
    )
    assert item.section == "major_contrary" and not item.independent_verification_refs
    try:
        build_morning_report_item(
            item_id="item", opportunity_id="op", company_window_id="window", display_rank=1,
            selection_state="unhandled", lifecycle="published", source_status="complete",
            reason_status="invalidated", material=True, is_new=False, summary="缺证据",
            coverage={"status": "complete"}, source_refs=[{"documentId": "recollected-old", "revision": 1}],
            independent_verification_refs=[], local_contrary_refs=[],
        )
    except MorningReportError:
        pass
    else:
        raise AssertionError("unqualified material cannot support a verified invalidation")


def test_morning_work_item_uses_saved_new_original_for_targeted_withdrawal(monkeypatch, tmp_path: Path):
    old_ref = {"documentId": "old-original", "revision": 1}
    new_ref = {"documentId": "new-contrary", "revision": 1}
    old_doc = _original("old-original", published=PARENT, body="原理由：合作进行中")
    new_doc = _original("new-contrary", published="2026-09-25T18:00:00+08:00",
                        body="昨晚漏读的前日公告：合作已经终止")
    base = {"candidate": {"candidateId": "candidate", "companyCode": "000001.SZ"},
            "opportunity": {"opportunityId": "opportunity", "state": "published"},
            "documents": [old_doc], "frozenEvidenceRefs": [old_ref], "observationIds": []}
    monkeypatch.setattr(morning_runtime.store, "read_run_config", lambda **_: {"payload": {}})
    monkeypatch.setattr(morning_runtime.store, "candidate_publication_cutoff", lambda **_: PARENT)
    monkeypatch.setattr(morning_runtime.store, "load_candidate_context", lambda **_: base)
    monkeypatch.setattr(morning_runtime.store, "load_document_versions", lambda *, refs, **_: [
        {"old-original": old_doc, "new-contrary": new_doc}[ref["documentId"]] for ref in refs])
    monkeypatch.setattr(morning_runtime.store, "list_observations", lambda **_: [])
    monkeypatch.setattr(v2_store, "read_morning_result", lambda **_: {
        "raw": {"material": True, "reasonStatus": "invalidated",
                "observationStatus": "unavailable", "summary": "前日旧公告昨晚漏读，已证明合作终止，撤回原理由。",
                "materialContraryEvidence": [{**new_ref, "claim": "前日公告确认终止合作。"}]},
        "capturedAt": "2026-09-26T08:10:00+08:00",
    })
    task = SimpleNamespace(task_id="morning-task", payload={
        "candidateId": "candidate", "originalCutoffAt": PARENT, "originalNewsCutoffAt": PARENT,
        "companyWindowId": "window", "displayRank": 1, "selectionState": "unhandled",
        "lifecycle": "published", "isNew": False, "configId": "run", "configRevision": 1,
        "workItemId": "review", "parentScanId": "scan", "sourceStatus": "partial",
        "morningEvidenceRefs": [new_ref], "morningSourceIndex": [],
        "independentVerificationRefs": [], "parentReasons": [{"candidateId": "candidate",
            "opportunityId": "opportunity", "sourceRefs": [old_ref], "analysisText": "合作进行中"}],
    })
    context = SimpleNamespace(task=task, db_path=tmp_path / "unused.sqlite", input_cutoff_at=MORNING,
                              execution_profile={"payload": {"discovery": {
                                  "reportInputContract": "k10-collected-input-3.6.1-b92"}}},
                              require_lease=lambda: None)
    result = morning_runtime.morning_review_handler(
        context, independent_evidence_fetch=lambda _action: (_ for _ in ()).throw(
            AssertionError("saved original must not require a provider call")))
    assert result.status == "completed" and result.stage == "withdrawn"
    assert result.checkpoint["reportSection"] == "major_contrary"
    assert result.checkpoint["independentVerificationRefs"] == []
    assert result.checkpoint["update"]["materialContraryEvidence"][0]["documentId"] == "new-contrary"


def test_titleless_indirect_flash_and_later_roundup_item_are_selectable(tmp_path: Path):
    db = tmp_path / "review.sqlite"
    init_schema(db)
    initialize_schema(db)
    writer = SqliteIngestionWriter(db_path=db)
    observed = datetime(2026, 9, 26, 0, 5, tzinfo=timezone.utc)
    flash = writer.append_document_version(source_key="jin10-flash", document=SourceDocumentInput(
        external_id="flash", canonical_url="https://flash.jin10.com/detail/flash",
        original_text="上游晶圆代工方宣布终止送样合作；未直接提及池内公司。", excerpt=None,
        published_at=observed, published_precision="exact", fetched_at=observed,
        fetch_version="fixture", metadata={"title": None, "sourceKind": "flash"},
    ))
    article = writer.append_document_version(source_key="jin10-news", document=SourceDocumentInput(
        external_id="roundup", canonical_url="https://xnews.jin10.com/details/roundup",
        original_text="一、甲公司订单正常\n二、乙公司暂停交付\n三、上游终止送样合作", excerpt=None,
        published_at=observed, published_precision="exact", fetched_at=observed,
        fetch_version="fixture", metadata={"title": "公告精选", "sourceKind": "article"},
    ))
    directory = writer.append_document_version(source_key="jin10-news", document=SourceDocumentInput(
        external_id="unread-roundup", canonical_url="https://xnews.jin10.com/details/unread-roundup",
        original_text=None, excerpt="今日公告精选目录，正文包含多项独立事项。",
        published_at=observed, published_precision="exact", fetched_at=observed,
        fetch_version="fixture", metadata={"title": "今日公告精选", "sourceKind": "article"},
    ))
    ordinary = writer.append_document_version(source_key="jin10-news", document=SourceDocumentInput(
        external_id="ordinary", canonical_url="https://xnews.jin10.com/details/ordinary",
        original_text="普通长文第一段。\n普通长文第二段。\n普通长文第三段。", excerpt="普通摘要。",
        published_at=observed, published_precision="exact", fetched_at=observed,
        fetch_version="fixture", metadata={"title": "精选观点：企业技术更新", "sourceKind": "article"},
    ))
    index = pipeline._b90_review_source_index(morning_refs=[
        {"documentId": flash.version.document_id, "revision": flash.version.revision},
        {"documentId": article.version.document_id, "revision": article.version.revision},
        {"documentId": directory.version.document_id, "revision": directory.version.revision},
        {"documentId": ordinary.version.document_id, "revision": ordinary.version.revision},
    ], db_path=db)
    parsed = morning_runtime._source_index(index)
    assert parsed is not None
    assert parsed[0]["title"] == "" and "终止送样合作" in parsed[0]["contentCue"]
    assert parsed[1]["title"] == "公告精选"
    assert any("上游终止送样合作" in cue["text"] for cue in parsed[1]["itemCues"])
    assert parsed[2]["itemCuesPending"] is True and "多项独立事项" in parsed[2]["contentCue"]
    assert "itemCues" not in parsed[3] and "itemCuesPending" not in parsed[3]
    assert all(item["documentId"] and item["revision"] == 1 for item in parsed)


def test_morning_question_routes_jin10_without_tavily(monkeypatch, tmp_path: Path):
    class Jin10:
        calls = []

        def fetch(self, **kwargs):
            self.calls.append(kwargs)
            document = DiscoveryDocument("new-jin10", 1, MORNING, MORNING,
                                         "公司公告所述关系终止", None, {"sourceKey": "jin10-flash"})
            return VerificationEvidenceBundle("available", (document,), (document,),
                                              {"state": "completed", "reason": "visible_results"})

    class Tavily:
        def for_checkpoint_namespace(self, _namespace):
            raise AssertionError("local Jin10 question must not invoke Tavily")

    parent = SimpleNamespace(db_path=tmp_path / "unused.sqlite", clock=lambda: datetime.fromisoformat(MORNING))
    target = {"reviewId": "review", "companyCode": "000001.SZ", "reasons": [{
        "opportunityId": "op", "analysisText": "送样合作支撑推荐",
        "sourceRefs": [{"documentId": "original", "revision": 1}],
    }]}
    jin10 = Jin10()
    refs, coverage = pipeline._b90_independent_review_evidence(
        parent=parent, target=target, configuration={}, gateway=Tavily(), jin10_gateway=jin10,
        cutoff_at=datetime.fromisoformat(MORNING),
        action={"action": "search", "source": "jin10-flash", "question": "合作是否终止？",
                "query": "上游公司名称", "rationale": "关系可能已改变"},
    )
    assert refs == [{"documentId": "new-jin10", "revision": 1}]
    assert coverage["source"] == "jin10-flash" and coverage["state"] == "complete"
    assert jin10.calls[0]["query_path"].target_source == "jin10-flash"
    assert jin10.calls[0]["question"].question == "合作是否终止？"


def test_report_jin10_factory_can_reuse_receipts_without_live_token(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("JIN10_MCP_TOKEN", raising=False)
    monkeypatch.setattr(collection_runtime, "collection_config_for_report", lambda **_: {"payload": {
        "mcp": {"endpoint": "https://mcp.jin10.com/mcp", "protocolVersion": "2025-11-25"},
        "sources": [
            {"sourceKey": "jin10-flash", "credentialEnv": "JIN10_MCP_TOKEN", "timeoutSeconds": 30},
            {"sourceKey": "jin10-news", "credentialEnv": "JIN10_MCP_TOKEN", "timeoutSeconds": 30},
        ]}})
    factory = pipeline._b92_jin10_client_factory(
        db_path=tmp_path / "unused.sqlite", collection_task_ids=["collected"], binding={})
    client = factory()
    assert client is not None
    try:
        assert client._token is None
    finally:
        client.close()


def test_partial_jin10_page_never_claims_complete_independent_coverage(tmp_path: Path):
    class Jin10:
        def fetch(self, **_kwargs):
            document = DiscoveryDocument("first-page", 1, MORNING, MORNING,
                                         "第一批相关资料", None, {"sourceKey": "jin10-news"})
            return VerificationEvidenceBundle("available", (document,), (document,),
                                              {"state": "partial", "reason": "next_page_unavailable",
                                               "pagesFetched": 1, "hasMore": True})

    parent = SimpleNamespace(db_path=tmp_path / "unused.sqlite", clock=lambda: datetime.fromisoformat(MORNING))
    target = {"reviewId": "review", "companyCode": "000001.SZ", "reasons": [{
        "opportunityId": "op", "analysisText": "送样合作支撑推荐",
        "sourceRefs": [{"documentId": "original", "revision": 1}],
    }]}
    refs, coverage = pipeline._b90_independent_review_evidence(
        parent=parent, target=target, configuration={}, gateway=None, jin10_gateway=Jin10(),
        cutoff_at=datetime.fromisoformat(MORNING),
        action={"action": "search", "source": "jin10-news", "question": "公告进展？",
                "query": "上游公司名称", "rationale": "关系可能已改变"},
    )
    assert refs == [{"documentId": "first-page", "revision": 1}]
    assert coverage["state"] == "partial" and coverage["pagesFetched"] == 1


def test_morning_reads_known_jin10_article_body_without_tavily(monkeypatch, tmp_path: Path):
    class Jin10:
        def fetch_fulltext(self, **kwargs):
            assert kwargs["document"].metadata["sourceKey"] == "jin10-news"
            assert kwargs["question"].question == "公告细节是否终止合作？"
            document = DiscoveryDocument("article", 2, MORNING, MORNING,
                                         "公告全文确认合作终止", None, {"sourceKey": "jin10-news"})
            return VerificationEvidenceBundle("available", (document,), (document,),
                                              {"state": "completed", "reason": "visible_results"})

    class Tavily:
        def for_checkpoint_namespace(self, _namespace):
            raise AssertionError("known Jin10 article must use Jin10 get_news")

    monkeypatch.setattr(pipeline.store, "load_document_versions", lambda **_: [{
        "documentId": "article", "revision": 1, "publishedAt": MORNING, "fetchedAt": MORNING,
        "originalText": None, "excerpt": "公告摘要", "metadata": {"providerId": "article"},
        "sourceKey": "jin10-news",
    }])
    parent = SimpleNamespace(db_path=tmp_path / "unused.sqlite", clock=lambda: datetime.fromisoformat(MORNING))
    target = {"reviewId": "review", "companyCode": "000001.SZ", "reasons": [{
        "opportunityId": "op", "analysisText": "送样合作支撑推荐",
        "sourceRefs": [{"documentId": "original", "revision": 1}],
    }]}
    refs, coverage = pipeline._b90_independent_review_evidence(
        parent=parent, target=target, configuration={}, gateway=Tavily(), jin10_gateway=Jin10(),
        cutoff_at=datetime.fromisoformat(MORNING),
        action={"action": "extract", "sourceRef": {"documentId": "article", "revision": 1},
                "_returnedIndependentRefs": [{"documentId": "article", "revision": 1}],
                "_independentQuestion": "公告细节是否终止合作？", "rationale": "摘要未说明终止范围"},
    )
    assert refs == [{"documentId": "article", "revision": 2}]
    assert coverage["state"] == "complete"


def test_morning_reads_shared_roundup_directory_through_jin10(monkeypatch, tmp_path: Path):
    class Jin10:
        def fetch_fulltext(self, **kwargs):
            assert kwargs["document"].metadata["sourceKey"] == "jin10-news"
            document = DiscoveryDocument("roundup", 2, MORNING, MORNING,
                                         "一、其他事项\n二、上游终止合作", None,
                                         {"sourceKey": "jin10-news"})
            return VerificationEvidenceBundle("available", (document,), (document,),
                                              {"state": "completed", "reason": "visible_results"})

    monkeypatch.setattr(pipeline.store, "load_document_versions", lambda **_: [{
        "documentId": "roundup", "revision": 1, "publishedAt": MORNING, "fetchedAt": MORNING,
        "originalText": None, "excerpt": "今日公告精选", "metadata": {"providerId": "roundup"},
        "sourceKey": "jin10-news",
    }])
    parent = SimpleNamespace(db_path=tmp_path / "unused.sqlite", clock=lambda: datetime.fromisoformat(MORNING))
    target = {"reviewId": "review", "companyCode": "000001.SZ", "reasons": [{
        "opportunityId": "op", "analysisText": "送样合作支撑推荐",
        "sourceRefs": [{"documentId": "original", "revision": 1}],
    }], "morningSourceIndex": [{"documentId": "roundup", "revision": 1,
                               "title": "今日公告精选", "itemCuesPending": True}]}
    refs, coverage = pipeline._b90_independent_review_evidence(
        parent=parent, target=target, configuration={}, gateway=None, jin10_gateway=Jin10(),
        cutoff_at=datetime.fromisoformat(MORNING),
        action={"action": "read_article", "sourceRef": {"documentId": "roundup", "revision": 1},
                "rationale": "目录没有子项，需要读原文确认第二事项"},
    )
    assert refs == [{"documentId": "roundup", "revision": 2}]
    assert coverage["agentDecision"] == "read_article" and coverage["state"] == "complete"


def test_reason_bound_local_lookup_finds_old_fact_without_widening_frozen_catalogue(tmp_path: Path):
    db = tmp_path / "local-history.sqlite"
    init_schema(db)
    initialize_schema(db)
    writer = SqliteIngestionWriter(db_path=db)
    observed = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)

    def put(identifier: str):
        return writer.append_document_version(source_key="jin10-flash", document=SourceDocumentInput(
            external_id=identifier, canonical_url="https://flash.jin10.com/detail/" + identifier,
            original_text="上游正式终止送样合作，原推荐关系不成立。", excerpt=None,
            published_at=datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc),
            published_precision="exact", fetched_at=observed,
            fetch_version="fixture", metadata={"title": None, "sourceKind": "flash"},
        ))

    old = put("overlooked-original")
    saved_verification = writer.append_document_version(source_key="tavily_verification", document=SourceDocumentInput(
        external_id="saved-verification", canonical_url="https://example.com/verification",
        original_text="补充核验结论：协议已解除。", excerpt=None,
        published_at=datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc),
        published_precision="exact", fetched_at=observed, fetch_version="fixture",
        metadata={"title": "已存核验", "sourceKind": "verification"},
    ))
    historical_roundup = writer.append_document_version(source_key="jin10-news", document=SourceDocumentInput(
        external_id="historical-roundup", canonical_url="https://xnews.jin10.com/details/historical-roundup",
        original_text="一、甲公司正常。\n二、乙公司正常。\n三、上游暂停交付。\n四、丙公司正常。",
        excerpt="公告精选", published_at=datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc),
        published_precision="exact", fetched_at=observed, fetch_version="fixture",
        metadata={"title": "公告精选", "sourceKind": "article"},
    ))
    with sqlite3.connect(db) as conn:
        ceiling = conn.execute("SELECT MAX(rowid) FROM k10_source_document_versions").fetchone()[0]
    late = put("late-after-freeze")  # Same old fetchedAt, but not present at this review's freeze.
    parent = SimpleNamespace(db_path=db, clock=lambda: datetime.fromisoformat(MORNING),
        execution_profile={"payload": {"discovery": {"collectionSourceKeys": ["jin10-flash", "jin10-news"]}}})
    target = {"reviewId": "review", "companyCode": "000001.SZ", "reasons": [{
        "opportunityId": "op", "analysisText": "送样合作支撑推荐",
        "sourceRefs": [{"documentId": "original", "revision": 1}],
    }], "morningSourceIndex": [], "localHistoryVisibleAt": MORNING,
       "localHistoryRowidCeiling": ceiling}
    refs, coverage = pipeline._b90_independent_review_evidence(
        parent=parent, target=target, configuration={}, gateway=None,
        cutoff_at=datetime.fromisoformat(MORNING),
        action={"action": "find_local", "question": "原合作关系是否已经被终止？",
                "query": "终止送样合作", "rationale": "昨晚理由可能漏读旧公告"},
    )
    assert refs == [] and coverage["agentDecision"] == "find_local"
    locators = morning_runtime._source_index(coverage["catalogueEntries"])
    assert locators is not None and len(locators) == 1
    assert locators[0]["documentId"] == old.version.document_id
    assert locators[0]["documentId"] != late.version.document_id
    assert locators[0]["localHistory"] is True
    assert "终止送样合作" in locators[0]["contentCue"]
    _refs, saved = pipeline._b90_independent_review_evidence(
        parent=parent, target=target, configuration={}, gateway=None,
        cutoff_at=datetime.fromisoformat(MORNING),
        action={"action": "find_local", "question": "此前核验是否已有结论？",
                "query": "协议已解除", "rationale": "复用已存核验，避免重复搜索"},
    )
    assert [item["documentId"] for item in saved["catalogueEntries"]] == [saved_verification.version.document_id]
    _refs, roundup = pipeline._b90_independent_review_evidence(
        parent=parent, target=target, configuration={}, gateway=None,
        cutoff_at=datetime.fromisoformat(MORNING),
        action={"action": "find_local", "question": "上游交付是否变化？",
                "query": "暂停交付", "rationale": "按理由定位历史合集中的具体事项"},
    )
    assert [item["documentId"] for item in roundup["catalogueEntries"]] == [historical_roundup.version.document_id]
    assert "暂停交付" in roundup["catalogueEntries"][0]["contentCue"]
    assert "itemCues" not in roundup["catalogueEntries"][0], "历史定位不能广播合集所有子项"


def test_historical_jin10_directory_can_read_only_after_frozen_locator_match(tmp_path: Path):
    db = tmp_path / "historical-article.sqlite"
    init_schema(db)
    initialize_schema(db)
    writer = SqliteIngestionWriter(db_path=db)
    observed = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)

    def put(identifier: str):
        return writer.append_document_version(source_key="jin10-news", document=SourceDocumentInput(
            external_id=identifier, canonical_url="https://xnews.jin10.com/details/" + identifier,
            original_text=None, excerpt="已发布的协议解除公告精选目录。",
            published_at=datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc),
            published_precision="exact", fetched_at=observed, fetch_version="fixture",
            metadata={"title": "协议解除公告精选", "sourceKind": "article", "providerId": identifier},
        ))

    old = put("old-directory")
    with sqlite3.connect(db) as conn:
        ceiling = conn.execute("SELECT MAX(rowid) FROM k10_source_document_versions").fetchone()[0]
    late = put("late-directory")
    parent = SimpleNamespace(db_path=db, clock=lambda: datetime.fromisoformat(MORNING),
        execution_profile={"payload": {"discovery": {"collectionSourceKeys": ["jin10-news"]}}})
    target = {"reviewId": "review", "companyCode": "000001.SZ", "reasons": [{
        "opportunityId": "op", "analysisText": "协议仍有效",
        "sourceRefs": [{"documentId": "original", "revision": 1}],
    }], "morningSourceIndex": [], "localHistoryVisibleAt": MORNING,
       "localHistoryRowidCeiling": ceiling}
    class Jin10:
        calls = 0

        def fetch_fulltext(self, **kwargs):
            self.calls += 1
            document = kwargs["document"]
            assert document.document_id == old.version.document_id
            full = DiscoveryDocument(document.document_id, document.revision + 1, MORNING, MORNING,
                                     "第二事项：协议正式解除。", None, {"sourceKey": "jin10-news"})
            return VerificationEvidenceBundle("available", (full,), (full,),
                                              {"state": "completed", "reason": "visible_results"})

    gateway = Jin10()
    action = {"action": "read_article", "sourceRef": {
        "documentId": old.version.document_id, "revision": old.version.revision},
        "rationale": "历史目录子项可能推翻原理由",
        "_localHistoryLocator": {"query": "协议解除", "offset": 0}}
    refs, coverage = pipeline._b90_independent_review_evidence(
        parent=parent, target=target, configuration={}, gateway=None, jin10_gateway=gateway,
        cutoff_at=datetime.fromisoformat(MORNING), action=action,
    )
    assert refs == [{"documentId": old.version.document_id, "revision": 2}]
    assert coverage["state"] == "complete" and gateway.calls == 1
    late_action = {**action, "sourceRef": {"documentId": late.version.document_id,
                                           "revision": late.version.revision}}
    late_refs, late_coverage = pipeline._b90_independent_review_evidence(
        parent=parent, target=target, configuration={}, gateway=None, jin10_gateway=gateway,
        cutoff_at=datetime.fromisoformat(MORNING), action=late_action,
    )
    assert late_refs == [] and late_coverage["reason"] == "independent_request_invalid"
    assert gateway.calls == 1


def test_same_review_rephrases_empty_local_lookup_then_reads_exact_old_original(monkeypatch, tmp_path: Path):
    old_ref = {"documentId": "parent", "revision": 1}
    found_ref = {"documentId": "saved-verification", "revision": 1}
    old_doc = _original("parent", published=PARENT, body="合作关系持续有效")
    found_doc = {**_original("saved-verification", published="2026-09-25T18:00:00+08:00",
                            body="前日公告：协议已解除，昨晚理由不成立。"),
                 "sourceKey": "tavily_verification"}
    base = {"candidate": {"candidateId": "candidate", "companyCode": "000001.SZ"},
            "opportunity": {"opportunityId": "opportunity", "state": "published"},
            "documents": [old_doc], "frozenEvidenceRefs": [old_ref], "observationIds": []}
    monkeypatch.setattr(morning_runtime.store, "read_run_config", lambda **_: {"payload": {}})
    monkeypatch.setattr(morning_runtime.store, "candidate_publication_cutoff", lambda **_: PARENT)
    monkeypatch.setattr(morning_runtime.store, "load_candidate_context", lambda **_: base)
    monkeypatch.setattr(morning_runtime.store, "load_document_versions", lambda *, refs, **_: [
        {("parent", 1): old_doc, ("saved-verification", 1): found_doc}
        [(ref["documentId"], ref["revision"])] for ref in refs])
    monkeypatch.setattr(morning_runtime.store, "list_observations", lambda **_: [])
    monkeypatch.setattr(v2_store, "read_morning_result", lambda **_: None)
    monkeypatch.setattr(v2_store, "save_morning_result", lambda **kwargs: {
        "raw": kwargs["raw"], "capturedAt": "2026-09-26T08:10:00+08:00"})

    class Provider:
        calls = 0

        def chat(self, messages, **_kwargs):
            self.calls += 1
            evidence = json.loads(messages[-1].content.split("<untrusted-evidence>\n", 1)[1]
                                  .split("\n</untrusted-evidence>", 1)[0])
            if self.calls == 1:
                reply = {"action": "find_local", "question": "是否终止送样合作？",
                         "query": "终止送样合作", "rationale": "原理由可能已有反证"}
            elif self.calls == 2:
                assert evidence["localLookupCoverage"]["reason"] == "local_history_no_match"
                reply = {"action": "find_local", "question": "协议是否解除？",
                         "query": "协议已解除", "rationale": "同一问题改查原件用词"}
            elif self.calls == 3:
                assert evidence["morningSourceIndex"][0]["documentId"] == found_ref["documentId"]
                reply = {"action": "read", **found_ref, "rationale": "读取精确原文版本"}
            else:
                assert self.calls == 4
                assert evidence["morningDocuments"][0]["originalText"] == found_doc["originalText"]
                assert evidence["morningSourceIndex"] == []
                reply = {"action": "conclude", "material": True, "reasonStatus": "invalidated",
                         "observationStatus": "unavailable",
                         "summary": "前日原件昨晚漏读，已解除协议，原理由失效。",
                         "materialContraryEvidence": [{**found_ref, "claim": "前日公告确认协议解除。"}]}
            return SimpleNamespace(ok=True, content=json.dumps(reply))

    provider = Provider()
    monkeypatch.setattr(morning_runtime, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution(
        "configured", provider, "fixture", None))
    task = SimpleNamespace(task_id="morning-task", attempt_count=1, payload={
        "candidateId": "candidate", "originalCutoffAt": PARENT, "originalNewsCutoffAt": PARENT,
        "companyWindowId": "window", "displayRank": 1, "selectionState": "unhandled",
        "lifecycle": "published", "isNew": False, "configId": "run", "configRevision": 1,
        "workItemId": "review", "parentScanId": "scan", "sourceStatus": "complete",
        "morningEvidenceRefs": [], "morningSourceIndex": [],
        "independentVerificationRefs": [], "parentReasons": [{"candidateId": "candidate",
            "opportunityId": "opportunity", "sourceRefs": [old_ref], "analysisText": "合作关系持续有效"}],
    })
    context = SimpleNamespace(task=task, db_path=tmp_path / "unused.sqlite", input_cutoff_at=MORNING,
        execution_profile={"payload": {"discovery": {
            "reportInputContract": "k10-collected-input-3.6.1-b92"}}},
        require_lease=lambda: None, clock=lambda: datetime.fromisoformat(MORNING),
        execution_deadline_at=None)
    actions = []

    def local_only(action):
        actions.append(action)
        assert action["action"] == "find_local", "saved original needs no provider search"
        if action["query"] == "终止送样合作":
            return [], {"state": "partial", "reason": "local_history_no_match", "catalogueEntries": []}
        return [], {"state": "complete", "reason": "local_history_locators", "catalogueEntries": [
            {**found_ref, "title": "已存核验", "sourceKey": "tavily_verification", "localHistory": True}]}

    result = morning_runtime.morning_review_handler(context, independent_evidence_fetch=local_only)
    assert result.status == "completed" and result.stage == "withdrawn"
    assert result.checkpoint["reportSection"] == "major_contrary"
    assert result.checkpoint["independentVerificationRefs"] == []
    assert len(actions) == 2 and provider.calls == 4


def test_review_work_item_reads_roundup_body_before_assessing_hidden_item(monkeypatch, tmp_path: Path):
    old_ref = {"documentId": "old", "revision": 1}
    directory_ref = {"documentId": "roundup", "revision": 1}
    full_ref = {"documentId": "roundup", "revision": 2}
    old_doc = _original("old", published=PARENT, body="送样合作仍在进行")
    directory_doc = {**_original("roundup", published=MORNING, body=None),
                     "excerpt": "今日公告精选", "sourceKey": "jin10-news"}
    full_doc = {**_original("roundup", revision=2, published=MORNING,
                           body="一、其他事项\n二、上游公司正式终止送样合作"), "sourceKey": "jin10-news"}
    base = {"candidate": {"candidateId": "candidate", "companyCode": "000001.SZ"},
            "opportunity": {"opportunityId": "opportunity", "state": "published"},
            "documents": [old_doc], "frozenEvidenceRefs": [old_ref], "observationIds": []}
    monkeypatch.setattr(morning_runtime.store, "read_run_config", lambda **_: {"payload": {}})
    monkeypatch.setattr(morning_runtime.store, "candidate_publication_cutoff", lambda **_: PARENT)
    monkeypatch.setattr(morning_runtime.store, "load_candidate_context", lambda **_: base)
    monkeypatch.setattr(morning_runtime.store, "load_document_versions", lambda *, refs, **_: [
        {("old", 1): old_doc, ("roundup", 1): directory_doc,
         ("roundup", 2): full_doc}[(ref["documentId"], ref["revision"])] for ref in refs])
    monkeypatch.setattr(morning_runtime.store, "list_observations", lambda **_: [])
    monkeypatch.setattr(v2_store, "read_morning_result", lambda **_: None)
    monkeypatch.setattr(v2_store, "save_morning_result", lambda **kwargs: {
        "raw": kwargs["raw"], "capturedAt": "2026-09-26T08:10:00+08:00"})

    class Provider:
        calls = 0

        def chat(self, messages, **_kwargs):
            self.calls += 1
            evidence = json.loads(messages[-1].content.split("<untrusted-evidence>\n", 1)[1]
                                  .split("\n</untrusted-evidence>", 1)[0])
            if self.calls == 1:
                assert evidence["morningSourceIndex"][0]["itemCuesPending"] is True
                assert evidence["morningDocuments"] == []
                reply = {"action": "read", **directory_ref, "rationale": "合集可能包含关联子事项"}
            else:
                assert self.calls == 2 and evidence["independentVerificationDocuments"][0]["originalText"] == full_doc["originalText"]
                assert evidence["morningSourceIndex"] == [], "already read directory should not repeat on every round"
                reply = {"action": "conclude", "material": True, "reasonStatus": "invalidated",
                         "observationStatus": "unavailable", "summary": "合集第二事项确认合作终止。",
                         "materialContraryEvidence": [{**full_ref, "claim": "第二事项确认终止。"}]}
            return SimpleNamespace(ok=True, content=json.dumps(reply))

    provider = Provider()
    monkeypatch.setattr(morning_runtime, "resolve_deepseek_v4_pro", lambda **_: ProviderResolution(
        "configured", provider, "fixture", None))
    task = SimpleNamespace(task_id="morning-task", attempt_count=1, payload={
        "candidateId": "candidate", "originalCutoffAt": PARENT, "originalNewsCutoffAt": PARENT,
        "companyWindowId": "window", "displayRank": 1, "selectionState": "unhandled",
        "lifecycle": "published", "isNew": False, "configId": "run", "configRevision": 1,
        "workItemId": "review", "parentScanId": "scan", "sourceStatus": "complete",
        "morningEvidenceRefs": [], "morningSourceIndex": [{**directory_ref,
            "title": "今日公告精选", "itemCuesPending": True, "sourceKey": "jin10-news"}],
        "independentVerificationRefs": [], "parentReasons": [{"candidateId": "candidate",
            "opportunityId": "opportunity", "sourceRefs": [old_ref], "analysisText": "合作进行中"}],
    })
    context = SimpleNamespace(task=task, db_path=tmp_path / "unused.sqlite", input_cutoff_at=MORNING,
                              execution_profile={"payload": {"discovery": {
                                  "reportInputContract": "k10-collected-input-3.6.1-b92"}}},
                              require_lease=lambda: None, clock=lambda: datetime.fromisoformat(MORNING),
                              execution_deadline_at=None)
    actions = []

    def get_body(action):
        actions.append(action)
        assert action["action"] == "read_article" and action["sourceRef"] == directory_ref
        return [full_ref], {"state": "complete", "reason": "ok", "agentDecision": "read_article",
                            "question": "这篇已采集文章的完整正文有哪些会改变昨晚冻结理由的独立事项？"}

    result = morning_runtime.morning_review_handler(context, independent_evidence_fetch=get_body)
    assert result.status == "completed" and result.stage == "withdrawn"
    assert result.checkpoint["reportSection"] == "major_contrary"
    assert result.checkpoint["independentVerificationRefs"] == [full_ref]
    assert len(actions) == 1 and provider.calls == 2
