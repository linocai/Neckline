"""K4 前置 H3:挂低单逆向选择(执行层)。

预注册见 `research/k4_pre_report.md` §3。用户下单习惯:挂单价低于现价 δ∈{0.5%,1%},
等回踩成交。待证假设(我方提出):能成交的不成比例是转弱的,真启动挂不到 → 挂低单
= 专接下坠 + 错过起飞(逆向选择)。

口径(严格同持有窗,见报告 §0 口径偏离登记):两腿都退出在 **T+(1+N) 开盘**
(面板 fwd 既有约定,N∈{2,3});唯一区别是入场价。
    · 决策日 D:域内 & vol_ratio_5≥1.5 & turnover∈[5%,10%] & close>ma20 & 非一字板。
    · 挂单价 = close_D×(1-δ);成交判定:fwd_low_1≤挂单价 且 D+1 非停牌、非一字跌停。
    · 成交组(A):入场=挂单价(预注册),退出=T+(1+N)开盘。
    · 市价对照(B):同一批 D 全体,入场=D+1开盘(fwd_ret_N),同退出。
    · 市价-填上的名(C):仅成交子集按市价入场(隔离 δ 折扣,看是否有出场端拖累)。
    · 未成交错过(D):未成交且可市价买入子集的市价收益(挂低单错过了什么)。
稳健性:入场=min(D+1开盘, 挂单价) 现实成交价(gap down 开在挂单价下方时取更优价)。

独立可重跑:`python research/k4p_h3_limit_order.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import get_panel  # noqa: E402
from k4p_common import (  # noqa: E402
    DEFAULT_COST_ONESIDE, add_k4p_features, base_expr, oneword_event_expr, fmt, year_2026_expr,
)

DELTAS = (0.005, 0.010)
NS = (2, 3)
COST2 = 2 * DEFAULT_COST_ONESIDE


def decision_universe(panel: pl.DataFrame) -> pl.DataFrame:
    return panel.filter(
        base_expr()
        & (pl.col("vol_ratio_5") >= 1.5)
        & (pl.col("turnover_rate") >= 5.0)
        & (pl.col("turnover_rate") <= 10.0)
        & (pl.col("close") > pl.col("ma20"))
        & ~oneword_event_expr()
    )


def _mean_net(series: pl.Series) -> dict:
    net = series - COST2
    n = net.len()
    if n == 0:
        return {"n": 0, "win": float("nan"), "mean_net": float("nan"),
                "p5": float("nan"), "p10": float("nan")}
    return {
        "n": n,
        "win": float((net > 0).sum()) / n,
        "mean_net": float(net.mean()),
        "p5": float(net.quantile(0.05)),
        "p10": float(net.quantile(0.10)),
    }


def analyze(dec: pl.DataFrame, delta: float, N: int) -> pl.DataFrame:
    limit_price = pl.col("close") * (1 - delta)
    exit_open = pl.col("fwd_entry_open") * (1 + pl.col(f"fwd_ret_{N}"))  # T+(1+N) 开盘

    # 一字板(D+1):open=high=low → 无振幅;涨停飞走 / 跌停砸死,均从成交池剔。
    oneword_next = pl.col("fwd_high_1") == pl.col("fwd_low_1")
    ld_oneword = pl.col("fwd_ld_next") & oneword_next
    lu_oneword = pl.col("fwd_lu_next") & oneword_next

    d = dec.with_columns(
        limit_price.alias("_lp"),
        exit_open.alias("_exit"),
        ((pl.col("fwd_low_1") <= limit_price)
         & pl.col("fwd_entry_open").is_not_null()
         & pl.col(f"fwd_ret_{N}").is_not_null()
         & ~ld_oneword).alias("_filled"),
        (pl.col("fwd_buyable") & pl.col(f"fwd_ret_{N}").is_not_null()).alias("_mkt_ok"),
        lu_oneword.alias("_lu_oneword"),
    )

    filled = d.filter(pl.col("_filled"))
    # 成交组 A:入场=挂单价(预注册)。realistic:入场=min(D+1开盘, 挂单价)。
    a_reg = (filled["_exit"] / filled["_lp"] - 1)
    entry_real = pl.min_horizontal(pl.col("fwd_entry_open"), pl.col("_lp"))
    a_real = (filled.with_columns(entry_real.alias("_er"))["_exit"] / filled.with_columns(entry_real.alias("_er"))["_er"] - 1)
    # C 市价-填上的名:同一批成交名按市价(D+1开盘)入场。
    c = filled[f"fwd_ret_{N}"]

    # B 市价对照全体:同一批 D 全体,市价买入(需 fwd_buyable)。
    mkt = d.filter(pl.col("_mkt_ok"))
    b = mkt[f"fwd_ret_{N}"]
    # D 未成交错过:未成交 且 可市价买入(排除 D+1 一字涨停飞走)。
    unfilled_buyable = d.filter(~pl.col("_filled") & pl.col("_mkt_ok"))
    dd = unfilled_buyable[f"fwd_ret_{N}"]

    rows = [
        {"leg": "A成交组(挂单价)", **_mean_net(a_reg)},
        {"leg": "A'成交组(现实min价)", **_mean_net(a_real)},
        {"leg": "C市价·填上的名", **_mean_net(c)},
        {"leg": "B市价对照全体", **_mean_net(b)},
        {"leg": "D未成交错过(市价)", **_mean_net(dd)},
    ]
    res = pl.DataFrame(rows)
    # 附:成交率、未成交里一字涨停飞走数(错过收益被低估的部分)。
    n_dec = d.height
    n_fill = filled.height
    n_unfill_lu = d.filter(~pl.col("_filled") & pl.col("_lu_oneword")).height
    res = res.with_columns(
        pl.lit(delta).alias("delta"), pl.lit(N).alias("N"),
        pl.lit(n_dec).alias("n_dec"), pl.lit(n_fill).alias("n_fill"),
        pl.lit(n_unfill_lu).alias("n_unfill_1字涨停"),
    )
    return res


def main() -> None:
    panel = add_k4p_features(get_panel())
    dec = decision_universe(panel)
    dec26 = dec.filter(year_2026_expr())

    print("# H3 挂低单逆向选择(执行层)—— 结果")
    print(f"\n决策日域(vr≥1.5 & 换手∈[5,10] & close>ma20 & 非一字板)事件 {dec.height}")
    print("退出统一在 T+(1+N) 开盘(同持有窗);成交价=挂单价(预注册),另报现实 min 价稳健。")

    for delta in DELTAS:
        for N in NS:
            print(f"\n## δ={delta:.1%} · 持有 N={N}(退出 T+{1+N} 开盘)")
            res = analyze(dec, delta, N)
            fr = res["n_fill"][0] / res["n_dec"][0]
            print(f"成交率 {fr:.1%}(成交 {res['n_fill'][0]} / 决策 {res['n_dec'][0]});"
                  f"未成交里 D+1 一字涨停飞走 {res['n_unfill_1字涨停'][0]} 只(错过收益被低估的部分)")
            print(fmt(res.select(["leg", "n", "win", "mean_net", "p5", "p10"]),
                      intcols=("n",)))
            a = res.filter(pl.col("leg") == "A成交组(挂单价)")["mean_net"][0]
            c = res.filter(pl.col("leg") == "C市价·填上的名")["mean_net"][0]
            b = res.filter(pl.col("leg") == "B市价对照全体")["mean_net"][0]
            dv = res.filter(pl.col("leg") == "D未成交错过(市价)")["mean_net"][0]
            print(f"  · δ 机械折扣检验 A−C = {a-c:+.4f}(≈δ 则出场无拖累;远小于δ 则有拖累)")
            print(f"  · 逆向选择检验 C−D = {c-dv:+.4f}(<0 = 成交的名市价也比未成交的差 → 挂低单选中输家)")
            print(f"  · 预注册主判据:A({a:+.4f}) vs B({b:+.4f}) → A−B={a-b:+.4f};未成交错过 D={dv:+.4f}")

    print("\n## 2026 分段(δ=0.5% & 1.0%,N=3)")
    for delta in DELTAS:
        print(f"\n### δ={delta:.1%} · N=3 · 2026")
        res = analyze(dec26, delta, 3)
        print(fmt(res.select(["leg", "n", "win", "mean_net", "p5", "p10"]), intcols=("n",)))


if __name__ == "__main__":
    main()
