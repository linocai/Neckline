"""Independent, resumable B92 news collection handler."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx

from . import store
from .collection_config import COLLECTION_CONTRACT, SOURCE_KEYS, validate_collection_config
from .collection_gateway import call_question_tool
from .ingestion import SqliteIngestionWriter
from .jin10_mcp import Jin10Client, Jin10Error
from .jin10_normalize import persist_question_tool_result
from .sources import SourceFetchRequest
from .tushare_news import TUSHARE_API_URL, TuShareMajorNewsAdapter
from .windows import SHANGHAI, ScanWindow
from .worker import TaskContext, TaskResult


def _hash(value: Mapping[str, Any]) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("采集时间必须带时区")
    return parsed


def collection_config_for_report(*, db_path: Path,
                                 collection_task_ids: list[str],
                                 binding: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Resolve only the exact question-tool profile frozen by the report scan.

    Mixed historical source configurations are valid input provenance. Missing
    or ambiguous question-tool binding disables new calls, not local research.
    """
    if not isinstance(binding, Mapping):
        return None
    task_id, config_id = binding.get("taskId"), binding.get("configId")
    revision, content_hash = binding.get("revision"), binding.get("contentHash")
    if (not isinstance(task_id, str) or task_id not in collection_task_ids
            or not isinstance(config_id, str) or not isinstance(revision, int)
            or isinstance(revision, bool) or not isinstance(content_hash, str)):
        return None
    task = store.get_task(task_id=task_id, db_path=db_path)
    if task is None or task.kind != "collect_news":
        return None
    payload = task.payload
    if (payload.get("configId"), payload.get("configRevision"), payload.get("configHash")) != (
            config_id, revision, content_hash):
        return None
    config = store.read_execution_config(config_id=config_id, revision=revision, db_path=db_path)
    if config is None or config["contentSha256"] != content_hash or not validate_collection_config(config["payload"]).ready:
        return None
    return config


def _source_start(*, config: Mapping[str, Any], source_key: str, slot: datetime,
                  db_path: Path) -> datetime:
    bootstrap = _instant(str(config["bootstrapStartAt"]))
    old = store.latest_source_watermark(source_key=source_key, db_path=db_path)
    if old is None:
        return bootstrap
    previous = _instant(str(old["successCutoffAt"]))
    if previous >= slot:
        return slot
    lookback = timedelta(seconds=int(config["lookbackSeconds"]))
    return max(bootstrap, previous - lookback)


def _base_state(source_key: str, start: datetime, slot: datetime) -> dict[str, Any]:
    return {"sourceKey": source_key, "state": "running",
            "requestedStartAt": start.isoformat(), "requestedEndAt": slot.isoformat(),
            "coverageThrough": None, "observedStartAt": None, "observedEndAt": None,
            "cursor": None, "cursorField": None, "pagesFetched": 0, "pageFingerprints": [],
            "documentRefs": [], "gaps": [], "limitations": [], "errorCode": None}


def _observed(state: dict[str, Any], refs: list[Mapping[str, Any]]) -> None:
    times = [str(ref["publishedAt"]) for ref in refs if isinstance(ref.get("publishedAt"), str)]
    if not times:
        return
    values = [value for value in (state.get("observedStartAt"), state.get("observedEndAt")) if isinstance(value, str)]
    values.extend(times)
    state["observedStartAt"] = min(values, key=_instant)
    state["observedEndAt"] = max(values, key=_instant)


def _save(context: TaskContext, source_key: str, state: Mapping[str, Any], *,
          watermark_through: str | None = None) -> dict[str, Any]:
    return store.save_collection_source_checkpoint(
        task_id=context.task.task_id, worker_id=str(context.task.lease_owner),
        source_key=source_key, source_state=state, saved_at=context.clock(),
        db_path=context.db_path, watermark_through=watermark_through,
        leaseguard=context.require_lease)


def _jin10_source(context: TaskContext, *, source_key: str, config: Mapping[str, Any],
                  mcp: Mapping[str, Any], slot: datetime, token: str | None,
                  client_factory: Callable[..., Jin10Client]) -> dict[str, Any]:
    start = _source_start(config=config, source_key=source_key, slot=slot, db_path=context.db_path)
    sources = context.checkpoint.get("sources") if isinstance(context.checkpoint, Mapping) else None
    stored = sources.get(source_key) if isinstance(sources, Mapping) else None
    state = dict(stored) if isinstance(stored, Mapping) else _base_state(source_key, start, slot)
    if state.get("state") == "completed":
        return state
    tool_name = "list_flash" if source_key == "jin10-flash" else "list_news"
    seen = set(state.get("pageFingerprints") or [])
    cursor = state.get("cursor")

    def require_next_request() -> None:
        context.require_lease()
        if store.task_control_status(kind="collect_news", db_path=context.db_path)["state"] != "open":
            raise Jin10Error("collection_paused", pre_send=True)

    try:
        with client_factory(token=token, endpoint=mcp["endpoint"],
                            protocol_version=mcp["protocolVersion"],
                            timeout_seconds=int(config["timeoutSeconds"])) as client:
            while int(state["pagesFetched"]) < int(config["maxPages"]):
                context.require_lease()
                if store.task_control_status(kind="collect_news", db_path=context.db_path)["state"] != "open":
                    state.update(state="partial", errorCode="collection_paused")
                    break
                args = {str(state.get("cursorField") or "cursor"): cursor} if cursor else {}
                fingerprint = _hash({"tool": tool_name, "arguments": args})
                if fingerprint in seen:
                    state.update(state="partial", errorCode="cursor_loop")
                    break
                structured = call_question_tool(
                    task_id=context.task.task_id,
                    question=f"按 {slot.isoformat()} 固定槽采集 {source_key} 可见列表",
                    target=f"{source_key}:{slot.isoformat()}", purpose="scheduled_collection",
                    tool_name=tool_name, arguments=args, db_path=context.db_path,
                    client=client, clock=context.clock, leaseguard=context.require_lease)
                result = persist_question_tool_result(
                    task_id=context.task.task_id, tool_name=tool_name, structured=structured,
                    obtained_at=context.clock(), question="scheduled_collection",
                    target=source_key, db_path=context.db_path, leaseguard=context.require_lease)
                page_refs = result["documentRefs"]
                # A credential can disappear after a paid reply but before
                # this page checkpoint. Replay the settled reply locally even
                # with no token, and do not duplicate refs if a later restart
                # has to revisit this same wire to recover its cursor.
                known_refs = {(ref["documentId"], ref["revision"])
                              for ref in state["documentRefs"]}
                new_refs = list(state["documentRefs"])
                for ref in page_refs:
                    key = (ref["documentId"], ref["revision"])
                    if key not in known_refs:
                        new_refs.append(ref)
                        known_refs.add(key)
                state["documentRefs"] = new_refs
                _observed(state, page_refs)
                next_cursor = result["coverage"]["nextCursor"]
                has_more = result["coverage"]["hasMore"]
                if has_more:
                    require_next_request()
                    parameter = (client.pagination_argument(tool_name, admission_guard=require_next_request)
                                 if isinstance(client, Jin10Client) else client.pagination_argument(tool_name))
                else:
                    parameter = None
                if has_more and parameter not in {"cursor", "offset"}:
                    state.update(state="partial", errorCode="pagination_parameter_unavailable")
                seen.add(fingerprint)
                state["pageFingerprints"] = list(state["pageFingerprints"]) + [fingerprint]
                state["pagesFetched"] = int(state["pagesFetched"]) + 1
                state["cursorField"] = parameter if parameter in {"cursor", "offset"} else None
                state["cursor"] = next_cursor if has_more and state["cursorField"] else None
                # Version writes and response receipt have committed before the
                # traversal cursor moves. A crash here replays the exact wire.
                _save(context, source_key, state)
                if state.get("errorCode") == "pagination_parameter_unavailable":
                    break
                if not has_more:
                    oldest = state.get("observedStartAt")
                    if not oldest or _instant(str(oldest)) > start:
                        state.update(state="partial", errorCode="history_unavailable",
                                     gaps=[{"startAt": start.isoformat(), "endAt": oldest or slot.isoformat(),
                                            "reasonCode": "history_unavailable"}])
                        _save(context, source_key, state)
                    else:
                        state.update(state="completed", coverageThrough=slot.isoformat(), errorCode=None)
                        _save(context, source_key, state, watermark_through=slot.isoformat())
                    return state
                if next_cursor == cursor:
                    state.update(state="partial", errorCode="cursor_not_advanced")
                    break
                cursor = next_cursor
            else:
                state.update(state="partial", errorCode="page_limit")
    except Jin10Error as exc:
        no_new_credential = exc.code == "credential_missing" and not token
        state.update(state=("unavailable" if no_new_credential and not state["documentRefs"] else "partial"),
                     errorCode=("collection_paused" if exc.code == "collection_paused"
                                else "credential_missing" if no_new_credential
                                else "pre_send_error" if exc.pre_send else exc.code))
    state["limitations"] = list(dict.fromkeys([*state.get("limitations", []), str(state.get("errorCode"))]))
    _save(context, source_key, state)
    return state


def _tushare_source(context: TaskContext, *, config: Mapping[str, Any], slot: datetime,
                    token: str | None,
                    request_callable: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None) -> dict[str, Any]:
    source_key = "tushare-major-news"
    start = _source_start(config=config, source_key=source_key, slot=slot, db_path=context.db_path)
    sources = context.checkpoint.get("sources") if isinstance(context.checkpoint, Mapping) else None
    stored = sources.get(source_key) if isinstance(sources, Mapping) else None
    state = dict(stored) if isinstance(stored, Mapping) else _base_state(source_key, start, slot)
    if state.get("state") == "completed":
        return state
    if not token:
        state.update(state="unavailable", errorCode="credential_missing", limitations=["credential_missing"])
        _save(context, source_key, state)
        return state

    def request(payload: Mapping[str, object]) -> Mapping[str, object]:
        context.require_lease()
        if store.task_control_status(kind="collect_news", db_path=context.db_path)["state"] != "open":
            raise RuntimeError("collection_paused")
        wire = {key: value for key, value in payload.items() if key != "token"}
        input_hash = _hash({"service": "tushare", "taskId": context.task.task_id, "wire": wire})
        attempt_key = "tushare:" + input_hash
        begun = store.begin_tool_attempt(task_id=context.task.task_id, stage="tushare:major_news",
                                          item_key=source_key, attempt_key=attempt_key,
                                          input_sha256=input_hash, started_at=context.clock().isoformat(),
                                          db_path=context.db_path)
        if begun["state"] == "reused":
            return begun["result"]
        if begun["state"] != "started":
            raise RuntimeError("tushare_outcome_unknown")
        attempt_id = str(begun["attemptId"])
        attempt_key = str(begun["attemptKey"])
        try:
            context.require_lease()
            if store.task_control_status(kind="collect_news", db_path=context.db_path)["state"] != "open":
                store.settle_tool_attempt(task_id=context.task.task_id, attempt_id=attempt_id,
                                          attempt_key=attempt_key, input_sha256=input_hash,
                                          result=None, outcome="failed", safe_error_code="closed_before_send",
                                          settled_at=context.clock().isoformat(), db_path=context.db_path)
                raise RuntimeError("collection_paused")
            if request_callable:
                result = request_callable(payload)
            else:
                with httpx.Client(timeout=float(config["timeoutSeconds"]), follow_redirects=False) as client:
                    response = client.post(TUSHARE_API_URL, json=dict(payload))
                if response.status_code != 200 or response.is_redirect:
                    raise RuntimeError("tushare_http_failure")
                result = response.json()
            if not isinstance(result, Mapping):
                raise RuntimeError("tushare_response_invalid")
            store.settle_tool_attempt(task_id=context.task.task_id, attempt_id=attempt_id,
                                      attempt_key=attempt_key, input_sha256=input_hash,
                                      result=result, outcome="succeeded", safe_error_code=None,
                                      settled_at=context.clock().isoformat(), db_path=context.db_path)
            return result
        except Exception:
            # If no explicit known HTTP response exists, sending may have
            # occurred. Preserve unknown and never blindly resend.
            try:
                store.settle_tool_attempt(task_id=context.task.task_id, attempt_id=attempt_id,
                                          attempt_key=attempt_key, input_sha256=input_hash,
                                          result=None, outcome="unknown", safe_error_code="transport_unknown",
                                          settled_at=context.clock().isoformat(), db_path=context.db_path)
            except store.K10Conflict:
                pass
            raise

    adapter = TuShareMajorNewsAdapter(token=token, request_bound=int(config["maxPages"]),
                                      request_callable=request, clock=context.clock)
    window = ScanWindow("evening", start, slot, True, True)
    result = adapter.fetch_incremental(SourceFetchRequest(window=window, previous_cursor=None,
        source_success_watermark=None))
    writer = SqliteIngestionWriter(db_path=context.db_path, leaseguard=context.require_lease)
    refs = list(state.get("documentRefs") or [])
    for document in result.documents:
        stored_doc = writer.append_document_version(source_key=source_key, document=document)
        refs.append({"documentId": stored_doc.version.document_id,
                     "revision": stored_doc.version.revision, "sourceKey": source_key,
                     "contentSha256": stored_doc.version.content_hash,
                     "publishedAt": document.published_at.isoformat() if document.published_at else None,
                     "fetchedAt": document.fetched_at.isoformat()})
    state["documentRefs"] = refs
    _observed(state, refs)
    state["pagesFetched"] = result.pages_fetched
    if result.complete:
        state.update(state="completed", coverageThrough=slot.isoformat(), errorCode=None)
        _save(context, source_key, state, watermark_through=slot.isoformat())
    else:
        state.update(state="partial", errorCode=(result.errors[0] if result.errors else "source_partial"),
                     limitations=list(result.errors))
        _save(context, source_key, state)
    return state


def create_collection_handler(
    *, tushare_token: str | None, jin10_token: str | None,
    client_factory: Callable[..., Jin10Client] = Jin10Client,
    tushare_request: Callable[[Mapping[str, object]], Mapping[str, object]] | None = None,
) -> Callable[[TaskContext], TaskResult]:
    def handle(context: TaskContext) -> TaskResult:
        payload = context.task.payload
        if payload.get("collectionContract") != COLLECTION_CONTRACT:
            return TaskResult("not_configured", "configuration", context.checkpoint, "采集契约无效")
        config = store.read_execution_config(config_id=payload["configId"],
                                             revision=payload["configRevision"],
                                             db_path=context.db_path)
        if config is None or config["contentSha256"] != payload.get("configHash") or not validate_collection_config(config["payload"]).ready:
            return TaskResult("not_configured", "configuration", context.checkpoint, "采集配置 revision 无效")
        slot = _instant(payload["slotAt"]).astimezone(SHANGHAI)
        sources = {item["sourceKey"]: item for item in config["payload"]["sources"]}
        outcomes: list[dict[str, Any]] = []
        for key in SOURCE_KEYS:
            if store.task_control_status(kind="collect_news", db_path=context.db_path)["state"] != "open":
                break
            try:
                if key == "tushare-major-news":
                    outcome = _tushare_source(context, config=sources[key], slot=slot,
                                              token=tushare_token, request_callable=tushare_request)
                else:
                    outcome = _jin10_source(context, source_key=key, config=sources[key],
                                            mcp=config["payload"]["mcp"], slot=slot,
                                            token=jin10_token, client_factory=client_factory)
            except store.K10Conflict:
                raise
            except Exception:
                # A source failure must not suppress the other sources, and
                # exception bodies may include provider-owned private data.
                outcome = _base_state(key, _source_start(config=sources[key], source_key=key,
                                    slot=slot, db_path=context.db_path), slot)
                outcome.update(state="failed", errorCode="source_execution_failed",
                               limitations=["source_execution_failed"])
                _save(context, key, outcome)
            outcomes.append(outcome)
        checkpoint = store.task_execution_input(task_id=context.task.task_id,
                                               db_path=context.db_path)["checkpoint"]
        status = "completed" if len(outcomes) == len(SOURCE_KEYS) and all(
            item["state"] == "completed" for item in outcomes) else "failed"
        if status == "failed" and any(
            item.get("errorCode") in {"rate_limited", "pre_send_error"} for item in outcomes
        ):
            return TaskResult("failed", "collection_retry", checkpoint, "采集限流或握手失败，同一任务延后续跑",
                              retry_at=context.clock() + timedelta(seconds=30),
                              retry_kind="failure", safe_error_code="collection_retryable_source")
        return TaskResult(status, "collection_complete" if status == "completed" else "collection_partial",
                          checkpoint, None if status == "completed" else "采集存在逐源缺口")
    return handle


__all__ = ["collection_config_for_report", "create_collection_handler"]
