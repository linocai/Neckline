"""K9-v3 D1 9:29 checklist, bound only to one immutable package."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Sequence

CHECKLIST_VERDICTS = ("rejected", "unbuyable", "pending_open")
CHECKLIST_FOOTNOTE = "K9-v3：9:29 仅冻结拒绝、确认不可买与待开盘状态；10:00 结果及 D1 收盘状态追加到同一成绩包。"
_LABELS = {"rejected": "已触发放弃", "unbuyable": "确认不可买", "pending_open": "待开盘后观察"}

@dataclass(frozen=True)
class ChecklistRow:
    ts_code: str
    name: str | None
    channels: Sequence[str]
    channel_ranks: Mapping[str, int]
    verdict: str
    readings: Mapping[str, Any]
    playbook_revision: int
    frozen_playbook: Mapping[str, Any]
    def as_dict(self) -> dict[str, Any]:
        return {"tsCode": self.ts_code, "name": self.name, "channels": list(self.channels),
                "channelRanks": dict(self.channel_ranks), "verdict": self.verdict,
                "readings": dict(self.readings), "playbookRevision": self.playbook_revision,
                "frozenPlaybook": dict(self.frozen_playbook),
                "playbookVersion": self.playbook_revision, "pattern": self.channels[0] if self.channels else "",
                "segment": _LABELS[self.verdict], "quoteState": ""}

@dataclass(frozen=True)
class Checklist:
    batch_id: str
    selection_date: str
    signal_trade_date: str
    trade_date: str
    captured_at: str
    rows: Sequence[ChecklistRow]
    def as_dict(self) -> dict[str, Any]:
        groups = {v: [] for v in CHECKLIST_VERDICTS}
        for row in self.rows:
            groups[row.verdict].append(row.as_dict())
        return {"batchId": self.batch_id, "selectionDate": self.selection_date,
                "signalTradeDate": self.signal_trade_date, "tradeDate": self.trade_date,
                "capturedAt": self.captured_at, "strategyVersion": "K9-v3",
                "d0Date": self.selection_date, "dataQuality": "unavailable", "footnote": CHECKLIST_FOOTNOTE,
                "noQuoteCodes": [], "noPlaybookCodes": [], "notes": [],
                "segments": [{"verdict": v, "label": _LABELS[v], "rows": groups[v]} for v in CHECKLIST_VERDICTS]}

def build_checklist(package: Mapping[str, Any], *, trade_date: date,
                    readings: Mapping[str, Mapping[str, Any]] = {}) -> Checklist:
    rows = []
    for item in package["candidates"]:
        raw = dict(readings.get(item["tsCode"], {}))
        rules = (item.get("playbook") or {}).get("openVerdict") or {}
        invalidation = rules.get("rejectBelow")
        price = raw.get("auctionPrice")
        limit_up = rules.get("unbuyableAtOrAbove")
        if price is not None and invalidation is not None and float(price) < float(invalidation):
            verdict = "rejected"
        elif price is not None and limit_up is not None and float(price) >= float(limit_up):
            verdict = "unbuyable"
        else:
            verdict = "pending_open"
        rows.append(ChecklistRow(item["tsCode"], item.get("name"), item["channels"],
                                 item["channelRanks"], verdict, raw,
                                 int(item.get("playbook", {}).get("revision", 0)),
                                 dict(item.get("playbook") or {})))
    return Checklist(package["batch_id"], package["selection_date"], package["signal_trade_date"],
                     trade_date.strftime("%Y%m%d"), datetime.now().isoformat(timespec="seconds"), rows)

__all__ = ["CHECKLIST_VERDICTS", "CHECKLIST_FOOTNOTE", "ChecklistRow", "Checklist", "build_checklist"]
