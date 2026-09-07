"""K10 的追加式 SQLite 存储。

本模块从不调用迁移。每个入口都要求明确 ``db_path``；读操作经 ``mode=ro`` 打开
既有 schema，写操作只在调用方已显式执行 ``initialize_schema`` 后运行。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from .schema import read_connection, require_schema, write_connection
from .types import CompanyWindowObservation, DocumentVersion, EventRevision, Observation, OpportunityPublicationInput, PublicationBatch, Task


class K10Conflict(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


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


def append_document_version(
    *, document_id: str, source_key: str, external_id: str, canonical_url: Optional[str],
    content_sha256: str, published_at: Optional[str], published_precision: str, fetched_at: str,
    original_text: Optional[str], excerpt: Optional[str], fetch_version: str,
    metadata: Mapping[str, Any], created_at: str, db_path: Path,
) -> DocumentVersion:
    """追加原始资料版本，以内容哈希幂等；同源 ID 绝不可改绑到另一 document ID。"""
    if published_precision not in {"exact", "date", "unknown"}:
        raise ValueError("published_precision 必须是 exact、date 或 unknown")
    with write_connection(db_path) as conn:
        _require_write_schema(conn)
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
            return DocumentVersion(document_id, int(old[0]), content_sha256)
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
    return DocumentVersion(document_id, revision, content_sha256)


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


def _opportunity_for_candidate_conn(conn, candidate_id: str) -> dict[str, Any] | None:
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
            "d2TradeDate": row[3], "state": _opportunity_state(conn, str(row[0]))}


def get_opportunity_for_candidate(*, candidate_id: str, db_path: Path) -> dict[str, Any] | None:
    with read_connection(db_path) as conn:
        require_schema(conn)
        return _opportunity_for_candidate_conn(conn, candidate_id)


def load_candidate_context(*, candidate_id: str, cutoff_at: str, db_path: Path) -> Optional[dict[str, Any]]:
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
                "opportunity": _opportunity_for_candidate_conn(conn, candidate_id),
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
            "ORDER BY revision DESC LIMIT 1", (observation_id, input_cutoff_at, analysis_kind),
        ).fetchone()
    if row is None:
        return None
    return {"analysisId": row[0], "revision": int(row[1]), "inputLineage": json.loads(row[2]),
            "content": json.loads(row[3]), "status": row[4], "createdAt": row[5]}


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
            "SELECT task_id FROM k10_tasks WHERE status='queued' OR (status='running' AND lease_until < ?) "
            "ORDER BY created_at,task_id LIMIT ?", (now_text, limit),
        ).fetchall()
        task_ids = [str(row[0]) for row in rows]
        claimed: list[Task] = []
        for task_id in task_ids:
            conn.execute(
                "UPDATE k10_tasks SET status='running',stage='leased',attempt_count=attempt_count+1,"
                "lease_owner=?,lease_until=?,updated_at=? WHERE task_id=? AND "
                "(status='queued' OR (status='running' AND lease_until < ?))",
                (worker_id, lease_until, now_text, task_id, now_text),
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
            "WHERE task_id=? AND (status='queued' OR (status='running' AND lease_until < ?))",
            (worker_id, lease_until, now_text, task_id, now_text),
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
    return {"budget": json.loads(row[0]), "checkpoint": json.loads(row[1]), "inputVersion": row[2], "inputCutoffAt": row[3]}


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
        changed = conn.execute(
            "UPDATE k10_tasks SET status=?,stage=?,checkpoint_json=?,error_text=?,lease_owner=NULL,lease_until=NULL,"
            "updated_at=? WHERE task_id=? AND status='running' AND lease_owner=? AND lease_until >= ?",
            (status, stage, _json(checkpoint), error_text, now_text, task_id, worker_id, now_text),
        ).rowcount
        if changed != 1:
            raise K10Conflict("任务租约已失效或不属于当前 worker")


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
        changed = conn.execute(
            "UPDATE k10_tasks SET status='queued',stage='retry_requested',error_text=NULL,lease_owner=NULL,"
            "lease_until=NULL,updated_at=? WHERE task_id=? AND attempt_count=? "
            "AND status IN ('failed','not_configured')",
            (retried_at, task_id, expected_attempt_count),
        ).rowcount
        if changed != 1:
            raise K10Conflict("任务不是可重试的当前失败版本")
        row = conn.execute(
            "SELECT task_id,kind,status,attempt_count,lease_owner,lease_until,payload_json FROM k10_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
    return _task_from_row(row)



# --- V1.4 opportunity publication and fixed-window evaluation -----------------

def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("K10 时间必须带时区")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha256(chr(31).join(parts).encode('utf-8')).hexdigest()[:32]}"


def _publication_input_payload(value) -> dict[str, Any]:
    return {"candidateId": value.candidate_id, "companyCode": value.company_code,
            "eventId": value.event_id, "eventRevision": value.event_revision,
            "opportunityKey": value.opportunity_key, "catalystStage": value.catalyst_stage,
            "category": value.category, "comparison": dict(value.comparison),
            "evidenceRefs": [dict(item) for item in value.evidence_refs],
            "sourceMarker": value.source_marker, "relatedOpportunityId": value.related_opportunity_id}


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
            allowed_comparison = required_comparison | {"marketContext"}
            if not isinstance(comparison, Mapping) or not required_comparison <= set(comparison) or not set(comparison) <= allowed_comparison:
                raise ValueError("正式推荐缺少完整比较快照")
            differences = comparison["differences"]
            classification = comparison["classification"]
            required_difference = {"role", "priorityReason", "gap", "rankChangeConditions", "twoDayReason"}
            required_classification = {"kind", "opportunityKey", "reason", "newFacts", "changedJudgment", "twoDayReason", "relatedOpportunityId"}
            if (not isinstance(comparison["summary"], str) or not comparison["summary"].strip() or
                not isinstance(differences, Mapping) or set(differences) != required_difference or
                not isinstance(classification, Mapping) or set(classification) != required_classification or
                differences.get("role") != item.category or classification.get("opportunityKey") != item.opportunity_key or
                classification.get("relatedOpportunityId") != item.related_opportunity_id or
                classification.get("kind") not in {"initial", "material_stage", "independent"} or
                not all(isinstance(differences.get(name), str) and differences[name].strip() for name in required_difference) or
                not all(isinstance(classification.get(name), str) and classification[name].strip() for name in ("reason", "newFacts", "twoDayReason")) or
                (classification.get("kind") == "material_stage" and (not item.related_opportunity_id or not isinstance(classification.get("changedJudgment"), str) or not classification["changedJudgment"].strip())) or
                (classification.get("kind") != "material_stage" and classification.get("changedJudgment") is not None and not isinstance(classification.get("changedJudgment"), str)) or
                ("marketContext" in comparison and not isinstance(comparison["marketContext"], Mapping)) or
                _json(comparison["evidenceRefs"]) != _json(item.evidence_refs)):
                raise ValueError("正式推荐比较、分类或精确证据引用不一致")
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
            rank = item.comparison.get("rank") if isinstance(item.comparison, Mapping) else None
            if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int) or rank < 1):
                raise ValueError("发布样本 rank 必须是正整数或 null")
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
        prior: list[tuple[str, str, datetime, int]] = []
        for action_id, action, created_at, rowid in _company_window_action_rows(conn, company_window_id=company_window_id):
            try:
                occurred = datetime.fromisoformat(str(created_at))
            except ValueError:
                continue
            if occurred.tzinfo is not None:
                prior.append((str(action_id), str(action), occurred, int(rowid)))
        prior.sort(key=lambda item: (item[2].astimezone(timezone.utc), item[3]))
    last = prior[-1] if prior else None
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
    if availability not in {"available", "suspended", "data_gap"}:
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
    row = conn.execute("SELECT kind FROM k10_opportunity_lifecycle_events WHERE opportunity_id=? ORDER BY rowid DESC LIMIT 1", (opportunity_id,)).fetchone()
    persisted = {"withdrawal": "withdrawn", "expired": "expired"}.get(row[0], "active") if row else "active"
    if persisted != "active":
        return persisted
    instant = datetime.now(timezone.utc) if as_of is None else as_of
    if instant.tzinfo is None:
        raise ValueError("机会状态查询 as_of 必须带时区")
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
        query = "SELECT w.company_window_id,w.company_code,w.first_batch_id,w.d0_trade_date,w.d1_trade_date,w.d2_trade_date,w.d1_selection_at,w.d2_close_at,w.sample_class,w.overlaps_window_id,s.state,s.action_ids_json,s.frozen_at FROM k10_company_windows w LEFT JOIN k10_company_window_selection_snapshots s ON s.company_window_id=w.company_window_id"
        args: tuple[Any, ...] = ()
        if company_code is not None:
            query += " WHERE w.company_code=?"; args = (company_code,)
        rows = conn.execute(query + " ORDER BY w.d1_trade_date,w.company_window_id", args).fetchall()
    return [{"companyWindowId": r[0], "companyCode": r[1], "firstBatchId": r[2], "d0TradeDate": r[3], "d1TradeDate": r[4], "d2TradeDate": r[5], "d1SelectionAt": r[6], "d2CloseAt": r[7], "sampleClass": r[8], "overlapsWindowId": r[9], "selection": None if r[10] is None else {"state": r[10], "actionIds": json.loads(r[11]), "frozenAt": r[12]}} for r in rows]


def list_opportunities(*, company_code: str | None = None, state: str | None = None, batch_id: str | None = None,
                       as_of: datetime | None = None, db_path: Path) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        query = "SELECT o.opportunity_id,o.opportunity_key,o.company_code,o.catalyst_event_id,o.catalyst_event_revision,o.catalyst_stage,o.related_opportunity_id,o.first_batch_id,o.company_window_id,o.created_at,b.available_at,w.d0_trade_date,w.d1_trade_date,w.d2_trade_date,w.sample_class,s.source_marker,p.content_json FROM k10_opportunities o JOIN k10_publication_batches b ON b.batch_id=o.first_batch_id JOIN k10_company_windows w ON w.company_window_id=o.company_window_id JOIN k10_publication_samples s ON s.opportunity_id=o.opportunity_id JOIN k10_opportunity_lifecycle_events p ON p.opportunity_id=o.opportunity_id AND p.kind='published'"
        clauses=[]; args=[]
        if company_code is not None: clauses.append("o.company_code=?"); args.append(company_code)
        if batch_id is not None: clauses.append("o.first_batch_id=?"); args.append(batch_id)
        if clauses: query += " WHERE " + " AND ".join(clauses)
        rows = conn.execute(query + " ORDER BY b.available_at,o.opportunity_id", tuple(args)).fetchall()
        events = {r[0]: _opportunity_state(conn, str(r[0]), as_of=as_of) for r in rows}
    items=[{"opportunityId":r[0],"opportunityKey":r[1],"companyCode":r[2],"eventId":r[3],"eventRevision":r[4],"catalystStage":r[5],"relatedOpportunityId":r[6],"firstBatchId":r[7],"companyWindowId":r[8],"createdAt":r[9],"availableAt":r[10],"d0TradeDate":r[11],"d1TradeDate":r[12],"d2TradeDate":r[13],"sampleClass":r[14],"sourceMarker":r[15],"latePublication":bool(json.loads(r[16]).get("latePublication", False)),"state":events[r[0]]} for r in rows]
    return [item for item in items if state is None or item["state"] == state]


def get_opportunity(*, opportunity_id: str, as_of: datetime | None = None, db_path: Path) -> dict[str, Any] | None:
    return next((item for item in list_opportunities(as_of=as_of, db_path=db_path) if item["opportunityId"] == opportunity_id), None)


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
    "append_morning_update", "append_opportunity_update", "append_run_config", "append_source_watermark",
    "append_event_revision", "candidate_state", "claim_task_by_id", "claim_tasks", "create_candidate",
    "create_scan", "enqueue_task", "finalize_scan", "finish_task", "freeze_company_window_selection",
    "get_candidate", "get_company_window_selection", "get_observation", "get_opportunity", "get_opportunity_for_candidate", "get_publication_batch", "get_scan",
    "latest_document_version", "latest_event_revision", "latest_market_day_facts", "latest_source_watermark", "list_candidates",
    "list_company_windows", "list_company_window_evaluations", "list_market_day_facts", "list_observations", "list_opportunities", "market_day_fact_id",
    "list_opportunity_lifecycle_events", "list_publication_batches", "list_publication_samples", "list_scans", "list_source_document_versions",
    "load_analysis_revision", "load_candidate_context", "load_document_versions", "load_observation_context",
    "load_task_analysis_config", "observe_candidate", "observe_company_window", "publish_opportunities", "read_run_config",
    "reopen_scan", "renew_task_lease", "retry_task", "task_execution_input", "update_running_scan_coverage",
    "withdraw_opportunity",
]
