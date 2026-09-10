"""Task-bound title selection. Only sealed title DTOs reach either model pass."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import store
from .discovery import DiscoveryDocument, deduplicate_documents
from .title_triage import (
    TitleTriageProtocolError, batch_request_spec, reconcile_request_spec,
    normalize_batch_result, normalize_reconcile_result, title_from_document_fields, triage_titles, validate_batch_result,
    validate_reconcile_result, review_request_spec, normalize_review_result, apply_title_review,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ref(document: DiscoveryDocument) -> dict[str, Any]:
    return {"documentId": document.document_id, "revision": document.revision}


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def select_title_documents(
    *, documents: Sequence[DiscoveryDocument], window_kind: str, task_id: str,
    execution_profile: Mapping[str, Any], model: Any, db_path: Path,
    guard: Callable[[], None] | None = None,
    progress: Callable[[Mapping[str, Any]], None] | None = None,
    known_subjects: Sequence[Mapping[str, Any]] = (),
) -> tuple[DiscoveryDocument, ...]:
    """Freeze one immutable selection; resumption never acquires another quota.

    Original text is used only for deterministic exact-copy detection here.
    Neither it nor arbitrary source metadata is included in a model request.
    """
    policy = execution_profile["payload"]["discovery"]
    approved = policy["titleTriagePolicy"]
    input_count = len(documents)
    batch_size = policy["titleBatchSize"]
    batch_concurrency = policy["titleTriageConcurrency"]
    if (isinstance(batch_concurrency, bool) or not isinstance(batch_concurrency, int)
            or batch_concurrency < 1):
        raise TitleTriageProtocolError("标题批次并发数无效")
    sources = tuple(sorted(documents, key=lambda doc: (doc.document_id, doc.revision)))
    input_refs = [_ref(doc) for doc in sources]
    by_ref = {(doc.document_id, doc.revision): doc for doc in sources}
    if len(by_ref) != len(sources):
        raise TitleTriageProtocolError("来源引用重复")

    exact = deduplicate_documents(sources)
    duplicates = {(ref.document_id, ref.revision): (target.document_id, target.revision)
                  for ref, target in exact.duplicates.items()}
    titles = []
    for document in exact.retained:
        title = title_from_document_fields(
            document_id=document.document_id, revision=document.revision,
            source_key=document.metadata.get("sourceKey"), published_at=document.published_at,
            metadata=document.metadata,
        )
        titles.append(title)

    if guard is not None:
        guard()
    store.freeze_title_triage_manifest(
        task_id=task_id, input_manifest_sha256=_digest(input_refs), window_kind=window_kind,
        policy_id=approved["policyId"], policy_revision=approved["revision"],
        policy_content_sha256=approved["contentSha256"], input_count=input_count,
        input_refs=input_refs, batch_count=(len(titles) + batch_size - 1) // batch_size,
        title_status="frozen", created_at=_now(), db_path=db_path,
    )
    frozen = store.read_title_selection_manifest(task_id=task_id, db_path=db_path)
    if frozen is not None:
        selected = frozen["selectedRefs"]
        if len(selected) > input_count or any((r["documentId"], r["revision"]) not in by_ref for r in selected):
            raise TitleTriageProtocolError("冻结的正文名单不匹配本轮来源")
        return tuple(by_ref[(row["documentId"], row["revision"])] for row in selected)

    title_policy = {**approved, "batchSize": batch_size}
    # Known subjects are separate trusted routing context, never old article bodies.
    context = [{key: item[key] for key in ("companyCode", "headline")
                if isinstance(item.get(key), str)} for item in known_subjects]

    def operation(stage, instruction, payload, validate):
        if guard is not None:
            guard()
        if context:
            payload = {**payload, "knownSubjects": context}
            instruction += "已有正式候选仅用于优先识别重大反证和事实变化；不得把这些旧标题当作本轮新增消息。"
        def check(value):
            if not isinstance(value, Mapping):
                raise TitleTriageProtocolError("标题输出不是 JSON 对象")
            normalized = validate(value)
            return dict(normalized) if isinstance(normalized, Mapping) else dict(value)
        return model.run_title_operation(stage=stage, instruction=instruction, payload=payload, validate=check)

    def batch_call(items):
        instruction, payload = batch_request_spec(items, title_policy)
        binding = getattr(model, "_company_profiles_binding", None)
        if binding is None:
            binding = getattr(getattr(model, "_base", None), "_company_profiles_binding", None)
        if binding is not None:
            from .v2_profiles import read_profiles, retrieve_company_context
            index = read_profiles(db_path=binding[0], profiles_id=binding[1], index_only=True)
            text = " ".join(item.title for item in items).casefold()
            routing = retrieve_company_context(db_path=binding[0], profiles_id=binding[1], query=text)
            recalled = [row for row in index if row["ts_code"] in routing["candidateCompanyCodes"]]
            payload = {**payload, "fixedPool": [{"companyCode": row["ts_code"], "name": row["name"]} for row in index], "recalledCompanyIndex": recalled}
            payload["output"]["items"][0]["companyCodes"] = ["possible fixedPool company code"]
            instruction += "companyCodes返回可能关联的池内公司，可为空；无合理关联时说明。只研究fixedPool内公司。索引未命中不能跳过标题；依据语义识别合理关联，需要正文则保留。索引是待核线索而非事实。不得逐标题搜索。"
        raw = operation("titleBatch", instruction, payload, lambda value: normalize_batch_result(value, items))
        results = validate_batch_result(raw, items)
        if binding is not None:
            from .schema import write_connection
            allowed_codes = {row["ts_code"] for row in index}
            with write_connection(db_path) as conn:
                for result in results:
                    if not set(result.company_codes) <= allowed_codes:
                        raise TitleTriageProtocolError("标题模型引用池外公司")
                    content = json.dumps(list(result.company_codes), sort_keys=True)
                    existing = conn.execute("SELECT company_codes_json FROM k10_v2_title_company_hints WHERE task_id=? AND document_id=? AND revision=?", (task_id,result.document_id,result.revision)).fetchone()
                    if existing and existing[0] != content:
                        raise TitleTriageProtocolError("冻结标题公司线索不可覆盖")
                    conn.execute("INSERT OR IGNORE INTO k10_v2_title_company_hints VALUES (?,?,?,?)", (task_id,result.document_id,result.revision,content))
        return results

    def reconcile_call(items, results, limit):
        instruction, payload = reconcile_request_spec(items, results, limit, title_policy)
        # Even an empty answer is explicitly validated, not inferred from a failed call.
        if all(result.status == "no_value" for result in results):
            raw = {"selected": [], "merged": [], "notSelected": []}
        else:
            raw = operation("titleReconcile", instruction, payload,
                lambda value: normalize_reconcile_result(value, items, results, limit))
        return validate_reconcile_result(raw, items, results, limit)

    selection = triage_titles(titles, window_kind=window_kind, policy=title_policy,
        batch_call=batch_call, reconcile_call=reconcile_call,
        batch_concurrency=batch_concurrency, checkpoint=progress)
    final_items = selection.items
    if selection.selected_refs:
        instruction, payload = review_request_spec(titles, selection.batch_results, selection, title_policy)
        review = operation("titleReconcile", instruction, payload,
            lambda value: normalize_review_result(value, titles, selection.batch_results, selection))
        final_items = apply_title_review(titles, selection.batch_results, selection, review)
    final_refs = tuple(item.ref for item in sorted(
        (item for item in final_items if item.disposition == "selected"),
        key=lambda item: item.selected_rank))
    batches = {title.ref: index // batch_size for index, title in enumerate(titles)}
    initial = {result.ref: result for result in selection.batch_results}
    for item in final_items:
        status = initial[item.ref].status
        disposition = ("no_value" if item.disposition == "no_value" else "merged" if item.disposition == "merged" else "protected" if status == "correction_or_denial"
                       else "uncertain" if item.disposition == "selected" and status == "uncertain"
                       else "candidate" if item.disposition == "selected" else "not_selected")
        store.record_title_triage_item(task_id=task_id, document_id=item.document_id, revision=item.revision,
            batch_index=batches[item.ref], disposition=disposition, matter_key=item.matter_key,
            merged_ref=None if item.merged_into is None else {"documentId": item.merged_into[0], "revision": item.merged_into[1]},
            selection_rank=item.selected_rank, audit_reason=item.reason, created_at=_now(), db_path=db_path)
    for ref, representative in duplicates.items():
        store.record_title_triage_item(task_id=task_id, document_id=ref[0], revision=ref[1],
            batch_index=batches[representative], disposition="exact_duplicate", matter_key=None,
            merged_ref={"documentId": representative[0], "revision": representative[1]}, selection_rank=None,
            audit_reason="标题和正文完全相同，复用代表文章的筛选结果", created_at=_now(), db_path=db_path)
    selected_refs = [{"documentId": ref[0], "revision": ref[1]} for ref in final_refs]
    if guard is not None:
        guard()
    store.freeze_title_selection_manifest(task_id=task_id, selection_manifest_sha256=_digest(selected_refs),
        selected_refs=selected_refs, created_at=_now(), db_path=db_path)
    return tuple(by_ref[ref] for ref in final_refs)
