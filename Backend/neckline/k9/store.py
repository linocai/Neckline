"""K9-v3 成绩包读模型。旧运行表不是任何运行路径的来源。"""
from __future__ import annotations
from datetime import date
from pathlib import Path
from typing import Optional
from neckline.scorecard import packages

def _day(d: date) -> str: return d.strftime("%Y%m%d")
def load_run(trade_date: date, *, strategy: str = "K9", strategy_version: str = "K9-v3", db_path: Optional[Path] = None):
    for state in ("active", "settled"):
        for row in packages.list_packages(state=state, db_path=db_path):
            if row["selection_date"] == _day(trade_date):
                return {"run_id": row["batch_id"], "params_package_version": row["params_package_version"], "seated_count": row["candidate_count"], "strategy_version": "K9-v3"}
    return None
def load_listing(trade_date: date, *, strategy: str = "K9", strategy_version: str = "K9-v3", db_path: Optional[Path] = None):
    run = load_run(trade_date, db_path=db_path)
    if not run: return []
    item = packages.load_package(run["run_id"], db_path=db_path) or {}
    return [{"ts_code": x["tsCode"], "name": x["name"], "sw_l2_code": x["swL2Code"], "sw_l2_name": x["swL2Name"], "patterns": x["channels"], "primary_pattern": x["channels"][0] if x["channels"] else "", "rank": min(x["channelRanks"].values(), default=0), "tier": "", "seat_kind": None, "score": None} for x in item.get("candidates", [])]
def load_listing_codes(trade_date: date, **kwargs): return [x["ts_code"] for x in load_listing(trade_date, **kwargs)]
