"""Append-only V2.4.2 selection-run audit store.

The application schema calls :func:`init_selection_run_schema` from ``db.init_schema``.
It is also safe for temporary-db tests to call directly.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from neckline.config import settings
from neckline.db import connection, init_schema


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS selection_runs (
  run_id TEXT PRIMARY KEY,
  trade_date TEXT NOT NULL,
  pipeline_version TEXT NOT NULL,
  config_json TEXT NOT NULL,
  config_fingerprint TEXT NOT NULL,
  lifecycle_state TEXT NOT NULL,
  publication_state TEXT NOT NULL DEFAULT 'processing',
  selection_state_text TEXT,
  stop_reason TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  budget_json TEXT NOT NULL DEFAULT '{}',
  totals_json TEXT NOT NULL DEFAULT '{}',
  published_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_selection_runs_date ON selection_runs(trade_date, started_at DESC);
CREATE TABLE IF NOT EXISTS selection_directions (
  run_id TEXT NOT NULL,
  direction_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  seed_keys_json TEXT NOT NULL,
  brief_json TEXT NOT NULL,
  merge_status TEXT NOT NULL,
  triage_disposition TEXT,
  final_disposition TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(run_id, direction_id),
  FOREIGN KEY(run_id) REFERENCES selection_runs(run_id)
);
CREATE TABLE IF NOT EXISTS selection_direction_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  direction_id TEXT,
  transition TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  batch_no INTEGER,
  fill_round INTEGER,
  llm_call_id INTEGER,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES selection_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_selection_direction_events_run ON selection_direction_events(run_id, id);
CREATE TABLE IF NOT EXISTS selection_llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  task TEXT NOT NULL,
  batch_no INTEGER,
  provider TEXT,
  model TEXT,
  enable_search INTEGER NOT NULL,
  wall_ms INTEGER NOT NULL,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  total_tokens INTEGER,
  raw_usage_json TEXT NOT NULL DEFAULT '{}',
  usage_unavailable INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES selection_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_selection_llm_calls_run ON selection_llm_calls(run_id, id);
"""


def init_selection_run_schema_on_connection(conn: sqlite3.Connection) -> None:
    """Additive/idempotent DDL hook for ``neckline.db.init_schema``."""
    conn.executescript(_SCHEMA)


def init_selection_run_schema(db_path: Optional[Path] = None) -> None:
    """Convenience initializer for isolated tests and one-off local setup only."""
    init_schema(db_path)
    with connection(db_path) as conn:
        init_selection_run_schema_on_connection(conn)


def _fingerprint(config: Mapping[str, Any]) -> str:
    import hashlib
    return hashlib.sha256(_json(config).encode("utf-8")).hexdigest()


def create_run(trade_date: str, config: Mapping[str, Any], *, run_id: Optional[str] = None,
               pipeline_version: Optional[str] = None, db_path: Optional[Path] = None) -> str:
    run_id = run_id or str(uuid.uuid4())
    version = pipeline_version or str(config.get("version", ""))
    if not version:
        raise ValueError("pipeline version is required")
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO selection_runs(run_id,trade_date,pipeline_version,config_json,config_fingerprint,lifecycle_state,started_at) VALUES(?,?,?,?,?,?,?)",
            (run_id, trade_date, version, _json(config), _fingerprint(config), "processing", _now()),
        )
    return run_id


def add_direction(run_id: str, *, direction_id: str, ordinal: int, seed_keys: Sequence[str], brief: Mapping[str, Any],
                  merge_status: str = "merge_policy_unconfigured", db_path: Optional[Path] = None) -> None:
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO selection_directions(run_id,direction_id,ordinal,seed_keys_json,brief_json,merge_status,created_at) VALUES(?,?,?,?,?,?,?)",
            (run_id, direction_id, ordinal, _json(list(seed_keys)), _json(brief), merge_status, _now()),
        )


def add_event(run_id: str, transition: str, *, direction_id: Optional[str] = None, reason: str = "",
              batch_no: Optional[int] = None, fill_round: Optional[int] = None,
              llm_call_id: Optional[int] = None, db_path: Optional[Path] = None,
              conn: Optional[sqlite3.Connection] = None) -> int:
    def _add(target: sqlite3.Connection) -> int:
        cursor = target.execute(
            "INSERT INTO selection_direction_events(run_id,direction_id,transition,reason,batch_no,fill_round,llm_call_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (run_id, direction_id, transition, reason, batch_no, fill_round, llm_call_id, _now()),
        )
        return int(cursor.lastrowid)
    if conn is not None:
        return _add(conn)
    with connection(db_path) as own:
        return _add(own)


def record_llm_call(run_id: str, *, task: str, batch_no: Optional[int], provider: Optional[str], model: Optional[str],
                    enable_search: bool, wall_ms: int, usage: Optional[Mapping[str, Any]], db_path: Optional[Path] = None) -> int:
    """Record only provider-reported token usage; absent usage is an explicit fault."""
    usage = dict(usage or {})
    def token(name: str) -> Optional[int]:
        value = usage.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    prompt = token("prompt_tokens")
    completion = token("completion_tokens")
    total = token("total_tokens")
    # The provider's explicit fault flag is authoritative even if a partial
    # token field happens to be present alongside it.
    unavailable = int(bool(usage.get("usage_unavailable")) or total is None)
    with connection(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO selection_llm_calls(run_id,task,batch_no,provider,model,enable_search,wall_ms,prompt_tokens,completion_tokens,total_tokens,raw_usage_json,usage_unavailable,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, task, batch_no, provider, model, int(enable_search), wall_ms, prompt, completion, total, _json(usage), unavailable, _now()),
        )
        return int(cursor.lastrowid)


def update_direction_disposition(run_id: str, direction_id: str, *, triage_disposition: Optional[str] = None,
                                 final_disposition: Optional[str] = None, db_path: Optional[Path] = None,
                                 conn: Optional[sqlite3.Connection] = None) -> None:
    if triage_disposition is None and final_disposition is None:
        return
    clauses, values = [], []
    if triage_disposition is not None:
        clauses.append("triage_disposition=?")
        values.append(triage_disposition)
    if final_disposition is not None:
        clauses.append("final_disposition=?")
        values.append(final_disposition)
    values += [run_id, direction_id]
    def _update(target: sqlite3.Connection) -> None:
        cursor = target.execute(f"UPDATE selection_directions SET {', '.join(clauses)} WHERE run_id=? AND direction_id=?", values)
        if cursor.rowcount != 1:
            raise KeyError(f"unknown selection direction {run_id}/{direction_id}")
    if conn is not None:
        _update(conn)
        return
    with connection(db_path) as own:
        _update(own)


def finish_run(run_id: str, *, selection_state: str, text: Optional[str] = None, stop_reason: Optional[str] = None,
               budget: Optional[Mapping[str, Any]] = None, totals: Optional[Mapping[str, Any]] = None,
               published: bool = False, db_path: Optional[Path] = None,
               conn: Optional[sqlite3.Connection] = None) -> None:
    if selection_state not in {"complete", "partial", "unavailable"}:
        raise ValueError("terminal selection_state required")
    def _finish(target: sqlite3.Connection) -> None:
        cursor = target.execute(
            "UPDATE selection_runs SET lifecycle_state=?,publication_state=?,selection_state_text=?,stop_reason=?,ended_at=?,budget_json=?,totals_json=?,published_at=? WHERE run_id=? AND lifecycle_state='processing'",
            ("finished", selection_state, text, stop_reason, _now(), _json(budget), _json(totals), _now() if published else None, run_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("selection run is absent or already terminal")
    if conn is not None:
        _finish(conn)
        return
    with connection(db_path) as own:
        _finish(own)


def _readonly_run_row(trade_date: str, sql: str, *, db_path: Optional[Path]) -> Optional[Tuple[Any, ...]]:
    """Read run metadata without enabling WAL or creating a journal sidecar."""
    path = Path(db_path or settings.db_path)
    if not path.exists():
        return None
    try:
        # Do not use the normal connection helper: this is read-only and never
        # migrates.  ``immutable=1`` is deliberately not used here: it ignores
        # a live SQLite WAL and could return the previous published generation
        # while another process is serving the newest one.
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute(sql, (trade_date,)).fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return None
        raise
    return row


def latest_published_run_id(trade_date: str, *, db_path: Optional[Path] = None) -> Optional[str]:
    """The sole visible generation for a D0's frozen selection facts.

    ``started_at`` is not a publication order: concurrent same-day work can
    start A first and publish B first.  The final transactional ``published_at``
    marker is therefore the ordering authority.  Processing/unavailable runs
    have no marker and cannot hide the last valid snapshot.
    """
    row = _readonly_run_row(
        trade_date,
        "SELECT run_id FROM selection_runs "
        "WHERE trade_date=? AND lifecycle_state='finished' AND published_at IS NOT NULL "
        "AND publication_state IN ('complete','partial') "
        "ORDER BY published_at DESC, rowid DESC LIMIT 1",
        db_path=db_path,
    )
    return str(row[0]) if row is not None else None


def latest_publication_state(trade_date: str, *, db_path: Optional[Path] = None) -> Optional[Mapping[str, str]]:
    """Small status overlay for report/API.  It never returns/replaces frozen basket facts."""
    row = _readonly_run_row(
        trade_date,
        "SELECT publication_state,selection_state_text FROM selection_runs "
        "WHERE trade_date=? ORDER BY started_at DESC, rowid DESC LIMIT 1",
        db_path=db_path,
    )
    if row is None:
        return None
    state, text = row
    return {"selectionState": str(state), "selectionStateText": str(text or "")}


__all__ = [
    "init_selection_run_schema", "init_selection_run_schema_on_connection", "create_run", "add_direction", "add_event", "record_llm_call",
    "update_direction_disposition", "finish_run", "latest_published_run_id", "latest_publication_state",
]
