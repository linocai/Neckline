"""Optional Tavily evidence for K10 event verification; never a market-wide source."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from neckline.search.tavily import TavilySearchClient, TavilySearchResponse, TavilyExtractResponse
from neckline.settings_store import get_tavily_api_key
from neckline.llm.usage import record as record_usage
from neckline.llm.base import SearchHit

from .discovery import DiscoveryDocument, EventDraft, ProviderThrottleYield
from .source_metadata import PublicationMetadataResolver
from .research_material import admit_material
from .types import DocumentVersion
from .schema import read_connection, require_schema
from . import store
from .verification_checkpoints import VerificationCheckpointError, VerificationCheckpointStore


@dataclass(frozen=True)
class VerificationEvidenceBundle:
    state: str
    documents: tuple[DiscoveryDocument, ...]
    eligible_documents: tuple[DiscoveryDocument, ...]
    coverage: Mapping[str, Any]


class SearchClient(Protocol):
    def search(self, query: str) -> TavilySearchResponse: ...


def _text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("核验取得时间必须带时区")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _published(value: str) -> tuple[str | None, str]:
    raw = (value or "").strip()
    if len(raw) == 10 and raw[4:5] == "-" and raw[7:8] == "-":
        try:
            return datetime.fromisoformat(raw + "T00:00:00+00:00").isoformat(timespec="seconds"), "date"
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.isoformat(timespec="seconds"), "exact"
    except ValueError:
        pass
    try:
        # Tavily uses RFC 2822 for some real publisher timestamps.  A date
        # without a concrete offset is not safe for an intraday cutoff.
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is not None:
            return parsed.isoformat(timespec="seconds"), "exact"
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    return None, "unknown"


def _aware_instant(value: str | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


class TavilyEvidenceGateway:
    """Question-bound evidence requests with durable, finite network retries."""
    source_key = "tavily_verification"

    def __init__(self, *, db_path: Path, client: SearchClient | None = None,
                 clock: Callable[[], datetime] | None = None,
                 metadata_resolver: PublicationMetadataResolver | None = None, task_id: str | None = None,
                 leaseguard: Callable[[], None] | None = None,
                 checkpoint_store: VerificationCheckpointStore | None = None,
                 network_max_attempts: int | None = None, lease_owner: str | None = None,
                 new_external_admission_guard: Callable[[], None] | None = None) -> None:
        self.db_path = db_path
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.metadata_resolver = metadata_resolver
        if checkpoint_store is not None and task_id is not None and checkpoint_store.task_id != task_id:
            raise ValueError("Tavily 核验 task_id 与检查点不一致")
        self.checkpoint_store = checkpoint_store or (
            VerificationCheckpointStore(db_path=db_path, task_id=task_id, leaseguard=leaseguard, lease_owner=lease_owner) if task_id else None
        )
        self.context_protocol = None
        if self.checkpoint_store:
            self.context_protocol = store.task_execution_input(task_id=self.checkpoint_store.task_id, db_path=db_path)['checkpoint'].get('contextProtocol')
        self.network_max_attempts = network_max_attempts
        self.new_external_admission_guard = new_external_admission_guard
        self.requests, self.credits = self.checkpoint_store.attempt_snapshot() if self.checkpoint_store else (0, 0)

    def _reserve_search(self, *, item_key: str, input_sha256: str, attempt: int) -> tuple[str | None, str | None]:
        """Start one external attempt after the event checkpoint claim."""
        if self.checkpoint_store is None:
            return None, None
        key = "tavily:" + sha256(f"{self.checkpoint_store.task_id}\x1f{item_key}\x1f{attempt}".encode()).hexdigest()
        admitted = store.begin_external_attempt(
            task_id=self.checkpoint_store.task_id, item_key=item_key, stage="search", attempt_key=key,
            input_sha256=input_sha256, started_at=_text(self.clock()), db_path=self.db_path,
        )
        if admitted.get("state") != "started":
            return None, {
                "paused": "execution_paused", "not_configured": "execution_not_configured",
                "pending_outcome": "tavily_request_outcome_unknown", "retired": "execution_retired_by_user",
                "reused": "tavily_attempt_reused",
                "terminal": str(admitted.get("reason") or "insufficient_balance"),
            }.get(str(admitted.get("state")), "tavily_attempt_admission_failed")
        attempt_id = admitted.get("attemptId")
        return (str(attempt_id), None) if isinstance(attempt_id, str) else (None, "tavily_attempt_admission_failed")

    def _settle_search(self, *, attempt_id: str, response: TavilySearchResponse | TavilyExtractResponse | None,
                       obtained_at: datetime | None = None) -> None:
        # Preserve account/authorization terminal causes from the provider's
        # raw response before an older compatibility mapper can collapse them
        # into a local coverage gap. The durable ledger must retain this
        # distinction: a 432/433 cannot be repaired by continuing other event
        # searches, while an ordinary unavailable response may be local.
        code = self._terminal_response_code(response) or self._response_code(response)
        outcome = "unknown" if code in {"tavily_request_outcome_unknown", "tavily_extract_outcome_unknown"} else ("succeeded" if response.ok else "failed")
        usage = None if response is None else {
            "promptTokens": None, "completionTokens": None, "totalTokens": None,
            "searchRequests": 1, "searchCredits": response.credits if isinstance(response.credits, int) else None,
        }
        if response is not None and (response.ok or response.reason == "tavily_fulltext_unavailable"):
            received_at = _text(obtained_at or self.clock())
            with read_connection(self.db_path) as conn:
                row = conn.execute("SELECT input_sha256 FROM k10_external_attempts WHERE attempt_id=?",
                                   (attempt_id,)).fetchone()
            if row is None:
                raise VerificationCheckpointError("verification_attempt_missing")
            store.settle_tavily_response_with_receipt(
                attempt_id=attempt_id, input_sha256=row[0],
                payload={"receiptVersion": "k10-tavily-response-b78",
                         "operation": "extract" if isinstance(response, TavilyExtractResponse) else "search",
                         "obtainedAt": received_at, "response": asdict(response)},
                usage=usage, settled_at=received_at, outcome=outcome,
                error_code=None if response.ok else "tavily_fulltext_unavailable", db_path=self.db_path,
            )
            return
        receipt = None
        if response is not None and not response.ok and response.reason != "tavily_fulltext_unavailable":
            receipt = {}
            if code == "rate_limited":
                profile = store.task_execution_profile(task_id=self.checkpoint_store.task_id, db_path=self.db_path)
                policy = profile["payload"]["discovery"]
                delay = response.retry_after_seconds
                if delay is None:
                    with read_connection(self.db_path) as conn:
                        attempts = conn.execute("SELECT network_attempt_count FROM k10_execution_item_checkpoints c "
                            "JOIN k10_external_attempts a ON c.task_id=a.task_id AND c.item_key=a.item_key "
                            "WHERE a.attempt_id=? AND c.stage='tavily_evidence'", (attempt_id,)).fetchone()[0]
                    delay = policy["retryBackoffSeconds"][min(attempts - 1, len(policy["retryBackoffSeconds"]) - 1)]
                from .delivery import is_current_runtime_contract
                with read_connection(self.db_path) as conn:
                    task_payload = conn.execute("SELECT payload_json FROM k10_tasks WHERE task_id=?",
                                                (self.checkpoint_store.task_id,)).fetchone()
                payload = json.loads(task_payload[0])
                if is_current_runtime_contract(payload.get("runtimeContract")):
                    deadline = _aware_instant(payload.get("deliveryDeadlineAt"))
                    remaining = (deadline - self.clock()).total_seconds() if deadline else None
                else:
                    # Historical tasks keep their original lifetime; a new
                    # evening task has no six-hour business deadline.
                    remaining = policy["completionDeadlineSeconds"]
                if remaining is not None and delay >= remaining:
                    code = "network_attempts_exhausted"
                else:
                    receipt = {"retryAt": _text(self.clock() + timedelta(seconds=delay))}
        store.settle_external_attempt(attempt_id=attempt_id, outcome=outcome, usage=usage,
                                      error_code=None if response is None or response.ok else code,
                                      verification_failure=receipt,
                                      settled_at=_text(self.clock()), db_path=self.db_path)

    @staticmethod
    def _response_from_receipt(receipt: Mapping[str, Any], *, operation: str):
        """Decode a hash/ownership-verified private receipt without network access."""
        try:
            payload = receipt["payload"]
            if payload["receiptVersion"] != "k10-tavily-response-b78" or payload["operation"] != operation:
                raise ValueError("unexpected receipt contract")
            obtained_at = _aware_instant(payload["obtainedAt"])
            if obtained_at is None or _text(obtained_at) != receipt["receivedAt"]:
                raise ValueError("receipt time mismatch")
            response = dict(payload["response"])
            if operation == "search":
                response["hits"] = tuple(SearchHit(**hit) for hit in response["hits"])
                restored = TavilySearchResponse(**response)
            else:
                restored = TavilyExtractResponse(**response)
            if not restored.ok and restored.reason != "tavily_fulltext_unavailable":
                raise ValueError("receipt has no known paid result")
            return restored, obtained_at
        except (KeyError, TypeError, ValueError) as exc:
            raise VerificationCheckpointError("tavily_response_receipt_invalid") from exc

    @staticmethod
    def _response_code(response) -> str:
        if response is None:
            return "tavily_request_outcome_unknown"
        if response.reason == "tavily_extract_outcome_unknown" or response.reason.endswith(("Timeout", "TimeoutError", "NetworkError", "ConnectError", "ReadError", "WriteError", "RemoteProtocolError")):
            return "tavily_request_outcome_unknown"
        # Tavily uses 432 for a plan cap and 433 for the PAYGO cap. Both
        # require account action, just like 402; retries cannot restore quota.
        return {"tavily_http_402": "insufficient_balance",
                "tavily_http_432": "insufficient_balance",
                "tavily_http_433": "insufficient_balance",
                "tavily_http_401": "provider_authorization_failed",
                "tavily_http_403": "provider_authorization_failed",
                "tavily_http_429": "rate_limited"}.get(
            response.reason, "tavily_response_unavailable")

    @staticmethod
    def _terminal_response_code(response) -> str | None:
        """Read non-retryable provider state without relying on a compatibility mapper."""
        if response is None:
            return None
        return {
            "tavily_http_402": "insufficient_balance",
            "tavily_http_432": "insufficient_balance",
            "tavily_http_433": "insufficient_balance",
            "tavily_http_401": "provider_authorization_failed",
            "tavily_http_403": "provider_authorization_failed",
        }.get(response.reason)

    def _failed_response(self, *, item_key: str, input_sha256: str) -> VerificationEvidenceBundle:
        # The ledger and failure receipt committed together. Re-read without
        # claiming another attempt or losing Retry-After during interruption.
        with read_connection(self.db_path) as conn:
            row = conn.execute("SELECT safe_error_code,result_json,network_attempt_count FROM k10_execution_item_checkpoints "
                "WHERE task_id=? AND item_kind='event' AND item_key=? AND stage='tavily_evidence'",
                (self.checkpoint_store.task_id, item_key)).fetchone()
        code, raw, attempts = row
        receipt = json.loads(raw or "{}")
        if code == "rate_limited" and attempts < self.network_max_attempts:
            raise ProviderThrottleYield(max(0, (datetime.fromisoformat(receipt["retryAt"]) - self.clock()).total_seconds()))
        return self._pending(code or "tavily_response_unavailable")

    def _pending_claim(self, claim) -> VerificationEvidenceBundle:
        if claim.reason == "rate_limited" and claim.result:
            raise ProviderThrottleYield(max(0, (datetime.fromisoformat(claim.result["retryAt"]) - self.clock()).total_seconds()))
        return self._pending(claim.reason or "verification_checkpoint_pending")

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        if not isinstance(value, Mapping):
            raise VerificationCheckpointError("research_query_contract_invalid")
        return value

    def _query_context(self, question: Any, query_path: Any) -> dict[str, Any] | None:
        # Only the explicitly frozen pre-scope protocol keeps its old input
        # identity. A later protocol/build must never fall back to legacy scope.
        scoped = bool(self.context_protocol) and self.context_protocol != "k10-v2-context-3.2.1"
        if question is None and query_path is None:
            # B69 never turns a bare headline into a new paid search.  A new
            # route has to be tied to its persisted event question and typed
            # target; historical snapshots retain their previous fallback.
            if scoped:
                raise VerificationCheckpointError("research_query_scope_invalid")
            return None
        question, path = self._mapping(question), self._mapping(query_path)
        required = ("questionId", "pathId", "query", "intent", "newPathReason",
                    "expectedInformationGain", "expectedJudgmentChange")
        if any(not isinstance(path.get(key), str) or not path[key].strip() for key in required):
            raise VerificationCheckpointError("research_query_contract_invalid")
        if path["questionId"] != question.get("questionId") or len(path["query"].strip()) > 400:
            raise VerificationCheckpointError("research_query_scope_invalid")
        # Historical contexts stay readable and preserve their original
        # checkpoint identity.  Only B69's new protocol can initiate a fresh
        # route, and it must carry the stricter local scope binding.
        if scoped and not self._query_scope_is_valid(question, path):
            raise VerificationCheckpointError("research_query_scope_invalid")
        return {key: path[key] for key in required} | {key: path[key] for key in
            ("targetSource", "purposeKind", "targetRefs", "questionScope") if key in path}

    @staticmethod
    def _query_scope_is_valid(question: Mapping[str, Any], path: Mapping[str, Any]) -> bool:
        """A direct gateway caller gets the same question-bound guard as runtime."""
        purpose = path.get("purposeKind")
        targets = path.get("targetRefs")
        scope = path.get("questionScope")
        if purpose not in {"event_fact", "company_event_link", "counterevidence"}:
            return False
        if not isinstance(targets, list) or not targets or not isinstance(scope, Mapping):
            return False
        claims = {item for item in question.get("claimIds", ()) if isinstance(item, str)}
        companies = {item for item in question.get("companyCodes", ()) if isinstance(item, str)}
        has_company = False
        for target in targets:
            if not isinstance(target, Mapping):
                return False
            if target.get("kind") == "claim" and target.get("claimId") in claims:
                continue
            if target.get("kind") == "company" and target.get("companyCode") in companies:
                has_company = True
                continue
            return False
        if purpose == "company_event_link" and not has_company:
            return False
        if scope.get("questionId") != question.get("questionId"):
            return False
        if sorted(scope.get("claimIds", ())) != sorted(question.get("claimIds", ())):
            return False
        if sorted(scope.get("companyCodes", ())) != sorted(question.get("companyCodes", ())):
            return False
        question_projection = {key: question.get(key) for key in
                               ("questionId", "question", "claimIds", "companyCodes", "supportCondition", "refuteCondition", "missingEvidence")}
        question_sha = sha256(json.dumps(question_projection, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
        if scope.get("questionSha256") != question_sha:
            return False
        base = {key: value for key, value in scope.items() if key != "scopeSha256"}
        expected = sha256(json.dumps(base, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()
        return scope.get("scopeSha256") == expected

    def fetch(self, *, event: EventDraft, retrieved_at: datetime, cutoff_at: datetime,
              cutoff_inclusive: bool = False, question: Any = None,
              query_path: Any = None) -> VerificationEvidenceBundle:
        if cutoff_at.tzinfo is None:
            raise ValueError("核验 cutoff 必须带时区")
        query_context = self._query_context(question, query_path)
        query = query_context["query"].strip() if query_context else event.headline[:400]
        if self.checkpoint_store is not None and (
            not isinstance(self.network_max_attempts, int) or isinstance(self.network_max_attempts, bool)
            or self.network_max_attempts < 1
        ):
            return self._pending("networkMaxAttempts_missing")
        client = self.client
        if client is None:
            key = get_tavily_api_key(db_path=self.db_path)
            client = TavilySearchClient(key) if key else None
        checkpoint_key: str | None = None
        checkpoint_input: str | None = None
        replay = None
        if self.checkpoint_store is not None:
            source_refs = sorted(
                ({"documentId": ref.document_id, "revision": ref.revision} for ref in event.source_refs),
                key=lambda item: (item["documentId"], item["revision"]),
            )
            checkpoint_key = self.checkpoint_store.item_key(
                canonical_key=event.canonical_key, stage_key=event.stage_key, event_state=event.event_state,
                question_id=query_context["questionId"] if query_context else None,
                path_id=query_context["pathId"] if query_context else None,
            )
            checkpoint_input = self.checkpoint_store.input_sha256(
                canonical_key=event.canonical_key, stage_key=event.stage_key, event_state=event.event_state,
                headline=event.headline, event_kind=event.event_kind, facts=event.facts, source_refs=source_refs,
                cutoff_at=_text(cutoff_at), cutoff_inclusive=cutoff_inclusive,
                investigation_path=query_context,
            )
            # The question/path contract above authorizes *this event* to ask
            # for independent evidence.  It deliberately is not part of the
            # physical Tavily request identity: two independently authorized
            # event questions can emit the exact same search wire.  Charging
            # that wire twice would defeat same-task source sharing merely
            # because their local claim or company scope differs.
            #
            # Keep the frozen time boundary in the key.  A later cut-off can
            # legitimately see a different source set, while the returned
            # document revisions themselves remain the durable evidence for
            # every receiving event.
            if self.context_protocol and query_context:
                from .research_context import digest, normalized_query
                physical_wire = {
                    'protocol': self.context_protocol,
                    'operation': 'search',
                    'query': normalized_query(query),
                    'cutoffAt': cutoff_at.astimezone(timezone.utc).isoformat(),
                    'cutoffInclusive': cutoff_inclusive,
                }
                checkpoint_input = digest(physical_wire)
                checkpoint_key = 'tavily:shared:' + checkpoint_input
            if client is None:
                with read_connection(self.db_path) as conn:
                    existing = conn.execute(
                        "SELECT status FROM k10_execution_item_checkpoints WHERE task_id=? "
                        "AND item_kind='event' AND item_key=? AND stage='tavily_evidence' AND input_sha256=?",
                        (self.checkpoint_store.task_id, checkpoint_key, checkpoint_input),
                    ).fetchone()
                    paid = store.load_tavily_response_receipt(task_id=self.checkpoint_store.task_id,
                        item_key=checkpoint_key, input_sha256=checkpoint_input, db_path=self.db_path, conn=conn)
                if paid is None and (existing is None or existing[0] != "completed"):
                    return self._pending("tavily_api_key_missing")
            claim = self.checkpoint_store.claim(item_key=checkpoint_key, input_sha256=checkpoint_input,
                                                network_max_attempts=self.network_max_attempts, updated_at=_text(self.clock()),
                                                new_external_admission_guard=self.new_external_admission_guard)
            self.requests = claim.requests
            if claim.state == "reused":
                return self._for_current_query_context(
                    self._restore_checkpoint_bundle(claim.result), query_context=query_context,
                )
            if claim.state == "pending":
                return self._pending_claim(claim)
            if claim.state == "replay":
                replay = claim.result
        if replay is not None:
            response, obtained_at = self._response_from_receipt(replay, operation="search")
            from .research_context import normalized_query
            if normalized_query(response.query) != normalized_query(query):
                raise VerificationCheckpointError("tavily_response_receipt_query_mismatch")
        else:
            if client is None:
                if self.checkpoint_store is not None:
                    self.checkpoint_store.defer_without_request(
                        item_key=checkpoint_key, input_sha256=checkpoint_input, safe_error_code="tavily_api_key_missing",
                    )
                return self._pending("tavily_api_key_missing")
            attempt_id, blocked = self._reserve_search(
                item_key=checkpoint_key or event.canonical_key, input_sha256=checkpoint_input or "unbound",
                attempt=self.requests,
            )
            if blocked is not None:
                if self.checkpoint_store is not None:
                    if blocked in {"execution_paused", "execution_not_configured", "execution_retired_by_user"}:
                        self.checkpoint_store.defer_without_request(
                            item_key=checkpoint_key, input_sha256=checkpoint_input, safe_error_code=blocked,
                        )
                    else:
                        self.checkpoint_store.fail_retryable(
                            item_key=checkpoint_key, input_sha256=checkpoint_input, safe_error_code=blocked,
                        )
                return self._pending(blocked)
            if self.checkpoint_store is None:
                self.requests += 1
            # The durable gateway owns every billable retry.
            if self.checkpoint_store is not None and isinstance(client, TavilySearchClient):
                client.max_attempts = 1
            response: TavilySearchResponse | None = None
            try:
                response = client.search(query)
            except Exception:
                if self.checkpoint_store is not None:
                    self.checkpoint_store.fail_retryable(item_key=checkpoint_key, input_sha256=checkpoint_input,
                                                         safe_error_code="tavily_request_outcome_unknown")
                    if attempt_id is not None:
                        self._settle_search(attempt_id=attempt_id, response=None)
                    return self._pending("tavily_request_outcome_unknown")
                raise
            obtained_at = self.clock()
            if attempt_id is not None:
                self._settle_search(attempt_id=attempt_id, response=response, obtained_at=obtained_at)
        # The caller's retrieved_at was captured before the blocking request.
        # Persist the actual completion time instead, so fetchedAt never
        # pretends that source material was available before it arrived.
        obtained_at_text = _text(obtained_at)
        # Tavily returns an actual credit count independently of model tokens.  The usage
        # writer is a no-op when the target has no migrated usage table and never creates DDL.
        if self.checkpoint_store is None or not response.ok:
            record_usage(task="discovery", result=None, trade_date=obtained_at.date(), outcome="search_success" if response.ok else "search_failed",
                         tavily_credits=response.credits, searched=True, duration_ms=response.wall_ms,
                         failure_reason=None if response.ok else response.reason, db_path=self.db_path)
        response_reason = response.reason if not self.checkpoint_store else (
            "tavily_response_unavailable" if not response.ok else "coverage_pending"
        )
        coverage: dict[str, Any] = {"provider": "tavily", "query": query, "requests": self.requests,
                                    "state": "pending", "credits": response.credits,
                                    "reason": response_reason}
        if query_context:
            coverage.update({"questionId": query_context["questionId"], "pathId": query_context["pathId"],
                             "operation": "search", "queryIntent": query_context["intent"]})
        if response.credits is not None:
            self.credits += response.credits
        if self.checkpoint_store is not None and not response.ok:
            return self._failed_response(item_key=checkpoint_key, input_sha256=checkpoint_input)
        docs: list[DiscoveryDocument] = []
        eligible: list[DiscoveryDocument] = []
        material_exclusions: dict[str, int] = {}
        for index, hit in enumerate(response.hits):
            excerpt = (hit.content or hit.title).strip()
            if not excerpt:
                continue
            external_id = hit.link or sha256(f"{query}\x1f{index}\x1f{excerpt}".encode()).hexdigest()
            document_id = "doc_" + sha256(f"{self.source_key}\x1f{external_id}".encode()).hexdigest()[:32]
            published_at, precision = _published(hit.publish_date)
            metadata = {"provider": "tavily", "query": query, "title": hit.title, "media": hit.media,
                        "requestId": response.request_id, "publishedDateRaw": hit.publish_date or None,
                        "publisher": hit.media or None, "originStatus": "unknown", "originEvidenceRef": None}
            if query_context:
                metadata["investigationPath"] = dict(query_context)
            final_obtained_at = obtained_at
            # Date-only Tavily values retain their previous conservative
            # behavior.  Only a wholly unparseable timestamp gets a bounded
            # direct-page metadata lookup, and only a precise, zoned result can
            # become intraday evidence.
            if precision == "unknown" and hit.link and self.metadata_resolver is not None:
                resolved = self.metadata_resolver.resolve(hit.link)
                resolved_published = _aware_instant(resolved.published_at)
                resolved_fetched = _aware_instant(resolved.fetched_at)
                if resolved_fetched is not None and resolved_fetched > final_obtained_at:
                    final_obtained_at = resolved_fetched
                metadata["metadataResolution"] = {
                    "provider": "source_metadata", "publishedAt": resolved.published_at,
                    "precision": resolved.precision, "fetchedAt": resolved.fetched_at,
                    "details": dict(resolved.metadata),
                }
                if resolved.precision == "exact" and resolved_published is not None:
                    published_at, precision = resolved_published.isoformat(timespec="seconds"), "exact"
            final_obtained_text = _text(final_obtained_at)
            with read_connection(self.db_path) as conn:
                require_schema(conn)
                first_seen = conn.execute("SELECT MIN(created_at) FROM k10_source_document_versions WHERE document_id=?",
                                          (document_id,)).fetchone()[0]
            metadata["firstSeenAt"] = first_seen or final_obtained_text
            metadata["publicationPrecision"] = precision
            metadata["afterCutoff"] = bool(published_at and precision == "exact" and
                (datetime.fromisoformat(published_at) > cutoff_at or
                 (not cutoff_inclusive and datetime.fromisoformat(published_at) == cutoff_at)))
            provisional = DiscoveryDocument(document_id, 1, published_at, final_obtained_text, None, excerpt, metadata)
            admission = admit_material(provisional)
            metadata["materialAdmission"] = {
                "state": admission.state,
                "reason": admission.reason,
                "contentSha256": admission.content_sha256,
            }
            payload = {"url": hit.link or None, "excerpt": excerpt, "publishedAt": published_at, "precision": precision, "metadata": metadata}
            version: DocumentVersion = store.append_document_version(document_id=document_id, source_key=self.source_key,
                external_id=external_id, canonical_url=hit.link or None,
                content_sha256=sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                published_at=published_at, published_precision=precision, fetched_at=final_obtained_text,
                original_text=None, excerpt=excerpt, fetch_version="tavily-basic-general-v2", metadata=metadata,
                created_at=final_obtained_text, db_path=self.db_path)
            document = DiscoveryDocument(version.document_id, version.revision, published_at, final_obtained_text, None, excerpt, metadata)
            if admission.state == "excluded":
                # Keep the locally classified search document for title/source
                # audit, but never return its snippet as a runtime evidence
                # card, eligible fact, or Extract lead.
                material_exclusions[admission.reason] = material_exclusions.get(admission.reason, 0) + 1
                continue
            docs.append(document)
            # A date-only source can prove it predates an earlier day, never a particular
            # intraday cutoff; unknown and late material stays archived/pending.
            if precision == "exact" and published_at:
                published = datetime.fromisoformat(published_at)
                if published < cutoff_at or (cutoff_inclusive and published == cutoff_at):
                    eligible.append(document)
            elif precision == "date" and published_at and datetime.fromisoformat(published_at).date() < cutoff_at.date():
                eligible.append(document)
        state = "available" if response.ok and response.credits is not None and eligible else "pending"
        reason = "ok" if state == "available" else (
            "tavily_response_unavailable" if self.checkpoint_store and not response.ok
            else (response.reason if not response.ok else "coverage_gap_or_usage_unavailable")
        )
        coverage.update({"state": state, "reason": reason, "documents": len(docs), "eligibleDocuments": len(eligible), "creditsTotal": self.credits,
                         **({"materialExclusions": material_exclusions} if material_exclusions else {})})
        bundle = VerificationEvidenceBundle(state, tuple(docs), tuple(eligible), coverage)
        if self.checkpoint_store is not None:
            safe_coverage = {
                "provider": "tavily", "state": state, "reason": reason, "requestState": "completed",
                "requests": self.requests, "credits": response.credits,
                "creditsTotal": self.credits, "documents": len(docs), "eligibleDocuments": len(eligible),
                **({"materialExclusions": material_exclusions} if material_exclusions else {}),
            }
            if query_context:
                safe_coverage.update({"questionId": query_context["questionId"], "pathId": query_context["pathId"],
                                      "operation": "search", "queryIntent": query_context["intent"]})
            result = {
                "state": state,
                "documentRefs": [self._document_ref(document) for document in docs],
                "eligibleDocumentRefs": [self._document_ref(document) for document in eligible],
                "coverage": safe_coverage,
            }
            self.checkpoint_store.complete(item_key=checkpoint_key, input_sha256=checkpoint_input, result=result)
            return VerificationEvidenceBundle(state, tuple(docs), tuple(eligible), safe_coverage)
        return bundle

    def _same_fulltext_with_reworded_explanation(self, *, existing: Any, event: EventDraft,
                                               ref: Mapping[str, Any], context: Mapping[str, Any],
                                               cutoff_at: datetime, cutoff_inclusive: bool) -> bool:
        """Prove an old completed input differs only in two explanatory fields.

        A model can request an extracted version again using revised prose. Its
        parent source and actual paid request stay identical. Historical input
        hashing keeps changed sources, cutoffs and event facts fail-closed.
        """
        if existing is None or existing[0] != "completed" or self.checkpoint_store is None:
            return False
        with read_connection(self.db_path) as conn:
            require_schema(conn)
            history = conn.execute(
                "SELECT DISTINCT f.request_json FROM k10_research_fulltext_requests f "
                "JOIN k10_research_snapshot_revisions s ON s.snapshot_id=f.snapshot_id "
                "AND s.revision=f.snapshot_revision WHERE s.task_id=? AND f.question_id=?",
                (self.checkpoint_store.task_id, context["questionId"]),
            ).fetchall()
        for (raw,) in history:
            request = json.loads(raw)
            original_context = dict(context) | {key: request[key] for key in
                ("reasonExcerptInsufficient", "expectedJudgmentChange")}
            original_digest = self.checkpoint_store.input_sha256(
                canonical_key=event.canonical_key, stage_key=event.stage_key, event_state=event.event_state,
                headline=event.headline, event_kind=event.event_kind, facts=event.facts, source_refs=[dict(ref)],
                cutoff_at=_text(cutoff_at), cutoff_inclusive=cutoff_inclusive, investigation_path=original_context)
            if original_digest == existing[1]:
                return True
        return False

    def fetch_fulltext(self, *, event: EventDraft, document: DiscoveryDocument, question: Any,
                       request: Any, cutoff_at: datetime,
                       cutoff_inclusive: bool = False) -> VerificationEvidenceBundle:
        """Admit a real stored source before Extract, preserving the frozen slots.

        A source's earlier publication timestamp is provenance, not proof that
        a page fetched now had this exact body at the cutoff.  The evidence
        assessor receives that distinction explicitly.
        """
        if cutoff_at.tzinfo is None:
            raise ValueError("核验 cutoff 必须带时区")
        question, request = self._mapping(question), self._mapping(request)
        ref = self._document_ref(document)
        if (request.get("sourceRef") != ref or not question.get("questionId")
                or request.get("questionId") != question.get("questionId")
                or any(not isinstance(request.get(key), str) or not request[key].strip()
                       for key in ("reasonExcerptInsufficient", "expectedJudgmentChange"))):
            raise VerificationCheckpointError("research_fulltext_contract_invalid")
        local_admission = admit_material(document)
        if local_admission.state == "excluded":
            # This is a completed local material decision, not a provider or
            # coverage failure.  The question can close with its real gap while
            # sibling evidence continues through the normal gateway.
            return VerificationEvidenceBundle("available", (), (), {
                "provider": "local", "operation": "extract", "questionId": question["questionId"],
                "state": "available", "reason": local_admission.reason, "requestState": "completed",
                "admissionState": "rejected", "sourceContentSha256": local_admission.content_sha256,
            })
        checkpoint = self.checkpoint_store
        if checkpoint is None:
            return self._pending("fulltext_task_binding_required")
        if not isinstance(self.network_max_attempts, int) or isinstance(self.network_max_attempts, bool) or self.network_max_attempts < 1:
            return self._pending("networkMaxAttempts_missing")
        if checkpoint.leaseguard is not None:
            checkpoint.leaseguard()
        stored = store.load_document_versions(refs=[ref], db_path=self.db_path, source_keys=(self.source_key,))
        if len(stored) != 1:
            raise VerificationCheckpointError("research_fulltext_source_unknown")
        source = stored[0]
        parent_ref = (source.get("metadata") or {}).get("fulltextSourceRef")
        if source.get("fetchVersion") == "tavily-extract-fulltext-v1" and isinstance(parent_ref, Mapping):
            if (parent_ref.get("documentId") != document.document_id
                    or not isinstance(parent_ref.get("revision"), int)
                    or isinstance(parent_ref["revision"], bool) or not 1 <= parent_ref["revision"] < document.revision):
                raise VerificationCheckpointError("research_fulltext_parent_invalid")
            parent = store.load_document_versions(refs=[parent_ref], db_path=self.db_path, source_keys=(self.source_key,))
            if len(parent) != 1:
                raise VerificationCheckpointError("research_fulltext_source_unknown")
            item = parent[0]
            original = DiscoveryDocument(item["documentId"], item["revision"], item.get("publishedAt"), item["fetchedAt"],
                item.get("originalText"), item.get("excerpt"), item.get("metadata") or {})
            return self.fetch_fulltext(event=event, document=original, question=question,
                request=dict(request) | {"sourceRef": dict(parent_ref)}, cutoff_at=cutoff_at, cutoff_inclusive=cutoff_inclusive)
        with read_connection(self.db_path) as conn:
            require_schema(conn)
            identity = conn.execute("SELECT canonical_url,external_id FROM k10_source_documents WHERE document_id=? AND source_key=?",
                                    (document.document_id, self.source_key)).fetchone()
        if identity is None:
            raise VerificationCheckpointError("research_fulltext_source_unknown")
        url, external_id = identity
        # Only a URL actually returned and durably saved by the evidence
        # gateway may reach Extract.  Model-authored URLs are never accepted.
        from urllib.parse import urlparse
        from ipaddress import ip_address
        parsed = urlparse(url) if isinstance(url, str) else None
        if not parsed or parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            return self._pending("fulltext_source_url_unavailable")
        hostname = parsed.hostname.lower().rstrip(".")
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".local", ".internal", ".localhost")):
            return self._pending("fulltext_source_url_unavailable")
        try:
            if not ip_address(hostname).is_global:
                return self._pending("fulltext_source_url_unavailable")
        except ValueError:
            pass
        client = self.client
        if client is None:
            key = get_tavily_api_key(db_path=self.db_path)
            client = TavilySearchClient(key) if key else None

        path_id = "fulltext:" + sha256(json.dumps(ref, sort_keys=True).encode()).hexdigest()
        item_key = checkpoint.item_key(canonical_key=event.canonical_key, stage_key=event.stage_key,
            event_state=event.event_state, question_id=question["questionId"], path_id=path_id, operation="extract")
        context = {"questionId": question["questionId"], "sourceRef": ref,
                   "reasonExcerptInsufficient": request["reasonExcerptInsufficient"],
                   "expectedJudgmentChange": request["expectedJudgmentChange"], "operation": "extract"}
        digest = checkpoint.input_sha256(canonical_key=event.canonical_key, stage_key=event.stage_key,
            event_state=event.event_state, headline=event.headline, event_kind=event.event_kind,
            facts=event.facts, source_refs=[ref], cutoff_at=_text(cutoff_at),
            cutoff_inclusive=cutoff_inclusive, investigation_path=context)
        if self.context_protocol:
            from .research_context import digest as context_digest
            item_key = 'tavily:shared:extract:' + context_digest([self.context_protocol, ref])
            digest = context_digest({'protocol': self.context_protocol, 'sourceRef': ref,
                'sourceContent': source.get('contentSha256'), 'url': url, 'operation': 'extract',
                'cutoffAt': cutoff_at.astimezone(timezone.utc).isoformat(), 'cutoffInclusive': cutoff_inclusive})
        admission = store.admit_article(task_id=checkpoint.task_id, document_id=document.document_id,
            revision=document.revision, admission_kind="tavily_full_article", created_at=_text(self.clock()), db_path=self.db_path)
        admission_ref = {"taskId": checkpoint.task_id, **ref}
        if admission.get("state") not in {"admitted", "reused"}:
            pending = self._pending(str(admission.get("reason") or "fulltext_admission_failed"))
            return VerificationEvidenceBundle(pending.state, (), (), dict(pending.coverage) | {
                "operation": "extract", "admissionState": "rejected", "admissionRef": admission_ref,
                "questionId": question["questionId"], "requestState": "completed",
            })
        # Reuse the same admitted body across questions.  This also closes a
        # restart between saving a provider result and completing its article
        # or request checkpoint, without sending the paid request again.
        with read_connection(self.db_path) as conn:
            require_schema(conn)
            cached_rows = conn.execute(
                "SELECT revision,metadata_json FROM k10_source_document_versions WHERE document_id=? "
                "AND fetch_version='tavily-extract-fulltext-v1' AND original_text IS NOT NULL ORDER BY revision DESC",
                (document.document_id,),
            ).fetchall()
            old_checkpoint = conn.execute(
                "SELECT status,input_sha256 FROM k10_execution_item_checkpoints "
                "WHERE task_id=? AND item_kind='event' AND item_key=? AND stage='tavily_evidence'",
                (checkpoint.task_id, item_key),
            ).fetchone()
        if admission.get("articleState") == "missing_body":
            missing_coverage = {"provider": "tavily", "operation": "extract", "questionId": question["questionId"],
                "pathId": path_id, "admissionRef": admission_ref, "admissionState": "admitted",
                "state": "pending", "reason": "tavily_fulltext_unavailable", "requestState": "reused",
                "requests": self.requests, "creditsTotal": self.credits}
            missing_result = {"state": "pending", "documentRefs": [], "eligibleDocumentRefs": [], "coverage": missing_coverage}
            if old_checkpoint is not None and old_checkpoint[1] != digest and not self._same_fulltext_with_reworded_explanation(
                    existing=old_checkpoint, event=event, ref=ref, context=context,
                    cutoff_at=cutoff_at, cutoff_inclusive=cutoff_inclusive):
                return self._pending("checkpoint_input_mismatch")
            if old_checkpoint is not None and old_checkpoint[0] == "running":
                checkpoint.complete(item_key=item_key, input_sha256=digest, result=missing_result)
            return VerificationEvidenceBundle("pending", (), (), missing_coverage)
        for cached_revision, raw_metadata in cached_rows:
            metadata = json.loads(raw_metadata)
            if metadata.get("articleAdmissionRef") != admission_ref:
                continue
            cached_ref = {"documentId": document.document_id, "revision": cached_revision}
            cached_coverage = {"provider": "tavily", "operation": "extract", "questionId": question["questionId"],
                "pathId": path_id, "admissionRef": admission_ref, "admissionState": "fulfilled",
                "state": metadata["fulltextCoverageState"], "reason": metadata["fulltextCoverageReason"],
                "credits": metadata.get("extractCredits"), "requestState": "reused",
                "requests": self.requests, "creditsTotal": self.credits}
            cached_result = {"state": cached_coverage["state"], "documentRefs": [cached_ref],
                "eligibleDocumentRefs": [cached_ref] if cached_coverage["state"] == "available" else [],
                "coverage": cached_coverage}
            if old_checkpoint is not None and old_checkpoint[1] != digest and not self._same_fulltext_with_reworded_explanation(
                    existing=old_checkpoint, event=event, ref=ref, context=context,
                    cutoff_at=cutoff_at, cutoff_inclusive=cutoff_inclusive):
                return self._pending("checkpoint_input_mismatch")
            store.record_article_outcome(task_id=checkpoint.task_id, document_id=document.document_id,
                revision=document.revision, state="completed", reason_code=None, updated_at=_text(self.clock()), db_path=self.db_path)
            if old_checkpoint is not None and old_checkpoint[0] == "running":
                checkpoint.complete(item_key=item_key, input_sha256=digest, result=cached_result)
            return self._restore_checkpoint_bundle(cached_result)
        claim = checkpoint.claim(item_key=item_key, input_sha256=digest, network_max_attempts=self.network_max_attempts,
                                 updated_at=_text(self.clock()),
                                 new_external_admission_guard=self.new_external_admission_guard)
        self.requests = claim.requests
        if claim.state == "reused":
            return self._restore_checkpoint_bundle(claim.result)
        if claim.state == "pending":
            return self._pending_claim(claim)
        if claim.state == "replay":
            response, obtained_at = self._response_from_receipt(claim.result, operation="extract")
            if response.url != url:
                raise VerificationCheckpointError("tavily_response_receipt_url_mismatch")
        else:
            if not callable(getattr(client, "extract", None)):
                code = "tavily_api_key_missing" if client is None else "tavily_extract_unavailable"
                checkpoint.defer_without_request(item_key=item_key, input_sha256=digest, safe_error_code=code)
                return self._pending(code)
            attempt_id, blocked = self._reserve_search(item_key=item_key, input_sha256=digest, attempt=self.requests)
            if blocked:
                if blocked in {"execution_paused", "execution_not_configured", "execution_retired_by_user"}:
                    checkpoint.defer_without_request(item_key=item_key, input_sha256=digest, safe_error_code=blocked)
                else:
                    checkpoint.fail_retryable(item_key=item_key, input_sha256=digest, safe_error_code=blocked)
                return self._pending(blocked)
            response: TavilyExtractResponse | None = None
            try:
                response = client.extract(url)
            except Exception:
                pass
            obtained_at = self.clock()
            if attempt_id is not None:
                if response is not None and response.reason == "tavily_extract_outcome_unknown":
                    self._settle_search(attempt_id=attempt_id, response=None)
                else:
                    self._settle_search(attempt_id=attempt_id, response=response, obtained_at=obtained_at)
        if response is None or (not response.ok and response.reason != "tavily_fulltext_unavailable"):
            code = ("tavily_extract_outcome_unknown" if response is None
                    else self._terminal_response_code(response) or self._response_code(response))
            if response is None:
                checkpoint.fail_retryable(item_key=item_key, input_sha256=digest, safe_error_code=code)
            store.record_article_outcome(task_id=checkpoint.task_id, document_id=document.document_id,
                revision=document.revision, state="failed", reason_code=code, updated_at=_text(self.clock()), db_path=self.db_path)
            return self._failed_response(item_key=item_key, input_sha256=digest)
        obtained_text = _text(obtained_at)
        if response.credits is not None:
            self.credits += response.credits
        # The paid receipt and both usage ledgers were committed together;
        # local document replay must not append another usage event.
        coverage: dict[str, Any] = {
            "provider": "tavily", "operation": "extract", "questionId": question["questionId"], "pathId": path_id,
            "admissionRef": admission_ref, "admissionState": "fulfilled" if response.ok else "admitted",
            "state": "pending", "reason": response.reason, "requestState": "completed",
            "requests": self.requests, "credits": response.credits, "creditsTotal": self.credits,
        }
        documents: tuple[DiscoveryDocument, ...] = ()
        eligible: tuple[DiscoveryDocument, ...] = ()
        if response.ok and response.raw_content:
            published = _aware_instant(source.get("publishedAt"))
            precision = source.get("publishedPrecision")
            time_eligible = bool(published and ((precision == "exact" and
                (published < cutoff_at or (cutoff_inclusive and published == cutoff_at)))
                or (precision == "date" and published.date() < cutoff_at.date())))
            metadata = dict(source.get("metadata") or {})
            metadata.update({"fulltextSourceRef": ref, "articleAdmissionRef": admission_ref,
                "questionId": question["questionId"], "bodyObservedAt": obtained_text,
                "contentVersionAtCutoff": "unconfirmed", "requestId": response.request_id,
                "firstSeenAt": (source.get("metadata") or {}).get("firstSeenAt") or source.get("fetchedAt"),
                "sourcePublicationInherited": True, "extractCredits": response.credits,
                "fulltextCoverageState": "available" if time_eligible else "pending",
                "fulltextCoverageReason": "ok" if time_eligible else "fulltext_publication_time_unconfirmed"})
            provisional = DiscoveryDocument(document.document_id, document.revision + 1, source.get("publishedAt"),
                                            obtained_text, response.raw_content, source.get("excerpt"), metadata)
            material_admission = admit_material(provisional)
            metadata["materialAdmission"] = {
                "state": material_admission.state,
                "reason": material_admission.reason,
                "contentSha256": material_admission.content_sha256,
            }
            if material_admission.state == "excluded":
                metadata["fulltextCoverageState"] = "pending"
                metadata["fulltextCoverageReason"] = material_admission.reason
            payload = {"body": response.raw_content, "sourceRef": ref, "metadata": metadata}
            version = store.append_document_version(document_id=document.document_id, source_key=self.source_key,
                external_id=external_id, canonical_url=url,
                content_sha256=sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                published_at=source.get("publishedAt"), published_precision=source["publishedPrecision"],
                fetched_at=obtained_text, original_text=response.raw_content, excerpt=source.get("excerpt"),
                fetch_version="tavily-extract-fulltext-v1", metadata=metadata, created_at=obtained_text, db_path=self.db_path)
            full = DiscoveryDocument(version.document_id, version.revision, source.get("publishedAt"),
                obtained_text, response.raw_content, source.get("excerpt"), metadata)
            if material_admission.state != "excluded":
                documents = (full,)
                if time_eligible:
                    eligible = documents
            coverage.update({"state": "available" if eligible else "pending",
                "reason": "ok" if eligible else (material_admission.reason if material_admission.state == "excluded"
                                                     else "fulltext_publication_time_unconfirmed"),
                **({"admissionState": "rejected"} if material_admission.state == "excluded" else {}),
                **({"materialExclusions": {material_admission.reason: 1}}
                   if material_admission.state == "excluded" else {})})
        store.record_article_outcome(task_id=checkpoint.task_id, document_id=document.document_id,
            revision=document.revision, state="completed" if documents else "missing_body",
            reason_code=None if documents else "tavily_fulltext_unavailable", updated_at=obtained_text, db_path=self.db_path)
        coverage.update({"documents": len(documents), "eligibleDocuments": len(eligible)})
        checkpoint.complete(item_key=item_key, input_sha256=digest, result={
            "state": coverage["state"], "documentRefs": [self._document_ref(item) for item in documents],
            "eligibleDocumentRefs": [self._document_ref(item) for item in eligible], "coverage": coverage,
        })
        return VerificationEvidenceBundle(str(coverage["state"]), documents, eligible, coverage)

    @staticmethod
    def _document_ref(document: DiscoveryDocument) -> dict[str, Any]:
        return {"documentId": document.document_id, "revision": document.revision}

    def _pending(self, reason: str) -> VerificationEvidenceBundle:
        return VerificationEvidenceBundle("pending", (), (), {
            "provider": "tavily", "state": "pending", "reason": reason,
            "requestState": "pending", "requests": self.requests,
            "credits": self.credits,
        })

    def _for_current_query_context(self, bundle: VerificationEvidenceBundle, *,
                                   query_context: Mapping[str, Any] | None) -> VerificationEvidenceBundle:
        """Associate a reused physical search with its independently checked path.

        The stored result records the first event that paid for the external
        query.  A later event never inherits that event's question ID or
        intent: it receives the same immutable source versions with its own
        already-validated local relationship in the durable tool checkpoint.
        """
        if query_context is None:
            return bundle
        coverage = dict(bundle.coverage)
        coverage.update({
            "questionId": query_context["questionId"],
            "pathId": query_context["pathId"],
            "operation": "search",
            "queryIntent": query_context["intent"],
            "requestState": "reused",
        })
        return VerificationEvidenceBundle(bundle.state, bundle.documents, bundle.eligible_documents, coverage)

    def _restore_checkpoint_bundle(self, result: Mapping[str, Any] | None) -> VerificationEvidenceBundle:
        if not isinstance(result, Mapping):
            raise VerificationCheckpointError("verification_checkpoint_corrupt")
        raw_refs = result.get("documentRefs")
        raw_eligible = result.get("eligibleDocumentRefs")
        coverage = result.get("coverage")
        if not isinstance(raw_refs, list) or not isinstance(raw_eligible, list) or not isinstance(coverage, Mapping):
            raise VerificationCheckpointError("verification_checkpoint_corrupt")
        docs = store.load_document_versions(refs=raw_refs, db_path=self.db_path, source_keys=(self.source_key,))
        if len(docs) != len(raw_refs):
            return self._pending("checkpoint_documents_unavailable")
        restored_all = tuple(DiscoveryDocument(
            str(item["documentId"]), int(item["revision"]), item.get("publishedAt"), str(item["fetchedAt"]),
            item.get("originalText"), item.get("excerpt"), item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {},
        ) for item in docs)
        restored = []
        material_exclusions: dict[str, int] = {}
        for document in restored_all:
            admission = admit_material(document)
            if admission.state == "excluded":
                material_exclusions[admission.reason] = material_exclusions.get(admission.reason, 0) + 1
                continue
            restored.append(document)
        index = {(item.document_id, item.revision): item for item in restored}
        eligible_rows = []
        for ref in raw_eligible:
            try:
                item = index[(str(ref["documentId"]), int(ref["revision"]))]
            except (KeyError, TypeError, ValueError):
                # A cached source can become newly recognized as an excluded
                # prospectus after its raw body/title parser improves.
                # Preserve its local audit while retaining any sibling source
                # that remains independently eligible.
                if material_exclusions:
                    continue
                return self._pending("checkpoint_documents_unavailable")
            eligible_rows.append(item)
        eligible = tuple(eligible_rows)
        safe_coverage = dict(coverage)
        safe_coverage.update({"requestState": "reused", "requests": self.requests, "creditsTotal": self.credits})
        if material_exclusions:
            old = safe_coverage.get("materialExclusions")
            merged = dict(old) if isinstance(old, Mapping) else {}
            for reason, count in material_exclusions.items():
                merged[reason] = int(merged.get(reason, 0)) + count
            safe_coverage["materialExclusions"] = merged
        state = result.get("state")
        if state not in {"available", "pending"}:
            raise VerificationCheckpointError("verification_checkpoint_corrupt")
        return VerificationEvidenceBundle(state, tuple(restored), eligible, safe_coverage)


__all__ = ["SearchClient", "TavilyEvidenceGateway", "VerificationEvidenceBundle"]
