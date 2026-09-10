"""Durable, task-bound checkpoints for finite per-event verification retries."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from .schema import require_schema, write_connection, read_connection
from . import store


_ITEM_KIND = "event"
_STAGE = "tavily_evidence"


class VerificationCheckpointError(RuntimeError):
    """A safe, actionable checkpoint state; it never exposes provider detail."""


@dataclass(frozen=True)
class VerificationRequestClaim:
    state: str  # reserved | reused | pending
    reason: str | None
    requests: int
    result: Mapping[str, Any] | None = None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class VerificationCheckpointStore:
    """One frozen event can retry a finite number of times, independently.

    The external-attempt ledger owns paid-call identity.  This checkpoint only
    preserves the event result and prevents a restarted process from treating
    an unknown request outcome as safely repeatable.
    """

    def __init__(self, *, db_path: Path, task_id: str, leaseguard: Callable[[], None] | None = None,
                 lease_owner: str | None = None) -> None:
        if not task_id:
            raise ValueError("Tavily 核验检查点需要 task_id")
        self.db_path = db_path
        self.task_id = task_id
        self.leaseguard = leaseguard
        self.lease_owner = lease_owner

    @staticmethod
    def input_sha256(*, canonical_key: str, stage_key: str, event_state: str, headline: str,
                     event_kind: str, facts: Mapping[str, Any], source_refs: list[Mapping[str, Any]],
                     cutoff_at: str, cutoff_inclusive: bool, investigation_path: Mapping[str, Any] | None = None) -> str:
        value = {
            "canonicalKey": canonical_key, "stageKey": stage_key, "eventState": event_state,
            "headline": headline, "eventKind": event_kind, "facts": facts,
            "sourceRefs": source_refs, "cutoffAt": cutoff_at, "cutoffInclusive": cutoff_inclusive,
        }
        if investigation_path is not None:
            value["investigationPath"] = dict(investigation_path)
        return sha256(_json(value).encode("utf-8")).hexdigest()

    @staticmethod
    def item_key(*, canonical_key: str, stage_key: str, event_state: str,
                 question_id: str | None = None, path_id: str | None = None,
                 operation: str = "search") -> str:
        # K10 identifies distinct event development by canonical key, stage and
        # state.  A later material stage must have its own evidence request;
        # changed frozen input for that same identity still cannot replace it.
        parts = {"canonicalKey": canonical_key, "stageKey": stage_key, "eventState": event_state}
        if question_id is not None or path_id is not None:
            if not question_id or not path_id:
                raise VerificationCheckpointError("question_path_identity_missing")
            parts.update({"questionId": question_id, "pathId": path_id, "operation": operation})
        identity = _json(parts)
        return "tavily:" + sha256(identity.encode("utf-8")).hexdigest()[:32]

    def attempt_snapshot(self) -> tuple[int, int]:
        with read_connection(self.db_path) as conn:
            require_schema(conn)
            self._bound(conn)
            row = conn.execute(
                "SELECT COALESCE(SUM(network_attempt_count),0) FROM k10_execution_item_checkpoints "
                "WHERE task_id=? AND item_kind=? AND stage=?",
                (self.task_id, _ITEM_KIND, _STAGE),
            ).fetchone()
            credit_rows = conn.execute(
                "SELECT result_json FROM k10_execution_item_checkpoints WHERE task_id=? AND item_kind=? "
                "AND stage=? AND status='completed'", (self.task_id, _ITEM_KIND, _STAGE),
            ).fetchall()
        credits = 0
        for (raw,) in credit_rows:
            try:
                value = json.loads(raw)
                coverage = value.get("coverage") if isinstance(value, Mapping) else None
                number = coverage.get("credits") if isinstance(coverage, Mapping) else None
                if isinstance(number, int) and not isinstance(number, bool):
                    credits += number
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return int(row[0]), credits

    def _bound(self, conn) -> None:
        if conn.execute("SELECT 1 FROM k10_tasks WHERE task_id=?", (self.task_id,)).fetchone() is None:
            raise VerificationCheckpointError("verification_task_missing")
        if conn.execute("SELECT 1 FROM k10_task_execution_bindings WHERE task_id=?", (self.task_id,)).fetchone() is None:
            raise VerificationCheckpointError("verification_task_execution_unbound")

    def _assert_transaction_lease(self, conn) -> None:
        """Prove the worker still owns the lease in the reservation transaction.

        ``leaseguard`` catches the normal fast path.  It cannot close the gap
        between a separate read and this write transaction, so a production
        caller also supplies the leased owner and this check runs under the
        same ``BEGIN IMMEDIATE`` as the insert/update.
        """
        if not self.lease_owner:
            return
        row = conn.execute(
            "SELECT status,lease_owner,lease_until FROM k10_tasks WHERE task_id=?", (self.task_id,)
        ).fetchone()
        if row is None or row[0] != "running" or row[1] != self.lease_owner or not row[2] or str(row[2]) < _now():
            raise store.K10Conflict("任务租约已失效，请等待恢复")

    def claim(self, *, item_key: str, input_sha256: str, network_max_attempts: int,
              updated_at: str | None = None) -> VerificationRequestClaim:
        if self.leaseguard is not None:
            self.leaseguard()
        with write_connection(self.db_path) as conn:
            require_schema(conn)
            self._bound(conn)
            self._assert_transaction_lease(conn)
            existing = conn.execute(
                "SELECT input_sha256,status,result_json,attempt_count,network_attempt_count,safe_error_code FROM k10_execution_item_checkpoints "
                "WHERE task_id=? AND item_kind=? AND item_key=? AND stage=?",
                (self.task_id, _ITEM_KIND, item_key, _STAGE),
            ).fetchone()
            used = int(conn.execute(
                "SELECT COALESCE(SUM(network_attempt_count),0) FROM k10_execution_item_checkpoints "
                "WHERE task_id=? AND item_kind=? AND stage=?",
                (self.task_id, _ITEM_KIND, _STAGE),
            ).fetchone()[0])
            if existing is not None:
                if str(existing[0]) != input_sha256:
                    return VerificationRequestClaim("pending", "checkpoint_input_mismatch", used)
                if str(existing[1]) == "completed":
                    try:
                        result = json.loads(existing[2])
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        raise VerificationCheckpointError("verification_checkpoint_corrupt") from exc
                    if not isinstance(result, Mapping):
                        raise VerificationCheckpointError("verification_checkpoint_corrupt")
                    return VerificationRequestClaim("reused", None, used, result)
                prior_attempts = int(existing[4])
                if existing[5] in {"insufficient_balance", "network_attempts_exhausted", "tavily_request_outcome_unknown", "tavily_extract_outcome_unknown"}:
                    return VerificationRequestClaim("pending", existing[5], used)
                if existing[1] == "failed" and existing[5] == "rate_limited" and prior_attempts < network_max_attempts:
                    receipt = json.loads(existing[2] or "{}")
                    retry_at = receipt.get("retryAt")
                    if retry_at and datetime.fromisoformat(updated_at or _now()) < datetime.fromisoformat(retry_at):
                        return VerificationRequestClaim("pending", "rate_limited", used, receipt)
                if str(existing[1]) == "failed" and prior_attempts < network_max_attempts:
                    conn.execute(
                        "UPDATE k10_execution_item_checkpoints SET status='running',attempt_count=?,network_attempt_count=?,"
                        "safe_error_code=NULL,safe_error_ref=NULL,updated_at=? WHERE task_id=? AND item_kind=? AND item_key=? AND stage=?",
                        (int(existing[3]) + 1, prior_attempts + 1, updated_at or _now(),
                         self.task_id, _ITEM_KIND, item_key, _STAGE),
                    )
                    return VerificationRequestClaim("reserved", None, used + 1)
                # A running row is an interrupted process whose outcome cannot
                # be established here.  It remains charged and awaits task-level
                # recovery rather than allowing duplicate live requests.
                reason = "network_attempts_exhausted" if prior_attempts >= network_max_attempts else "tavily_request_outcome_unknown"
                return VerificationRequestClaim("pending", reason, used)
            conn.execute(
                "INSERT INTO k10_execution_item_checkpoints(task_id,item_kind,item_key,stage,input_sha256,status,"
                "attempt_count,network_attempt_count,repair_attempt_count,elapsed_ms,input_tokens,output_tokens,"
                "result_json,safe_error_code,safe_error_ref,updated_at) VALUES(?,?,?,?,?,'running',1,1,0,0,NULL,NULL,NULL,NULL,NULL,?)",
                (self.task_id, _ITEM_KIND, item_key, _STAGE, input_sha256, updated_at or _now()),
            )
            return VerificationRequestClaim("reserved", None, used + 1)

    def complete(self, *, item_key: str, input_sha256: str, result: Mapping[str, Any], updated_at: str | None = None) -> None:
        attempts, network_attempts = self._attempt_counts(item_key=item_key, input_sha256=input_sha256)
        store.record_execution_checkpoint(
            task_id=self.task_id, item_kind=_ITEM_KIND, item_key=item_key, stage=_STAGE, input_sha256=input_sha256,
            status="completed", attempt_count=attempts, network_attempt_count=network_attempts, repair_attempt_count=0, elapsed_ms=0,
            input_tokens=None, output_tokens=None, result=result, safe_error_code=None, safe_error_ref=None,
            updated_at=updated_at or _now(), db_path=self.db_path, leaseguard=self.leaseguard,
        )

    def fail_retryable(self, *, item_key: str, input_sha256: str, safe_error_code: str,
                       updated_at: str | None = None) -> None:
        attempts, network_attempts = self._attempt_counts(item_key=item_key, input_sha256=input_sha256)
        store.record_execution_checkpoint(
            task_id=self.task_id, item_kind=_ITEM_KIND, item_key=item_key, stage=_STAGE, input_sha256=input_sha256,
            status="failed", attempt_count=attempts, network_attempt_count=network_attempts, repair_attempt_count=0, elapsed_ms=0,
            input_tokens=None, output_tokens=None, result=None, safe_error_code=safe_error_code,
            safe_error_ref=None, updated_at=updated_at or _now(), db_path=self.db_path, leaseguard=self.leaseguard,
        )

    def defer_without_request(self, *, item_key: str, input_sha256: str, safe_error_code: str,
                              updated_at: str | None = None) -> None:
        """Undo a claim when the provider request was never admitted.

        The external-attempt ledger checks the run control after this
        checkpoint claims an event.  A pause or invalid binding therefore
        must not consume a finite network retry: no provider request left the
        process.  Keep a safe note while making this frozen input claimable
        again when operations reopen.
        """
        if self.leaseguard is not None:
            self.leaseguard()
        with write_connection(self.db_path) as conn:
            require_schema(conn)
            self._bound(conn)
            self._assert_transaction_lease(conn)
            row = conn.execute(
                "SELECT input_sha256,status,attempt_count,network_attempt_count FROM k10_execution_item_checkpoints "
                "WHERE task_id=? AND item_kind=? AND item_key=? AND stage=?",
                (self.task_id, _ITEM_KIND, item_key, _STAGE),
            ).fetchone()
            if row is None or str(row[0]) != input_sha256 or str(row[1]) != "running":
                raise VerificationCheckpointError("verification_reservation_lost")
            attempts, network_attempts = int(row[2]), int(row[3])
            if attempts < 1 or network_attempts < 1:
                raise VerificationCheckpointError("verification_checkpoint_corrupt")
            conn.execute(
                "UPDATE k10_execution_item_checkpoints SET status='failed',attempt_count=?,network_attempt_count=?,"
                "safe_error_code=?,safe_error_ref=NULL,updated_at=? WHERE task_id=? AND item_kind=? AND item_key=? AND stage=?",
                (attempts - 1, network_attempts - 1, safe_error_code, updated_at or _now(),
                 self.task_id, _ITEM_KIND, item_key, _STAGE),
            )

    def _attempt_counts(self, *, item_key: str, input_sha256: str) -> tuple[int, int]:
        with read_connection(self.db_path) as conn:
            require_schema(conn)
            self._bound(conn)
            row = conn.execute(
                "SELECT input_sha256,status,attempt_count,network_attempt_count FROM k10_execution_item_checkpoints "
                "WHERE task_id=? AND item_kind=? AND item_key=? AND stage=?",
                (self.task_id, _ITEM_KIND, item_key, _STAGE),
            ).fetchone()
        if row is None or str(row[0]) != input_sha256 or str(row[1]) != "running":
            raise VerificationCheckpointError("verification_reservation_lost")
        return int(row[2]), int(row[3])


__all__ = ["VerificationCheckpointError", "VerificationCheckpointStore", "VerificationRequestClaim"]
