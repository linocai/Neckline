#!/usr/bin/env python3
"""Append complete card price plans to one already-published report.

This is a narrow maintenance command.  It does not import or call the evening
pipeline, selection aggregate, Tavily, Tiering, or APNs.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import settings  # noqa: E402
from neckline.llm.budget import LEDGER_REASON, BudgetLedger  # noqa: E402
from neckline.llm.factory import get_provider  # noqa: E402
from neckline.llm.router import TASK_SCRIPT  # noqa: E402
from neckline.report.card_plan_repair import (  # noqa: E402
    CardRepair,
    apply_report_card_repairs,
    atomic_write_text,
    build_repair_context,
    json_sha256,
    patch_report_markdown,
    patch_report_snapshot,
    repair_frozen_card,
    TARGETED_REPAIR_SYSTEM_PROMPT,
)
from neckline.report.store import load_report_by_str  # noqa: E402
from neckline.selection.basket_card import (  # noqa: E402
    LLM_OK,
    run_card_llm,
    trade_plan_missing_pieces,
)
from neckline.selection.basket_store import load_basket_card, load_baskets_for_date  # noqa: E402
from neckline.selection.run_store import latest_published_run_id  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-date", required=True, help="market/selection key YYYYMMDD")
    parser.add_argument("--report-date", required=True, help="visible report date YYYYMMDD")
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-basket-count", required=True, type=int)
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument("--report-file", type=Path)
    parser.add_argument("--apply", action="store_true", help="required to mutate DB/report file")
    return parser.parse_args()


def main() -> int:
    args = _args()
    trade_day = datetime.strptime(args.trade_date, "%Y%m%d").date()
    datetime.strptime(args.report_date, "%Y%m%d")
    visible_run = latest_published_run_id(args.trade_date, db_path=args.db)
    if visible_run != args.expected_run_id:
        raise SystemExit(f"published selection run changed: {visible_run!r}")
    report = load_report_by_str(args.trade_date, db_path=args.db)
    if report is None or report.get("report_date") != args.report_date:
        raise SystemExit("target report identity not found")
    refs = load_baskets_for_date(args.trade_date, db_path=args.db)
    if len(refs) != args.expected_basket_count:
        raise SystemExit(f"basket count changed: expected {args.expected_basket_count}, got {len(refs)}")
    snapshot = report.get("basket_daily") or {}
    if len(snapshot.get("baskets") or []) != args.expected_basket_count:
        raise SystemExit("report snapshot basket count does not match frozen baskets")
    provider = get_provider(TASK_SCRIPT, db_path=args.db)
    if provider is None:
        raise SystemExit("TASK_SCRIPT provider is unavailable")
    ledger = BudgetLedger()
    ledger.limits[LEDGER_REASON] = 24 * 60 * 60
    repairs = {}
    for index, ref in enumerate(refs, start=1):
        row = load_basket_card(ref.basket_id, db_path=args.db)
        if row is None or row.get("card_corrupt") or not isinstance(row.get("card"), dict):
            raise SystemExit(f"basket {ref.basket_id} has no readable frozen card")
        old_card = row["card"]
        old_version = int(row["version"])
        context = build_repair_context(old_card, trade_date=trade_day)
        narrative, payload, stage = run_card_llm(
            context, provider=provider, ledger=ledger,
            system_prompt=TARGETED_REPAIR_SYSTEM_PROMPT,
        )
        if stage != LLM_OK or payload is None:
            raise SystemExit(f"basket {ref.basket_id} card repair LLM failed: {stage}")
        new_card = repair_frozen_card(
            old_card, payload, narrative=narrative, version=old_version + 1,
        )
        missing = trade_plan_missing_pieces(new_card)
        if missing:
            raise SystemExit(f"basket {ref.basket_id} repaired card is incomplete: {missing}")
        repairs[ref.basket_id] = CardRepair(
            basket_id=ref.basket_id, from_version=old_version, to_version=old_version + 1,
            expected_card_sha256=json_sha256(old_card), card=new_card,
        )
        print(f"prepared {index}/{len(refs)}: basket_id={ref.basket_id} version={old_version + 1}")

    snapshot_sha = json_sha256(snapshot)
    repaired_snapshot = patch_report_snapshot(snapshot, repairs)
    repaired_markdown = patch_report_markdown(
        report["markdown"], repaired_snapshot, trade_date=trade_day,
    )
    summary = {
        "tradeDate": args.trade_date, "reportDate": args.report_date,
        "selectionRunId": visible_run, "basketCount": len(repairs),
        "memberCount": sum(len(repair.card.get("members") or []) for repair in repairs.values()),
        "fromVersions": sorted({repair.from_version for repair in repairs.values()}),
        "toVersions": sorted({repair.to_version for repair in repairs.values()}),
        "applied": bool(args.apply), "selectionRerun": False, "notificationSent": False,
    }
    if not args.apply:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    apply_report_card_repairs(
        trade_date=args.trade_date, report_date=args.report_date,
        expected_snapshot_sha256=snapshot_sha, repairs=repairs,
        snapshot=repaired_snapshot, markdown=repaired_markdown, db_path=args.db,
    )
    report_file = args.report_file or settings.data_dir / "reports" / f"{args.report_date}.md"
    atomic_write_text(report_file, repaired_markdown)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
