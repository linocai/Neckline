"""K7 前置 · 战役一 H11:题材生命周期五态(注意力口径)。

预注册见 `research/k7_pre_report.md` §2。新证据声明:H6 测 persist_days 标量单调性,
本假设测阶段化结构定义(含梯队断板/涨停家数),「分歧回调」态在 H6 框架里不存在。

五态(per 行业日,互斥,从上往下第一个命中;强度日/persist 复用 H6 定义):
    1 启动:强度日 & persist=1        2 发酵:强度日 & persist∈[2,3]
    3 过热:强度日 & persist≥4        4 分歧回调:非强度日 & 近2日有强度 & lu_cnt≥1
    5 退潮:非强度日 & 近5日有强度 & lu_cnt=0     0 无题材态(对照)

独立可重跑:`python research/k7p_h11_stage.py`
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import get_panel  # noqa: E402
from k7p_common import (  # noqa: E402
    add_k7p_features, attention_table, base_expr, fmt, oneword_event_expr, seg_exprs,
)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "neckline.db"
MIN_MEMBERS = 5
STRENGTH_Q = 0.80
GRID_Q = (0.85, 0.80, 0.70)
STAGE_ORDER = ["1启动", "2发酵", "3过热", "4分歧回调", "5退潮", "0无题材"]


def load_industry_map() -> pl.DataFrame:
    con = sqlite3.connect(str(DB_PATH))
    rows = con.execute("SELECT ts_code, industry FROM stock_basic").fetchall()
    con.close()
    data = [(tc, ind.strip()) for tc, ind in rows if ind and str(ind).strip()]
    return pl.DataFrame(data, schema=["ts_code", "industry"], orient="row")


def industry_states(panel: pl.DataFrame, q: float) -> pl.DataFrame:
    """(industry, trade_date) → 五态标签。"""
    # 行业日强度(H6 体例:全体有 industry & ret_1d 的票,成员数 ≥5)
    ind_daily = (
        panel.filter(pl.col("industry").is_not_null() & pl.col("ret_1d").is_not_null())
        .group_by(["trade_date", "industry"])
        .agg(pl.col("ret_1d").median().alias("med_ret"), pl.len().alias("n_members"))
        .filter(pl.col("n_members") >= MIN_MEMBERS)
    )
    thr = ind_daily.group_by("trade_date").agg(pl.col("med_ret").quantile(q).alias("thr"))
    ind_daily = ind_daily.join(thr, on="trade_date").with_columns(
        (pl.col("med_ret") >= pl.col("thr")).alias("is_str"))

    # 域内涨停家数(含一字;簇身份量)
    lu_cnt = (
        panel.filter(base_expr() & pl.col("is_limit_up") & pl.col("industry").is_not_null())
        .group_by(["trade_date", "industry"])
        .agg(pl.len().alias("lu_cnt"))
    )
    ind_daily = ind_daily.join(lu_cnt, on=["trade_date", "industry"], how="left").with_columns(
        pl.col("lu_cnt").fill_null(0))

    # persist(连续强度日数)与近 N 日有强度
    ind_daily = ind_daily.sort(["industry", "trade_date"])
    flip = (pl.col("is_str") != pl.col("is_str").shift(1).fill_null(False)).over("industry")
    ind_daily = ind_daily.with_columns(flip.cum_sum().over("industry").alias("_run_id"))
    ind_daily = ind_daily.with_columns(
        pl.col("trade_date").cum_count().over(["industry", "_run_id"]).alias("persist"))
    s1 = pl.col("is_str").shift(1).over("industry").fill_null(False)
    s2 = pl.col("is_str").shift(2).over("industry").fill_null(False)
    recent2 = s1 | s2
    recent5 = s1 | s2
    for k in (3, 4, 5):
        recent5 = recent5 | pl.col("is_str").shift(k).over("industry").fill_null(False)

    stage = (
        pl.when(pl.col("is_str") & (pl.col("persist") == 1)).then(pl.lit("1启动"))
        .when(pl.col("is_str") & (pl.col("persist") <= 3)).then(pl.lit("2发酵"))
        .when(pl.col("is_str")).then(pl.lit("3过热"))
        .when(recent2 & (pl.col("lu_cnt") >= 1)).then(pl.lit("4分歧回调"))
        .when(recent5 & (pl.col("lu_cnt") == 0)).then(pl.lit("5退潮"))
        .otherwise(pl.lit("0无题材"))
    )
    return ind_daily.with_columns(stage.alias("stage")).select(["trade_date", "industry", "stage"])


def main() -> None:
    panel = get_panel()
    imap = load_industry_map()
    panel = add_k7p_features(panel).join(imap, on="ts_code", how="left")
    dom = panel.filter(base_expr() & ~oneword_event_expr() & pl.col("industry").is_not_null())

    for q in GRID_Q:
        tag = "主判" if q == STRENGTH_Q else "敏感性"
        states = industry_states(panel, q)
        ev = dom.join(states, on=["trade_date", "industry"], how="inner")
        cnt = states.group_by("stage").agg(pl.len().alias("n_industry_days")).sort("stage")
        print(f"\n=== [{tag}] 强度阈 q={q}:行业日分布 ===")
        print(fmt(cnt, intcols=("n_industry_days",)))

        groups = [(s, ev.filter(pl.col("stage") == s)) for s in STAGE_ORDER]
        for seg, expr in seg_exprs():
            if q != STRENGTH_Q and seg != "2026分段":
                continue  # 敏感性档只打 2026 方向一致性
            print(f"\n-- 成员层注意力表 · {seg}(q={q})--")
            print(fmt(attention_table(groups, expr), intcols=("n", "n_buyable")))

        if q == STRENGTH_Q:
            # 特别对拍(二池命题):分歧回调 × 强势资格 vs 启动/发酵全体
            strong = (pl.col("limitup_count_20d") >= 2) | (pl.col("ret_20d") >= 0.25)
            special = [
                ("分歧回调×强势资格", ev.filter((pl.col("stage") == "4分歧回调") & strong)),
                ("启动+发酵全体", ev.filter(pl.col("stage").is_in(["1启动", "2发酵"]))),
                ("启动+发酵×强势资格", ev.filter(pl.col("stage").is_in(["1启动", "2发酵"]) & strong)),
            ]
            for seg, expr in seg_exprs():
                print(f"\n-- 特别对拍(后发制人) · {seg} --")
                print(fmt(attention_table(special, expr), intcols=("n", "n_buyable")))


if __name__ == "__main__":
    main()
