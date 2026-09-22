"""Append-only persistence and read projections for B39 investigation evidence."""
from __future__ import annotations

import json
from hashlib import sha256
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .research_contracts import (
    Claim, EvidenceDisclosure, FullTextRequest, QueryPath, Question,
    ResearchContractError, ResearchRoundResult, ResearchSnapshot, ResearchStageResult,
    EXECUTION_STATUSES, RESEARCH_ROUND_ACTION, RESEARCH_STATUSES,
    validate_company_assessment, validate_evidence_update, group_evidence_updates,
)
from .schema import read_connection, require_schema, write_connection
from .store import K10Conflict, _json


LeaseGuard = Callable[[], None]

_ROUND_UNSAFE_KEYS = frozenset({"originalText", "original_text", "rawResponse", "raw_response", "prompt"})


def _safe_json(value: Any, *, field: str) -> Mapping[str, Any] | list[Any]:
    """Keep direct-round state useful for replay but free of bodies/raw replies."""
    def clean(item: Any) -> Any:
        if isinstance(item, Mapping):
            if _ROUND_UNSAFE_KEYS.intersection(item):
                raise K10Conflict(f"{field} 不得保存正文或原始供应商回复")
            return {str(key): clean(value) for key, value in item.items()}
        if isinstance(item, list):
            return [clean(value) for value in item]
        if item is None or isinstance(item, (str, int, float, bool)):
            return item
        raise K10Conflict(f"{field} 不是 JSON")
    result = clean(value)
    if not isinstance(result, (Mapping, list)):
        raise K10Conflict(f"{field} 必须是 JSON 对象或数组")
    return result


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


def append_research_round(
    *, snapshot_id: str, expected_revision: int, input_packet: Mapping[str, Any],
    result: ResearchRoundResult, local_context_results: Sequence[Mapping[str, Any]] = (),
    local_tool_evidence: Sequence[Mapping[str, Any]] = (), research_status: str,
    updated_at: str, db_path: Path, lease_guard: LeaseGuard | None = None,
) -> ResearchSnapshot:
    """CAS append one B78 result without using the retired stage-result table."""
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
        raise K10Conflict("expected_revision 无效")
    if not isinstance(result, ResearchRoundResult) or result.action != RESEARCH_ROUND_ACTION:
        raise K10Conflict("B78 研究轮次结果无效")
    if research_status not in RESEARCH_STATUSES:
        raise K10Conflict("B78 研究状态无效")
    if result.context_requests and research_status != "continue_research":
        raise K10Conflict("局部回读轮次必须保持研究中状态")
    if not result.context_requests:
        conclusion = result.conclusion
        if not isinstance(conclusion, Mapping) or conclusion.get("researchStatus") != research_status:
            raise K10Conflict("B78 研究状态与轮次结论不一致")
    _aware_instant(updated_at, "updated_at")
    packet = _safe_json(input_packet, field="研究资料包")
    clean_result = _safe_json(result.to_dict(), field="研究轮次结果")
    contexts = _safe_json(list(local_context_results), field="本地回读结果")
    tools = _safe_json(list(local_tool_evidence), field="本地补查证据")
    input_sha256 = sha256(_json(packet).encode("utf-8")).hexdigest()
    with write_connection(db_path) as conn:
        require_schema(conn)
        _guard(lease_guard)
        latest = _latest_snapshot_row(conn, snapshot_id)
        if latest is None:
            raise K10Conflict("研究快照不存在")
        current = _snapshot(latest[1])
        if current.revision != expected_revision:
            replay = conn.execute(
                "SELECT input_sha256,input_packet_json,result_json,context_results_json,tool_evidence_json "
                "FROM k10_research_round_results WHERE snapshot_id=? AND revision=?",
                (snapshot_id, current.revision),
            ).fetchone()
            expected = (input_sha256, _json(packet), _json(clean_result), _json(contexts), _json(tools))
            if (replay is not None and tuple(replay) == expected
                    and current.research_status == research_status and current.execution_status == "ok"):
                return current
            raise K10Conflict("研究快照已被其他执行者推进")
        next_snapshot = ResearchSnapshot(
            snapshot_id=current.snapshot_id, task_id=current.task_id, event_id=current.event_id,
            event_revision=current.event_revision, news_cutoff_at=current.news_cutoff_at,
            verification_cutoff_at=current.verification_cutoff_at, context_sha256=current.context_sha256,
            prompt_contract_revision=current.prompt_contract_revision,
            model_parameters_sha256=current.model_parameters_sha256, research_status=research_status,
            execution_status="ok", revision=current.revision + 1,
            created_at=current.created_at, updated_at=updated_at,
            admission_context=current.admission_context,
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
             _json(next_snapshot.to_dict()), next_snapshot.created_at, updated_at),
        )
        conn.execute(
            "INSERT INTO k10_research_round_results("
            "snapshot_id,revision,input_sha256,input_packet_json,result_json,context_results_json,tool_evidence_json,created_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            (snapshot_id, next_snapshot.revision, input_sha256, _json(packet), _json(clean_result),
             _json(contexts), _json(tools), updated_at),
        )
    return next_snapshot


def mark_research_round_failed(
    *, snapshot_id: str, expected_revision: int, input_packet: Mapping[str, Any],
    safe_error_code: str, updated_at: str, db_path: Path,
    lease_guard: LeaseGuard | None = None,
) -> ResearchSnapshot:
    """Durably terminalize one malformed B78 receipt without a legacy stage row.

    The provider's paid raw reply remains in the model-operation checkpoint.  This
    appends only the program's safe disposition and marks the immutable snapshot
    failed, so a reclaim cannot reinterpret an already rejected reply as a new
    research admission or issue another request.
    """
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
        raise K10Conflict("expected_revision 无效")
    terminal = ResearchRoundResult(safe_error_code=safe_error_code)
    _aware_instant(updated_at, "updated_at")
    packet = _safe_json(input_packet, field="研究资料包")
    clean_result = _safe_json(terminal.to_dict(), field="研究轮次失败结果")
    input_sha256 = sha256(_json(packet).encode("utf-8")).hexdigest()
    with write_connection(db_path) as conn:
        require_schema(conn)
        _guard(lease_guard)
        latest = _latest_snapshot_row(conn, snapshot_id)
        if latest is None:
            raise K10Conflict("研究快照不存在")
        current = _snapshot(latest[1])
        if current.revision != expected_revision:
            replay = conn.execute(
                "SELECT input_sha256,result_json,context_results_json,tool_evidence_json "
                "FROM k10_research_round_results WHERE snapshot_id=? AND revision=?",
                (snapshot_id, current.revision),
            ).fetchone()
            if (replay is not None and replay[0] == input_sha256 and replay[1] == _json(clean_result)
                    and replay[2] == "[]" and replay[3] == "[]"
                    and current.execution_status == "failed"):
                return current
            raise K10Conflict("研究快照已被其他执行者推进")
        next_snapshot = ResearchSnapshot(
            snapshot_id=current.snapshot_id, task_id=current.task_id, event_id=current.event_id,
            event_revision=current.event_revision, news_cutoff_at=current.news_cutoff_at,
            verification_cutoff_at=current.verification_cutoff_at, context_sha256=current.context_sha256,
            prompt_contract_revision=current.prompt_contract_revision,
            model_parameters_sha256=current.model_parameters_sha256, research_status=current.research_status,
            execution_status="failed", revision=current.revision + 1,
            created_at=current.created_at, updated_at=updated_at,
            admission_context=current.admission_context,
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
             _json(next_snapshot.to_dict()), next_snapshot.created_at, updated_at),
        )
        conn.execute(
            "INSERT INTO k10_research_round_results("
            "snapshot_id,revision,input_sha256,input_packet_json,result_json,context_results_json,tool_evidence_json,created_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            (snapshot_id, next_snapshot.revision, input_sha256, _json(packet), _json(clean_result),
             "[]", "[]", updated_at),
        )
    return next_snapshot


def load_research_round_state(*, snapshot_id: str, db_path: Path) -> dict[str, Any] | None:
    """Read B78 direct-round state only; historical stage rows stay read-only."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        latest = _latest_snapshot_row(conn, snapshot_id)
        if latest is None:
            return None
        snapshot = _snapshot(latest[1])
        rows = conn.execute(
            "SELECT revision,input_sha256,input_packet_json,result_json,context_results_json,tool_evidence_json,created_at "
            "FROM k10_research_round_results WHERE snapshot_id=? ORDER BY revision",
            (snapshot_id,),
        ).fetchall()
    rounds: list[dict[str, Any]] = []
    for row in rows:
        try:
            input_packet, result = json.loads(row[2]), json.loads(row[3])
            contexts, tools = json.loads(row[4]), json.loads(row[5])
            if (not isinstance(input_packet, Mapping)
                    or sha256(_json(input_packet).encode("utf-8")).hexdigest() != row[1]
                    or not isinstance(contexts, list) or not isinstance(tools, list)):
                raise ValueError("round integrity")
            typed = ResearchRoundResult.from_dict(result)
            clean_packet = _safe_json(input_packet, field="B78 研究资料包")
            clean_contexts = _safe_json(contexts, field="B78 本地回读结果")
            clean_tools = _safe_json(tools, field="B78 本地补查证据")
            if (_json(clean_packet) != row[2] or _json(typed.to_dict()) != row[3]
                    or _json(clean_contexts) != row[4] or _json(clean_tools) != row[5]):
                raise ValueError("round canonical JSON")
            rounds.append({"revision": int(row[0]), "inputSha256": row[1], "inputPacket": dict(clean_packet),
                           "result": typed.to_dict(), "contextResults": list(clean_contexts),
                           "toolEvidence": list(clean_tools), "createdAt": row[6]})
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise K10Conflict("B78 研究轮次状态不可读取") from exc
    return {"snapshot": snapshot, "rounds": rounds}


def _evidence_update(value: Mapping[str, Any]) -> tuple[str, str, int, str, str, Mapping[str, Any]]:
    value = validate_evidence_update(value)
    claim_id = value["claimId"]
    relation = value["relation"]
    location = value["location"]
    document_id, revision = _ref(value["sourceRef"], "evidenceUpdates.sourceRef")
    return claim_id, document_id, revision, relation, location, value["applicability"]


def _distinct(values: Sequence[Any], key: Callable[[Any], str], name: str) -> None:
    seen: set[str] = set()
    for item in values:
        value = key(item)
        if value in seen:
            raise ResearchContractError(f"{name} ID 重复")
        seen.add(value)


def _prepared_stage(stage_result: ResearchStageResult) -> tuple[list[dict[str, Any]], list[tuple[str, str, int, str, str, Mapping[str, Any]]]]:
    """Validate a stage before a transaction can append any part of a receipt."""
    _distinct(stage_result.claims, lambda item: item.claim_id, "claim")
    _distinct(stage_result.questions, lambda item: item.question_id, "question")
    _distinct(stage_result.query_paths, lambda item: item.path_id, "query path")
    _distinct(stage_result.fulltext_requests, lambda item: item.request_id, "fulltext request")
    clean_assessments = [validate_company_assessment(item) for item in stage_result.company_assessments]
    _distinct(clean_assessments, lambda item: item["companyCode"], "company assessment")
    updates = [_evidence_update(group[0]) for group in group_evidence_updates(stage_result.evidence_updates)]
    return clean_assessments, updates


def _append_research_stage(conn, *, current: ResearchSnapshot, research_status: str,
                           execution_status: str, stage_result: ResearchStageResult,
                           input_sha256: str, updated_at: str,
                           verification_cutoff_at: str | None,
                           prepared: tuple[list[dict[str, Any]], list[tuple[str, str, int, str, str, Mapping[str, Any]]]]) -> ResearchSnapshot:
    """Append one old-schema stage inside the caller's open transaction."""
    updated_instant = _aware_instant(updated_at, "updated_at")
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
        admission_context=current.admission_context,
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
        (next_snapshot.snapshot_id, next_snapshot.revision, stage_result.action, input_sha256, _json(stage_result.to_dict()),
         stage_result.safe_error_code, updated_at),
    )
    clean_assessments, updates = prepared
    for claim in stage_result.claims:
        doc, doc_revision = _ref(claim.source_ref, "claim.sourceRef")
        conn.execute(
            "INSERT INTO k10_research_claims(snapshot_id,snapshot_revision,claim_id,document_id,document_revision,"
            "claim_kind,novelty,verification_status,claim_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (next_snapshot.snapshot_id, next_snapshot.revision, claim.claim_id, doc, doc_revision, claim.kind, claim.novelty,
             claim.verification_status, _json(claim.to_dict()), updated_at),
        )
    for claim_id, doc, doc_revision, relation, location, applicability in updates:
        conn.execute(
            "INSERT INTO k10_research_evidence_links(snapshot_id,snapshot_revision,claim_id,document_id,document_revision,"
            "relation,location,applicability_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (next_snapshot.snapshot_id, next_snapshot.revision, claim_id, doc, doc_revision, relation, location,
             _json(dict(applicability)), updated_at),
        )
    for question in stage_result.questions:
        conn.execute(
            "INSERT INTO k10_research_questions(snapshot_id,snapshot_revision,question_id,state,question_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (next_snapshot.snapshot_id, next_snapshot.revision, question.question_id, question.state, _json(question.to_dict()), updated_at),
        )
    for path in stage_result.query_paths:
        conn.execute(
            "INSERT INTO k10_research_query_paths(snapshot_id,snapshot_revision,path_id,question_id,state,path_json,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (next_snapshot.snapshot_id, next_snapshot.revision, path.path_id, path.question_id, path.state, _json(path.to_dict()), updated_at),
        )
    for request in stage_result.fulltext_requests:
        doc, doc_revision = _ref(request.source_ref, "fulltextRequest.sourceRef")
        conn.execute(
            "INSERT INTO k10_research_fulltext_requests(snapshot_id,snapshot_revision,request_id,question_id,document_id,"
            "document_revision,state,request_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (next_snapshot.snapshot_id, next_snapshot.revision, request.request_id, request.question_id, doc, doc_revision,
             request.state, _json(request.to_dict()), updated_at),
        )
    for assessment in clean_assessments:
        conn.execute(
            "INSERT INTO k10_research_company_assessments(snapshot_id,snapshot_revision,company_code,role,rank,"
            "disclosure_json,assessment_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (next_snapshot.snapshot_id, next_snapshot.revision, assessment["companyCode"], assessment["role"], assessment["rank"],
             _json(assessment["evidenceDisclosure"]), _json(assessment), updated_at),
        )
    return next_snapshot


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
        # The index has one row per claim/source relation. The immutable stage
        # result retains every validated location; readers expand that group.
        updates = [_evidence_update(group[0]) for group in group_evidence_updates(stage_result.evidence_updates)]
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
            admission_context=current.admission_context,
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


def advance_research_snapshot_batch(
    *, snapshot_id: str, expected_revision: int, research_status: str, execution_status: str,
    stage_results: Sequence[tuple[ResearchStageResult, str]], updated_at: str, db_path: Path,
    lease_guard: LeaseGuard | None = None, verification_cutoff_at: str | None = None,
) -> ResearchSnapshot:
    """Atomically derive old actions from one already-durable B76 model receipt.

    Each pair is an existing ``ResearchStageResult`` plus its deterministic
    sub-operation input identity.  Validation happens before the write
    transaction; a crash cannot leave only half a receipt-derived plan/closure
    for recovery to reinterpret or re-POST.
    """
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
        raise K10Conflict("expected_revision 无效")
    if len(stage_results) != 2:
        raise K10Conflict("合并研究必须派生两个旧阶段")
    prepared: list[tuple[ResearchStageResult, str, Any]] = []
    for stage, input_sha256 in stage_results:
        if not isinstance(stage, ResearchStageResult) or not isinstance(input_sha256, str) or len(input_sha256) != 64:
            raise K10Conflict("合并研究派生阶段无效")
        prepared.append((stage, input_sha256, _prepared_stage(stage)))
    with write_connection(db_path) as conn:
        require_schema(conn)
        _guard(lease_guard)
        latest = _latest_snapshot_row(conn, snapshot_id)
        if latest is None:
            raise K10Conflict("研究快照不存在")
        current = _snapshot(latest[1])
        if current.revision != expected_revision:
            rows = conn.execute(
                "SELECT action,input_sha256 FROM k10_research_stage_results "
                "WHERE snapshot_id=? AND revision>? AND revision<=? ORDER BY revision",
                (snapshot_id, expected_revision, expected_revision + len(prepared)),
            ).fetchall()
            expected = [(stage.action, digest) for stage, digest, _ in prepared]
            if current.revision == expected_revision + len(prepared) and [(row[0], row[1]) for row in rows] == expected:
                return current
            raise K10Conflict("研究快照已被其他执行者推进")
        for index, (stage, input_sha256, stage_prepared) in enumerate(prepared):
            current = _append_research_stage(
                conn, current=current,
                research_status=research_status if index == len(prepared) - 1 else current.research_status,
                execution_status=execution_status, stage_result=stage, input_sha256=input_sha256,
                updated_at=updated_at, verification_cutoff_at=verification_cutoff_at if index == 0 else None,
                prepared=stage_prepared,
            )
    return current


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
        "SELECT e.claim_id,e.document_id,e.document_revision,e.relation,e.location,e.applicability_json,e.snapshot_revision,s.result_json "
        "FROM k10_research_evidence_links e LEFT JOIN k10_research_stage_results s "
        "ON s.snapshot_id=e.snapshot_id AND s.revision=e.snapshot_revision "
        "WHERE e.snapshot_id=? AND e.snapshot_revision<=? ORDER BY e.snapshot_revision ASC",
        (snapshot_id, revision),
    ).fetchall()
    stages: dict[int, Mapping[str, Any]] = {}
    latest: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for claim_id, document_id, document_revision, relation, location, applicability, source_revision, raw in rows:
        key = str(claim_id), str(document_id), int(document_revision)
        indexed = {
            "claimId": str(claim_id), "sourceRef": {"documentId": str(document_id), "revision": int(document_revision)},
            "relation": str(relation), "location": str(location), "applicability": json.loads(applicability),
        }
        if source_revision not in stages:
            stages[source_revision] = json.loads(raw) if raw else {}
        matches = [item for item in stages[source_revision].get("evidenceUpdates", ())
                   if (item["claimId"], item["sourceRef"]["documentId"], item["sourceRef"]["revision"]) == key]
        groups = group_evidence_updates(matches)
        latest[key] = groups[0] if groups else [indexed]
    return [item for key in sorted(latest) for item in latest[key]]


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
    include_historical_sources: bool = False, same_task_frozen: bool = False,
    verification_cutoff: datetime | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Return a source timing decision without selecting any article body."""
    document_id, revision = _ref(ref, "sourceRef")
    provenance = {"sourceRef": {"documentId": document_id, "revision": revision}}
    if same_task_frozen and (document_id, revision) not in allowed:
        return False, {**provenance, "reason": "not_same_task_frozen_input"}
    if (document_id, revision) not in allowed and not include_historical_sources:
        return False, {**provenance, "reason": "not_current_input"}
    if (document_id, revision) not in allowed:
        newer = conn.execute("SELECT fetched_at FROM k10_source_document_versions WHERE document_id=? AND revision>?",
                             (document_id, revision)).fetchall()
        if any(_aware_instant(row[0], "newer source fetched_at") <= cutoff for row in newer):
            return False, {**provenance, "reason": "source_revision_superseded"}
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
    effective_verification_cutoff = verification_cutoff or cutoff
    if same_task_frozen:
        # A normal collection may complete moments after the nominal report
        # cutoff.  Its exact version is still usable only when it is an input
        # frozen for this receiving task and the provider fact snapshot existed
        # before the verification action's cutoff.
        if fetched > effective_verification_cutoff:
            return False, {**provenance, "reason": "fetched_after_verification_cutoff", "fetchedAt": str(fetched_at)}
        return True, {**provenance, "publishedAt": str(published_at), "fetchedAt": str(fetched_at),
                      "sameTaskFrozenInput": True}
    if fetched > cutoff:
        return False, {**provenance, "reason": "fetched_after_cutoff", "fetchedAt": str(fetched_at)}
    return True, {**provenance, "publishedAt": str(published_at), "fetchedAt": str(fetched_at)}


def load_prior_research_evidence(
    *, task_id: str, input_source_refs: Sequence[Mapping[str, Any]], news_cutoff_at: str,
    verification_cutoff_at: str, prompt_contract_revision: str, model_parameters_sha256: str,
    db_path: Path, canonical_key: str | None = None, event_id: str | None = None,
    include_historical_sources: bool = False, exclude_snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Load time-applicable evidence from prior and related task snapshots.

    This is intentionally a source-fact/relation cache, not a comparison cache:
    it never returns assessments, ranks, market context, prompts, or article
    bodies.  A caller must choose exactly one stable event identity and supplies
    its currently frozen source refs. The caller snapshot is excluded; another
    event in the same task can contribute overlapping source facts, but not
    company relations. Anything
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
            "WHERE (event_id=? AND task_id<>?) OR (? IS NOT NULL AND task_id=? AND snapshot_id<>?) GROUP BY snapshot_id"
            ") latest ON latest.snapshot_id=r.snapshot_id AND latest.revision=r.revision "
            "WHERE r.prompt_contract_revision=? AND r.model_parameters_sha256=? "
            "ORDER BY r.updated_at DESC,r.snapshot_id DESC",
            (source_event_id, task_id, exclude_snapshot_id, task_id, exclude_snapshot_id, prompt_contract_revision, model_parameters_sha256),
        ).fetchall()
        claims: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        isolated: list[dict[str, Any]] = []
        seen_claims: set[tuple[str, str, int]] = set()
        seen_relations: set[tuple[str, str]] = set()
        relations_selected = False
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
            if prior_task_id == task_id:
                original_refs = conn.execute('SELECT source_refs_json FROM k10_event_revisions WHERE event_id=? AND revision=?',
                    (prior_event_id, source_event_revision)).fetchone()
                # Shared facts require an actual source-version overlap, never
                # a coincidental company or a similar event headline.
                if not original_refs or not ({_ref(ref, 'shared source') for ref in json.loads(original_refs[0])} & allowed):
                    continue
            for claim in _latest_payloads(conn, "k10_research_claims", "claim_id", "claim_json", str(snapshot_id), int(revision)):
                ref = claim.get("sourceRef")
                try:
                    eligible, timing = _eligible_prior_source(conn, ref=ref, allowed=allowed, cutoff=news_cutoff,
                        include_historical_sources=include_historical_sources,
                        same_task_frozen=prior_task_id == task_id,
                        verification_cutoff=verification_cutoff)
                except ResearchContractError:
                    eligible, timing = False, {"sourceRef": ref, "reason": "invalid_source_ref"}
                if not eligible:
                    isolated.append({**timing, "kind": "claim", "provenance": snapshot_provenance})
                    continue
                key = (str(claim.get("text")), timing["sourceRef"]["documentId"], timing["sourceRef"]["revision"])
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
            if relations_selected or prior_task_id == task_id:
                continue
            stage_rows = conn.execute(
                "SELECT result_json FROM k10_research_stage_results WHERE snapshot_id=? AND revision<=? ORDER BY revision DESC",
                (snapshot_id, revision),
            ).fetchall()
            for (result_json,) in stage_rows:
                result = json.loads(result_json)
                conclusion = result.get("conclusion") if isinstance(result, Mapping) else None
                mappings = conclusion.get("companyMappings") if isinstance(conclusion, Mapping) else None
                if not isinstance(mappings, Sequence) or isinstance(mappings, (str, bytes)):
                    continue
                # Each close result is a complete relation set. Corrections
                # and an explicit empty set supersede all older guesses.
                relations_selected = True
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
                            eligible, timing = _eligible_prior_source(conn, ref=ref, allowed=allowed, cutoff=verification_cutoff,
                                include_historical_sources=include_historical_sources,
                                same_task_frozen=prior_task_id == task_id,
                                verification_cutoff=verification_cutoff)
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
                break
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


def _current_direct_round(conn, snapshot: ResearchSnapshot) -> tuple[ResearchRoundResult, Mapping[str, Any]] | None:
    """Decode the current B78 result and its frozen visible packet.

    Direct B78 state is read as-is instead of being re-projected into retired
    stage tables.  The packet is returned only to reconstruct persisted
    question coverage; it has already passed the safe JSON boundary.
    """
    row = conn.execute(
        "SELECT input_sha256,input_packet_json,result_json,context_results_json,tool_evidence_json "
        "FROM k10_research_round_results WHERE snapshot_id=? AND revision=?",
        (snapshot.snapshot_id, snapshot.revision),
    ).fetchone()
    if row is None:
        return None
    try:
        packet, result, contexts, tools = json.loads(row[1]), json.loads(row[2]), json.loads(row[3]), json.loads(row[4])
        if (not isinstance(packet, Mapping)
                or sha256(_json(packet).encode("utf-8")).hexdigest() != row[0]
                or not isinstance(contexts, list) or not isinstance(tools, list)):
            raise ValueError("round integrity")
        typed = ResearchRoundResult.from_dict(result)
        clean_packet = _safe_json(packet, field="B78 研究资料包")
        clean_contexts = _safe_json(contexts, field="B78 本地回读结果")
        clean_tools = _safe_json(tools, field="B78 本地补查证据")
        if (not isinstance(clean_packet, Mapping) or _json(clean_packet) != row[1]
                or _json(typed.to_dict()) != row[2]
                or _json(clean_contexts) != row[3] or _json(clean_tools) != row[4]):
            raise ValueError("round canonical JSON")
        return typed, clean_packet
    except (TypeError, ValueError, json.JSONDecodeError, ResearchContractError) as exc:
        raise K10Conflict("B78 研究轮次状态不可读取") from exc


def _direct_questions(packet: Mapping[str, Any], result: ResearchRoundResult) -> tuple[Question, ...]:
    """Merge a round's answer updates into the frozen visible question packet."""
    raw = packet.get("questions", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise K10Conflict("B78 研究资料包问题不可读取")
    merged: dict[str, Question] = {}
    try:
        for item in raw:
            question = Question.from_dict(item)
            merged[question.question_id] = question
        for question in result.questions:
            merged[question.question_id] = question
    except ResearchContractError as exc:
        raise K10Conflict("B78 研究资料包问题不可读取") from exc
    return tuple(merged[key] for key in sorted(merged))


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
            direct_state = _current_direct_round(conn, snapshot)
            direct = None if direct_state is None else direct_state[0]
            assessments = (direct.company_assessments if direct is not None else
                           _latest_payloads(conn, "k10_research_company_assessments", "company_code", "assessment_json",
                                            snapshot.snapshot_id, snapshot.revision))
            safe_error_code = (direct.safe_error_code if direct is not None else
                               _safe_error_code(conn, snapshot.snapshot_id, snapshot.revision))
            for assessment in assessments:
                values.append({"snapshotId": snapshot.snapshot_id, "snapshotRevision": snapshot.revision,
                               "eventId": snapshot.event_id, "eventRevision": snapshot.event_revision,
                               "researchStatus": snapshot.research_status, "executionStatus": snapshot.execution_status,
                               "safeErrorCode": safe_error_code, **dict(assessment)})
    return values


def research_summary_for_scan(*, scan_id: str, db_path: Path) -> dict[str, Any] | None:
    """Safe aggregate projection for the API; prompt text, bodies and URLs never appear."""
    with read_connection(db_path) as conn:
        require_schema(conn)
        task_id = _task_for_scan(conn, scan_id)
        if task_id is None:
            return None
        # A provider can fail in title triage before any event exists, so there
        # is no per-event research snapshot to carry the failure.  That is
        # still an execution failure of this scan, never an empty/clean
        # research result.  The immutable scan binding supplies the task used
        # for this summary; historical scans without that binding remain
        # ``None`` above.
        task_row = conn.execute("SELECT status FROM k10_tasks WHERE task_id=?", (task_id,)).fetchone()
        task_execution_failed = task_row is not None and str(task_row[0]) == "failed"
        snapshots = _latest_snapshots_for_task(conn, task_id)
        research_counts: dict[str, int] = {}
        execution_counts: dict[str, int] = {}
        safe_failure_counts: dict[str, int] = {}
        question_counts = {state: 0 for state in ("open", "answered", "blocked", "abandoned")}
        company_counts = {role: 0 for role in ("primary", "alternative", "tied", "pending", "excluded")}
        direct_comparisons: set[str] = set()
        for snapshot in snapshots:
            research_counts[snapshot.research_status] = research_counts.get(snapshot.research_status, 0) + 1
            execution_counts[snapshot.execution_status] = execution_counts.get(snapshot.execution_status, 0) + 1
            direct_state = _current_direct_round(conn, snapshot)
            direct = None if direct_state is None else direct_state[0]
            safe_error_code = (direct.safe_error_code if direct is not None else
                               _safe_error_code(conn, snapshot.snapshot_id, snapshot.revision))
            if safe_error_code is not None:
                safe_failure_counts[safe_error_code] = safe_failure_counts.get(safe_error_code, 0) + 1
            questions = (_direct_questions(direct_state[1], direct) if direct_state is not None else
                         _latest_payloads(conn, "k10_research_questions", "question_id", "question_json",
                                          snapshot.snapshot_id, snapshot.revision))
            assessments = (direct.company_assessments if direct is not None else
                           _latest_payloads(conn, "k10_research_company_assessments", "company_code", "assessment_json",
                                            snapshot.snapshot_id, snapshot.revision))
            for question in questions:
                state = question.state if isinstance(question, Question) else question["state"]
                question_counts[state] += 1
            for assessment in assessments:
                company_counts[assessment["role"]] += 1
            if direct is not None and direct.comparison is not None and direct.company_assessments:
                direct_comparisons.add(snapshot.snapshot_id)
    return {
        "scanId": scan_id, "taskId": task_id, "eventCount": len(snapshots),
        "questionCounts": question_counts, "companyCounts": {**company_counts,
            "comparable": company_counts["primary"] + company_counts["alternative"] + company_counts["tied"]},
        "researchStatusCounts": research_counts, "executionStatusCounts": execution_counts,
        "safeFailureCounts": safe_failure_counts,
        "comparisonComplete": bool(snapshots) and all(
            item.research_status == "comparison_complete" or item.snapshot_id in direct_comparisons
            for item in snapshots),
        "executionFailed": task_execution_failed or any(item.execution_status == "failed" for item in snapshots),
    }


__all__ = [
    "append_research_round", "advance_research_snapshot", "advance_research_snapshot_batch", "create_research_snapshot", "find_research_snapshot", "list_research_assessments", "list_research_claims", "mark_research_round_failed",
    "list_research_fulltext_requests", "list_research_query_paths", "list_research_questions", "load_prior_research_evidence", "load_research_round_state", "read_research_snapshot", "read_research_state",
    "research_summary_for_scan",
]
