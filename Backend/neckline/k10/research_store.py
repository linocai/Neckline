"""Append-only persistence and read projections for B39 investigation evidence."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .research_contracts import (
    Claim, EvidenceDisclosure, FullTextRequest, QueryPath, Question,
    ResearchContractError, ResearchSnapshot, ResearchStageResult,
    validate_company_assessment,
)
from .schema import read_connection, require_schema, write_connection
from .store import K10Conflict, _json


LeaseGuard = Callable[[], None]


def _guard(value: LeaseGuard | None) -> None:
    if value is not None:
        value()


def _aware_instant(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise K10Conflict(f"{field} 无效")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise K10Conflict(f"{field} 必须是带时区的 ISO 时间") from exc
    if parsed.tzinfo is None:
        raise K10Conflict(f"{field} 必须带时区")
    return parsed


def _ref(value: Any, field: str) -> tuple[str, int]:
    if not isinstance(value, Mapping):
        raise ResearchContractError(f"{field} 必须为来源引用")
    document_id, revision = value.get("documentId"), value.get("revision")
    if not isinstance(document_id, str) or not document_id or isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ResearchContractError(f"{field} 必须含有效 documentId/revision")
    return document_id, revision


def _latest_snapshot_row(conn, snapshot_id: str):
    return conn.execute(
        "SELECT revision,snapshot_json FROM k10_research_snapshot_revisions "
        "WHERE snapshot_id=? ORDER BY revision DESC LIMIT 1", (snapshot_id,)
    ).fetchone()


def _snapshot_for_input_row(conn, snapshot: ResearchSnapshot):
    return conn.execute(
        "SELECT snapshot_id FROM k10_research_snapshot_revisions WHERE revision=1 AND task_id=? AND event_id=? "
        "AND event_revision=? AND context_sha256=? AND prompt_contract_revision=? AND model_parameters_sha256=?",
        (snapshot.task_id, snapshot.event_id, snapshot.event_revision, snapshot.context_sha256,
         snapshot.prompt_contract_revision, snapshot.model_parameters_sha256),
    ).fetchone()


def _snapshot(value: str) -> ResearchSnapshot:
    return ResearchSnapshot.from_dict(json.loads(value))


def _assert_same_identity(existing: ResearchSnapshot, candidate: ResearchSnapshot) -> None:
    keys = (
        "snapshot_id", "task_id", "event_id", "event_revision", "news_cutoff_at", "verification_cutoff_at",
        "context_sha256", "prompt_contract_revision", "model_parameters_sha256",
    )
    if any(getattr(existing, key) != getattr(candidate, key) for key in keys):
        raise K10Conflict("研究快照 ID 已绑定不同的不可变输入")


def create_research_snapshot(*, snapshot: ResearchSnapshot, db_path: Path,
                             lease_guard: LeaseGuard | None = None) -> ResearchSnapshot:
    """Create immutable revision 1, or return its current descendant on a safe replay."""
    if snapshot.revision != 1:
        raise K10Conflict("研究快照只能从 revision 1 创建")
    with write_connection(db_path) as conn:
        require_schema(conn)
        _guard(lease_guard)
        previous = _latest_snapshot_row(conn, snapshot.snapshot_id)
        if previous is not None:
            current = _snapshot(previous[1])
            _assert_same_identity(current, snapshot)
            return current
        matching_input = _snapshot_for_input_row(conn, snapshot)
        if matching_input is not None:
            current_row = _latest_snapshot_row(conn, str(matching_input[0]))
            if current_row is None:  # Defensive: the partial unique row must have a parent snapshot.
                raise K10Conflict("研究快照输入索引不完整")
            return _snapshot(current_row[1])
        conn.execute(
            "INSERT INTO k10_research_snapshot_revisions("
            "snapshot_id,revision,task_id,event_id,event_revision,news_cutoff_at,verification_cutoff_at,"
            "context_sha256,prompt_contract_revision,model_parameters_sha256,research_status,execution_status,"
            "snapshot_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (snapshot.snapshot_id, snapshot.revision, snapshot.task_id, snapshot.event_id, snapshot.event_revision,
             snapshot.news_cutoff_at, snapshot.verification_cutoff_at, snapshot.context_sha256,
             snapshot.prompt_contract_revision, snapshot.model_parameters_sha256, snapshot.research_status,
             snapshot.execution_status, _json(snapshot.to_dict()), snapshot.created_at, snapshot.updated_at),
        )
    return snapshot


def _evidence_update(value: Mapping[str, Any]) -> tuple[str, str, int, str, str, Mapping[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {"claimId", "sourceRef", "relation", "location", "applicability"}:
        raise ResearchContractError("evidenceUpdates 必须精确包含 claimId/sourceRef/relation/location/applicability")
    claim_id = value["claimId"]
    relation = value["relation"]
    location = value["location"]
    if not isinstance(claim_id, str) or not claim_id:
        raise ResearchContractError("evidenceUpdates.claimId 无效")
    if relation not in {"supports", "partially_supports", "contradicts", "duplicate", "irrelevant", "conflicts"}:
        raise ResearchContractError("evidenceUpdates.relation 无效")
    if not isinstance(location, str) or not location:
        raise ResearchContractError("evidenceUpdates.location 无效")
    if not isinstance(value["applicability"], Mapping):
        raise ResearchContractError("evidenceUpdates.applicability 必须为对象")
    document_id, revision = _ref(value["sourceRef"], "evidenceUpdates.sourceRef")
    return claim_id, document_id, revision, relation, location, value["applicability"]


def _distinct(values: Sequence[Any], key: Callable[[Any], str], name: str) -> None:
    seen: set[str] = set()
    for item in values:
        value = key(item)
        if value in seen:
            raise ResearchContractError(f"{name} ID 重复")
        seen.add(value)


def advance_research_snapshot(
    *, snapshot_id: str, expected_revision: int, research_status: str, execution_status: str,
    stage_result: ResearchStageResult, input_sha256: str, updated_at: str, db_path: Path,
    lease_guard: LeaseGuard | None = None, verification_cutoff_at: str | None = None,
) -> ResearchSnapshot:
    """CAS append one semantic research action and its typed derivatives.

    Repeating the identical action/input after a completed write returns the
    appended revision; all other stale writers fail instead of overwriting it.
    """
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
        raise K10Conflict("expected_revision 无效")
    if not isinstance(input_sha256, str) or len(input_sha256) != 64:
        raise K10Conflict("研究输入摘要无效")
    updated_instant = _aware_instant(updated_at, "updated_at")
    try:
        # Validate status by making the next snapshot.  This also protects DB
        # callers that bypass model decoding.
        _distinct(stage_result.claims, lambda item: item.claim_id, "claim")
        _distinct(stage_result.questions, lambda item: item.question_id, "question")
        _distinct(stage_result.query_paths, lambda item: item.path_id, "query path")
        _distinct(stage_result.fulltext_requests, lambda item: item.request_id, "fulltext request")
        clean_assessments = [validate_company_assessment(item) for item in stage_result.company_assessments]
        _distinct(clean_assessments, lambda item: item["companyCode"], "company assessment")
        updates = [_evidence_update(item) for item in stage_result.evidence_updates]
    except ResearchContractError:
        raise
    with write_connection(db_path) as conn:
        require_schema(conn)
        _guard(lease_guard)
        latest = _latest_snapshot_row(conn, snapshot_id)
        if latest is None:
            raise K10Conflict("研究快照不存在")
        current = _snapshot(latest[1])
        if current.revision != expected_revision:
            replay = conn.execute(
                "SELECT input_sha256 FROM k10_research_stage_results WHERE snapshot_id=? AND revision=? AND action=?",
                (snapshot_id, current.revision, stage_result.action),
            ).fetchone()
            if replay is not None and replay[0] == input_sha256:
                return current
            raise K10Conflict("研究快照已被其他执行者推进")
        next_verification_cutoff = current.verification_cutoff_at
        if verification_cutoff_at is not None:
            old_cutoff = _aware_instant(current.verification_cutoff_at, "既有 verification_cutoff_at")
            proposed_cutoff = _aware_instant(verification_cutoff_at, "verification_cutoff_at")
            if proposed_cutoff < old_cutoff:
                raise K10Conflict("verification_cutoff_at 不得倒退")
            if proposed_cutoff > updated_instant:
                raise K10Conflict("verification_cutoff_at 不得晚于 updated_at")
            next_verification_cutoff = verification_cutoff_at
        next_snapshot = ResearchSnapshot(
            snapshot_id=current.snapshot_id, task_id=current.task_id, event_id=current.event_id,
            event_revision=current.event_revision, news_cutoff_at=current.news_cutoff_at,
            verification_cutoff_at=next_verification_cutoff, context_sha256=current.context_sha256,
            prompt_contract_revision=current.prompt_contract_revision,
            model_parameters_sha256=current.model_parameters_sha256, research_status=research_status,
            execution_status=execution_status, revision=current.revision + 1,
            created_at=current.created_at, updated_at=updated_at,
        )
        conn.execute(
            "INSERT INTO k10_research_snapshot_revisions("
            "snapshot_id,revision,task_id,event_id,event_revision,news_cutoff_at,verification_cutoff_at,"
            "context_sha256,prompt_contract_revision,model_parameters_sha256,research_status,execution_status,"
            "snapshot_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (next_snapshot.snapshot_id, next_snapshot.revision, next_snapshot.task_id, next_snapshot.event_id,
             next_snapshot.event_revision, next_snapshot.news_cutoff_at, next_snapshot.verification_cutoff_at,
             next_snapshot.context_sha256, next_snapshot.prompt_contract_revision,
             next_snapshot.model_parameters_sha256, next_snapshot.research_status, next_snapshot.execution_status,
             _json(next_snapshot.to_dict()), next_snapshot.created_at, next_snapshot.updated_at),
        )
        conn.execute(
            "INSERT INTO k10_research_stage_results(snapshot_id,revision,action,input_sha256,result_json,safe_error_code,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (snapshot_id, next_snapshot.revision, stage_result.action, input_sha256, _json(stage_result.to_dict()),
             stage_result.safe_error_code, updated_at),
        )
        for claim in stage_result.claims:
            doc, doc_revision = _ref(claim.source_ref, "claim.sourceRef")
            conn.execute(
                "INSERT INTO k10_research_claims(snapshot_id,snapshot_revision,claim_id,document_id,document_revision,"
                "claim_kind,novelty,verification_status,claim_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (snapshot_id, next_snapshot.revision, claim.claim_id, doc, doc_revision, claim.kind, claim.novelty,
                 claim.verification_status, _json(claim.to_dict()), updated_at),
            )
        for claim_id, doc, doc_revision, relation, location, applicability in updates:
            conn.execute(
                "INSERT INTO k10_research_evidence_links(snapshot_id,snapshot_revision,claim_id,document_id,document_revision,"
                "relation,location,applicability_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (snapshot_id, next_snapshot.revision, claim_id, doc, doc_revision, relation, location,
                 _json(dict(applicability)), updated_at),
            )
        for question in stage_result.questions:
            conn.execute(
                "INSERT INTO k10_research_questions(snapshot_id,snapshot_revision,question_id,state,question_json,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (snapshot_id, next_snapshot.revision, question.question_id, question.state, _json(question.to_dict()), updated_at),
            )
        for path in stage_result.query_paths:
            conn.execute(
                "INSERT INTO k10_research_query_paths(snapshot_id,snapshot_revision,path_id,question_id,state,path_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (snapshot_id, next_snapshot.revision, path.path_id, path.question_id, path.state, _json(path.to_dict()), updated_at),
            )
        for request in stage_result.fulltext_requests:
            doc, doc_revision = _ref(request.source_ref, "fulltextRequest.sourceRef")
            conn.execute(
                "INSERT INTO k10_research_fulltext_requests(snapshot_id,snapshot_revision,request_id,question_id,document_id,"
                "document_revision,state,request_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (snapshot_id, next_snapshot.revision, request.request_id, request.question_id, doc, doc_revision,
                 request.state, _json(request.to_dict()), updated_at),
            )
        for assessment in clean_assessments:
            conn.execute(
                "INSERT INTO k10_research_company_assessments(snapshot_id,snapshot_revision,company_code,role,rank,"
                "disclosure_json,assessment_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (snapshot_id, next_snapshot.revision, assessment["companyCode"], assessment["role"], assessment["rank"],
                 _json(assessment["evidenceDisclosure"]), _json(assessment), updated_at),
            )
    return next_snapshot


def read_research_snapshot(*, snapshot_id: str, db_path: Path,
                           revision: int | None = None) -> ResearchSnapshot | None:
    """Strictly read one immutable snapshot revision; no schema initialization."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        if revision is None:
            row = _latest_snapshot_row(conn, snapshot_id)
        else:
            row = conn.execute(
                "SELECT revision,snapshot_json FROM k10_research_snapshot_revisions WHERE snapshot_id=? AND revision=?",
                (snapshot_id, revision),
            ).fetchone()
    return None if row is None else _snapshot(row[1])


def find_research_snapshot(
    *, task_id: str, event_id: str, event_revision: int, context_sha256: str,
    prompt_contract_revision: str, model_parameters_sha256: str, db_path: Path,
) -> ResearchSnapshot | None:
    """Read the stable snapshot for exactly one frozen research input.

    Recovery callers use this instead of inferring identity from a candidate or
    a later publication revision.
    """
    probe = ResearchSnapshot("probe", task_id, event_id, event_revision, "cutoff", "cutoff", context_sha256,
                             prompt_contract_revision, model_parameters_sha256, "continue_research", "ok", 1,
                             "created", "updated")
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = _snapshot_for_input_row(conn, probe)
        latest = None if row is None else _latest_snapshot_row(conn, str(row[0]))
    return None if latest is None else _snapshot(latest[1])


def _latest_payloads(conn, table: str, id_column: str, json_column: str, snapshot_id: str, revision: int | None) -> list[dict[str, Any]]:
    if revision is None:
        latest = _latest_snapshot_row(conn, snapshot_id)
        if latest is None:
            return []
        revision = int(latest[0])
    rows = conn.execute(
        f"SELECT {id_column},{json_column} FROM {table} WHERE snapshot_id=? AND snapshot_revision<=? "
        f"ORDER BY snapshot_revision ASC", (snapshot_id, revision),
    ).fetchall()
    latest_by_id: dict[str, dict[str, Any]] = {}
    for item_id, payload in rows:
        latest_by_id[str(item_id)] = json.loads(payload)
    return [latest_by_id[key] for key in sorted(latest_by_id)]


def list_research_claims(*, snapshot_id: str, db_path: Path, revision: int | None = None) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        return _latest_payloads(conn, "k10_research_claims", "claim_id", "claim_json", snapshot_id, revision)


def list_research_questions(*, snapshot_id: str, db_path: Path, revision: int | None = None) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        return _latest_payloads(conn, "k10_research_questions", "question_id", "question_json", snapshot_id, revision)


def list_research_query_paths(*, snapshot_id: str, db_path: Path, revision: int | None = None) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        return _latest_payloads(conn, "k10_research_query_paths", "path_id", "path_json", snapshot_id, revision)


def list_research_fulltext_requests(*, snapshot_id: str, db_path: Path, revision: int | None = None) -> list[dict[str, Any]]:
    with read_connection(db_path) as conn:
        require_schema(conn)
        return _latest_payloads(conn, "k10_research_fulltext_requests", "request_id", "request_json", snapshot_id, revision)


def _latest_evidence_updates(conn, snapshot_id: str, revision: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT claim_id,document_id,document_revision,relation,location,applicability_json "
        "FROM k10_research_evidence_links WHERE snapshot_id=? AND snapshot_revision<=? ORDER BY snapshot_revision ASC",
        (snapshot_id, revision),
    ).fetchall()
    latest: dict[tuple[str, str, int], dict[str, Any]] = {}
    for claim_id, document_id, document_revision, relation, location, applicability in rows:
        latest[(str(claim_id), str(document_id), int(document_revision))] = {
            "claimId": str(claim_id), "sourceRef": {"documentId": str(document_id), "revision": int(document_revision)},
            "relation": str(relation), "location": str(location), "applicability": json.loads(applicability),
        }
    return [latest[key] for key in sorted(latest)]


def read_research_state(*, snapshot_id: str, db_path: Path) -> dict[str, Any] | None:
    """Read a complete safe recovery state without executing DDL or a model action.

    ``snapshot`` remains typed; derivatives are validated persisted dictionaries.
    Stage results remain in durable revision order so callers can resume the
    first missing action instead of replaying completed work.
    """
    with read_connection(db_path) as conn:
        require_schema(conn)
        row = _latest_snapshot_row(conn, snapshot_id)
        if row is None:
            return None
        snapshot = _snapshot(row[1])
        stages = conn.execute(
            "SELECT revision,action,input_sha256,result_json FROM k10_research_stage_results "
            "WHERE snapshot_id=? ORDER BY revision ASC", (snapshot_id,),
        ).fetchall()
        return {
            "snapshot": snapshot,
            "claims": _latest_payloads(conn, "k10_research_claims", "claim_id", "claim_json", snapshot_id, snapshot.revision),
            "questions": _latest_payloads(conn, "k10_research_questions", "question_id", "question_json", snapshot_id, snapshot.revision),
            "paths": _latest_payloads(conn, "k10_research_query_paths", "path_id", "path_json", snapshot_id, snapshot.revision),
            "fulltextRequests": _latest_payloads(conn, "k10_research_fulltext_requests", "request_id", "request_json", snapshot_id, snapshot.revision),
            "assessments": _latest_payloads(conn, "k10_research_company_assessments", "company_code", "assessment_json", snapshot_id, snapshot.revision),
            "evidenceUpdates": _latest_evidence_updates(conn, snapshot_id, snapshot.revision),
            "stageResults": [
                {"revision": int(revision), "action": str(action), "inputSha256": str(input_sha256), "result": json.loads(result)}
                for revision, action, input_sha256, result in stages
            ],
        }


def _eligible_prior_source(
    conn, *, ref: Mapping[str, Any], allowed: set[tuple[str, int]], cutoff: datetime,
) -> tuple[bool, dict[str, Any]]:
    """Return a source timing decision without selecting any article body."""
    document_id, revision = _ref(ref, "sourceRef")
    provenance = {"sourceRef": {"documentId": document_id, "revision": revision}}
    if (document_id, revision) not in allowed:
        return False, {**provenance, "reason": "not_current_input"}
    row = conn.execute(
        "SELECT published_at,published_precision,fetched_at FROM k10_source_document_versions "
        "WHERE document_id=? AND revision=?", (document_id, revision),
    ).fetchone()
    if row is None:
        return False, {**provenance, "reason": "missing_document"}
    published_at, precision, fetched_at = row
    if precision != "exact" or published_at is None:
        return False, {**provenance, "reason": "unknown_published_time"}
    try:
        published = _aware_instant(str(published_at), "source published_at")
        fetched = _aware_instant(str(fetched_at), "source fetched_at")
    except K10Conflict:
        return False, {**provenance, "reason": "unknown_source_time"}
    if published > cutoff:
        return False, {**provenance, "reason": "published_after_cutoff", "publishedAt": str(published_at)}
    if fetched > cutoff:
        return False, {**provenance, "reason": "fetched_after_cutoff", "fetchedAt": str(fetched_at)}
    return True, {**provenance, "publishedAt": str(published_at), "fetchedAt": str(fetched_at)}


def load_prior_research_evidence(
    *, task_id: str, input_source_refs: Sequence[Mapping[str, Any]], news_cutoff_at: str,
    verification_cutoff_at: str, prompt_contract_revision: str, model_parameters_sha256: str,
    db_path: Path, canonical_key: str | None = None, event_id: str | None = None,
) -> dict[str, Any]:
    """Load bounded, time-applicable evidence from prior *task* snapshots.

    This is intentionally a source-fact/relation cache, not a comparison cache:
    it never returns assessments, ranks, market context, prompts, or article
    bodies.  A caller must choose exactly one stable event identity and supplies
    its currently frozen source refs.  The current task is always excluded:
    its in-progress snapshot is already part of the caller's packet. Anything
    absent, imprecisely timed, or after either relevant cutoff is returned only
    as an isolation record.
    """
    if (canonical_key is None) == (event_id is None):
        raise ValueError("必须且只能指定 canonical_key 或 event_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id 必须是非空字符串")
    if not isinstance(prompt_contract_revision, str) or not prompt_contract_revision:
        raise ValueError("prompt_contract_revision 必须是非空字符串")
    if not isinstance(model_parameters_sha256, str) or len(model_parameters_sha256) != 64:
        raise ValueError("model_parameters_sha256 必须是 SHA-256")
    if isinstance(input_source_refs, (str, bytes)) or not isinstance(input_source_refs, Sequence):
        raise ValueError("input_source_refs 必须是来源引用列表")
    allowed = {_ref(ref, "input_source_refs") for ref in input_source_refs}
    news_cutoff = _aware_instant(news_cutoff_at, "news_cutoff_at")
    verification_cutoff = _aware_instant(verification_cutoff_at, "verification_cutoff_at")
    if verification_cutoff < news_cutoff:
        raise K10Conflict("verification_cutoff_at 不得早于 news_cutoff_at")
    with read_connection(db_path) as conn:
        require_schema(conn)
        if canonical_key is not None:
            event_row = conn.execute("SELECT event_id FROM k10_events WHERE stable_key=?", (canonical_key,)).fetchone()
        else:
            event_row = conn.execute("SELECT event_id FROM k10_events WHERE event_id=?", (event_id,)).fetchone()
        if event_row is None:
            return {
                "claims": [], "companyRelations": [], "isolated": [],
                "scope": {"currentTaskId": task_id, "canonicalKey": canonical_key, "eventId": event_id,
                          "newsCutoffAt": news_cutoff_at, "verificationCutoffAt": verification_cutoff_at,
                          "promptContractRevision": prompt_contract_revision,
                          "modelParametersSha256": model_parameters_sha256,
                          "inputSourceRefs": [{"documentId": document_id, "revision": revision}
                                              for document_id, revision in sorted(allowed)]},
            }
        source_event_id = str(event_row[0])
        rows = conn.execute(
            "SELECT r.snapshot_id,r.revision,r.task_id,r.event_id,r.event_revision,r.news_cutoff_at,r.verification_cutoff_at "
            "FROM k10_research_snapshot_revisions r JOIN ("
            "SELECT snapshot_id,MAX(revision) revision FROM k10_research_snapshot_revisions "
            "WHERE event_id=? AND task_id<>? GROUP BY snapshot_id"
            ") latest ON latest.snapshot_id=r.snapshot_id AND latest.revision=r.revision "
            "WHERE r.event_id=? AND r.task_id<>? AND r.prompt_contract_revision=? AND r.model_parameters_sha256=? "
            "ORDER BY r.updated_at DESC,r.snapshot_id DESC",
            (source_event_id, task_id, source_event_id, task_id, prompt_contract_revision, model_parameters_sha256),
        ).fetchall()
        claims: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        isolated: list[dict[str, Any]] = []
        seen_claims: set[tuple[str, str, int]] = set()
        seen_relations: set[tuple[str, str]] = set()
        for snapshot_id, revision, prior_task_id, prior_event_id, source_event_revision, source_news_cutoff, source_verification_cutoff in rows:
            snapshot_provenance = {
                "taskId": str(prior_task_id), "snapshotId": str(snapshot_id), "snapshotRevision": int(revision), "eventId": str(prior_event_id),
                "eventRevision": int(source_event_revision), "newsCutoffAt": str(source_news_cutoff),
                "verificationCutoffAt": str(source_verification_cutoff),
            }
            try:
                prior_news_cutoff = _aware_instant(str(source_news_cutoff), "prior news_cutoff_at")
                prior_verification_cutoff = _aware_instant(str(source_verification_cutoff), "prior verification_cutoff_at")
            except K10Conflict:
                isolated.append({"kind": "snapshot", "reason": "unknown_snapshot_cutoff", "provenance": snapshot_provenance})
                continue
            if prior_news_cutoff > news_cutoff or prior_verification_cutoff > verification_cutoff:
                isolated.append({"kind": "snapshot", "reason": "snapshot_after_current_cutoff", "provenance": snapshot_provenance})
                continue
            for claim in _latest_payloads(conn, "k10_research_claims", "claim_id", "claim_json", str(snapshot_id), int(revision)):
                ref = claim.get("sourceRef")
                try:
                    eligible, timing = _eligible_prior_source(conn, ref=ref, allowed=allowed, cutoff=news_cutoff)
                except ResearchContractError:
                    eligible, timing = False, {"sourceRef": ref, "reason": "invalid_source_ref"}
                if not eligible:
                    isolated.append({**timing, "kind": "claim", "provenance": snapshot_provenance})
                    continue
                key = (str(claim.get("claimId")), timing["sourceRef"]["documentId"], timing["sourceRef"]["revision"])
                if key in seen_claims:
                    continue
                seen_claims.add(key)
                claims.append({
                    "claimId": claim.get("claimId"), "text": claim.get("text"), "kind": claim.get("kind"),
                    "novelty": claim.get("novelty"), "verificationStatus": claim.get("verificationStatus"),
                    "decisionImpact": claim.get("decisionImpact"), "sourceRef": timing["sourceRef"],
                    "location": claim.get("location"), "sourceTiming": {"publishedAt": timing["publishedAt"], "fetchedAt": timing["fetchedAt"]},
                    "provenance": snapshot_provenance,
                })
            stage_rows = conn.execute(
                "SELECT result_json FROM k10_research_stage_results WHERE snapshot_id=? AND revision<=? ORDER BY revision ASC",
                (snapshot_id, revision),
            ).fetchall()
            for (result_json,) in stage_rows:
                result = json.loads(result_json)
                conclusion = result.get("conclusion") if isinstance(result, Mapping) else None
                mappings = conclusion.get("companyMappings") if isinstance(conclusion, Mapping) else None
                if not isinstance(mappings, Sequence) or isinstance(mappings, (str, bytes)):
                    continue
                for mapping in mappings:
                    if not isinstance(mapping, Mapping):
                        continue
                    company_code, stage = mapping.get("companyCode"), mapping.get("affectedStage")
                    refs = mapping.get("relationEvidence")
                    if not isinstance(company_code, str) or not company_code or not isinstance(stage, str) or not stage:
                        continue
                    if not isinstance(refs, Sequence) or isinstance(refs, (str, bytes)) or not refs:
                        isolated.append({"kind": "companyRelation", "reason": "missing_relation_evidence", "provenance": snapshot_provenance})
                        continue
                    timing_rows: list[dict[str, Any]] = []
                    failed = False
                    for ref in refs:
                        try:
                            eligible, timing = _eligible_prior_source(conn, ref=ref, allowed=allowed, cutoff=verification_cutoff)
                        except ResearchContractError:
                            eligible, timing = False, {"sourceRef": ref, "reason": "invalid_source_ref"}
                        if not eligible:
                            isolated.append({**timing, "kind": "companyRelation", "companyCode": company_code,
                                             "provenance": snapshot_provenance})
                            failed = True
                            break
                        timing_rows.append(timing)
                    if failed:
                        continue
                    key = (company_code, stage)
                    if key in seen_relations:
                        continue
                    seen_relations.add(key)
                    relations.append({
                        "companyCode": company_code, "affectedStage": stage,
                        "relationEvidence": [item["sourceRef"] for item in timing_rows],
                        "sourceTiming": [{"sourceRef": item["sourceRef"], "publishedAt": item["publishedAt"],
                                          "fetchedAt": item["fetchedAt"]} for item in timing_rows],
                        "inference": dict(mapping["inference"]) if isinstance(mapping.get("inference"), Mapping) else {},
                        "uncertainty": mapping.get("uncertainty") if isinstance(mapping.get("uncertainty"), str) else None,
                        "provenance": snapshot_provenance,
                    })
    return {
        "claims": claims, "companyRelations": relations, "isolated": isolated,
        "scope": {"currentTaskId": task_id, "canonicalKey": canonical_key, "eventId": event_id,
                  "newsCutoffAt": news_cutoff_at, "verificationCutoffAt": verification_cutoff_at,
                  "promptContractRevision": prompt_contract_revision,
                  "modelParametersSha256": model_parameters_sha256,
                  "inputSourceRefs": [{"documentId": document_id, "revision": revision}
                                      for document_id, revision in sorted(allowed)]},
    }


def _latest_snapshots_for_task(conn, task_id: str) -> list[ResearchSnapshot]:
    rows = conn.execute(
        "SELECT r.snapshot_json FROM k10_research_snapshot_revisions r JOIN ("
        "SELECT snapshot_id,MAX(revision) AS revision FROM k10_research_snapshot_revisions WHERE task_id=? GROUP BY snapshot_id"
        ") latest ON latest.snapshot_id=r.snapshot_id AND latest.revision=r.revision ORDER BY r.snapshot_id",
        (task_id,),
    ).fetchall()
    return [_snapshot(row[0]) for row in rows]


def _task_for_scan(conn, scan_id: str) -> str | None:
    row = conn.execute("SELECT task_id FROM k10_scan_execution_bindings WHERE scan_id=?", (scan_id,)).fetchone()
    return None if row is None else str(row[0])


def _safe_error_code(conn, snapshot_id: str, revision: int) -> str | None:
    row = conn.execute(
        "SELECT safe_error_code FROM k10_research_stage_results WHERE snapshot_id=? AND revision=?",
        (snapshot_id, revision),
    ).fetchone()
    return None if row is None or row[0] is None else str(row[0])


def _one_selector(*, scan_id: str | None, task_id: str | None, snapshot_id: str | None) -> None:
    if sum(value is not None for value in (scan_id, task_id, snapshot_id)) != 1:
        raise ValueError("必须且只能指定 scan_id、task_id 或 snapshot_id 之一")


def list_research_assessments(*, db_path: Path, scan_id: str | None = None, task_id: str | None = None,
                              snapshot_id: str | None = None) -> list[dict[str, Any]]:
    """Read complete latest assessments, retaining pending/excluded coverage."""
    _one_selector(scan_id=scan_id, task_id=task_id, snapshot_id=snapshot_id)
    with read_connection(db_path) as conn:
        require_schema(conn)
        if scan_id is not None:
            task_id = _task_for_scan(conn, scan_id)
            if task_id is None:
                return []
        snapshots = ([read for read in _latest_snapshots_for_task(conn, task_id)] if task_id is not None else
                     ([(_snapshot(row[1]))] if (row := _latest_snapshot_row(conn, snapshot_id)) is not None else []))
        values: list[dict[str, Any]] = []
        for snapshot in snapshots:
            for assessment in _latest_payloads(conn, "k10_research_company_assessments", "company_code", "assessment_json",
                                               snapshot.snapshot_id, snapshot.revision):
                values.append({"snapshotId": snapshot.snapshot_id, "snapshotRevision": snapshot.revision,
                               "eventId": snapshot.event_id, "eventRevision": snapshot.event_revision,
                               "researchStatus": snapshot.research_status, "executionStatus": snapshot.execution_status,
                               "safeErrorCode": _safe_error_code(conn, snapshot.snapshot_id, snapshot.revision),
                               **assessment})
    return values


def research_summary_for_scan(*, scan_id: str, db_path: Path) -> dict[str, Any] | None:
    """Safe aggregate projection for the API; prompt text, bodies and URLs never appear."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        task_id = _task_for_scan(conn, scan_id)
        if task_id is None:
            return None
        snapshots = _latest_snapshots_for_task(conn, task_id)
        research_counts: dict[str, int] = {}
        execution_counts: dict[str, int] = {}
        safe_failure_counts: dict[str, int] = {}
        question_counts = {state: 0 for state in ("open", "answered", "blocked", "abandoned")}
        company_counts = {role: 0 for role in ("primary", "alternative", "tied", "pending", "excluded")}
        for snapshot in snapshots:
            research_counts[snapshot.research_status] = research_counts.get(snapshot.research_status, 0) + 1
            execution_counts[snapshot.execution_status] = execution_counts.get(snapshot.execution_status, 0) + 1
            safe_error_code = _safe_error_code(conn, snapshot.snapshot_id, snapshot.revision)
            if safe_error_code is not None:
                safe_failure_counts[safe_error_code] = safe_failure_counts.get(safe_error_code, 0) + 1
            for question in _latest_payloads(conn, "k10_research_questions", "question_id", "question_json",
                                             snapshot.snapshot_id, snapshot.revision):
                state = question["state"]
                question_counts[state] += 1
            for assessment in _latest_payloads(conn, "k10_research_company_assessments", "company_code", "assessment_json",
                                               snapshot.snapshot_id, snapshot.revision):
                company_counts[assessment["role"]] += 1
    return {
        "scanId": scan_id, "taskId": task_id, "eventCount": len(snapshots),
        "questionCounts": question_counts, "companyCounts": {**company_counts,
            "comparable": company_counts["primary"] + company_counts["alternative"] + company_counts["tied"]},
        "researchStatusCounts": research_counts, "executionStatusCounts": execution_counts,
        "safeFailureCounts": safe_failure_counts,
        "comparisonComplete": bool(snapshots) and all(item.research_status == "comparison_complete" for item in snapshots),
        "executionFailed": any(item.execution_status == "failed" for item in snapshots),
    }


__all__ = [
    "advance_research_snapshot", "create_research_snapshot", "find_research_snapshot", "list_research_assessments", "list_research_claims",
    "list_research_fulltext_requests", "list_research_query_paths", "list_research_questions", "load_prior_research_evidence", "read_research_snapshot", "read_research_state",
    "research_summary_for_scan",
]
