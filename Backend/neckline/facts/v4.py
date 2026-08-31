"""``fp-4`` 的独立事实合同和构建器。

fp-4 不是 fp-3 的别名：它冻结了 K9-v3 所需的可交易性、自由流通市值（元）、
交易日龄和 *as-of* 申万二级归属。历史行业修复和 60 日活跃度是策略窗口计算，
本包只保存逐日、可复算的原子事实；缺少原子事实时构建失败，绝不猜值。
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Optional

import polars as pl

from neckline.data.market_data import load_stock_basic
from neckline.db import readonly_tables
from neckline.facts import industry
from neckline.facts import pack as fp3
from neckline.facts import completeness

PACK_VERSION = "fp-4"
V4_COLUMNS = (
    "delist_date", "delist_risk", "valid_quote", "free_float_mv",
    "free_float_mv_unit", "sw_l2_member_count",
)
PACK_COLUMNS = (*fp3.PACK_COLUMNS, *V4_COLUMNS)
REQUIRED_COLUMNS = frozenset(PACK_COLUMNS)


def missing_columns(columns: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(REQUIRED_COLUMNS - set(columns)))


def columns_for(version: str) -> tuple[str, ...]:
    if version != PACK_VERSION:
        raise ValueError(f"v4 只描述 {PACK_VERSION}，收到 {version}")
    return PACK_COLUMNS


def hard_boundary_gaps(rows: pl.DataFrame) -> tuple[str, ...]:
    """Validate population and units for fields used by K9's hard boundary."""
    missing = missing_columns(rows.columns)
    if missing:
        return (f"fp-4字段缺失:{','.join(missing)}",)
    checks = {
        "amount(kCNY)": pl.col("amount").cast(pl.Float64, strict=False).is_finite()
        & (pl.col("amount").cast(pl.Float64, strict=False) > 0),
        "turnover_rate": pl.col("turnover_rate").cast(pl.Float64, strict=False).is_finite()
        & (pl.col("turnover_rate").cast(pl.Float64, strict=False) >= 0),
        "free_float_mv(CNY)": pl.col("free_float_mv").cast(pl.Float64, strict=False).is_finite()
        & (pl.col("free_float_mv").cast(pl.Float64, strict=False) > 0),
        "free_float_mv_unit": pl.col("free_float_mv_unit") == "CNY",
    }
    gaps: list[str] = []
    for name, valid in checks.items():
        invalid = int(rows.select((~valid.fill_null(False)).sum()).item())
        if invalid:
            gaps.append(f"fp-4硬边界字段 {name} 有 {invalid}/{rows.height} 行缺失或无效")
    return tuple(gaps)


def _as_of_members(trade_date: date, db_path: Optional[Path]) -> tuple[dict[str, tuple[str, str, str, str, str]], Optional[dict[str, Any]], list[str]]:
    """读取目标日不可变 SW2021 成员快照；绝不回退到当前成员表。"""
    with readonly_tables("sw_industry_member_snapshots", "sw_industry_snapshot_manifests", db_path=db_path) as conn:
        if conn is None:
            return {}, None, ["sw_industry_member_snapshots:数据库未迁移"]
        rows = conn.execute(
            "SELECT ts_code,l1_code,l1_name,l2_code,l2_name,l3_code FROM sw_industry_member_snapshots WHERE trade_date=?", (trade_date.strftime("%Y%m%d"),)
        ).fetchall()
        manifest = conn.execute(
            "SELECT content_sha256,source_id,source_generated_at,source_fetched_at,raw_file_sha256,row_count,imported_at FROM sw_industry_snapshot_manifests WHERE trade_date=?",
            (trade_date.strftime("%Y%m%d"),),
        ).fetchone()
    if not rows:
        return {}, None, [f"sw_industry_member_snapshots:{trade_date} 没有可靠 SW2021 成员快照"]
    if manifest is None:
        return {}, None, [f"sw_industry_member_snapshots:{trade_date} 缺少不可变来源 manifest"]
    result: dict[str, tuple[str, str, str, str, str]] = {}
    malformed = 0
    for code, l1c, l1n, l2c, l2n, l3c in rows:
        if not all((code,l1c,l1n,l2c,l2n,l3c)):
            malformed += 1
            continue
        result[str(code)] = (str(l1c), str(l1n), str(l2c), str(l2n), str(l3c))
    gaps: list[str] = []
    if malformed:
        gaps.append(f"sw_industry_member_snapshots:有 {malformed} 行成员身份不完整")
    if not result:
        gaps.append(f"sw_industry_member:{trade_date} 没有有效的 SW2021 成员")
    # The ledger hash includes names; retrieve them from the source table so a
    # name-only historical correction is detectable too.
    with readonly_tables("sw_industry_member_snapshots", db_path=db_path) as conn:
        identities = [] if conn is None else conn.execute(
            "SELECT ts_code,name,l1_code,l1_name,l2_code,l2_name,l3_code,l3_name FROM sw_industry_member_snapshots WHERE trade_date=? ORDER BY ts_code",
            (trade_date.strftime("%Y%m%d"),),
        ).fetchall()
    payload = {"tradeDate": trade_date.strftime("%Y%m%d"), "members": [list(row) for row in identities]}
    actual_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if int(manifest[5]) != len(identities) or str(manifest[0]) != actual_hash:
        gaps.append(f"sw_industry_member_snapshots:{trade_date} 成员内容哈希或行数与 manifest 不一致")
    source = {"contentSha256": manifest[0], "sourceId": manifest[1], "sourceGeneratedAt": manifest[2],
              "sourceFetchedAt": manifest[3], "rawFileSha256": manifest[4], "rowCount": manifest[5], "importedAt": manifest[6]}
    return result, source, gaps


def _stock_meta(trade_date: date, db_path: Optional[Path]) -> tuple[pl.DataFrame, list[str]]:
    """返回构建时可证实的时点身份事实，未来退市不倒灌历史。"""
    sb = load_stock_basic(db_path)
    if sb.is_empty():
        return pl.DataFrame(), ["stock_basic:为空"]
    status = pl.col("list_status").cast(pl.String).str.to_uppercase()
    return sb.select(["ts_code", "delist_date", "list_status"]).with_columns(
        pl.when(pl.col("delist_date").is_not_null() & (pl.col("delist_date") <= trade_date)).then(True)
        .when(status == "L").then(False)
        # P/D without an effective date cannot be projected backwards.  Null makes
        # the hard boundary reject it rather than pretending a historical verdict.
        .otherwise(None).alias("delist_risk")
    ), []


def build(trade_date: date, *, parquet_dir: Optional[Path] = None, db_path: Optional[Path] = None) -> fp3.Pack:
    """构建新的 fp-4；绝不修改已经冻结的 fp-3。"""
    members, membership_source, member_gaps = _as_of_members(trade_date, db_path)
    meta, meta_gaps = _stock_meta(trade_date, db_path)
    if member_gaps or meta_gaps:
        return fp3.IncompletePack(trade_date=trade_date, pack_version=PACK_VERSION,
                                  missing=tuple([*member_gaps, *meta_gaps]))
    mapping = pl.DataFrame({
        "ts_code": list(members), "sw_l1_code_v4": [v[0] for v in members.values()],
        "sw_l1_name_v4": [v[1] for v in members.values()], "sw_l2_code_v4": [v[2] for v in members.values()],
        "sw_l2_name_v4": [v[3] for v in members.values()], "sw_l3_code_v4": [v[4] for v in members.values()],
    })
    mapping = mapping.rename({
        "sw_l1_code_v4": "sw_l1_code", "sw_l1_name_v4": "sw_l1_name", "sw_l2_code_v4": "sw_l2_code",
        "sw_l2_name_v4": "sw_l2_name", "sw_l3_code_v4": "sw_l3_code",
    })
    l2_of = {code: (value[2], value[3]) for code, value in members.items()}
    base = fp3.build(trade_date, parquet_dir=parquet_dir, db_path=db_path,
                     _sw_override=mapping, _l2_override=l2_of)
    if not isinstance(base, fp3.CompletePack):
        return fp3.IncompletePack(trade_date=trade_date, pack_version=PACK_VERSION, missing=base.missing)
    rows = base.rows.join(meta, on="ts_code", how="left")
    finite_quote = (
        pl.col("open").is_not_null() & pl.col("high").is_not_null() & pl.col("low").is_not_null()
        & pl.col("close").is_not_null() & pl.col("pre_close").is_not_null()
        & (pl.col("open") > 0) & (pl.col("high") > 0) & (pl.col("low") > 0)
        & (pl.col("close") > 0) & (pl.col("pre_close") > 0)
    )
    rows = rows.with_columns(
        finite_quote.alias("valid_quote"),
        pl.when(pl.col("free_share").is_not_null() & (pl.col("free_share") >= 0) & (pl.col("close") > 0))
        .then(pl.col("free_share") * pl.lit(10_000.0) * pl.col("close")).otherwise(None).alias("free_float_mv"),
        pl.lit("CNY", dtype=pl.String).alias("free_float_mv_unit"),
        pl.col("delist_risk").cast(pl.Boolean),
    )
    for col in PACK_COLUMNS:
        if col not in rows.columns:
            rows = rows.with_columns(pl.lit(None).alias(col))
    rows = rows.select(list(PACK_COLUMNS)).sort("ts_code")
    boundary_gaps = hard_boundary_gaps(rows)
    if boundary_gaps:
        return fp3.IncompletePack(
            trade_date=trade_date, pack_version=PACK_VERSION, missing=boundary_gaps)
    # Unlike daily equity rows, this sidecar retains members with no daily bar
    # (halted/missing quotes).  It is frozen inside market_json, therefore the
    # fact-pack fingerprint changes if even one as-of constituent changes.
    member_snapshot: dict[str, dict[str, object]] = {}
    for code, value in members.items():
        l2_code, l2_name = value[2], value[3]
        item = member_snapshot.setdefault(l2_code, {"industryCode": l2_code, "industryName": l2_name, "memberCodes": []})
        item["memberCodes"].append(code)
    for item in member_snapshot.values():
        codes = sorted(set(item["memberCodes"]))
        item["memberCodes"] = codes
        item["memberCount"] = len(codes)
        item["memberHashSha256"] = hashlib.sha256("\n".join(codes).encode("utf-8")).hexdigest()
    market = dict(base.market)
    market["fp4"] = {"dailyAmountUnit": "kCNY", "freeFloatMarketValueUnit": "CNY", "swMembership": "as_of_date", "benchmarkSource": "indices",
                     "industryMembers": member_snapshot, "swMembershipSource": membership_source}
    return fp3.CompletePack(trade_date=trade_date, pack_version=PACK_VERSION, rows=rows,
                            industry_rows=base.industry_rows, market=market,
                            sources=(*base.sources, completeness.SourceRecord(
                                "sw_industry_member_snapshots", None, int(membership_source["rowCount"]),
                                str(membership_source["sourceFetchedAt"]), metadata=membership_source,
                            )),
                            suspend_anomaly_count=base.suspend_anomaly_count)


__all__ = ["PACK_VERSION", "PACK_COLUMNS", "REQUIRED_COLUMNS", "missing_columns", "columns_for", "hard_boundary_gaps", "build"]
