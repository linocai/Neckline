"""B2.4 六年稳定性 & 领先性 + B2.5 2026-07-22 校准实验。

稳定性:主线不逐日乱翻(top-1 持续天数分布 / top-K 集合日翻手率)。
领先性:主线识别日 vs 板块指数后续 N 日收益(主线是否**领先于**大涨,而非事后追认)。
校准:2026-07-22 输出主线/支线,与用户五主题(科技〔半导体/光模块〕、药、电、机器人、
      材料)对照 —— 命中/漏报/误报逐项列,差异 = 调参线索(不为对齐硬调到过拟合)。

运行:python -m research.b2_analysis
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List

import polars as pl

from research import lab
from research.b2_mainline import build_mainline_panel, mainlines_on


# ======================================================================
#  B2.4 稳定性
# ======================================================================

def top1_persistence(d: pl.DataFrame, rank_col: str = "rank_all") -> Dict[str, float]:
    """top-1 板块的持续性:相邻交易日 top-1 是否相同的比例;top-1 连续持续天数分布。"""
    top1 = (
        d.filter(pl.col(rank_col) == 1)
        .select(["trade_date", "ts_code"])
        .sort("trade_date")
    )
    codes = top1["ts_code"].to_list()
    same = sum(1 for i in range(1, len(codes)) if codes[i] == codes[i - 1])
    same_rate = same / (len(codes) - 1) if len(codes) > 1 else float("nan")
    # 连续持续天数(runs)
    runs, cur = [], 1
    for i in range(1, len(codes)):
        if codes[i] == codes[i - 1]:
            cur += 1
        else:
            runs.append(cur); cur = 1
    runs.append(cur)
    runs_s = pl.Series(runs)
    return {
        "top1_same_next_day_rate": same_rate,
        "n_switches": len(runs) - 1,
        "run_median": float(runs_s.median()),
        "run_mean": float(runs_s.mean()),
        "run_max": int(runs_s.max()),
    }


def topk_turnover(d: pl.DataFrame, k: int = 5, rank_col: str = "rank_all") -> float:
    """top-K 主线集合的日翻手率 = 相邻交易日 top-K 集合的平均 Jaccard 距离(1-交集/并集)。"""
    days = d.select("trade_date").unique().sort("trade_date")["trade_date"].to_list()
    topk = {
        dt: set(d.filter((pl.col("trade_date") == dt) & (pl.col(rank_col) <= k))["ts_code"].to_list())
        for dt in days
    }
    dists = []
    for i in range(1, len(days)):
        a, b = topk[days[i - 1]], topk[days[i]]
        if not a and not b:
            continue
        jac = len(a & b) / len(a | b) if (a | b) else 1.0
        dists.append(1 - jac)
    return float(sum(dists) / len(dists)) if dists else float("nan")


# ======================================================================
#  B2.4 领先性(前瞻板块收益 by tier)
# ======================================================================

def leadership(d: pl.DataFrame, rank_col: str = "rank_all", main_k: int = 2, branch_k: int = 5) -> pl.DataFrame:
    """按主线/支线/非主线分层的前瞻 3/5 日**板块指数**收益。主线若领先 → 前瞻收益正。
    (板块指数收益 ≠ 个股可交易收益;这里只测识别器的领先性,不作可交易主张。)"""
    dd = d.with_columns(
        pl.when(pl.col(rank_col) <= main_k).then(pl.lit("main"))
        .when(pl.col(rank_col) <= branch_k).then(pl.lit("branch"))
        .otherwise(pl.lit("none")).alias("_tier_tmp")
    )
    valid = dd.filter(pl.col("board_fwd3").is_not_null())
    return (
        valid.group_by("_tier_tmp")
        .agg(
            pl.len().alias("n"),
            pl.col("board_fwd3").mean().alias("mean_fwd3"),
            (pl.col("board_fwd3") > 0).mean().alias("win_fwd3"),
            pl.col("board_fwd5").mean().alias("mean_fwd5"),
        )
        .sort("_tier_tmp")
        .rename({"_tier_tmp": "tier"})
    )


# ======================================================================
#  B2.5 校准(2026-07-22 vs 用户五主题)
# ======================================================================

USER_THEMES = {
    "科技·半导体/光模块": r"半导体|光模块|光通信|芯片|CPO|存储|先进封装|EDA|光刻|PCB|算力|数据中心|光器件",
    "药": r"创新药|医药|生物|CXO|CRO|疫苗|仿制药|减肥药|中药|医疗",
    "电": r"电力|电网|绿电|绿色电力|储能|光伏|风电|核电|特高压|固态电池|锂电|新能源",
    "机器人": r"机器人|人形|减速器|灵巧手|谐波|丝杠",
    "材料": r"稀土|锂矿|有色|钢|化工|新材料|磁材|钨|钼|石墨|碳纤维|铜|铝|金属",
}


def calibration(d: pl.DataFrame, cal: date = date(2026, 7, 22), top_k: int = 8) -> Dict:
    """校准日 top-K 主线板块命中了哪些用户主题;每个用户主题的最好排名。"""
    day = d.filter(pl.col("trade_date") == cal)
    # 三口径排名:rank_all(四信号)/ rank_clean(②③强)/ vol_share attention
    day = day.with_columns(
        pl.col("vol_share").rank(descending=True).over("trade_date").alias("att_rank")
    )
    topk_all = day.sort("rank_all").head(top_k)
    result = {"cal": cal, "topk_all": topk_all}
    # 每主题最好排名(三口径)
    theme_rows = []
    for theme, pat in USER_THEMES.items():
        sub = day.filter(pl.col("board_name").str.contains(pat))
        if sub.is_empty():
            theme_rows.append({"theme": theme, "n_boards": 0})
            continue
        theme_rows.append({
            "theme": theme,
            "n_boards": sub.height,
            "best_rank_all": int(sub["rank_all"].min()),
            "best_rank_clean": int(sub["rank_clean"].min()),
            "best_att_rank": int(sub["att_rank"].min()),
            "best_board": sub.sort("rank_all").row(0, named=True)["board_name"],
        })
    result["themes"] = pl.DataFrame(theme_rows)
    # topk_all 命中的主题(反向:top-K 板块各自属于哪个主题)
    def theme_of(name: str) -> str:
        import re
        for theme, pat in USER_THEMES.items():
            if re.search(pat, name or ""):
                return theme
        return "(未归入五主题)"
    hits = topk_all.with_columns(
        pl.col("board_name").map_elements(theme_of, return_dtype=pl.Utf8).alias("theme")
    ).select(["rank_all", "board_name", "board_age", "board_ret_20d", "board_limitup_n", "vol_share", "theme"])
    result["topk_labeled"] = hits
    return result


if __name__ == "__main__":
    d, idx_clean = build_mainline_panel(cache=True)
    print(f"清洗后概念板块 {idx_clean.height} 个;面板 {d.height} 行 "
          f"{d['trade_date'].min()}~{d['trade_date'].max()}")

    print("\n" + "=" * 70)
    print("B2.4 稳定性(主线不逐日乱翻?)")
    print("=" * 70)
    for rc in ("rank_all", "rank_clean", "rank_att"):
        p = top1_persistence(d, rc)
        tk = topk_turnover(d, 10 if rc == "rank_att" else 5, rc)
        kdesc = "top-10" if rc == "rank_att" else "top-5"
        print(f"[{rc}] top-1 次日不变率={p['top1_same_next_day_rate']:.2%} "
              f"切换{p['n_switches']}次 持续天数(中位/均值/最长)={p['run_median']:.0f}/{p['run_mean']:.1f}/{p['run_max']} "
              f"| {kdesc} 集合日翻手率={tk:.2%}")

    print("\n" + "=" * 70)
    print("B2.4 领先性(前瞻板块收益 by tier;main 应领先=前瞻正)")
    print("=" * 70)
    print("[rank_all(四信号 等权)]"); print(lab.fmt(leadership(d, "rank_all")))
    print("[rank_clean(②③强 等权)]"); print(lab.fmt(leadership(d, "rank_clean")))
    print("[rank_att(资金主战场·平滑成交额占比,main=top10/branch=top25)]")
    print(lab.fmt(leadership(d, "rank_att", main_k=10, branch_k=25)))

    print("\n" + "=" * 70)
    print("B2.5 校准实验(2026-07-22 vs 用户五主题)")
    print("=" * 70)
    cal = calibration(d)
    print("\n[top-8 主线板块(rank_all)及其主题归属]")
    print(lab.fmt(cal["topk_labeled"]))
    print("\n[资金主战场 top-10(rank_att,平滑成交额占比)——07-22]")
    att10 = (d.filter(pl.col("trade_date") == date(2026, 7, 22)).sort("rank_att")
             .select(["rank_att", "board_name", "board_age", "board_ret_20d", "board_limitup_n", "vol_share_5d"]).head(10))
    print(lab.fmt(att10))
    print("\n[用户五主题的最好识别排名(三口径:四信号/②③强/纯attention)]")
    print(lab.fmt(cal["themes"]))
