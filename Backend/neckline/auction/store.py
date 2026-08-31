"""Append-only persistence for K9-v3 checklist snapshots."""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from neckline.db import connection, readonly_tables

def save_checklist(checklist: Any, *, db_path: Optional[Path] = None) -> None:
    payload = json.dumps(checklist.as_dict(), ensure_ascii=False, sort_keys=True)
    with connection(db_path) as conn:
        prior = conn.execute("SELECT checklist_json FROM k9_package_checklists WHERE batch_id=?",
                             (checklist.batch_id,)).fetchone()
        if prior is not None:
            if prior[0] != payload:
                raise RuntimeError(f"{checklist.batch_id} 的 9:29 核对已冻结")
            return
        conn.execute("INSERT INTO k9_package_checklists(batch_id,trade_date,checklist_json,created_at) VALUES(?,?,?,?)",
                     (checklist.batch_id, checklist.trade_date, payload,
                      datetime.now().isoformat(timespec="seconds")))

def load_checklist(batch_id: str, *, db_path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    with readonly_tables("k9_package_checklists.batch_id", db_path=db_path) as conn:
        if conn is None: return None
        row = conn.execute("SELECT checklist_json FROM k9_package_checklists WHERE batch_id=?", (batch_id,)).fetchone()
    return None if row is None else json.loads(row[0])

__all__ = ["save_checklist", "load_checklist"]
