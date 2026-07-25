"""K4 前置 · 战役二 H4:MACD/KDJ 金叉(形态确认层)。

预注册见 `research/k4_pre2_report.md` §1。审计用户漏斗第③层——"MACD 和 KDJ 至少
一个金叉;两个金叉更有好感"。

指标参数**钉死不扫参**(§0 预注册,审的是行情软件里那个金叉,不是最优参数):
    · MACD:DIF = EMA(close,12) − EMA(close,26);DEA = EMA(DIF,9)。多头态 = DIF>DEA。
      EMA 用 ewm_mean(span=N, adjust=False)(标准递推,alpha=2/(N+1))。
    · KDJ(9,3,3):RSV = (close−min(low,9))/(max(high,9)−min(low,9))×100;
      K = 2/3·K_prev + 1/3·RSV;D = 2/3·D_prev + 1/3·K。多头态 = K>D。
      2/3+1/3 平滑 == ewm_mean(alpha=1/3, adjust=False)(数学等价:EMA 递推
      x_t = (1−α)x_{t−1}+α·new,α=1/3 即得 2/3 旧 + 1/3 新)。初值差异:标准
      KDJ 播种 K=D=50,ewm 从首个非空 RSV 起步——差异在 warmup 后快速收敛,且
      判决域要求 days_since_listing≥120,判决行远在收敛后。

warmup:指标按面板内每票 EMA 递推,**前 34 个面板 bar 置 null**(26+9−1)去冷启动
偏差(仅影响 2020 年初及次新上市初期,对判决域无实质影响);KDJ 另天然 null 到
RSV 成形(9 bar)。判决只落在指标非空行。

主判据 = 状态四分(双金叉态/仅MACD/仅KDJ/双空头态);次判据 = 叉发生日事件
(今日多头态 & 昨日非多头态);稳健性 = close>ma20 分层(不改判决)。指标参数钉死
无参数网格,稳健性由次判据 + close>ma20 承担。

独立可重跑:`python research/k4p_h4_cross.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.research.eventstudy import event_study, event_study_grouped  # noqa: E402
from lab import get_panel  # noqa: E402
from k4p_common import (  # noqa: E402
    add_k4p_features, base_expr, oneword_event_expr, hold_table, exposure_row, fmt, year_2026_expr,
)

HOLDS = (1, 2, 3, 4, 5)
WARMUP_BARS = 34  # MACD DEA(EMA9 of DIF, DIF~EMA26) 冷启动去偏


def add_macd_kdj(panel: pl.DataFrame) -> pl.DataFrame:
    """逐票(per ts_code)算 MACD 多头态 / KDJ 多头态 + 金叉日 + 四态标签。只进研究计算。"""
    df = panel.sort(["ts_code", "trade_date"])
    over = "ts_code"

    # MACD:标准 EMA 递推(adjust=False)。
    ema12 = pl.col("close").ewm_mean(span=12, adjust=False).over(over)
    ema26 = pl.col("close").ewm_mean(span=26, adjust=False).over(over)
    df = df.with_columns((ema12 - ema26).alias("_dif"))
    dea = pl.col("_dif").ewm_mean(span=9, adjust=False).over(over)
    df = df.with_columns(dea.alias("_dea"))

    # KDJ(9,3,3):RSV → K(ewm α=1/3) → D(ewm α=1/3 of K)。
    rmin = pl.col("low").rolling_min(9, min_samples=9).over(over)
    rmax = pl.col("high").rolling_max(9, min_samples=9).over(over)
    rng = (rmax - rmin)
    rsv = pl.when(rng > 0).then((pl.col("close") - rmin) / rng * 100).otherwise(50.0)
    df = df.with_columns(rsv.alias("_rsv"))
    df = df.with_columns(
        pl.col("_rsv").ewm_mean(alpha=1 / 3, adjust=False, ignore_nulls=True).over(over).alias("_k")
    )
    df = df.with_columns(
        pl.col("_k").ewm_mean(alpha=1 / 3, adjust=False, ignore_nulls=True).over(over).alias("_d")
    )

    # warmup 去冷启动:前 WARMUP_BARS 个面板 bar 的状态置 null。
    bar_idx = pl.col("trade_date").cum_count().over(over)
    macd_bull_raw = pl.col("_dif") > pl.col("_dea")
    kdj_bull_raw = pl.col("_k") > pl.col("_d")
    warm = bar_idx >= WARMUP_BARS
    kdj_ready = pl.col("_k").is_not_null() & pl.col("_d").is_not_null()
    df = df.with_columns(
        pl.when(warm).then(macd_bull_raw).otherwise(None).alias("macd_bull"),
        pl.when(warm & kdj_ready).then(kdj_bull_raw).otherwise(None).alias("kdj_bull"),
    )

    # 金叉日(上穿):今日多头态 & 昨日非多头态(shift over ts_code)。
    macd_prev = pl.col("macd_bull").shift(1).over(over)
    kdj_prev = pl.col("kdj_bull").shift(1).over(over)
    df = df.with_columns(
        (pl.col("macd_bull") & (~macd_prev.fill_null(True))).alias("macd_cross"),
        (pl.col("kdj_bull") & (~kdj_prev.fill_null(True))).alias("kdj_cross"),
    )

    # 四态标签(仅两态均非空时有效)。
    both_ready = pl.col("macd_bull").is_not_null() & pl.col("kdj_bull").is_not_null()
    state = (
        pl.when(~both_ready).then(None)
        .when(pl.col("macd_bull") & pl.col("kdj_bull")).then(pl.lit("①双金叉态"))
        .when(pl.col("macd_bull") & ~pl.col("kdj_bull")).then(pl.lit("②仅MACD"))
        .when(~pl.col("macd_bull") & pl.col("kdj_bull")).then(pl.lit("③仅KDJ"))
        .otherwise(pl.lit("④双空头态"))
    )
    return df.with_columns(state.alias("state4"))


def indicator_domain(panel: pl.DataFrame) -> pl.DataFrame:
    """§0 主判决域 + 非一字板 + 四态非空(两指标均成形)。"""
    return panel.filter(
        base_expr() & ~oneword_event_expr() & pl.col("state4").is_not_null()
    )


def _grouped_net(events: pl.DataFrame, group_col: str, holds=(3, 5)) -> pl.DataFrame:
    g = event_study_grouped(events, pl.col("ts_code").is_not_null(), group_col, hold_days=holds)
    if g.is_empty():
        return g
    return g.select([group_col, "hold_days", "n", "win_rate", "mean_net"])


def state_table(dom: pl.DataFrame, holds=HOLDS) -> None:
    order = ["①双金叉态", "②仅MACD", "③仅KDJ", "④双空头态"]
    for st in order:
        sub = dom.filter(pl.col("state4") == st)
        print(f"\n### {st}(n={sub.height})")
        print(fmt(hold_table(sub, holds)))
    print(f"\n### 域基线(四态并集,n={dom.height})")
    print(fmt(hold_table(dom, holds)))


def main() -> None:
    panel = add_macd_kdj(add_k4p_features(get_panel()))
    dom = indicator_domain(panel)

    print("# H4 MACD/KDJ 金叉(形态确认层)—— 结果")
    print(f"\n指标域(base + 非一字板 + 四态非空)行数 {dom.height}")
    cnt = dom.group_by("state4").len().sort("state4")
    print("四态分布:")
    for r in cnt.iter_rows(named=True):
        print(f"  {r['state4']}: {r['len']}")

    # —— 口径交叉核对:hold_table 与 eventstudy 在双金叉态上一致 ——
    s1 = dom.filter(pl.col("state4") == "①双金叉态")
    chk = event_study(s1, pl.col("ts_code").is_not_null(), hold_days=(3,))
    mine = hold_table(s1, holds=(3,))
    assert abs(float(chk["mean_net"][0]) - float(mine["mean_net"][0])) < 1e-9, "口径与 eventstudy 不一致!"
    print("[口径交叉核对通过:hold_table.mean_net == eventstudy.event_study.mean_net]")

    print("\n## 主判据 A:状态四分 全期前瞻收益 + 左尾(扣双边成本 0.0015×2)")
    state_table(dom)

    print("\n## B:次日/持有内跌停暴露(买入后 D+1 / D+1~D+3 收盘跌停率)")
    rows = [exposure_row(dom.filter(pl.col("state4") == st), st)
            for st in ["①双金叉态", "②仅MACD", "③仅KDJ", "④双空头态"]]
    rows.append(exposure_row(dom, "域基线"))
    print(fmt(pl.DataFrame(rows)))

    print("\n## C:次判据 叉发生日事件(上穿日 fwd 对比)")
    macd_x = dom.filter(pl.col("macd_cross"))
    kdj_x = dom.filter(pl.col("kdj_cross"))
    both_x = dom.filter(pl.col("macd_cross") & pl.col("kdj_cross"))
    print(f"\n### MACD 金叉日(n={macd_x.height})")
    print(fmt(hold_table(macd_x, HOLDS)))
    print(f"\n### KDJ 金叉日(n={kdj_x.height})")
    print(fmt(hold_table(kdj_x, HOLDS)))
    print(f"\n### 同日双叉(n={both_x.height})")
    print(fmt(hold_table(both_x, HOLDS)))

    print("\n## D:稳健性叠加 close>ma20(不改判决;四态各分趋势上下,hold=3)")
    up = dom.filter(pl.col("close") > pl.col("ma20"))
    dn = dom.filter(pl.col("close") <= pl.col("ma20"))
    for label, sub in (("close>ma20", up), ("close≤ma20", dn)):
        r = []
        for st in ["①双金叉态", "②仅MACD", "③仅KDJ", "④双空头态"]:
            ht = hold_table(sub.filter(pl.col("state4") == st), (3,)).to_dicts()[0]
            r.append({"state4": st, "n": ht["n"], "win_rate": ht["win_rate"],
                      "mean_net": ht["mean_net"], "p5_net": ht["p5_net"]})
        print(f"\n### {label}")
        print(fmt(pl.DataFrame(r)))

    print("\n## E:2026 分段(生存视角单列,四态 hold=3/5)")
    dom26 = dom.filter(year_2026_expr())
    for st in ["①双金叉态", "②仅MACD", "③仅KDJ", "④双空头态"]:
        sub = dom26.filter(pl.col("state4") == st)
        print(f"\n### {st} 2026(n={sub.height})")
        print(fmt(hold_table(sub, (1, 2, 3, 4, 5))))
    print("\n### 2026 跌停暴露")
    print(fmt(pl.DataFrame(
        [exposure_row(dom26.filter(pl.col("state4") == st), st + "26")
         for st in ["①双金叉态", "④双空头态"]])))

    print("\n## F:年份分层(①双金叉态 vs ④双空头态,mean_net hold 3/5)")
    print("\n### ①双金叉态")
    print(fmt(_grouped_net(dom.filter(pl.col("state4") == "①双金叉态"), "year")))
    print("\n### ④双空头态")
    print(fmt(_grouped_net(dom.filter(pl.col("state4") == "④双空头态"), "year")))

    print("\n## G:市场状态分层(sse_above_ma;①vs④,hold 3/5)")
    print("\n### ①双金叉态")
    print(fmt(_grouped_net(dom.filter(pl.col("state4") == "①双金叉态"), "sse_above_ma")))
    print("\n### ④双空头态")
    print(fmt(_grouped_net(dom.filter(pl.col("state4") == "④双空头态"), "sse_above_ma")))

    print("\n## H:板块分层(board;①vs④,hold 3/5;概念板块降级为 board 见 §0)")
    print("\n### ①双金叉态")
    print(fmt(_grouped_net(dom.filter(pl.col("state4") == "①双金叉态"), "board")))
    print("\n### ④双空头态")
    print(fmt(_grouped_net(dom.filter(pl.col("state4") == "④双空头态"), "board")))


if __name__ == "__main__":
    main()
