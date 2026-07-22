"""B3 · 个股主线成员判定(共动性代理为主·纯价格无洞,成分快照为辅·成分洞降级)。

主口径(B3.1,历史无洞):个股日收益 vs **所属主线板块指数**日收益的滚动相关
(20d/40d),纯价格数据,不依赖历史成分。用当前 `ths_member` 快照确定「个股属于哪个
板块」(这一步有幸存者偏差),但**共动分数本身是纯价格**——一只当前在册的成分,若历史
上与板块不共动,共动分自然低、被阈值挡掉,这是共动口径相对「纯用成分快照」的稳健处。

辅证(B3.2,明确降级):`ths_member` 当前快照做 overlap sanity——同一板块指数下,当前
在册成分 vs 非成分的共动分布,交叉验证「高共动 ↔ 真成员」。**明示**:无历史成分、
时点快照自 2026-07 起,未来做前向验证补强。

产出(B3.3):`(trade_date, ts_code, mainline_id, comovement_score, is_member)` 落
`research/_cache/mainline_members.parquet`,供 B4 注入面板。成员阈值(共动分位)粗网格定。

运行:python -m research.b3_membership
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List, Optional

import polars as pl

from neckline.config import settings

PDIR = settings.parquet_dir
CACHE_DIR = Path(__file__).resolve().parent / "_cache"
PANEL_CACHE = CACHE_DIR / "panel_full.parquet"
MAINLINE_CACHE = CACHE_DIR / "mainline_panel.parquet"
MEMBERS_CACHE = CACHE_DIR / "mainline_members.parquet"

EVER_MAINLINE_K = 15      # 「曾进 top-15 资金主战场」的板块才算候选主线(限定共动计算范围)
CORR_WINDOWS = (20, 40)   # 滚动相关窗口(粗网格)


def _rolling_corr(df: pl.DataFrame, group: List[str], x: str, y: str, w: int, out: str) -> pl.DataFrame:
    """按 group 分组的滚动 Pearson 相关(手写协方差/标准差版,min_samples=w,跨 polars
    版本稳健)。df 需已按 [*group, trade_date] 排序。"""
    df = df.with_columns((pl.col(x) * pl.col(y)).alias("_xy"),
                         (pl.col(x) ** 2).alias("_xx"), (pl.col(y) ** 2).alias("_yy"))
    mx = pl.col(x).rolling_mean(w, min_samples=w).over(group)
    my = pl.col(y).rolling_mean(w, min_samples=w).over(group)
    mxy = pl.col("_xy").rolling_mean(w, min_samples=w).over(group)
    mxx = pl.col("_xx").rolling_mean(w, min_samples=w).over(group)
    myy = pl.col("_yy").rolling_mean(w, min_samples=w).over(group)
    cov = mxy - mx * my
    vx = (mxx - mx * mx).clip(lower_bound=1e-12)
    vy = (myy - my * my).clip(lower_bound=1e-12)
    return df.with_columns((cov / (vx.sqrt() * vy.sqrt())).alias(out)).drop("_xy", "_xx", "_yy")


def build_comovement(ever_k: int = EVER_MAINLINE_K) -> pl.DataFrame:
    """对「曾进 top-ever_k 资金主战场」的板块的当前成分对 (con_code, index_code),
    计算个股收益 vs 板块指数收益的滚动相关(20d/40d)。返回长表
    (con_code, index_code, trade_date, corr_20, corr_40, board_rank_att, board_limitup_n)。"""
    mainline = pl.read_parquet(MAINLINE_CACHE)
    ever = set(mainline.filter(pl.col("rank_att") <= ever_k)["ts_code"].to_list())
    member = pl.read_parquet(PDIR / "ths_member.parquet").select(["index_code", "con_code"])
    member = member.filter(pl.col("index_code").is_in(list(ever))).unique()

    # 个股收益(用 qfq ret_1d,干净有界;pct_chg 有极端脏值)
    stock = pl.read_parquet(PANEL_CACHE, columns=["ts_code", "trade_date", "ret_1d", "is_limit_up"]).rename(
        {"ts_code": "con_code", "ret_1d": "s_ret"}
    )
    # 板块指数收益 + 当日注意力排名 + 涨停家数
    board_ret = pl.read_parquet(PDIR / "ths_daily.parquet", columns=["ts_code", "trade_date", "pct_change"]).rename(
        {"ts_code": "index_code", "pct_change": "b_ret"}
    )
    board_meta = mainline.select(["ts_code", "trade_date", "rank_att", "board_limitup_n"]).rename(
        {"ts_code": "index_code", "rank_att": "board_rank_att"}
    )

    # 长表:成分对 × 交易日
    long = (
        member.join(stock, on="con_code", how="inner")
        .join(board_ret, on=["index_code", "trade_date"], how="inner")
        .join(board_meta, on=["index_code", "trade_date"], how="left")
        .sort(["con_code", "index_code", "trade_date"])
    )
    for w in CORR_WINDOWS:
        long = _rolling_corr(long, ["con_code", "index_code"], "s_ret", "b_ret", w, f"corr_{w}")
    return long


def build_members(daily_main_k: int = 10, corr_threshold: float = 0.5,
                  ever_k: int = EVER_MAINLINE_K, comovement: Optional[pl.DataFrame] = None) -> pl.DataFrame:
    """成员判定表(B3.3)。规则:某交易日 T,若个股所属某板块**当日** rank_att ≤
    daily_main_k(=当日主战场主线)且该 (个股,板块) 的 corr_20 ≥ corr_threshold →
    该股是主线成员;个股在多个主线板块时取共动最高者为 mainline_id / comovement_score。
    产出 (trade_date, ts_code, mainline_id, comovement_score, is_member)。"""
    long = comovement if comovement is not None else build_comovement(ever_k)
    # 当日属于主战场主线的成分对
    cur = long.filter(pl.col("board_rank_att") <= daily_main_k)
    # 每 (股, 日) 取共动最高的主线板块
    cur = cur.filter(pl.col("corr_20").is_not_null())
    best = (
        cur.sort(["con_code", "trade_date", "corr_20"], descending=[False, False, True])
        .group_by(["con_code", "trade_date"], maintain_order=True)
        .agg(pl.col("index_code").first().alias("mainline_id"),
             pl.col("corr_20").first().alias("comovement_score"))
    )
    best = best.with_columns((pl.col("comovement_score") >= corr_threshold).alias("is_member"))
    return best.rename({"con_code": "ts_code"}).sort(["trade_date", "ts_code"])


# ======================================================================
#  B3.2 成分快照辅证(overlap sanity):高共动 ↔ 真成员?
# ======================================================================

def sanity_overlap(sample_boards: Optional[List[str]] = None, ever_k: int = EVER_MAINLINE_K) -> pl.DataFrame:
    """对若干主线板块,比较**当前在册成分** vs **全市场非成分**与该板块指数的共动分布。
    若高共动主要落在在册成分上 → 共动口径合理(交叉验证)。为控算量,对全市场股票
    只算与 sample_boards 的相关,分成员/非成员两组比均值分位。"""
    mainline = pl.read_parquet(MAINLINE_CACHE)
    if sample_boards is None:
        # 取样本外末期最常见的几个主战场板块
        last = mainline.filter(pl.col("rank_att") <= 6).group_by("ts_code").len().sort("len", descending=True)
        sample_boards = last.head(5)["ts_code"].to_list()
    member = pl.read_parquet(PDIR / "ths_member.parquet").select(["index_code", "con_code"]).unique()
    stock = pl.read_parquet(PANEL_CACHE, columns=["ts_code", "trade_date", "ret_1d"]).rename({"ret_1d": "s_ret"})
    board_ret = pl.read_parquet(PDIR / "ths_daily.parquet", columns=["ts_code", "trade_date", "pct_change"])

    rows = []
    name_map = dict(zip(mainline["ts_code"].to_list(), mainline["board_name"].to_list()))
    all_codes = stock["con_code" if "con_code" in stock.columns else "ts_code"].unique().to_list()
    for b in sample_boards:
        br = board_ret.filter(pl.col("ts_code") == b).select(["trade_date", "pct_change"]).rename({"pct_change": "b_ret"})
        if br.height < 60:
            continue
        # 全市场股票与该板块指数的样本外整体相关(单值,窗口=全样本外简化;区分成员/非成员)
        merged = stock.join(br, on="trade_date", how="inner")
        # 只取样本外窗口降算量
        merged = merged.filter(pl.col("trade_date") >= date(2025, 1, 1))
        corr = (
            merged.group_by("ts_code")
            .agg(pl.corr("s_ret", "b_ret").alias("corr"), pl.len().alias("n"))
            .filter(pl.col("n") >= 60)
        )
        mem_codes = set(member.filter(pl.col("index_code") == b)["con_code"].to_list())
        corr = corr.with_columns(pl.col("ts_code").is_in(list(mem_codes)).alias("is_member"))
        agg = corr.group_by("is_member").agg(
            pl.len().alias("n"), pl.col("corr").mean().alias("mean_corr"),
            pl.col("corr").median().alias("median_corr"),
            pl.col("corr").quantile(0.9).alias("p90_corr"),
        )
        for r in agg.iter_rows(named=True):
            rows.append({"board": name_map.get(b, b), "is_member": r["is_member"], "n": r["n"],
                         "mean_corr": r["mean_corr"], "median_corr": r["median_corr"], "p90_corr": r["p90_corr"]})
    return pl.DataFrame(rows)


if __name__ == "__main__":
    from research import lab
    print(f"[B3.1] 计算共动性(ever-mainline top-{EVER_MAINLINE_K} 板块的成分对,滚动相关 20d/40d)...")
    long = build_comovement()
    print(f"  长表 {long.height} 行,{long['con_code'].n_unique()} 只成分股 × "
          f"{long['index_code'].n_unique()} 个主线板块")
    long.write_parquet(CACHE_DIR / "comovement_long.parquet")

    print("\n[B3.3] 成员判定表(粗网格 daily_main_k × corr_threshold)...")
    grid_rows = []
    for k in (5, 10, 15):
        for thr in (0.3, 0.5, 0.7):
            mem = build_members(daily_main_k=k, corr_threshold=thr, comovement=long)
            n_member = int(mem["is_member"].sum())
            n_daystock = mem.height
            grid_rows.append({"daily_main_k": k, "corr_threshold": thr,
                              "n_daystock_in_mainline_board": n_daystock,
                              "n_is_member": n_member,
                              "member_frac": n_member / n_daystock if n_daystock else float("nan")})
    print(lab.fmt(pl.DataFrame(grid_rows)))

    # 采纳集(供 B4):daily_main_k=10, corr_threshold=0.5
    members = build_members(daily_main_k=10, corr_threshold=0.5, comovement=long)
    members.write_parquet(MEMBERS_CACHE)
    print(f"\n采纳集(k=10,thr=0.5)成员表 → {MEMBERS_CACHE.name}:{members.height} 行,"
          f"is_member={int(members['is_member'].sum())}")

    print("\n[B3.2] 成分快照 overlap sanity(高共动 ↔ 真成员?样本外)...")
    print(lab.fmt(sanity_overlap()))
