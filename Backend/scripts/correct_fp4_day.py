#!/usr/bin/env python3
"""Refresh one daily_basic partition and append an audited fp-4 correction.

This command is intentionally unsuitable for timers: it requires the exact
superseded pack id, an operator reason, and an external backup directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl  # noqa: E402

from neckline.config import settings  # noqa: E402
from neckline.data.market_data import day_file_path, get_market_slice, write_table_day  # noqa: E402
from neckline.data.tushare_client import ts_daily_basic_all  # noqa: E402
from neckline.db import init_schema  # noqa: E402
from neckline.facts import pack as fact_types  # noqa: E402
from neckline.facts import readiness, store, v4  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _to_polars(pdf) -> pl.DataFrame:
    frame = pl.from_pandas(pdf)
    if "trade_date" in frame.columns:
        frame = frame.with_columns(
            pl.col("trade_date").cast(pl.String).str.strptime(pl.Date, "%Y%m%d", strict=False)
        )
    return frame


def _validate(frame: pl.DataFrame, expected_codes: set[str]) -> None:
    required = {"ts_code", "trade_date", "turnover_rate_f", "free_share"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RuntimeError(f"daily_basic 返回缺列:{','.join(missing)}")
    codes = set(frame["ts_code"].drop_nulls().cast(pl.String).to_list())
    if frame["ts_code"].n_unique() != frame.height or codes != expected_codes:
        raise RuntimeError(
            f"daily_basic 代码集合不完整(expected={len(expected_codes)}, actual={len(codes)})")
    checks = {
        "free_share": pl.col("free_share").cast(pl.Float64, strict=False).is_finite()
        & (pl.col("free_share").cast(pl.Float64, strict=False) > 0),
        "turnover_rate_f": pl.col("turnover_rate_f").cast(pl.Float64, strict=False).is_finite()
        & (pl.col("turnover_rate_f").cast(pl.Float64, strict=False) >= 0),
    }
    for name, valid in checks.items():
        invalid = int(frame.select((~valid.fill_null(False)).sum()).item())
        if invalid:
            raise RuntimeError(f"daily_basic {name} 有 {invalid}/{frame.height} 行缺失或无效")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trade_date", help="YYYYMMDD")
    parser.add_argument("--expected-pack-id", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--db", default=None)
    parser.add_argument("--parquet-dir", default=None)
    args = parser.parse_args()

    trade_date = datetime.strptime(args.trade_date, "%Y%m%d").date()
    db_path = Path(args.db) if args.db else None
    parquet_dir = Path(args.parquet_dir) if args.parquet_dir else settings.parquet_dir
    backup_dir = Path(args.backup_dir).resolve()
    raw_path = day_file_path("daily_basic", trade_date, parquet_dir)
    if not raw_path.exists():
        raise RuntimeError(f"待修订 daily_basic 不存在:{raw_path}")
    if not settings.tushare_token:
        raise RuntimeError("TUSHARE_TOKEN 未配置")

    init_schema(db_path)
    current = store.load_pack(
        trade_date, pack_version="fp-4", parquet_dir=parquet_dir, db_path=db_path)
    if current.pack_id != args.expected_pack_id:
        raise RuntimeError(
            f"expected pack 不匹配(expected={args.expected_pack_id}, actual={current.pack_id})")

    daily = get_market_slice(trade_date, "daily", parquet_dir=parquet_dir)
    expected_codes = set(daily["ts_code"].drop_nulls().cast(pl.String).to_list())
    if not expected_codes:
        raise RuntimeError("当日 daily 为空，拒绝修订")
    response = ts_daily_basic_all(args.trade_date)
    if not response.ok or response.data is None:
        raise RuntimeError(f"daily_basic 拉取失败:{response.reason}")
    replacement = _to_polars(response.data)
    _validate(replacement, expected_codes)

    backup_path = backup_dir / "daily_basic" / f"{args.trade_date}.parquet"
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.exists():
        raise RuntimeError(f"备份目标已存在，拒绝覆盖:{backup_path}")
    shutil.copy2(raw_path, backup_path)
    before_sha = _sha256(backup_path)
    corrected = None
    try:
        write_table_day("daily_basic", trade_date, replacement, parquet_dir=parquet_dir)
        built = v4.build(trade_date, parquet_dir=parquet_dir, db_path=db_path)
        if not isinstance(built, fact_types.CompletePack):
            raise RuntimeError("修订后 fp-4 仍不完整:" + "；".join(built.missing))
        corrected = store.freeze_correction(
            built, expected_superseded_pack_id=args.expected_pack_id,
            correction_reason=args.reason, parquet_dir=parquet_dir, db_path=db_path)
        ready = readiness.preflight(
            trade_date, pack_version="fp-4", parquet_dir=parquet_dir, db_path=db_path)
        if not ready.ready or ready.pack_id != corrected.pack_id:
            raise RuntimeError("修订写入后 readiness 验证失败:" + "；".join(ready.gaps))
    except Exception:
        if corrected is None:
            shutil.copy2(backup_path, raw_path)
        raise

    manifest = {
        "tradeDate": args.trade_date,
        "oldPackId": args.expected_pack_id,
        "newPackId": corrected.pack_id,
        "revision": corrected.revision,
        "reason": args.reason,
        "rawBackup": str(backup_path),
        "rawBeforeSha256": before_sha,
        "rawAfterSha256": _sha256(raw_path),
        "rowCount": replacement.height,
    }
    manifest_path = backup_dir / "fp4-correction.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
