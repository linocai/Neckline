"""P2 / 1.6 板块年龄因子(同花顺概念板块)。

诚实的数据约束(先声明):`ths_member` 是【当前】成分快照,TuShare 600 档无历史成分,
故"股票→板块"的历史映射会带幸存者/前视偏差。因此:
  · 主分析走【板块指数本身】(无成分映射问题,干净):板块年龄 = 板块指数连续站上 MA20
    的交易日数;度量"板块启动第 N 天"的前瞻板块收益,回答 P2「早期加分 / 4-5 天后降权」。
  · 股票级联动(当前成分映射)仅作【探索性】参考,明确标注被幸存者偏差污染,不作定论。

运行:python -m research.p2_sector_age
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from neckline.config import settings
from research import lab

PDIR = settings.parquet_dir


def load_board_daily() -> pl.DataFrame:
    p = PDIR / "ths_daily.parquet"
    if not p.exists():
        raise SystemExit("缺 ths_daily.parquet,先跑 scripts/backfill_concept.py")
    return pl.read_parquet(p).sort(["ts_code", "trade_date"])


def add_board_age(bd: pl.DataFrame) -> pl.DataFrame:
    """板块年龄 = 板块指数连续站上 MA20 的交易日数(regime streak);+ 前瞻板块收益。"""
    bd = bd.with_columns(pl.col("close").rolling_mean(20, min_samples=20).over("ts_code").alias("ma20"))
    bd = bd.with_columns((pl.col("close") > pl.col("ma20")).alias("above"))
    # streak 分组:above 翻转处开新组,组内序号+1 即"启动第几天"
    bd = bd.with_columns(
        (pl.col("above") != pl.col("above").shift(1).over("ts_code")).fill_null(True).alias("_flip")
    )
    bd = bd.with_columns(pl.col("_flip").cast(pl.Int32).cum_sum().over("ts_code").alias("_grp"))
    bd = bd.with_columns(
        pl.when(pl.col("above"))
        .then(pl.int_range(0, pl.len()).over(["ts_code", "_grp"]) + 1)
        .otherwise(0)
        .alias("board_age")
    )
    # 板块 20 日动量(热度)+ 前瞻 3 日板块收益
    bd = bd.with_columns(
        (pl.col("close") / pl.col("close").shift(20).over("ts_code") - 1).alias("board_ret_20d"),
        (pl.col("close").shift(-3).over("ts_code") / pl.col("close") - 1).alias("board_fwd3"),
    )
    return bd


def age_bucket(col: str = "board_age") -> pl.Expr:
    return (
        pl.when(pl.col(col) == 0).then(pl.lit("0-未启动"))
        .when(pl.col(col) <= 1).then(pl.lit("1"))
        .when(pl.col(col) <= 2).then(pl.lit("2"))
        .when(pl.col(col) <= 3).then(pl.lit("3"))
        .when(pl.col(col) <= 5).then(pl.lit("4-5"))
        .when(pl.col(col) <= 10).then(pl.lit("6-10"))
        .otherwise(pl.lit("11+"))
        .alias("age_bucket")
    )


def main():
    bd = add_board_age(load_board_daily())
    print(f"板块指数日线:{bd.height} 行,{bd['ts_code'].n_unique()} 个概念板块,"
          f"{bd['trade_date'].min()} ~ {bd['trade_date'].max()}")

    # ---- 板块级:按板块年龄分桶的前瞻 3 日板块收益(全体启动中的板块)----
    valid = bd.filter(pl.col("board_fwd3").is_not_null() & pl.col("ma20").is_not_null())
    tbl = (
        valid.with_columns(age_bucket())
        .group_by("age_bucket")
        .agg(pl.len().alias("n"), pl.col("board_fwd3").mean().alias("mean_fwd3"),
             pl.col("board_fwd3").median().alias("median_fwd3"),
             (pl.col("board_fwd3") > 0).mean().alias("win_rate"))
        .sort("age_bucket")
    )
    print("\n[板块级] 按板块年龄(连续站上MA20天数)分桶的前瞻3日板块收益")
    print(lab.fmt(tbl))

    # ---- 只看"热板块"(当日 board_ret_20d 处于横截面前 20%):早期 vs 晚期 ----
    hot = valid.with_columns(
        (pl.col("board_ret_20d").rank(descending=True).over("trade_date")
         / pl.col("board_ret_20d").count().over("trade_date")).alias("hot_rank")
    ).filter(pl.col("hot_rank") <= 0.20)
    tbl2 = (
        hot.with_columns(age_bucket())
        .group_by("age_bucket")
        .agg(pl.len().alias("n"), pl.col("board_fwd3").mean().alias("mean_fwd3"),
             (pl.col("board_fwd3") > 0).mean().alias("win_rate"))
        .sort("age_bucket")
    )
    print("\n[板块级·仅热板块前20%] 按板块年龄分桶的前瞻3日板块收益(P2 核心问题)")
    print(lab.fmt(tbl2))


if __name__ == "__main__":
    main()
