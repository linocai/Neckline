"""D1 9:29 package checklist scheduler."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from neckline.auction import AUCTION_WINDOW_END, AUCTION_WINDOW_START, SKIP_ALREADY_RAN, SKIP_NO_LISTING, SKIP_NOT_WINDOW
from neckline.auction import checklist, store
from neckline.calendar import CN_TZ, is_trading_day
from neckline.scorecard import packages

AUCTION_SCOPE, EVENT_CHECKLIST = "auction", "v3_checklist"

@dataclass
class ChecklistRunResult:
    trade_date: object; now: datetime; ran: bool = False; skipped_reason: str = ""; batch_count: int = 0
    rejected: int = 0; unbuyable: int = 0; pending_open: int = 0
    @property
    def counts(self) -> dict[str, int]:
        return {"rejected": self.rejected, "unbuyable": self.unbuyable, "pendingOpen": self.pending_open}
    @property
    def should_push(self) -> bool: return self.ran and self.batch_count > 0

def is_auction_window(now: datetime, *, db_path: Optional[Path] = None) -> bool:
    local = now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)
    try:
        open_day = is_trading_day(local.date(), **({"db_path": db_path} if db_path is not None else {}))
    except RuntimeError:
        return False
    return open_day and AUCTION_WINDOW_START <= local.time().replace(tzinfo=None) < AUCTION_WINDOW_END

def run_checklist_tick(now: datetime, *, db_path: Optional[Path] = None, parquet_dir: Optional[Path] = None,
                       readings: Mapping[str, Mapping[str, Any]] = {}) -> ChecklistRunResult:
    now = now.replace(tzinfo=CN_TZ) if now.tzinfo is None else now.astimezone(CN_TZ)
    result = ChecklistRunResult(now.date(), now)
    if not is_auction_window(now, db_path=db_path): result.skipped_reason = SKIP_NOT_WINDOW; return result
    due = [p for p in packages.list_packages(state="active", db_path=db_path)
           if p["d1_trade_date"] == now.strftime("%Y%m%d")]
    if not due: result.skipped_reason = SKIP_NO_LISTING; return result
    for summary in due:
        package = packages.load_package(summary["batch_id"], db_path=db_path)
        if package is None: continue
        # Freeze markers and checklist are one transaction.  A process crash or
        # checklist validation failure therefore leaves neither half behind.
        snapshot = packages.freeze_checklist_atomic(package, trade_date=now.date(),
                                                    readings=readings, now=now, db_path=db_path)
        result.batch_count += 1
        for row in snapshot.rows:
            setattr(result, row.verdict if row.verdict != "pending_open" else "pending_open",
                    getattr(result, row.verdict if row.verdict != "pending_open" else "pending_open") + 1)
    result.ran = result.batch_count > 0
    return result

__all__ = ["AUCTION_SCOPE", "EVENT_CHECKLIST", "ChecklistRunResult", "is_auction_window", "run_checklist_tick"]
