#!/usr/bin/env python3
"""Import an audited, complete historical SW2021 L2 membership JSON file.

This is the only production write entry point for historical membership.  The
file must include source id plus offset-aware generatedAt/fetchedAt, then each
full daily snapshot's ``complete: true`` and ``expectedMemberCount``.  See
``neckline.data.sw_industry.import_historical_snapshots`` for the exact input
format and validation contract.  It never synthesizes dates from current data.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.data.sw_industry import import_historical_snapshots  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True, help="complete historical SW2021 snapshot JSON")
    parser.add_argument("--db", type=Path, default=None, help="target SQLite database (defaults to configured DB)")
    args = parser.parse_args()
    try:
        result = import_historical_snapshots(args.file, db_path=args.db)
    except (OSError, ValueError) as exc:
        print(f"[sw-history-import] REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
