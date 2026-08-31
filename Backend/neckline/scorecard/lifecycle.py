"""K9-v3 夜间生命周期编排。

按顺序尝试 D2、D1、D0，但失败互相隔离；实际行情收集由调用方提供，确保本模块不联网
也不在读路径写库。
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timezone
import uuid
from typing import Callable
from pathlib import Path
from typing import Optional, Any

from neckline.db import connection, readonly_tables

@dataclass(frozen=True)
class StageOutcome:
    stage: str
    ok: bool
    detail: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def begin_attempt(*, selection_date: date, signal_trade_date: date, run_identity: str,
                  db_path: Optional[Path] = None) -> str:
    """Persist the attempt before any stage; a crash remains report-visible."""
    attempt_id = str(uuid.uuid4())
    with connection(db_path) as conn:
        inserted = conn.execute(
            "INSERT OR IGNORE INTO k9_lifecycle_attempts("
            "attempt_id,selection_date,signal_trade_date,strategy_version,run_identity,status,started_at) "
            "VALUES(?,?,?,?,?,'running',?)",
            (attempt_id, selection_date.strftime("%Y%m%d"), signal_trade_date.strftime("%Y%m%d"),
             "K9-v3", run_identity, _now()),
        ).rowcount == 1
        row = conn.execute(
            "SELECT attempt_id FROM k9_lifecycle_attempts WHERE selection_date=? AND signal_trade_date=? "
            "AND strategy_version='K9-v3' AND run_identity=?",
            (selection_date.strftime("%Y%m%d"), signal_trade_date.strftime("%Y%m%d"), run_identity),
        ).fetchone()
        if row is None:  # impossible unless the database's unique contract was damaged
            raise RuntimeError("lifecycle attempt 写入后无法读取权威身份")
        authoritative_id = str(row[0])
        if inserted:
            conn.executemany(
                "INSERT INTO k9_lifecycle_stages(attempt_id,stage,status,detail,updated_at) VALUES(?,?, 'running',NULL,?)",
                [(authoritative_id, stage, _now()) for stage in ("d2", "d1", "d0")],
            )
    return authoritative_id


def record_attempt(*, attempt_id: str, outcomes: tuple[StageOutcome, ...], db_path: Optional[Path] = None) -> None:
    with connection(db_path) as conn:
        current = conn.execute("SELECT status FROM k9_lifecycle_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if current is None:
            raise LookupError(f"未知 lifecycle attempt:{attempt_id}")
        # A completed successful run is immutable evidence.  A later service
        # retry may observe missing params or a transient feed fault, but must
        # never rewrite the same run identity as failed.
        if current[0] == "ok":
            return
        for outcome in outcomes:
            conn.execute("UPDATE k9_lifecycle_stages SET status=?,detail=?,updated_at=? "
                         "WHERE attempt_id=? AND stage=? AND status!='ok'",
                         ("ok" if outcome.ok else "failed", outcome.detail, _now(), attempt_id, outcome.stage))
        # Stage evidence can be written over several recoverable invocations.
        # Derive the aggregate from the durable state after this update, never
        # from this call's partial outcomes (which may omit a prior success).
        stages = conn.execute(
            "SELECT stage,status FROM k9_lifecycle_stages WHERE attempt_id=?", (attempt_id,)
        ).fetchall()
        states = {str(stage): str(status) for stage, status in stages}
        succeeded = all(states.get(stage) == "ok" for stage in ("d2", "d1", "d0"))
        conn.execute("UPDATE k9_lifecycle_attempts SET status=?,finished_at=? WHERE attempt_id=? AND status!='ok'",
                     ("ok" if succeeded else "failed", _now(), attempt_id))


def attempt_is_ok(attempt_id: str, *, db_path: Optional[Path] = None) -> bool:
    """Whether this exact run identity has already completed successfully."""
    with readonly_tables("k9_lifecycle_attempts.attempt_id", db_path=db_path) as conn:
        row = None if conn is None else conn.execute(
            "SELECT status FROM k9_lifecycle_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
    return row is not None and row[0] == "ok"


def latest_attempt(*, selection_date: date, signal_trade_date: date,
                   db_path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    with readonly_tables("k9_lifecycle_attempts.attempt_id", "k9_lifecycle_stages.attempt_id", db_path=db_path) as conn:
        if conn is None:
            return None
        row = conn.execute("SELECT attempt_id,status,run_identity,started_at,finished_at FROM k9_lifecycle_attempts WHERE selection_date=? AND signal_trade_date=? AND strategy_version='K9-v3' ORDER BY CASE status WHEN 'ok' THEN 0 ELSE 1 END, started_at DESC LIMIT 1",
                           (selection_date.strftime("%Y%m%d"), signal_trade_date.strftime("%Y%m%d"))).fetchone()
        if row is None:
            return None
        stages = conn.execute("SELECT stage,status,detail FROM k9_lifecycle_stages WHERE attempt_id=?", (row[0],)).fetchall()
    return {"attemptId": row[0], "status": row[1], "runIdentity": row[2], "startedAt": row[3], "finishedAt": row[4],
            "stages": {stage: {"status": status, "detail": detail} for stage, status, detail in stages}}

def run_nightly(*, settle_d2: Callable[[], None], update_d1: Callable[[], None], create_d0: Callable[[], None]) -> tuple[StageOutcome, ...]:
    outcomes = []
    for stage, action in (("d2", settle_d2), ("d1", update_d1), ("d0", create_d0)):
        try:
            action(); outcomes.append(StageOutcome(stage, True))
        except Exception as exc:  # 每个包的旧结论不能被下一阶段失败清空。
            outcomes.append(StageOutcome(stage, False, f"{type(exc).__name__}: {exc}"))
    return tuple(outcomes)

__all__ = ["StageOutcome", "run_nightly", "begin_attempt", "record_attempt", "attempt_is_ok", "latest_attempt"]
