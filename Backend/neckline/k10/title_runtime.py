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
    validate_reconcile_result,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ref(document: DiscoveryDocument) -> dict[str, Any]:
    return {"documentId": document.document_id, "revision": document.revision}


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def read_title_failures(*, task_id: str, db_path: Path) -> list[dict[str, Any]]:
    """Read durable local gaps, never relabel them as successful title work."""
    from .schema import read_connection
    with read_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT input_sha256,status,result_json FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND stage='title_batch_gap' ORDER BY item_key", (task_id,),
        ).fetchall()
    result = []
    for digest, status, raw in rows:
        value = json.loads(raw) if isinstance(raw, str) else None
        if (status != "completed" or not isinstance(value, dict) or _digest(value) != digest
                or not isinstance(value.get("inputRefs"), list)
                or not isinstance(value.get("batchRefs"), list)
                or not isinstance(value.get("batchIndex"), int)
                or not isinstance(value.get("reasonCode"), str)):
            raise TitleTriageProtocolError("标题失败范围检查点不可读取")
        result.append(value)
    return result


class _FrozenTitleGap(Exception):
    def __init__(self, code: str):
        self.code = code


def _local_failure_code(exc: BaseException) -> str | None:
    code = getattr(exc, "code", None)
    if isinstance(exc, _FrozenTitleGap):
        return code
    if code in {"content_policy_refused", "json_invalid", "json_root_invalid", "model_json_invalid",
                "model_json_root_invalid", "model_result_not_json", "model_result_root_invalid",
                "model_result_unsafe", "title_protected_merge_invalid"}:
        return code
    if isinstance(code, str) and code.startswith("title_json_"):
        return code
    # Accounting ambiguity, broken inputs, pause/deadline, configuration and
    # storage failures retain their global control semantics.
    return None


def _filter_company_hints(value: Any, allowed: set[str]) -> Any:
    """Optional routing hints cannot invalidate an otherwise usable title.

    Applied on both fresh responses and cached decode without changing request
    identities or rewriting paid checkpoints. Preserve the article itself.
    """
    if not isinstance(value, Mapping) or not isinstance(value.get("items"), list):
        return value
    return {**value, "items": [
        {**row, "companyCodes": [code for code in row["companyCodes"]
                                if isinstance(code, str) and code in allowed]
         if isinstance(row["companyCodes"], list) else []}
        if isinstance(row, Mapping) and "companyCodes" in row else row
        for row in value["items"]]}


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
    prior_manifest = store.read_title_triage_manifest(task_id=task_id, db_path=db_path)
    frozen = store.read_title_selection_manifest(task_id=task_id, db_path=db_path)
    if frozen is not None:
        if (prior_manifest is None or prior_manifest["inputManifestSha256"] != _digest(input_refs)
                or prior_manifest["windowKind"] != window_kind
                or prior_manifest["policyContentSha256"] != approved["contentSha256"]
                or prior_manifest["policyId"] != approved["policyId"]
                or prior_manifest["policyRevision"] != approved["revision"]
                or prior_manifest["inputCount"] != input_count
                or prior_manifest["batchCount"] != (len(titles) + batch_size - 1) // batch_size):
            raise TitleTriageProtocolError("冻结标题选择不匹配当前输入或策略")
        selected = frozen["selectedRefs"]
        if len(selected) > input_count or any((r["documentId"], r["revision"]) not in by_ref for r in selected):
            raise TitleTriageProtocolError("冻结的正文名单不匹配本轮来源")
        return tuple(by_ref[(row["documentId"], row["revision"])] for row in selected)
    store.freeze_title_triage_manifest(
        task_id=task_id, input_manifest_sha256=_digest(input_refs), window_kind=window_kind,
        policy_id=approved["policyId"], policy_revision=approved["revision"],
        policy_content_sha256=approved["contentSha256"], input_count=input_count,
        input_refs=input_refs, batch_count=(len(titles) + batch_size - 1) // batch_size,
        title_status="frozen", created_at=_now(), db_path=db_path,
    )
    saved_gaps = {row["batchIndex"]: row for row in read_title_failures(task_id=task_id, db_path=db_path)}
    restored_gaps = tuple(saved_gaps.values())

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
            try:
                normalized = validate(value)
            except TitleTriageProtocolError as exc:
                # This callback validates a returned answer, not the frozen
                # input. Even bookkeeping errors deserve the bound JSON repair.
                if "json" not in exc.code:
                    exc.code = "title_json_contract_invalid"
                raise
            return dict(normalized) if isinstance(normalized, Mapping) else dict(value)
        return model.run_title_operation(stage=stage, instruction=instruction, payload=payload, validate=check)

    def batch_call(items):
        refs = [{"documentId": item.document_id, "revision": item.revision} for item in items]
        for gap in restored_gaps:
            if gap["batchRefs"] == refs:
                raise _FrozenTitleGap(gap["reasonCode"])
        instruction, payload = batch_request_spec(items, title_policy)
        binding = getattr(model, "_company_profiles_binding", None)
        if binding is None:
            binding = getattr(getattr(model, "_base", None), "_company_profiles_binding", None)
        if binding is not None:
            from .v2_profiles import read_profiles, retrieve_company_context, company_index_matches
            index = read_profiles(db_path=binding[0], profiles_id=binding[1], index_only=True)
            text = " ".join(item.title for item in items).casefold()
            routing = retrieve_company_context(db_path=binding[0], profiles_id=binding[1], query=text)
            recalled = [row for row in index if row["ts_code"] in routing["candidateCompanyCodes"]]
            # Persist conservative dependencies before a title request can fail.
            # These hints can only exclude an affected company, never recommend it.
            from .schema import write_connection
            dependencies = [(item, set(company_index_matches(recalled, item.title))) for item in items]
            with write_connection(db_path) as conn:
                for item, local_codes in dependencies:
                    existing = conn.execute("SELECT company_codes_json FROM k10_v2_title_company_hints WHERE task_id=? AND document_id=? AND revision=?",
                                            (task_id, item.document_id, item.revision)).fetchone()
                    codes = local_codes | (set(json.loads(existing[0])) if existing else set())
                    conn.execute("INSERT INTO k10_v2_title_company_hints VALUES (?,?,?,?) ON CONFLICT(task_id,document_id,revision) DO UPDATE SET company_codes_json=excluded.company_codes_json",
                                 (task_id, item.document_id, item.revision, json.dumps(sorted(codes))))
            payload = {**payload, "fixedPool": [{"companyCode": row["ts_code"], "name": row["name"]} for row in index], "recalledCompanyIndex": recalled}
            payload["output"]["items"][0]["companyCodes"] = ["possible fixedPool company code"]
            instruction += "companyCodes返回可能关联的池内公司，可为空；无合理关联时说明。只研究fixedPool内公司。索引未命中不能跳过标题；依据语义识别合理关联，需要正文则保留。索引是待核线索而非事实。不得逐标题搜索。"
        allowed_codes = {row["ts_code"] for row in index} if binding is not None else None
        def normalize(value):
            if allowed_codes is not None:
                value = _filter_company_hints(value, allowed_codes)
            return normalize_batch_result(value, items)
        raw = operation("titleBatch", instruction, payload, normalize)
        results = validate_batch_result(raw, items)
        if binding is not None:
            from .schema import write_connection
            with write_connection(db_path) as conn:
                for result in results:
                    existing = conn.execute("SELECT company_codes_json FROM k10_v2_title_company_hints WHERE task_id=? AND document_id=? AND revision=?", (task_id,result.document_id,result.revision)).fetchone()
                    codes = set(result.company_codes) | (set(json.loads(existing[0])) if existing else set())
                    conn.execute("INSERT INTO k10_v2_title_company_hints VALUES (?,?,?,?) ON CONFLICT(task_id,document_id,revision) DO UPDATE SET company_codes_json=excluded.company_codes_json",
                                 (task_id,result.document_id,result.revision,json.dumps(sorted(codes))))
        return results

    def reconcile_call(items, results, limit):
        instruction, payload = reconcile_request_spec(items, results, limit, title_policy)
        instruction += "同一发布的不同细节在本次全局归并中一并判断；筛选理由不是新增事实依据。纠正、否认及重大反证不得被普通正面消息吞并。"
        # Even an empty answer is explicitly validated, not inferred from a failed call.
        if all(result.status == "no_value" for result in results):
            raw = {"selected": [], "merged": [], "notSelected": []}
        else:
            raw = operation("titleReconcile", instruction, payload,
                lambda value: normalize_reconcile_result(value, items, results, limit))
        return validate_reconcile_result(raw, items, results, limit)

    def isolate_batch(index, items, exc):
        code = _local_failure_code(exc)
        if code is None:
            return False
        refs = [{"documentId": item.document_id, "revision": item.revision} for item in items]
        representative_keys = {item.ref for item in items}
        affected = representative_keys | {ref for ref, target in duplicates.items() if target in representative_keys}
        value = {"batchIndex": index, "batchRefs": refs, "reasonCode": code,
                 "inputRefs": [{"documentId": ref[0], "revision": ref[1]} for ref in sorted(affected)]}
        old = saved_gaps.get(index)
        if old is not None and old != value:
            raise TitleTriageProtocolError("标题失败范围不可静默改写")
        if old is None:
            store.record_execution_checkpoint(
                task_id=task_id, item_kind="global", item_key=f"title-batch-gap:{index}",
                stage="title_batch_gap", input_sha256=_digest(value), status="completed",
                attempt_count=0, network_attempt_count=0, repair_attempt_count=0, elapsed_ms=0,
                input_tokens=None, output_tokens=None, result=value, safe_error_code=None,
                safe_error_ref=None, updated_at=_now(), db_path=db_path, leaseguard=guard,
            )
            saved_gaps[index] = value
        if progress is not None:
            progress({"stage": "title_triage_batch", "state": "failed", **value})
        return True

    selection = triage_titles(titles, window_kind=window_kind, policy=title_policy,
        batch_call=batch_call, reconcile_call=reconcile_call,
        batch_concurrency=batch_concurrency, checkpoint=progress, isolate_batch_failure=isolate_batch)
    final_items = selection.items
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
        if representative not in initial:
            continue
        store.record_title_triage_item(task_id=task_id, document_id=ref[0], revision=ref[1],
            batch_index=batches[representative], disposition="exact_duplicate", matter_key=None,
            merged_ref={"documentId": representative[0], "revision": representative[1]}, selection_rank=None,
            audit_reason="标题和正文完全相同，复用代表文章的筛选结果", created_at=_now(), db_path=db_path)
    selected_refs = [{"documentId": ref[0], "revision": ref[1]} for ref in final_refs]
    if guard is not None:
        guard()
    store.freeze_title_selection_manifest(task_id=task_id, selection_manifest_sha256=_digest(selected_refs),
        selected_refs=selected_refs, created_at=_now(), db_path=db_path,
        failed_refs=[ref for gap in saved_gaps.values() for ref in gap["inputRefs"]])
    return tuple(by_ref[ref] for ref in final_refs)
