"""B1 · 情绪仪表盘三态阈值定量化(EOD 全回测;兼答阶段 1 遗留⑤「市场择时正解」)。

阶段 1 判决:大盘 MA20 实时闸门样本内外双双更差(滞后指标不可交易),但「市场状态
确实分层」为真。B1 检验:**领先型情绪指标**做成实时开仓闸门,能否成功(MA20 失败在
滞后,情绪领先)。

B1.1 抽一个**向量化纯函数** `build_sentiment_panel`,在 `limit_derived` + `daily` 上一次
性算出**每交易日一行**的情绪面板——指标定义与 `neckline.report.sentiment.compute_sentiment`
**逐字一致**(只换成批量),`validate_against_production()` 对若干日对拍两路必须相等。
不改 `report/sentiment.py` 生产逻辑。

运行:python -m research.b1_sentiment
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import polars as pl

from neckline.data.market_data import scan_table_range, load_trade_cal_days
from neckline.report import sentiment as prod
from neckline.strategy.momentum import MomentumConfig
from research import lab
from neckline.research.panel import (
    SAMPLE_IN_START, SAMPLE_IN_END, SAMPLE_OUT_START, SAMPLE_OUT_END,
)

CACHE = Path(__file__).resolve().parent / "_cache" / "sentiment_daily.parquet"

FULL, HALF, REST = prod.FULL, prod.HALF, prod.REST


# ======================================================================
#  B1.1 向量化情绪日序列(指标定义与 compute_sentiment 逐字一致)
# ======================================================================

def build_sentiment_panel(
    start: date = SAMPLE_IN_START,
    end: date = SAMPLE_OUT_END,
) -> pl.DataFrame:
    """每交易日一行的情绪面板。指标口径与 `compute_sentiment` 逐字一致:
      · limit_up_count  = 当日 is_limit_up 家数(limit_derived)
      · limit_down_count= 当日 is_limit_down 家数
      · zaban_count     = 当日 is_zaban 家数
      · zaban_rate      = zaban_count / (zaban_count + limit_up_count),分母 0 → 0
      · max_consec      = 当日涨停票 consec_limit_up_days 的最大值(无涨停→0)
      · prev_limit_up_premium_avg = 对【前一交易日涨停】的票,mean(今日 close / 昨日 close - 1)
        (用**原始 daily 收盘**,与 compute_sentiment 一致;前日无涨停/两日任一 close 缺失→None)

    全部只用 ≤ 当日数据,无前视。前一交易日按**全局交易日历**取(与 compute_sentiment
    的 prev_trading_day 同口径,非逐票 shift)。
    """
    ld = scan_table_range("limit_derived", start, end)
    daily = scan_table_range("daily", start, end).select(["ts_code", "trade_date", "close"])

    # —— 每日涨停/跌停/炸板家数 + 最高连板 ——
    counts = (
        ld.group_by("trade_date")
        .agg(
            pl.col("is_limit_up").sum().alias("limit_up_count"),
            pl.col("is_limit_down").sum().alias("limit_down_count"),
            pl.col("is_zaban").sum().alias("zaban_count"),
            pl.col("consec_limit_up_days").filter(pl.col("is_limit_up")).max().alias("max_consec"),
        )
    )
    counts = counts.with_columns(
        pl.col("limit_up_count").cast(pl.Int64),
        pl.col("limit_down_count").cast(pl.Int64),
        pl.col("zaban_count").cast(pl.Int64),
        pl.col("max_consec").fill_null(0).cast(pl.Int64),
    ).with_columns(
        pl.when((pl.col("zaban_count") + pl.col("limit_up_count")) > 0)
        .then(pl.col("zaban_count") / (pl.col("zaban_count") + pl.col("limit_up_count")))
        .otherwise(0.0)
        .alias("zaban_rate")
    )

    # —— 前一交易日涨停股今日平均溢价(全局日历 prev/next 映射)——
    cal = [d for d in load_trade_cal_days() if start <= d <= end]
    cal_df = pl.DataFrame({"trade_date": sorted(cal)}).with_columns(
        pl.col("trade_date").shift(-1).alias("next_date")  # D 的下一个交易日
    )
    lu = ld.filter(pl.col("is_limit_up")).select(["ts_code", "trade_date"])
    # 昨日(=D)涨停票,其 D 收盘 c0
    lu = lu.join(daily.rename({"close": "c0"}), on=["ts_code", "trade_date"], how="left")
    # D → target 日 T = next_date
    lu = lu.join(cal_df, on="trade_date", how="left")
    # target 日 T 的收盘 c1
    lu = lu.join(
        daily.rename({"trade_date": "next_date", "close": "c1"}),
        on=["ts_code", "next_date"], how="left",
    )
    lu = lu.filter(pl.col("c0").is_not_null() & pl.col("c1").is_not_null() & (pl.col("c0") > 0))
    lu = lu.with_columns((pl.col("c1") / pl.col("c0") - 1).alias("ret"))
    prem = (
        lu.group_by("next_date")
        .agg(
            pl.col("ret").mean().alias("prev_limit_up_premium_avg"),
            pl.col("ret").len().alias("prev_limit_up_sample"),
        )
        .rename({"next_date": "trade_date"})
    )

    out = counts.join(prem, on="trade_date", how="left").sort("trade_date")
    out = out.with_columns(pl.col("prev_limit_up_sample").fill_null(0).cast(pl.Int64))
    return out


# ======================================================================
#  B1.2 三态判据(粗网格,阈值可注入;默认 = 生产 sentiment.py 起点值)
# ======================================================================

@dataclass(frozen=True)
class TierThresholds:
    min_lu_full: int = prod.MIN_LIMIT_UP_FOR_FULL         # 40
    max_ld_full: int = prod.MAX_LIMIT_DOWN_FOR_FULL       # 10
    max_zaban_full: float = prod.MAX_ZABAN_RATE_FOR_FULL  # 0.30
    min_lu_rest: int = prod.MIN_LIMIT_UP_FOR_REST         # 15
    min_zaban_rest: float = prod.MIN_ZABAN_RATE_FOR_REST  # 0.50
    premium_warn: float = prod.PREMIUM_WARN_THRESHOLD     # -0.02


_TIER_ORDER = (REST, HALF, FULL)


def _downgrade_expr(tier: pl.Expr) -> pl.Expr:
    return (
        pl.when(tier == FULL).then(pl.lit(HALF))
        .when(tier == HALF).then(pl.lit(REST))
        .otherwise(pl.lit(REST))
    )


def assign_tier(panel: pl.DataFrame, th: TierThresholds = TierThresholds()) -> pl.DataFrame:
    """向量化三态判定 + 溢价降级,逐字对应 sentiment._base_tier / _downgrade。"""
    base = (
        pl.when(
            (pl.col("limit_up_count") < th.min_lu_rest)
            | (pl.col("zaban_rate") >= th.min_zaban_rest)
            | (pl.col("limit_down_count") > pl.col("limit_up_count"))
        ).then(pl.lit(REST))
        .when(
            (pl.col("limit_up_count") >= th.min_lu_full)
            & (pl.col("zaban_rate") <= th.max_zaban_full)
            & (pl.col("limit_down_count") <= th.max_ld_full)
        ).then(pl.lit(FULL))
        .otherwise(pl.lit(HALF))
    )
    df = panel.with_columns(base.alias("_base_tier"))
    # 溢价降级:premium 非空且 ≤ 警戒线 → 降一档
    warn = pl.col("prev_limit_up_premium_avg").is_not_null() & (
        pl.col("prev_limit_up_premium_avg") <= th.premium_warn
    )
    df = df.with_columns(
        pl.when(warn).then(_downgrade_expr(pl.col("_base_tier")))
        .otherwise(pl.col("_base_tier"))
        .alias("tier")
    )
    return df


# ======================================================================
#  验证:向量化 vs 生产 compute_sentiment 逐字对拍
# ======================================================================

def validate_against_production(sample_dates: List[date], panel: Optional[pl.DataFrame] = None) -> List[dict]:
    """对若干交易日,把向量化面板 + assign_tier 与生产 compute_sentiment 逐字段对拍。
    返回每日 diff 记录(空 mismatch = 通过)。"""
    if panel is None:
        panel = assign_tier(build_sentiment_panel())
    pmap = {r["trade_date"]: r for r in panel.iter_rows(named=True)}
    results = []
    for d in sample_dates:
        prod_sd = prod.compute_sentiment(d)
        row = pmap.get(d)
        rec: dict = {"date": d}
        if row is None:
            rec["mismatch"] = "vectorized panel missing date"
            results.append(rec)
            continue
        checks = {
            "limit_up_count": (row["limit_up_count"], prod_sd.limit_up_count),
            "limit_down_count": (row["limit_down_count"], prod_sd.limit_down_count),
            "zaban_count": (row["zaban_count"], prod_sd.zaban_count),
            "max_consec": (row["max_consec"], prod_sd.max_consec_limit_up),
            "tier": (row["tier"], prod_sd.position_quota),
        }
        # zaban_rate / premium 浮点容差比对
        mism = []
        for k, (a, b) in checks.items():
            if a != b:
                mism.append(f"{k}: vec={a} prod={b}")
        if abs(row["zaban_rate"] - prod_sd.zaban_rate) > 1e-9:
            mism.append(f"zaban_rate: vec={row['zaban_rate']} prod={prod_sd.zaban_rate}")
        pa, pb = row["prev_limit_up_premium_avg"], prod_sd.prev_limit_up_premium_avg
        if (pa is None) != (pb is None):
            mism.append(f"premium None-mismatch: vec={pa} prod={pb}")
        elif pa is not None and pb is not None and abs(pa - pb) > 1e-9:
            mism.append(f"premium: vec={pa} prod={pb}")
        rec["mismatch"] = "; ".join(mism) if mism else ""
        results.append(rec)
    return results


if __name__ == "__main__":
    import sys
    print("[B1.1] 构建向量化情绪面板 2020-2026 ...")
    panel = build_sentiment_panel()
    panel_t = assign_tier(panel)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    panel_t.write_parquet(CACHE)
    print(f"  情绪面板 {panel_t.height} 行,{panel_t['trade_date'].min()} ~ {panel_t['trade_date'].max()} → {CACHE.name}")

    # 验证:随机抽若干日对拍生产
    import random
    random.seed(42)
    all_dates = panel_t["trade_date"].to_list()
    sample = sorted(random.sample(all_dates, min(12, len(all_dates))))
    print("\n[验证] 向量化 vs 生产 compute_sentiment 逐字对拍:")
    res = validate_against_production(sample, panel_t)
    nbad = 0
    for r in res:
        status = "OK" if not r["mismatch"] else f"MISMATCH: {r['mismatch']}"
        if r["mismatch"]:
            nbad += 1
        print(f"  {r['date']}: {status}")
    print(f"\n对拍结果:{len(res)-nbad}/{len(res)} 通过")
    if nbad:
        sys.exit(1)
