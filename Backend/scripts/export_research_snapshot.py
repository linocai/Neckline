#!/usr/bin/env python3
"""Export a consistent SQLite snapshot for the offline whynotme laboratory."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.config import settings


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_snapshot(source: Path, destination: Path, *, force: bool = False) -> dict:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")
    if destination.exists() and not force:
        raise FileExistsError(f"destination already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        target_connection = sqlite3.connect(str(temporary))
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
    finally:
        source_connection.close()
    temporary.replace(destination)

    manifest = {
        "schemaVersion": 1,
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database": destination.name,
        "sha256": _sha256(destination),
        "bytes": destination.stat().st_size,
        "sourceKind": "neckline-sqlite-backup",
        "parquetReadOnlyPath": str(settings.parquet_dir.resolve()),
    }
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=settings.db_path,
                        help="source SQLite database (default: configured Neckline DB)")
    parser.add_argument("--out", type=Path, required=True,
                        help="destination snapshot path")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing destination snapshot")
    args = parser.parse_args()
    manifest = export_snapshot(args.source, args.out, force=args.force)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
