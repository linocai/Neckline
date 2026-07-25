"""K4 前置 H2:量比/换手带(入池闸门层)。

预注册见 `research/k4_pre_report.md` §2。事件(带内)= vol_ratio_5≥1.5 & turnover∈[5%,10%],
域内。四对照组(量比达标/换手带外两侧、换手达标/量比不足、双不达标)拆开对比,
看闸门带内是否真优于带外。敏感性 量比{1.2,1.5,2.0}×换手带{[3,8],[5,10],[7,15]}。
稳健性叠加 close>ma20(不改判决)。

独立可重跑:`python research/k4p_h2_band.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neckline.research.eventstudy import event_study_grouped  # noqa: E402
from lab import get_panel  # noqa: E402
from k4p_common import (  # noqa: E402
    add_k4p_features, base_expr, oneword_event_expr, hold_table, exposure_row, fmt, year_2026_expr,
)

VR, TO_LO, TO_HI = 1.5, 5.0, 10.0
HOLDS = (1, 2, 3, 4, 5)
GRID_VR = (1.2, 1.5, 2.0)
GRID_BAND = ((3.0, 8.0), (5.0, 10.0), (7.0, 15.0))


def analysis_universe(panel: pl.DataFrame) -> pl.DataFrame:
    """基础域 + 量比/换手非空 + 非一字板(五组据此干净划分)。"""
    return panel.filter(
        base_expr()
        & pl.col("vol_ratio_5").is_not_null()
        & pl.col("turnover_rate").is_not_null()
        & ~oneword_event_expr()
    )


def split_groups(uni: pl.DataFrame, vr=VR, lo=TO_LO, hi=TO_HI) -> dict:
    to = pl.col("turnover_rate")
    vok = pl.col("vol_ratio_5") >= vr
    tok = (to >= lo) & (to <= hi)
    return {
        "带内(vr≥闸&换手带内)": uni.filter(vok & tok),
        "①vr≥闸·换手<下限": uni.filter(vok & (to < lo)),
        "②vr≥闸·换手>上限": uni.filter(vok & (to > hi)),
        "③vr<闸·换手带内": uni.filter(~vok & tok),
        "④双不达标": uni.filter(~vok & ~tok),
    }


def summary(groups: dict, hold: int) -> pl.DataFrame:
    rows = []
    for label, ev in groups.items():
        ht = hold_table(ev, (hold,)).to_dicts()[0]
        ex = exposure_row(ev, label)
        rows.append({
            "group": label, "n": ht["n"], "win_rate": ht["win_rate"], "mean_net": ht["mean_net"],
            "p5_net": ht["p5_net"], "p10_net": ht["p10_net"], "next_ld": ex["next_ld_rate"],
        })
    return pl.DataFrame(rows)


def main() -> None:
    panel = add_k4p_features(get_panel())
    uni = analysis_universe(panel)
    groups = split_groups(uni)

    print("# H2 量比/换手带(入池闸门层)—— 结果")
    print(f"\n分析域(基础域+量比换手非空+非一字板)行数 {uni.height}")
    print("事件:带内 = vol_ratio_5≥1.5 & turnover∈[5%,10%];四对照拆开。")
    for k, v in groups.items():
        print(f"  {k}: {v.height}")

    print("\n## 主对比 A:五组前瞻收益 + 左尾 + 次日跌停(hold=3)")
    print(fmt(summary(groups, 3)))
    print("\n## 主对比 A':五组(hold=5)")
    print(fmt(summary(groups, 5)))

    print("\n## B:带内组全持有窗(hold 1..5)")
    print(fmt(hold_table(groups["带内(vr≥闸&换手带内)"], HOLDS)))

    print("\n## C:2026 分段(五组,hold=3)")
    g26 = {k: v.filter(year_2026_expr()) for k, v in groups.items()}
    print(fmt(summary(g26, 3)))
    print("\n### 2026 带内组全持有窗")
    print(fmt(hold_table(g26["带内(vr≥闸&换手带内)"], HOLDS)))

    print("\n## D:年份分层(带内组,mean_net hold 3/5)")
    band = groups["带内(vr≥闸&换手带内)"]
    print(fmt(event_study_grouped(band, pl.col("ts_code").is_not_null(), "year", hold_days=(3, 5))
              .select(["year", "hold_days", "n", "win_rate", "mean_net"])))

    print("\n## E:市场状态分层(带内组)")
    print(fmt(event_study_grouped(band, pl.col("ts_code").is_not_null(), "sse_above_ma", hold_days=(3, 5))
              .select(["sse_above_ma", "hold_days", "n", "win_rate", "mean_net"])))

    print("\n## F:板块分层(带内组;概念板块降级为 board,见 §0)")
    print(fmt(event_study_grouped(band, pl.col("ts_code").is_not_null(), "board", hold_days=(3, 5))
              .select(["board", "hold_days", "n", "win_rate", "mean_net"])))

    print("\n## G:稳健性叠加 close>ma20(不改判决;带内组分趋势上下)")
    up = band.filter(pl.col("close") > pl.col("ma20"))
    dn = band.filter(pl.col("close") <= pl.col("ma20"))
    print(fmt(summary({"带内&close>ma20": up, "带内&close≤ma20": dn}, 3)))

    print("\n## H:±1 格敏感性(带内定义网格,mean_net + win + 次日跌停,hold=3)")
    rows = []
    for vr in GRID_VR:
        for lo, hi in GRID_BAND:
            g = split_groups(uni, vr=vr, lo=lo, hi=hi)["带内(vr≥闸&换手带内)"]
            ht = hold_table(g, (3,)).to_dicts()[0]
            ex = exposure_row(g, "band")
            rows.append({
                "vr": vr, "band": f"[{lo:.0f},{hi:.0f}]", "n": ht["n"],
                "win_rate": ht["win_rate"], "mean_net_h3": ht["mean_net"],
                "p5_net": ht["p5_net"], "next_ld": ex["next_ld_rate"],
            })
    print(fmt(pl.DataFrame(rows), intcols=("n",)))


if __name__ == "__main__":
    main()
