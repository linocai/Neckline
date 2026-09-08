"""K10 的追加式 SQLite 存储。

本模块从不调用迁移。每个入口都要求明确 ``db_path``；读操作经 ``mode=ro`` 打开
既有 schema，写操作只在调用方已显式执行 ``initialize_schema`` 后运行。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from .schema import read_connection, require_schema, write_connection
from .config import validate_execution_config
from .research_contracts import EvidenceDisclosure, ResearchContractError
from .types import CompanyWindowObservation, DocumentVersion, EventRevision, Observation, OpportunityPublicationInput, PublicationBatch, Task


class K10Conflict(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _utc_instant(value: str | datetime) -> str:
    """Validate a stored ISO timestamp and return its canonical instant.

    Scan ingestion deliberately persists timestamps in UTC, while the scheduler keeps its
    human-facing Shanghai cutoff.  A report belongs to the scan's instant, not one spelling
    of that instant.
    """
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise K10Conflict("晨报截止时间无效") from exc
    if parsed.tzinfo is None:
        raise K10Conflict("晨报截止时间必须带时区")
    return parsed.astimezone(timezone.utc).isoformat()


def _require_write_schema(conn) -> None:
    require_schema(conn)


def append_run_config(
    *, config_id: str, payload: Mapping[str, Any], created_at: str, db_path: Path
) -> int:
    """追加配置版本；相同内容重放返回原版本，内容变化绝不原地覆盖。"""
    fingerprint = _hash(payload)
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        old = conn.execute(
            "SELECT revision FROM k10_run_config_revisions WHERE config_id=? AND content_sha256=?",
            (config_id, fingerprint),
        ).fetchone()
        if old is not None:
            return int(old[0])
        row = conn.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM k10_run_config_revisions WHERE config_id=?",
            (config_id,),
        ).fetchone()
        revision = int(row[0]) + 1
        conn.execute(
            "INSERT INTO k10_run_config_revisions(config_id,revision,payload_json,content_sha256,created_at) "
            "VALUES(?,?,?,?,?)",
            (config_id, revision, _json(payload), fingerprint, created_at),
        )
    return revision


def read_run_config(*, config_id: str, revision: int, db_path: Path) -> Optional[dict[str, Any]]:
    """精确读取不可变配置修订；不存在不回退到“当前”或任何默认。"""
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT payload_json,content_sha256,created_at FROM k10_run_config_revisions WHERE config_id=? AND revision=?",
            (config_id, revision),
        ).fetchone()
    if row is None:
        return None
    return {"configId": config_id, "revision": revision, "payload": json.loads(row[0]),
            "contentSha256": row[1], "createdAt": row[2]}


def append_execution_config(
    *, config_id: str, payload: Mapping[str, Any], created_at: str, db_path: Path,
) -> int:
    """Append an immutable execution profile; it never mutates a strategy revision."""
    fingerprint = _hash(payload)
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        old = conn.execute(
            "SELECT revision FROM k10_execution_config_revisions WHERE config_id=? AND content_sha256=?",
            (config_id, fingerprint),
        ).fetchone()
        if old is not None:
            return int(old[0])
        revision = int(conn.execute(
            "SELECT COALESCE(MAX(revision),0) FROM k10_execution_config_revisions WHERE config_id=?", (config_id,)
        ).fetchone()[0]) + 1
        conn.execute(
            "INSERT INTO k10_execution_config_revisions(config_id,revision,payload_json,content_sha256,created_at) "
            "VALUES(?,?,?,?,?)", (config_id, revision, _json(payload), fingerprint, created_at),
        )
    return revision


def read_execution_config(*, config_id: str, revision: int, db_path: Path) -> Optional[dict[str, Any]]:
    """Read the exact operational revision; absence never falls back to a latest profile."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT payload_json,content_sha256,created_at FROM k10_execution_config_revisions "
            "WHERE config_id=? AND revision=?", (config_id, revision),
        ).fetchone()
    if row is None:
        return None
    return {"configId": config_id, "revision": revision, "payload": json.loads(row[0]),
            "contentSha256": row[1], "createdAt": row[2]}


def append_title_triage_policy(
    *, policy_id: str, content: Mapping[str, Any], approval_state: str,
    created_at: str, approved_at: str | None, db_path: Path,
) -> int:
    """Append one immutable, explicitly approved title policy revision."""
    if not isinstance(policy_id, str) or not policy_id.strip() or not isinstance(content, Mapping) or not content:
        raise ValueError("标题筛选 policy 必须有非空 ID 与内容")
    if approval_state not in {"draft", "approved", "retired"} or (approval_state == "approved") != (approved_at is not None):
        raise ValueError("标题筛选 policy 的批准状态无效")
    fingerprint = _hash(content)
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        old = conn.execute(
            "SELECT revision,approval_state,approved_at FROM k10_title_triage_policy_revisions "
            "WHERE policy_id=? AND content_sha256=?", (policy_id, fingerprint),
        ).fetchone()
        if old is not None:
            if tuple(old[1:]) != (approval_state, approved_at):
                raise K10Conflict("同一标题 policy 内容的批准状态不可被静默改写")
            return int(old[0])
        revision = int(conn.execute(
            "SELECT COALESCE(MAX(revision),0) FROM k10_title_triage_policy_revisions WHERE policy_id=?", (policy_id,)
        ).fetchone()[0]) + 1
        conn.execute(
            "INSERT INTO k10_title_triage_policy_revisions(policy_id,revision,content_json,content_sha256,approval_state,approved_at,created_at) "
            "VALUES(?,?,?,?,?,?,?)", (policy_id, revision, _json(content), fingerprint, approval_state, approved_at, created_at),
        )
    return revision


def read_title_triage_policy(*, policy_id: str, revision: int, db_path: Path) -> Optional[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT content_json,content_sha256,approval_state,approved_at,created_at "
            "FROM k10_title_triage_policy_revisions WHERE policy_id=? AND revision=?", (policy_id, revision),
        ).fetchone()
    if row is None:
        return None
    return {"policyId": policy_id, "revision": revision, "content": json.loads(row[0]), "contentSha256": row[1],
            "approvalState": row[2], "approvedAt": row[3], "createdAt": row[4]}


def _title_refs(value: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("标题 manifest 必须是明确 documentId/revision 引用列表")
    refs: list[dict[str, Any]] = []
    keys: set[tuple[str, int]] = set()
    for raw in value:
        if (not isinstance(raw, Mapping) or not isinstance(raw.get("documentId"), str) or not raw["documentId"].strip()
                or isinstance(raw.get("revision"), bool) or not isinstance(raw.get("revision"), int) or raw["revision"] < 1):
            raise ValueError("标题 manifest 引用必须精确包含 documentId 与 revision")
        key = (raw["documentId"], raw["revision"])
        if key in keys:
            raise ValueError("标题 manifest 不可重复同一来源文章")
        keys.add(key)
        refs.append({"documentId": key[0], "revision": key[1]})
    return refs


def freeze_title_triage_manifest(
    *, task_id: str, input_manifest_sha256: str, window_kind: str, policy_id: str, policy_revision: int,
    policy_content_sha256: str, article_limit: int, input_refs: Sequence[Mapping[str, Any]], batch_count: int,
    title_status: str, created_at: str, db_path: Path,
) -> dict[str, Any]:
    """Freeze every title input before any body admission can be created."""
    if window_kind not in {"evening", "morning"} or article_limit != {"evening": 80, "morning": 40}.get(window_kind):
        raise ValueError("标题筛选必须使用晚间 80、晨间 40 的正文限额")
    if title_status not in {"frozen", "partial"} or not isinstance(batch_count, int) or batch_count < 0:
        raise ValueError("标题 manifest 状态或批次数无效")
    refs = _title_refs(input_refs)
    if input_manifest_sha256 != _hash(refs) or len(policy_content_sha256) != 64:
        raise ValueError("标题 manifest 哈希不匹配")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        policy = conn.execute(
            "SELECT content_sha256,approval_state FROM k10_title_triage_policy_revisions WHERE policy_id=? AND revision=?",
            (policy_id, policy_revision),
        ).fetchone()
        if policy is None or policy[0] != policy_content_sha256 or policy[1] != "approved":
            raise K10Conflict("标题 manifest 未绑定已批准 policy")
        if conn.execute("SELECT 1 FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone() is None:
            raise K10Conflict("标题 manifest 任务不存在")
        expected = (input_manifest_sha256, window_kind, policy_id, policy_revision, policy_content_sha256,
                    article_limit, _json(refs), batch_count, title_status)
        old = conn.execute(
            "SELECT input_manifest_sha256,window_kind,policy_id,policy_revision,policy_content_sha256,article_limit,"
            "input_refs_json,batch_count,title_status,selection_status FROM k10_title_triage_manifests WHERE task_id=?", (task_id,),
        ).fetchone()
        if old is not None:
            if tuple(old[:9]) != expected or old[9] not in {"pending", "frozen"}:
                raise K10Conflict("冻结标题 manifest 不可被重写")
        else:
            conn.execute(
                "INSERT INTO k10_title_triage_manifests(task_id,input_manifest_sha256,window_kind,policy_id,policy_revision,"
                "policy_content_sha256,article_limit,input_refs_json,batch_count,title_status,selection_status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (task_id, *expected, "pending", created_at),
            )
    return read_title_triage_manifest(task_id=task_id, db_path=db_path)  # type: ignore[return-value]


def read_title_triage_manifest(*, task_id: str, db_path: Path) -> Optional[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT input_manifest_sha256,window_kind,policy_id,policy_revision,policy_content_sha256,article_limit,"
            "input_refs_json,batch_count,title_status,selection_status,created_at FROM k10_title_triage_manifests WHERE task_id=?", (task_id,),
        ).fetchone()
    if row is None:
        return None
    return {"taskId": task_id, "inputManifestSha256": row[0], "windowKind": row[1], "policyId": row[2],
            "policyRevision": int(row[3]), "policyContentSha256": row[4], "articleLimit": int(row[5]),
            "inputRefs": json.loads(row[6]), "batchCount": int(row[7]), "titleStatus": row[8],
            "selectionStatus": row[9], "createdAt": row[10]}


def record_title_triage_item(
    *, task_id: str, document_id: str, revision: int, batch_index: int, disposition: str, matter_key: str | None,
    merged_ref: Mapping[str, Any] | None, selection_rank: int | None, audit_reason: str, created_at: str, db_path: Path,
) -> None:
    """Record one title result once; it cannot be changed after global freeze."""
    if disposition not in {"candidate", "uncertain", "protected", "not_selected", "merged", "no_value", "exact_duplicate"} or not document_id or revision < 1:
        raise ValueError("标题筛选条目缺少有效来源或去向")
    if batch_index < 0 or (selection_rank is not None and (isinstance(selection_rank, bool) or selection_rank < 1)) or not audit_reason.strip():
        raise ValueError("标题筛选条目的批次、排名或理由无效")
    merged = None if merged_ref is None else _title_refs([merged_ref])[0]
    if disposition in {"exact_duplicate", "merged"} and merged is None:
        raise ValueError("精确重复或语义合并标题必须指向代表文章")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        manifest = conn.execute("SELECT input_refs_json,selection_status FROM k10_title_triage_manifests WHERE task_id=?", (task_id,)).fetchone()
        if manifest is None:
            raise K10Conflict("标题筛选条目没有冻结 manifest")
        if manifest[1] != "pending":
            raise K10Conflict("全局选择已冻结，标题筛选条目不可改写")
        if {"documentId": document_id, "revision": revision} not in json.loads(manifest[0]):
            raise K10Conflict("标题筛选条目不在冻结输入内")
        expected = (batch_index, disposition, matter_key, None if merged is None else merged["documentId"],
                    None if merged is None else merged["revision"], selection_rank, audit_reason)
        old = conn.execute(
            "SELECT batch_index,disposition,matter_key,merged_document_id,merged_revision,selection_rank,audit_reason "
            "FROM k10_title_triage_items WHERE task_id=? AND document_id=? AND revision=?", (task_id, document_id, revision),
        ).fetchone()
        if old is not None:
            if tuple(old) != expected:
                raise K10Conflict("标题筛选条目不可被静默改写")
            return
        conn.execute(
            "INSERT INTO k10_title_triage_items(task_id,document_id,revision,batch_index,disposition,matter_key,"
            "merged_document_id,merged_revision,selection_rank,audit_reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, document_id, revision, *expected, created_at),
        )


def read_title_triage_items(*, task_id: str, db_path: Path) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        rows = conn.execute(
            "SELECT document_id,revision,batch_index,disposition,matter_key,merged_document_id,merged_revision,selection_rank,audit_reason,created_at "
            "FROM k10_title_triage_items WHERE task_id=? ORDER BY batch_index,document_id,revision", (task_id,),
        ).fetchall()
    return [{"documentId": row[0], "revision": int(row[1]), "batchIndex": int(row[2]), "disposition": row[3],
             "matterKey": row[4], "mergedRef": None if row[5] is None else {"documentId": row[5], "revision": int(row[6])},
             "selectionRank": None if row[7] is None else int(row[7]), "auditReason": row[8], "createdAt": row[9]} for row in rows]


def freeze_title_selection_manifest(
    *, task_id: str, selection_manifest_sha256: str, selected_refs: Sequence[Mapping[str, Any]], created_at: str, db_path: Path,
) -> dict[str, Any]:
    """Atomically freeze selected article membership before any body call."""
    refs = _title_refs(selected_refs)
    if selection_manifest_sha256 != _hash(refs):
        raise ValueError("全局选择 manifest 哈希不匹配")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        manifest = conn.execute(
            "SELECT input_refs_json,article_limit,title_status,selection_status FROM k10_title_triage_manifests WHERE task_id=?", (task_id,),
        ).fetchone()
        if manifest is None:
            raise K10Conflict("全局选择没有标题 manifest")
        if manifest[2] != "frozen":
            raise K10Conflict("标题筛选不完整，禁止冻结正文选择")
        if len(refs) > int(manifest[1]):
            raise K10Conflict("入选正文超过 80/40 限额")
        input_keys = {(item["documentId"], item["revision"]) for item in json.loads(manifest[0])}
        if any((item["documentId"], item["revision"]) not in input_keys for item in refs):
            raise K10Conflict("全局选择包含未参与标题初筛的文章")
        rows = conn.execute(
            "SELECT document_id,revision,selection_rank,disposition,merged_document_id,merged_revision "
            "FROM k10_title_triage_items WHERE task_id=?", (task_id,)
        ).fetchall()
        if len(rows) != len(input_keys):
            raise K10Conflict("标题筛选未覆盖全部输入，禁止冻结正文选择")
        ranks = {(str(row[0]), int(row[1])): row[2] for row in rows}
        selected_keys = {(item["documentId"], item["revision"]) for item in refs}
        if {key for key, rank in ranks.items() if rank is not None} != selected_keys:
            raise K10Conflict("selection rank 只能属于冻结入选文章")
        if [ranks.get((item["documentId"], item["revision"])) for item in refs] != list(range(1, len(refs) + 1)):
            raise K10Conflict("全局选择必须与逐标题审计的连续 rank 一致")
        for row in rows:
            if row[3] == "merged" and (str(row[4]), int(row[5])) not in selected_keys:
                raise K10Conflict("语义合并必须指向冻结入选文章")
        old = conn.execute(
            "SELECT selection_manifest_sha256,selected_refs_json FROM k10_title_selection_manifests WHERE task_id=?", (task_id,),
        ).fetchone()
        expected = (selection_manifest_sha256, _json(refs))
        if old is not None:
            if tuple(old) != expected:
                raise K10Conflict("全局选择 manifest 不可被重写")
        else:
            conn.execute("INSERT INTO k10_title_selection_manifests(task_id,selection_manifest_sha256,selected_refs_json,created_at) VALUES(?,?,?,?)",
                         (task_id, *expected, created_at))
            # Selection itself owns every 80/40 slot.  Reserving all members
            # here prevents a concurrent deep-read/Tavily path from consuming
            # a later selected article's slot before it starts.
            conn.executemany(
                "INSERT INTO k10_article_admissions(task_id,document_id,revision,admission_kind,state,reason_code,admitted_at,completed_at) "
                "VALUES(?,?,?,'selected','admitted',NULL,?,NULL)",
                [(task_id, item["documentId"], item["revision"], created_at) for item in refs],
            )
            conn.execute("UPDATE k10_title_triage_manifests SET selection_status='frozen' WHERE task_id=?", (task_id,))
    return read_title_selection_manifest(task_id=task_id, db_path=db_path)  # type: ignore[return-value]


def read_title_selection_manifest(*, task_id: str, db_path: Path) -> Optional[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT selection_manifest_sha256,selected_refs_json,created_at FROM k10_title_selection_manifests WHERE task_id=?", (task_id,),
        ).fetchone()
    return None if row is None else {"taskId": task_id, "selectionManifestSha256": row[0], "selectedRefs": json.loads(row[1]), "createdAt": row[2]}


def admit_article(
    *, task_id: str, document_id: str, revision: int, admission_kind: str, created_at: str, db_path: Path,
) -> dict[str, Any]:
    """Atomically admit one real article body; an article never gains a second slot."""
    if not document_id or isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("正文 admission 必须精确引用 documentId 与 revision")
    if admission_kind not in {"selected", "tavily_full_article"}:
        raise ValueError("正文 admission 类型无效")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        manifest = conn.execute(
            "SELECT article_limit,selection_status FROM k10_title_triage_manifests WHERE task_id=?", (task_id,),
        ).fetchone()
        if manifest is None or manifest[1] != "frozen":
            return {"state": "not_frozen", "reason": "title_selection_not_frozen", "admissionKind": admission_kind}
        old = conn.execute(
            "SELECT admission_kind,state,reason_code FROM k10_article_admissions WHERE task_id=? AND document_id=? AND revision=?",
            (task_id, document_id, revision),
        ).fetchone()
        if old is not None:
            if old[0] != admission_kind:
                raise K10Conflict("同一文章不能以不同 admission 类型占两次名额")
            return {"state": "reused", "reason": old[2], "admissionKind": old[0], "articleState": old[1]}
        selected = conn.execute(
            "SELECT 1 FROM k10_title_selection_manifests m JOIN k10_title_triage_items i ON i.task_id=m.task_id "
            "WHERE m.task_id=? AND i.document_id=? AND i.revision=? AND i.selection_rank IS NOT NULL",
            (task_id, document_id, revision),
        ).fetchone()
        if admission_kind == "selected" and selected is None:
            return {"state": "not_selected", "reason": "article_not_in_frozen_selection", "admissionKind": admission_kind}
        used = int(conn.execute("SELECT COUNT(*) FROM k10_article_admissions WHERE task_id=?", (task_id,)).fetchone()[0])
        if used >= int(manifest[0]):
            return {"state": "limit_reached", "reason": "article_limit_reached", "admissionKind": admission_kind}
        conn.execute(
            "INSERT INTO k10_article_admissions(task_id,document_id,revision,admission_kind,state,reason_code,admitted_at,completed_at) "
            "VALUES(?,?,?,?, 'admitted',NULL,?,NULL)", (task_id, document_id, revision, admission_kind, created_at),
        )
    return {"state": "admitted", "reason": None, "admissionKind": admission_kind, "articleState": "admitted"}


def record_article_outcome(
    *, task_id: str, document_id: str, revision: int, state: str, reason_code: str | None,
    updated_at: str, db_path: Path,
) -> dict[str, Any]:
    """Record a selected member's body outcome without ever changing membership."""
    stored_state = {"completed": "completed", "missing": "missing_body", "missing_body": "missing_body", "failed": "failed"}.get(state)
    if stored_state is None or not document_id or revision < 1:
        raise ValueError("正文 outcome 无效")
    if stored_state == "failed" and not reason_code:
        raise ValueError("正文失败必须带安全 reason_code")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        old = conn.execute(
            "SELECT state,reason_code FROM k10_article_admissions WHERE task_id=? AND document_id=? AND revision=?",
            (task_id, document_id, revision),
        ).fetchone()
        if old is None:
            raise K10Conflict("正文 outcome 没有 article admission")
        if old[0] in {"completed", "missing_body"}:
            if tuple(old) != (stored_state, reason_code):
                raise K10Conflict("已终结正文 outcome 不可被改写")
            return {"state": old[0], "reason": old[1]}
        if old[0] == "failed" and stored_state not in {"failed", "completed", "missing_body"}:
            raise K10Conflict("失败正文只可重试为 completed 或 missing_body")
        conn.execute(
            "UPDATE k10_article_admissions SET state=?,reason_code=?,completed_at=? WHERE task_id=? AND document_id=? AND revision=?",
            (stored_state, reason_code, updated_at, task_id, document_id, revision),
        )
    return {"state": stored_state, "reason": reason_code}


def _bound_v3_config(conn, *, task_id: str) -> tuple[Mapping[str, Any] | None, str | None]:
    row = conn.execute(
        "SELECT c.payload_json FROM k10_task_execution_bindings b JOIN k10_execution_config_revisions c "
        "ON c.config_id=b.execution_config_id AND c.revision=b.execution_config_revision WHERE b.task_id=?", (task_id,),
    ).fetchone()
    if row is None:
        return None, "execution_unbound"
    payload = json.loads(row[0])
    if not validate_execution_config(payload).ready or payload.get("executionVersion") != "k10-execution-v3":
        return None, "execution_not_configured"
    policy = payload["discovery"]["titleTriagePolicy"]
    stored = conn.execute(
        "SELECT content_json,content_sha256,approval_state FROM k10_title_triage_policy_revisions WHERE policy_id=? AND revision=?",
        (policy["policyId"], policy["revision"]),
    ).fetchone()
    if stored is None or stored[1] != policy["contentSha256"] or stored[2] != "approved" or json.loads(stored[0]) != policy["content"]:
        return None, "title_triage_policy_not_approved"
    return payload, None


_EXTERNAL_USAGE_FIELDS = ("promptTokens", "completionTokens", "totalTokens", "searchRequests", "searchCredits")


def _external_usage(value: Mapping[str, Any] | None) -> dict[str, int | None]:
    if value is None:
        return {field: None for field in _EXTERNAL_USAGE_FIELDS}
    if not isinstance(value, Mapping) or set(value) != set(_EXTERNAL_USAGE_FIELDS):
        raise ValueError("外部调用 usage 必须精确声明 token 与搜索字段")
    result: dict[str, int | None] = {}
    for field in _EXTERNAL_USAGE_FIELDS:
        number = value[field]
        if number is not None and (isinstance(number, bool) or not isinstance(number, int) or number < 0):
            raise ValueError(f"外部调用 usage.{field} 必须是非负整数或 null")
        result[field] = number
    return result


def is_discovery_retired(*, task_id: str | None = None, scan_id: str | None = None, db_path: Path) -> Optional[dict[str, Any]]:
    if bool(task_id) == bool(scan_id):
        raise ValueError("retirement 查询必须精确指定 task_id 或 scan_id")
    column, value = ("task_id", task_id) if task_id else ("scan_id", scan_id)
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            f"SELECT scan_id,task_id,reason_code,retired_at FROM k10_discovery_retirements WHERE {column}=?", (value,),
        ).fetchone()
    return None if row is None else {"scanId": row[0], "taskId": row[1], "reasonCode": row[2], "retiredAt": row[3]}


def retire_interrupted_discovery(
    *, scan_id: str, task_id: str, reason_code: str, retired_at: str, db_path: Path,
) -> dict[str, Any]:
    """Atomically retire an abandoned discovery input without erasing its audit history."""
    if not all(isinstance(value, str) and value for value in (scan_id, task_id, reason_code, retired_at)):
        raise ValueError("discovery retirement 缺少 scan、task、原因或时间")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        scan = conn.execute("SELECT coverage_json FROM k10_scans WHERE scan_id=?", (scan_id,)).fetchone()
        task = conn.execute("SELECT 1 FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()
        if scan is None or task is None:
            raise K10Conflict("discovery retirement 的 scan 或 task 不存在")
        binding = conn.execute("SELECT task_id FROM k10_scan_execution_bindings WHERE scan_id=?", (scan_id,)).fetchone()
        if binding is not None and binding[0] != task_id:
            raise K10Conflict("discovery retirement 的 task 不属于该 scan")
        old = conn.execute(
            "SELECT scan_id,task_id,reason_code,retired_at FROM k10_discovery_retirements WHERE scan_id=? OR task_id=?",
            (scan_id, task_id),
        ).fetchone()
        expected = (scan_id, task_id, reason_code, retired_at)
        if old is not None:
            if tuple(old) != expected:
                raise K10Conflict("discovery retirement 不可被静默改写")
            return {"scanId": old[0], "taskId": old[1], "reasonCode": old[2], "retiredAt": old[3]}
        try:
            coverage = json.loads(scan[0])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise K10Conflict("scan coverage 无效，拒绝覆盖 retirement 事实") from exc
        if not isinstance(coverage, Mapping):
            raise K10Conflict("scan coverage 无效，拒绝覆盖 retirement 事实")
        updated_coverage = dict(coverage)
        updated_coverage["retiredByUser"] = {"retiredAt": retired_at, "reasonCode": reason_code}
        conn.execute("DELETE FROM k10_task_retry_schedules WHERE task_id=?", (task_id,))
        conn.execute(
            "UPDATE k10_tasks SET status='cancelled',stage='retired_by_user',lease_owner=NULL,lease_until=NULL,error_text=?,updated_at=? "
            "WHERE task_id=?", (reason_code, retired_at, task_id),
        )
        conn.execute(
            "UPDATE k10_scans SET status='partial',coverage_json=?,completed_at=? WHERE scan_id=?",
            (_json(updated_coverage), retired_at, scan_id),
        )
        conn.execute(
            "INSERT INTO k10_discovery_retirements(scan_id,task_id,reason_code,retired_at) VALUES(?,?,?,?)", expected,
        )
    return {"scanId": scan_id, "taskId": task_id, "reasonCode": reason_code, "retiredAt": retired_at}


def begin_external_attempt(
    *, task_id: str, stage: str, item_key: str, attempt_key: str, input_sha256: str,
    started_at: str, db_path: Path,
) -> dict[str, Any]:
    """Persist the exact external request intent before sending it, without a total cap."""
    if not all(isinstance(value, str) and value for value in (task_id, stage, item_key, attempt_key, input_sha256, started_at)):
        raise ValueError("外部调用 attempt 缺少 task、stage、item、输入哈希或时间")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        control = conn.execute("SELECT state,reason_code FROM k10_run_controls WHERE control_key='k10_discovery'").fetchone()
        if control is None or control[0] != "open":
            return {"state": "paused", "reason": "control_missing" if control is None else control[1], "attemptId": None}
        retired = conn.execute("SELECT reason_code FROM k10_discovery_retirements WHERE task_id=?", (task_id,)).fetchone()
        if retired is not None:
            return {"state": "retired", "reason": retired[0], "attemptId": None}
        _, config_error = _bound_v3_config(conn, task_id=task_id)
        if config_error is not None:
            return {"state": "not_configured", "reason": config_error, "attemptId": None}
        old = conn.execute(
            "SELECT attempt_id,state,input_sha256 FROM k10_external_attempts WHERE task_id=? AND attempt_key=?", (task_id, attempt_key),
        ).fetchone()
        if old is not None:
            if old[2] != input_sha256:
                raise K10Conflict("同一外部调用 attempt_key 不可对应不同输入")
            old_state = str(old[1])
            return {"state": "pending_outcome" if old_state == "started" else "reused", "reason": None, "attemptId": old[0]}
        attempt_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO k10_external_attempts(attempt_id,task_id,stage,item_key,attempt_key,input_sha256,state,started_at) "
            "VALUES(?,?,?,?,?,?, 'started',?)", (attempt_id, task_id, stage, item_key, attempt_key, input_sha256, started_at),
        )
    return {"state": "started", "reason": None, "attemptId": attempt_id}


def settle_external_attempt(
    *, attempt_id: str, outcome: str, usage: Mapping[str, Any] | None,
    settled_at: str, error_code: str | None, db_path: Path,
) -> dict[str, Any]:
    """Settle one started attempt with actual or explicitly unavailable provider usage."""
    if outcome not in {"succeeded", "failed", "unknown"} or not attempt_id or not settled_at:
        raise ValueError("外部调用结算 outcome 或身份无效")
    if outcome == "failed" and not error_code:
        raise ValueError("失败外部调用必须记录安全错误码")
    values = _external_usage(usage)
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        old = conn.execute(
            "SELECT state,prompt_tokens,completion_tokens,total_tokens,search_requests,search_credits,error_code "
            "FROM k10_external_attempts WHERE attempt_id=?", (attempt_id,),
        ).fetchone()
        if old is None:
            raise K10Conflict("外部调用 attempt 不存在")
        expected = (outcome, values["promptTokens"], values["completionTokens"], values["totalTokens"],
                    values["searchRequests"], values["searchCredits"], error_code)
        if old[0] != "started":
            if tuple(old) != expected:
                raise K10Conflict("已结算外部调用不可被改写")
            return {"state": old[0], "attemptId": attempt_id}
        conn.execute(
            "UPDATE k10_external_attempts SET state=?,prompt_tokens=?,completion_tokens=?,total_tokens=?,search_requests=?,"
            "search_credits=?,error_code=?,settled_at=? WHERE attempt_id=?",
            (*expected, settled_at, attempt_id),
        )
    return {"state": outcome, "attemptId": attempt_id}


def external_attempt_summary(*, task_id: str, db_path: Path) -> dict[str, Any]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        rows = conn.execute(
            "SELECT state,prompt_tokens,completion_tokens,total_tokens,search_requests,search_credits FROM k10_external_attempts WHERE task_id=?",
            (task_id,),
        ).fetchall()
    counts = {state: 0 for state in ("started", "succeeded", "failed", "unknown")}
    usage = {field: 0 for field in _EXTERNAL_USAGE_FIELDS}
    for row in rows:
        counts[str(row[0])] += 1
        for index, field in enumerate(_EXTERNAL_USAGE_FIELDS, start=1):
            if row[index] is not None:
                usage[field] += int(row[index])
    return {**counts, "actualUsage": usage}


def store_fact_cache(
    *, cache_key: str, source_refs: Sequence[Mapping[str, Any]], eligible_at: str,
    template_content_sha256: str, model: str, prompt_input_sha256: str,
    result: Mapping[str, Any] | list[Any], created_at: str, db_path: Path,
) -> None:
    """Store a validated generic fact derivative, never raw source/model text.

    The caller supplies the cache identity from source versions and every
    material template/model/prompt input.  A later cutoff reuses a fact only
    when its original eligible instant is inside that cutoff.
    """
    if (not isinstance(cache_key, str) or not cache_key or not isinstance(template_content_sha256, str) or len(template_content_sha256) != 64
            or not isinstance(prompt_input_sha256, str) or len(prompt_input_sha256) != 64
            or not isinstance(model, str) or not model):
        raise ValueError("事实缓存身份不完整")
    canonical_eligible = _utc_instant(eligible_at)
    safe_refs = _safe_execution_result(list(source_refs))
    safe_result = _safe_execution_result(result)
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        expected = (_json(safe_refs), canonical_eligible, template_content_sha256, model, prompt_input_sha256, _json(safe_result))
        old = conn.execute(
            "SELECT source_refs_json,eligible_at,template_content_sha256,model,prompt_input_sha256,result_json "
            "FROM k10_fact_cache WHERE cache_key=?", (cache_key,),
        ).fetchone()
        if old is not None:
            if tuple(old) != expected:
                raise K10Conflict("事实缓存键不可对应不同输入或结果")
            return
        conn.execute(
            "INSERT INTO k10_fact_cache(cache_key,source_refs_json,eligible_at,template_content_sha256,model,prompt_input_sha256,result_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)", (cache_key, *expected, created_at),
        )


def read_fact_cache(*, cache_key: str, cutoff_at: str, db_path: Path) -> Optional[dict[str, Any]]:
    cutoff = _utc_instant(cutoff_at)
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT source_refs_json,eligible_at,template_content_sha256,model,prompt_input_sha256,result_json,created_at "
            "FROM k10_fact_cache WHERE cache_key=?", (cache_key,),
        ).fetchone()
    if row is None or str(row[1]) > cutoff:
        return None
    return {"cacheKey": cache_key, "sourceRefs": json.loads(row[0]), "eligibleAt": row[1],
            "templateContentSha256": row[2], "model": row[3], "promptInputSha256": row[4],
            "result": json.loads(row[5]), "createdAt": row[6]}


def run_control_status(*, db_path: Path) -> dict[str, Any]:
    """Read the durable discovery switch.  Its migration default is closed."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT state,reason_code,changed_at,changed_by FROM k10_run_controls WHERE control_key='k10_discovery'"
        ).fetchone()
    if row is None:
        return {"state": "closed", "reasonCode": "control_missing", "changedAt": None, "changedBy": None}
    return {"state": row[0], "reasonCode": row[1], "changedAt": row[2], "changedBy": row[3]}


def set_run_control(*, state: str, reason_code: str, changed_at: str, changed_by: str, db_path: Path) -> dict[str, Any]:
    """The only store write which can explicitly open a paused discovery runtime."""
    if state not in {"closed", "open"} or not isinstance(reason_code, str) or not reason_code.strip():
        raise ValueError("运行控制需要明确 state 与 reason_code")
    if not isinstance(changed_by, str) or not changed_by.strip():
        raise ValueError("运行控制需要明确 changed_by")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        conn.execute(
            "INSERT INTO k10_run_controls(control_key,state,reason_code,changed_at,changed_by) VALUES('k10_discovery',?,?,?,?) "
            "ON CONFLICT(control_key) DO UPDATE SET state=excluded.state,reason_code=excluded.reason_code,"
            "changed_at=excluded.changed_at,changed_by=excluded.changed_by",
            (state, reason_code, changed_at, changed_by),
        )
    return run_control_status(db_path=db_path)


def bind_task_execution(
    *, task_id: str, execution_config_id: str, execution_config_revision: int,
    binding_kind: str, bound_at: str, db_path: Path,
) -> dict[str, Any]:
    """Bind a task once to a profile, with a recorded content hash for audit and recovery."""
    if binding_kind not in {"scheduled", "recovery"}:
        raise ValueError("execution binding_kind 必须是 scheduled 或 recovery")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        profile = conn.execute(
            "SELECT content_sha256,payload_json FROM k10_execution_config_revisions WHERE config_id=? AND revision=?",
            (execution_config_id, execution_config_revision),
        ).fetchone()
        if profile is None:
            raise K10Conflict("指定执行配置修订不存在")
        execution_payload = json.loads(profile[1])
        execution_status = validate_execution_config(execution_payload)
        if not execution_status.ready or execution_payload.get("executionVersion") != "k10-execution-v3":
            raise K10Conflict("只有已批准的 k10-execution-v3 可绑定付费任务；V1/V2 仅供历史读取")
        policy = execution_payload["discovery"]["titleTriagePolicy"]
        policy_row = conn.execute(
            "SELECT content_json,content_sha256,approval_state FROM k10_title_triage_policy_revisions "
            "WHERE policy_id=? AND revision=?", (policy["policyId"], policy["revision"]),
        ).fetchone()
        if (policy_row is None or policy_row[1] != policy["contentSha256"] or policy_row[2] != "approved" or
                json.loads(policy_row[0]) != policy["content"]):
            raise K10Conflict("V3 执行配置未绑定相同的已批准标题筛选 policy")
        if conn.execute("SELECT 1 FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone() is None:
            raise K10Conflict("执行绑定任务不存在")
        expected = (execution_config_id, execution_config_revision, str(profile[0]), binding_kind)
        old = conn.execute(
            "SELECT execution_config_id,execution_config_revision,execution_content_sha256,binding_kind "
            "FROM k10_task_execution_bindings WHERE task_id=?", (task_id,),
        ).fetchone()
        if old is not None:
            if tuple(old) != expected:
                raise K10Conflict("任务已绑定不同执行配置，拒绝静默替换")
        else:
            conn.execute(
                "INSERT INTO k10_task_execution_bindings(task_id,execution_config_id,execution_config_revision,"
                "execution_content_sha256,binding_kind,bound_at) VALUES(?,?,?,?,?,?)",
                (task_id, *expected, bound_at),
            )
    return {"configId": execution_config_id, "revision": execution_config_revision,
            "contentSha256": str(profile[0]), "bindingKind": binding_kind, "payload": json.loads(profile[1])}


def bind_scan_execution(
    *, scan_id: str, task_id: str, execution_config_id: str, execution_config_revision: int,
    binding_kind: str, bound_at: str, db_path: Path,
) -> None:
    """Record the same explicit execution profile on the scan created by that task."""
    profile = read_execution_config(config_id=execution_config_id, revision=execution_config_revision, db_path=db_path)
    if profile is None:
        raise K10Conflict("指定执行配置修订不存在")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        if conn.execute("SELECT 1 FROM k10_scans WHERE scan_id=?", (scan_id,)).fetchone() is None:
            raise K10Conflict("执行绑定扫描不存在")
        task_binding = conn.execute(
            "SELECT execution_config_id,execution_config_revision,execution_content_sha256,binding_kind "
            "FROM k10_task_execution_bindings WHERE task_id=?", (task_id,),
        ).fetchone()
        expected = (task_id, execution_config_id, execution_config_revision, profile["contentSha256"], binding_kind)
        if task_binding is None or tuple(task_binding) != expected[1:]:
            raise K10Conflict("扫描执行绑定必须与任务的不可变绑定一致")
        old = conn.execute(
            "SELECT task_id,execution_config_id,execution_config_revision,execution_content_sha256,binding_kind "
            "FROM k10_scan_execution_bindings WHERE scan_id=?", (scan_id,),
        ).fetchone()
        if old is not None:
            if tuple(old) != expected:
                raise K10Conflict("扫描已绑定不同执行配置，拒绝静默替换")
            return
        conn.execute(
            "INSERT INTO k10_scan_execution_bindings(scan_id,task_id,execution_config_id,execution_config_revision,"
            "execution_content_sha256,binding_kind,bound_at) VALUES(?,?,?,?,?,?,?)",
            (scan_id, *expected, bound_at),
        )


def task_execution_profile(*, task_id: str, db_path: Path) -> Optional[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT b.execution_config_id,b.execution_config_revision,b.execution_content_sha256,b.binding_kind,"
            "b.bound_at,c.payload_json FROM k10_task_execution_bindings b "
            "JOIN k10_execution_config_revisions c ON c.config_id=b.execution_config_id "
            "AND c.revision=b.execution_config_revision WHERE b.task_id=?", (task_id,),
        ).fetchone()
    if row is None:
        return None
    return {"configId": row[0], "revision": int(row[1]), "contentSha256": row[2],
            "bindingKind": row[3], "boundAt": row[4], "payload": json.loads(row[5])}


def append_document_version(
    *, document_id: str, source_key: str, external_id: str, canonical_url: Optional[str],
    content_sha256: str, published_at: Optional[str], published_precision: str, fetched_at: str,
    original_text: Optional[str], excerpt: Optional[str], fetch_version: str,
    metadata: Mapping[str, Any], created_at: str, db_path: Path, scan_id: str | None = None,
    leaseguard: Callable[[], None] | None = None, return_append_result: bool = False,
) -> DocumentVersion | tuple[DocumentVersion, bool]:
    """追加原始资料版本，以内容哈希幂等；同源 ID 绝不可改绑到另一 document ID。"""
    if published_precision not in {"exact", "date", "unknown"}:
        raise ValueError("published_precision 必须是 exact、date 或 unknown")
    if leaseguard is not None:
        leaseguard()
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        scan_coverage: dict[str, Any] | None = None
        if scan_id is not None:
            scan = conn.execute("SELECT status,coverage_json FROM k10_scans WHERE scan_id=?", (scan_id,)).fetchone()
            if scan is None or scan[0] != "running":
                raise K10Conflict("来源资料只能写入当前 running 扫描")
            raw_coverage = json.loads(scan[1])
            if not isinstance(raw_coverage, Mapping):
                raise K10Conflict("当前扫描 coverage 无效")
            scan_coverage = dict(raw_coverage)
        identity = conn.execute(
            "SELECT document_id FROM k10_source_documents WHERE source_key=? AND external_id=?",
            (source_key, external_id),
        ).fetchone()
        if identity is not None and identity[0] != document_id:
            raise K10Conflict("来源 document identity 已绑定到另一稳定 ID")
        existing_doc = conn.execute(
            "SELECT source_key,external_id FROM k10_source_documents WHERE document_id=?", (document_id,)
        ).fetchone()
        if existing_doc is not None and tuple(existing_doc) != (source_key, external_id):
            raise K10Conflict("document ID 不能改绑来源 identity")
        conn.execute(
            "INSERT OR IGNORE INTO k10_source_documents(document_id,source_key,external_id,canonical_url,first_seen_at) "
            "VALUES(?,?,?,?,?)",
            (document_id, source_key, external_id, canonical_url, created_at),
        )
        old = conn.execute(
            "SELECT revision FROM k10_source_document_versions WHERE document_id=? AND content_sha256=?",
            (document_id, content_sha256),
        ).fetchone()
        if old is not None:
            version = DocumentVersion(document_id, int(old[0]), content_sha256)
            created = False
        else:
            revision = int(conn.execute(
                "SELECT COALESCE(MAX(revision),0) FROM k10_source_document_versions WHERE document_id=?",
                (document_id,),
            ).fetchone()[0]) + 1
            conn.execute(
                "INSERT INTO k10_source_document_versions(document_id,revision,content_sha256,published_at,"
                "published_precision,fetched_at,original_text,excerpt,fetch_version,metadata_json,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (document_id, revision, content_sha256, published_at, published_precision, fetched_at,
                 original_text, excerpt, fetch_version, _json(metadata), created_at),
            )
            version = DocumentVersion(document_id, revision, content_sha256)
            created = True
        if scan_id is not None and scan_coverage is not None:
            accepted = scan_coverage.get("sourceAcceptedDocumentRefs")
            entries = list(accepted) if isinstance(accepted, list) else []
            key = (version.document_id, version.revision)
            if not any(isinstance(item, Mapping) and (item.get("documentId"), item.get("revision")) == key for item in entries):
                # Re-check immediately before the scan checkpoint.  If ownership changed
                # during this write transaction, the exception rolls back the document insert
                # as well, so a former worker cannot leave an unowned half-write behind.
                if leaseguard is not None:
                    leaseguard()
                entries.append({"documentId": version.document_id, "revision": version.revision, "isNew": created})
                scan_coverage["sourceAcceptedDocumentRefs"] = entries
                conn.execute("UPDATE k10_scans SET coverage_json=? WHERE scan_id=? AND status='running'",
                             (_json(scan_coverage), scan_id))
    return (version, created) if return_append_result else version


def append_event_revision(
    *, event_id: str, stable_key: str, headline: str, event_kind: str, facts: Mapping[str, Any],
    source_refs: Sequence[Mapping[str, Any]], supersedes_revision: Optional[int], created_at: str,
    db_path: Path,
) -> EventRevision:
    """以完整事件内容判重的追加修订；更正和否认是新的 revision。"""
    payload = (headline, event_kind, _json(facts), _json(source_refs), supersedes_revision)
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        identity = conn.execute("SELECT event_id FROM k10_events WHERE stable_key=?", (stable_key,)).fetchone()
        if identity is not None and identity[0] != event_id:
            raise K10Conflict("event stable_key 已绑定到另一稳定 ID")
        old_event = conn.execute("SELECT stable_key FROM k10_events WHERE event_id=?", (event_id,)).fetchone()
        if old_event is not None and old_event[0] != stable_key:
            raise K10Conflict("event ID 不能改绑 stable_key")
        conn.execute(
            "INSERT OR IGNORE INTO k10_events(event_id,stable_key,created_at) VALUES(?,?,?)",
            (event_id, stable_key, created_at),
        )
        old = conn.execute(
            "SELECT revision,headline,event_kind,facts_json,source_refs_json,supersedes_revision "
            "FROM k10_event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1", (event_id,)
        ).fetchone()
        # A crash can happen after this event has been appended but before its candidate or
        # scan checkpoint.  `supersedes_revision` describes a *new* revision; it is not part
        # of the immutable event content used to detect replay of the current revision.
        if old is not None and tuple(old[1:5]) == payload[:4]:
            return EventRevision(event_id, int(old[0]))
        revision = 1 if old is None else int(old[0]) + 1
        if supersedes_revision is not None and supersedes_revision != revision - 1:
            raise K10Conflict("事件修订只能明确替代紧邻上一版本")
        conn.execute(
            "INSERT INTO k10_event_revisions(event_id,revision,headline,event_kind,facts_json,"
            "source_refs_json,supersedes_revision,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (event_id, revision, headline, event_kind, _json(facts), _json(source_refs),
             supersedes_revision, created_at),
        )
    return EventRevision(event_id, revision)


def append_company_mapping(
    *, mapping_id: str, event_id: str, event_revision: int, company_code: str,
    affected_stage: str, relation_evidence: Sequence[Mapping[str, Any]],
    inference: Mapping[str, Any], uncertainty: str, created_at: str, db_path: Path,
) -> None:
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        old = conn.execute(
            "SELECT event_id,event_revision,company_code,affected_stage,relation_evidence_json,"
            "inference_json,uncertainty FROM k10_company_mappings WHERE mapping_id=?", (mapping_id,)
        ).fetchone()
        expected = (event_id, event_revision, company_code, affected_stage, _json(relation_evidence),
                    _json(inference), uncertainty)
        if old is not None:
            if tuple(old) != expected:
                raise K10Conflict("公司映射 ID 已存在但内容不同")
            return
        conn.execute(
            "INSERT INTO k10_company_mappings(mapping_id,event_id,event_revision,company_code,affected_stage,"
            "relation_evidence_json,inference_json,uncertainty,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (mapping_id, *expected, created_at),
        )


def create_scan(
    *, scan_id: str, window_kind: str, cutoff_at: str, config_id: Optional[str],
    config_revision: Optional[int], status: str, coverage: Mapping[str, Any], created_at: str,
    completed_at: Optional[str], db_path: Path,
) -> None:
    if window_kind not in {"evening", "morning"}:
        raise ValueError("window_kind 必须是 evening 或 morning")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        row = (window_kind, cutoff_at, config_id, config_revision, status, _json(coverage), created_at, completed_at)
        old = conn.execute(
            "SELECT window_kind,cutoff_at,config_id,config_revision,status,coverage_json,created_at,completed_at "
            "FROM k10_scans WHERE scan_id=?", (scan_id,)
        ).fetchone()
        if old is not None:
            # A recovered scan retains its original created/coverage values; only its immutable
            # identity must match the retry request.
            if tuple(old[:4]) != tuple(row[:4]):
                raise K10Conflict("scan ID 已存在但冻结输入不同")
            return
        conn.execute(
            "INSERT INTO k10_scans(scan_id,window_kind,cutoff_at,config_id,config_revision,status,coverage_json,"
            "created_at,completed_at) VALUES(?,?,?,?,?,?,?,?,?)", (scan_id, *row),
        )


def finalize_scan(
    *, scan_id: str, status: str, coverage: Mapping[str, Any], completed_at: str, db_path: Path,
) -> None:
    """冻结扫描最终覆盖面；只允许 ``running`` 进入终态，不能改 cutoff 或配置版本。"""
    if status not in {"completed", "partial", "failed", "not_configured"}:
        raise ValueError("scan 终态必须是 completed、partial、failed 或 not_configured")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        changed = conn.execute(
            "UPDATE k10_scans SET status=?,coverage_json=?,completed_at=? WHERE scan_id=? AND status='running'",
            (status, _json(coverage), completed_at, scan_id),
        ).rowcount
        if changed != 1:
            raise K10Conflict("扫描不存在、并非 running，或已被冻结")


def reopen_scan(*, scan_id: str, db_path: Path) -> bool:
    """Resume a failed/not-configured frozen scan without changing its identity or inputs."""
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        if conn.execute("SELECT 1 FROM k10_discovery_retirements WHERE scan_id=?", (scan_id,)).fetchone() is not None:
            return False
        changed = conn.execute(
            "UPDATE k10_scans SET status='running',completed_at=NULL WHERE scan_id=? AND status IN ('failed','not_configured')",
            (scan_id,),
        ).rowcount
    return changed == 1


def update_running_scan_coverage(*, scan_id: str, coverage: Mapping[str, Any], db_path: Path) -> None:
    """Checkpoint a running scan before an LLM call; terminal scans remain immutable."""
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        changed = conn.execute("UPDATE k10_scans SET coverage_json=? WHERE scan_id=? AND status='running'",
                               (_json(coverage), scan_id)).rowcount
        if changed != 1:
            raise K10Conflict("扫描不是当前 running 状态")


def append_source_watermark(
    *, watermark_id: str, source_key: str, cursor_value: Optional[str], success_cutoff_at: str,
    fetched_at: str, scan_id: Optional[str], created_at: str, db_path: Path,
) -> None:
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        expected = (source_key, cursor_value, success_cutoff_at, fetched_at, scan_id, created_at)
        old = conn.execute(
            "SELECT source_key,cursor_value,success_cutoff_at,fetched_at,scan_id,created_at "
            "FROM k10_source_watermark_updates WHERE watermark_id=?", (watermark_id,),
        ).fetchone()
        if old is not None:
            # A resumed frozen scan may retrieve the same successful boundary again at a
            # different wall time.  Keep the first audit timestamp; cursor/boundary/scan
            # identity must still be identical.
            if tuple(old[:3]) != tuple(expected[:3]) or old[4] != expected[4]:
                raise K10Conflict("source watermark ID 已存在但内容不同")
            return
        conn.execute(
            "INSERT INTO k10_source_watermark_updates(watermark_id,source_key,cursor_value,success_cutoff_at,"
            "fetched_at,scan_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (watermark_id, *expected),
        )


def latest_source_watermark(*, source_key: str, db_path: Path) -> Optional[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT watermark_id,cursor_value,success_cutoff_at,fetched_at,scan_id,created_at "
            "FROM k10_source_watermark_updates WHERE source_key=? ORDER BY success_cutoff_at DESC,rowid DESC LIMIT 1",
            (source_key,),
        ).fetchone()
    if row is None:
        return None
    return {"watermarkId": row[0], "sourceKey": source_key, "cursorValue": row[1],
            "successCutoffAt": row[2], "fetchedAt": row[3], "scanId": row[4], "createdAt": row[5]}


def create_candidate(
    *, candidate_id: str, scan_id: str, event_id: str, event_revision: int, company_code: str,
    comparison: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], created_at: str, db_path: Path,
) -> None:
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        row = (scan_id, event_id, event_revision, company_code, _json(comparison), _json(evidence), created_at)
        old = conn.execute(
            "SELECT scan_id,event_id,event_revision,company_code,comparison_json,evidence_json,created_at "
            "FROM k10_candidates WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        if old is not None:
            if tuple(old) != row:
                raise K10Conflict("candidate ID 已存在但内容不同")
            return
        conn.execute(
            "INSERT INTO k10_candidates(candidate_id,scan_id,event_id,event_revision,company_code,comparison_json,"
            "evidence_json,created_at) VALUES(?,?,?,?,?,?,?,?)", (candidate_id, *row),
        )


def _action_replay(conn, *, idempotency_key: str, candidate_id: str, action: str, reason: Optional[str]):
    old = conn.execute(
        "SELECT action_id,candidate_id,action,reason FROM k10_candidate_actions WHERE idempotency_key=?",
        (idempotency_key,),
    ).fetchone()
    if old is None:
        return None
    if tuple(old[1:]) != (candidate_id, action, reason):
        raise K10Conflict("用户动作幂等键被不同请求复用")
    return str(old[0])


def _append_action(
    conn, *, action_id: str, candidate_id: str, action: str, idempotency_key: str,
    reason: Optional[str], created_at: str,
) -> tuple[str, bool]:
    if action not in {"observe", "skip", "restore", "withdraw"}:
        raise ValueError("未知候选用户动作")
    replay = _action_replay(conn, idempotency_key=idempotency_key, candidate_id=candidate_id,
                            action=action, reason=reason)
    if replay is not None:
        return replay, False
    conn.execute(
        "INSERT INTO k10_candidate_actions(action_id,candidate_id,action,idempotency_key,reason,created_at) "
        "VALUES(?,?,?,?,?,?)", (action_id, candidate_id, action, idempotency_key, reason, created_at),
    )
    return action_id, True


def append_candidate_action(
    *, action_id: str, candidate_id: str, action: str, idempotency_key: str, reason: Optional[str],
    created_at: str, db_path: Path,
) -> str:
    """追加非观察动作；``observe`` 必须走原子 ``observe_candidate``。"""
    if action == "observe":
        raise ValueError("observe 必须走 observe_candidate，保证分析作业同事务入队")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        action_id, _ = _append_action(conn, action_id=action_id, candidate_id=candidate_id,
                                      action=action, idempotency_key=idempotency_key, reason=reason,
                                      created_at=created_at)
    return action_id


def observe_candidate(
    *, action_id: str, observation_id: str, task_id: str, outbox_id: str, candidate_id: str,
    idempotency_key: str, task_input_version: str, task_input_cutoff_at: str,
    task_payload: Mapping[str, Any], task_budget: Mapping[str, Any], created_at: str, db_path: Path,
) -> Observation:
    """留下某一公司候选，并原子创建 observation、分析任务和 outbox。"""
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        replay = _action_replay(conn, idempotency_key=idempotency_key, candidate_id=candidate_id,
                                action="observe", reason=None)
        if replay is not None:
            row = conn.execute(
                "SELECT observation_id,candidate_id FROM k10_observations WHERE created_from_action_id=?", (replay,)
            ).fetchone()
            if row is None:
                raise K10Conflict("observe 动作缺少 observation，拒绝伪造修复")
            return Observation(str(row[0]), str(row[1]), False)
        _append_action(conn, action_id=action_id, candidate_id=candidate_id, action="observe",
                       idempotency_key=idempotency_key, reason=None, created_at=created_at)
        # Withdraw 后再次留下仍是同一家公司候选的同一深度观察：追加新的用户动作，
        # 但复用既有 Observation 和分析任务。若要重跑，走显式 jobs/retry，不隐式
        # 制造第二套分析链和两个待领取任务。
        previous = conn.execute(
            "SELECT observation_id FROM k10_observations WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
        if previous is not None:
            return Observation(str(previous[0]), candidate_id, False)
        conn.execute(
            "INSERT INTO k10_observations(observation_id,candidate_id,created_from_action_id,created_at) "
            "VALUES(?,?,?,?)", (observation_id, candidate_id, action_id, created_at),
        )
        payload = {**task_payload, "observationId": observation_id, "companyCandidateId": candidate_id}
        _insert_task(conn, task_id=task_id, kind="analysis", idempotency_key=f"analysis:{observation_id}",
                     input_version=task_input_version, input_cutoff_at=task_input_cutoff_at, payload=payload,
                     budget=task_budget, created_at=created_at)
        conn.execute(
            "INSERT INTO k10_task_outbox(outbox_id,task_id,created_at,dispatched_at) VALUES(?,?,?,NULL)",
            (outbox_id, task_id, created_at),
        )
    return Observation(observation_id, candidate_id, True)


def _window_representative_candidate(conn, *, company_window_id: str) -> str:
    """Choose one immutable published member to carry a shared window action.

    The representative is an implementation detail.  The command itself belongs to the company
    window, so a second catalyst cannot create another observation or analysis task.
    """
    row = conn.execute(
        "SELECT candidate_id FROM k10_publication_samples WHERE company_window_id=? "
        "ORDER BY rank IS NULL,rank,candidate_id LIMIT 1", (company_window_id,)
    ).fetchone()
    if row is None:
        raise K10Conflict("公司窗口没有已发布正式推荐")
    return str(row[0])


def _append_company_window_action_conn(
    conn, *, action_id: str, company_window_id: str, action: str, idempotency_key: str,
    reason: Optional[str], created_at: str,
) -> tuple[str, str, bool]:
    if action not in {"observe", "skip", "restore", "withdraw"}:
        raise ValueError("未知公司窗口用户动作")
    existing = conn.execute(
        "SELECT action_id,company_window_id,candidate_id,action,reason FROM k10_company_window_actions "
        "WHERE idempotency_key=?", (idempotency_key,)
    ).fetchone()
    if existing is not None:
        if tuple(existing[1:]) != (company_window_id, str(existing[2]), action, reason):
            raise K10Conflict("公司窗口动作幂等键被不同请求复用")
        return str(existing[0]), str(existing[2]), True
    by_id = conn.execute(
        "SELECT company_window_id,candidate_id,action,reason FROM k10_company_window_actions WHERE action_id=?",
        (action_id,),
    ).fetchone()
    if by_id is not None:
        raise K10Conflict("公司窗口动作 ID 已被不同请求使用")
    candidate_id = _window_representative_candidate(conn, company_window_id=company_window_id)
    candidate_action_id = _stable_id("window-candidate-action", action_id)
    _append_action(
        conn, action_id=candidate_action_id, candidate_id=candidate_id, action=action,
        idempotency_key=f"window:{company_window_id}:{idempotency_key}", reason=reason,
        created_at=created_at,
    )
    conn.execute(
        "INSERT INTO k10_company_window_actions(action_id,company_window_id,candidate_id,candidate_action_id,action,"
        "idempotency_key,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (action_id, company_window_id, candidate_id, candidate_action_id, action, idempotency_key, reason, created_at),
    )
    return action_id, candidate_id, False


def append_company_window_action(
    *, action_id: str, company_window_id: str, action: str, idempotency_key: str,
    reason: Optional[str], created_at: str, db_path: Path,
) -> str:
    """Append a window-scoped skip/restore/withdraw action exactly once.

    `observe` is intentionally rejected here because leaving a company must atomically create
    (or reuse) its sole shared observation and analysis task through
    :func:`observe_company_window`.
    """
    if action == "observe":
        raise ValueError("observe 必须走 observe_company_window，保证共享分析同事务入队")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        stored_action_id, _candidate_id, _replayed = _append_company_window_action_conn(
            conn, action_id=action_id, company_window_id=company_window_id, action=action,
            idempotency_key=idempotency_key, reason=reason, created_at=created_at,
        )
    return stored_action_id


def observe_company_window(
    *, action_id: str, observation_id: str, task_id: str, outbox_id: str,
    company_window_id: str, idempotency_key: str, task_input_version: str,
    task_input_cutoff_at: str, task_payload: Mapping[str, Any], task_budget: Mapping[str, Any],
    created_at: str, db_path: Path,
) -> CompanyWindowObservation:
    """Leave a company window once and create at most one analysis chain for all catalysts."""
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        stored_action_id, candidate_id, replayed = _append_company_window_action_conn(
            conn, action_id=action_id, company_window_id=company_window_id, action="observe",
            idempotency_key=idempotency_key, reason=None, created_at=created_at,
        )
        existing = conn.execute(
            "SELECT observation_id,candidate_id,task_id FROM k10_company_window_observations WHERE company_window_id=?",
            (company_window_id,),
        ).fetchone()
        if existing is not None:
            return CompanyWindowObservation(company_window_id, stored_action_id, str(existing[1]), str(existing[0]),
                                            str(existing[2]), False, replayed)
        # Preserve a direct pre-V1.4 observation if this controlled migration sees one: choose
        # the earliest linked observation and attach it rather than duplicating an analysis.
        legacy = conn.execute(
            "SELECT o.observation_id,o.candidate_id,t.task_id FROM k10_observations o "
            "JOIN k10_publication_samples s ON s.candidate_id=o.candidate_id "
            "JOIN k10_tasks t ON t.idempotency_key=('analysis:' || o.observation_id) "
            "WHERE s.company_window_id=? ORDER BY o.rowid LIMIT 1", (company_window_id,),
        ).fetchone()
        if legacy is not None:
            conn.execute(
                "INSERT INTO k10_company_window_observations(company_window_id,observation_id,candidate_id,task_id,created_at) "
                "VALUES(?,?,?,?,?)", (company_window_id, str(legacy[0]), str(legacy[1]), str(legacy[2]), created_at),
            )
            return CompanyWindowObservation(company_window_id, stored_action_id, str(legacy[1]), str(legacy[0]),
                                            str(legacy[2]), False, replayed)
        candidate_action = conn.execute(
            "SELECT candidate_action_id FROM k10_company_window_actions WHERE action_id=?", (stored_action_id,)
        ).fetchone()
        if candidate_action is None:
            raise K10Conflict("公司窗口 observe 动作缺少候选动作")
        conn.execute(
            "INSERT INTO k10_observations(observation_id,candidate_id,created_from_action_id,created_at) VALUES(?,?,?,?)",
            (observation_id, candidate_id, str(candidate_action[0]), created_at),
        )
        payload = {**task_payload, "observationId": observation_id, "companyCandidateId": candidate_id,
                   "companyWindowId": company_window_id}
        _insert_task(conn, task_id=task_id, kind="analysis", idempotency_key=f"analysis:{observation_id}",
                     input_version=task_input_version, input_cutoff_at=task_input_cutoff_at, payload=payload,
                     budget=task_budget, created_at=created_at)
        conn.execute("INSERT INTO k10_task_outbox(outbox_id,task_id,created_at,dispatched_at) VALUES(?,?,?,NULL)",
                     (outbox_id, task_id, created_at))
        conn.execute(
            "INSERT INTO k10_company_window_observations(company_window_id,observation_id,candidate_id,task_id,created_at) "
            "VALUES(?,?,?,?,?)", (company_window_id, observation_id, candidate_id, task_id, created_at),
        )
    return CompanyWindowObservation(company_window_id, stored_action_id, candidate_id, observation_id, task_id, True, replayed)


def _insert_task(
    conn, *, task_id: str, kind: str, idempotency_key: str, input_version: str, input_cutoff_at: str,
    payload: Mapping[str, Any], budget: Mapping[str, Any], created_at: str,
) -> Task:
    if kind in {"evening_scan", "morning_scan"}:
        retired = conn.execute(
            "SELECT 1 FROM k10_discovery_retirements r JOIN k10_tasks t ON t.task_id=r.task_id "
            "WHERE t.kind=? AND t.input_cutoff_at=?", (kind, input_cutoff_at),
        ).fetchone()
        if retired is not None:
            raise K10Conflict("用户已弃用该发现窗口，禁止重新入队")
    stored = (kind, idempotency_key, input_version, input_cutoff_at, _json(payload), _json(budget))
    old = conn.execute(
        "SELECT task_id,kind,idempotency_key,input_version,input_cutoff_at,payload_json,budget_json,status,"
        "attempt_count,lease_owner,lease_until FROM k10_tasks WHERE idempotency_key=?", (idempotency_key,)
    ).fetchone()
    if old is not None:
        if tuple(old[1:7]) != stored:
            raise K10Conflict("任务幂等键被不同输入复用")
        return Task(str(old[0]), str(old[1]), str(old[7]), int(old[8]), old[9], old[10], json.loads(old[5]))
    conn.execute(
        "INSERT INTO k10_tasks(task_id,kind,idempotency_key,input_version,input_cutoff_at,payload_json,status,"
        "stage,attempt_count,error_text,budget_json,checkpoint_json,lease_owner,lease_until,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,'created',0,NULL,?,'{}',NULL,NULL,?,?)",
        (task_id, kind, idempotency_key, input_version, input_cutoff_at, _json(payload), "queued",
         _json(budget), created_at, created_at),
    )
    return Task(task_id, kind, "queued", 0, None, None, dict(payload))


def enqueue_task(
    *, task_id: str, kind: str, idempotency_key: str, input_version: str, input_cutoff_at: str,
    payload: Mapping[str, Any], budget: Mapping[str, Any], created_at: str, db_path: Path,
) -> Task:
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        return _insert_task(conn, task_id=task_id, kind=kind, idempotency_key=idempotency_key,
                            input_version=input_version, input_cutoff_at=input_cutoff_at, payload=payload,
                            budget=budget, created_at=created_at)


def _window_related_document_refs(conn, *, company_window_id: str) -> set[tuple[str, int]]:
    """Return only document versions already attached to this window's immutable history."""
    allowed: set[tuple[str, int]] = set()
    def add(raw: Any) -> None:
        for ref in _explicit_refs(raw):
            allowed.add((ref["documentId"], ref["revision"]))
    sample_rows = conn.execute(
        "SELECT evidence_refs_json,comparison_json FROM k10_publication_samples WHERE company_window_id=?", (company_window_id,)
    ).fetchall()
    for refs_json, comparison_json in sample_rows:
        add(json.loads(refs_json))
        comparison = json.loads(comparison_json)
        if isinstance(comparison, Mapping):
            add(_frozen_evidence_refs(event_source_refs=(), event_facts={}, comparison=comparison, mappings=()))
    lifecycle_rows = conn.execute(
        "SELECT e.source_refs_json,e.content_json FROM k10_opportunity_lifecycle_events e JOIN k10_opportunities o "
        "ON o.opportunity_id=e.opportunity_id WHERE o.company_window_id=?", (company_window_id,)
    ).fetchall()
    for refs_json, content_json in lifecycle_rows:
        add(json.loads(refs_json))
        content = json.loads(content_json)
        if isinstance(content, Mapping):
            add(content.get("sourceRefs")); add(content.get("materialContraryEvidence"))
    request_rows = conn.execute("SELECT source_refs_json FROM k10_analysis_requests WHERE company_window_id=?", (company_window_id,)).fetchall()
    for (refs_json,) in request_rows:
        add(json.loads(refs_json))
    analysis_rows = conn.execute(
        "SELECT a.input_lineage_json FROM k10_analysis_revisions a JOIN k10_company_window_observations w "
        "ON w.observation_id=a.observation_id WHERE w.company_window_id=?", (company_window_id,)
    ).fetchall()
    for (lineage_json,) in analysis_rows:
        lineage = json.loads(lineage_json)
        if isinstance(lineage, Mapping):
            add(lineage.get("frozenEvidenceRefs")); add(lineage.get("documentVersions"))
    morning_rows = conn.execute(
        "SELECT i.content_json FROM k10_morning_report_items i WHERE i.company_window_id=?", (company_window_id,)
    ).fetchall()
    for (content_json,) in morning_rows:
        content = json.loads(content_json)
        if isinstance(content, Mapping):
            add(content.get("sourceRefs")); add(content.get("materialContraryEvidence"))
    return allowed


def _request_source_refs(
    conn, *, company_window_id: str, source_refs: Sequence[Mapping[str, Any]], cutoff_at: str, require_nonempty: bool,
) -> list[dict[str, Any]]:
    if isinstance(source_refs, (str, bytes)) or not isinstance(source_refs, Sequence) or (require_nonempty and not source_refs):
        raise ValueError("证据追加分析必须提供冻结资料版本")
    try:
        cutoff = datetime.fromisoformat(cutoff_at)
    except ValueError as exc:
        raise ValueError("追加分析截止时间必须是带时区 ISO 时间") from exc
    if cutoff.tzinfo is None:
        raise ValueError("追加分析截止时间必须带时区")
    allowed = _window_related_document_refs(conn, company_window_id=company_window_id)
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for raw in source_refs:
        if not isinstance(raw, Mapping):
            raise ValueError("追加分析 sourceRefs 每项必须是对象")
        document_id, revision = raw.get("documentId"), raw.get("revision")
        if not isinstance(document_id, str) or not document_id or isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("追加分析 sourceRefs 必须精确引用 documentId 与 revision")
        key = (document_id, revision)
        if key in seen:
            raise ValueError("追加分析 sourceRefs 不可重复")
        if key not in allowed:
            raise K10Conflict("追加分析资料未关联到该公司窗口")
        source = conn.execute(
            "SELECT d.source_key,v.fetched_at,v.published_at,v.published_precision FROM k10_source_document_versions v "
            "JOIN k10_source_documents d ON d.document_id=v.document_id WHERE v.document_id=? AND v.revision=?",
            key,
        ).fetchone()
        if source is None:
            raise K10Conflict("追加分析引用的资料版本不存在")
        try:
            fetched_at = datetime.fromisoformat(str(source[1]))
        except ValueError as exc:
            raise K10Conflict("追加分析引用的资料取得时间无效") from exc
        if fetched_at.tzinfo is None or fetched_at.astimezone(timezone.utc) > cutoff.astimezone(timezone.utc):
            raise K10Conflict("追加分析引用的资料晚于冻结截止")
        refs.append({"documentId": document_id, "revision": revision, "sourceKey": source[0],
                     "fetchedAt": source[1], "publishedAt": source[2], "publishedPrecision": source[3]})
        seen.add(key)
    return refs


def _analysis_request_intent_refs(source_refs: Sequence[Mapping[str, Any]], *, require_nonempty: bool) -> tuple[tuple[str, int], ...]:
    if isinstance(source_refs, (str, bytes)) or not isinstance(source_refs, Sequence) or (require_nonempty and not source_refs):
        raise ValueError("证据追加分析必须提供冻结资料版本")
    values: set[tuple[str, int]] = set()
    for raw in source_refs:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("documentId"), str) or not raw["documentId"] or isinstance(raw.get("revision"), bool) or not isinstance(raw.get("revision"), int) or raw["revision"] < 1:
            raise ValueError("追加分析 sourceRefs 必须精确引用 documentId 与 revision")
        values.add((raw["documentId"], raw["revision"]))
    return tuple(sorted(values))


def create_analysis_request(
    *, request_id: str, task_id: str, company_window_id: str, kind: str, question: str | None,
    source_refs: Sequence[Mapping[str, Any]], idempotency_key: str, input_cutoff_at: str,
    task_input_version: str, task_payload: Mapping[str, Any], task_budget: Mapping[str, Any],
    created_at: str, db_path: Path,
) -> dict[str, Any]:
    """Queue one immutable follow-up analysis for an already observed company window.

    The initial keep-created task remains the parent context.  Each new request receives the
    next observation-global revision and captures the inherited task configuration/budget plus
    exactly the supplied document versions.  Retrying a task never creates another request.
    """
    if kind not in {"user_question", "evidence_update"}:
        raise ValueError("追加分析 kind 必须是 user_question 或 evidence_update")
    if kind == "user_question" and (not isinstance(question, str) or not question.strip()):
        raise ValueError("用户追问必须提供 question")
    if question is not None and (not isinstance(question, str) or not question.strip()):
        raise ValueError("question 必须是非空字符串或 null")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ValueError("追加分析必须提供 idempotencyKey")
    if not isinstance(task_input_version, str) or not task_input_version or not isinstance(task_payload, Mapping) or not isinstance(task_budget, Mapping):
        raise ValueError("追加分析必须显式提供冻结任务版本、输入和重试配置")
    normalized_question = question.strip() if isinstance(question, str) else None
    intent_refs = _analysis_request_intent_refs(source_refs, require_nonempty=kind == "evidence_update")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        observation = conn.execute(
            "SELECT observation_id,task_id FROM k10_company_window_observations WHERE company_window_id=?", (company_window_id,)
        ).fetchone()
        if observation is None:
            raise K10Conflict("公司窗口尚未留下，不能追加分析")
        observation_id = str(observation[0])
        existing = conn.execute(
            "SELECT request_id,company_window_id,observation_id,task_id,global_revision,parent_revision,kind,question,source_refs_json,"
            "task_input_version,task_payload_json,task_budget_json,input_cutoff_at,created_at "
            "FROM k10_analysis_requests WHERE idempotency_key=?", (idempotency_key,),
        ).fetchone()
        if existing is not None:
            stored_refs = tuple(sorted((item["documentId"], item["revision"]) for item in json.loads(existing[8])))
            if (existing[1], existing[2], existing[6], existing[7], stored_refs) != (company_window_id, observation_id, kind, normalized_question, intent_refs):
                raise K10Conflict("追加分析幂等键被不同请求复用")
            return _analysis_request_from_row(existing, replayed=True)
        refs = _request_source_refs(conn, company_window_id=company_window_id, source_refs=source_refs, cutoff_at=input_cutoff_at,
                                    require_nonempty=kind == "evidence_update")
        old_task = conn.execute("SELECT 1 FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()
        if old_task is not None:
            raise K10Conflict("追加分析 task ID 已存在")
        prior_request = conn.execute("SELECT MAX(global_revision) FROM k10_analysis_requests WHERE observation_id=?", (observation_id,)).fetchone()[0]
        # V2 databases can contain retired morning analysis rows.  They are audit history, not
        # a pro/con parent revision for a new user analysis request.
        prior_artifact = conn.execute(
            "SELECT MAX(revision) FROM k10_analysis_revisions WHERE observation_id=? AND analysis_kind IN ('pro','con')",
            (observation_id,),
        ).fetchone()[0]
        parent_revision = max(int(prior_request or 0), int(prior_artifact or 0))
        if parent_revision < 1:
            raise K10Conflict("初始分析尚未完成，不能创建追加版本")
        attempt_rows = conn.execute(
            "SELECT analysis_kind,status FROM k10_analysis_revisions WHERE observation_id=? AND revision=? "
            "AND analysis_kind IN ('pro','con') ORDER BY rowid", (observation_id, parent_revision),
        ).fetchall()
        latest_attempts = {row[0]: row[1] for row in attempt_rows}
        if latest_attempts != {"pro": "completed", "con": "completed"}:
            raise K10Conflict("上一分析版本尚未完整完成；请先等待或重试同一版本")
        global_revision = parent_revision + 1
        payload = {**dict(task_payload), "observationId": observation_id, "companyWindowId": company_window_id,
                   "analysisRequestId": request_id, "globalRevision": global_revision, "targetRevision": global_revision,
                   "parentRevision": parent_revision, "analysisRequestKind": kind, "question": normalized_question,
                   "frozenEvidenceRefs": refs}
        budget = dict(task_budget)
        task_key = "analysis-request:" + idempotency_key
        _insert_task(conn, task_id=task_id, kind="analysis", idempotency_key=task_key,
                     input_version=task_input_version, input_cutoff_at=input_cutoff_at, payload=payload,
                     budget=budget, created_at=created_at)
        conn.execute(
            "INSERT INTO k10_analysis_requests(request_id,company_window_id,observation_id,task_id,idempotency_key,global_revision,parent_revision,kind,question,source_refs_json,task_input_version,task_payload_json,task_budget_json,input_cutoff_at,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (request_id, company_window_id, observation_id, task_id, idempotency_key, global_revision, parent_revision,
             kind, normalized_question, _json(refs), task_input_version, _json(payload), _json(budget), input_cutoff_at, created_at),
        )
        row = conn.execute(
            "SELECT request_id,company_window_id,observation_id,task_id,global_revision,parent_revision,kind,question,source_refs_json,"
            "task_input_version,task_payload_json,task_budget_json,input_cutoff_at,created_at FROM k10_analysis_requests WHERE request_id=?", (request_id,),
        ).fetchone()
    return _analysis_request_from_row(row, replayed=False)


def _analysis_request_from_row(row, *, replayed: bool = False) -> dict[str, Any]:
    return {"requestId": row[0], "companyWindowId": row[1], "observationId": row[2], "taskId": row[3],
            "globalRevision": int(row[4]), "targetRevision": int(row[4]), "parentRevision": None if row[5] is None else int(row[5]),
            "kind": row[6], "question": row[7], "sourceRefs": json.loads(row[8]),
            "taskInputVersion": row[9], "taskPayload": json.loads(row[10]), "taskBudget": json.loads(row[11]),
            "inputCutoffAt": row[12], "createdAt": row[13], "replayed": replayed}


def get_analysis_request_by_idempotency_key(*, idempotency_key: str, db_path: Path) -> dict[str, Any] | None:
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT request_id,company_window_id,observation_id,task_id,global_revision,parent_revision,kind,question,source_refs_json,"
            "task_input_version,task_payload_json,task_budget_json,input_cutoff_at,created_at FROM k10_analysis_requests WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
    return None if row is None else _analysis_request_from_row(row)


def append_analysis_revision(
    *, analysis_id: str, observation_id: str, revision: int, analysis_kind: str, input_cutoff_at: str,
    input_lineage: Mapping[str, Any], content: Mapping[str, Any], status: str, created_at: str, db_path: Path,
) -> None:
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        conn.execute(
            "INSERT INTO k10_analysis_revisions(analysis_id,observation_id,revision,analysis_kind,input_cutoff_at,"
            "input_lineage_json,content_json,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (analysis_id, observation_id, revision, analysis_kind, input_cutoff_at, _json(input_lineage),
             _json(content), status, created_at),
        )


def append_morning_update(
    *, update_id: str, observation_id: Optional[str], candidate_id: Optional[str], cutoff_at: str,
    content: Mapping[str, Any], created_at: str, db_path: Path,
) -> None:
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        expected = (observation_id, candidate_id, cutoff_at, _json(content), created_at)
        old = conn.execute(
            "SELECT observation_id,candidate_id,cutoff_at,content_json,created_at "
            "FROM k10_morning_updates WHERE update_id=?", (update_id,),
        ).fetchone()
        if old is not None:
            if tuple(old) != expected:
                raise K10Conflict("morning update ID 已存在但内容不同")
            return
        conn.execute(
            "INSERT INTO k10_morning_updates(update_id,observation_id,candidate_id,cutoff_at,content_json,created_at) "
            "VALUES(?,?,?,?,?,?)", (update_id, *expected),
        )


_MORNING_REPORT_GROUPS = (
    "major_contrary", "thesis_changed", "continuing_or_expiring", "new", "needs_review",
)


def append_morning_report(
    *, report_id: str, scan_id: str, cutoff_at: str, generated_at: str, status: str,
    coverage: Mapping[str, Any], groups: Mapping[str, Sequence[Mapping[str, Any]]],
    created_at: str, db_path: Path,
) -> dict[str, Any]:
    """Persist one complete, ordered morning report for a completed morning scan.

    All five product groups are written in one transaction, including empty groups.  A retry
    may replay byte-identical content, but cannot overwrite a prior report or change its actual
    generation time.
    """
    if status not in {"completed", "partial", "failed", "not_configured"}:
        raise ValueError("晨报状态无效")
    if set(groups) != set(_MORNING_REPORT_GROUPS):
        raise ValueError("晨报必须包含固定五组")
    normalized: dict[str, list[dict[str, Any]]] = {}
    for group in _MORNING_REPORT_GROUPS:
        raw_items = groups[group]
        if isinstance(raw_items, (str, bytes)) or not isinstance(raw_items, Sequence):
            raise ValueError("晨报分组必须是项目列表")
        values: list[dict[str, Any]] = []
        for position, raw in enumerate(raw_items):
            if not isinstance(raw, Mapping):
                raise ValueError("晨报项目必须是对象")
            item = dict(raw)
            item_id = item.get("itemId")
            state = item.get("status")
            content = item.get("content")
            if not isinstance(item_id, str) or not item_id or not isinstance(state, str) or not state or not isinstance(content, Mapping):
                raise ValueError("晨报项目缺少 itemId、status 或 content")
            values.append({"itemId": item_id, "section": group, "position": position,
                           "opportunityId": item.get("opportunityId"), "companyWindowId": item.get("companyWindowId"),
                           "status": state, "content": dict(content)})
        if len({item["itemId"] for item in values}) != len(values):
            raise ValueError("同一晨报分组 itemId 不可重复")
        normalized[group] = values
    payload = {group: normalized[group] for group in _MORNING_REPORT_GROUPS}
    opportunity_ids = [item["opportunityId"] for values in normalized.values() for item in values if item["opportunityId"] is not None]
    if any(not isinstance(opportunity_id, str) or not opportunity_id for opportunity_id in opportunity_ids) or len(opportunity_ids) != len(set(opportunity_ids)):
        raise ValueError("同一晨报的正式机会只能出现一次")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        scan = conn.execute("SELECT window_kind,cutoff_at,status FROM k10_scans WHERE scan_id=?", (scan_id,)).fetchone()
        if scan is None or scan[0] != "morning":
            raise K10Conflict("晨报必须绑定同一截止时间的晨间扫描")
        if scan[2] == "running":
            raise K10Conflict("晨报只能绑定已终态的晨间扫描")
        if _utc_instant(str(scan[1])) != _utc_instant(cutoff_at):
            raise K10Conflict("晨报必须绑定同一截止时间的晨间扫描")
        # The scan is the immutable authority.  New reports use its canonical persisted
        # timestamp, even when a legacy scheduler task still carries an equivalent +08:00
        # spelling.
        canonical_cutoff_at = _utc_instant(str(scan[1]))
        existing = conn.execute(
            "SELECT scan_id,revision,cutoff_at,generated_at,status,coverage_json,created_at FROM k10_morning_reports WHERE report_id=?",
            (report_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing[0] != scan_id
                or _utc_instant(str(existing[2])) != _utc_instant(canonical_cutoff_at)
                or (existing[3], existing[4], existing[5], existing[6])
                != (generated_at, status, _json(coverage), created_at)
            ):
                raise K10Conflict("晨报 ID 已存在但内容不同")
            stored_items = conn.execute(
                "SELECT group_key,item_id,position,opportunity_id,company_window_id,status,content_json "
                "FROM k10_morning_report_items WHERE report_id=? ORDER BY group_key,position,item_id", (report_id,)
            ).fetchall()
            supplied_items = sorted(
                (group, item["itemId"], item["position"], item["opportunityId"], item["companyWindowId"], item["status"], _json(item["content"]))
                for group, values in normalized.items() for item in values
            )
            if [tuple(item) for item in stored_items] != supplied_items:
                raise K10Conflict("晨报 ID 已存在但项目内容不同")
            return _morning_report_from_connection(conn, report_id=report_id) or {}
        revision = int(conn.execute("SELECT COALESCE(MAX(revision),0) FROM k10_morning_reports WHERE scan_id=?", (scan_id,)).fetchone()[0]) + 1
        for group, values in normalized.items():
            for item in values:
                opportunity_id = item["opportunityId"]
                window_id = item["companyWindowId"]
                if opportunity_id is not None:
                    row = conn.execute("SELECT company_window_id FROM k10_opportunities WHERE opportunity_id=?", (opportunity_id,)).fetchone()
                    if row is None or (window_id is not None and row[0] != window_id):
                        raise K10Conflict("晨报项目机会与公司窗口不一致")
                if window_id is not None and conn.execute("SELECT 1 FROM k10_company_windows WHERE company_window_id=?", (window_id,)).fetchone() is None:
                    raise K10Conflict("晨报项目公司窗口不存在")
        conn.execute(
            "INSERT INTO k10_morning_reports(report_id,scan_id,revision,cutoff_at,generated_at,status,coverage_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (report_id, scan_id, revision, canonical_cutoff_at, generated_at, status, _json(coverage), created_at),
        )
        for group, values in normalized.items():
            for item in values:
                conn.execute(
                    "INSERT INTO k10_morning_report_items(report_id,item_id,group_key,position,opportunity_id,company_window_id,status,content_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (report_id, item["itemId"], group, item["position"], item["opportunityId"], item["companyWindowId"],
                     item["status"], _json(item["content"]), created_at),
                )
    return {"reportId": report_id, "scanId": scan_id, "revision": revision, "cutoffAt": canonical_cutoff_at, "generatedAt": generated_at,
            "status": status, "coverage": dict(coverage), "groups": payload, "createdAt": created_at}


def _morning_report_from_connection(conn, *, report_id: str) -> dict[str, Any] | None:
    report = conn.execute(
        "SELECT scan_id,revision,cutoff_at,generated_at,status,coverage_json,created_at FROM k10_morning_reports WHERE report_id=?", (report_id,)
    ).fetchone()
    if report is None:
        return None
    groups: dict[str, list[dict[str, Any]]] = {group: [] for group in _MORNING_REPORT_GROUPS}
    rows = conn.execute(
        "SELECT item_id,group_key,position,opportunity_id,company_window_id,status,content_json,created_at "
        "FROM k10_morning_report_items WHERE report_id=? ORDER BY CASE group_key "
        "WHEN 'major_contrary' THEN 0 WHEN 'thesis_changed' THEN 1 WHEN 'continuing_or_expiring' THEN 2 "
        "WHEN 'new' THEN 3 ELSE 4 END,position,item_id", (report_id,),
    ).fetchall()
    for row in rows:
        groups[str(row[1])].append({"itemId": row[0], "section": row[1], "opportunityId": row[3], "companyWindowId": row[4],
                                    "status": row[5], "content": json.loads(row[6]), "createdAt": row[7]})
    return {"reportId": report_id, "scanId": report[0], "revision": int(report[1]), "cutoffAt": report[2], "generatedAt": report[3],
            "status": report[4], "coverage": json.loads(report[5]), "groups": groups, "createdAt": report[6]}


def get_morning_report(*, report_id: str, db_path: Path) -> dict[str, Any] | None:
    with read_connection(db_path) as conn:
        require_schema(conn)
        return _morning_report_from_connection(conn, report_id=report_id)


def list_morning_reports(*, db_path: Path) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        ids = conn.execute("SELECT report_id FROM k10_morning_reports ORDER BY generated_at DESC,revision DESC,report_id DESC").fetchall()
        return [_morning_report_from_connection(conn, report_id=str(row[0])) for row in ids]


def _task_from_row(row) -> Task:
    return Task(str(row[0]), str(row[1]), str(row[2]), int(row[3]), row[4], row[5], json.loads(row[6]))


def get_task(*, task_id: str, db_path: Path) -> Optional[Task]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT task_id,kind,status,attempt_count,lease_owner,lease_until,payload_json FROM k10_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
    return _task_from_row(row) if row is not None else None


def latest_document_version(*, document_id: str, db_path: Path) -> Optional[DocumentVersion]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT revision,content_sha256 FROM k10_source_document_versions WHERE document_id=? "
            "ORDER BY revision DESC LIMIT 1", (document_id,),
        ).fetchone()
    return None if row is None else DocumentVersion(document_id, int(row[0]), str(row[1]))


def _validated_source_keys(source_keys: Sequence[str] | None) -> tuple[str, ...] | None:
    """Return an explicit, de-duplicated source allow-list for a read query.

    ``None`` deliberately preserves the historic all-source read for callers that
    already hold an explicit frozen reference.  An empty sequence is an empty
    allow-list: it must never quietly become a fallback source selection.
    """
    if source_keys is None:
        return None
    if isinstance(source_keys, (str, bytes)) or any(not isinstance(key, str) or not key for key in source_keys):
        raise ValueError("source_keys 必须是非空来源键序列")
    return tuple(dict.fromkeys(source_keys))


def list_source_document_versions(
    *, cutoff_at: Optional[str], db_path: Path, source_keys: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Read traceable document versions, optionally limited to explicit source keys."""
    allowed_source_keys = _validated_source_keys(source_keys)
    if allowed_source_keys == ():
        return []
    with read_connection(db_path) as conn:
        require_schema(conn)
        query = ("SELECT v.document_id,v.revision,v.content_sha256,v.published_at,v.published_precision,v.fetched_at,"
                 "v.original_text,v.excerpt,v.fetch_version,v.metadata_json,v.created_at,d.source_key "
                 "FROM k10_source_document_versions v JOIN k10_source_documents d ON d.document_id=v.document_id")
        clauses: list[str] = []
        args: list[Any] = []
        if cutoff_at is not None:
            clauses.append("v.fetched_at <= ?"); args.append(cutoff_at)
        if allowed_source_keys is not None:
            clauses.append("d.source_key IN (" + ",".join("?" for _ in allowed_source_keys) + ")")
            args.extend(allowed_source_keys)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        rows = conn.execute(query + " ORDER BY v.fetched_at,v.document_id,v.revision", tuple(args)).fetchall()
    return [{"documentId": r[0], "revision": r[1], "contentSha256": r[2], "publishedAt": r[3],
             "publishedPrecision": r[4], "fetchedAt": r[5], "originalText": r[6], "excerpt": r[7],
             "fetchVersion": r[8], "metadata": json.loads(r[9]), "createdAt": r[10], "sourceKey": r[11]} for r in rows]


def latest_event_revision(*, event_id: str, db_path: Path) -> Optional[EventRevision]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT revision FROM k10_event_revisions WHERE event_id=? ORDER BY revision DESC LIMIT 1", (event_id,)
        ).fetchone()
    return None if row is None else EventRevision(event_id, int(row[0]))


def list_scans(*, window_kind: Optional[str], db_path: Path) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        query = ("SELECT scan_id,window_kind,cutoff_at,status,coverage_json,config_id,config_revision,created_at,completed_at "
                 "FROM k10_scans")
        args: tuple[Any, ...] = ()
        if window_kind is not None:
            query += " WHERE window_kind=?"; args = (window_kind,)
        rows = conn.execute(query + " ORDER BY cutoff_at DESC,scan_id DESC", args).fetchall()
    return [{"scanId": r[0], "windowKind": r[1], "cutoffAt": r[2], "status": r[3],
             "coverage": json.loads(r[4]), "configId": r[5], "configRevision": r[6],
             "createdAt": r[7], "completedAt": r[8]} for r in rows]


def get_scan(*, scan_id: str, db_path: Path) -> Optional[dict[str, Any]]:
    return next((item for item in list_scans(window_kind=None, db_path=db_path) if item["scanId"] == scan_id), None)


def _state_in_connection(conn, candidate_id: str) -> str:
    action = conn.execute(
        "SELECT action FROM k10_candidate_actions WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    return "offered" if action is None else {"observe": "observed", "skip": "skipped", "restore": "offered", "withdraw": "offered"}[action[0]]


def _candidate_dict(row, state: str) -> dict[str, Any]:
    return {"candidateId": row[0], "scanId": row[1], "eventId": row[2], "eventRevision": row[3],
            "companyCode": row[4], "comparison": json.loads(row[5]), "evidence": json.loads(row[6]),
            "createdAt": row[7], "state": state}


def list_candidates(
    *, scan_id: Optional[str], state: Optional[str], db_path: Path,
) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        query = "SELECT candidate_id,scan_id,event_id,event_revision,company_code,comparison_json,evidence_json,created_at FROM k10_candidates"
        args: tuple[Any, ...] = ()
        if scan_id is not None:
            query += " WHERE scan_id=?"; args = (scan_id,)
        rows = conn.execute(query + " ORDER BY created_at,candidate_id", args).fetchall()
        items = [_candidate_dict(row, _state_in_connection(conn, str(row[0]))) for row in rows]
    return [item for item in items if state is None or item["state"] == state]


def get_candidate(*, candidate_id: str, db_path: Path) -> Optional[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT candidate_id,scan_id,event_id,event_revision,company_code,comparison_json,evidence_json,created_at "
            "FROM k10_candidates WHERE candidate_id=?", (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        return _candidate_dict(row, _state_in_connection(conn, candidate_id))


def list_observations(*, db_path: Path) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        rows = conn.execute(
            "SELECT observation_id,candidate_id,created_from_action_id,created_at FROM k10_observations ORDER BY rowid"
        ).fetchall()
        return [{"observationId": r[0], "candidateId": r[1], "actionId": r[2], "createdAt": r[3],
                 "candidateState": _state_in_connection(conn, str(r[1]))} for r in rows]


def get_observation(*, observation_id: str, db_path: Path) -> Optional[dict[str, Any]]:
    return next((item for item in list_observations(db_path=db_path) if item["observationId"] == observation_id), None)


def _frozen_documents(
    conn, refs: Sequence[Mapping[str, Any]], *, cutoff_at: str | None = None,
    source_keys: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Resolve only explicit document revisions; never substitute a newer correction."""
    allowed_source_keys = _validated_source_keys(source_keys)
    if allowed_source_keys == ():
        return []
    cutoff: datetime | None = None
    if cutoff_at is not None:
        try:
            cutoff = datetime.fromisoformat(cutoff_at)
        except ValueError:
            raise ValueError("资料截止时间必须是 ISO 时间") from None
        if cutoff.tzinfo is None:
            raise ValueError("资料截止时间必须带时区")
    documents: list[dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, Mapping) or not ref.get("documentId") or not isinstance(ref.get("revision"), int):
            continue
        query = (
            "SELECT v.document_id,v.revision,v.content_sha256,v.published_at,v.published_precision,v.fetched_at,"
            "v.original_text,v.excerpt,v.fetch_version,v.metadata_json,v.created_at,d.source_key "
            "FROM k10_source_document_versions v JOIN k10_source_documents d ON d.document_id=v.document_id "
            "WHERE v.document_id=? AND v.revision=?"
        )
        args: list[Any] = [str(ref["documentId"]), int(ref["revision"])]
        if allowed_source_keys is not None:
            query += " AND d.source_key IN (" + ",".join("?" for _ in allowed_source_keys) + ")"
            args.extend(allowed_source_keys)
        row = conn.execute(query, tuple(args)).fetchone()
        if row is not None:
            if cutoff is not None:
                try:
                    fetched_at = datetime.fromisoformat(str(row[5]))
                except ValueError:
                    continue
                if fetched_at.tzinfo is None or fetched_at > cutoff:
                    continue
            documents.append({"documentId": row[0], "revision": row[1], "contentSha256": row[2],
                              "publishedAt": row[3], "publishedPrecision": row[4], "fetchedAt": row[5],
                              "originalText": row[6], "excerpt": row[7], "fetchVersion": row[8],
                              "metadata": json.loads(row[9]), "createdAt": row[10], "sourceKey": row[11]})
    return documents


def load_document_versions(
    *, refs: Sequence[Mapping[str, Any]], db_path: Path, source_keys: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Read frozen document revisions, optionally enforcing an explicit source allow-list."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        return _frozen_documents(conn, refs, source_keys=source_keys)


def _explicit_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    refs: list[dict[str, Any]] = []
    for item in value:
        if (isinstance(item, Mapping) and isinstance(item.get("documentId"), str) and item["documentId"]
                and isinstance(item.get("revision"), int) and not isinstance(item["revision"], bool)):
            refs.append({"documentId": item["documentId"], "revision": item["revision"]})
    return refs


def _candidate_mapping_ids(value: Any) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return set()
    return {item["mappingId"] for item in value
            if isinstance(item, Mapping) and isinstance(item.get("mappingId"), str) and item["mappingId"]}


def _frozen_evidence_refs(
    *, event_source_refs: Any, event_facts: Any, comparison: Any,
    mappings: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Stable evidence union for a single candidate, using only its stored revisions."""
    groups: list[Any] = [event_source_refs]
    if isinstance(event_facts, Mapping):
        verification = event_facts.get("verification")
        if isinstance(verification, Mapping):
            groups.append(verification.get("evidenceRefs"))
    if isinstance(comparison, Mapping):
        groups.append(comparison.get("evidenceRefs"))
        # Historical cases are part of the frozen comparison decision.  They are not a search
        # expansion: only the exact document revisions already recorded on the comparison are
        # admitted to later analysis context.
        historical_cases = comparison.get("historicalCases")
        if isinstance(historical_cases, Sequence) and not isinstance(historical_cases, (str, bytes)):
            for case in historical_cases:
                if isinstance(case, Mapping):
                    groups.extend((case.get("sourceRefs"), case.get("evidenceRefs")))
        coverage = comparison.get("historicalCoverage")
        if isinstance(coverage, Mapping):
            groups.extend((coverage.get("sourceRefs"), coverage.get("evidenceRefs")))
    groups.extend(mapping.get("relationEvidence") for mapping in mappings)
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for group in groups:
        for ref in _explicit_refs(group):
            key = (str(ref["documentId"]), int(ref["revision"]))
            if key not in seen:
                seen.add(key)
                refs.append(ref)
    return refs


def _mapping_context_rows(conn, *, event_id: str, event_revision: int, candidate_evidence: Any) -> list[Any]:
    mapping_ids = _candidate_mapping_ids(candidate_evidence)
    if not mapping_ids:
        return []
    rows = conn.execute(
        "SELECT mapping_id,company_code,affected_stage,relation_evidence_json,inference_json,uncertainty,created_at "
        "FROM k10_company_mappings WHERE event_id=? AND event_revision=? ORDER BY rowid",
        (event_id, event_revision),
    ).fetchall()
    return [row for row in rows if row[0] in mapping_ids]


def _mapping_payloads(rows: Sequence[Any]) -> list[dict[str, Any]]:
    return [{"mappingId": row[0], "companyCode": row[1], "affectedStage": row[2],
             "relationEvidence": json.loads(row[3]), "inference": json.loads(row[4]),
             "uncertainty": row[5], "createdAt": row[6]} for row in rows]


def _opportunity_for_candidate_conn(conn, candidate_id: str, *, as_of: datetime | None = None) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT o.opportunity_id,o.company_window_id,w.d1_trade_date,w.d2_trade_date "
        "FROM k10_publication_samples s JOIN k10_opportunities o ON o.opportunity_id=s.opportunity_id "
        "JOIN k10_company_windows w ON w.company_window_id=o.company_window_id "
        "JOIN k10_publication_batches b ON b.batch_id=s.batch_id "
        "WHERE s.candidate_id=? ORDER BY b.available_at DESC,s.sample_id DESC LIMIT 1", (candidate_id,)
    ).fetchone()
    if row is None:
        return None
    return {"opportunityId": row[0], "companyWindowId": row[1], "d1TradeDate": row[2],
            "d2TradeDate": row[3], "state": _opportunity_state(conn, str(row[0]), as_of=as_of)}


def get_opportunity_for_candidate(*, candidate_id: str, db_path: Path) -> dict[str, Any] | None:
    with read_connection(db_path) as conn:
        require_schema(conn)
        return _opportunity_for_candidate_conn(conn, candidate_id)


def load_candidate_context(*, candidate_id: str, cutoff_at: str, db_path: Path,
                           lifecycle_as_of: datetime | None = None) -> Optional[dict[str, Any]]:
    """Read an offered or observed candidate's frozen event/mapping evidence for morning review."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        base = conn.execute(
            "SELECT c.event_id,c.event_revision,c.company_code,c.comparison_json,c.evidence_json,"
            "e.headline,e.event_kind,e.facts_json,e.source_refs_json "
            "FROM k10_candidates c JOIN k10_event_revisions e ON e.event_id=c.event_id AND e.revision=c.event_revision "
            "WHERE c.candidate_id=?", (candidate_id,),
        ).fetchone()
        if base is None:
            return None
        comparison, candidate_evidence = json.loads(base[3]), json.loads(base[4])
        facts, source_refs = json.loads(base[7]), json.loads(base[8])
        mappings = _mapping_payloads(_mapping_context_rows(
            conn, event_id=base[0], event_revision=base[1], candidate_evidence=candidate_evidence,
        ))
        frozen_refs = _frozen_evidence_refs(event_source_refs=source_refs, event_facts=facts,
                                             comparison=comparison, mappings=mappings)
        observations = conn.execute("SELECT observation_id FROM k10_observations WHERE candidate_id=? ORDER BY rowid", (candidate_id,)).fetchall()
        return {"candidateId": candidate_id, "cutoffAt": cutoff_at,
                "observationIds": [row[0] for row in observations],
                "opportunity": _opportunity_for_candidate_conn(conn, candidate_id, as_of=lifecycle_as_of),
                "candidate": {"candidateId": candidate_id, "eventId": base[0], "eventRevision": base[1],
                              "companyCode": base[2], "comparison": comparison,
                              "evidence": candidate_evidence, "state": _state_in_connection(conn, candidate_id)},
                "event": {"eventId": base[0], "revision": base[1], "headline": base[5], "kind": base[6],
                          "facts": facts, "sourceRefs": source_refs},
                "mappings": mappings, "frozenEvidenceRefs": frozen_refs,
                "documents": _frozen_documents(conn, frozen_refs, cutoff_at=cutoff_at)}


def load_observation_context(
    *, observation_id: str, cutoff_at: str, db_path: Path,
) -> Optional[dict[str, Any]]:
    """在明确资料截止时间读取一个 observation 的冻结输入，不执行模型或补采集。"""
    with read_connection(db_path) as conn:
        require_schema(conn)
        base = conn.execute(
            "SELECT o.candidate_id,c.event_id,c.event_revision,c.company_code,c.comparison_json,c.evidence_json,"
            "e.headline,e.event_kind,e.facts_json,e.source_refs_json "
            "FROM k10_observations o JOIN k10_candidates c ON c.candidate_id=o.candidate_id "
            "JOIN k10_event_revisions e ON e.event_id=c.event_id AND e.revision=c.event_revision "
            "WHERE o.observation_id=?", (observation_id,),
        ).fetchone()
        if base is None:
            return None
        comparison, candidate_evidence = json.loads(base[4]), json.loads(base[5])
        facts, source_refs = json.loads(base[8]), json.loads(base[9])
        mappings = _mapping_payloads(_mapping_context_rows(
            conn, event_id=base[1], event_revision=base[2], candidate_evidence=candidate_evidence,
        ))
        frozen_refs = _frozen_evidence_refs(event_source_refs=source_refs, event_facts=facts,
                                             comparison=comparison, mappings=mappings)
        return {"observationId": observation_id, "cutoffAt": cutoff_at,
                "opportunity": _opportunity_for_candidate_conn(conn, str(base[0])),
                "candidate": {"candidateId": base[0], "eventId": base[1], "eventRevision": base[2],
                              "companyCode": base[3], "comparison": comparison,
                              "evidence": candidate_evidence, "state": _state_in_connection(conn, str(base[0]))},
                "event": {"eventId": base[1], "revision": base[2], "headline": base[6], "kind": base[7],
                          "facts": facts, "sourceRefs": source_refs},
                "mappings": mappings, "frozenEvidenceRefs": frozen_refs,
                "documents": _frozen_documents(conn, frozen_refs, cutoff_at=cutoff_at)}


def load_task_analysis_config(*, task_id: str, db_path: Path) -> Optional[dict[str, Any]]:
    """从分析任务冻结关联的 observation→candidate→scan 读取同一配置修订。"""
    with read_connection(db_path) as conn:
        require_schema(conn)
        task = conn.execute("SELECT payload_json FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()
        if task is None:
            return None
        payload = json.loads(task[0])
        frozen_config_id = payload.get("configId")
        frozen_config_revision = payload.get("configRevision")
        if isinstance(frozen_config_id, str) and isinstance(frozen_config_revision, int):
            frozen = read_run_config(config_id=frozen_config_id, revision=frozen_config_revision, db_path=db_path)
            if frozen is None:
                return None
            return {"configId": frozen["configId"], "configRevision": frozen["revision"],
                    "payload": frozen["payload"], "contentSha256": frozen["contentSha256"],
                    "createdAt": frozen["createdAt"]}
        observation_id = payload.get("observationId")
        if not isinstance(observation_id, str) or not observation_id:
            return None
        scan = conn.execute(
            "SELECT s.config_id,s.config_revision FROM k10_observations o "
            "JOIN k10_candidates c ON c.candidate_id=o.candidate_id "
            "JOIN k10_scans s ON s.scan_id=c.scan_id WHERE o.observation_id=?", (observation_id,)
        ).fetchone()
        if scan is None or scan[0] is None or scan[1] is None:
            return None
        config = conn.execute(
            "SELECT payload_json FROM k10_run_config_revisions WHERE config_id=? AND revision=?", (scan[0], scan[1])
        ).fetchone()
    if config is None:
        return None
    return {"configId": scan[0], "configRevision": int(scan[1]), "payload": json.loads(config[0])}


def load_analysis_revision(
    *, observation_id: str, input_cutoff_at: str, analysis_kind: str, db_path: Path,
) -> Optional[dict[str, Any]]:
    """读指定输入截止的最新分析修订，供任务崩溃后恢复，不改变历史。"""
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT analysis_id,revision,input_lineage_json,content_json,status,created_at "
            "FROM k10_analysis_revisions WHERE observation_id=? AND input_cutoff_at=? AND analysis_kind=? "
            "ORDER BY revision DESC,rowid DESC LIMIT 1", (observation_id, input_cutoff_at, analysis_kind),
        ).fetchone()
    if row is None:
        return None
    return {"analysisId": row[0], "revision": int(row[1]), "inputLineage": json.loads(row[2]),
            "content": json.loads(row[3]), "status": row[4], "createdAt": row[5]}


def load_analysis_request_context(*, request_id: str, db_path: Path) -> Optional[dict[str, Any]]:
    """Read the exact follow-up request snapshot, including only its frozen source versions."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute(
            "SELECT observation_id,company_window_id,source_refs_json,input_cutoff_at,kind,question,global_revision,parent_revision "
            "FROM k10_analysis_requests WHERE request_id=?", (request_id,),
        ).fetchone()
    if row is None:
        return None
    base = load_observation_context(observation_id=str(row[0]), cutoff_at=str(row[3]), db_path=db_path)
    if base is None:
        return None
    request_refs = json.loads(row[2])
    # A follow-up must retain the initial frozen case as well as the explicitly supplied
    # additions.  Exact document+revision identity prevents either list from drifting.
    merged_refs: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for raw in [*(base.get("frozenEvidenceRefs") or ()), *request_refs]:
        if isinstance(raw, Mapping) and isinstance(raw.get("documentId"), str) and isinstance(raw.get("revision"), int):
            key = (raw["documentId"], raw["revision"])
            if key not in seen:
                merged_refs.append(dict(raw)); seen.add(key)
    with read_connection(db_path) as conn:
        require_schema(conn)
        documents = _frozen_documents(conn, merged_refs, cutoff_at=str(row[3]))
        prior_attempt_rows = [] if row[7] is None else conn.execute(
            "SELECT analysis_id,revision,analysis_kind,status,input_cutoff_at,input_lineage_json,content_json,created_at "
            "FROM k10_analysis_revisions WHERE observation_id=? AND revision=? AND analysis_kind IN ('pro','con') "
            "ORDER BY rowid",
            (str(row[0]), int(row[7])),
        ).fetchall()
    if len(documents) != len(merged_refs):
        raise K10Conflict("追加分析冻结资料版本不可读取")
    request_documents = [document for document in documents if (document.get("documentId"), document.get("revision"))
                         in {(ref["documentId"], ref["revision"]) for ref in request_refs}]
    latest_prior_by_role = {item[2]: item for item in prior_attempt_rows}
    prior_analyses = [{"analysisId": item[0], "revision": int(item[1]), "role": item[2], "status": item[3], "inputCutoffAt": item[4],
                       "inputLineage": json.loads(item[5]), "content": json.loads(item[6]), "createdAt": item[7]}
                      for _role, item in sorted(latest_prior_by_role.items())]
    return {**base, "companyWindowId": row[1], "cutoffAt": row[3], "frozenEvidenceRefs": merged_refs,
            "documents": documents, "requestDocuments": request_documents, "priorAnalyses": prior_analyses,
            "analysisRequest": {"requestId": request_id, "kind": row[4], "question": row[5],
            "globalRevision": int(row[6]), "targetRevision": int(row[6]),
            "parentRevision": None if row[7] is None else int(row[7]), "sourceRefs": request_refs}}


def list_analysis_chain(*, company_window_id: str, db_path: Path) -> dict[str, Any]:
    """Return every analysis revision for one shared company-window observation in revision order."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        binding = conn.execute(
            "SELECT observation_id,task_id FROM k10_company_window_observations WHERE company_window_id=?", (company_window_id,)
        ).fetchone()
        if binding is None:
            return {"companyWindowId": company_window_id, "items": []}
        observation_id, initial_task_id = str(binding[0]), str(binding[1])
        requests = conn.execute(
            "SELECT request_id,task_id,global_revision,parent_revision,kind,question,source_refs_json,input_cutoff_at,created_at "
            "FROM k10_analysis_requests WHERE company_window_id=? AND observation_id=? ORDER BY global_revision",
            (company_window_id, observation_id),
        ).fetchall()
        request_by_revision = {int(row[2]): row for row in requests}
        artifacts = conn.execute(
            "SELECT rowid,analysis_id,revision,analysis_kind,input_cutoff_at,input_lineage_json,content_json,status,created_at "
            "FROM k10_analysis_revisions WHERE observation_id=? AND analysis_kind IN ('pro','con') ORDER BY revision,rowid",
            (observation_id,),
        ).fetchall()
        grouped_attempts: dict[int, dict[str, Any]] = {}
        for row in artifacts:
            grouped_attempts.setdefault(int(row[2]), {})[str(row[3])] = row
        grouped = {revision: [by_role[key] for key in sorted(by_role, key=lambda role: {"pro": 0, "con": 1}.get(role, 2))]
                   for revision, by_role in grouped_attempts.items()}
        revisions = sorted(set(grouped) | set(request_by_revision))
        items: list[dict[str, Any]] = []
        for revision in revisions:
            request = request_by_revision.get(revision)
            rows = grouped.get(revision, [])
            first = rows[0] if rows else None
            lineage = json.loads(first[5]) if first is not None else {}
            source_refs = json.loads(request[6]) if request is not None else list(lineage.get("frozenEvidenceRefs") or lineage.get("documentVersions") or [])
            task_id = str(request[1]) if request is not None else initial_task_id
            task = conn.execute("SELECT status,stage,attempt_count,error_text,created_at,updated_at FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()
            analyses = [{"analysisId": row[1], "role": row[3], "status": row[7], "inputCutoffAt": row[4],
                         "inputLineage": json.loads(row[5]), "content": json.loads(row[6]), "createdAt": row[8]}
                        for row in rows]
            items.append({"revision": revision,
                          "inputCutoffAt": request[7] if request is not None else (first[4] if first is not None else None),
                          "requestId": request[0] if request is not None else None,
                          "kind": request[4] if request is not None else "initial",
                          "question": request[5] if request is not None else None,
                          "parentRevision": None if request is None or request[3] is None else int(request[3]),
                          "sourceRefs": source_refs, "analyses": analyses,
                          "job": None if task is None else {"taskId": task_id, "status": task[0], "stage": task[1],
                                  "attemptCount": int(task[2]), "error": task[3], "createdAt": task[4], "updatedAt": task[5]}})
    return {"companyWindowId": company_window_id, "items": items}


def candidate_state(*, candidate_id: str, db_path: Path) -> Optional[str]:
    """由追加动作计算当前候选状态，绝不覆盖动作历史。"""
    with read_connection(db_path) as conn:
        require_schema(conn)
        exists = conn.execute("SELECT 1 FROM k10_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        if exists is None:
            return None
        action = conn.execute(
            "SELECT action FROM k10_candidate_actions WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
    if action is None:
        return "offered"
    return {"observe": "observed", "skip": "skipped", "restore": "offered", "withdraw": "offered"}[action[0]]


def claim_tasks(
    *, worker_id: str, now: datetime, lease_for: timedelta, limit: int, db_path: Path,
) -> list[Task]:
    """以租约领取可运行任务；过期运行任务可恢复，未过期的不被另一 worker 抢走。"""
    if not worker_id or limit < 1 or lease_for.total_seconds() <= 0:
        raise ValueError("worker_id、limit 和 lease_for 必须有效")
    if now.tzinfo is None:
        raise ValueError("now 必须带时区")
    now_text = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    lease_until = (now.astimezone(timezone.utc) + lease_for).isoformat(timespec="seconds")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        rows = conn.execute(
            "SELECT t.task_id FROM k10_tasks t LEFT JOIN k10_task_retry_schedules r ON r.task_id=t.task_id "
            "WHERE NOT EXISTS (SELECT 1 FROM k10_discovery_retirements d WHERE d.task_id=t.task_id) AND "
            "((t.status='queued' AND (r.not_before_at IS NULL OR r.not_before_at <= ?)) "
            "OR (t.status='running' AND t.lease_until < ?)) "
            "ORDER BY CASE t.kind WHEN 'morning_scan' THEN 0 WHEN 'evening_scan' THEN 1 ELSE 2 END, t.created_at,t.task_id LIMIT ?",
            (now_text, now_text, limit),
        ).fetchall()
        task_ids = [str(row[0]) for row in rows]
        claimed: list[Task] = []
        for task_id in task_ids:
            conn.execute(
                "UPDATE k10_tasks SET status='running',stage='leased',attempt_count=attempt_count+1,"
                "lease_owner=?,lease_until=?,updated_at=? WHERE task_id=? AND "
                "NOT EXISTS (SELECT 1 FROM k10_discovery_retirements d WHERE d.task_id=k10_tasks.task_id) AND "
                "((status='queued' AND NOT EXISTS (SELECT 1 FROM k10_task_retry_schedules r WHERE r.task_id=k10_tasks.task_id AND r.not_before_at > ?)) "
                "OR (status='running' AND lease_until < ?))",
                (worker_id, lease_until, now_text, task_id, now_text, now_text),
            )
            row = conn.execute(
                "SELECT task_id,kind,status,attempt_count,lease_owner,lease_until,payload_json FROM k10_tasks "
                "WHERE task_id=? AND lease_owner=? AND lease_until=?", (task_id, worker_id, lease_until),
            ).fetchone()
            if row is not None:
                claimed.append(_task_from_row(row))
    return claimed


def claim_task_by_id(*, task_id: str, worker_id: str, now: datetime, lease_for: timedelta, db_path: Path) -> Optional[Task]:
    """Claim one known child task without accidentally consuming unrelated queued work."""
    if not worker_id or lease_for.total_seconds() <= 0 or now.tzinfo is None:
        raise ValueError("task_id、worker_id、带时区 now 与 lease_for 必须有效")
    now_text = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    lease_until = (now.astimezone(timezone.utc) + lease_for).isoformat(timespec="seconds")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        changed = conn.execute(
            "UPDATE k10_tasks SET status='running',stage='leased',attempt_count=attempt_count+1,lease_owner=?,lease_until=?,updated_at=? "
            "WHERE task_id=? AND NOT EXISTS (SELECT 1 FROM k10_discovery_retirements d WHERE d.task_id=k10_tasks.task_id) AND "
            "((status='queued' AND NOT EXISTS (SELECT 1 FROM k10_task_retry_schedules r WHERE r.task_id=k10_tasks.task_id AND r.not_before_at > ?)) "
            "OR (status='running' AND lease_until < ?))",
            (worker_id, lease_until, now_text, task_id, now_text, now_text),
        ).rowcount
        if changed != 1:
            return None
        row = conn.execute(
            "SELECT task_id,kind,status,attempt_count,lease_owner,lease_until,payload_json FROM k10_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
    return _task_from_row(row)


def task_execution_input(*, task_id: str, db_path: Path) -> Optional[dict[str, Any]]:
    """Read the immutable execution inputs for an already-owned task; no schema mutation."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = conn.execute("SELECT budget_json,checkpoint_json,input_version,input_cutoff_at FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()
    if row is None:
        return None
    return {"budget": json.loads(row[0]), "checkpoint": json.loads(row[1]), "inputVersion": row[2], "inputCutoffAt": row[3],
            "executionProfile": task_execution_profile(task_id=task_id, db_path=db_path)}


_UNSAFE_EXECUTION_RESULT_KEYS = frozenset({"prompt", "rawResponse", "raw_response", "originalText", "original_text"})


def _safe_execution_result(value: Any) -> Any:
    """Persist only validated derivative output, never a provider response or prompt body."""
    if isinstance(value, Mapping):
        if _UNSAFE_EXECUTION_RESULT_KEYS & {str(key) for key in value}:
            raise ValueError("执行检查点不得保存 prompt、原始响应或全文资料")
        return {str(key): _safe_execution_result(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_execution_result(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("执行检查点结果必须是 JSON 值")


def record_execution_checkpoint(
    *, task_id: str, item_kind: str, item_key: str, stage: str, input_sha256: str, status: str,
    attempt_count: int, network_attempt_count: int, repair_attempt_count: int, elapsed_ms: int,
    input_tokens: int | None, output_tokens: int | None, result: Mapping[str, Any] | list[Any] | None,
    safe_error_code: str | None, safe_error_ref: str | None, updated_at: str, db_path: Path,
    leaseguard: Callable[[], None] | None = None,
) -> None:
    """Atomically persist one resumable, sanitized item stage under the owning task lease.

    ``leaseguard`` must be read-only.  It runs once before opening the write
    transaction to fail quickly, and again after ``BEGIN IMMEDIATE`` has
    reserved the writer slot.  The latter check closes the hand-off window in
    which a former worker passed an early guard but another worker then claimed
    the expired task before this checkpoint was persisted.
    """
    if item_kind not in {"document", "event", "global"} or not item_key or not stage or not input_sha256:
        raise ValueError("执行检查点缺少有效 item、stage 或输入哈希")
    if status not in {"pending", "running", "completed", "failed"}:
        raise ValueError("执行检查点状态无效")
    numbers = (attempt_count, network_attempt_count, repair_attempt_count, elapsed_ms)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in numbers):
        raise ValueError("执行检查点计数必须是非负整数")
    if any(value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0)
           for value in (input_tokens, output_tokens)):
        raise ValueError("执行检查点 token 必须是非负整数或 null")
    if status == "completed" and result is None:
        raise ValueError("完成的执行检查点必须保存已校验的派生结果")
    if status != "completed" and result is not None:
        raise ValueError("未完成执行检查点不能保存未经完成的结果")
    if status == "failed" and not safe_error_code:
        raise ValueError("失败执行检查点必须有安全错误码")
    if leaseguard is not None:
        leaseguard()
    safe_result = _safe_execution_result(result) if result is not None else None
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        # ``write_connection`` has begun the immediate transaction, so a new
        # claimant cannot interleave after this check and before the upsert.
        # TaskContext.require_lease uses a read-only connection; a write guard
        # here would deadlock and is deliberately not a supported callback.
        if leaseguard is not None:
            leaseguard()
        if conn.execute("SELECT 1 FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone() is None:
            raise K10Conflict("执行检查点任务不存在")
        expected = (input_sha256, status, attempt_count, network_attempt_count, repair_attempt_count,
                    elapsed_ms, input_tokens, output_tokens, _json(safe_result) if safe_result is not None else None,
                    safe_error_code, safe_error_ref)
        old = conn.execute(
            "SELECT input_sha256,status,attempt_count,network_attempt_count,repair_attempt_count,elapsed_ms,"
            "input_tokens,output_tokens,result_json,safe_error_code,safe_error_ref FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND item_kind=? AND item_key=? AND stage=?",
            (task_id, item_kind, item_key, stage),
        ).fetchone()
        if old is not None and old[1] == "completed" and tuple(old) != expected:
            raise K10Conflict("已完成执行检查点不可被重写")
        conn.execute(
            "INSERT INTO k10_execution_item_checkpoints(task_id,item_kind,item_key,stage,input_sha256,status,"
            "attempt_count,network_attempt_count,repair_attempt_count,elapsed_ms,input_tokens,output_tokens,"
            "result_json,safe_error_code,safe_error_ref,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(task_id,item_kind,item_key,stage) DO UPDATE SET input_sha256=excluded.input_sha256,"
            "status=excluded.status,attempt_count=excluded.attempt_count,network_attempt_count=excluded.network_attempt_count,"
            "repair_attempt_count=excluded.repair_attempt_count,elapsed_ms=excluded.elapsed_ms,input_tokens=excluded.input_tokens,"
            "output_tokens=excluded.output_tokens,result_json=excluded.result_json,safe_error_code=excluded.safe_error_code,"
            "safe_error_ref=excluded.safe_error_ref,updated_at=excluded.updated_at",
            (task_id, item_kind, item_key, stage, *expected, updated_at),
        )


def reject_completed_execution_checkpoint(
    *, task_id: str, item_kind: str, item_key: str, stage: str, input_sha256: str,
    safe_error_code: str, updated_at: str, db_path: Path,
    leaseguard: Callable[[], None] | None = None,
) -> bool:
    """Downgrade one completed derivative after stricter semantic validation.

    The validated JSON derivative and actual model usage stay in the immutable
    row for audit. Only its reusable completion state changes, under the same
    task lease and exact input identity that created it.
    """
    if (item_kind not in {"document", "event", "global"} or not item_key or not stage
            or not input_sha256 or not isinstance(safe_error_code, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", safe_error_code)):
        raise ValueError("拒绝执行检查点参数无效")
    if leaseguard is not None:
        leaseguard()
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        if leaseguard is not None:
            leaseguard()
        changed = conn.execute(
            "UPDATE k10_execution_item_checkpoints SET status='failed',safe_error_code=?,safe_error_ref=?,updated_at=? "
            "WHERE task_id=? AND item_kind=? AND item_key=? AND stage=? AND input_sha256=? AND status='completed'",
            (safe_error_code, item_key, updated_at, task_id, item_kind, item_key, stage, input_sha256),
        ).rowcount
    return changed == 1


def completed_execution_items(*, task_id: str, item_kind: str, stage: str, db_path: Path) -> list[dict[str, Any]]:
    """Read only immutable completed derivatives for a frozen-task resume."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        rows = conn.execute(
            "SELECT item_key,input_sha256,result_json,attempt_count,network_attempt_count,repair_attempt_count,"
            "elapsed_ms,input_tokens,output_tokens,updated_at FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND item_kind=? AND stage=? AND status='completed' ORDER BY item_key",
            (task_id, item_kind, stage),
        ).fetchall()
    return [{"itemKey": row[0], "inputSha256": row[1], "result": json.loads(row[2]), "attemptCount": row[3],
             "networkAttemptCount": row[4], "repairAttemptCount": row[5], "elapsedMs": row[6],
             "inputTokens": row[7], "outputTokens": row[8], "updatedAt": row[9]} for row in rows]


def execution_progress_for_scan(*, scan_id: str, db_path: Path) -> Optional[dict[str, Any]]:
    """Project V3 title-first work without exposing bodies, prompts or usage totals."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        scan = conn.execute(
            "SELECT s.status,s.coverage_json,s.config_id,s.config_revision,c.content_sha256,b.task_id,"
            "b.execution_config_id,b.execution_config_revision,b.execution_content_sha256,b.binding_kind,t.stage,t.status "
            "FROM k10_scans s LEFT JOIN k10_run_config_revisions c ON c.config_id=s.config_id AND c.revision=s.config_revision "
            "LEFT JOIN k10_scan_execution_bindings b ON b.scan_id=s.scan_id LEFT JOIN k10_tasks t ON t.task_id=b.task_id "
            "WHERE s.scan_id=?", (scan_id,),
        ).fetchone()
        if scan is None or scan[5] is None:
            return None
        task_id = str(scan[5])
        try:
            coverage = json.loads(scan[1])
        except (TypeError, ValueError, json.JSONDecodeError):
            coverage = {}
        manifest = conn.execute(
            "SELECT input_refs_json,article_limit,title_status,selection_status FROM k10_title_triage_manifests WHERE task_id=?", (task_id,),
        ).fetchone()
        items = conn.execute(
            "SELECT disposition,selection_rank FROM k10_title_triage_items WHERE task_id=?", (task_id,),
        ).fetchall()
        selection = conn.execute("SELECT selected_refs_json FROM k10_title_selection_manifests WHERE task_id=?", (task_id,)).fetchone()
        articles = conn.execute(
            "SELECT admission_kind,state FROM k10_article_admissions WHERE task_id=?", (task_id,),
        ).fetchall()
        checkpoints = conn.execute(
            "SELECT stage,status,safe_error_code,safe_error_ref,result_json FROM k10_execution_item_checkpoints WHERE task_id=?", (task_id,),
        ).fetchall()
        tavily_checkpoint_results = conn.execute(
            "SELECT result_json FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND stage='tavily_evidence' AND status='completed'",
            (task_id,),
        ).fetchall()
        attempts = conn.execute(
            "SELECT state,COUNT(*) FROM k10_external_attempts WHERE task_id=? GROUP BY state", (task_id,),
        ).fetchall()
        retry = conn.execute("SELECT not_before_at FROM k10_task_retry_schedules WHERE task_id=?", (task_id,)).fetchone()
        retirement = conn.execute("SELECT reason_code,retired_at FROM k10_discovery_retirements WHERE scan_id=?", (scan_id,)).fetchone()
    coverage = coverage if isinstance(coverage, Mapping) else {}
    tavily_refs: set[tuple[str, int]] = set()
    for (raw_result,) in tavily_checkpoint_results:
        try:
            result = json.loads(raw_result)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(result, Mapping):
            continue
        receipt = result.get("coverage")
        if not isinstance(receipt, Mapping) or receipt.get("requestState") != "completed":
            continue
        for raw_ref in result.get("documentRefs", ()):
            if (isinstance(raw_ref, Mapping) and isinstance(raw_ref.get("documentId"), str)
                    and not isinstance(raw_ref.get("revision"), bool) and isinstance(raw_ref.get("revision"), int)
                    and raw_ref["revision"] >= 1):
                tavily_refs.add((raw_ref["documentId"], raw_ref["revision"]))
    tavily_excerpts = 0
    if tavily_refs:
        clauses = ",".join("(?,?)" for _ in tavily_refs)
        values = tuple(value for ref in tavily_refs for value in ref)
        with read_connection(db_path) as conn:
            tavily_excerpts = int(conn.execute(
                "SELECT COUNT(*) FROM k10_source_document_versions v "
                "JOIN k10_source_documents d ON d.document_id=v.document_id "
                "WHERE d.source_key='tavily_verification' AND v.excerpt IS NOT NULL "
                "AND length(trim(v.excerpt))>0 AND (v.document_id,v.revision) IN (" + clauses + ")",
                values,
            ).fetchone()[0])
    input_refs = [] if manifest is None else json.loads(manifest[0])
    title_covered = len(items)
    exact_duplicate_count = sum(1 for row in items if row[0] == "exact_duplicate")
    checkpoint_titles = 0
    for stage, status, _, _, result_json in checkpoints:
        if stage != "model:titleBatch" or status != "completed":
            continue
        try:
            result = json.loads(result_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(result, Mapping):
            count = result.get("itemCount", result.get("processedCount"))
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                checkpoint_titles += count
            elif isinstance(result.get("items"), list):
                checkpoint_titles += len(result["items"])
    received = coverage.get("receivedTitleCount")
    if isinstance(received, bool) or not isinstance(received, int) or received < 0:
        received = len(input_refs) if manifest is not None else None
    exact = len(input_refs) if manifest is not None else None
    triaged = (title_covered - exact_duplicate_count) if title_covered else (min(checkpoint_titles, exact) if exact is not None else None)
    selected = 0 if selection is None else len(json.loads(selection[0]))
    title_incomplete = (manifest is not None and manifest[2] == "partial") or (
        coverage.get("executionState") == "title_incomplete"
        or coverage.get("pipelineState") in {"title_incomplete", "partial"}
    )
    title_counts = None if manifest is None else {
        "received": received, "exactDeduplicated": exact_duplicate_count, "triaged": triaged,
        "merged": sum(1 for row in items if row[0] == "merged"),
        "notSelected": sum(1 for row in items if row[0] != "exact_duplicate" and row[1] is None),
        "protected": sum(1 for row in items if row[0] == "protected"),
        "partial": max(0, exact - title_covered) if title_incomplete else 0,
    }
    article_counts = None if manifest is None else {
        "limit": int(manifest[1]), "selected": selected, "admitted": len(articles),
        "completed": sum(1 for row in articles if row[1] == "completed"),
        "missingBody": sum(1 for row in articles if row[1] == "missing_body"),
        "tavilyExcerpt": tavily_excerpts,
        "tavilyFullArticle": sum(1 for row in articles if row[0] == "tavily_full_article"),
    }
    attempt_counts = {"started": 0, "succeeded": 0, "failed": 0, "unknown": 0}
    for state, count in attempts:
        if state in attempt_counts:
            attempt_counts[str(state)] = int(count)
    failures: list[dict[str, str]] = []
    seen_failures: set[tuple[str, str, str]] = set()
    for stage, status, code, ref, _ in checkpoints:
        if status == "failed" and isinstance(code, str):
            key = (str(stage), code, str(ref or ""))
            if key not in seen_failures:
                failures.append({"stage": key[0], "code": key[1], "ref": key[2]})
                seen_failures.add(key)
    for kind, state in articles:
        if state == "failed":
            key = ("article", "body_failed", str(kind))
            if key not in seen_failures:
                failures.append({"stage": "article", "code": "body_failed", "ref": str(kind)})
                seen_failures.add(key)
    raw_state = str(scan[0])
    state = {"queued": "running", "running": "running", "completed": "completed", "partial": "partial", "failed": "failed", "not_configured": "notConfigured"}.get(raw_state, "failed")
    control = run_control_status(db_path=db_path)
    if retirement is not None:
        state = "partial"
    elif control["state"] == "closed" and raw_state in {"queued", "running"}:
        state = "paused"
    execution_state = coverage.get("executionState")
    pipeline_state = coverage.get("pipelineState")
    if retirement is not None:
        stage = "retired"
    elif retry is not None and str(scan[11]) == "queued":
        stage = "recovery"
    elif execution_state in {"title_selection", "title_triage", "title_incomplete"} or pipeline_state == "title_incomplete":
        stage = "title_triage"
    elif execution_state == "deep_read":
        stage = "deep_read"
    elif selection is None and manifest is not None:
        stage = "title_triage"
    elif raw_state == "completed":
        stage = "published"
    else:
        stage = str(scan[10])
    return {
        "state": state, "stage": stage, "titleCounts": title_counts, "articleCounts": article_counts,
        "attemptCounts": attempt_counts, "factCacheHits": coverage.get("factCacheHits") if isinstance(coverage.get("factCacheHits"), int) else None,
        "safeFailures": failures, "coverageStatus": "complete" if state == "completed" and not failures else "partial",
        "nextRetryAt": None if retry is None or str(scan[11]) != "queued" else retry[0],
        "strategyBinding": None if scan[2] is None or scan[3] is None else {"configId": scan[2], "revision": int(scan[3]), "contentSha256": scan[4]},
        "executionBinding": {"configId": scan[6], "revision": scan[7], "contentSha256": scan[8], "bindingKind": scan[9]},
        "runControl": {"state": "ready" if control["state"] == "open" else "paused", "reasonCode": control["reasonCode"], "changedAt": control["changedAt"]},
        "retiredByUser": None if retirement is None else {"reasonCode": retirement[0], "retiredAt": retirement[1]},
        "taskId": task_id,
    }


def finish_task(
    *, task_id: str, worker_id: str, status: str, stage: str, checkpoint: Mapping[str, Any],
    error_text: Optional[str], finished_at: datetime, db_path: Path,
) -> None:
    if status not in {"completed", "failed", "not_configured", "cancelled"}:
        raise ValueError("finish_task 只接受终态")
    if finished_at.tzinfo is None:
        raise ValueError("finished_at 必须带时区")
    now_text = finished_at.astimezone(timezone.utc).isoformat(timespec="seconds")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        checkpoint = _preserve_execution_started_at(conn, task_id=task_id, checkpoint=checkpoint)
        changed = conn.execute(
            "UPDATE k10_tasks SET status=?,stage=?,checkpoint_json=?,error_text=?,lease_owner=NULL,lease_until=NULL,"
            "updated_at=? WHERE task_id=? AND status='running' AND lease_owner=? AND lease_until >= ?",
            (status, stage, _json(checkpoint), error_text, now_text, task_id, worker_id, now_text),
        ).rowcount
        if changed != 1:
            raise K10Conflict("任务租约已失效或不属于当前 worker")
        conn.execute("DELETE FROM k10_task_retry_schedules WHERE task_id=?", (task_id,))


def schedule_task_retry(
    *, task_id: str, worker_id: str, stage: str, checkpoint: Mapping[str, Any], safe_error_code: str,
    not_before_at: datetime, scheduled_at: datetime, retry_kind: str, max_failure_attempts: int, db_path: Path,
) -> bool:
    """Schedule a slice continuation or bounded failure retry without a terminal notification."""
    if (not safe_error_code or not stage or not_before_at.tzinfo is None or scheduled_at.tzinfo is None or
        retry_kind not in {"continuation", "failure"} or isinstance(max_failure_attempts, bool) or
        not isinstance(max_failure_attempts, int) or max_failure_attempts < 1):
        raise ValueError("延后重试需要安全错误码、阶段和带时区时间")
    not_before = not_before_at.astimezone(timezone.utc).isoformat(timespec="seconds")
    now_text = scheduled_at.astimezone(timezone.utc).isoformat(timespec="seconds")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        # This runs under the same immediate write transaction as the retry
        # row.  A worker-side precheck alone leaves a pause→schedule window.
        control = conn.execute(
            "SELECT state FROM k10_run_controls WHERE control_key='k10_discovery'"
        ).fetchone()
        if control is None or control[0] != "open":
            return False
        if conn.execute(
            "SELECT 1 FROM k10_discovery_retirements WHERE task_id=?", (task_id,)
        ).fetchone() is not None:
            return False
        row = conn.execute(
            "SELECT t.attempt_count,COALESCE(r.failure_attempt_count,0),t.checkpoint_json FROM k10_tasks t "
            "LEFT JOIN k10_task_retry_schedules r ON r.task_id=t.task_id "
            "WHERE t.task_id=? AND t.status='running' AND t.lease_owner=? AND t.lease_until >= ?",
            (task_id, worker_id, now_text),
        ).fetchone()
        if row is None:
            raise K10Conflict("任务租约已失效或不属于当前 worker")
        failure_count = int(row[1]) + (1 if retry_kind == "failure" else 0)
        if retry_kind == "failure" and failure_count >= max_failure_attempts:
            return False
        checkpoint = _preserve_execution_started_at(conn, task_id=task_id, checkpoint=checkpoint, existing_raw=row[2])
        changed = conn.execute(
            "UPDATE k10_tasks SET status='queued',stage='retry_scheduled',checkpoint_json=?,error_text=?,"
            "lease_owner=NULL,lease_until=NULL,updated_at=? WHERE task_id=? AND status='running' AND lease_owner=?",
            (_json(checkpoint), safe_error_code, now_text, task_id, worker_id),
        ).rowcount
        if changed != 1:
            raise K10Conflict("任务重试排程未取得当前租约")
        conn.execute(
            "INSERT INTO k10_task_retry_schedules(task_id,not_before_at,scheduled_attempt_count,failure_attempt_count,retry_kind,safe_error_code,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET not_before_at=excluded.not_before_at,"
            "scheduled_attempt_count=excluded.scheduled_attempt_count,failure_attempt_count=excluded.failure_attempt_count,"
            "retry_kind=excluded.retry_kind,safe_error_code=excluded.safe_error_code,updated_at=excluded.updated_at",
            (task_id, not_before, int(row[0]), failure_count, retry_kind, safe_error_code, now_text, now_text),
        )
    return True


def _execution_started_at(raw: object) -> str | None:
    if not isinstance(raw, Mapping):
        return None
    value = raw.get("executionStartedAt")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _preserve_execution_started_at(conn, *, task_id: str, checkpoint: Mapping[str, Any], existing_raw: object | None = None) -> dict[str, Any]:
    """Carry durable task runtime fields across handler checkpoint replacement."""
    if not isinstance(checkpoint, Mapping):
        raise ValueError("任务 checkpoint 必须是对象")
    existing = existing_raw
    if existing is None:
        row = conn.execute("SELECT checkpoint_json FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()
        existing = row[0] if row is not None else None
    try:
        parsed = json.loads(existing) if isinstance(existing, str) else existing
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    started = _execution_started_at(parsed)
    merged = dict(checkpoint)
    if started is not None:
        merged["executionStartedAt"] = started
    # Only authorize_discovery_recovery writes this authority. A handler's
    # progress result must neither drop it at a slice boundary nor forge/replace
    # it. All later slices need the same stable failed-stage recovery identity.
    merged.pop("recoveryAuthorized", None)
    authorization = parsed.get("recoveryAuthorized") if isinstance(parsed, Mapping) else None
    if authorization is not None:
        if not isinstance(authorization, Mapping):
            raise K10Conflict("任务恢复授权记录无效")
        merged["recoveryAuthorized"] = dict(authorization)
    return merged


def ensure_task_execution_started(
    *, task_id: str, worker_id: str, started_at: datetime, db_path: Path,
) -> str:
    """Persist the first real handler-entry time for one explicitly bound task.

    This is intentionally task state rather than process state: slice retries,
    lease recovery and a restarted worker continue the same whole-run deadline.
    """
    if not worker_id or started_at.tzinfo is None:
        raise ValueError("任务执行起点需要 worker 与带时区时间")
    now_text = started_at.astimezone(timezone.utc).isoformat(timespec="seconds")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        row = conn.execute(
            "SELECT checkpoint_json FROM k10_tasks WHERE task_id=? AND status='running' AND lease_owner=? AND lease_until >= ?",
            (task_id, worker_id, now_text),
        ).fetchone()
        if row is None:
            raise K10Conflict("任务租约已失效或不属于当前 worker")
        try:
            checkpoint = json.loads(row[0])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise K10Conflict("任务 checkpoint 无效") from exc
        if not isinstance(checkpoint, Mapping):
            raise K10Conflict("任务 checkpoint 无效")
        existing = _execution_started_at(checkpoint)
        if existing is not None:
            return existing
        stored = dict(checkpoint)
        stored["executionStartedAt"] = now_text
        changed = conn.execute(
            "UPDATE k10_tasks SET checkpoint_json=?,updated_at=? WHERE task_id=? AND status='running' AND lease_owner=? AND lease_until >= ?",
            (_json(stored), now_text, task_id, worker_id, now_text),
        ).rowcount
        if changed != 1:
            raise K10Conflict("任务执行起点未取得当前租约")
    return now_text


def renew_task_lease(
    *, task_id: str, worker_id: str, now: datetime, lease_for: timedelta, db_path: Path,
) -> Task:
    """续租只接受仍由当前 worker 持有且未过期的任务，过期实例无法延长或提交。"""
    if not worker_id or now.tzinfo is None or lease_for.total_seconds() <= 0:
        raise ValueError("worker_id、带时区 now 与正 lease_for 为必填")
    now_text = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    lease_until = (now.astimezone(timezone.utc) + lease_for).isoformat(timespec="seconds")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        if conn.execute("SELECT 1 FROM k10_discovery_retirements WHERE task_id=?", (task_id,)).fetchone() is not None:
            raise K10Conflict("用户已弃用该发现任务，禁止续租")
        changed = conn.execute(
            "UPDATE k10_tasks SET lease_until=?,updated_at=? WHERE task_id=? AND status='running' "
            "AND lease_owner=? AND lease_until >= ?",
            (lease_until, now_text, task_id, worker_id, now_text),
        ).rowcount
        if changed != 1:
            raise K10Conflict("任务租约已失效或不属于当前 worker")
        row = conn.execute(
            "SELECT task_id,kind,status,attempt_count,lease_owner,lease_until,payload_json FROM k10_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
    return _task_from_row(row)


def retry_task(
    *, task_id: str, expected_attempt_count: int, retried_at: str, db_path: Path,
) -> Task:
    """把一个明确失败态任务重新入队；调用方须带上看到的尝试数以避免盲目重试覆盖。"""
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        if conn.execute("SELECT 1 FROM k10_discovery_retirements WHERE task_id=?", (task_id,)).fetchone() is not None:
            raise K10Conflict("用户已弃用该发现任务，禁止重试")
        changed = conn.execute(
            "UPDATE k10_tasks SET status='queued',stage='retry_requested',error_text=NULL,lease_owner=NULL,"
            "lease_until=NULL,updated_at=? WHERE task_id=? AND attempt_count=? "
            "AND status IN ('failed','not_configured')",
            (retried_at, task_id, expected_attempt_count),
        ).rowcount
        if changed != 1:
            raise K10Conflict("任务不是可重试的当前失败版本")
        conn.execute("DELETE FROM k10_task_retry_schedules WHERE task_id=?", (task_id,))
        row = conn.execute(
            "SELECT task_id,kind,status,attempt_count,lease_owner,lease_until,payload_json FROM k10_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
    return _task_from_row(row)


def is_failed_title_scan(scan: Mapping[str, Any]) -> bool:
    """Recognize the B39 unpublished execution failure mislabeled as partial."""
    coverage = scan.get("coverage")
    return (scan.get("status") == "partial" and isinstance(coverage, Mapping)
            and coverage.get("executionState") == "title_incomplete"
            and coverage.get("inputSnapshotFrozen") is True
            and isinstance(coverage.get("titleFailure"), str)
            and not isinstance(coverage.get("discoveryDraft"), Mapping))


def authorize_discovery_recovery(
    *, scan_id: str, execution_config_id: str, execution_config_revision: int,
    confirmed_input_sha256: str, authorized_at: str, db_path: Path,
) -> Task:
    """Requeue the already-bound failed discovery task against its frozen input.

    A B39 recovery cannot create a fresh task: title triage, actual article
    admissions, successful provider checkpoints and research snapshots are all
    task-owned. This atomic transition leaves those rows in place and records
    the human-confirmed frozen-input digest for the coordinator's one allowed
    failed-stage resume path.
    """
    if (not isinstance(scan_id, str) or not scan_id or not isinstance(execution_config_id, str) or not execution_config_id
            or isinstance(execution_config_revision, bool) or not isinstance(execution_config_revision, int)
            or execution_config_revision < 1 or not isinstance(confirmed_input_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", confirmed_input_sha256)
            or not isinstance(authorized_at, str) or not authorized_at):
        raise ValueError("受控恢复参数无效")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        control = conn.execute("SELECT state FROM k10_run_controls WHERE control_key='k10_discovery'").fetchone()
        if control is None or control[0] != "open":
            raise K10Conflict("K10 运行已暂停，拒绝恢复")
        row = conn.execute(
            "SELECT s.status,s.coverage_json,b.task_id,b.execution_config_id,b.execution_config_revision,"
            "b.execution_content_sha256,t.status,t.attempt_count,t.checkpoint_json,t.input_cutoff_at,"
            "tb.execution_config_id,tb.execution_config_revision,tb.execution_content_sha256,t.stage "
            "FROM k10_scans s JOIN k10_scan_execution_bindings b ON b.scan_id=s.scan_id "
            "JOIN k10_tasks t ON t.task_id=b.task_id "
            "JOIN k10_task_execution_bindings tb ON tb.task_id=t.task_id WHERE s.scan_id=?",
            (scan_id,),
        ).fetchone()
        if row is None:
            raise K10Conflict("扫描没有可复用的原任务执行绑定")
        (scan_status, coverage_raw, task_id, bound_id, bound_revision, bound_hash,
         task_status, attempt_count, checkpoint_raw, _cutoff, task_bound_id,
         task_bound_revision, task_bound_hash, task_stage) = row
        try:
            coverage = json.loads(coverage_raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise K10Conflict("扫描覆盖记录无效") from exc
        title_partial = is_failed_title_scan({"status": scan_status, "coverage": coverage})
        paused_running = scan_status == "running" and task_status == "failed" and task_stage == "paused"
        if (scan_status not in {"failed", "not_configured"} and not title_partial and not paused_running) or task_status not in {"failed", "not_configured"}:
            raise K10Conflict("只有当前失败的冻结扫描及原任务可受控恢复")
        if conn.execute("SELECT 1 FROM k10_external_attempts WHERE task_id=? AND state IN ('started','unknown')", (task_id,)).fetchone() is not None:
            raise K10Conflict("仍有未结算的外部调用，拒绝恢复")
        if conn.execute("SELECT 1 FROM k10_publication_batches WHERE scan_id=?", (scan_id,)).fetchone() is not None:
            raise K10Conflict("已有正式发布批次不能受控恢复")
        profile = conn.execute(
            "SELECT content_sha256 FROM k10_execution_config_revisions WHERE config_id=? AND revision=?",
            (execution_config_id, execution_config_revision),
        ).fetchone()
        if (profile is None or bound_id != execution_config_id or bound_revision != execution_config_revision
                or task_bound_id != execution_config_id or task_bound_revision != execution_config_revision
                or bound_hash != profile[0] or task_bound_hash != profile[0]):
            raise K10Conflict("受控恢复必须使用原任务完全相同的执行配置")
        if conn.execute("SELECT 1 FROM k10_discovery_retirements WHERE scan_id=? OR task_id=?", (scan_id, task_id)).fetchone() is not None:
            raise K10Conflict("用户已弃用该发现任务，禁止恢复")
        try:
            coverage = json.loads(coverage_raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise K10Conflict("扫描覆盖记录无效") from exc
        refs = coverage.get("inputDocumentRefs") if isinstance(coverage, Mapping) else None
        if coverage.get("inputSnapshotFrozen") is not True or not isinstance(refs, list) or not refs:
            raise K10Conflict("扫描未冻结输入快照，拒绝恢复")
        actual = hashlib.sha256(json.dumps(refs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if actual != confirmed_input_sha256:
            raise K10Conflict("冻结资料哈希确认不匹配，拒绝恢复")
        try:
            checkpoint = json.loads(checkpoint_raw)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise K10Conflict("任务 checkpoint 无效") from exc
        if not isinstance(checkpoint, Mapping):
            raise K10Conflict("任务 checkpoint 无效")
        updated_checkpoint = dict(checkpoint)
        prior = updated_checkpoint.get("recoveryAuthorized")
        if prior is not None and not isinstance(prior, Mapping):
            raise K10Conflict("任务恢复授权记录无效")
        updated_checkpoint["recoveryAuthorized"] = {
            "scanId": scan_id, "frozenInputSha256": confirmed_input_sha256, "authorizedAt": authorized_at,
            "previousAttemptCount": int(attempt_count), "previousStage": updated_checkpoint.get("stage"),
            "previousScanStatus": scan_status,
        }
        if title_partial or paused_running:
            # Correct only the legacy title-execution status inside the same
            # authorized transaction. Inputs, cutoff, attempts and results stay.
            conn.execute("UPDATE k10_scans SET status='failed' WHERE scan_id=? AND status IN ('partial','running')", (scan_id,))
        changed = conn.execute(
            "UPDATE k10_tasks SET status='queued',stage='recovery_authorized',checkpoint_json=?,error_text=NULL,"
            "lease_owner=NULL,lease_until=NULL,updated_at=? WHERE task_id=? AND attempt_count=? "
            "AND status IN ('failed','not_configured')",
            (_json(updated_checkpoint), authorized_at, task_id, int(attempt_count)),
        ).rowcount
        if changed != 1:
            raise K10Conflict("任务不是可恢复的当前失败版本")
        conn.execute("DELETE FROM k10_task_retry_schedules WHERE task_id=?", (task_id,))
        task = conn.execute(
            "SELECT task_id,kind,status,attempt_count,lease_owner,lease_until,payload_json FROM k10_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
    return _task_from_row(task)



# --- V1.4 opportunity publication and fixed-window evaluation -----------------

def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("K10 时间必须带时区")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha256(chr(31).join(parts).encode('utf-8')).hexdigest()[:32]}"


def _publication_input_payload(value) -> dict[str, Any]:
    payload = {"candidateId": value.candidate_id, "companyCode": value.company_code,
            "eventId": value.event_id, "eventRevision": value.event_revision,
            "opportunityKey": value.opportunity_key, "catalystStage": value.catalyst_stage,
            "category": value.category, "comparison": dict(value.comparison),
            "evidenceRefs": [dict(item) for item in value.evidence_refs],
            "sourceMarker": value.source_marker, "relatedOpportunityId": value.related_opportunity_id}
    # Omit the additive field when absent so historical batch retries retain their
    # existing immutable input hash.
    if value.display_rank is not None:
        payload["displayRank"] = value.display_rank
    return payload


_HISTORICAL_OUTCOMES = {"success", "flat", "failure", "unclassified"}
_HISTORICAL_COVERAGE_STATES = {"complete", "partial", "unavailable"}
_HISTORICAL_REQUESTED_OUTCOMES = ("success", "flat", "failure")


def _historical_ref_keys(conn, refs: Any, *, field: str, require_nonempty: bool) -> tuple[tuple[str, int], ...]:
    """Validate frozen document-version references in a published historical context."""
    if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence) or (require_nonempty and not refs):
        raise ValueError(f"{field} 必须包含可追溯资料")
    keys: list[tuple[str, int]] = []
    for ref in refs:
        if (not isinstance(ref, Mapping) or not isinstance(ref.get("documentId"), str) or not ref["documentId"].strip()
                or isinstance(ref.get("revision"), bool) or not isinstance(ref.get("revision"), int) or ref["revision"] < 1):
            raise ValueError(f"{field} 必须精确引用 documentId 与 revision")
        key = (ref["documentId"], ref["revision"])
        if key in keys:
            raise ValueError(f"{field} 不可重复引用同一资料版本")
        if conn.execute("SELECT 1 FROM k10_source_document_versions WHERE document_id=? AND revision=?", key).fetchone() is None:
            raise K10Conflict(f"{field} 引用了未保存的资料版本")
        keys.append(key)
    return tuple(keys)


def _validate_historical_context(conn, comparison: Mapping[str, Any]) -> None:
    """Reject malformed frozen history before a publication transaction becomes visible.

    V2 comparison snapshots legitimately have neither history field.  Once either Schema 3
    field is supplied, however, it is a complete frozen context rather than best-effort JSON.
    """
    has_cases = "historicalCases" in comparison
    has_coverage = "historicalCoverage" in comparison
    if not has_cases and not has_coverage:
        return
    if not has_cases or not has_coverage:
        raise ValueError("历史案例与覆盖面必须成对冻结")
    cases, coverage = comparison["historicalCases"], comparison["historicalCoverage"]
    if not isinstance(cases, list) or not isinstance(coverage, Mapping):
        raise ValueError("历史案例上下文结构无效")
    required_coverage = {"state", "requestedOutcomes", "presentOutcomes", "missingOutcomes", "reason", "sourceRefs"}
    if set(coverage) != required_coverage or coverage.get("state") not in _HISTORICAL_COVERAGE_STATES:
        raise ValueError("历史案例覆盖面无效")
    requested, present, missing = (coverage[name] for name in ("requestedOutcomes", "presentOutcomes", "missingOutcomes"))
    if (not all(isinstance(value, list) for value in (requested, present, missing))
            or tuple(requested) != _HISTORICAL_REQUESTED_OUTCOMES
            or any(value not in _HISTORICAL_REQUESTED_OUTCOMES for value in [*present, *missing])
            or len(set(present)) != len(present) or len(set(missing)) != len(missing)
            or set(present) & set(missing) or set(present) | set(missing) != set(requested)
            or not isinstance(coverage["reason"], str) or not coverage["reason"].strip()):
        raise ValueError("历史案例覆盖结果不完整")
    _historical_ref_keys(conn, coverage["sourceRefs"], field="历史案例 coverage.sourceRefs", require_nonempty=False)
    case_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError("历史案例必须是对象")
        case_id = case.get("caseId")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in case_ids:
            raise ValueError("历史案例身份无效")
        case_ids.add(case_id)
        if (case.get("outcome") not in _HISTORICAL_OUTCOMES
                or not isinstance(case.get("summary"), str) or not case["summary"].strip()
                or not isinstance(case.get("marketFacts"), list)):
            raise ValueError("历史案例缺少结果、摘要或行情事实")
        try:
            observed_at = datetime.fromisoformat(str(case.get("observedAt")))
        except ValueError as exc:
            raise ValueError("历史案例 observedAt 无效") from exc
        if observed_at.tzinfo is None:
            raise ValueError("历史案例 observedAt 必须带时区")
        _historical_ref_keys(conn, case.get("sourceRefs"), field="历史案例 sourceRefs", require_nonempty=True)
        if "sourceBasedDescription" in case and (not isinstance(case["sourceBasedDescription"], str) or not case["sourceBasedDescription"].strip()):
            raise ValueError("历史案例 sourceBasedDescription 无效")


def _validate_event_comparison_inputs(values: Sequence["OpportunityPublicationInput"]) -> None:
    """Enforce one event-level ranking decision before any sample is written."""
    grouped: dict[tuple[str, int], list["OpportunityPublicationInput"]] = {}
    for item in values:
        grouped.setdefault((item.event_id, item.event_revision), []).append(item)
    for _event, peers in grouped.items():
        primary = [item for item in peers if item.category == "primary"]
        if len(primary) > 1:
            raise ValueError("同一事件修订至多一个 primary")
        by_rank: dict[int, list["OpportunityPublicationInput"]] = {}
        for item in peers:
            comparison = item.comparison if isinstance(item.comparison, Mapping) else {}
            rank = (comparison.get("eventRank") if comparison.get("rankNamespace") == "event"
                    else comparison.get("rank"))
            if rank is None:
                continue
            if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
                raise ValueError("发布样本 rank 必须是正整数或 null")
            by_rank.setdefault(rank, []).append(item)
        for rank, tied in by_rank.items():
            if len(tied) > 1 and any(item.category != "tied" for item in tied):
                raise ValueError("同一事件修订的同 rank 仅允许 tied")


def _validate_research_publication_bridge(
    conn, *, comparison: Mapping[str, Any], evidence_refs: Sequence[Mapping[str, Any]], scan_id: str, event_id: str,
) -> None:
    """Validate the additive B39 disclosure/provenance fields on a publication.

    Schema 6 publications legitimately have neither field.  When a research
    bridge is supplied, however, its disclosure cannot be omitted or detached
    from a real immutable snapshot revision.
    """
    snapshot_id = comparison.get("researchSnapshotId")
    research_revision = comparison.get("researchRevision")
    has_bridge = snapshot_id is not None or research_revision is not None
    differences = comparison.get("differences")
    disclosure_payload = differences.get("evidenceDisclosure") if isinstance(differences, Mapping) else None
    bound = conn.execute("SELECT task_id FROM k10_scan_execution_bindings WHERE scan_id=?", (scan_id,)).fetchone()
    researched_event = False
    if bound is not None:
        researched_event = conn.execute(
            "SELECT 1 FROM k10_research_snapshot_revisions WHERE task_id=? AND event_id=? LIMIT 1",
            (bound[0], event_id),
        ).fetchone() is not None
    if has_bridge:
        if not isinstance(snapshot_id, str) or not snapshot_id.strip() or isinstance(research_revision, bool) or not isinstance(research_revision, int) or research_revision < 1:
            raise ValueError("研究快照桥接必须精确包含 researchSnapshotId 与 researchRevision")
        if disclosure_payload is None:
            raise ValueError("研究快照桥接不得省略 evidenceDisclosure")
        snapshot = conn.execute(
            "SELECT task_id FROM k10_research_snapshot_revisions WHERE snapshot_id=? AND revision=?",
            (snapshot_id, research_revision),
        ).fetchone()
        if snapshot is None:
            raise K10Conflict("研究快照桥接引用不存在的不可变修订")
        if bound is not None and snapshot[0] != bound[0]:
            raise K10Conflict("研究快照桥接不属于当前扫描任务")
    if disclosure_payload is None:
        if researched_event:
            raise ValueError("研究正式发布不得省略 evidenceDisclosure")
        return
    try:
        disclosure = EvidenceDisclosure.from_dict(disclosure_payload).to_dict()
    except ResearchContractError as exc:
        raise ValueError("正式推荐 evidenceDisclosure 无效") from exc
    origin = disclosure["originEvidenceRef"]
    if origin is not None:
        key = (origin["documentId"], origin["revision"])
        available = {(ref.get("documentId"), ref.get("revision")) for ref in evidence_refs if isinstance(ref, Mapping)}
        if key not in available:
            raise ValueError("evidenceDisclosure.originEvidenceRef 必须属于正式推荐精确证据引用")
        if conn.execute("SELECT 1 FROM k10_source_document_versions WHERE document_id=? AND revision=?", key).fetchone() is None:
            raise K10Conflict("evidenceDisclosure.originEvidenceRef 引用未保存资料版本")


def publish_opportunities(*, batch_id: str, scan_id: str, publication_kind: str,
                          inputs: Sequence["OpportunityPublicationInput"], db_path: Path,
                          clock: "Callable[[], datetime]") -> "PublicationBatch":
    """Atomically expose a completed recommendation batch and every included sample.

    The timestamp is sampled while the SQLite write transaction is held.  A caller cannot make
    a batch visible with a scan-start timestamp, and a crash leaves either the whole batch or no
    publication records at all.
    """
    from .lifecycle import expected_d1_for_scan_cutoff, fixed_window_for_publication, windows_overlap
    from .types import OpportunityPublicationInput, PublicationBatch
    if publication_kind not in {"evening", "morning"}:
        raise ValueError("publication_kind 必须是 evening 或 morning")
    values = tuple(inputs)
    if any(not isinstance(item, OpportunityPublicationInput) for item in values):
        raise TypeError("inputs 必须是 OpportunityPublicationInput")
    keys = [item.opportunity_key for item in values]
    candidates = [item.candidate_id for item in values]
    if len(keys) != len(set(keys)) or len(candidates) != len(set(candidates)):
        raise K10Conflict("同一发布批次的机会键和候选必须唯一")
    if publication_kind == "evening" and len({item.company_code for item in values}) > 30:
        raise K10Conflict("晚间正式推荐最多 30 家公司")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        input_sha256 = _hash({"scanId": scan_id, "publicationKind": publication_kind,
                              "inputs": [_publication_input_payload(item) for item in values]})
        existing = conn.execute("SELECT scan_id,publication_kind,available_at,input_sha256 FROM k10_publication_batches WHERE batch_id=?", (batch_id,)).fetchone()
        if existing is not None:
            if tuple(existing[:2]) != (scan_id, publication_kind) or existing[3] != input_sha256:
                raise K10Conflict("发布批次 ID 已绑定到不同冻结输入")
            rows = conn.execute("SELECT candidate_id FROM k10_publication_samples WHERE batch_id=?", (batch_id,)).fetchall()
            return PublicationBatch(batch_id, scan_id, publication_kind, str(existing[2]), len(rows))
        if conn.execute("SELECT 1 FROM k10_publication_batches WHERE scan_id=? AND publication_kind=?", (scan_id, publication_kind)).fetchone() is not None:
            raise K10Conflict("同一扫描窗口只能有一个可见发布批次")
        scan = conn.execute("SELECT cutoff_at FROM k10_scans WHERE scan_id=? AND status IN ('completed','partial')", (scan_id,)).fetchone()
        if scan is None:
            raise K10Conflict("只能发布已完成或部分覆盖的扫描")
        try:
            scan_cutoff_at = datetime.fromisoformat(str(scan[0]))
        except ValueError as exc:
            raise K10Conflict("扫描截止时间无效") from exc
        expected_d1 = expected_d1_for_scan_cutoff(cutoff_at=scan_cutoff_at, db_path=db_path)
        for item in values:
            if not all(isinstance(x, str) and x.strip() for x in (item.candidate_id, item.company_code, item.event_id, item.opportunity_key, item.catalyst_stage, item.category, item.source_marker)) or item.event_revision < 1:
                raise ValueError("发布机会缺少稳定身份或事件修订")
            if item.category not in {"primary", "alternative", "tied"}:
                raise ValueError("正式推荐 category 必须是 primary、alternative 或 tied")
            if item.source_marker not in {"evening", "morning"}:
                raise ValueError("source_marker 必须是 evening 或 morning")
            if item.source_marker != publication_kind:
                raise ValueError("source_marker 必须与首发批次一致")
            comparison = item.comparison
            required_comparison = {"summary", "differences", "evidenceRefs", "rank", "classification"}
            allowed_comparison = required_comparison | {
                "marketContext", "historicalCases", "historicalCoverage", "rankNamespace", "eventRank",
                "researchSnapshotId", "researchRevision",
            }
            if not isinstance(comparison, Mapping) or not required_comparison <= set(comparison) or not set(comparison) <= allowed_comparison:
                raise ValueError("正式推荐缺少完整比较快照")
            differences = comparison["differences"]
            classification = comparison["classification"]
            required_difference = {"role", "priorityReason", "gap", "rankChangeConditions", "twoDayReason"}
            allowed_difference = required_difference | {"evidenceDisclosure"}
            required_classification = {"kind", "opportunityKey", "reason", "newFacts", "changedJudgment", "twoDayReason", "relatedOpportunityId"}
            if (not isinstance(comparison["summary"], str) or not comparison["summary"].strip() or
                not isinstance(differences, Mapping) or not required_difference <= set(differences) or not set(differences) <= allowed_difference or
                not isinstance(classification, Mapping) or set(classification) != required_classification or
                differences.get("role") != item.category or classification.get("opportunityKey") != item.opportunity_key or
                classification.get("relatedOpportunityId") != item.related_opportunity_id or
                classification.get("kind") not in {"initial", "material_stage", "independent"} or
                not all(isinstance(differences.get(name), str) and differences[name].strip() for name in required_difference) or
                not all(isinstance(classification.get(name), str) and classification[name].strip() for name in ("reason", "newFacts", "twoDayReason")) or
                (classification.get("kind") == "material_stage" and (not item.related_opportunity_id or not isinstance(classification.get("changedJudgment"), str) or not classification["changedJudgment"].strip())) or
                (classification.get("kind") != "material_stage" and classification.get("changedJudgment") is not None and not isinstance(classification.get("changedJudgment"), str)) or
                ("marketContext" in comparison and not isinstance(comparison["marketContext"], Mapping)) or
                (("rankNamespace" in comparison or "eventRank" in comparison) and
                 (comparison.get("rankNamespace") != "event" or comparison.get("eventRank") != comparison.get("rank"))) or
                (item.display_rank is not None and (isinstance(item.display_rank, bool) or not isinstance(item.display_rank, int) or item.display_rank < 1)) or
                _json(comparison["evidenceRefs"]) != _json(item.evidence_refs)):
                raise ValueError("正式推荐比较、分类或精确证据引用不一致")
            _validate_research_publication_bridge(
                conn, comparison=comparison, evidence_refs=item.evidence_refs, scan_id=scan_id, event_id=item.event_id,
            )
            _validate_historical_context(conn, comparison)
            candidate = conn.execute("SELECT event_id,event_revision,company_code FROM k10_candidates WHERE candidate_id=?", (item.candidate_id,)).fetchone()
            if candidate is None or tuple(candidate) != (item.event_id, item.event_revision, item.company_code):
                raise K10Conflict("发布样本与候选快照不一致")
            if item.related_opportunity_id is not None and classification.get("kind") == "material_stage":
                related = conn.execute("SELECT company_code FROM k10_opportunities WHERE opportunity_id=?", (item.related_opportunity_id,)).fetchone()
                if related is None or related[0] != item.company_code:
                    raise K10Conflict("实质新阶段必须关联同公司的既有机会")
            old = conn.execute("SELECT opportunity_id FROM k10_opportunities WHERE opportunity_key=?", (item.opportunity_key,)).fetchone()
            if old is not None:
                raise K10Conflict("既有机会不得作为新首发样本重复发布")
        _validate_event_comparison_inputs(values)
        now = clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("publication clock 必须返回带时区 datetime")
        available_at = _utc_text(now)
        fixed = fixed_window_for_publication(available_at=now, publication_kind=publication_kind, db_path=db_path,
                                             expected_d1_trade_date=expected_d1)
        conn.execute("INSERT INTO k10_publication_batches(batch_id,scan_id,publication_kind,available_at,input_sha256,created_at) VALUES(?,?,?,?,?,?)",
                     (batch_id, scan_id, publication_kind, available_at, input_sha256, available_at))
        windows: dict[str, str] = {}
        for company in sorted({item.company_code for item in values}):
            prior = conn.execute(
                "SELECT w.company_window_id,w.d1_trade_date,w.d2_trade_date FROM k10_company_windows w "
                "JOIN k10_publication_batches b ON b.batch_id=w.first_batch_id "
                "WHERE w.company_code=? ORDER BY b.available_at,w.company_window_id", (company,)
            ).fetchall()
            overlap = next((str(row[0]) for row in prior if windows_overlap(d1_a=fixed.d1_trade_date, d2_a=fixed.d2_trade_date,
                                                                              d1_b=str(row[1]), d2_b=str(row[2]))), None)
            window_id = _stable_id("window", company, batch_id)
            conn.execute("INSERT INTO k10_company_windows(company_window_id,company_code,first_batch_id,d0_trade_date,d1_trade_date,d2_trade_date,d1_selection_at,d2_close_at,sample_class,overlaps_window_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (window_id, company, batch_id, fixed.d0_trade_date, fixed.d1_trade_date, fixed.d2_trade_date,
                          fixed.d1_selection_at, fixed.d2_close_at, "overlap" if overlap else "primary", overlap, available_at))
            windows[company] = window_id
        for item in values:
            opportunity_id = _stable_id("opportunity", item.opportunity_key)
            window_id = windows[item.company_code]
            conn.execute("INSERT INTO k10_opportunities(opportunity_id,opportunity_key,company_code,catalyst_event_id,catalyst_event_revision,catalyst_stage,related_opportunity_id,first_batch_id,company_window_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (opportunity_id, item.opportunity_key, item.company_code, item.event_id, item.event_revision,
                          item.catalyst_stage, item.related_opportunity_id, batch_id, window_id, available_at))
            sample_id = _stable_id("sample", batch_id, item.candidate_id)
            rank = item.display_rank if item.display_rank is not None else item.comparison.get("rank")
            conn.execute("INSERT INTO k10_publication_samples(sample_id,batch_id,candidate_id,opportunity_id,company_window_id,event_id,event_revision,company_code,category,source_marker,comparison_json,evidence_refs_json,rank,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (sample_id, batch_id, item.candidate_id, opportunity_id, window_id, item.event_id, item.event_revision,
                          item.company_code, item.category, item.source_marker, _json(item.comparison), _json(item.evidence_refs), rank, available_at))
            lifecycle_id = _stable_id("lifecycle", opportunity_id, "published", batch_id)
            conn.execute("INSERT INTO k10_opportunity_lifecycle_events(lifecycle_event_id,opportunity_id,kind,reason,source_refs_json,content_json,occurred_at,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (lifecycle_id, opportunity_id, "published", None, _json(item.evidence_refs),
                          _json({"batchId": batch_id, "candidateId": item.candidate_id,
                                 "sourceMarker": item.source_marker, "latePublication": fixed.delayed}), available_at, available_at))
        committed = clock()
        if not isinstance(committed, datetime) or committed.tzinfo is None:
            raise ValueError("publication clock 必须返回带时区 datetime")
        committed_fixed = fixed_window_for_publication(available_at=committed, publication_kind=publication_kind, db_path=db_path,
                                                        expected_d1_trade_date=expected_d1)
        if committed_fixed != fixed:
            raise K10Conflict("发布跨越固定 D1 边界；已回滚，须按实际可见时间重试")
        final_at = _utc_text(committed)
        if final_at != available_at:
            conn.execute("UPDATE k10_publication_batches SET available_at=?,created_at=? WHERE batch_id=?", (final_at, final_at, batch_id))
            conn.execute("UPDATE k10_company_windows SET created_at=? WHERE first_batch_id=?", (final_at, batch_id))
            conn.execute("UPDATE k10_opportunities SET created_at=? WHERE first_batch_id=?", (final_at, batch_id))
            conn.execute("UPDATE k10_publication_samples SET created_at=? WHERE batch_id=?", (final_at, batch_id))
            conn.execute("UPDATE k10_opportunity_lifecycle_events SET occurred_at=?,created_at=? WHERE kind='published' AND opportunity_id IN (SELECT opportunity_id FROM k10_opportunities WHERE first_batch_id=?)", (final_at, final_at, batch_id))
            available_at = final_at
    return PublicationBatch(batch_id, scan_id, publication_kind, available_at, len(values))


def append_opportunity_update(*, lifecycle_event_id: str, opportunity_id: str, kind: str, reason: str | None,
                              source_refs: Sequence[Mapping[str, Any]], content: Mapping[str, Any], occurred_at: str,
                              created_at: str, db_path: Path) -> None:
    if kind not in {"evidence_update", "risk", "withdrawal", "expired"}:
        raise ValueError("机会更新 kind 无效")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        expected = (opportunity_id, kind, reason, _json(source_refs), _json(content), occurred_at, created_at)
        old = conn.execute("SELECT opportunity_id,kind,reason,source_refs_json,content_json,occurred_at,created_at FROM k10_opportunity_lifecycle_events WHERE lifecycle_event_id=?", (lifecycle_event_id,)).fetchone()
        if old is not None:
            if tuple(old) != expected:
                raise K10Conflict("机会生命周期 ID 已存在但内容不同")
            return
        if conn.execute("SELECT 1 FROM k10_opportunities WHERE opportunity_id=?", (opportunity_id,)).fetchone() is None:
            raise K10Conflict("机会不存在")
        conn.execute("INSERT INTO k10_opportunity_lifecycle_events(lifecycle_event_id,opportunity_id,kind,reason,source_refs_json,content_json,occurred_at,created_at) VALUES(?,?,?,?,?,?,?,?)",
                     (lifecycle_event_id, *expected))


def withdraw_opportunity(*, opportunity_id: str, reason: str, source_refs: Sequence[Mapping[str, Any]],
                         withdrawn_at: str, db_path: Path) -> str:
    event_id = _stable_id("lifecycle", opportunity_id, "withdrawal", withdrawn_at, _hash({"reason": reason, "refs": list(source_refs)}))
    append_opportunity_update(lifecycle_event_id=event_id, opportunity_id=opportunity_id, kind="withdrawal", reason=reason,
                              source_refs=source_refs, content={}, occurred_at=withdrawn_at, created_at=withdrawn_at, db_path=db_path)
    return event_id


def freeze_company_window_selection(*, company_window_id: str, frozen_at: str, db_path: Path) -> dict[str, Any]:
    """Freeze one company-window choice from the last explicit pre-09:30 action.

    Actions at 09:30 or later belong to later user history and cannot revise the already fixed
    selection cohort. Multiple catalyst cards therefore share exactly one company decision.
    """
    try:
        freeze_time = datetime.fromisoformat(frozen_at)
    except ValueError as exc:
        raise ValueError("冻结时间必须是带时区 ISO 时间") from exc
    if freeze_time.tzinfo is None:
        raise ValueError("冻结时间必须带时区")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        old = conn.execute("SELECT state,action_ids_json,frozen_at FROM k10_company_window_selection_snapshots WHERE company_window_id=?", (company_window_id,)).fetchone()
        if old is not None:
            return {"companyWindowId": company_window_id, "state": old[0], "actionIds": json.loads(old[1]), "frozenAt": old[2]}
        window = conn.execute("SELECT d1_selection_at FROM k10_company_windows WHERE company_window_id=?", (company_window_id,)).fetchone()
        if window is None:
            raise K10Conflict("公司窗口不存在")
        selection_at = datetime.fromisoformat(window[0])
        if freeze_time < selection_at:
            raise K10Conflict("不得在固定 D1 09:30 前冻结选择")
        rows = _company_window_action_rows(conn, company_window_id=company_window_id)
        prior: list[tuple[str, str, datetime, int]] = []
        for action_id, action, created_at, rowid in rows:
            try:
                occurred = datetime.fromisoformat(str(created_at))
            except ValueError:
                continue
            if occurred.tzinfo is not None and occurred < selection_at:
                prior.append((str(action_id), str(action), occurred, int(rowid)))
        # UTC-normalized timestamps can differ lexically; order actual instants then append row.
        prior.sort(key=lambda item: (item[2].astimezone(timezone.utc), item[3]))
        last = prior[-1] if prior else None
        state = "selected" if last and last[1] == "observe" else ("skipped" if last and last[1] == "skip" else "unhandled")
        action_ids = [] if last is None else [last[0]]
        conn.execute("INSERT INTO k10_company_window_selection_snapshots(company_window_id,state,action_ids_json,frozen_at,created_at) VALUES(?,?,?,?,?)",
                     (company_window_id, state, _json(action_ids), frozen_at, frozen_at))
    return {"companyWindowId": company_window_id, "state": state, "actionIds": action_ids, "frozenAt": frozen_at}


def _company_window_action_rows(conn, *, company_window_id: str):
    """Return V1.4 window commands plus legacy direct candidate commands once each."""
    return conn.execute(
        "SELECT action_id,action,created_at,rowid FROM k10_company_window_actions WHERE company_window_id=? "
        "UNION ALL "
        "SELECT a.action_id,a.action,a.created_at,a.rowid FROM k10_candidate_actions a "
        "JOIN k10_publication_samples s ON s.candidate_id=a.candidate_id "
        "LEFT JOIN k10_company_window_actions w ON w.candidate_action_id=a.action_id "
        "WHERE s.company_window_id=? AND w.action_id IS NULL",
        (company_window_id, company_window_id),
    ).fetchall()


def _current_company_window_action(conn, *, company_window_id: str) -> tuple[str, str, datetime, int] | None:
    """Project the latest action from the unified window/candidate append-only ledger."""
    actions: list[tuple[str, str, datetime, int]] = []
    for action_id, action, created_at, rowid in _company_window_action_rows(conn, company_window_id=company_window_id):
        try:
            occurred = datetime.fromisoformat(str(created_at))
        except ValueError:
            continue
        if occurred.tzinfo is not None:
            actions.append((str(action_id), str(action), occurred, int(rowid)))
    # Compare actual instants.  Row IDs deterministically resolve simultaneous timestamps.
    actions.sort(key=lambda item: (item[2].astimezone(timezone.utc), item[3]))
    return actions[-1] if actions else None


def get_company_window_selection(*, company_window_id: str, db_path: Path) -> dict[str, Any] | None:
    """Read the current and frozen company-window selection without writing a snapshot.

    ``currentState`` is the latest appended command; ``snapshotState`` is immutable after D1
    freeze and deliberately does not change when a user acts after the open.
    """
    with read_connection(db_path) as conn:
        require_schema(conn)
        try:
            representative = _window_representative_candidate(conn, company_window_id=company_window_id)
        except K10Conflict:
            return None
        boundary = conn.execute(
            "SELECT d1_selection_at FROM k10_company_windows WHERE company_window_id=?", (company_window_id,)
        ).fetchone()
        if boundary is None:
            return None
        try:
            selection_at = datetime.fromisoformat(str(boundary[0]))
        except ValueError as exc:
            raise K10Conflict("公司窗口 D1 冻结时间无效") from exc
        if selection_at.tzinfo is None:
            raise K10Conflict("公司窗口 D1 冻结时间缺少时区")
        snapshot = conn.execute(
            "SELECT state,action_ids_json,frozen_at FROM k10_company_window_selection_snapshots WHERE company_window_id=?",
            (company_window_id,),
        ).fetchone()
        observation = conn.execute(
            "SELECT observation_id,task_id FROM k10_company_window_observations WHERE company_window_id=?",
            (company_window_id,),
        ).fetchone()
        last = _current_company_window_action(conn, company_window_id=company_window_id)
    current = "kept" if last and last[1] == "observe" else ("skipped" if last and last[1] == "skip" else "unhandled")
    snapshot_state = None if snapshot is None else {"selected": "kept", "skipped": "skipped", "unhandled": "unhandled"}[str(snapshot[0])]
    return {
        "companyWindowId": company_window_id,
        "currentState": current,
        "lastActionId": None if last is None else last[0],
        "lastActionAt": None if last is None else last[2].isoformat(timespec="seconds"),
        "postFreeze": bool(last and last[2].astimezone(timezone.utc) >= selection_at.astimezone(timezone.utc)),
        "representativeCandidateId": representative,
        "observationId": None if observation is None else str(observation[0]),
        "taskId": None if observation is None else str(observation[1]),
        "snapshotState": snapshot_state,
        "snapshotActionIds": [] if snapshot is None else json.loads(snapshot[1]),
        "frozenAt": None if snapshot is None else str(snapshot[2]),
    }

def append_market_day_fact(*, company_code: str, trade_date: str, availability: str,
                           open_price: float | None, high_price: float | None, low_price: float | None,
                           close_price: float | None, pre_close: float | None, limit_up_price: float | None,
                           close_limit_up: bool | None, touched_limit_up: bool | None,
                           source_refs: Sequence[Mapping[str, Any]], obtained_at: str, created_at: str,
                           db_path: Path, adj_factor: float | None = None,
                           metadata: Mapping[str, Any] | None = None) -> int:
    if availability not in {"available", "suspended", "data_gap", "anomaly"}:
        raise ValueError("行情事实 availability 无效")
    payload = (availability, open_price, high_price, low_price, close_price, pre_close, limit_up_price,
               None if close_limit_up is None else int(close_limit_up), None if touched_limit_up is None else int(touched_limit_up),
               adj_factor, _json(metadata or {}), _json(source_refs), obtained_at)
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        old = conn.execute("SELECT revision,availability,open,high,low,close,pre_close,limit_up_price,close_limit_up,touched_limit_up,adj_factor,metadata_json,source_refs_json,obtained_at FROM k10_market_day_fact_revisions WHERE company_code=? AND trade_date=? ORDER BY revision DESC LIMIT 1", (company_code, trade_date)).fetchone()
        if old is not None and tuple(old[1:]) == payload:
            return int(old[0])
        revision = 1 if old is None else int(old[0]) + 1
        conn.execute("INSERT INTO k10_market_day_fact_revisions(company_code,trade_date,revision,availability,open,high,low,close,pre_close,limit_up_price,close_limit_up,touched_limit_up,adj_factor,metadata_json,source_refs_json,obtained_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (company_code, trade_date, revision, *payload, created_at))
    return revision


def append_company_window_evaluation(*, company_window_id: str, state: str, fact_refs: Sequence[Mapping[str, Any]],
                                     result: Mapping[str, Any], evaluated_at: str, created_at: str, db_path: Path) -> int:
    if state not in {"pending", "due", "completed", "incomplete"}:
        raise ValueError("评价状态无效")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
        old = conn.execute("SELECT revision,state,fact_refs_json,result_json,evaluated_at FROM k10_company_window_evaluation_revisions WHERE company_window_id=? ORDER BY revision DESC LIMIT 1", (company_window_id,)).fetchone()
        payload = (state, _json(fact_refs), _json(result), evaluated_at)
        if old is not None and tuple(old[1:]) == payload:
            return int(old[0])
        revision = 1 if old is None else int(old[0]) + 1
        conn.execute("INSERT INTO k10_company_window_evaluation_revisions(company_window_id,revision,state,fact_refs_json,result_json,evaluated_at,created_at) VALUES(?,?,?,?,?,?,?)",
                     (company_window_id, revision, *payload, created_at))
    return revision


def _opportunity_state(conn, opportunity_id: str, *, as_of: datetime | None = None) -> str:
    # Lifecycle updates are append-only evidence.  A later ordinary update must never turn a
    # withdrawn or expired opportunity back into an active one merely because it is newest.
    instant = datetime.now(timezone.utc) if as_of is None else as_of
    if instant.tzinfo is None:
        raise ValueError("机会状态查询 as_of 必须带时区")
    terminal = conn.execute(
        "SELECT kind FROM k10_opportunity_lifecycle_events WHERE opportunity_id=? "
        "AND kind IN ('withdrawal','expired') AND (? IS NULL OR (julianday(occurred_at)<=julianday(?) "
        "AND julianday(created_at)<=julianday(?))) ORDER BY CASE kind WHEN 'withdrawal' THEN 0 ELSE 1 END,rowid DESC LIMIT 1",
        (opportunity_id, None if as_of is None else instant.isoformat(), instant.isoformat(), instant.isoformat()),
    ).fetchone()
    if terminal is not None:
        return "withdrawn" if terminal[0] == "withdrawal" else "expired"
    close = conn.execute(
        "SELECT w.d2_close_at FROM k10_opportunities o JOIN k10_company_windows w ON w.company_window_id=o.company_window_id "
        "WHERE o.opportunity_id=?", (opportunity_id,),
    ).fetchone()
    if close is not None:
        try:
            if instant.astimezone(timezone.utc) >= datetime.fromisoformat(str(close[0])).astimezone(timezone.utc):
                # GET remains read-only. The evaluator can append an explicit ``expired`` event
                # later, but a delayed scheduler must never make a completed D2 look active.
                return "expired"
        except ValueError:
            raise K10Conflict("公司窗口 D2 收盘时间无效")
    return "active"


def list_publication_batches(*, db_path: Path) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        rows = conn.execute("SELECT batch_id,scan_id,publication_kind,available_at,created_at FROM k10_publication_batches ORDER BY available_at,batch_id").fetchall()
    return [{"batchId": r[0], "scanId": r[1], "publicationKind": r[2], "availableAt": r[3], "createdAt": r[4]} for r in rows]


def get_publication_batch(*, batch_id: str, db_path: Path) -> dict[str, Any] | None:
    return next((item for item in list_publication_batches(db_path=db_path) if item["batchId"] == batch_id), None)


def list_company_windows(*, company_code: str | None = None, db_path: Path) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        query = "SELECT w.company_window_id,w.company_code,w.first_batch_id,w.d0_trade_date,w.d1_trade_date,w.d2_trade_date,w.d1_selection_at,w.d2_close_at,w.sample_class,w.overlaps_window_id,s.state,s.action_ids_json,s.frozen_at,b.available_at,MIN(p.rank) FROM k10_company_windows w JOIN k10_publication_batches b ON b.batch_id=w.first_batch_id LEFT JOIN k10_company_window_selection_snapshots s ON s.company_window_id=w.company_window_id LEFT JOIN k10_publication_samples p ON p.company_window_id=w.company_window_id"
        args: tuple[Any, ...] = ()
        if company_code is not None:
            query += " WHERE w.company_code=?"; args = (company_code,)
        query += " GROUP BY w.company_window_id ORDER BY b.available_at DESC,CASE WHEN MIN(p.rank) IS NULL THEN 1 ELSE 0 END,MIN(p.rank),w.company_window_id"
        rows = conn.execute(query, args).fetchall()
    return [{"companyWindowId": r[0], "companyCode": r[1], "firstBatchId": r[2], "d0TradeDate": r[3], "d1TradeDate": r[4], "d2TradeDate": r[5], "d1SelectionAt": r[6], "d2CloseAt": r[7], "sampleClass": r[8], "overlapsWindowId": r[9], "selection": None if r[10] is None else {"state": r[10], "actionIds": json.loads(r[11]), "frozenAt": r[12]}, "availableAt": r[13], "displayRank": r[14]} for r in rows]


def list_opportunities(*, company_code: str | None = None, state: str | None = None, batch_id: str | None = None,
                       as_of: datetime | None = None, db_path: Path) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        query = "SELECT o.opportunity_id,o.opportunity_key,o.company_code,o.catalyst_event_id,o.catalyst_event_revision,o.catalyst_stage,o.related_opportunity_id,o.first_batch_id,o.company_window_id,o.created_at,b.available_at,w.d0_trade_date,w.d1_trade_date,w.d2_trade_date,w.sample_class,s.source_marker,p.content_json,s.rank FROM k10_opportunities o JOIN k10_publication_batches b ON b.batch_id=o.first_batch_id JOIN k10_company_windows w ON w.company_window_id=o.company_window_id JOIN k10_publication_samples s ON s.opportunity_id=o.opportunity_id JOIN k10_opportunity_lifecycle_events p ON p.opportunity_id=o.opportunity_id AND p.kind='published'"
        clauses=[]; args=[]
        if company_code is not None: clauses.append("o.company_code=?"); args.append(company_code)
        if batch_id is not None: clauses.append("o.first_batch_id=?"); args.append(batch_id)
        if clauses: query += " WHERE " + " AND ".join(clauses)
        rows = conn.execute(query + " ORDER BY b.available_at DESC,CASE WHEN s.rank IS NULL THEN 1 ELSE 0 END,s.rank,o.opportunity_id", tuple(args)).fetchall()
        events = {r[0]: _opportunity_state(conn, str(r[0]), as_of=as_of) for r in rows}
    items=[{"opportunityId":r[0],"opportunityKey":r[1],"companyCode":r[2],"eventId":r[3],"eventRevision":r[4],"catalystStage":r[5],"relatedOpportunityId":r[6],"firstBatchId":r[7],"companyWindowId":r[8],"createdAt":r[9],"availableAt":r[10],"d0TradeDate":r[11],"d1TradeDate":r[12],"d2TradeDate":r[13],"sampleClass":r[14],"sourceMarker":r[15],"latePublication":bool(json.loads(r[16]).get("latePublication", False)),"displayRank":r[17],"state":events[r[0]]} for r in rows]
    return [item for item in items if state is None or item["state"] == state]


def get_opportunity(*, opportunity_id: str, as_of: datetime | None = None, db_path: Path) -> dict[str, Any] | None:
    return next((item for item in list_opportunities(as_of=as_of, db_path=db_path) if item["opportunityId"] == opportunity_id), None)


def list_morning_report_targets(*, as_of: datetime, db_path: Path, scan_id: str | None = None) -> list[dict[str, Any]]:
    """Enumerate every formally published target for a morning report; none are inferred away.

    This deliberately includes withdrawn and expired opportunities, and projects user choice
    from the same append-only company-window action ledger used elsewhere.
    """
    if as_of.tzinfo is None:
        raise ValueError("晨报目标查询 as_of 必须带时区")
    with read_connection(db_path) as conn:
        require_schema(conn)
        rows = conn.execute(
            "SELECT o.opportunity_id,o.company_window_id,s.candidate_id,o.company_code,w.d1_trade_date,w.d2_trade_date,"
            "s.evidence_refs_json,s.rank,b.available_at,b.scan_id FROM k10_opportunities o JOIN k10_publication_samples s ON s.opportunity_id=o.opportunity_id "
            "JOIN k10_company_windows w ON w.company_window_id=o.company_window_id JOIN k10_publication_batches b ON b.batch_id=o.first_batch_id "
            "ORDER BY b.available_at DESC,CASE WHEN s.rank IS NULL THEN 1 ELSE 0 END,s.rank,o.opportunity_id"
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            snapshot = conn.execute(
                "SELECT state,frozen_at FROM k10_company_window_selection_snapshots WHERE company_window_id=?", (row[1],)
            ).fetchone()
            if snapshot is not None:
                selection_state = {"selected": "kept", "skipped": "skipped", "unhandled": "unhandled"}[snapshot[0]]
                selection_frozen_at = snapshot[1]
            else:
                action = _current_company_window_action(conn, company_window_id=str(row[1]))
                selection_state = "kept" if action is not None and action[1] == "observe" else ("skipped" if action is not None and action[1] == "skip" else "unhandled")
                selection_frozen_at = None
            state = _opportunity_state(conn, str(row[0]), as_of=as_of)
            lifecycle = list_opportunity_lifecycle_events(opportunity_id=str(row[0]), db_path=db_path)
            items.append({"opportunityId": row[0], "companyWindowId": row[1], "candidateId": row[2], "companyCode": row[3],
                          "d1TradeDate": row[4], "d2TradeDate": row[5], "selectionState": selection_state,
                          "selectionFrozenAt": selection_frozen_at, "state": state,
                          "windowState": "expired" if state == "expired" else "active", "sourceRefs": json.loads(row[6]),
                          "displayRank": row[7], "availableAt": row[8], "isNew": scan_id is not None and row[9] == scan_id,
                          "lifecycle": lifecycle})
    return items


def list_publication_samples(*, batch_id: str, db_path: Path) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        rows=conn.execute("SELECT sample_id,candidate_id,opportunity_id,company_window_id,event_id,event_revision,company_code,category,source_marker,comparison_json,evidence_refs_json,rank,created_at FROM k10_publication_samples WHERE batch_id=? ORDER BY rank,sample_id", (batch_id,)).fetchall()
    return [{"sampleId":r[0],"candidateId":r[1],"opportunityId":r[2],"companyWindowId":r[3],"eventId":r[4],"eventRevision":r[5],"companyCode":r[6],"category":r[7],"sourceMarker":r[8],"comparison":json.loads(r[9]),"evidenceRefs":json.loads(r[10]),"rank":r[11],"createdAt":r[12]} for r in rows]


def market_day_fact_id(*, company_code: str, trade_date: str) -> str:
    """Stable identity for every append-only company/day fact revision sequence."""
    return _stable_id("market-fact", company_code, trade_date)


def list_market_day_facts(*, company_code: str, db_path: Path) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        rows=conn.execute("SELECT company_code,trade_date,revision,availability,open,high,low,close,pre_close,limit_up_price,close_limit_up,touched_limit_up,adj_factor,metadata_json,source_refs_json,obtained_at,created_at FROM k10_market_day_fact_revisions WHERE company_code=? ORDER BY trade_date,revision", (company_code,)).fetchall()
    return [{"factId":market_day_fact_id(company_code=str(r[0]),trade_date=str(r[1])),"companyCode":r[0],"tradeDate":r[1],"revision":r[2],"availability":r[3],"open":r[4],"high":r[5],"low":r[6],"close":r[7],"preClose":r[8],"limitUpPrice":r[9],"closeLimitUp":None if r[10] is None else bool(r[10]),"touchedLimitUp":None if r[11] is None else bool(r[11]),"adjFactor":r[12],"metadata":json.loads(r[13]),"sourceRefs":json.loads(r[14]),"obtainedAt":r[15],"createdAt":r[16]} for r in rows]


def latest_market_day_facts(*, company_code: str, trade_dates: Sequence[str] | None = None,
                            db_path: Path) -> list[dict[str, Any]]:
    """Read one latest append-only revision per requested company/day without DDL."""
    requested = None if trade_dates is None else set(trade_dates)
    latest: dict[str, dict[str, Any]] = {}
    for item in list_market_day_facts(company_code=company_code, db_path=db_path):
        if requested is not None and item["tradeDate"] not in requested:
            continue
        latest[str(item["tradeDate"])] = item
    return [latest[day] for day in sorted(latest)]


def list_opportunity_lifecycle_events(*, opportunity_id: str, db_path: Path) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        rows = conn.execute("SELECT lifecycle_event_id,kind,reason,source_refs_json,content_json,occurred_at,created_at FROM k10_opportunity_lifecycle_events WHERE opportunity_id=? ORDER BY occurred_at,rowid", (opportunity_id,)).fetchall()
    return [{"lifecycleEventId": r[0], "opportunityId": opportunity_id, "kind": r[1], "reason": r[2],
             "sourceRefs": json.loads(r[3]), "content": json.loads(r[4]), "occurredAt": r[5], "createdAt": r[6]} for r in rows]


def list_company_window_evaluations(*, company_window_id: str | None = None, db_path: Path) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        query = "SELECT e.company_window_id,e.revision,e.state,e.fact_refs_json,e.result_json,e.evaluated_at,e.created_at FROM k10_company_window_evaluation_revisions e JOIN (SELECT company_window_id,MAX(revision) revision FROM k10_company_window_evaluation_revisions"
        args: tuple[Any, ...] = ()
        if company_window_id is not None:
            query += " WHERE company_window_id=?"; args = (company_window_id,)
        query += " GROUP BY company_window_id) current ON current.company_window_id=e.company_window_id AND current.revision=e.revision ORDER BY e.company_window_id"
        rows = conn.execute(query, args).fetchall()
    return [{"companyWindowId":r[0],"revision":r[1],"state":r[2],"factRefs":json.loads(r[3]),"result":json.loads(r[4]),"evaluatedAt":r[5],"createdAt":r[6]} for r in rows]


__all__ = [
    "K10Conflict", "append_analysis_revision", "append_candidate_action", "append_company_mapping", "append_company_window_action",
    "append_company_window_evaluation", "append_document_version", "append_market_day_fact",
    "admit_article", "append_execution_config", "append_morning_update", "append_opportunity_update", "append_run_config", "append_source_watermark",
    "authorize_discovery_recovery",
    "append_title_triage_policy", "begin_external_attempt",
    "append_event_revision", "candidate_state", "claim_task_by_id", "claim_tasks", "create_candidate",
    "bind_scan_execution", "bind_task_execution", "completed_execution_items", "create_scan",
    "reject_completed_execution_checkpoint", "enqueue_task", "execution_progress_for_scan", "finalize_scan", "finish_task", "freeze_company_window_selection",
    "get_candidate", "get_company_window_selection", "get_observation", "get_opportunity", "get_opportunity_for_candidate", "get_publication_batch", "get_scan",
    "latest_document_version", "latest_event_revision", "latest_market_day_facts", "latest_source_watermark", "list_candidates",
    "list_company_windows", "list_company_window_evaluations", "list_market_day_facts", "list_observations", "list_opportunities", "market_day_fact_id",
    "list_opportunity_lifecycle_events", "list_publication_batches", "list_publication_samples", "list_scans", "list_source_document_versions",
    "load_analysis_revision", "load_candidate_context", "load_document_versions", "load_observation_context",
    "external_attempt_summary", "freeze_title_selection_manifest", "freeze_title_triage_manifest",
    "is_discovery_retired", "load_task_analysis_config", "observe_candidate", "observe_company_window", "publish_opportunities",
    "read_execution_config", "read_fact_cache", "read_run_config", "read_title_selection_manifest",
    "read_title_triage_items", "read_title_triage_manifest", "read_title_triage_policy",
    "record_article_outcome", "record_execution_checkpoint", "record_title_triage_item", "reopen_scan",
    "renew_task_lease", "retire_interrupted_discovery", "retry_task", "run_control_status",
    "schedule_task_retry", "set_run_control", "settle_external_attempt", "store_fact_cache",
    "task_execution_input", "task_execution_profile", "update_running_scan_coverage",
    "withdraw_opportunity",
]
