"""Question-bound, replay-safe access to the five approved Jin10 tools."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from . import store
from .jin10_mcp import Jin10Client, Jin10Error, TOOLS
from .schema import SqliteWriteBusy, read_connection


def _digest(value: Mapping[str, Any]) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_arguments(tool_name: str, arguments: Mapping[str, Any]) -> None:
    allowed = {
        "list_flash": (set(), {"cursor", "offset"}),
        "list_news": (set(), {"cursor", "offset"}),
        "get_news": ({"id"}, {"id"}),
        "search_flash": ({"keyword"}, {"keyword"}),
        "search_news": ({"keyword"}, {"keyword", "cursor", "offset"}),
    }
    required, fields = allowed[tool_name]
    if not required <= set(arguments) or not set(arguments) <= fields:
        raise ValueError("金十工具 wire 参数不符合已测契约")
    if "cursor" in arguments and "offset" in arguments:
        raise ValueError("金十分页 wire 只能使用一个位置参数")
    if any(not isinstance(value, str) or not value.strip() for value in arguments.values()):
        raise ValueError("金十工具参数必须是非空字符串")


def _business_result(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    if raw.get("isError") is True:
        raise Jin10Error("tool_is_error")
    structured = raw.get("structuredContent")
    if not isinstance(structured, Mapping):
        raise Jin10Error("structured_content_missing")
    status = structured.get("status")
    if status is not None and status not in {200, "200", "ok", "success"}:
        raise Jin10Error("business_status_error")
    return structured


def _has_existing_reply_or_pending(*, task_id: str, tool_name: str, attempt_key: str,
                                   input_hash: str, db_path: Path) -> bool:
    """No-token mode may replay a settled reply or report an unresolved wire."""
    with read_connection(db_path) as conn:
        task = conn.execute("SELECT checkpoint_json FROM k10_tasks WHERE task_id=?",
                            (task_id,)).fetchone()
        rows = conn.execute(
            "SELECT state,input_sha256,attempt_key FROM k10_external_attempts "
            "WHERE task_id=? AND stage=? AND (attempt_key=? OR attempt_key LIKE ?)",
            (task_id, "jin10:" + tool_name, attempt_key, attempt_key + ":retry:%"),
        ).fetchall()
    if task is None:
        return False
    try:
        checkpoint = json.loads(task[0])
        receipts = checkpoint.get("toolReceipts", {})
        return any(row[1] == input_hash and (
            row[0] in {"started", "unknown"} or (
                row[0] == "succeeded"
                and isinstance(receipts.get(row[2]), dict)
                and receipts[row[2]].get("inputSha256") == input_hash
                and isinstance(receipts[row[2]].get("result"), Mapping)
            )) for row in rows)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False


def _exact_settled_reply(*, task_id: str, attempt_id: str, attempt_key: str,
                         input_hash: str, raw: Mapping[str, Any], settled_at: str,
                         db_path: Path) -> bool:
    """Resolve an uncertain commit using the same attempt and exact reply."""
    with read_connection(db_path) as conn:
        row = conn.execute(
            "SELECT a.state,a.input_sha256,a.attempt_key,a.settled_at,t.checkpoint_json "
            "FROM k10_external_attempts a JOIN k10_tasks t ON t.task_id=a.task_id "
            "WHERE a.attempt_id=? AND a.task_id=?", (attempt_id, task_id),
        ).fetchone()
    if row is None or tuple(row[:4]) != ("succeeded", input_hash, attempt_key, settled_at):
        return False
    try:
        checkpoint = json.loads(row[4])
        receipt = checkpoint["toolReceipts"][attempt_key]
        return (isinstance(receipt, dict) and set(receipt) == {"inputSha256", "result", "receivedAt"}
                and receipt["inputSha256"] == input_hash and receipt["receivedAt"] == settled_at
                and isinstance(receipt["result"], Mapping) and _digest(receipt["result"]) == _digest(raw))
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return False


def _settle_received_reply(*, task_id: str, attempt_id: str, attempt_key: str,
                           input_hash: str, raw: Mapping[str, Any], settled_at: str,
                           db_path: Path) -> None:
    """Try one more local write after SQLite contention; never repost the tool."""
    kwargs = dict(task_id=task_id, attempt_id=attempt_id, attempt_key=attempt_key,
                  input_sha256=input_hash, result=raw, outcome="succeeded",
                  safe_error_code=None, settled_at=settled_at, db_path=db_path)
    try:
        store.settle_tool_attempt(**kwargs)
        return
    except SqliteWriteBusy:
        if _exact_settled_reply(task_id=task_id, attempt_id=attempt_id,
                                attempt_key=attempt_key, input_hash=input_hash,
                                raw=raw, settled_at=settled_at, db_path=db_path):
            return
    try:
        store.settle_tool_attempt(**kwargs)
    except (SqliteWriteBusy, store.K10Conflict):
        if _exact_settled_reply(task_id=task_id, attempt_id=attempt_id,
                                attempt_key=attempt_key, input_hash=input_hash,
                                raw=raw, settled_at=settled_at, db_path=db_path):
            return
        raise


def call_question_tool(
    *, task_id: str, question: str, target: str, purpose: str,
    tool_name: str, arguments: Mapping[str, Any], db_path: Path,
    client: Jin10Client, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    leaseguard: Callable[[], None] | None = None,
    new_external_admission_guard: Callable[[], None] | None = None,
) -> Mapping[str, Any]:
    """Call or replay exact input. A sent request with unknown outcome stays blocked."""
    if tool_name not in TOOLS or not all(isinstance(v, str) and v.strip() for v in (question, target, purpose)):
        raise ValueError("工具调用必须有获准工具与具体问题、目标和用途")
    _validate_arguments(tool_name, arguments)
    # Wire identity is independent of the prose question. One report task can
    # cite the same paid reply for several questions; a future collection slot
    # is a new task and must fetch the list again.
    intent = {"service": "jin10", "endpoint": client.endpoint,
              "protocolVersion": client.protocol_version, "taskId": task_id,
              "tool": tool_name, "arguments": dict(arguments)}
    input_hash = _digest(intent)
    attempt_key = "jin10:" + input_hash
    now = clock()
    if now.tzinfo is None:
        raise ValueError("工具时钟必须带时区")
    if leaseguard:
        leaseguard()
    if isinstance(client, Jin10Client) and not client.has_credential and not _has_existing_reply_or_pending(
        task_id=task_id, tool_name=tool_name, attempt_key=attempt_key,
        input_hash=input_hash, db_path=db_path,
    ):
        raise Jin10Error("credential_missing", pre_send=True)
    begun = store.begin_tool_attempt(task_id=task_id, stage="jin10:" + tool_name,
                                      item_key=target, attempt_key=attempt_key,
                                      input_sha256=input_hash, started_at=now.isoformat(),
                                      db_path=db_path)
    if begun["state"] == "reused":
        store.record_tool_question_link(task_id=task_id, input_sha256=input_hash,
                                        question=question, target=target, purpose=purpose,
                                        db_path=db_path)
        return _business_result(begun["result"])
    if begun["state"] != "started":
        raise Jin10Error("outcome_unknown" if begun["state"] == "pending_outcome" else str(begun["state"]),
                         unknown=begun["state"] == "pending_outcome")
    attempt_id = str(begun["attemptId"])
    attempt_key = str(begun["attemptKey"])

    def require_new_wire() -> None:
        # The MCP handshake may involve several HTTP requests before the
        # billable tools/call. A control closing during discovery must stop
        # the next request, while the already-returned response remains
        # eligible for local settlement below.
        try:
            if leaseguard:
                leaseguard()
            if new_external_admission_guard is not None:
                new_external_admission_guard()
            task = store.get_task(task_id=task_id, db_path=db_path)
            if task is None or store.task_control_status(kind=task.kind, db_path=db_path)["state"] != "open":
                raise Jin10Error("closed_before_send", pre_send=True)
        except Jin10Error:
            raise
        except Exception:
            raise Jin10Error("closed_before_send", pre_send=True) from None

    try:
        # The second check closes the normal claim/close race. A control that
        # closes after the request starts still permits its response to settle.
        if leaseguard:
            leaseguard()
        if new_external_admission_guard is not None:
            try:
                new_external_admission_guard()
            except Exception:
                store.settle_tool_attempt(task_id=task_id, attempt_id=attempt_id,
                                          attempt_key=attempt_key, input_sha256=input_hash,
                                          result=None, outcome="failed", safe_error_code="closed_before_send",
                                          settled_at=clock().isoformat(), db_path=db_path)
                raise Jin10Error("collection_paused") from None
        if store.task_control_status(kind=store.get_task(task_id=task_id, db_path=db_path).kind,
                                     db_path=db_path)["state"] != "open":
            store.settle_tool_attempt(task_id=task_id, attempt_id=attempt_id,
                                      attempt_key=attempt_key, input_sha256=input_hash,
                                      result=None, outcome="failed", safe_error_code="closed_before_send",
                                      settled_at=clock().isoformat(), db_path=db_path)
            raise Jin10Error("collection_paused")
        raw = (client.call_tool_raw(tool_name, arguments, admission_guard=require_new_wire)
               if isinstance(client, Jin10Client) else client.call_tool_raw(tool_name, arguments))
    except Jin10Error as exc:
        if exc.code == "collection_paused":
            raise
        store.settle_tool_attempt(task_id=task_id, attempt_id=attempt_id,
                                  attempt_key=attempt_key, input_sha256=input_hash,
                                  result=None, outcome="unknown" if exc.unknown and not exc.pre_send else "failed",
                                  safe_error_code="pre_send_error" if exc.pre_send else exc.code,
                                  settled_at=clock().isoformat(), db_path=db_path)
        raise
    # Durable receipt precedes business parsing; changed parsers never rebill.
    _settle_received_reply(task_id=task_id, attempt_id=attempt_id,
                           attempt_key=attempt_key, input_hash=input_hash,
                           raw=raw, settled_at=clock().isoformat(), db_path=db_path)
    store.record_tool_question_link(task_id=task_id, input_sha256=input_hash,
                                    question=question, target=target, purpose=purpose,
                                    db_path=db_path)
    return _business_result(raw)


__all__ = ["call_question_tool"]
