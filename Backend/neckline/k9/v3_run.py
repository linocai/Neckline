"""K9-v3 deterministic engine: P2/P3/P4 only, no tiers, floors or cross-channel score."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import hashlib

import polars as pl

from neckline.facts import store as fact_store
from neckline.facts.v4 import PACK_COLUMNS, PACK_VERSION, missing_columns
from neckline.k9.v3_params import V3Params
from neckline.scorecard import packages


class PackUnavailable(RuntimeError): pass
class PackageCreationError(RuntimeError): pass


@dataclass(frozen=True)
class V3Hit:
    ts_code: str
    name: Optional[str]
    sw_l2_code: Optional[str]
    sw_l2_name: Optional[str]
    channel: str
    rank: int
    score: float
    baseline: Mapping[str, Any]
    thresholds: Mapping[str, Any]


def _dates(frame: pl.DataFrame, days: int) -> list[object]:
    return sorted(frame["trade_date"].unique().to_list())[-days:]


def _window(frame: pl.DataFrame, days: int) -> pl.DataFrame:
    return frame.filter(pl.col("trade_date").is_in(_dates(frame, days)))


def _listing_cutoff(history: pl.DataFrame, required_days: int) -> object:
    """Return the oldest frozen day needed to prove listing-age eligibility.

    K9 only asks whether a stock has reached the approved threshold.  It does
    not need an exact lifetime trading-day count, and must never turn that
    bounded question into a dependency on decades of exchange calendars.
    """
    days = sorted(history["trade_date"].drop_nulls().unique().to_list())
    if required_days <= 0 or len(days) < required_days:
        raise PackUnavailable(
            f"{PACK_VERSION} 上市历史证明不足：需要 {required_days} 个冻结交易日，只有 {len(days)}"
        )
    return days[-required_days]


def _require_facts(trade_date: date, *, parquet_dir: Optional[Path], db_path: Optional[Path], minimum_days: int) -> tuple[object, pl.DataFrame]:
    pack = fact_store.load_pack(trade_date, pack_version=PACK_VERSION, parquet_dir=parquet_dir, db_path=db_path)
    absent = missing_columns(pack.rows.columns)
    if absent: raise PackUnavailable(f"{PACK_VERSION} 字段缺失:{','.join(absent)}")
    rows = fact_store.list_packs(pack_version=PACK_VERSION, db_path=db_path)
    dates = [date(int(x[0][:4]),int(x[0][4:6]),int(x[0][6:])) for x in rows if x[0] <= trade_date.strftime("%Y%m%d")]
    if len(dates) < minimum_days: raise PackUnavailable(f"{PACK_VERSION} 冻结历史不足：需要 {minimum_days} 个交易日，只有 {len(dates)}")
    start = sorted(dates)[-minimum_days]
    history = fact_store.load_pack_range(start, trade_date, as_of=trade_date, columns=PACK_COLUMNS, pack_version=PACK_VERSION, parquet_dir=parquet_dir, db_path=db_path)
    if history.is_empty() or len(history["trade_date"].unique()) < minimum_days:
        raise PackUnavailable(f"{PACK_VERSION} 冻结历史不完整")
    return pack, history


def _boundary(history: pl.DataFrame, cfg: Mapping[str, Any]) -> pl.DataFrame:
    """单一硬边界；任何不可证实的输入都直接拒绝。"""
    act, liq = cfg["activity"], cfg["d0Liquidity"]
    listing_cutoff = _listing_cutoff(history, int(cfg["newListingTradingDays"]))
    h = _window(history, int(act["windowDays"]))
    metrics = h.group_by("ts_code").agg(
        pl.col("amount").median().alias("_median_amount"),
        pl.col("turnover_rate").median().alias("_median_participation"),
        pl.len().alias("_valid_days"),
    ).filter(pl.col("_valid_days") >= int(act["minimumValidDays"]))
    metrics = metrics.with_columns(
        (pl.col("_median_amount").rank("average") / pl.len()).alias("_amount_pct"),
        (pl.col("_median_participation").rank("average") / pl.len()).alias("_participation_pct"),
    ).with_columns((pl.col("_amount_pct") * float(act["amountWeight"]) + pl.col("_participation_pct") * float(act["participationWeight"])).alias("_activity_pct"))
    today = (
        history.filter(pl.col("trade_date") == history["trade_date"].max())
        .join(metrics, on="ts_code", how="left")
        # TuShare daily.amount is published in kCNY; K9 parameter thresholds
        # and free_float_mv are CNY.  Convert once at the hard-boundary seam.
        .with_columns((pl.col("amount") * pl.lit(1_000.0)).alias("_amount_cny"))
    )
    return today.filter(
        ~pl.col("board").is_in(["STAR", "BSE"])
        & ~pl.col("is_st").fill_null(True) & ~pl.col("delist_risk").fill_null(True)
        & pl.col("list_date").is_not_null() & (pl.col("list_date") <= pl.lit(listing_cutoff))
        & (pl.col("close") > 0) & (pl.col("close") <= 100) & pl.col("valid_quote").fill_null(False)
        & (pl.col("suspend_flag") != "S") & pl.col("sw_l2_code").is_not_null()
        & ~pl.col("sw_l2_code").is_in(list(cfg["excludedL2Codes"]))
        & pl.col("_activity_pct").is_not_null() & (pl.col("_activity_pct") >= float(act["excludeBottomPct"]))
        & (pl.col("_amount_cny") >= float(liq["minimumAmountCny"]))
        & pl.col("free_float_mv").is_not_null() & (pl.col("_amount_cny") >= pl.col("free_float_mv") * float(liq["freeFloatMarketValueRatio"]))
        & ~pl.col("is_limit_up").fill_null(True) & ~pl.col("is_limit_down").fill_null(True)
    )


def _rank(frame: pl.DataFrame, channel: str, score: pl.Expr, quota: int, locked: set[str], thresholds: Mapping[str, Any]) -> list[V3Hit]:
    if quota <= 0: return []
    ranked = frame.filter(~pl.col("ts_code").is_in(sorted(locked))).with_columns(score.alias("_score")).filter(pl.col("_score").is_finite()).sort(["_score","ts_code"], descending=[True,False]).head(quota)
    result: list[V3Hit] = []
    for i,row in enumerate(ranked.iter_rows(named=True),1):
        baseline = {k: row.get(k) for k in ("trade_date","close","pre_close","limit_up_price","limit_down_price","free_float_mv","ret_1d","sw_l2_median_ret","rel_strength_1d")}
        baseline = {k:(v.isoformat() if hasattr(v,"isoformat") else v) for k,v in baseline.items()}
        result.append(V3Hit(str(row["ts_code"]),row.get("name"),row.get("sw_l2_code"),row.get("sw_l2_name"),channel,i,float(row["_score"]),baseline,dict(thresholds)))
    return result


def _p2(history: pl.DataFrame, pool: pl.DataFrame, cfg: Mapping[str, Any], quota: int, locked: set[str]) -> list[V3Hit]:
    rec, rank = cfg["recall"],cfg["ranking"]; h=_window(history,int(rec["windowDays"]))
    volume_days=int(rec["volumeBaselineDays"])
    required_days = len(_dates(history, int(rec["windowDays"])))
    prior=(history.sort(["ts_code","trade_date"]).with_columns(
        pl.when(pl.col("vol")>0).then(pl.col("vol")).otherwise(None).shift(1).rolling_mean(window_size=volume_days,min_samples=volume_days).over("ts_code").alias("_prior_vol_avg"),
        pl.when(pl.col("vol")>0).then(1).otherwise(0).shift(1).rolling_sum(window_size=volume_days,min_samples=volume_days).over("ts_code").alias("_prior_vol_days"),
    ).filter(pl.col("trade_date")==history["trade_date"].max()).select("ts_code","_prior_vol_avg","_prior_vol_days"))
    # K9 3.0.3: compound stock and industry returns over the same complete
    # window, then subtract; summing daily relative returns is invalid.
    stats=(h.filter(pl.col("ret_1d").is_finite() & pl.col("sw_l2_median_ret").is_finite())
           .sort(["ts_code", "trade_date"])
           .group_by("ts_code").agg(
               pl.col("close").first().alias("_first"), pl.col("high").max().alias("_high"),
               pl.col("low").min().alias("_low"), pl.len().alias("_relative_days"),
               pl.col("trade_date").n_unique().alias("_relative_unique_days"),
               (pl.col("ret_1d") + 1).product().sub(1).alias("_stock_return"),
               (pl.col("sw_l2_median_ret") + 1).product().sub(1).alias("_industry_return"),
               pl.col("ret_1d").mean().alias("_ret_mean"),
           ).with_columns((pl.col("_stock_return") - pl.col("_industry_return")).alias("_industry_rel"))
           .filter((pl.col("_relative_days") == required_days) & (pl.col("_relative_unique_days") == required_days)))
    x=pool.join(stats,on="ts_code",how="inner").with_columns(
        (pl.col("close")/pl.col("_first")-1).alias("_drop"),(pl.col("close")/pl.col("_high")-1).alias("_drawdown"),
        ((pl.col("close")-pl.col("_low"))/(pl.col("_high")-pl.col("_low"))).fill_nan(1.0).alias("_close_loc"),
        (pl.col("ret_1d")-pl.col("_ret_mean")).alias("_deceleration"),
    ).join(prior,on="ts_code",how="left").with_columns((pl.col("vol")/pl.col("_prior_vol_avg")).alias("_volume_multiple")).filter(((pl.col("_drop") <= -float(rec["minCumulativeDropPct"])) | (pl.col("_drawdown") <= -float(rec["minDrawdownPct"]))) & (pl.col("_industry_rel") <= -float(rec["minIndustryUnderperformancePct"])) & (pl.col("_prior_vol_days")>=volume_days) & (pl.col("_volume_multiple") >= float(rec["minVolumeMultiple"])) & ((pl.col("_close_loc") >= float(rec["supportCloseLocationPct"])) | (pl.col("ret_1d") >= float(rec["supportDailyReturnPct"]))))
    # _close_loc 是从窗口低点的回收幅度；数值越高才越符合 K9-v3 的低点回收。
    score=(-pl.min_horizontal("_drop","_drawdown")*float(rank["oversoldDepthWeight"]) + -pl.col("_industry_rel")*float(rank["industryUnderperformanceWeight"]) + pl.col("_close_loc")*float(rank["lowRecoveryWeight"]) + pl.col("_deceleration")*float(rank["declineDecelerationWeight"]) + pl.col("turnover_rate").rank("average")/pl.len()*float(rank["turnoverWeight"]))
    return _rank(x,"p2",score,quota,locked,{"recall":rec,"ranking":rank})


def _p3(history: pl.DataFrame, pool: pl.DataFrame, cfg: Mapping[str, Any], quota: int, locked: set[str]) -> list[V3Hit]:
    identity,opportunity,rank=cfg["identity"],cfg["opportunity"],cfg["ranking"]; h=_window(history,int(identity["windowDays"])); event_days=int(identity["eventWindowDays"]); base_days=int(identity["volumeBaselineDays"]); hot=identity["hotness"]
    daily=(history.sort(["ts_code","trade_date"]).with_columns(
        (pl.col("amount").rank("average").over("trade_date")/pl.len().over("trade_date")).alias("_amount_pct"),
        (pl.col("turnover_rate").rank("average").over("trade_date")/pl.len().over("trade_date")).alias("_turnover_pct"),
        pl.when(pl.col("vol")>0).then(pl.col("vol")).otherwise(None).shift(1).rolling_mean(window_size=base_days,min_samples=base_days).over("ts_code").alias("_prior_vol_avg"),
        pl.when(pl.col("vol")>0).then(1).otherwise(0).shift(1).rolling_sum(window_size=base_days,min_samples=base_days).over("ts_code").alias("_prior_vol_days"),
    ).with_columns((pl.col("_amount_pct")*float(hot["amountWeight"])+pl.col("_turnover_pct")*float(hot["turnoverWeight"])).alias("_hotness")))
    daily=daily.filter(pl.col("trade_date").is_in(_dates(history,int(identity["windowDays"]))))
    event=daily.filter(pl.col("trade_date").is_in(_dates(history,event_days))).with_columns(
        ((pl.col("close") / pl.col("pre_close") - 1).abs()).alias("_move"),
        ((pl.col("high") - pl.col("low")) / pl.col("pre_close")).alias("_amp"),
        (pl.col("vol") / pl.col("_prior_vol_avg")).alias("_volmul"),
        pl.when(
            (pl.col("pre_close") > 0)
            & (pl.col("limit_up_price") > 0)
            & (pl.col("limit_down_price") > 0)
        ).then(
            pl.max_horizontal(
                (pl.col("limit_up_price") / pl.col("pre_close") - 1).abs(),
                (pl.col("limit_down_price") / pl.col("pre_close") - 1).abs(),
            )
        ).otherwise(None).alias("_limit_width"),
    ).with_columns(
        (pl.col("_move") / pl.col("_limit_width")).alias("_move_vs_limit"),
        (pl.col("_amp") / pl.col("_limit_width")).alias("_amp_vs_limit"),
    )
    per=daily.group_by("ts_code").agg((pl.col("_hotness") >= 1-float(identity["topPct"])).sum().alias("_hot_days"),pl.col("_hotness").last().alias("_d0_hot"),pl.col("high").max().alias("_high"),pl.col("close").first().alias("_first"))
    event_condition = (
        (pl.col("_move_vs_limit") >= float(identity["largeMoveLimitWidthPct"])).fill_null(False)
        | (pl.col("_amp_vs_limit") >= float(identity["largeMoveAmplitudePct"])).fill_null(False)
        | ((pl.col("_prior_vol_days") >= base_days)
           & (pl.col("_volmul") >= float(identity["largeMoveVolumeMultiple"])))
    )
    events=event.group_by("ts_code").agg(
        event_condition.any().alias("_event"),
        pl.col("is_limit_down").any().alias("_recent_down"),
    )
    x=pool.join(per,on="ts_code",how="inner").join(events,on="ts_code",how="left").with_columns((pl.col("close")/pl.col("_high")-1).alias("_overextended"),pl.col("net_amount_rate").fill_null(-1.0).alias("_capital"),((pl.col("close")-pl.col("low"))/(pl.col("high")-pl.col("low"))).fill_nan(0.0).alias("_structure"))
    ident=(pl.col("_hot_days") >= int(identity["minHotDays"])) & (pl.col("_d0_hot") >= 1-float(identity["topPct"])) & pl.col("_event").fill_null(False)
    direction=(pl.col("ret_1d") > 0) if opportunity["requireDirectionResolved"] else pl.lit(True)
    x=x.filter(ident & direction & (pl.col("_overextended") >= -float(opportunity["maxOverextendedPct"])) & (pl.col("rel_strength_1d") >= float(opportunity["minRelativeLeadership"])) & (pl.col("_capital") >= float(opportunity["minCapitalRetention"])) & (pl.col("_structure") >= float(opportunity["minStructureIntegrity"])))
    score=(pl.col("ret_1d").rank("average")/pl.len()*float(rank["directionWeight"]) + (1+pl.col("_overextended")).rank("average")/pl.len()*float(rank["notOverextendedWeight"]) + pl.col("rel_strength_1d").rank("average")/pl.len()*float(rank["relativeLeadershipWeight"]) + pl.col("_capital").rank("average")/pl.len()*float(rank["capitalRetentionWeight"]) + pl.col("_structure").rank("average")/pl.len()*float(rank["structureIntegrityWeight"]) - pl.when(pl.col("_recent_down")).then(float(rank["recentLimitDownRiskDeduction"])).otherwise(0.0))
    return _rank(x,"p3",score,quota,locked,{"identity":identity,"opportunity":opportunity,"ranking":rank})


def _benchmark_series(history: pl.DataFrame, index_code: str, *, parquet_dir: Optional[Path], db_path: Optional[Path]) -> pl.DataFrame:
    """Read the approved benchmark from every frozen day, never from a live index feed."""
    rows=[]
    for day in sorted(history["trade_date"].unique().to_list()):
        pack=fact_store.load_pack(day,pack_version=PACK_VERSION,parquet_dir=parquet_dir,db_path=db_path)
        match=next((x for x in pack.market.get("indices",[]) if isinstance(x,Mapping) and x.get("tsCode")==index_code),None)
        if not isinstance(match,Mapping) or not isinstance(match.get("pctChg"),(int,float)):
            raise PackUnavailable(f"fp-4 {day} 未冻结批准市场基准 {index_code}")
        # index_daily.pct_chg is percentage points; strategy ratios use decimal throughout.
        rows.append({"trade_date":day,"_benchmark_ret":float(match["pctChg"])/100.0})
    return pl.DataFrame(rows)


def _p4(history: pl.DataFrame, pool: pl.DataFrame, cfg: Mapping[str, Any], quota: int, locked: set[str], *, parquet_dir: Optional[Path], db_path: Optional[Path]) -> list[V3Hit]:
    benchmark=cfg["benchmark"]["indexCode"]
    ind,stock,rank=cfg["industry"],cfg["stock"],cfg["ranking"]; h=_window(history,int(ind["windowDays"]))
    required_days = len(_dates(history, int(ind["windowDays"])))
    benchmark_series=_benchmark_series(h,benchmark,parquet_dir=parquet_dir,db_path=db_path)
    series=(h.group_by(["trade_date","sw_l2_code"]).agg(
                pl.col("sw_l2_median_ret").first().alias("_median"),
                pl.col("ret_1d").gt(0).mean().alias("_breadth"),
                pl.col("sw_l2_member_count").first().alias("_members"),
            ).join(benchmark_series,on="trade_date",how="inner")
            .filter(pl.col("_median").is_finite() & pl.col("_benchmark_ret").is_finite()
                    & pl.col("_breadth").is_finite() & pl.col("_members").is_not_null())
            # The one-day repair leg is intentionally still today's raw
            # industry-minus-benchmark difference.
            .with_columns((pl.col("_median")-pl.col("_benchmark_ret")).alias("_repair"))
            .sort(["sw_l2_code", "trade_date"]))
    selected=(series.group_by("sw_l2_code").agg(
                pl.len().alias("_days"), pl.col("trade_date").n_unique().alias("_unique_days"),
                (pl.col("_median") + 1).product().sub(1).alias("_industry_return"),
                (pl.col("_benchmark_ret") + 1).product().sub(1).alias("_benchmark_return"),
                pl.col("_breadth").last().alias("_breadth"), pl.col("_repair").last().alias("_repair"),
                pl.col("_members").last().alias("_members"),
            ).with_columns((pl.col("_industry_return") - pl.col("_benchmark_return")).alias("_period"))
            # A missing/non-finite day makes this industry unusable; never
            # shorten one side of the comparison.
            .filter((pl.col("_days") == required_days) & (pl.col("_unique_days") == required_days)
                    & (pl.col("_members")>=int(ind["minMembers"]))&(pl.col("_breadth")>=float(ind["minBreadthPct"]))
                    &(pl.col("_period")<=-float(ind["minOversoldRelativePct"]))&(pl.col("_repair")>=float(ind["minRepairPct"])))
            .sort(["_period","sw_l2_code"]).head(int(ind["maxIndustries"])))
    x=pool.join(selected,on="sw_l2_code",how="inner").with_columns((pl.col("amount").rank("average").over("sw_l2_code")/pl.len().over("sw_l2_code")).alias("_liquidity"),(pl.col("turnover_rate").rank("average").over("sw_l2_code")/pl.len().over("sw_l2_code")).alias("_core"))
    x=x.filter((pl.col("rel_strength_1d")>=float(stock["minRelativeStrength"]))&(pl.col("_core")>=float(stock["minCoreScore"]))&(pl.col("_liquidity")>=float(stock["minLiquidityScore"])))
    score=pl.col("rel_strength_1d").rank("average")/pl.len()*float(rank["relativeStrengthWeight"])+pl.col("_core")*float(rank["coreWeight"])+pl.col("_liquidity")*float(rank["liquidityWeight"])
    # P4 quota is deliberately two-stage: a single industry cannot consume the
    # complete channel allowance before the global mechanical order is applied.
    capped = (x.filter(~pl.col("ts_code").is_in(sorted(locked))).with_columns(score.alias("_p4_score"))
             .sort(["sw_l2_code", "_p4_score", "ts_code"], descending=[False, True, False])
             .group_by("sw_l2_code", maintain_order=True).head(int(ind["perIndustryStockCap"])))
    return _rank(capped,"p4",score,quota,set(),{"benchmark":cfg["benchmark"],"industry":ind,"stock":stock,"ranking":rank,"playbookBounds":cfg["playbookBounds"]})


def compute(trade_date: date, *, selection_date: date, params: V3Params,
            parquet_dir: Optional[Path] = None, db_path: Optional[Path] = None) -> list[V3Hit]:
    p2_recall = params.channels["p2"]["recall"]
    p3_identity = params.channels["p3"]["identity"]
    # Every parameter shape remains validated by ``v3_params``.  Data history,
    # however, is required only for the always-on shared boundary and channels
    # that this approved package explicitly enables; a parked P3/P4 must not
    # turn an otherwise runnable P2 package into a false not_run.
    windows = [
        int(params.raw["boundary"]["activity"]["windowDays"]),
        int(params.raw["boundary"]["newListingTradingDays"]),
    ]
    if params.channels["p2"]["enabled"]:
        windows.append(max(int(p2_recall["windowDays"]), int(p2_recall["volumeBaselineDays"]) + 1))
    if params.channels["p3"]["enabled"]:
        windows.append(max(int(p3_identity["windowDays"]), int(p3_identity["eventWindowDays"]) + int(p3_identity["volumeBaselineDays"])))
    if params.channels["p4"]["enabled"]:
        windows.append(int(params.channels["p4"]["industry"]["windowDays"]))
    pack,history=_require_facts(trade_date,parquet_dir=parquet_dir,db_path=db_path,minimum_days=max(windows))
    # Facts are keyed to the closed trading day; the in-flight lock is keyed to
    # public formal package sequence, so Sunday must not collapse to Friday.
    pool=_boundary(history,params.raw["boundary"]); locked=packages.recent_locked_codes(before_selection_date=selection_date,db_path=db_path); hits: list[V3Hit]=[]
    if params.channels["p2"]["enabled"]: hits+=_p2(history,pool,params.channels["p2"],int(params.quotas["p2"]),locked)
    if params.channels["p3"]["enabled"]: hits+=_p3(history,pool,params.channels["p3"],int(params.quotas["p3"]),locked)
    if params.channels["p4"]["enabled"]: hits+=_p4(history,pool,params.channels["p4"],int(params.quotas["p4"]),locked,parquet_dir=parquet_dir,db_path=db_path)
    return hits


def _p4_industry_evidence(*, signal_trade_date: date, pack_id: str, hits: Sequence[V3Hit],
                          params: V3Params, parquet_dir: Optional[Path], db_path: Optional[Path]) -> dict[str, Any]:
    """Freeze the *as-of fp-4 rows*, never today's SW member table, for P4 D1.

    A selection package is the only authority for later P4 member aggregation.
    The check against fp-4's industry member count catches a partial fact pack
    before it can produce a half-observable Day 1 package.
    """
    p4_hits = [hit for hit in hits if hit.channel == "p4"]
    if not p4_hits:
        return {}
    pack = fact_store.load_pack(signal_trade_date, pack_version=PACK_VERSION,
                                parquet_dir=parquet_dir, db_path=db_path)
    if pack.pack_id != pack_id:
        raise PackageCreationError("D0 未创建：fp-4 身份与运行 packId 不一致")
    sidecar = ((pack.market or {}).get("fp4") or {}).get("industryMembers")
    if not isinstance(sidecar, Mapping):
        raise PackageCreationError("D0 未创建：fp-4 缺少完整 SW L2 冻结成员 sidecar")
    benchmark = (params.channels.get("p4") or {}).get("benchmark")
    if not isinstance(benchmark, Mapping) or not isinstance(benchmark.get("indexCode"), str) or not benchmark["indexCode"].strip():
        raise PackageCreationError("D0 未创建：P4 批准基准身份缺失")
    industries: dict[str, Any] = {}
    for code in sorted({str(hit.sw_l2_code) for hit in p4_hits if hit.sw_l2_code}):
        item = sidecar.get(code)
        if not isinstance(item, Mapping):
            raise PackageCreationError(f"D0 未创建：fp-4 P4 行业 {code} 成员 sidecar 缺失")
        member_codes = item.get("memberCodes")
        names = {str(item.get("industryName") or "")}
        if (not isinstance(member_codes, list) or not member_codes or any(not isinstance(value, str) or not value for value in member_codes)
                or len(set(member_codes)) != len(member_codes) or int(item.get("memberCount") or 0) != len(member_codes)
                or not next(iter(names))):
            raise PackageCreationError(f"D0 未创建：fp-4 P4 行业 {code} 成员不完整或数量不一致")
        member_codes = sorted(member_codes)
        digest = hashlib.sha256("\n".join(member_codes).encode("utf-8")).hexdigest()
        if str(item.get("memberHashSha256") or "") != digest:
            raise PackageCreationError(f"D0 未创建：fp-4 P4 行业 {code} 成员哈希不一致")
        industries[code] = {
            "industryCode": code,
            "industryName": next(iter(names)),
            "signalTradeDate": signal_trade_date.strftime("%Y%m%d"),
            "memberCodes": member_codes,
            "memberHashSha256": hashlib.sha256("\n".join(member_codes).encode("utf-8")).hexdigest(),
            "memberCount": len(member_codes),
            "fp4MemberCount": len(member_codes),
            "aggregationSchemaVersion": "k9-v3-p4-member-aggregate-v1",
            "benchmark": {"indexCode": benchmark["indexCode"].strip()},
        }
    if len(industries) != len({str(hit.sw_l2_code) for hit in p4_hits if hit.sw_l2_code}):
        raise PackageCreationError("D0 未创建：P4 行业身份缺失")
    return {"aggregationSchemaVersion": "k9-v3-p4-member-aggregate-v1",
            "signalTradeDate": signal_trade_date.strftime("%Y%m%d"),
            "benchmark": {"indexCode": benchmark["indexCode"].strip()}, "industries": industries}


def create_package(*, batch_id: str, selection_date: date, signal_trade_date: date, d1_trade_date: date, d2_trade_date: date, params: V3Params, pack_id: str, hits: Sequence[V3Hit], playbooks: Optional[Mapping[str,Mapping[str,Any]]] = None, playbook_provenance: Optional[Mapping[str, Any]] = None, revision: int = 1, parquet_dir: Optional[Path] = None, db_path: Optional[Path] = None) -> None:
    """Create immutable D0 only after every selected code has a real frozen playbook."""
    grouped: dict[str,list[V3Hit]]={}
    for hit in hits: grouped.setdefault(hit.ts_code,[]).append(hit)
    if grouped and (playbooks is None or any(code not in playbooks or not playbooks[code] for code in grouped)):
        raise PackageCreationError("D0 未创建：候选缺少冻结预案")
    candidates=[]
    for code,channels in sorted(grouped.items()):
        first=channels[0]
        candidates.append(packages.Candidate(code,first.name,first.sw_l2_code,first.sw_l2_name,[x.channel for x in channels],{x.channel:x.rank for x in channels},dict(playbooks[code]),dict(first.baseline),{"channels":{x.channel:x.thresholds for x in channels},"scores":{x.channel:x.score for x in channels}}))
    p4_evidence = _p4_industry_evidence(signal_trade_date=signal_trade_date, pack_id=pack_id, hits=hits,
                                         params=params, parquet_dir=parquet_dir, db_path=db_path)
    contract = {"strategyVersion":"K9-v3","factPackVersion":"fp-4","labelContractVersion":"d2-v2",
                "parameters":params.raw}
    if p4_evidence:
        contract["p4IndustryEvidence"] = p4_evidence
    packages.create_batch(batch_id=batch_id,selection_date=selection_date,signal_trade_date=signal_trade_date,d1_trade_date=d1_trade_date,d2_trade_date=d2_trade_date,revision=revision,params_package_version=params.package_version,params_sha256=params.source_sha256,pack_id=pack_id,frozen_contract=contract,candidates=candidates,playbook_provenance=playbook_provenance,db_path=db_path)


__all__=["V3Hit","PackUnavailable","PackageCreationError","compute","create_package"]
