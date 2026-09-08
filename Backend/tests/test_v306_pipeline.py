"""Concrete transport regressions for title-first selection and real article limits."""
from datetime import datetime
from hashlib import sha256
import json

import httpx
import pytest

from neckline.k10 import store
from neckline.k10.discovery import DiscoveryDocument, prepare_document_for_analysis
from neckline.k10.metering import MeteredProvider
from neckline.k10.pipeline import DeepSeekDiscoveryModel, _CheckpointedDiscoveryModel, PipelineError
from neckline.k10.schema import initialize_schema
from neckline.k10.title_runtime import select_title_documents
from tests.k10_v306_fixture import append_approved_execution_profile

NOW = "2026-09-08T06:00:00+00:00"


def _setup(path, *, task="titles", count=95, duplicate=True):
    initialize_schema(path)
    store.set_run_control(state="open", reason_code="offline_fixture", changed_at=NOW, changed_by="test", db_path=path)
    cfg, rev = append_approved_execution_profile(db_path=path, created_at=NOW, config_id="v306")
    profile = store.read_execution_config(config_id=cfg, revision=rev, db_path=path)
    profile["payload"]["discovery"]["titleBatchSize"] = 32
    rev = store.append_execution_config(config_id=cfg, payload=profile["payload"], created_at=NOW, db_path=path)
    store.enqueue_task(task_id=task, kind="evening_scan", idempotency_key=task, input_version="fixture",
        input_cutoff_at=NOW, payload={}, budget={}, created_at=NOW, db_path=path)
    binding = store.bind_task_execution(task_id=task, execution_config_id=cfg, execution_config_revision=rev,
        binding_kind="scheduled", bound_at=NOW, db_path=path)
    documents = []
    for index in range(count + int(duplicate)):
        original = 1 if index == count else index
        key = f"article-{index:04d}"
        title = f"公司{original}披露新的项目进展"
        body = f"BODY_SENTINEL_{original}：独立来源正文，不得出现在标题模型请求。"
        metadata = {"title": title, "sourceKey": "fixture", "secretBodyField": "METADATA_MUST_NOT_LEAK"}
        row = store.append_document_version(document_id=key, source_key="fixture", external_id=key,
            canonical_url=None, content_sha256=sha256(body.encode()).hexdigest(), published_at=NOW,
            published_precision="exact", fetched_at=NOW, original_text=body, excerpt=None,
            fetch_version="fixture", metadata=metadata, created_at=NOW, db_path=path)
        documents.append(DiscoveryDocument(row.document_id, row.revision, NOW, NOW, body, None, metadata))
    provider = MeteredProvider(ledger_db=path, ledger_task="discovery", api_key="offline-fixture",
        model="deepseek-v4-pro", name="fixture", api_url="https://api.deepseek.com/chat/completions")
    base = DeepSeekDiscoveryModel(provider)
    base.set_execution_policy(binding["payload"]["discovery"])
    base.set_scan_cutoff(datetime.fromisoformat(NOW))
    model = _CheckpointedDiscoveryModel(base=base, task_id=task, execution_profile=binding,
        cutoff_at=datetime.fromisoformat(NOW), db_path=path, leaseguard=None)
    return tuple(documents), binding, model


def _mock_http(monkeypatch, *, invalid_batch=False, sole_output=False, remove_last=False, invalid_review=False,
               invalid_body_once=False):
    calls = []
    def respond(request):
        request_json = json.loads(request.content)
        content = request_json["messages"][-1]["content"]
        payload = json.loads(content.split("<untrusted-k10-evidence>\n", 1)[1].split("\n</untrusted-k10-evidence>", 1)[0])
        if payload.get("operation") == "titleSelectionReview":
            stage = "review"
            assert "BODY_SENTINEL" not in content and "METADATA_MUST_NOT_LEAK" not in content
            assert all(set(row) == {"i", "sourceKey", "publishedAt", "title", "status", "proposedReason"}
                       for row in payload["items"])
            kept = payload["items"][:-1] if remove_last else payload["items"]
            result = {"complete": True, "kept": [{"i": row["i"], "reason": "标题有独立实质事实，保留深读"} for row in kept],
                      "removed": [{"i": payload["items"][-1]["i"], "reason": "不足以支持新增实质事实", "duplicateOf": None}] if remove_last else []}
            if invalid_review:
                result["kept"].pop()
        elif "articleLimit" in payload:
            stage = "global"
            indexes = [row["i"] for row in payload["items"]]
            limit = payload["articleLimit"]
            result = {"selected": [{"i": i, "selectedRank": rank + 1, "reason": "全局比较入选"}
                                    for rank, i in enumerate(indexes[:limit])],
                      "merged": [], "selectionComplete": True, "reviewedCount": len(indexes)}
        elif "items" in payload:
            stage = "titles"
            assert "BODY_SENTINEL" not in content and "METADATA_MUST_NOT_LEAK" not in content
            assert all(set(row) == {"documentId", "revision", "sourceKey", "publishedAt", "title"} for row in payload["items"])
            result = {"items": [{"i": index, "status": "candidate", "matterKey": row["documentId"],
                                  "stageKey": "new", "reason": "有新增事实，值得进一步核查"}
                                 for index, row in enumerate(payload["items"])]}
            if invalid_batch:
                result["items"].pop()
        else:
            stage = "body"
            assert "BODY_SENTINEL" in payload["text"]
            assert payload["textMode"] == "full_text" and payload["isExcerpt"] is False
            result = {"events": [], "needsFullText": False}
            if invalid_body_once and not any(prior_stage == "body" for prior_stage, _ in calls):
                result = {"events": {}}
        if sole_output:
            result = {"output": result}
        calls.append((stage, payload))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": json.dumps(result)},
                                                     "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})
    transport = httpx.MockTransport(respond)
    client = httpx.Client
    def isolated_client(**kwargs):
        kwargs["transport"] = transport
        return client(**kwargs)
    monkeypatch.setattr(httpx, "Client", isolated_client)
    return calls


@pytest.mark.parametrize(("window", "limit"), [("evening", 80), ("morning", 40)])
def test_actual_http_title_pipeline_freezes_quota_and_resumes_without_replay(tmp_path, monkeypatch, window, limit):
    path = tmp_path / "title-pipeline.sqlite"
    documents, binding, model = _setup(path)
    calls = _mock_http(monkeypatch)
    selected = select_title_documents(documents=documents, window_kind=window, task_id="titles",
        execution_profile=binding, model=model, db_path=path)
    assert len(selected) == limit and all(stage != "body" for stage, _ in calls)
    assert len(store.read_title_triage_items(task_id="titles", db_path=path)) == len(documents)
    assert sum(row["disposition"] == "exact_duplicate" for row in store.read_title_triage_items(task_id="titles", db_path=path)) == 1
    # Selected articles reserve all slots even before the first body is read.
    assert store.admit_article(task_id="titles", document_id="extra-web-body", revision=1,
        admission_kind="tavily_full_article", created_at=NOW, db_path=path)["state"] == "limit_reached"
    for document in selected:
        assert model.understand(document=prepare_document_for_analysis(document)) == ()
    assert sum(stage == "body" for stage, _ in calls) == limit
    before = len(calls)
    replay = select_title_documents(documents=documents, window_kind=window, task_id="titles",
        execution_profile=binding, model=model, db_path=path)
    for document in replay:
        assert model.understand(document=prepare_document_for_analysis(document)) == ()
    assert len(calls) == before
    assert store.external_attempt_summary(task_id="titles", db_path=path)["succeeded"] == before
    unselected = next(doc for doc in documents if doc not in selected)
    with pytest.raises(PipelineError, match="准入"):
        model.understand(document=unselected)
    assert len(calls) == before


def test_incomplete_title_response_never_admits_any_body(tmp_path, monkeypatch):
    path = tmp_path / "invalid-title.sqlite"
    documents, binding, model = _setup(path, count=4, duplicate=False)
    calls = _mock_http(monkeypatch, invalid_batch=True)
    with pytest.raises(PipelineError):
        select_title_documents(documents=documents, window_kind="evening", task_id="titles",
            execution_profile=binding, model=model, db_path=path)
    assert calls and all(stage == "titles" for stage, _ in calls)
    assert store.read_title_selection_manifest(task_id="titles", db_path=path) is None
    with pytest.raises(PipelineError, match="准入"):
        model.understand(document=documents[0])


def test_selected_missing_body_never_calls_provider_or_releases_its_slot(tmp_path, monkeypatch):
    from dataclasses import replace
    path = tmp_path / "missing.sqlite"
    documents, binding, model = _setup(path, count=1, duplicate=False)
    missing = replace(documents[0], original_text=None, excerpt=None, analysis_text=None)
    calls = _mock_http(monkeypatch)
    selected = select_title_documents(documents=[missing], window_kind="evening", task_id="titles",
        execution_profile=binding, model=model, db_path=path)
    before = len(calls)
    with pytest.raises(PipelineError, match="正文缺失"):
        model.understand(document=selected[0])
    assert len(calls) == before
    admitted = store.admit_article(task_id="titles", document_id=missing.document_id, revision=1,
        admission_kind="selected", created_at=NOW, db_path=path)
    assert admitted["state"] == "reused" and admitted["articleState"] == "missing_body"


@pytest.mark.parametrize("changed", [False, True])
def test_source_facts_reuse_only_for_unchanged_revisions_across_later_scan(tmp_path, monkeypatch, changed):
    path = tmp_path / "later.sqlite"
    documents, binding, model = _setup(path, count=1, duplicate=False)
    calls = _mock_http(monkeypatch)
    selected = select_title_documents(documents=documents, window_kind="evening", task_id="titles",
        execution_profile=binding, model=model, db_path=path)
    model.understand(document=prepare_document_for_analysis(selected[0]))
    later = "2026-09-09T06:00:00+00:00"
    if changed:
        from dataclasses import replace
        original = documents[0]
        body = original.original_text + " 更正：项目已经取消。"
        version = store.append_document_version(document_id=original.document_id, source_key="fixture",
            external_id=original.document_id, canonical_url=None, content_sha256=sha256(body.encode()).hexdigest(),
            published_at=later, published_precision="exact", fetched_at=later, original_text=body,
            excerpt=None, fetch_version="fixture", metadata=original.metadata, created_at=later, db_path=path)
        documents = (replace(original, revision=version.revision, original_text=body, published_at=later, fetched_at=later),)
    store.enqueue_task(task_id="later", kind="evening_scan", idempotency_key="later", input_version="fixture",
        input_cutoff_at=later, payload={}, budget={}, created_at=later, db_path=path)
    next_binding = store.bind_task_execution(task_id="later", execution_config_id=binding["configId"],
        execution_config_revision=binding["revision"], binding_kind="scheduled", bound_at=later, db_path=path)
    base = DeepSeekDiscoveryModel(model._base.provider)
    base.set_execution_policy(next_binding["payload"]["discovery"])
    base.set_scan_cutoff(datetime.fromisoformat(later))
    next_model = _CheckpointedDiscoveryModel(base=base, task_id="later", execution_profile=next_binding,
        cutoff_at=datetime.fromisoformat(later), db_path=path, leaseguard=None)
    selected = select_title_documents(documents=documents, window_kind="evening", task_id="later",
        execution_profile=next_binding, model=next_model, db_path=path)
    next_model.understand(document=prepare_document_for_analysis(selected[0]))
    assert sum(stage == "body" for stage, _ in calls) == (2 if changed else 1)
    assert next_model.fact_cache_hits == (0 if changed else 1)


def test_title_calls_normalize_the_known_sole_output_envelope(tmp_path, monkeypatch):
    path = tmp_path / "wrapped.sqlite"
    documents, binding, model = _setup(path, count=3, duplicate=False)
    calls = _mock_http(monkeypatch, sole_output=True)
    selected = select_title_documents(documents=documents, window_kind="morning", task_id="titles",
        execution_profile=binding, model=model, db_path=path)
    assert len(selected) == 3
    assert [stage for stage, _ in calls] == ["titles", "global", "review"]
    assert store.external_attempt_summary(task_id="titles", db_path=path)["succeeded"] == 3
    assert model.understand(document=prepare_document_for_analysis(selected[0])) == ()
    assert [stage for stage, _ in calls] == ["titles", "global", "review", "body"]


def test_title_final_review_only_removes_without_refill_and_is_reused_on_resume(tmp_path, monkeypatch):
    path = tmp_path / "review.sqlite"
    documents, binding, model = _setup(path, count=44, duplicate=False)
    calls = _mock_http(monkeypatch, remove_last=True)
    selected = select_title_documents(documents=documents, window_kind="morning", task_id="titles",
        execution_profile=binding, model=model, db_path=path)
    assert len(selected) == 39
    assert sum(stage == "review" for stage, _ in calls) == 1
    assert all(stage != "body" for stage, _ in calls)
    before = len(calls)
    assert select_title_documents(documents=documents, window_kind="morning", task_id="titles",
        execution_profile=binding, model=model, db_path=path) == selected
    assert len(calls) == before


def test_incomplete_final_title_review_keeps_every_body_closed(tmp_path, monkeypatch):
    path = tmp_path / "review-invalid.sqlite"
    documents, binding, model = _setup(path, count=4, duplicate=False)
    calls = _mock_http(monkeypatch, invalid_review=True)
    with pytest.raises(PipelineError):
        select_title_documents(documents=documents, window_kind="morning", task_id="titles",
            execution_profile=binding, model=model, db_path=path)
    assert store.read_title_selection_manifest(task_id="titles", db_path=path) is None
    assert all(stage != "body" for stage, _ in calls)


def test_body_shape_repair_reuses_one_article_admission_and_records_both_requests(tmp_path, monkeypatch):
    path = tmp_path / "body-repair.sqlite"
    documents, binding, model = _setup(path, count=1, duplicate=False)
    calls = _mock_http(monkeypatch, invalid_body_once=True)
    selected = select_title_documents(documents=documents, window_kind="morning", task_id="titles",
        execution_profile=binding, model=model, db_path=path)
    assert model.understand(document=prepare_document_for_analysis(selected[0])) == ()
    assert sum(stage == "body" for stage, _ in calls) == 2
    assert len(store.read_title_selection_manifest(task_id="titles", db_path=path)["selectedRefs"]) == 1
    assert store.external_attempt_summary(task_id="titles", db_path=path)["succeeded"] == len(calls)
