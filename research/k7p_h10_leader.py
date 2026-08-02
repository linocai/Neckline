"""K7 前置 · 战役一 H10:龙头结构——簇内头名 vs 后排跟风(注意力口径)。

预注册见 `research/k7_pre_report.md` §1。新证据声明:K2 测的是板块层无次日领先性,
本假设测**簇内成员角色层**(同一涨停簇内,龙头 vs 后排的相对延续),K2 未测过。

定义(§1 预注册,tie-break 定死保证可复现):
    · 簇 = (trade_date, industry),当日域内涨停家数 ≥3(敏感性 {3,5};含一字)。
    · 龙头三定义:L1 连板高度 / L2 成交额 / L3 RS20 簇内最大;
      tie-break:指标降序 → amount 降序 → ts_code 升序。
    · 主判配对量:per 簇日,龙头 fwd_c_ret_3 − 其余成员 fwd_c_ret_3 中位数。

独立可重跑:`python research/k7p_h10_leader.py`
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import List

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import get_panel  # noqa: E402
from k7p_common import add_k7p_features, attention_table, base_expr, fmt, seg_exprs  # noqa: E402

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "neckline.db"
MIN_CLUSTER = 3           # 主判:当日行业涨停家数 ≥3
GRID_CLUSTER = (3, 5)     # 敏感性
LEADER_DEFS = {
    "L1连板高度": "consec_limit_up_days",
    "L2成交额": "amount",
    "L3_RS20": "ret_20d",
}


def load_industry_map() -> pl.DataFrame:
    con = sqlite3.connect(str(DB_PATH))
    rows = con.execute("SELECT ts_code, industry FROM stock_basic").fetchall()
    con.close()
    data = [(tc, ind.strip()) for tc, ind in rows if ind and str(ind).strip()]
    return pl.DataFrame(data, schema=["ts_code", "industry"], orient="row")


def build_clusters(panel: pl.DataFrame, min_cluster: int) -> pl.DataFrame:
    """当日涨停簇成员(域内,含一字;行业成员涨停家数 ≥ min_cluster)。"""
    lu = panel.filter(base_expr() & pl.col("is_limit_up") & pl.col("industry").is_not_null())
    sizes = lu.group_by(["trade_date", "industry"]).agg(pl.len().alias("cluster_n"))
    return lu.join(sizes.filter(pl.col("cluster_n") >= min_cluster), on=["trade_date", "industry"], how="inner")


def rank_in_cluster(cl: pl.DataFrame, metric: str) -> pl.DataFrame:
    """簇内排名(tie-break:指标降序 → amount 降序 → ts_code 升序),pos=1 为龙头。"""
    df = cl.sort(
        ["trade_date", "industry", metric, "amount", "ts_code"],
        descending=[False, False, True, True, False],
        nulls_last=True,
    )
    return df.with_columns(pl.col("ts_code").cum_count().over(["trade_date", "industry"]).alias("pos"))


def paired_stats(ranked: pl.DataFrame, ret_col: str) -> pl.DataFrame:
    """per 簇日配对差:龙头 − 后排中位数;按分段汇总。"""
    leaders = ranked.filter(pl.col("pos") == 1).select(
        ["trade_date", "industry", "year", pl.col(ret_col).alias("leader_ret")])
    followers = (
        ranked.filter(pl.col("pos") > 1)
        .group_by(["trade_date", "industry"])
        .agg(pl.col(ret_col).median().alias("follower_med"))
    )
    pairs = leaders.join(followers, on=["trade_date", "industry"], how="inner").with_columns(
        (pl.col("leader_ret") - pl.col("follower_med")).alias("diff"))
    pairs = pairs.filter(pl.col("diff").is_not_null())
    rows: List[dict] = []
    for seg, expr in seg_exprs():
        sub = pairs.filter(expr)
        n = sub.height
        rows.append({
            "seg": seg, "n_cluster": n,
            "diff_mean": float(sub["diff"].mean()) if n else float("nan"),
            "diff_med": float(sub["diff"].median()) if n else float("nan"),
            "win_share": float((sub["diff"] > 0).mean()) if n else float("nan"),
        })
    return pl.DataFrame(rows)


def main() -> None:
    panel = get_panel()
    imap = load_industry_map()
    panel = add_k7p_features(panel).join(imap, on="ts_code", how="left")

    for min_cluster in GRID_CLUSTER:
        tag = "主判" if min_cluster == MIN_CLUSTER else "敏感性"
        cl = build_clusters(panel, min_cluster)
        n_days = cl.select(pl.col("trade_date").n_unique()).item()
        n_clusters = cl.group_by(["trade_date", "industry"]).agg(pl.len()).height
        print(f"\n=== [{tag}] 簇阈值 ≥{min_cluster}:{n_clusters} 个簇日,覆盖 {n_days} 个交易日,"
              f"成员行 {cl.height} ===")
        for name, metric in LEADER_DEFS.items():
            ranked = rank_in_cluster(cl, metric)
            print(f"\n-- {name} · 配对差(龙头 fwd_c_ret_3 − 后排中位) --")
            print(fmt(paired_stats(ranked, "fwd_c_ret_3"), intcols=("n_cluster",)))
            print(f"-- {name} · 配对差(fwd_c_ret_1) --")
            print(fmt(paired_stats(ranked, "fwd_c_ret_1"), intcols=("n_cluster",)))

        # 注意力全量表(主判阈值才打,龙头用 L1;后排 = pos>1)
        if min_cluster == MIN_CLUSTER:
            ranked = rank_in_cluster(cl, LEADER_DEFS["L1连板高度"])
            groups = [("龙头(L1)", ranked.filter(pl.col("pos") == 1)),
                      ("后排", ranked.filter(pl.col("pos") > 1))]
            for seg, expr in seg_exprs():
                print(f"\n-- 注意力全量表 · {seg}(L1 定义)--")
                print(fmt(attention_table(groups, expr), intcols=("n", "n_buyable")))


if __name__ == "__main__":
    main()
